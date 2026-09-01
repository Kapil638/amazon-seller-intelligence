"""12B.3G follow-up — `AmazonListingsSyncTriggerService.trigger()` under
real PostgreSQL: the specific `NULLS FIRST` ordering defect this file's
first test reproduces cannot be proven on SQLite at all (SQLite defaults
to `NULLS LAST` for `DESC`, the opposite of PostgreSQL), and the
concurrency proofs below need real row-level locking exactly like every
other file in this directory.

Opt-in only — see `_guard.py`. Exercises the full service layer (not just
the repository) by pointing the application's own global engine at the
disposable database for the duration of each test, since `trigger()`
resolves its session via `session_scope()` -> the process-wide cached
engine, not a caller-supplied one. Written and statically reasoned
through carefully but could not be executed end-to-end in the authoring
environment (no local PostgreSQL). Treat a first real run as the actual
proof, not this file's existence.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.amazon.listings_sync import AmazonListingsSyncTriggerService
from app.core.config import get_settings
from app.persistence.database import reset_persistence, session_scope
from app.persistence.models import (
    AmazonConnection,
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]
MARKETPLACE = "ATVPDKIKX0DER"


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@contextmanager
def _application_pointed_at(url: str):
    """Unlike the sibling concurrency file (which talks to its own
    dedicated engine directly), `AmazonListingsSyncTriggerService.trigger()`
    resolves its session through the process-wide cached `get_engine()` —
    so proving anything about the *service*, not just the repository,
    requires the application's own global engine to actually point at the
    disposable database for the duration of the test."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    reset_persistence()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        reset_persistence()


@pytest.fixture
def disposable_engine():
    url = _guard.disposable_url()
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        if existing_tables - {"alembic_version"}:
            pytest.fail(
                "POSTGRES_DISPOSABLE_TEST_URL points at a non-empty database "
                f"({len(existing_tables)} existing table(s)) — refusing to run "
                "destructive migration tests against it. Use a genuinely fresh "
                "disposable instance."
            )
        with _application_pointed_at(url):
            command.upgrade(_alembic_config(url), "head")
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def _seed_default_org_scope(engine) -> tuple[UUID, UUID, UUID]:
    """`current_organization_id()` (used internally by `trigger()`) always
    returns `Settings.default_organization_id` in this single-tenant-per-
    deployment version — so the scope this test seeds must live under
    that exact organization, not an arbitrary one, or `trigger()` would
    correctly report `scope_not_found` for a foreign participation."""
    org_id = get_settings().default_organization_id
    seller_account_id = uuid4()
    participation_id = uuid4()
    connection_id = uuid4()
    with Session(engine) as session:
        if session.get(Organization, org_id) is None:
            session.add(Organization(id=org_id, name=get_settings().default_organization_name))
        session.add(
            AmazonConnection(
                id=connection_id,
                organization_id=org_id,
                provider="SP_API",
                environment="PRODUCTION",
                region="na",
                status="connected",
                token_reference=f"asi-amazon-secret:{uuid4().hex}",
            )
        )
        session.add(
            AmazonSellerAccount(
                id=seller_account_id,
                organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}",
                status="active",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=connection_id,
                marketplace_id=MARKETPLACE,
                region="na",
                is_active=True,
                is_participating=True,
            )
        )
        session.commit()
    return org_id, seller_account_id, participation_id


# --- 1: the exact production defect — a days-old `cancelled_before_start`
# row (`started_at IS NULL`) must never outrank a genuinely newer, real
# run under PostgreSQL's real `NULLS FIRST` DESC ordering. -----------------


def test_get_latest_listings_run_is_not_fooled_by_a_null_started_at_row(disposable_engine) -> None:
    """Only provable against real PostgreSQL: SQLite defaults to `NULLS
    LAST` for `DESC`, so the pre-fix `ORDER BY started_at DESC` query
    would (accidentally) return the correct row there too, masking this
    exact defect. PostgreSQL defaults to `NULLS FIRST` for `DESC` — a
    `started_at IS NULL` row sorts ahead of every real row regardless of
    age, which is precisely what let a days-old cancelled job hide three
    brand-new successful syncs from production's cooldown check."""
    org_id, seller_account_id, participation_id = _seed_default_org_scope(disposable_engine)
    with Session(disposable_engine) as session:
        repo = AmazonIngestionRunRepository(session)
        cancelled_claim = repo.enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, cancelled_claim.run_id)
        row.created_at = datetime.now(UTC) - timedelta(days=3)
        session.commit()
    with Session(disposable_engine) as session:
        terminalized = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            org_id, cancelled_claim.run_id
        )
        session.commit()
    assert terminalized is True

    with Session(disposable_engine) as session:
        real_claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="worker-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(disposable_engine) as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, real_claim.run_id, lease_owner="worker-1", status="succeeded",
        )
        session.commit()

    with Session(disposable_engine) as session:
        latest = AmazonIngestionRunRepository(session).get_latest_listings_run(org_id, participation_id)
        assert latest.id == real_claim.run_id, "returned the 3-day-old cancelled row instead of the real one"


# --- 2/3: concurrent `trigger()` calls at the full service layer. --------


@dataclass
class _TriggerOutcome:
    reason: str
    run_id: UUID | None


def _trigger_attempt(*, participation_id: UUID, barrier: threading.Barrier, outcomes: list, errors: list, lock) -> None:
    try:
        barrier.wait()
        outcome = AmazonListingsSyncTriggerService().trigger(participation_id)
        run_id = outcome.job.run_id if outcome.job is not None else None
        with lock:
            outcomes.append(_TriggerOutcome(reason=outcome.reason, run_id=run_id))
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(exc)


def test_two_concurrent_triggers_create_at_most_one_job(disposable_engine) -> None:
    _, _, participation_id = _seed_default_org_scope(disposable_engine)

    barrier = threading.Barrier(2)
    outcomes: list[_TriggerOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_trigger_attempt,
            kwargs=dict(participation_id=participation_id, barrier=barrier, outcomes=outcomes, errors=errors, lock=lock),
        )
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    assert len(outcomes) == 2
    created_run_ids = {o.run_id for o in outcomes if o.reason == "queued"}
    assert len(created_run_ids) == 1, outcomes
    non_queued = [o for o in outcomes if o.reason != "queued"]
    assert len(non_queued) == 1
    assert non_queued[0].reason == "already_running"


def test_ten_concurrent_triggers_create_at_most_one_job(disposable_engine) -> None:
    _, _, participation_id = _seed_default_org_scope(disposable_engine)

    barrier = threading.Barrier(10)
    outcomes: list[_TriggerOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_trigger_attempt,
            kwargs=dict(participation_id=participation_id, barrier=barrier, outcomes=outcomes, errors=errors, lock=lock),
        )
        for _ in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == []
    assert len(outcomes) == 10
    created_run_ids = {o.run_id for o in outcomes if o.reason == "queued"}
    assert len(created_run_ids) == 1, outcomes
    assert all(o.reason in ("queued", "already_running") for o in outcomes)


# --- 4: a trigger racing an already-claimed job recognizes it rather than
# creating a second. ---------------------------------------------------


def test_trigger_racing_a_worker_claim_recognizes_the_same_job_not_a_new_one(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_default_org_scope(disposable_engine)
    with session_scope() as session:
        enqueue = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
    assert enqueue.claimed is True

    with session_scope() as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
    assert claimed is not None
    assert claimed.id == enqueue.run_id

    outcome = AmazonListingsSyncTriggerService().trigger(participation_id)
    assert outcome.reason == "already_running"
    assert outcome.job is not None
    assert outcome.job.run_id == enqueue.run_id
    assert outcome.job.status == "started"

    with session_scope() as session:
        active_count = AmazonIngestionRunRepository(session).get_active_listings_run(org_id, participation_id)
        assert active_count is not None and active_count.id == enqueue.run_id

"""12B.3G follow-up — `AmazonListingsSyncTriggerService.trigger()` under
real PostgreSQL: the specific `NULLS FIRST` ordering defect this file's
first test reproduces cannot be proven on SQLite at all (SQLite defaults
to `NULLS LAST` for `DESC`, the opposite of PostgreSQL), and the
concurrency proofs below need real row-level locking exactly like every
other file in this directory.

Opt-in only — see `_guard.py`. Follows the exact two-context-manager
pattern already established by `test_disposable_postgres_marketplace_
reconciliation_concurrency.py`, not a bespoke one — an earlier version of
this file folded both concerns into a single helper invoked only around
the migration step inside the `disposable_engine` fixture, so the global
engine redirect was torn down again before any test body ran at all;
every test that went through `AmazonListingsSyncTriggerService.trigger()`
(which resolves its session via `session_scope()` -> the process-wide
cached `get_engine()`, never a caller-supplied engine) was then silently
querying whatever engine `conftest.py`'s autouse fixture had already
warmed up (SQLite), not the disposable Postgres instance — indistinguishable
from a genuine `scope_not_found`/ownership failure from the caller's side,
and exactly what CI's "uniform scope_not_found" and "direct enqueue
ownership failure" results were. `_alembic_environment` (migration only,
narrow) and `_global_engine_pointed_at` (wraps an entire test body, held
until threads join and assertions finish) are kept as two separate
helpers for exactly this reason — collapsing them back into one is the
regression to guard against here.

Written and statically reasoned through carefully but the corrected
structure could not be executed end-to-end in the authoring environment
(no local PostgreSQL). Treat a first real CI run as the actual proof, not
this file's existence.
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
from sqlalchemy import create_engine, func, inspect, select, text
from sqlalchemy.orm import Session

from app.amazon.listings_sync import AmazonListingsSyncTriggerService
from app.core.config import get_settings
from app.persistence.database import get_engine, reset_persistence, session_scope
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
def _alembic_environment(url: str):
    """Migration-only. `migrations/env.py` always re-reads `DATABASE_URL`,
    so it must be overridden here even though the URL is also set on the
    `Config` object — but this never touches the application's own
    process-global engine, so it is deliberately narrow: entered only for
    the duration of a single `command.upgrade(...)` call, never wrapped
    around a test body."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@contextmanager
def _global_engine_pointed_at(url: str):
    """Point the application's process-global session factory at `url`,
    for as long as the caller's `with` block is open.

    `get_engine()` is a *zero-argument* `lru_cache` — setting
    `DATABASE_URL` and calling `get_settings.cache_clear()` alone (as
    `_alembic_environment` above does, correctly, for driving Alembic
    directly) has no effect on it. `AmazonListingsSyncTriggerService.
    trigger()` and this test module's own `session_scope()` calls always
    go through `session_scope()` -> `get_engine()`, so without also
    resetting *that* cache they keep resolving to whatever engine this
    test process warmed up first (SQLite, via `conftest.py`'s autouse
    fixture) — not the disposable Postgres instance.

    Established once, from the main thread, before any worker thread
    starts — never from inside a worker, which would race the same
    process-global caches across threads for no benefit, since every
    thread wants the identical URL anyway. The resulting `Engine`/
    connection pool is safe to share across threads afterward, exactly as
    it is in the real running application. Callers must keep this context
    open for the entire test body — seeding, thread start/join, and every
    assertion or verification query — not merely around migration or
    seeding; closing it early is exactly the defect this pattern exists to
    prevent (see the module docstring)."""
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    reset_persistence()
    get_engine()
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
    correctly report `scope_not_found` for a foreign participation. Must
    be called only after `_global_engine_pointed_at` is active — both so
    `get_settings().default_organization_id` reflects the redirected
    settings, and so this data actually lands in the disposable database
    the rest of the test (and the service under test) will query."""
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


def _assert_service_resolves_the_disposable_engine(disposable_engine) -> None:
    """Proves the process-global engine `session_scope()`/`trigger()` will
    actually use is the disposable one — never by printing or comparing
    URL strings (which could leak a credential into CI logs), only by
    comparing SQLAlchemy `URL` objects for equality. Call this only from
    inside an active `_global_engine_pointed_at` block, after seeding —
    a failure here means every assertion later in the same test would be
    checking the wrong database without this catching it first."""
    resolved = get_engine()
    assert resolved is not None
    assert resolved.url == disposable_engine.url


# --- 0: the redirect itself resolves to the disposable engine, not
# whatever engine this test process warmed up first. -----------------------


def test_global_engine_redirect_resolves_to_the_disposable_engine(disposable_engine) -> None:
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "head")

    with _global_engine_pointed_at(url):
        _assert_service_resolves_the_disposable_engine(disposable_engine)


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
    brand-new successful syncs from production's cooldown check. Uses
    `disposable_engine` directly throughout (never `session_scope()`/
    `AmazonListingsSyncTriggerService`), so unlike the tests below it does
    not depend on the global engine redirect at all — only the migration
    needs to be applied first."""
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "head")

    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(disposable_engine) as session:
        session.add(Organization(id=org_id, name="12B.3G Postgres Trigger Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id, organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id=MARKETPLACE, region="na",
            )
        )
        session.commit()

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


def _diagnostics_message(outcomes: "list[_TriggerOutcome]", engine) -> str:
    """A failure-only, sanitized summary of a concurrent-trigger run — no
    organization, seller, connection, participation, or run identifier is
    ever included, only counts and a same-job boolean. Safe to attach as
    an assertion message (pytest only renders it when the assertion
    actually fails)."""
    reason_counts: dict[str, int] = {}
    for outcome in outcomes:
        reason_counts[outcome.reason] = reason_counts.get(outcome.reason, 0) + 1

    non_null_count = sum(1 for o in outcomes if o.run_id is not None)
    null_count = sum(1 for o in outcomes if o.run_id is None)
    distinct_non_null_run_ids = {o.run_id for o in outcomes if o.run_id is not None}

    with Session(engine) as session:
        status_rows = session.execute(
            select(AmazonIngestionRun.status, func.count())
            .where(AmazonIngestionRun.run_type == "listings")
            .group_by(AmazonIngestionRun.status)
        ).all()
    status_counts = {status: count for status, count in status_rows}

    return (
        "outcome reason counts: "
        f"{reason_counts} | "
        f"non-null run id outcomes: {non_null_count} (distinct job(s): {len(distinct_non_null_run_ids)}) | "
        f"null run id outcomes: {null_count} | "
        f"all non-null outcomes reference the same durable job: {len(distinct_non_null_run_ids) <= 1} | "
        f"final Listings run counts by status: {status_counts}"
    )


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
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "head")

    with _global_engine_pointed_at(url):
        _assert_service_resolves_the_disposable_engine(disposable_engine)
        _, _, participation_id = _seed_default_org_scope(disposable_engine)

        barrier = threading.Barrier(2)
        outcomes: list[_TriggerOutcome] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_trigger_attempt,
                kwargs=dict(
                    participation_id=participation_id, barrier=barrier, outcomes=outcomes, errors=errors, lock=lock
                ),
            )
            for _ in range(2)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        diagnostics = lambda: _diagnostics_message(outcomes, disposable_engine)  # noqa: E731
        assert errors == [], diagnostics()
        assert len(outcomes) == 2, diagnostics()
        created_run_ids = {o.run_id for o in outcomes if o.reason == "queued"}
        assert len(created_run_ids) == 1, diagnostics()
        non_queued = [o for o in outcomes if o.reason != "queued"]
        assert len(non_queued) == 1, diagnostics()
        assert non_queued[0].reason == "already_running", diagnostics()


def test_ten_concurrent_triggers_create_at_most_one_job(disposable_engine) -> None:
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "head")

    with _global_engine_pointed_at(url):
        _assert_service_resolves_the_disposable_engine(disposable_engine)
        _, _, participation_id = _seed_default_org_scope(disposable_engine)

        barrier = threading.Barrier(10)
        outcomes: list[_TriggerOutcome] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_trigger_attempt,
                kwargs=dict(
                    participation_id=participation_id, barrier=barrier, outcomes=outcomes, errors=errors, lock=lock
                ),
            )
            for _ in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

        diagnostics = lambda: _diagnostics_message(outcomes, disposable_engine)  # noqa: E731
        assert errors == [], diagnostics()
        assert len(outcomes) == 10, diagnostics()
        created_run_ids = {o.run_id for o in outcomes if o.reason == "queued"}
        assert len(created_run_ids) == 1, diagnostics()
        assert all(o.reason in ("queued", "already_running") for o in outcomes), diagnostics()


# --- 4: a trigger racing an already-claimed job recognizes it rather than
# creating a second. ---------------------------------------------------


def test_trigger_racing_a_worker_claim_recognizes_the_same_job_not_a_new_one(disposable_engine) -> None:
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "head")

    with _global_engine_pointed_at(url):
        _assert_service_resolves_the_disposable_engine(disposable_engine)
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
            active_run = AmazonIngestionRunRepository(session).get_active_listings_run(org_id, participation_id)
            assert active_run is not None and active_run.id == enqueue.run_id

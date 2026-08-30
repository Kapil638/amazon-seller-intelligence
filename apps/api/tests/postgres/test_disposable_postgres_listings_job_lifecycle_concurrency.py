"""12B.3G — Durable Listings job lifecycle (queued / started / waiting_to_
retry) under real PostgreSQL concurrency, and the 0010 -> 0011 migration.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Written and statically reasoned through
carefully (mirroring `test_disposable_postgres_listings_run_claim_
concurrency.py`), but could not be executed end-to-end in the authoring
environment (no Docker, no local PostgreSQL binary available). Whoever
runs this with a real disposable Postgres instance should treat a first
run as the actual proof, not this file's existence.

`SELECT ... FOR UPDATE SKIP LOCKED` (used by `claim_next_listings_job`) is
a no-op on SQLite — nothing in `tests/test_amazon_listings_job_lifecycle.py`
(the SQLite-only companion suite covering this same repository surface's
non-concurrent semantics) proves anything about real locking behavior.
This file, run against real PostgreSQL, is the only proof of that.
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

from app.core.config import get_settings
from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@contextmanager
def _alembic_environment(url: str):
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
        with _alembic_environment(url):
            command.upgrade(_alembic_config(url), "head")
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def _seed_scope(engine, *, org_id: UUID | None = None) -> tuple[UUID, UUID, UUID]:
    org_id = org_id or uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="12B.3G Postgres Job Lifecycle Test Org"))
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
                marketplace_id="ATVPDKIKX0DER",
                region="na",
            )
        )
        session.commit()
    return org_id, seller_account_id, participation_id


# --- 1: the widened partial unique index protects queued/started/
# waiting_to_retry as ONE mutually-exclusive set, not three independent
# ones — proven by mixing `enqueue_listings_run` and `claim_listings_run`
# (the two different insert paths) against the same scope. -----------------


def test_enqueue_and_immediate_claim_are_mutually_exclusive_for_the_same_scope(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        enqueue = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
    assert enqueue.claimed is True

    with Session(disposable_engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-a", lease_duration_seconds=300,
        )
        session.commit()
    assert claim.claimed is False
    assert claim.reason == "already_running"

    with Session(disposable_engine) as session:
        rows = session.query(AmazonIngestionRun).filter_by(
            seller_account_id=seller_account_id, marketplace_participation_id=participation_id
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "queued"


# --- 2: concurrent `enqueue_listings_run` calls for a fresh scope have
# exactly one winner. ------------------------------------------------------


@dataclass
class _EnqueueOutcome:
    label: str
    claimed: bool
    reason: str | None


def _enqueue_attempt(
    *, engine, org_id, seller_account_id, participation_id, label, barrier, outcomes, errors, lock
) -> None:
    try:
        barrier.wait()
        with Session(engine) as session:
            claim = AmazonIngestionRunRepository(session).enqueue_listings_run(
                organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
                connection_id=None,
            )
            session.commit()
        with lock:
            outcomes.append(_EnqueueOutcome(label=label, claimed=claim.claimed, reason=claim.reason))
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(exc)


def test_concurrent_enqueue_for_the_same_participation_has_exactly_one_winner(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    barrier = threading.Barrier(2)
    outcomes: list[_EnqueueOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_enqueue_attempt,
            kwargs=dict(
                engine=disposable_engine, org_id=org_id, seller_account_id=seller_account_id,
                participation_id=participation_id, label=label, barrier=barrier,
                outcomes=outcomes, errors=errors, lock=lock,
            ),
        )
        for label in ("trigger-a", "trigger-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    assert len(outcomes) == 2
    winners = [o for o in outcomes if o.claimed]
    losers = [o for o in outcomes if not o.claimed]
    assert len(winners) == 1, outcomes
    assert len(losers) == 1
    assert losers[0].reason == "already_running"


# --- 3: `claim_next_listings_job`'s `SKIP LOCKED` claim genuinely prevents
# two concurrent "workers" from claiming the SAME queued job. --------------


@dataclass
class _ClaimOutcome:
    worker: str
    run_id: UUID | None


def _worker_claim_attempt(
    *, engine, worker, barrier, outcomes, errors, lock, max_global=10, max_per_org=10
) -> None:
    try:
        barrier.wait()
        with Session(engine) as session:
            claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
                lease_owner=worker, lease_duration_seconds=300,
                max_global_active=max_global, max_active_per_organization=max_per_org,
            )
            run_id = claimed.id if claimed is not None else None
            session.commit()
        with lock:
            outcomes.append(_ClaimOutcome(worker=worker, run_id=run_id))
    except Exception as exc:  # noqa: BLE001
        with lock:
            errors.append(exc)


def test_two_workers_racing_one_queued_job_never_both_claim_it(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        enqueue = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
    assert enqueue.claimed is True

    barrier = threading.Barrier(2)
    outcomes: list[_ClaimOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_worker_claim_attempt,
            kwargs=dict(engine=disposable_engine, worker=w, barrier=barrier, outcomes=outcomes, errors=errors, lock=lock),
        )
        for w in ("worker-1", "worker-2")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    winners = [o for o in outcomes if o.run_id is not None]
    assert len(winners) == 1, outcomes
    assert winners[0].run_id == enqueue.run_id

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, enqueue.run_id)
        assert row.status == "started"
        assert row.lease_owner in ("worker-1", "worker-2")


def test_many_queued_jobs_are_distributed_one_per_worker_with_no_double_claim(disposable_engine) -> None:
    """Five distinct queued jobs (five different participations, so the
    partial unique index never blocks any of them), five concurrent
    workers racing `claim_next_listings_job` together — every job must be
    claimed by exactly one worker, and no two workers may ever end up
    holding the same run id."""
    org_id = uuid4()
    run_ids: list[UUID] = []
    for _ in range(5):
        _, seller_account_id, participation_id = _seed_scope(disposable_engine, org_id=org_id)
        with Session(disposable_engine) as session:
            enqueue = AmazonIngestionRunRepository(session).enqueue_listings_run(
                organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
                connection_id=None,
            )
            session.commit()
        assert enqueue.claimed is True
        run_ids.append(enqueue.run_id)

    barrier = threading.Barrier(5)
    outcomes: list[_ClaimOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_worker_claim_attempt,
            kwargs=dict(
                engine=disposable_engine, worker=f"worker-{i}", barrier=barrier,
                outcomes=outcomes, errors=errors, lock=lock, max_global=10, max_per_org=10,
            ),
        )
        for i in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)

    assert errors == []
    claimed_run_ids = [o.run_id for o in outcomes if o.run_id is not None]
    assert len(claimed_run_ids) == 5
    assert sorted(claimed_run_ids) == sorted(run_ids)  # every job claimed
    assert len(set(claimed_run_ids)) == 5  # no run id claimed twice


def test_per_organization_concurrency_limit_is_enforced_under_concurrent_claims(disposable_engine) -> None:
    """Two queued jobs for the SAME organization (different participations),
    `max_active_per_organization=1`: only one may ever transition to
    `started` even when two workers race simultaneously."""
    org_id = uuid4()
    run_ids: list[UUID] = []
    for _ in range(2):
        _, seller_account_id, participation_id = _seed_scope(disposable_engine, org_id=org_id)
        with Session(disposable_engine) as session:
            enqueue = AmazonIngestionRunRepository(session).enqueue_listings_run(
                organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
                connection_id=None,
            )
            session.commit()
        run_ids.append(enqueue.run_id)

    barrier = threading.Barrier(2)
    outcomes: list[_ClaimOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_worker_claim_attempt,
            kwargs=dict(
                engine=disposable_engine, worker=f"worker-{i}", barrier=barrier,
                outcomes=outcomes, errors=errors, lock=lock, max_global=10, max_per_org=1,
            ),
        )
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    claimed = [o.run_id for o in outcomes if o.run_id is not None]
    assert len(claimed) == 1, outcomes

    with Session(disposable_engine) as session:
        statuses = {row.id: row.status for row in session.query(AmazonIngestionRun).filter(
            AmazonIngestionRun.id.in_(run_ids)
        ).all()}
    started = [rid for rid, status in statuses.items() if status == "started"]
    queued = [rid for rid, status in statuses.items() if status == "queued"]
    assert len(started) == 1
    assert len(queued) == 1


# --- 4: `reschedule_listings_run_for_retry` is lease-owner-gated exactly
# like `complete_listings_run` — a stale worker whose lease already
# expired can never move a run to `waiting_to_retry` out from under
# whoever (or whatever recovery step) has since reclaimed it. --------------


def test_reschedule_fails_once_lease_has_expired_even_with_no_replacement_worker(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(disposable_engine) as session:
        rescheduled = AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=datetime.now(UTC) + timedelta(seconds=30),
            failure_class="throttled",
        )
        session.commit()
    assert rescheduled is False

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"  # untouched — the compare-and-set never matched


def test_rescheduled_run_can_be_reclaimed_after_its_retry_time_passes(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(disposable_engine) as session:
        rescheduled = AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=datetime.now(UTC) - timedelta(seconds=1),  # already due
            failure_class="throttled",
        )
        session.commit()
    assert rescheduled is True

    with Session(disposable_engine) as session:
        reclaimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        session.commit()
    assert reclaimed is not None
    assert reclaimed.id == claim.run_id
    assert reclaimed.status == "started"
    assert reclaimed.retry_count == 1  # incremented only on a genuine retry reclaim


# --- 5: migration 0010 -> 0011 preserves existing rows and the downgrade
# refuses to destroy data it cannot represent. ------------------------------


def test_migration_0011_upgrade_preserves_existing_0010_rows_and_widens_constraints(disposable_engine) -> None:
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.downgrade(_alembic_config(url), "0010_amazon_seller_listings")

    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                id=uuid4(), organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, run_type="listings",
                domain="listings_items", region="na", environment="PRODUCTION", status="succeeded",
            )
        )
        session.commit()

    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "0011_listings_job_lifecycle")

    with Session(disposable_engine) as session:
        rows = session.query(AmazonIngestionRun).filter_by(
            seller_account_id=seller_account_id, marketplace_participation_id=participation_id
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "succeeded"  # pre-existing row untouched

    # The widened CHECK constraint now genuinely accepts the new states.
    with Session(disposable_engine) as session:
        queued_row = AmazonIngestionRun(
            id=uuid4(), organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=uuid4(), run_type="listings", domain="listings_items",
            region="na", environment="PRODUCTION", status="queued", started_at=None,
        )
        session.add(queued_row)
        session.commit()
        session.refresh(queued_row)
        assert queued_row.status == "queued"
        assert queued_row.started_at is None


def test_migration_0011_downgrade_refuses_to_discard_queued_or_waiting_rows(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()

    url = _guard.disposable_url()
    with pytest.raises(RuntimeError):
        with _alembic_environment(url):
            command.downgrade(_alembic_config(url), "0010_amazon_seller_listings")

    # The refused downgrade must not have partially applied — the row is
    # still there and the schema is still at 0011.
    with Session(disposable_engine) as session:
        rows = session.query(AmazonIngestionRun).filter_by(
            seller_account_id=seller_account_id, marketplace_participation_id=participation_id
        ).all()
        assert len(rows) == 1
        assert rows[0].status == "queued"


# --- 12B.3G follow-up: operator terminalize vs. a genuinely concurrent
# worker claim, under real PostgreSQL row locking. -------------------------


def test_concurrent_terminalize_and_worker_claim_are_mutually_exclusive(disposable_engine) -> None:
    """A real race: one thread runs the operator's `terminalize_unclaimed_
    listings_run`, another runs a worker's `claim_next_listings_job`, both
    targeting the same queued row, released simultaneously via a barrier.
    Exactly one must win. If the claim wins, the row must end up
    `started` with a lease — never also marked `failed`. If terminalize
    wins, the row must end up `failed` — never claimable afterward."""
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        enqueue = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
    assert enqueue.claimed is True

    barrier = threading.Barrier(2)
    outcomes: dict[str, object] = {}
    errors: list[BaseException] = []
    lock = threading.Lock()

    def _run_terminalize() -> None:
        try:
            barrier.wait()
            with Session(disposable_engine) as session:
                ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
                    org_id, enqueue.run_id
                )
                session.commit()
            with lock:
                outcomes["terminalize_ok"] = ok
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    def _run_claim() -> None:
        try:
            barrier.wait()
            with Session(disposable_engine) as session:
                claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
                    lease_owner="worker-1", lease_duration_seconds=300,
                    max_global_active=10, max_active_per_organization=10,
                )
                run_id = claimed.id if claimed is not None else None
                session.commit()
            with lock:
                outcomes["claimed_run_id"] = run_id
        except Exception as exc:  # noqa: BLE001
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=_run_terminalize), threading.Thread(target=_run_claim)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, enqueue.run_id)
        final_status = row.status
        final_lease_owner = row.lease_owner
        final_failure_class = row.failure_class

    if outcomes.get("claimed_run_id") == enqueue.run_id:
        # The worker won: the row must be started, with a lease, and the
        # terminalize attempt must have reported a safe conflict.
        assert outcomes["terminalize_ok"] is False
        assert final_status == "started"
        assert final_lease_owner == "worker-1"
        assert final_failure_class is None
    else:
        # The operator won: the row must be failed, with no lease, and
        # the claim attempt must have found nothing eligible.
        assert outcomes["terminalize_ok"] is True
        assert outcomes.get("claimed_run_id") is None
        assert final_status == "failed"
        assert final_lease_owner is None
        assert final_failure_class == "cancelled_before_start"

    # Either way, exactly one outcome won — never both, never neither.
    winners = [
        outcomes.get("terminalize_ok") is True,
        outcomes.get("claimed_run_id") == enqueue.run_id,
    ]
    assert sum(1 for w in winners if w) == 1

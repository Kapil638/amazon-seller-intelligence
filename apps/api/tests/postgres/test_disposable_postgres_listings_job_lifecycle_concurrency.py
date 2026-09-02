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
    AmazonSellerListing,
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
    """Seeds one seller account + one marketplace participation. Passing
    an existing `org_id` reuses that organization (creating a second,
    additional seller account/participation under it — `selling_partner_
    id` is freshly random per call, and is uniquely constrained globally,
    not per-organization, so this never collides) rather than attempting
    to insert `Organization` a second time, which would violate its
    primary key. Omit `org_id` (or pass a genuinely new one) to seed an
    entirely fresh organization instead."""
    reuse_org = org_id is not None
    org_id = org_id or uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        if not reuse_org or session.get(Organization, org_id) is None:
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


def test_claim_next_listings_job_does_not_hold_locks_on_unclaimed_candidates(disposable_engine) -> None:
    """Diagnostic proof for the over-locking bug this claim was rewritten
    to fix: `claim_next_listings_job` claims at most one row, so every
    OTHER eligible candidate must remain completely unlocked for the rest
    of the claiming transaction — a fully independent connection must be
    able to lock any of them *immediately* (`NOWAIT`), with no need to
    wait for this transaction to commit or roll back. The earlier
    implementation selected a whole batch of candidates with `FOR UPDATE
    SKIP LOCKED` and only chose one in Python afterward, holding row
    locks on every candidate it merely inspected; that version would fail
    this exact assertion, since the un-chosen candidates would stay
    locked (and therefore not `NOWAIT`-lockable) until the whole
    transaction ended — which is exactly what produced the 2-of-5
    under-claim this test file's other test above once required a real
    fixture fix to even reach.
    """
    org_id = uuid4()
    run_ids: list[UUID] = []
    for _ in range(3):
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

    claiming_session = Session(disposable_engine)
    try:
        claimed = AmazonIngestionRunRepository(claiming_session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert claimed is not None
        unclaimed_ids = [rid for rid in run_ids if rid != claimed.id]
        assert len(unclaimed_ids) == 2

        with disposable_engine.connect() as probe_conn:
            with probe_conn.begin():
                for rid in unclaimed_ids:
                    probe_conn.execute(
                        text("SELECT id FROM amazon_ingestion_runs WHERE id = :id FOR UPDATE NOWAIT"),
                        {"id": rid},
                    )
    finally:
        claiming_session.rollback()
        claiming_session.close()


def test_more_workers_than_jobs_yields_one_success_per_job_and_none_for_the_rest(disposable_engine) -> None:
    """Three eligible jobs, five concurrent workers: exactly three
    successes (one per job, no duplicates), and the two excess workers
    must get `None` rather than an error or a stalled claim."""
    org_id = uuid4()
    run_ids: list[UUID] = []
    for _ in range(3):
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
    assert len(outcomes) == 5
    successes = [o.run_id for o in outcomes if o.run_id is not None]
    failures = [o for o in outcomes if o.run_id is None]
    assert len(successes) == 3
    assert len(failures) == 2
    assert sorted(successes) == sorted(run_ids)
    assert len(set(successes)) == 3


def test_fewer_workers_than_jobs_leaves_the_remainder_queued_under_real_concurrency(disposable_engine) -> None:
    """Five eligible jobs, two concurrent workers: exactly two distinct
    jobs are claimed and the other three remain genuinely `queued` —
    a worker never claims more than one job per call, and a scarcity of
    workers never causes a job to be dropped or duplicated."""
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

    barrier = threading.Barrier(2)
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
        for i in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    successes = [o.run_id for o in outcomes if o.run_id is not None]
    assert len(successes) == 2
    assert len(set(successes)) == 2
    assert set(successes).issubset(set(run_ids))

    with Session(disposable_engine) as session:
        statuses = {
            row.id: row.status
            for row in session.query(AmazonIngestionRun).filter(AmazonIngestionRun.id.in_(run_ids)).all()
        }
    started = [rid for rid, status in statuses.items() if status == "started"]
    queued = [rid for rid, status in statuses.items() if status == "queued"]
    assert len(started) == 2
    assert len(queued) == 3


def test_claim_skips_a_row_locked_by_another_transaction_and_claims_the_next_one(disposable_engine) -> None:
    """A row already locked by a concurrent, uncommitted transaction
    (here a plain `FOR UPDATE` holder — not another claim call) must be
    skipped via `SKIP LOCKED` in favor of the next eligible candidate,
    never blocked on, and never left in any state but its original
    `queued` once skipped."""
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        first = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
    assert first.claimed is True

    _, seller_account_id_2, participation_id_2 = _seed_scope(disposable_engine, org_id=org_id)
    with Session(disposable_engine) as session:
        second = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id_2,
            marketplace_participation_id=participation_id_2, region="na", environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
    assert second.claimed is True

    # Force deterministic FIFO order: `first` would be picked first on
    # ordering grounds alone, so skipping it can only be due to the lock.
    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, first.run_id)
        row.created_at = datetime.now(UTC) - timedelta(minutes=5)
        session.commit()

    holder_conn = disposable_engine.connect()
    holder_txn = holder_conn.begin()
    holder_conn.execute(
        text("SELECT id FROM amazon_ingestion_runs WHERE id = :id FOR UPDATE"), {"id": first.run_id}
    )
    try:
        with Session(disposable_engine) as session:
            claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
                lease_owner="worker-1", lease_duration_seconds=300,
                max_global_active=10, max_active_per_organization=10,
            )
            # Captured as a plain value *before* `session.commit()` —
            # SQLAlchemy expires ORM attributes on commit by default, and
            # accessing them afterward (once this `with` block has closed
            # the session) raises `DetachedInstanceError`, not a silent
            # stale read.
            assert claimed is not None
            claimed_run_id = claimed.id
            session.commit()
        assert claimed_run_id == second.run_id  # skipped the locked, older row

        with Session(disposable_engine) as session:
            still_queued = session.get(AmazonIngestionRun, first.run_id)
            assert still_queued.status == "queued"  # untouched, only skipped
            started_row = session.get(AmazonIngestionRun, claimed_run_id)
            assert started_row.status == "started"
    finally:
        holder_txn.rollback()
        holder_conn.close()


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
        # Captured as plain values *before* `session.commit()` — SQLAlchemy
        # expires ORM attributes on commit by default, and accessing them
        # afterward (once this `with` block has closed the session) raises
        # `DetachedInstanceError`, not a silent stale read. This is a test
        # boundary fix only; production session behavior is unchanged.
        assert reclaimed is not None
        reclaimed_id = reclaimed.id
        reclaimed_status = reclaimed.status
        reclaimed_retry_count = reclaimed.retry_count
        session.commit()
    assert reclaimed_id == claim.run_id
    assert reclaimed_status == "started"
    assert reclaimed_retry_count == 1  # incremented only on a genuine retry reclaim


# --- 5: migration 0010 -> 0011 preserves existing rows and the downgrade
# refuses to destroy data it cannot represent. ------------------------------


_INSERT_0010_INGESTION_RUN_SQL = text(
    """
    INSERT INTO amazon_ingestion_runs (
        id, organization_id, seller_account_id, marketplace_participation_id,
        run_type, domain, region, environment, status,
        started_at, completed_at, records_received, records_accepted, records_rejected,
        retry_count, failure_class, pagination_complete, pages_fetched, reported_total_results
    ) VALUES (
        :id, :organization_id, :seller_account_id, :marketplace_participation_id,
        'listings', 'listings_items', 'na', 'PRODUCTION', :status,
        :started_at, :completed_at, :records_received, :records_accepted, :records_rejected,
        0, :failure_class, :pagination_complete, :pages_fetched, :reported_total_results
    )
    """
)

# Column set restricted to what genuinely exists at 0011 — safe to read
# back with while the database is pinned there. Does NOT select via the
# current `AmazonIngestionRun` ORM class, which (after 12B.4B) also maps
# six 0012-only counter columns; `session.get`/`session.query` against
# that class issue a `SELECT` naming every mapped column, which PostgreSQL
# would reject with `UndefinedColumn` for a table that doesn't have them
# yet. See this file's use below for the full explanation.
_SELECT_0011_INGESTION_RUN_SQL = text(
    """
    SELECT status, started_at, completed_at, records_accepted, failure_class,
           next_retry_at, last_heartbeat_at
    FROM amazon_ingestion_runs
    WHERE id = :id
    """
)


def test_migration_0011_upgrade_preserves_existing_0010_rows_and_widens_constraints(disposable_engine) -> None:
    """Pinned to the genuine `0010` schema for its seeding phase, and this
    test's entire body stays at `0011` after that — it deliberately never
    upgrades on to `0012` or `head`, to keep this test's proof exact to
    the `0010 -> 0011` boundary it names. This means the CURRENT
    `AmazonIngestionRun` ORM class is unsafe to use for `amazon_ingestion_
    runs` reads *or* writes anywhere in this test, from the initial
    `0010`-pinned seeding all the way through its final assertions —
    that ORM class now maps not only `0011`'s `next_retry_at`/
    `last_heartbeat_at` (absent at `0010`) but also `0012`'s six
    `orders_received`/`orders_accepted`/`orders_rejected`/`items_received`/
    `items_accepted`/`items_rejected` counters (absent at `0011` too, and
    each carrying a Python-side `default=0` that SQLAlchemy includes in
    every ORM-generated `INSERT` regardless of whether the caller
    mentioned it). PostgreSQL correctly rejects any such `INSERT`/`SELECT`
    with `UndefinedColumn` against a table that doesn't have those columns
    yet — a real failure this test hit in CI once migration `0012` shipped,
    fixed here by using raw SQL restricted to genuinely-`0011` columns for
    every `amazon_ingestion_runs` interaction in this test, instead of
    upgrading further (which would defeat the point of a test pinned to
    this exact boundary). `Organization`/`AmazonSellerAccount`/`Amazon
    MarketplaceParticipation`/`AmazonSellerListing` are all untouched by
    both `0011` and `0012`, so the current ORM remains valid for those
    regardless of which `amazon_ingestion_runs` revision is applied.
    """
    url = _guard.disposable_url()
    with _alembic_environment(url):
        command.downgrade(_alembic_config(url), "0010_amazon_seller_listings")

    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)

    # Representative historical evidence: one succeeded run, one failed
    # run, both genuinely pre-`0011` shaped. Expected values captured as
    # plain Python locals *before* upgrading, for after-upgrade comparison.
    succeeded_run_id = uuid4()
    failed_run_id = uuid4()
    now = datetime.now(UTC)
    succeeded_params = {
        "id": succeeded_run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
        "marketplace_participation_id": participation_id, "status": "succeeded",
        "started_at": now - timedelta(hours=2), "completed_at": now - timedelta(hours=1, minutes=55),
        "records_received": 5, "records_accepted": 5, "records_rejected": 0,
        "failure_class": None, "pagination_complete": True, "pages_fetched": 1,
        "reported_total_results": 5,
    }
    failed_params = {
        "id": failed_run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
        "marketplace_participation_id": participation_id, "status": "failed",
        "started_at": now - timedelta(hours=1), "completed_at": now - timedelta(minutes=55),
        "records_received": 2, "records_accepted": 0, "records_rejected": 2,
        "failure_class": "malformed_page", "pagination_complete": False, "pages_fetched": 1,
        "reported_total_results": 5,
    }
    with disposable_engine.begin() as conn:
        conn.execute(_INSERT_0010_INGESTION_RUN_SQL, succeeded_params)
        conn.execute(_INSERT_0010_INGESTION_RUN_SQL, failed_params)

    # Listing provenance: a real `amazon_seller_listings` row whose
    # composite FK points at the succeeded historical run. This table is
    # unaffected by `0011`, so the current ORM is safe here even while
    # `amazon_ingestion_runs` itself is still pinned to `0010`.
    with Session(disposable_engine) as session:
        listing = AmazonSellerListing(
            id=uuid4(), marketplace_participation_id=participation_id, seller_sku="HIST-SKU-1",
            status=["BUYABLE"], offers=[], fulfillment_availability=[], issues=[], product_types=[],
            is_buyable=True, is_discoverable=True, is_active=True,
            last_ingestion_run_id=succeeded_run_id,
        )
        session.add(listing)
        session.commit()
        listing_id = listing.id

    with _alembic_environment(url):
        command.upgrade(_alembic_config(url), "0011_listings_job_lifecycle")

    # Still pinned at 0011 here (the upgrade above only reached
    # 0011_listings_job_lifecycle) — read the two historical runs back with
    # raw SQL restricted to genuinely-0011 columns, NOT the current
    # `AmazonIngestionRun` ORM (see this test's own docstring for why).
    with disposable_engine.connect() as conn:
        succeeded_row = conn.execute(_SELECT_0011_INGESTION_RUN_SQL, {"id": succeeded_run_id}).mappings().one()
        failed_row = conn.execute(_SELECT_0011_INGESTION_RUN_SQL, {"id": failed_run_id}).mappings().one()
    assert succeeded_row["status"] == "succeeded"
    assert succeeded_row["started_at"] == succeeded_params["started_at"]
    assert succeeded_row["completed_at"] == succeeded_params["completed_at"]
    assert succeeded_row["records_accepted"] == 5
    assert failed_row["status"] == "failed"
    assert failed_row["failure_class"] == "malformed_page"
    # New columns correctly NULL for historical rows — nothing before
    # `0011` ever tracked either concept, so there is nothing to
    # backfill; NULL is the truthful value, not a default guess.
    assert succeeded_row["next_retry_at"] is None
    assert succeeded_row["last_heartbeat_at"] is None
    assert failed_row["next_retry_at"] is None
    assert failed_row["last_heartbeat_at"] is None

    # `amazon_seller_listings` is untouched by both 0011 and 0012, so the
    # current `AmazonSellerListing` ORM remains genuinely safe here even
    # while `amazon_ingestion_runs` itself is still pinned to 0011 — this
    # one read is deliberately still ORM-based, in contrast to the raw-SQL
    # reads immediately above, to make that distinction concrete rather
    # than implying every ORM class became unsafe.
    with Session(disposable_engine) as session:
        listing_row = session.get(AmazonSellerListing, listing_id)
        assert listing_row.last_ingestion_run_id == succeeded_run_id
        assert listing_row.seller_sku == "HIST-SKU-1"

    # Terminal historical rows (both are terminal — succeeded/failed —
    # for this exact scope) never block a *new* queued job: the widened
    # partial unique index only ever covers queued/started/waiting_to_retry.
    #
    # `AmazonIngestionRunRepository.enqueue_listings_run` cannot be called
    # here — it constructs a current `AmazonIngestionRun(...)` internally
    # and `session.add()`s it, which (per this test's docstring) is unsafe
    # while the database is pinned at 0011. This is not a gap in proof: a
    # bare `INSERT` succeeding under the widened partial unique index and
    # widened CHECK constraint *is* the schema-level fact this step needs
    # to prove — the repository method's own validation/ownership logic is
    # already covered elsewhere in this file against a fully-upgraded
    # (head) database, where that ORM usage is genuinely safe.
    queued_run_id = uuid4()
    with disposable_engine.begin() as conn:
        conn.execute(
            _INSERT_0010_INGESTION_RUN_SQL,
            {
                "id": queued_run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
                "marketplace_participation_id": participation_id, "status": "queued",
                "started_at": None, "completed_at": None,
                "records_received": 0, "records_accepted": 0, "records_rejected": 0,
                "failure_class": None, "pagination_complete": True, "pages_fetched": 0,
                "reported_total_results": None,
            },
        )
    with disposable_engine.connect() as conn:
        queued_status = conn.execute(
            text("SELECT status FROM amazon_ingestion_runs WHERE id = :id"), {"id": queued_run_id}
        ).scalar_one()
    assert queued_status == "queued"

    # The widened CHECK constraint now genuinely accepts the new states.
    # A second, genuinely seeded scope is used here (reusing the same org)
    # rather than a fabricated participation id — the FK to
    # `amazon_marketplace_participations` is real, and the original
    # `participation_id` already has a queued row from the insert above,
    # so reusing it here would collide with the widened partial unique
    # index instead of proving anything new. Raw SQL again, for the same
    # 0011-pinned-database reason as above — not the current
    # `AmazonIngestionRun(...)` ORM constructor.
    _, seller_account_id_2, participation_id_2 = _seed_scope(disposable_engine, org_id=org_id)
    second_queued_run_id = uuid4()
    with disposable_engine.begin() as conn:
        conn.execute(
            _INSERT_0010_INGESTION_RUN_SQL,
            {
                "id": second_queued_run_id, "organization_id": org_id, "seller_account_id": seller_account_id_2,
                "marketplace_participation_id": participation_id_2, "status": "queued",
                "started_at": None, "completed_at": None,
                "records_received": 0, "records_accepted": 0, "records_rejected": 0,
                "failure_class": None, "pagination_complete": True, "pages_fetched": 0,
                "reported_total_results": None,
            },
        )
    with disposable_engine.connect() as conn:
        second_row = conn.execute(
            text("SELECT status, started_at FROM amazon_ingestion_runs WHERE id = :id"),
            {"id": second_queued_run_id},
        ).mappings().one()
    assert second_row["status"] == "queued"
    assert second_row["started_at"] is None


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

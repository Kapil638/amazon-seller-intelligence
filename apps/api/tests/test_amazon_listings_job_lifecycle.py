"""12B.3G — AmazonIngestionRunRepository durable Listings job lifecycle:
`enqueue_listings_run`, `get_active_listings_run`, `claim_next_listings_job`,
`reschedule_listings_run_for_retry`, and the two admission-control count
methods. Dedicated file-based SQLite engine (genuine write-write
contention), matching `test_amazon_ingestion_run_listings_claim.py`'s own
pattern for the pre-existing `claim_listings_run` path.

`claim_next_listings_job`'s `SELECT ... FOR UPDATE SKIP LOCKED` claim is a
no-op on SQLite — nothing here proves its behavior under real concurrent
claimants; that proof lives only in
`tests/postgres/test_disposable_postgres_listings_job_lifecycle_concurrency.py`.
Everything tested here is single-threaded, sequential behavior: ordering,
eligibility windows, concurrency-limit counting, and bookkeeping — all of
which SQLite can prove just as well as PostgreSQL, since none of it
depends on row-level locking.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    Base,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository


def _dedicated_engine(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine


def _seed_scope(engine, *, org_id=None) -> tuple:
    reuse_org = org_id is not None
    org_id = org_id or uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        if not reuse_org or session.get(Organization, org_id) is None:
            session.add(Organization(id=org_id, name="12B.3G Job Lifecycle Test Org"))
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


def _enqueue(engine, org_id, seller_account_id, participation_id):
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
        )
        session.commit()
        return claim


# --- enqueue_listings_run --------------------------------------------------


def test_enqueue_creates_a_queued_run_with_no_lease_and_no_started_at(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_basic")
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    claim = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert claim.claimed is True

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "queued"
        assert row.started_at is None
        assert row.lease_owner is None
        assert row.lease_expires_at is None
        assert row.retry_count == 0


def test_enqueue_blocked_while_a_queued_run_is_active(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_blocked_queued")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    first = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert first.claimed is True

    second = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert second.claimed is False
    assert second.reason == "already_running"


def test_enqueue_blocked_while_a_started_run_is_active(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_blocked_started")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    result = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert result.claimed is False
    assert result.reason == "already_running"


def test_enqueue_blocked_while_waiting_to_retry(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_blocked_waiting")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=datetime.now(UTC) + timedelta(seconds=60), failure_class="throttled",
        )
        session.commit()

    result = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert result.claimed is False
    assert result.reason == "already_running"


def test_enqueue_allowed_after_previous_run_succeeded(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_after_success")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", status="succeeded",
        )
        session.commit()

    result = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert result.claimed is True


def test_enqueue_reclaims_a_stale_started_run_first(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_reclaims_stale")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=uuid4(), organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, run_type="listings",
                domain="listings_items", region="na", environment="PRODUCTION", status="started",
                lease_owner="crashed-worker", lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    result = _enqueue(engine, org_id, seller_account_id, participation_id)
    assert result.claimed is True

    with Session(engine) as session:
        rows = session.query(AmazonIngestionRun).filter_by(
            seller_account_id=seller_account_id, marketplace_participation_id=participation_id
        ).all()
        statuses = sorted(row.status for row in rows)
        assert statuses == ["queued", "timed_out"]


# --- get_active_listings_run ------------------------------------------------


def test_get_active_listings_run_returns_none_when_nothing_active(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "active_none")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        active = AmazonIngestionRunRepository(session).get_active_listings_run(org_id, participation_id)
    assert active is None


def test_get_active_listings_run_finds_a_queued_row(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "active_queued")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)
    with Session(engine) as session:
        active = AmazonIngestionRunRepository(session).get_active_listings_run(org_id, participation_id)
    assert active is not None
    assert active.id == claim.run_id
    assert active.status == "queued"


def test_get_active_listings_run_ignores_terminal_runs(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "active_ignores_terminal")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", status="failed",
        )
        session.commit()

    with Session(engine) as session:
        active = AmazonIngestionRunRepository(session).get_active_listings_run(org_id, participation_id)
    assert active is None


# --- claim_next_listings_job -------------------------------------------------


def test_claim_next_listings_job_returns_none_when_no_eligible_jobs(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_none")
    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
    assert claimed is None


def test_claim_next_listings_job_claims_oldest_queued_first(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_fifo")
    org_id = uuid4()
    run_ids = []
    for _ in range(3):
        _, seller_account_id, participation_id = _seed_scope(engine, org_id=org_id)
        claim = _enqueue(engine, org_id, seller_account_id, participation_id)
        run_ids.append(claim.run_id)
        # created_at has second-level-or-better resolution; force a
        # deterministic ordering rather than relying on real elapsed time.
        with Session(engine) as session:
            row = session.get(AmazonIngestionRun, claim.run_id)
            row.created_at = datetime.now(UTC) + timedelta(seconds=len(run_ids))
            session.commit()

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert claimed is not None
        claimed_id = claimed.id
        session.commit()
    assert claimed_id == run_ids[0]


def test_claim_next_listings_job_claims_a_due_waiting_to_retry_row(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_due_retry")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=datetime.now(UTC) - timedelta(seconds=1), failure_class="throttled",
        )
        session.commit()

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert claimed is not None
        assert claimed.id == claim.run_id
        assert claimed.status == "started"
        assert claimed.retry_count == 1
        session.commit()


def test_claim_next_listings_job_skips_a_not_yet_due_waiting_to_retry_row(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_not_due")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=datetime.now(UTC) + timedelta(minutes=10), failure_class="throttled",
        )
        session.commit()

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
    assert claimed is None


def test_claim_next_listings_job_respects_global_limit(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_global_limit")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-existing", lease_duration_seconds=300,
        )
        session.commit()

    org_id_2, seller_account_id_2, participation_id_2 = _seed_scope(engine, org_id=uuid4())
    _enqueue(engine, org_id_2, seller_account_id_2, participation_id_2)

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=1, max_active_per_organization=10,
        )
    assert claimed is None


def test_claim_next_listings_job_respects_per_organization_limit(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_org_limit")
    org_id = uuid4()
    _, seller_account_id_a, participation_id_a = _seed_scope(engine, org_id=org_id)
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id_a,
            marketplace_participation_id=participation_id_a, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-existing", lease_duration_seconds=300,
        )
        session.commit()

    _, seller_account_id_b, participation_id_b = _seed_scope(engine, org_id=org_id)
    _enqueue(engine, org_id, seller_account_id_b, participation_id_b)

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=1,
        )
    assert claimed is None


def test_claim_next_listings_job_sets_started_at_only_on_first_claim(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_started_at_once")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)

    with Session(engine) as session:
        first = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        session.commit()
        first_started_at = first.started_at
    assert first_started_at is not None

    with Session(engine) as session:
        AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="worker-1",
            next_retry_at=datetime.now(UTC) - timedelta(seconds=1), failure_class="throttled",
        )
        session.commit()

    with Session(engine) as session:
        second = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert second.started_at == first_started_at  # preserved, not reset
        assert second.retry_count == 1
        session.commit()


def test_claim_next_listings_job_reclaims_a_stale_started_row_first(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_stale_started")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=uuid4(), organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, run_type="listings",
                domain="listings_items", region="na", environment="PRODUCTION", status="started",
                lease_owner="crashed-worker", lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
                started_at=datetime.now(UTC) - timedelta(hours=2),
            )
        )
        session.commit()
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert claimed is not None
        assert claimed.id == claim.run_id  # the fresh queued job, not the stale one
        session.commit()

    with Session(engine) as session:
        stale_rows = session.query(AmazonIngestionRun).filter_by(
            seller_account_id=seller_account_id, marketplace_participation_id=participation_id, status="timed_out",
        ).all()
        assert len(stale_rows) == 1


# --- reschedule_listings_run_for_retry --------------------------------------


def test_reschedule_transitions_to_waiting_to_retry_and_clears_lease(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "reschedule_basic")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    next_retry_at = datetime.now(UTC) + timedelta(seconds=90)

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=next_retry_at, failure_class="throttled",
            pages_fetched=2, records_received=20, reported_total_results=20,
        )
        session.commit()
    assert ok is True

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "waiting_to_retry"
        assert row.lease_owner is None
        assert row.lease_expires_at is None
        assert row.failure_class == "throttled"
        assert row.pages_fetched == 2
        assert row.records_received == 20


def test_reschedule_fails_for_wrong_lease_owner(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "reschedule_wrong_owner")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="someone-else",
            next_retry_at=datetime.now(UTC) + timedelta(seconds=30), failure_class="throttled",
        )
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"


def test_reschedule_fails_when_run_is_not_started(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "reschedule_not_started")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)  # status='queued'

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="owner-1",
            next_retry_at=datetime.now(UTC) + timedelta(seconds=30), failure_class="throttled",
        )
    assert ok is False


# --- count_active_listings_runs_for_organization / _global -----------------


def test_count_active_listings_runs_for_organization(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "count_org")
    org_id = uuid4()
    for _ in range(2):
        _, seller_account_id, participation_id = _seed_scope(engine, org_id=org_id)
        _enqueue(engine, org_id, seller_account_id, participation_id)
    other_org, other_seller, other_participation = _seed_scope(engine)
    _enqueue(engine, other_org, other_seller, other_participation)

    with Session(engine) as session:
        count = AmazonIngestionRunRepository(session).count_active_listings_runs_for_organization(org_id)
    assert count == 2


def test_count_active_listings_runs_for_organization_excludes_terminal(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "count_org_excludes_terminal")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", status="succeeded",
        )
        session.commit()

    with Session(engine) as session:
        count = AmazonIngestionRunRepository(session).count_active_listings_runs_for_organization(org_id)
    assert count == 0


def test_count_active_listings_runs_global_spans_organizations(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "count_global")
    for _ in range(3):
        org_id, seller_account_id, participation_id = _seed_scope(engine)
        _enqueue(engine, org_id, seller_account_id, participation_id)

    with Session(engine) as session:
        count = AmazonIngestionRunRepository(session).count_active_listings_runs_global()
    assert count == 3


# --- 12B.3G follow-up: a `queued` job has NO age-based expiration -------
#
# Live-observed question (2026-08-29): does an unclaimed `queued` job
# ever "recover" via lease expiry? No. Every stale-reclaim `UPDATE` in
# this repository (`claim_listings_run`, `enqueue_listings_run`,
# `claim_next_listings_job`) is gated on `status == 'started' AND
# lease_expires_at IS NOT NULL AND lease_expires_at < now()`. A `queued`
# row has `status='queued'` and `lease_expires_at=NULL` — it structurally
# cannot match any of those predicates, no matter how old `created_at`
# is. These tests seed a queued row with a `created_at` far in the past
# (30 days) and prove every one of those code paths leaves it completely
# untouched.


def _old_queued_row(engine, scope: dict, *, age_days: int = 30) -> None:
    with Session(engine) as session:
        row = AmazonIngestionRun(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            run_type="listings",
            domain="listings_items",
            region="na",
            environment="PRODUCTION",
            status="queued",
            created_at=datetime.now(UTC) - timedelta(days=age_days),
        )
        session.add(row)
        session.commit()
        return row.id


def _snapshot(engine, run_id) -> dict:
    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        return {
            "status": row.status,
            "lease_owner": row.lease_owner,
            "lease_expires_at": row.lease_expires_at,
            "started_at": row.started_at,
            "last_heartbeat_at": row.last_heartbeat_at,
            "completed_at": row.completed_at,
            "retry_count": row.retry_count,
            "next_retry_at": row.next_retry_at,
            "failure_class": row.failure_class,
        }


def test_an_old_queued_row_is_not_reclaimed_by_claim_listings_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "old_queued_not_reclaimed_by_claim")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    scope = {"organization_id": org_id, "seller_account_id": seller_account_id, "marketplace_participation_id": participation_id}
    run_id = _old_queued_row(engine, scope, age_days=30)
    before = _snapshot(engine, run_id)

    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="fresh-attempt", lease_duration_seconds=300,
        )
        session.commit()

    # Still blocked — the 30-day-old queued row remains the active scope
    # holder; the claim attempt does not reclaim it.
    assert claim.claimed is False
    assert claim.reason == "already_running"
    assert _snapshot(engine, run_id) == before


def test_an_old_queued_row_is_not_reclaimed_by_enqueue_listings_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "old_queued_not_reclaimed_by_enqueue")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    scope = {"organization_id": org_id, "seller_account_id": seller_account_id, "marketplace_participation_id": participation_id}
    run_id = _old_queued_row(engine, scope, age_days=30)
    before = _snapshot(engine, run_id)

    result = _enqueue(engine, org_id, seller_account_id, participation_id)

    assert result.claimed is False
    assert result.reason == "already_running"
    assert _snapshot(engine, run_id) == before


def test_an_old_queued_row_is_not_reclaimed_by_claim_next_listings_job_unless_a_worker_actually_calls_it(tmp_path) -> None:
    """`claim_next_listings_job` (the worker's own entry point) DOES claim
    an old queued row — that is the intended, correct behavior (a queued
    job, however old, must remain claimable). The point of this test is
    the *negative* case above: nothing else ever calls this method on its
    own. This test exists to make that contrast explicit rather than
    leave it implicit."""
    engine = _dedicated_engine(tmp_path, "old_queued_reclaimed_only_by_worker_call")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    scope = {"organization_id": org_id, "seller_account_id": seller_account_id, "marketplace_participation_id": participation_id}
    run_id = _old_queued_row(engine, scope, age_days=30)

    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert claimed is not None
        assert claimed.id == run_id
        assert claimed.status == "started"  # only because this test explicitly invoked the worker's own claim call
        session.commit()


def test_an_old_queued_row_can_remain_queued_indefinitely_with_no_automatic_process_touching_it(tmp_path) -> None:
    """No age-based sweep exists anywhere in this repository. Simulates
    the passage of a long period (only `created_at` ages — nothing else
    in this test advances real or fake time, since nothing in the
    production code path is time-driven for `queued` rows at all) and
    confirms the row is bit-for-bit unchanged with no call made at all."""
    engine = _dedicated_engine(tmp_path, "old_queued_indefinite")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    scope = {"organization_id": org_id, "seller_account_id": seller_account_id, "marketplace_participation_id": participation_id}
    run_id = _old_queued_row(engine, scope, age_days=365)

    assert _snapshot(engine, run_id)["status"] == "queued"
    # Reading it (as any GET endpoint would) additional times changes nothing.
    for _ in range(3):
        with Session(engine) as session:
            row = session.get(AmazonIngestionRun, run_id)
            assert row.status == "queued"
    assert _snapshot(engine, run_id)["status"] == "queued"


def test_get_active_listings_run_never_mutates_an_old_queued_row(tmp_path) -> None:
    """Proves the read path a GET endpoint actually uses performs no
    write — a plain SELECT, called repeatedly, must never change
    anything about the row it reads."""
    engine = _dedicated_engine(tmp_path, "read_path_never_mutates")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    scope = {"organization_id": org_id, "seller_account_id": seller_account_id, "marketplace_participation_id": participation_id}
    run_id = _old_queued_row(engine, scope, age_days=30)
    before = _snapshot(engine, run_id)

    for _ in range(5):
        with Session(engine) as session:
            active = AmazonIngestionRunRepository(session).get_active_listings_run(org_id, participation_id)
            assert active is not None
            assert active.id == run_id

    assert _snapshot(engine, run_id) == before


# --- 12B.3G follow-up: execution-capacity claiming, fairness, no starvation


def test_claim_next_listings_job_claims_configured_number_leaving_remainder_queued(tmp_path) -> None:
    """Three queued jobs, global execution limit of 2: exactly two claims
    succeed and one queued job remains — the limit bounds *simultaneous
    execution*, never how many jobs may be queued."""
    engine = _dedicated_engine(tmp_path, "claim_configured_number")
    org_id = uuid4()
    run_ids = []
    for _ in range(3):
        _, seller_account_id, participation_id = _seed_scope(engine, org_id=org_id)
        claim = _enqueue(engine, org_id, seller_account_id, participation_id)
        run_ids.append(claim.run_id)

    claimed_ids = []
    for i in range(3):
        with Session(engine) as session:
            claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
                lease_owner=f"worker-{i}", lease_duration_seconds=300,
                max_global_active=2, max_active_per_organization=10,
            )
            if claimed is not None:
                claimed_ids.append(claimed.id)
            session.commit()

    assert len(claimed_ids) == 2
    with Session(engine) as session:
        statuses = {
            row.id: row.status
            for row in session.query(AmazonIngestionRun).filter(AmazonIngestionRun.id.in_(run_ids)).all()
        }
    started = [rid for rid, status in statuses.items() if status == "started"]
    queued = [rid for rid, status in statuses.items() if status == "queued"]
    assert len(started) == 2
    assert len(queued) == 1


def test_claim_next_listings_job_skips_a_maxed_out_organization_for_a_later_job_from_another(tmp_path) -> None:
    """Fairness / no-starvation: organization A has two queued jobs
    (both older, so first in FIFO order); organization B has one queued
    job created afterward. With `max_active_per_organization=1`, the
    second claim attempt must skip A's second (already-blocked) job and
    claim B's job instead — a single high-volume organization can never
    monopolize every claim just because its jobs happen to be older."""
    engine = _dedicated_engine(tmp_path, "claim_fairness")
    org_a = uuid4()
    org_b = uuid4()

    a_run_ids = []
    for i in range(2):
        _, seller_account_id, participation_id = _seed_scope(engine, org_id=org_a)
        claim = _enqueue(engine, org_a, seller_account_id, participation_id)
        with Session(engine) as session:
            row = session.get(AmazonIngestionRun, claim.run_id)
            row.created_at = datetime.now(UTC) - timedelta(minutes=10 - i)  # older than org B's job below
            session.commit()
        a_run_ids.append(claim.run_id)

    _, seller_account_id_b, participation_id_b = _seed_scope(engine, org_id=org_b)
    claim_b = _enqueue(engine, org_b, seller_account_id_b, participation_id_b)

    with Session(engine) as session:
        first = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=1,
        )
        assert first is not None
        assert first.id == a_run_ids[0]  # oldest overall, org A not yet at its limit
        session.commit()

    with Session(engine) as session:
        second = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-2", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=1,
        )
        # Org A's second job is next in raw FIFO order but org A is
        # already at its per-organization limit (1 started) — the claim
        # must skip it and land on org B's job instead.
        assert second is not None
        assert second.id == claim_b.run_id
        session.commit()

    with Session(engine) as session:
        a_second_row = session.get(AmazonIngestionRun, a_run_ids[1])
        assert a_second_row.status == "queued"  # never starved permanently — just not yet


# --- 12B.3G follow-up: safe queued-job terminalization (operator-only) ---


def test_terminalize_unclaimed_listings_run_succeeds_for_a_genuinely_unclaimed_queued_row(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_success")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, claim.run_id)
        session.commit()
    assert ok is True

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "failed"
        assert row.failure_class == "cancelled_before_start"
        assert row.completed_at is not None
        assert row.pagination_complete is False
        assert row.started_at is None
        assert row.lease_owner is None
        assert row.lease_expires_at is None
        assert row.last_heartbeat_at is None
        assert row.records_received == 0
        assert row.records_accepted == 0
        assert row.records_rejected == 0
        assert row.pages_fetched == 0


def test_terminalize_unclaimed_listings_run_accepts_a_custom_sanitized_reason(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_custom_reason")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            org_id, claim.run_id, failure_class="worker_not_deployed"
        )
        session.commit()
    assert ok is True

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.failure_class == "worker_not_deployed"


def test_terminalize_unclaimed_listings_run_refuses_a_started_row(tmp_path) -> None:
    """The core race-safety guarantee: a job a worker has already claimed
    (even a fraction of a second ago) must never be terminalized by this
    operation — it is no longer 'unclaimed', by definition."""
    engine = _dedicated_engine(tmp_path, "terminalize_refuses_started")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="worker-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, claim.run_id)
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"
        assert row.lease_owner == "worker-1"


def test_terminalize_unclaimed_listings_run_refuses_a_waiting_to_retry_row(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_refuses_waiting")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="worker-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            org_id, claim.run_id, lease_owner="worker-1",
            next_retry_at=datetime.now(UTC) + timedelta(minutes=5), failure_class="throttled",
        )
        session.commit()

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, claim.run_id)
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "waiting_to_retry"


def test_terminalize_unclaimed_listings_run_refuses_an_already_terminal_row(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_refuses_terminal")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="worker-1", lease_duration_seconds=300,
        )
        session.commit()
    with Session(engine) as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, claim.run_id, lease_owner="worker-1", status="succeeded",
        )
        session.commit()

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, claim.run_id)
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "succeeded"


def test_terminalize_unclaimed_listings_run_refuses_a_foreign_organization(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_refuses_foreign_org")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(uuid4(), claim.run_id)
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "queued"


def test_terminalize_unclaimed_listings_run_never_touches_a_marketplace_participations_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_never_touches_marketplace_participations")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        row = AmazonIngestionRun(
            id=uuid4(), organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, run_type="marketplace_participations",
            domain="marketplace_participations", region="na", environment="PRODUCTION",
            status="started",
        )
        session.add(row)
        session.commit()
        run_id = row.id

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, run_id)
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        assert row.status == "started"
        assert row.run_type == "marketplace_participations"


def test_terminalize_unclaimed_listings_run_returns_false_for_a_nonexistent_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "terminalize_nonexistent")
    org_id, _seller_account_id, _participation_id = _seed_scope(engine)

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, uuid4())
        session.commit()
    assert ok is False


def test_terminalize_race_a_concurrent_worker_claim_wins_and_terminalize_then_fails(tmp_path) -> None:
    """The exact race this operation exists to be safe against: an
    operator observes a job as queued, but a worker claims it a moment
    later, before the operator's terminalize call actually executes.
    Sequential here (SQLite has no real concurrent claimants — see this
    file's own module docstring), but proves the COMPARE-AND-SET itself
    correctly refuses once the row's state has moved on, which is the
    only thing that matters for correctness regardless of timing."""
    engine = _dedicated_engine(tmp_path, "terminalize_race")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    claim = _enqueue(engine, org_id, seller_account_id, participation_id)

    # The worker wins the race.
    with Session(engine) as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_listings_job(
            lease_owner="worker-1", lease_duration_seconds=300,
            max_global_active=10, max_active_per_organization=10,
        )
        assert claimed is not None
        assert claimed.id == claim.run_id
        session.commit()

    # The operator's terminalize call, arriving after, must now fail.
    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(org_id, claim.run_id)
        session.commit()
    assert ok is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"
        assert row.lease_owner == "worker-1"

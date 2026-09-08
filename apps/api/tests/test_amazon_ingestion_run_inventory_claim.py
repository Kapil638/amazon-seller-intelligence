"""12B.6B — AmazonIngestionRunRepository inventory-run enqueue/claim/lease/
heartbeat/completion. Dedicated file-based SQLite engine (genuine write-
write contention, matching the pattern already used for
`test_amazon_ingestion_run_listings_claim.py`) — real PostgreSQL proof of
the same guarantees under genuine concurrency lives in
`tests/postgres/test_disposable_postgres_inventory_migration.py`.

Inventory's enqueue/claim shape is two steps (`enqueue_inventory_run`
followed by a separate worker's `claim_next_inventory_job`), unlike
Listings' single combined `claim_listings_run` — this file's tests are
adapted accordingly rather than mirrored line-for-line.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
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


def _seed_scope(engine) -> tuple:
    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="12B.6B Claim Test Org"))
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


def _enqueue(session, org_id, seller_account_id, participation_id, **overrides):
    return AmazonIngestionRunRepository(session).enqueue_inventory_run(
        organization_id=org_id, seller_account_id=seller_account_id,
        marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
        connection_id=None, **overrides,
    )


def _claim(session, *, lease_owner: str, max_global_active=4, max_active_per_organization=1):
    return AmazonIngestionRunRepository(session).claim_next_inventory_job(
        lease_owner=lease_owner, lease_duration_seconds=300, max_global_active=max_global_active,
        max_active_per_organization=max_active_per_organization,
    )


def test_successful_enqueue_then_claim(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "enqueue_claim_success")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        enqueued = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    assert enqueued.claimed is True
    assert enqueued.run_id is not None

    with Session(engine) as session:
        claimed = _claim(session, lease_owner="worker-1")
        assert claimed is not None
        assert claimed.id == enqueued.run_id
        assert claimed.status == "started"
        assert claimed.lease_owner == "worker-1"
        session.commit()


def test_active_scope_blocks_a_second_enqueue() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        first = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    assert first.claimed is True

    with Session(engine) as session:
        second = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    assert second.claimed is False
    assert second.reason == "already_running"


def test_stale_started_lease_is_terminalized_by_enqueue_and_a_fresh_run_can_then_be_claimed() -> None:
    """`enqueue_inventory_run` itself terminalizes a crashed worker's
    expired `started` lease before inserting the new `queued` row — the
    two-step flow's own reclaim path, distinct from Listings' single
    combined `claim_listings_run`."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    stale_run_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=stale_run_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="inventory",
                domain="fba_inventory",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="crashed-owner",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    with Session(engine) as session:
        enqueued = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    assert enqueued.claimed is True
    assert enqueued.run_id != stale_run_id

    with Session(engine) as session:
        stale_row = session.get(AmazonIngestionRun, stale_run_id)
        assert stale_row.status == "timed_out"
        assert stale_row.failure_class == "lease_expired"

    with Session(engine) as session:
        claimed = _claim(session, lease_owner="new-owner")
        assert claimed is not None
        assert claimed.id == enqueued.run_id
        session.commit()


def test_claim_alone_terminalizes_a_stale_lease_even_with_nothing_queued_to_claim() -> None:
    """`claim_next_inventory_job`'s own stale-reclaim sweep runs
    independently of whether any queued/waiting_to_retry row exists —
    it must not leave an expired lease outstanding just because there is
    nothing else to hand out."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    stale_run_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=stale_run_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="inventory",
                domain="fba_inventory",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="crashed-owner",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    with Session(engine) as session:
        claimed = _claim(session, lease_owner="new-owner")
        session.commit()
    assert claimed is None

    with Session(engine) as session:
        stale_row = session.get(AmazonIngestionRun, stale_run_id)
        assert stale_row.status == "timed_out"
        assert stale_row.failure_class == "lease_expired"


def test_completed_run_releases_scope_for_a_new_enqueue() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        enqueued = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    with Session(engine) as session:
        claimed = _claim(session, lease_owner="worker-1")
        session.commit()
        run_id = claimed.id

    with Session(engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_inventory_run(
            org_id, run_id, lease_owner="worker-1", status="succeeded", records_received=5,
            records_accepted=5, pages_fetched=1, reported_total_results=5, pagination_complete=True,
        )
        session.commit()
    assert completed is True

    with Session(engine) as session:
        second = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    assert second.claimed is True


def test_failed_run_also_releases_scope() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    with Session(engine) as session:
        claimed = _claim(session, lease_owner="worker-1")
        session.commit()
        run_id = claimed.id

    with Session(engine) as session:
        AmazonIngestionRunRepository(session).complete_inventory_run(
            org_id, run_id, lease_owner="worker-1", status="failed", failure_class="malformed_page",
        )
        session.commit()

    with Session(engine) as session:
        second = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    assert second.claimed is True


def test_different_marketplaces_run_independently() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_a = _seed_scope(engine)
    participation_b = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id="A2EUQ1WTGCTBG2", region="eu",
            )
        )
        session.commit()

    with Session(engine) as session:
        claim_a = _enqueue(session, org_id, seller_account_id, participation_a)
        claim_b = _enqueue(session, org_id, seller_account_id, participation_b)
        session.commit()
    assert claim_a.claimed is True
    assert claim_b.claimed is True


def test_heartbeat_extends_lease_and_updates_progress() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    with Session(engine) as session:
        claimed = _claim(session, lease_owner="worker-1")
        session.commit()
        run_id = claimed.id

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).heartbeat_inventory_run(
            org_id, run_id, lease_owner="worker-1", lease_duration_seconds=300, pages_fetched=3,
        )
        session.commit()
    assert ok is True
    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        assert row.pages_fetched == 3


def test_heartbeat_and_completion_fail_after_lease_is_stolen() -> None:
    """Compare-and-set guarantee: once a stale-reclaim steals the scope,
    the original (crashed-then-revived) owner's heartbeat and completion
    calls both fail closed instead of silently succeeding."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    stale_run_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                id=stale_run_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, run_type="inventory", domain="fba_inventory",
                region="na", environment="PRODUCTION", status="started", lease_owner="original-owner",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    with Session(engine) as session:
        enqueued = _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    with Session(engine) as session:
        claimed = _claim(session, lease_owner="new-owner")
        session.commit()
        assert claimed.id == enqueued.run_id

    with Session(engine) as session:
        heartbeat_ok = AmazonIngestionRunRepository(session).heartbeat_inventory_run(
            org_id, stale_run_id, lease_owner="original-owner", lease_duration_seconds=300, pages_fetched=1,
        )
        session.commit()
    assert heartbeat_ok is False

    with Session(engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_inventory_run(
            org_id, stale_run_id, lease_owner="original-owner", status="succeeded",
        )
        session.commit()
    assert completed is False


def test_heartbeat_fails_once_lease_has_expired_even_with_no_replacement_worker() -> None:
    """An expired lease must fail closed even when nothing has reclaimed
    the scope yet — matching on `lease_owner` and `status='started'`
    alone is not enough."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    with Session(engine) as session:
        claimed = _claim(session, lease_owner="worker-1")
        session.commit()
        run_id = claimed.id

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).heartbeat_inventory_run(
            org_id, run_id, lease_owner="worker-1", lease_duration_seconds=300, pages_fetched=1,
        )
        session.commit()
    assert ok is False


def test_enqueue_rejects_cross_organization_seller_account() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_a, seller_account_a, participation_a = _seed_scope(engine)
    org_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Other Org"))
        session.commit()

    with Session(engine) as session:
        with pytest.raises(TypeError):
            _enqueue(session, org_b, seller_account_a, participation_a)


def test_enqueue_rejects_participation_belonging_to_another_seller_account() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_a, participation_a = _seed_scope(engine)
    seller_account_b = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonSellerAccount(
                id=seller_account_b, organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active",
            )
        )
        session.commit()

    with Session(engine) as session:
        with pytest.raises(TypeError):
            _enqueue(session, org_id, seller_account_b, participation_a)


def test_global_concurrency_limit_gates_claim_across_organizations() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_a, seller_account_a, participation_a = _seed_scope(engine)
    org_b = uuid4()
    seller_account_b = uuid4()
    participation_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Second Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_b, organization_id=org_b,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b, organization_id=org_b, seller_account_id=seller_account_b,
                marketplace_id="ATVPDKIKX0DER", region="na",
            )
        )
        session.commit()

    with Session(engine) as session:
        _enqueue(session, org_a, seller_account_a, participation_a)
        _enqueue(session, org_b, seller_account_b, participation_b)
        session.commit()

    with Session(engine) as session:
        first = _claim(session, lease_owner="worker-1", max_global_active=1, max_active_per_organization=1)
        session.commit()
    assert first is not None

    with Session(engine) as session:
        second = _claim(session, lease_owner="worker-2", max_global_active=1, max_active_per_organization=1)
        session.commit()
    assert second is None


def test_per_organization_concurrency_limit_gates_claim_within_one_organization() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_a = _seed_scope(engine)
    participation_b = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id="A2EUQ1WTGCTBG2", region="eu",
            )
        )
        session.commit()

    with Session(engine) as session:
        _enqueue(session, org_id, seller_account_id, participation_a)
        _enqueue(session, org_id, seller_account_id, participation_b)
        session.commit()

    with Session(engine) as session:
        first = _claim(session, lease_owner="worker-1", max_global_active=4, max_active_per_organization=1)
        session.commit()
    assert first is not None

    with Session(engine) as session:
        second = _claim(session, lease_owner="worker-2", max_global_active=4, max_active_per_organization=1)
        session.commit()
    assert second is None


def test_waiting_to_retry_row_is_not_claimed_before_its_retry_time() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        _enqueue(session, org_id, seller_account_id, participation_id)
        session.commit()
    with Session(engine) as session:
        claimed = _claim(session, lease_owner="worker-1")
        session.commit()
        run_id = claimed.id

    with Session(engine) as session:
        AmazonIngestionRunRepository(session).reschedule_inventory_run_for_retry(
            org_id, run_id, lease_owner="worker-1", next_retry_at=datetime.now(UTC) + timedelta(hours=1),
            failure_class="throttled",
        )
        session.commit()

    with Session(engine) as session:
        premature = _claim(session, lease_owner="worker-2")
        session.commit()
    assert premature is None

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, run_id)
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(engine) as session:
        reclaimed = _claim(session, lease_owner="worker-3")
        assert reclaimed is not None
        assert reclaimed.id == run_id
        assert reclaimed.retry_count == 1
        session.commit()

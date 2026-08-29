"""12B.3D — AmazonIngestionRunRepository listings-run claim/lease/heartbeat/
completion. Dedicated file-based SQLite engine (genuine write-write
contention, matching the pattern already used for the 12B.3B partial
unique index tests) — real PostgreSQL proof lives in
`tests/postgres/test_disposable_postgres_listings_run_claim_concurrency.py`.
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
        session.add(Organization(id=org_id, name="12B.3D Claim Test Org"))
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


def test_successful_claim(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "claim_success")
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="owner-1",
            lease_duration_seconds=300,
        )
        session.commit()
    assert claim.claimed is True
    assert claim.run_id is not None


def test_unexpired_run_blocks_a_second_claim() -> None:
    from sqlalchemy import create_engine as _ce

    engine = _ce("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)
    with Session(engine) as session:
        first = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="owner-1",
            lease_duration_seconds=300,
        )
        session.commit()
    assert first.claimed is True

    with Session(engine) as session:
        second = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="owner-2",
            lease_duration_seconds=300,
        )
        session.commit()
    assert second.claimed is False
    assert second.reason == "already_running"


def test_expired_run_is_safely_terminalized_and_reclaimed() -> None:
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
                run_type="listings",
                domain="listings_items",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="crashed-owner",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="new-owner",
            lease_duration_seconds=300,
        )
        session.commit()

    assert claim.claimed is True
    assert claim.run_id != stale_run_id
    with Session(engine) as session:
        stale_row = session.get(AmazonIngestionRun, stale_run_id)
        assert stale_row.status == "timed_out"
        assert stale_row.failure_class == "lease_expired"


def test_completed_run_releases_scope_for_a_new_claim() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        repo = AmazonIngestionRunRepository(session)
        first = repo.claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="owner-1",
            lease_duration_seconds=300,
        )
        session.commit()
    assert first.claimed

    with Session(engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, first.run_id, lease_owner="owner-1", status="succeeded", records_received=5,
            records_accepted=5, pages_fetched=1, reported_total_results=5, pagination_complete=True,
        )
        session.commit()
    assert completed is True

    with Session(engine) as session:
        second = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            region="na",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="owner-2",
            lease_duration_seconds=300,
        )
        session.commit()
    assert second.claimed is True


def test_failed_run_also_releases_scope() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        first = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, first.run_id, lease_owner="owner-1", status="failed", failure_class="malformed_page",
        )
        session.commit()

    with Session(engine) as session:
        second = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-2", lease_duration_seconds=300,
        )
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
                id=participation_b,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_id="A2EUQ1WTGCTBG2",
                region="eu",
            )
        )
        session.commit()

    with Session(engine) as session:
        claim_a = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_a, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-a", lease_duration_seconds=300,
        )
        claim_b = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_b, region="eu", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-b", lease_duration_seconds=300,
        )
        session.commit()
    assert claim_a.claimed is True
    assert claim_b.claimed is True


def test_heartbeat_extends_lease_and_updates_progress() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", lease_duration_seconds=300, pages_fetched=3,
        )
        session.commit()
    assert ok is True
    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.pages_fetched == 3


def test_heartbeat_and_completion_fail_after_lease_is_stolen() -> None:
    """Proves the compare-and-set guarantee: once a stale-reclaim steals the
    scope, the original (crashed-then-revived) owner's heartbeat and
    completion calls both fail closed instead of silently succeeding."""
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
                run_type="listings",
                domain="listings_items",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="original-owner",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    with Session(engine) as session:
        reclaim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="new-owner", lease_duration_seconds=300,
        )
        session.commit()
    assert reclaim.claimed is True

    with Session(engine) as session:
        heartbeat_ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, stale_run_id, lease_owner="original-owner", lease_duration_seconds=300, pages_fetched=1,
        )
        session.commit()
    assert heartbeat_ok is False

    with Session(engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, stale_run_id, lease_owner="original-owner", status="succeeded",
        )
        session.commit()
    assert completed is False


def test_claim_rejects_cross_organization_seller_account() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_a, seller_account_a, participation_a = _seed_scope(engine)
    org_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Other Org"))
        session.commit()

    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunRepository(session).claim_listings_run(
                organization_id=org_b, seller_account_id=seller_account_a,
                marketplace_participation_id=participation_a, region="na", environment="PRODUCTION",
                connection_id=None, lease_owner="owner", lease_duration_seconds=300,
            )


def test_claim_rejects_participation_belonging_to_another_seller_account() -> None:
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
            AmazonIngestionRunRepository(session).claim_listings_run(
                organization_id=org_id, seller_account_id=seller_account_b,
                marketplace_participation_id=participation_a, region="na", environment="PRODUCTION",
                connection_id=None, lease_owner="owner", lease_duration_seconds=300,
            )


# --- 12B.3D remediation: real lease expiry, not just lease-owner matching --


def test_heartbeat_fails_once_lease_has_expired_even_with_no_replacement_worker() -> None:
    """The core new behavior: an expired lease must fail closed even when
    nothing has reclaimed the scope yet — matching on `lease_owner` and
    `status='started'` alone is not enough."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    # Simulate time passing without any replacement worker ever claiming
    # the scope: backdate the lease directly, exactly what a real expiry
    # would look like from the database's point of view.
    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(engine) as session:
        heartbeat_ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", lease_duration_seconds=300, pages_fetched=5,
        )
        session.commit()
    assert heartbeat_ok is False

    # The row itself is untouched by the failed heartbeat attempt — a
    # failed compare-and-set writes nothing; it does not self-terminalize.
    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"
        assert row.pages_fetched == 0
        assert row.lease_owner == "owner-1"


def test_completion_fails_once_lease_has_expired_even_with_no_replacement_worker() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", status="succeeded",
        )
        session.commit()
    assert completed is False

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"  # never finalized by the expired-lease worker


def test_after_reclaim_the_original_worker_remains_unable_to_heartbeat_or_complete() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        original = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="original-owner", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, original.run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    # A second worker's claim attempt notices the expiry and reclaims it.
    with Session(engine) as session:
        reclaim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="new-owner", lease_duration_seconds=300,
        )
        session.commit()
    assert reclaim.claimed is True

    with Session(engine) as session:
        original_row = session.get(AmazonIngestionRun, original.run_id)
        assert original_row.status == "timed_out"
        assert original_row.failure_class == "lease_expired"
        assert original_row.lease_owner is None
        assert original_row.pagination_complete is False

    # The original worker, still believing it holds the lease, must remain
    # unable to heartbeat or complete against its now-terminalized run.
    with Session(engine) as session:
        heartbeat_ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, original.run_id, lease_owner="original-owner", lease_duration_seconds=300, pages_fetched=9,
        )
        session.commit()
    assert heartbeat_ok is False

    with Session(engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, original.run_id, lease_owner="original-owner", status="succeeded",
        )
        session.commit()
    assert completed is False


def test_initial_claim_lease_has_the_expected_future_expiry() -> None:
    """SQLite-side proxy for lease *creation*: this project's SQLite tests
    cannot execute the database-time expression itself (see
    `AmazonIngestionRunRepository._lease_expiry_value`'s docstring — that
    expression only compiles/executes against PostgreSQL), so this only
    proves the fallback path used in these tests produces a sane future
    value. The production, database-time-authoritative behavior is proven
    separately in
    `tests/postgres/test_disposable_postgres_listings_run_claim_concurrency.py`.
    """
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    before = datetime.now(UTC)
    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    after = datetime.now(UTC)

    with Session(engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        lease_expires_at = row.lease_expires_at.replace(tzinfo=UTC)

    assert before + timedelta(seconds=295) <= lease_expires_at <= after + timedelta(seconds=305)


def test_reclaim_creates_a_new_lease_with_its_own_future_expiry() -> None:
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
                run_type="listings",
                domain="listings_items",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="crashed-owner",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    before = datetime.now(UTC)
    with Session(engine) as session:
        reclaim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="new-owner", lease_duration_seconds=300,
        )
        session.commit()
    after = datetime.now(UTC)
    assert reclaim.claimed is True

    with Session(engine) as session:
        new_row = session.get(AmazonIngestionRun, reclaim.run_id)
        lease_expires_at = new_row.lease_expires_at.replace(tzinfo=UTC)
    assert before + timedelta(seconds=295) <= lease_expires_at <= after + timedelta(seconds=305)


def test_heartbeat_moves_the_expiry_further_into_the_future() -> None:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    org_id, seller_account_id, participation_id = _seed_scope(engine)

    with Session(engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(engine) as session:
        expiry_before = session.get(AmazonIngestionRun, claim.run_id).lease_expires_at

    with Session(engine) as session:
        ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", lease_duration_seconds=600, pages_fetched=1,
        )
        session.commit()
    assert ok is True

    with Session(engine) as session:
        expiry_after = session.get(AmazonIngestionRun, claim.run_id).lease_expires_at
    assert expiry_after > expiry_before

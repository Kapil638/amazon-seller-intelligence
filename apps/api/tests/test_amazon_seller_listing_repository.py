"""12B.3D — AmazonSellerListingRepository. SQLite.

12B.3D remediation: every public read/write now takes `organization_id`
and validates it against `marketplace_participation_id` inside the
repository itself (`_require_participation_in_organization`) — a
participation UUID alone is never sufficient. Writes go through exactly
one boundary, `reconcile_snapshot()`, validated once per call regardless of
how many listings are in the snapshot.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.amazon.listings_normalization import NormalizedListing
from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    Base,
    Organization,
)
from app.persistence.repositories import AmazonSellerListingRepository


def _engine():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return engine


def _seed(engine) -> tuple:
    """Explicit `flush()` after each `add()` — see the note this file
    already carried before this remediation: `AmazonIngestionRun` has no
    ORM `relationship()` to `AmazonMarketplaceParticipation`, so
    SQLAlchemy's automatic flush-dependency ordering cannot detect that
    this row must be inserted after it. Required only for the
    FK-pragma-enabled test below; harmless otherwise."""
    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    run_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="Listing Repo Test Org"))
        session.flush()
        session.add(
            AmazonSellerAccount(
                id=seller_account_id, organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active",
            )
        )
        session.flush()
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id="ATVPDKIKX0DER", region="na",
            )
        )
        session.flush()
        session.add(
            AmazonIngestionRun(
                id=run_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, run_type="listings",
                domain="listings_items", region="na", environment="PRODUCTION", status="succeeded",
            )
        )
        session.commit()
    return org_id, participation_id, run_id


def _listing(sku: str, **overrides) -> NormalizedListing:
    base = dict(
        seller_sku=sku, asin=None, product_type="TOY", condition_type=None, item_name="Widget",
        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
        status=["BUYABLE"], is_buyable=True, is_discoverable=False, offers=[],
        price_amount=Decimal("9.99"), price_currency="USD", fulfillment_availability=[],
        issues=[], issue_count=0, highest_issue_severity=None, product_types=[],
    )
    base.update(overrides)
    return NormalizedListing(**base)


def test_first_snapshot_inserts() -> None:
    engine = _engine()
    org_id, participation_id, run_id = _seed(engine)
    with Session(engine) as session:
        upserted, deactivated = AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-1")], last_ingestion_run_id=run_id,
        )
        session.commit()
    assert upserted == 1
    assert deactivated == 0

    with Session(engine) as session:
        fetched = AmazonSellerListingRepository(session).get_by_natural_key(org_id, participation_id, "SKU-1")
        assert fetched is not None
        assert fetched.first_seen_at is not None
        assert fetched.is_active is True


def test_second_snapshot_updates_and_preserves_first_seen_at() -> None:
    engine = _engine()
    org_id, participation_id, run_id = _seed(engine)
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-1", item_name="Original Name")], last_ingestion_run_id=run_id,
        )
        session.commit()
    with Session(engine) as session:
        first_seen_before = AmazonSellerListingRepository(session).get_by_natural_key(
            org_id, participation_id, "SKU-1"
        ).first_seen_at

    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-1", item_name="Updated Name")], last_ingestion_run_id=run_id,
        )
        session.commit()

    with Session(engine) as session:
        row = AmazonSellerListingRepository(session).get_by_natural_key(org_id, participation_id, "SKU-1")
        assert row.item_name == "Updated Name"
        assert row.first_seen_at == first_seen_before


def test_deactivate_missing_marks_only_absent_active_rows() -> None:
    engine = _engine()
    org_id, participation_id, run_id = _seed(engine)
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-KEEP"), _listing("SKU-DROP")], last_ingestion_run_id=run_id,
        )
        session.commit()

    with Session(engine) as session:
        upserted, deactivated = AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-KEEP")], last_ingestion_run_id=run_id,
        )
        session.commit()
    assert upserted == 1
    assert deactivated == 1

    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        assert repo.get_by_natural_key(org_id, participation_id, "SKU-KEEP").is_active is True
        assert repo.get_by_natural_key(org_id, participation_id, "SKU-DROP").is_active is False


def test_inactive_listing_reactivated_when_it_returns() -> None:
    engine = _engine()
    org_id, participation_id, run_id = _seed(engine)
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-1")], last_ingestion_run_id=run_id,
        )
        session.commit()
    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[], last_ingestion_run_id=run_id,
        )
        session.commit()
    with Session(engine) as session:
        assert AmazonSellerListingRepository(session).get_by_natural_key(
            org_id, participation_id, "SKU-1"
        ).is_active is False

    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-1")], last_ingestion_run_id=run_id,
        )
        session.commit()
    with Session(engine) as session:
        assert AmazonSellerListingRepository(session).get_by_natural_key(
            org_id, participation_id, "SKU-1"
        ).is_active is True


def test_last_ingestion_provenance_must_be_same_marketplace_and_listings_type() -> None:
    """Composite FK enforcement (real FK behavior requires PRAGMA foreign_keys
    on SQLite — see the 12B.3B integrity review's precedent). Proves a listing
    cannot be upserted with a last_ingestion_run_id from a DIFFERENT
    marketplace participation."""
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    org_id, participation_a, run_for_a = _seed(engine)

    with Session(engine) as session:
        participation_a_row = session.get(AmazonMarketplaceParticipation, participation_a)
        seller_account_id = participation_a_row.seller_account_id
        participation_b = uuid4()
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_id="A2EUQ1WTGCTBG2", region="eu",
            )
        )
        session.commit()

    with Session(engine) as session:
        with pytest.raises(IntegrityError):
            AmazonSellerListingRepository(session).reconcile_snapshot(
                organization_id=org_id, marketplace_participation_id=participation_b,
                listings=[_listing("SKU-CROSS")], last_ingestion_run_id=run_for_a,
            )
            session.commit()


# --- 12B.3D remediation: organization ownership at the write boundary -----


def test_correct_organization_can_reconcile_its_own_participation() -> None:
    engine = _engine()
    org_id, participation_id, run_id = _seed(engine)
    with Session(engine) as session:
        upserted, _ = AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            listings=[_listing("SKU-1")], last_ingestion_run_id=run_id,
        )
        session.commit()
    assert upserted == 1


def test_another_organization_cannot_write_through_a_foreign_participation_id() -> None:
    engine = _engine()
    org_a, participation_a, run_a = _seed(engine)
    org_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Other Org"))
        session.commit()

    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonSellerListingRepository(session).reconcile_snapshot(
                organization_id=org_b, marketplace_participation_id=participation_a,
                listings=[_listing("SKU-HOSTILE")], last_ingestion_run_id=run_a,
            )

    # Nothing was written under the foreign organization's attempt.
    with Session(engine) as session:
        assert AmazonSellerListingRepository(session).get_by_natural_key(
            org_a, participation_a, "SKU-HOSTILE"
        ) is None


def test_nonexistent_and_foreign_participation_fail_identically() -> None:
    engine = _engine()
    org_a, participation_a, run_a = _seed(engine)
    org_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Other Org"))
        session.commit()

    with Session(engine) as session:
        repo = AmazonSellerListingRepository(session)
        with pytest.raises(TypeError) as nonexistent_exc:
            repo.reconcile_snapshot(
                organization_id=org_b, marketplace_participation_id=uuid4(),
                listings=[], last_ingestion_run_id=run_a,
            )
        with pytest.raises(TypeError) as foreign_exc:
            repo.reconcile_snapshot(
                organization_id=org_b, marketplace_participation_id=participation_a,
                listings=[], last_ingestion_run_id=run_a,
            )
    assert str(nonexistent_exc.value) == str(foreign_exc.value)


def test_no_cross_organization_existence_information_is_disclosed() -> None:
    engine = _engine()
    org_a, participation_a, run_a = _seed(engine)
    org_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Other Org"))
        session.commit()

    with Session(engine) as session:
        with pytest.raises(TypeError) as excinfo:
            AmazonSellerListingRepository(session).reconcile_snapshot(
                organization_id=org_b, marketplace_participation_id=participation_a,
                listings=[], last_ingestion_run_id=run_a,
            )
    message = str(excinfo.value)
    assert str(participation_a) not in message
    assert str(org_a) not in message


def test_reads_also_enforce_the_organization_boundary() -> None:
    engine = _engine()
    org_a, participation_a, run_a = _seed(engine)
    org_b = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_b, name="Other Org"))
        session.commit()

    with Session(engine) as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=org_a, marketplace_participation_id=participation_a,
            listings=[_listing("SKU-1")], last_ingestion_run_id=run_a,
        )
        session.commit()

    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonSellerListingRepository(session).get_by_natural_key(org_b, participation_a, "SKU-1")
        with pytest.raises(TypeError):
            AmazonSellerListingRepository(session).list_for_participation(org_b, participation_a)

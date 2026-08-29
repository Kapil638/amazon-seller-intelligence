"""12B.3E — AmazonListingsReadService. Uses the shared, per-test-isolated
SQLite database via `current_organization_id()` (conftest's autouse
`reset_persistence()` fixture), matching the established service-test
pattern. No Amazon call, no secret resolution possible from this service.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.amazon.listings_normalization import NormalizedListing
from app.amazon.listings_read import AmazonListingsReadService
from app.core.exceptions import AmazonListingsParticipationNotFoundError, AmazonSellerListingNotFoundError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun, Organization
from app.persistence.repositories import (
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerListingRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


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


def _seed_participation() -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
        )
        return {"org_id": org_id, "seller_account_id": seller_account.id, "participation_id": participation.id}


def _reconcile(scope: dict, listings: list[NormalizedListing]) -> None:
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )


def _seed_run(scope: dict, *, status: str, started_at: datetime | None = None, **overrides) -> None:
    with session_scope() as session:
        session.add(
            AmazonIngestionRun(
                organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
                marketplace_participation_id=scope["participation_id"], run_type="listings",
                domain="listings_items", region="na", environment="PRODUCTION", status=status,
                started_at=started_at or datetime.now(UTC),
                **overrides,
            )
        )


# --- authorization / tenancy -----------------------------------------------


def test_summary_for_own_participation_succeeds() -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1")])
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.total_listings == 1


def test_summary_foreign_and_nonexistent_participation_produce_identical_errors() -> None:
    scope = _seed_participation()
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other Org"))

    with session_scope() as session:
        foreign_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=other_org,
            seller_account_id=AmazonSellerAccountRepository(session).create_or_reconcile(
                organization_id=other_org, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
            ).id,
            marketplace_id=MARKETPLACE, region="na",
        ).id

    service = AmazonListingsReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError) as foreign_exc:
        service.get_summary(foreign_participation)
    with pytest.raises(AmazonListingsParticipationNotFoundError) as nonexistent_exc:
        service.get_summary(uuid4())
    assert str(foreign_exc.value).split()[0:2] == str(nonexistent_exc.value).split()[0:2]
    assert str(other_org) not in str(foreign_exc.value)


def test_listing_cannot_be_retrieved_through_another_participation() -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1")])
    with session_scope() as session:
        listing_id = AmazonSellerListingRepository(session).get_by_natural_key(
            scope["org_id"], scope["participation_id"], "SKU-1"
        ).id

    with session_scope() as session:
        other_participation_id = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2", region="eu",
        ).id

    service = AmazonListingsReadService()
    with pytest.raises(AmazonSellerListingNotFoundError):
        service.get_listing(other_participation_id, listing_id)
    # Sanity: it IS retrievable through the correct participation.
    detail = service.get_listing(scope["participation_id"], listing_id)
    assert detail.seller_sku == "SKU-1"


def test_foreign_listing_and_nonexistent_listing_are_indistinguishable() -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1")])
    service = AmazonListingsReadService()
    with pytest.raises(AmazonSellerListingNotFoundError) as nonexistent_exc:
        service.get_listing(scope["participation_id"], uuid4())

    with session_scope() as session:
        other_participation_id = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2", region="eu",
        ).id
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=other_participation_id,
            listings=[_listing("SKU-EU-1")], last_ingestion_run_id=None,
        )
        foreign_listing_id = AmazonSellerListingRepository(session).get_by_natural_key(
            scope["org_id"], other_participation_id, "SKU-EU-1"
        ).id
    with pytest.raises(AmazonSellerListingNotFoundError) as foreign_exc:
        service.get_listing(scope["participation_id"], foreign_listing_id)

    assert type(nonexistent_exc.value) is type(foreign_exc.value)


def test_multiple_seller_accounts_within_one_organization_remain_separated() -> None:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_a = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        seller_b = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation_a = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_a.id, marketplace_id=MARKETPLACE, region="na",
        ).id
        participation_b = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_b.id, marketplace_id=MARKETPLACE, region="na",
        ).id

    _reconcile({"org_id": org_id, "participation_id": participation_a}, [_listing("SKU-A-1")])
    _reconcile({"org_id": org_id, "participation_id": participation_b}, [_listing("SKU-B-1"), _listing("SKU-B-2")])

    service = AmazonListingsReadService()
    summary_a = service.get_summary(participation_a)
    summary_b = service.get_summary(participation_b)
    assert summary_a.total_listings == 1
    assert summary_b.total_listings == 2


# --- synchronization evidence -----------------------------------------------


def test_never_synchronized_summary() -> None:
    scope = _seed_participation()
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "never_synchronized"
    assert summary.sync.last_successful_synchronized_at is None
    assert summary.total_listings == 0


@pytest.mark.parametrize(
    "db_status,expected_api_status",
    [
        ("started", "running"),
        ("succeeded", "succeeded"),
        ("failed", "failed"),
        ("partial", "partial"),
        ("timed_out", "timed_out"),
    ],
)
def test_run_status_maps_to_sync_status(db_status, expected_api_status) -> None:
    scope = _seed_participation()
    _seed_run(scope, status=db_status, failure_class="malformed_page" if db_status == "failed" else None)
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == expected_api_status


def test_latest_run_selected_not_an_older_one() -> None:
    scope = _seed_participation()
    now = datetime.now(UTC)
    _seed_run(scope, status="failed", failure_class="malformed_page", started_at=now - timedelta(minutes=10))
    _seed_run(scope, status="succeeded", started_at=now)
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "succeeded"


def test_last_successful_synchronized_at_survives_a_later_failure() -> None:
    """The latest attempt failed, but a PRIOR attempt succeeded — the
    summary's overall status reflects the latest attempt, while
    last_successful_synchronized_at still reflects the earlier success."""
    scope = _seed_participation()
    now = datetime.now(UTC)
    _seed_run(scope, status="succeeded", started_at=now - timedelta(minutes=10), completed_at=now - timedelta(minutes=9))
    _seed_run(scope, status="failed", failure_class="request_failed", started_at=now)
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "failed"
    assert summary.sync.last_successful_synchronized_at is not None


def test_latest_listings_run_selected_not_a_marketplace_participations_run() -> None:
    scope = _seed_participation()
    with session_scope() as session:
        session.add(
            AmazonIngestionRun(
                organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
                marketplace_participation_id=scope["participation_id"], run_type="marketplace_participations",
                domain="marketplace_participations", region="na", environment="PRODUCTION", status="succeeded",
            )
        )
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.sync.status == "never_synchronized"


# --- summary field independence ---------------------------------------------


def test_active_buyable_discoverable_counts_are_independent() -> None:
    scope = _seed_participation()
    _reconcile(
        scope,
        [
            _listing("SKU-1", is_buyable=True, is_discoverable=False),
            _listing("SKU-2", is_buyable=False, is_discoverable=True),
        ],
    )
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.active_count == 2
    assert summary.buyable_count == 1
    assert summary.discoverable_count == 1


def test_severity_distribution_and_issue_counts() -> None:
    scope = _seed_participation()
    _reconcile(
        scope,
        [
            _listing("SKU-1", issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-2", issue_count=0, highest_issue_severity=None),
        ],
    )
    summary = AmazonListingsReadService().get_summary(scope["participation_id"])
    assert summary.with_issues_count == 1
    assert summary.without_issues_count == 1
    assert summary.issue_severity_error_count == 1
    assert summary.issue_severity_warning_count == 0

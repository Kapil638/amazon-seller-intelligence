"""12B.5A — unit tests for the five skill evidence services. SQLite,
offline, no Amazon/OpenAI call. Mirrors the established seeding patterns
from `test_amazon_listings_read_service.py`/`test_amazon_orders_read_
service.py` exactly."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.amazon.listings_normalization import NormalizedListing
from app.amazon.listings_read import AmazonListingsReadService
from app.amazon.orders_read import AmazonOrdersReadService
from app.core.exceptions import AmazonListingsParticipationNotFoundError
from app.copilot.skills.cancellations import CancellationAnomalyEvidenceService, is_anomalous, _WindowCancellation
from app.copilot.skills.listing_health import ListingHealthEvidenceService
from app.copilot.skills.listing_risk import ListingRiskEvidenceService
from app.copilot.skills.non_buyable import ListingNotFoundForInvestigationError, NonBuyableListingEvidenceService
from app.copilot.skills.order_trends import OrderTrendEvidenceService
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerListingRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _listing(sku: str, **overrides) -> NormalizedListing:
    base = dict(
        seller_sku=sku, asin=None, product_type="TOY", condition_type=None, item_name="Widget",
        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
        status=["BUYABLE"], is_buyable=True, is_discoverable=True, offers=[],
        price_amount=Decimal("9.99"), price_currency="USD", fulfillment_availability=[],
        issues=[], issue_count=0, highest_issue_severity=None, product_types=[],
    )
    base.update(overrides)
    return NormalizedListing(**base)


def _seed_scope() -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
            connection_id=connection.id,
        )
        session.flush()
        return {
            "org_id": org_id,
            "seller_account_id": seller_account.id,
            "participation_id": participation.id,
            "connection_id": connection.id,
        }


def _reconcile_listings(scope: dict, listings: list[NormalizedListing]) -> None:
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )


def _seed_orders_run(scope: dict) -> "uuid4":
    with session_scope() as session:
        run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        claim = run_repo.enqueue_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"], marketplace_participation_ids=[scope["participation_id"]],
            region="na", environment="PRODUCTION",
        )
        assert claim.claimed, claim.reason
        claimed = run_repo.claim_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"], region="na",
            environment="PRODUCTION", lease_owner="test-lease", lease_duration_seconds=300,
        )
        assert claimed.claimed, claimed.reason
        return claimed.run_id


def _seed_order(
    scope: dict, run_id, amazon_order_id: str, *, seller_sku: str, created_at: datetime,
    was_cancelled: bool = False, quantity: int = 1, proceeds: Decimal | None = Decimal("10.00"),
    currency: str = "USD", asin: str | None = "B0TEST00001",
) -> None:
    status = "CANCELLED" if was_cancelled else "SHIPPED"
    with session_scope() as session:
        order_repo = AmazonSellerOrderRepository(session)
        item_repo = AmazonSellerOrderItemRepository(session)
        order = order_repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            amazon_order_id=amazon_order_id, fulfillment_status=status, fulfilled_by="MERCHANT",
            sales_channel_name="AMAZON", sales_channel_marketplace_id=MARKETPLACE,
            sales_channel_marketplace_name="Amazon.com", items_shipped_count=0, items_unshipped_count=0,
            order_total_amount=proceeds, order_total_currency=currency if proceeds is not None else None,
            is_business_order=False, is_prime=False, was_cancelled=was_cancelled,
            amazon_created_at=created_at, amazon_last_updated_at=created_at, last_ingestion_run_id=run_id,
        )
        item_repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            order_id=order.id, amazon_order_item_id=f"{amazon_order_id}-ITEM", seller_sku=seller_sku, asin=asin,
            item_name="Widget", condition_type=None, quantity_ordered=quantity, quantity_fulfilled=quantity,
            quantity_unfulfilled=0, unit_price_amount=proceeds, unit_price_currency=currency if proceeds else None,
            item_proceeds_amount=proceeds, item_proceeds_currency=currency if proceeds is not None else None,
            last_ingestion_run_id=run_id,
        )


# --- Skill 1: Listing Health Prioritizer ------------------------------------


def test_listing_health_ranks_error_above_warning_above_clean() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-CLEAN"),
            _listing("SKU-WARN", issues=[{"code": "X", "severity": "WARNING"}], issue_count=1, highest_issue_severity="WARNING"),
            _listing("SKU-ERR", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
        ],
    )
    evidence = ListingHealthEvidenceService().evaluate(scope["participation_id"])
    order = [row["seller_sku"] for row in evidence.records]
    assert order.index("SKU-ERR") < order.index("SKU-WARN") < order.index("SKU-CLEAN")
    assert evidence.metrics["issue_severity_error_count"] == 1
    assert evidence.metrics["issue_severity_warning_count"] == 1
    assert evidence.confidence in {"high", "medium"}


def test_listing_health_includes_order_exposure_for_matching_sku() -> None:
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-A", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")])
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "111-1", seller_sku="SKU-A", created_at=now - timedelta(days=1))
    evidence = ListingHealthEvidenceService().evaluate(scope["participation_id"])
    row = next(r for r in evidence.records if r["seller_sku"] == "SKU-A")
    assert row["recent_order_count"] == 1
    assert row["recent_order_value_by_currency"] == {"USD": "10.0000"}


def test_listing_health_foreign_participation_raises() -> None:
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        ListingHealthEvidenceService().evaluate(uuid4())


# --- Skill 2: Non-buyable Listing Investigator ------------------------------


def test_non_buyable_investigator_flags_possible_explanation_when_error_present() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [_listing("SKU-NB", is_buyable=False, issues=[{"code": "Z", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")],
    )
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"], seller_sku="SKU-NB")
    kinds = [row.get("kind") for row in evidence.records]
    assert "possible_explanation" in kinds
    assert evidence.metrics["is_buyable"] is False


def test_non_buyable_investigator_never_claims_causation_without_error_issue() -> None:
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-NB2", is_buyable=False)])
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"], seller_sku="SKU-NB2")
    assert not any(row.get("kind") == "possible_explanation" for row in evidence.records)
    note_row = next(row for row in evidence.records if row.get("field") is None and "note" in row)
    assert "cannot be attributed" in note_row["note"]


def test_non_buyable_investigator_missing_listing_raises() -> None:
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-OTHER")])
    with pytest.raises(ListingNotFoundForInvestigationError):
        NonBuyableListingEvidenceService().investigate(scope["participation_id"], seller_sku="NOT-A-REAL-SKU")


def test_non_buyable_investigator_selects_prioritized_candidates_when_no_locator_given() -> None:
    """No seller_sku/asin given ("why are my listings not buyable?") must
    never guess a target — it returns a ranked selection instead, worst
    (ERROR) first, and never includes a currently-buyable listing."""
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-BUYABLE"),
            _listing("SKU-NB-WARN", is_buyable=False, issues=[{"code": "W", "severity": "WARNING"}], issue_count=1, highest_issue_severity="WARNING"),
            _listing("SKU-NB-ERR", is_buyable=False, issues=[{"code": "E", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
        ],
    )
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"])
    skus = [row["seller_sku"] for row in evidence.records]
    assert skus == ["SKU-NB-ERR", "SKU-NB-WARN"]
    assert "SKU-BUYABLE" not in skus
    assert evidence.metrics["not_buyable_count"] == 2
    assert evidence.confidence == "high"


def test_non_buyable_investigator_selection_reports_insufficient_data_when_all_buyable() -> None:
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-ONLY-BUYABLE")])
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"])
    assert evidence.records == []
    assert evidence.metrics["not_buyable_count"] == 0
    assert evidence.confidence == "insufficient_data"


# --- Skill 3: Order and Sales Trend Analyst ---------------------------------


def test_order_trends_computes_counts_and_zero_baseline_change() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "222-1", seller_sku="SKU-A", created_at=now - timedelta(days=1), quantity=2)
    _seed_order(scope, run_id, "222-2", seller_sku="SKU-B", created_at=now - timedelta(days=2), quantity=1)
    evidence = OrderTrendEvidenceService().analyze(scope["participation_id"])
    assert evidence.metrics["order_count"] == 2
    assert evidence.metrics["unit_count"] == 3
    assert evidence.metrics["order_value_by_currency"] == {"USD": "20.0000"}
    # No orders at all in the comparison period -> zero baseline -> None, never +inf.
    assert evidence.metrics["order_count_percentage_change"] is None


def test_order_trends_never_combines_currencies() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "333-1", seller_sku="SKU-A", created_at=now - timedelta(days=1), proceeds=Decimal("10.00"), currency="USD")
    _seed_order(scope, run_id, "333-2", seller_sku="SKU-B", created_at=now - timedelta(days=1), proceeds=Decimal("9.00"), currency="EUR")
    evidence = OrderTrendEvidenceService().analyze(scope["participation_id"])
    assert evidence.metrics["order_value_by_currency"] == {"EUR": "9.0000", "USD": "10.0000"}


# --- Skill 4: Cancellation/Operational Anomaly Detector ---------------------


def test_is_anomalous_requires_minimum_sample_size() -> None:
    current = _WindowCancellation(total_orders=3, cancelled_orders=3)
    previous = _WindowCancellation(total_orders=3, cancelled_orders=0)
    anomalous, reason = is_anomalous(current, previous)
    assert anomalous is False
    assert "sample too small" in reason


def test_is_anomalous_true_for_large_relative_increase_with_enough_volume() -> None:
    current = _WindowCancellation(total_orders=20, cancelled_orders=8)  # 40%
    previous = _WindowCancellation(total_orders=20, cancelled_orders=2)  # 10%
    anomalous, _reason = is_anomalous(current, previous)
    assert anomalous is True


def test_cancellation_service_reports_rate_and_small_sample_honestly() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "444-1", seller_sku="SKU-A", created_at=now - timedelta(days=1), was_cancelled=True)
    _seed_order(scope, run_id, "444-2", seller_sku="SKU-B", created_at=now - timedelta(days=1), was_cancelled=False)
    evidence = CancellationAnomalyEvidenceService().detect(scope["participation_id"])
    assert evidence.metrics["cancelled_orders"] == 1
    assert evidence.metrics["total_orders"] == 2
    assert evidence.metrics["is_anomalous"] is False  # sample too small
    assert "SKU-A" in [row["seller_sku"] for row in evidence.records]


# --- Skill 5: Listing Risk by Order Exposure --------------------------------


def test_listing_risk_only_includes_listings_with_issues() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-CLEAN"),
            _listing("SKU-RISK", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
        ],
    )
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "555-1", seller_sku="SKU-RISK", created_at=now - timedelta(days=1), proceeds=Decimal("25.00"))
    _seed_order(scope, run_id, "555-2", seller_sku="SKU-CLEAN", created_at=now - timedelta(days=1), proceeds=Decimal("5.00"))
    evidence = ListingRiskEvidenceService().rank(scope["participation_id"])
    skus = [row["seller_sku"] for row in evidence.records]
    assert skus == ["SKU-RISK"]
    assert evidence.metrics["exposed_order_value_by_currency"] == {"USD": "25.0000"}


def test_listing_risk_reports_unmatched_counts() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope, [_listing("SKU-RISK", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")]
    )
    evidence = ListingRiskEvidenceService().rank(scope["participation_id"])
    assert evidence.metrics["unmatched_listings_count"] == 1
    assert evidence.metrics["at_risk_listing_count"] == 1

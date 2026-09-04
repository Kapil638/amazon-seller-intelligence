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


def _finalize_orders_run(scope: dict, run_id) -> None:
    with session_scope() as session:
        AmazonIngestionRunMarketplaceParticipationRepository(session).finalize_successful_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            ingestion_run_id=run_id, participation_watermarks={scope["participation_id"]: datetime.now(UTC)},
        )


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


def test_listing_health_material_fix_not_discoverable_outranks_fully_healthy() -> None:
    """12B.5B before/after proof: a buyable-but-not-discoverable, issue-
    free listing must rank ahead of a fully healthy (discoverable,
    issue-free) listing — before this fix, `is_discoverable` was never
    consulted by the rank key at all, so these two tied and sorted only
    by `seller_sku`."""
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-HEALTHY", is_discoverable=True),
            _listing("SKU-INVISIBLE", is_discoverable=False),
        ],
    )
    evidence = ListingHealthEvidenceService().evaluate(scope["participation_id"])
    order = [row["seller_sku"] for row in evidence.records]
    assert order.index("SKU-INVISIBLE") < order.index("SKU-HEALTHY"), (
        "a not-discoverable listing must surface before an otherwise-identical, fully healthy one"
    )
    invisible_row = next(r for r in evidence.records if r["seller_sku"] == "SKU-INVISIBLE")
    assert invisible_row["score_factors"]["is_discoverable"] is False


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


def test_non_buyable_investigator_never_claims_causation_without_error_issue_or_missing_offer() -> None:
    """Neither an ERROR issue nor a missing active offer is present here
    (an explicit offer is seeded) — the only remaining, fully-hedged,
    non-causal note must be returned."""
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing(
                "SKU-NB2", is_buyable=False,
                offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"amount": "9.99", "currencyCode": "USD"}}],
            )
        ],
    )
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"], seller_sku="SKU-NB2")
    assert not any(row.get("kind") == "possible_explanation" for row in evidence.records)
    note_row = next(row for row in evidence.records if row.get("field") is None and "note" in row)
    assert "cannot be attributed" in note_row["note"]


def test_non_buyable_investigator_flags_possible_explanation_when_no_active_offer() -> None:
    """12B.5B material fix: a missing active offer — a common real
    reason a listing is not buyable — is now checked and surfaced, not
    just issue severity."""
    scope = _seed_scope()
    _reconcile_listings(scope, [_listing("SKU-NB-NO-OFFER", is_buyable=False, offers=[])])
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"], seller_sku="SKU-NB-NO-OFFER")
    kinds = [row.get("kind") for row in evidence.records]
    assert "possible_explanation" in kinds
    offer_row = next(row for row in evidence.records if row.get("field") == "active_offer_evidence")
    assert offer_row["has_active_offer"] is False


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


def test_order_trends_material_fix_flags_percentage_change_unreliable_below_minimum_sample() -> None:
    from app.copilot.skills.order_trends import MIN_SAMPLE_SIZE_FOR_TREND

    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    # 2 orders in the current period, 1 in the comparison period — both
    # far below MIN_SAMPLE_SIZE_FOR_TREND, so the swing is real arithmetic
    # (+100%) but not a reliable signal at this sample size.
    _seed_order(scope, run_id, "666-1", seller_sku="SKU-A", created_at=now - timedelta(days=1))
    _seed_order(scope, run_id, "666-2", seller_sku="SKU-B", created_at=now - timedelta(days=2))
    _seed_order(scope, run_id, "666-3", seller_sku="SKU-A", created_at=now - timedelta(days=35))
    evidence = OrderTrendEvidenceService().analyze(scope["participation_id"])
    assert evidence.metrics["order_count"] < MIN_SAMPLE_SIZE_FOR_TREND
    assert evidence.metrics["sample_size_sufficient_for_trend"] is False
    assert evidence.metrics["order_count_percentage_change"] is not None
    assert any("too small" in limitation or "not a statistically reliable" in limitation for limitation in evidence.limitations)


def test_order_trends_material_fix_reports_sample_sufficient_at_or_above_minimum() -> None:
    from app.copilot.skills.order_trends import MIN_SAMPLE_SIZE_FOR_TREND

    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    for i in range(MIN_SAMPLE_SIZE_FOR_TREND):
        _seed_order(scope, run_id, f"777-cur-{i}", seller_sku="SKU-A", created_at=now - timedelta(days=1))
    for i in range(MIN_SAMPLE_SIZE_FOR_TREND):
        _seed_order(scope, run_id, f"777-prev-{i}", seller_sku="SKU-A", created_at=now - timedelta(days=35))
    evidence = OrderTrendEvidenceService().analyze(scope["participation_id"])
    assert evidence.metrics["sample_size_sufficient_for_trend"] is True


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


def test_cancellation_bounds_records_to_affected_sku_limit_for_a_large_population() -> None:
    """Final safety/bounded-evidence review: `records` must never be an
    unbounded list — this was the one genuine gap found across all five
    skills. Seeds well past `AFFECTED_SKU_LIMIT` distinct affected SKUs
    and proves the returned list is bounded while the full population
    count is still reported honestly."""
    from app.copilot.skills.cancellations import AFFECTED_SKU_LIMIT

    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    total_affected = AFFECTED_SKU_LIMIT + 30
    for i in range(total_affected):
        _seed_order(
            scope, run_id, f"666-{i:04d}", seller_sku=f"SKU-{i:04d}",
            created_at=now - timedelta(days=1), was_cancelled=True,
        )
    evidence = CancellationAnomalyEvidenceService().detect(scope["participation_id"])

    assert len(evidence.records) == AFFECTED_SKU_LIMIT
    assert evidence.metrics["affected_sku_count"] == total_affected
    assert evidence.metrics["returned_sku_count"] == AFFECTED_SKU_LIMIT
    assert evidence.metrics["sku_list_truncated"] is True
    assert any("prioritized subset" in limitation for limitation in evidence.limitations)
    # Payload stays bounded regardless of population size — proven by
    # actually measuring bytes, not just the record count.
    import json

    payload_bytes = len(json.dumps(evidence.model_dump(mode="json"), default=str).encode("utf-8"))
    assert payload_bytes < 20_000


def test_cancellation_deterministic_selection_orders_by_cancelled_order_count_then_sku() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    # SKU-HIGH appears on 3 distinct cancelled orders, SKU-LOW on 1 —
    # SKU-HIGH must rank first regardless of seed/insertion order.
    _seed_order(scope, run_id, "777-1", seller_sku="SKU-HIGH", created_at=now - timedelta(days=1), was_cancelled=True)
    _seed_order(scope, run_id, "777-2", seller_sku="SKU-HIGH", created_at=now - timedelta(days=2), was_cancelled=True)
    _seed_order(scope, run_id, "777-3", seller_sku="SKU-HIGH", created_at=now - timedelta(days=3), was_cancelled=True)
    _seed_order(scope, run_id, "777-4", seller_sku="SKU-LOW", created_at=now - timedelta(days=1), was_cancelled=True)

    evidence = CancellationAnomalyEvidenceService().detect(scope["participation_id"])
    order = [row["seller_sku"] for row in evidence.records]
    assert order.index("SKU-HIGH") < order.index("SKU-LOW")
    high_row = next(r for r in evidence.records if r["seller_sku"] == "SKU-HIGH")
    assert high_row["cancelled_order_count"] == 3


def test_cancellation_deterministic_tie_break_by_seller_sku() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    # Both SKUs tie at exactly 1 cancelled order each — the tie-break
    # (seller_sku, ascending) must be deterministic regardless of seed
    # order, never dependent on incidental fetch order.
    _seed_order(scope, run_id, "888-1", seller_sku="SKU-Z", created_at=now - timedelta(days=1), was_cancelled=True)
    _seed_order(scope, run_id, "888-2", seller_sku="SKU-A", created_at=now - timedelta(days=1), was_cancelled=True)
    evidence = CancellationAnomalyEvidenceService().detect(scope["participation_id"])
    order = [row["seller_sku"] for row in evidence.records]
    assert order.index("SKU-A") < order.index("SKU-Z")


def test_cancellation_aggregate_metrics_reflect_full_population_not_truncated_subset() -> None:
    """No important aggregate may be calculated only from the truncated
    top-N `records` — every count/rate must come from the full,
    untruncated order-level query, proven here by seeding well past the
    truncation limit and confirming the aggregate counts equal the true
    total, not the 25 shown."""
    from app.copilot.skills.cancellations import AFFECTED_SKU_LIMIT

    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    total_affected = AFFECTED_SKU_LIMIT + 15
    for i in range(total_affected):
        _seed_order(
            scope, run_id, f"999-{i:04d}", seller_sku=f"SKU-{i:04d}",
            created_at=now - timedelta(days=1), was_cancelled=True,
        )
    # A handful of non-cancelled orders too, so the rate is meaningfully < 100%.
    for i in range(5):
        _seed_order(
            scope, run_id, f"999-clean-{i:04d}", seller_sku=f"SKU-CLEAN-{i:04d}",
            created_at=now - timedelta(days=1), was_cancelled=False,
        )
    evidence = CancellationAnomalyEvidenceService().detect(scope["participation_id"])

    assert evidence.metrics["cancelled_orders"] == total_affected
    assert evidence.metrics["total_orders"] == total_affected + 5
    assert evidence.metrics["cancellation_rate"] == pytest.approx(total_affected / (total_affected + 5))
    assert len(evidence.records) == AFFECTED_SKU_LIMIT  # records still bounded


def test_cancellation_evidence_never_leaks_across_marketplace_participations() -> None:
    scope_a = _seed_scope()
    # A second participation under the *same* organization/connection —
    # deliberately not a second `_seed_scope()` call, which would
    # collide on this org's single `amazon_connections` row (one per
    # organization/provider/environment) — proving isolation across
    # participations specifically, not merely across organizations
    # (already proven for the other four skills).
    with session_scope() as session:
        participation_b = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope_a["org_id"], seller_account_id=scope_a["seller_account_id"],
            marketplace_id="A1PA6795UKMFR9", region="na", connection_id=scope_a["connection_id"],
        )
        session.flush()
        participation_b_id = participation_b.id
    scope_b = {**scope_a, "participation_id": participation_b_id}

    # One shared Orders run covering both participations at once — the
    # realistic shape for one organization/connection syncing multiple
    # marketplace participations together, and avoids a second, invalid
    # `enqueue_orders_run` claim attempt for the same organization/
    # seller_account/region scope the first call already holds.
    with session_scope() as session:
        run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        claim = run_repo.enqueue_orders_run(
            organization_id=scope_a["org_id"], seller_account_id=scope_a["seller_account_id"],
            connection_id=scope_a["connection_id"],
            marketplace_participation_ids=[scope_a["participation_id"], participation_b_id],
            region="na", environment="PRODUCTION",
        )
        assert claim.claimed, claim.reason
        claimed = run_repo.claim_orders_run(
            organization_id=scope_a["org_id"], seller_account_id=scope_a["seller_account_id"], region="na",
            environment="PRODUCTION", lease_owner="test-lease", lease_duration_seconds=300,
        )
        assert claimed.claimed, claimed.reason
        run_id = claimed.run_id

    now = datetime.now(UTC)
    _seed_order(scope_a, run_id, "111-a", seller_sku="SKU-ONLY-IN-A", created_at=now - timedelta(days=1), was_cancelled=True)
    _seed_order(scope_b, run_id, "111-b", seller_sku="SKU-ONLY-IN-B", created_at=now - timedelta(days=1), was_cancelled=True)

    evidence_a = CancellationAnomalyEvidenceService().detect(scope_a["participation_id"])
    evidence_b = CancellationAnomalyEvidenceService().detect(participation_b_id)

    skus_a = {row["seller_sku"] for row in evidence_a.records}
    skus_b = {row["seller_sku"] for row in evidence_b.records}
    assert "SKU-ONLY-IN-B" not in skus_a
    assert "SKU-ONLY-IN-A" not in skus_b
    assert evidence_a.metrics["cancelled_orders"] == 1
    assert evidence_b.metrics["cancelled_orders"] == 1


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


def test_listing_risk_material_fix_caps_confidence_when_majority_unmatched() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-RISK-1", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-RISK-2", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-RISK-3", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
        ],
    )
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    # Only 1 of the 3 at-risk listings has any linked order in the window —
    # a clear majority (2/3) is unmatched.
    _seed_order(scope, run_id, "888-1", seller_sku="SKU-RISK-1", created_at=now - timedelta(days=1))
    _finalize_orders_run(scope, run_id)
    evidence = ListingRiskEvidenceService().rank(scope["participation_id"])
    assert evidence.metrics["majority_unmatched"] is True
    # Freshness alone is NOT incomplete here (the run was finalized as
    # succeeded) — "medium" is reached only via `majority_unmatched`,
    # proving the new rule actually changes the outcome.
    assert evidence.confidence == "medium"
    assert any("mostly unmatched" in limitation for limitation in evidence.limitations)


def test_listing_risk_does_not_cap_confidence_when_minority_unmatched() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing("SKU-RISK-1", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-RISK-2", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
            _listing("SKU-RISK-3", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR"),
        ],
    )
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    # 2 of 3 at-risk listings have linked orders — only a minority (1/3)
    # is unmatched, so confidence is not capped by this rule.
    _seed_order(scope, run_id, "999-1", seller_sku="SKU-RISK-1", created_at=now - timedelta(days=1))
    _seed_order(scope, run_id, "999-2", seller_sku="SKU-RISK-2", created_at=now - timedelta(days=1))
    _finalize_orders_run(scope, run_id)
    evidence = ListingRiskEvidenceService().rank(scope["participation_id"])
    assert evidence.metrics["majority_unmatched"] is False
    assert evidence.confidence == "high"
    assert not any("mostly unmatched" in limitation for limitation in evidence.limitations)


# --- 12B.5B: Phase 5 evidence enrichment (item_name, score/risk factors,
# issue categories, failure category) — additive to every skill's records/
# metrics. Remediation correction: a major "2.0.0" bump for all five was
# reverted — most of those additions were presentational, not a material
# intelligence change, and a major skill-version bump must never be used
# merely to invalidate caches. Each skill now carries its own honestly
# earned version (see contracts.py's SKILL_VERSIONS comment for the
# per-skill rationale).


def test_skill_versions_reflect_only_genuine_formula_changes() -> None:
    from app.copilot.skills.contracts import SKILL_VERSIONS

    assert SKILL_VERSIONS == {
        "listing_health_prioritizer": "1.1.0",
        "non_buyable_listing_investigator": "1.1.0",
        "order_and_sales_trend_analyst": "1.1.0",
        "cancellation_operational_anomaly_detector": "1.1.0",
        "listing_risk_by_order_exposure": "1.1.0",
    }


def test_listing_health_records_include_item_name_and_score_factors() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [_listing("SKU-ERR", item_name="Blue Widget XL", issues=[{"code": "X", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")],
    )
    evidence = ListingHealthEvidenceService().evaluate(scope["participation_id"])
    row = evidence.records[0]
    assert row["item_name"] == "Blue Widget XL"
    assert row["score_factors"] == {
        "has_error_issue": True,
        "has_warning_issue": False,
        "issue_count": 1,
        "is_buyable": True,
        "is_discoverable": True,
        "is_active": True,
        "recent_order_count": 0,
    }
    assert evidence.skill_version == "1.1.0"


def test_non_buyable_detail_includes_item_name_categories_and_failure_category() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [
            _listing(
                "SKU-NB", item_name="Red Widget", is_buyable=False, is_discoverable=False,
                issues=[{"code": "Z", "severity": "ERROR", "categories": ["IMAGE"]}],
                issue_count=1, highest_issue_severity="ERROR",
            )
        ],
    )
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"], seller_sku="SKU-NB")
    assert evidence.metrics["item_name"] == "Red Widget"
    assert evidence.metrics["failure_category"] == "not_buyable_and_not_discoverable"
    issue_summary = next(row for row in evidence.records if row.get("field") == "issue_summary")
    assert issue_summary["issue_categories"] == ["IMAGE"]


def test_non_buyable_selection_records_include_item_name() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [_listing("SKU-NB-2", item_name="Green Widget", is_buyable=False, issues=[{"code": "Z", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")],
    )
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"])
    assert evidence.records[0]["item_name"] == "Green Widget"


def test_listing_risk_records_include_item_name_and_risk_factors() -> None:
    scope = _seed_scope()
    _reconcile_listings(
        scope,
        [_listing("SKU-RISK-ITEM", item_name="Yellow Widget", issues=[{"code": "Y", "severity": "ERROR"}], issue_count=1, highest_issue_severity="ERROR")],
    )
    evidence = ListingRiskEvidenceService().rank(scope["participation_id"])
    row = evidence.records[0]
    assert row["item_name"] == "Yellow Widget"
    assert row["risk_factors"] == {"has_error_issue": True, "issue_count": 1, "recent_order_count": 0}


def test_order_trends_top_sku_records_include_item_name() -> None:
    scope = _seed_scope()
    run_id = _seed_orders_run(scope)
    now = datetime.now(UTC)
    _seed_order(scope, run_id, "999-1", seller_sku="SKU-NAMED", created_at=now - timedelta(days=1))
    evidence = OrderTrendEvidenceService().analyze(scope["participation_id"])
    top = next(row for row in evidence.records if row["kind"] == "top_sku_by_units")
    assert top["item_name"] == "Widget"

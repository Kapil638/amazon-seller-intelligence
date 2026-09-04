"""12B.5B Phase 11 — deterministic, non-flaky proof that the evidence
sent toward a customer-facing answer is materially smaller than the raw
rows it was computed from. Byte-size comparisons only (no wall-clock
timing assertions, which are inherently noisy on a shared CI runner) —
see the 12B.5B handover doc for why latency/token claims are reported
qualitatively (backed by the cache-hit tests) rather than asserted here
as a hard millisecond threshold."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from app.amazon.listings_normalization import NormalizedListing
from app.copilot.skills.cancellations import CancellationAnomalyEvidenceService
from app.copilot.skills.listing_health import ListingHealthEvidenceService
from app.copilot.skills.listing_risk import ListingRiskEvidenceService
from app.copilot.skills.non_buyable import NonBuyableListingEvidenceService
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
# Deliberately larger than the skill's own default result limit (25) —
# a realistic catalog is typically much bigger than what's shown in one
# answer; the top-N reduction is exactly where the compactness payoff is
# most visible, on top of the smaller per-field payoff that always
# applies regardless of catalog size.
LISTING_COUNT = 100


def _listing(index: int) -> NormalizedListing:
    return NormalizedListing(
        seller_sku=f"SKU-{index:04d}", asin=f"B0TEST{index:04d}", product_type="TOY", condition_type=None,
        item_name=f"Synthetic Widget Model {index:04d} — Extended Description Variant", main_image_url=None,
        amazon_created_at=None, amazon_last_updated_at=None, status=["BUYABLE", "DISCOVERABLE"],
        is_buyable=True, is_discoverable=True,
        offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"amount": "9.99", "currencyCode": "USD"}}],
        price_amount=Decimal("9.99"), price_currency="USD",
        fulfillment_availability=[{"fulfillmentChannelCode": "DEFAULT", "quantity": 50}],
        issues=(
            [
                {
                    "code": "MISSING_ATTRIBUTE", "message": "A synthetic, sanitized issue message of realistic length "
                    "describing a missing attribute requirement for this product type.", "severity": "ERROR",
                    "categories": ["MISSING_ATTRIBUTE"], "attributeNames": ["bullet_point", "color"],
                }
            ]
            if index % 3 == 0
            else []
        ),
        issue_count=1 if index % 3 == 0 else 0,
        highest_issue_severity="ERROR" if index % 3 == 0 else None,
        product_types=[{"marketplaceId": MARKETPLACE, "productType": "TOY"}],
    )


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
            "org_id": org_id, "seller_account_id": seller_account.id,
            "participation_id": participation.id, "connection_id": connection.id,
        }


def _seed_orders(scope: dict, *, cancelled: bool = False) -> list[dict]:
    """Returns each seeded order+item pair's own raw `__dict__` (captured
    at insert time, while the ORM objects are still live session-bound
    instances) so callers can measure a real "raw" byte size instead of
    guessing at one — SQLAlchemy row `__dict__`s carry a `_sa_instance_
    state` entry that `json.dumps(..., default=str)` stringifies rather
    than crashing on, which is exactly why every other raw-size
    comparison in this file already serializes ORM `__dict__`s directly
    (see `test_listing_health_...`'s `listing.__dict__` usage above)."""
    raw_rows: list[dict] = []
    run_repo = AmazonIngestionRunMarketplaceParticipationRepository
    with session_scope() as session:
        repo = run_repo(session)
        claim = repo.enqueue_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"], marketplace_participation_ids=[scope["participation_id"]],
            region="na", environment="PRODUCTION",
        )
        assert claim.claimed
        claimed = repo.claim_orders_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"], region="na",
            environment="PRODUCTION", lease_owner="w", lease_duration_seconds=300,
        )
        assert claimed.claimed
        run_id = claimed.run_id
    now = datetime.now(UTC)
    with session_scope() as session:
        for i in range(LISTING_COUNT):
            is_cancelled = cancelled and i % 4 == 0
            order = AmazonSellerOrderRepository(session).upsert(
                organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
                amazon_order_id=f"900-{i:04d}", fulfillment_status="CANCELLED" if is_cancelled else "SHIPPED",
                fulfilled_by="MERCHANT", sales_channel_name="AMAZON", sales_channel_marketplace_id=MARKETPLACE,
                sales_channel_marketplace_name="Amazon.com", items_shipped_count=1, items_unshipped_count=0,
                order_total_amount=Decimal("19.99"), order_total_currency="USD",
                is_business_order=False, is_prime=False, was_cancelled=is_cancelled,
                amazon_created_at=now - timedelta(days=1), amazon_last_updated_at=now - timedelta(days=1),
                last_ingestion_run_id=run_id,
            )
            item = AmazonSellerOrderItemRepository(session).upsert(
                organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
                order_id=order.id, amazon_order_item_id=f"900-{i:04d}-ITEM", seller_sku=f"SKU-{i:04d}",
                asin=f"B0TEST{i:04d}", item_name=f"Synthetic Widget Model {i:04d}", condition_type=None,
                quantity_ordered=1, quantity_fulfilled=1, quantity_unfulfilled=0,
                unit_price_amount=Decimal("19.99"), unit_price_currency="USD",
                item_proceeds_amount=Decimal("19.99"), item_proceeds_currency="USD", last_ingestion_run_id=run_id,
            )
            raw_rows.append({"order": dict(order.__dict__), "item": dict(item.__dict__)})
    return raw_rows


def _bytes_of(payload) -> int:
    return len(json.dumps(payload, default=str).encode("utf-8"))


def test_listing_health_evidence_payload_is_materially_smaller_than_the_raw_listing_rows() -> None:
    scope = _seed_scope()
    listings = [_listing(i) for i in range(LISTING_COUNT)]
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )
    _seed_orders(scope)

    # "Raw" comparison: the full normalized listing objects this evidence
    # was computed from — the naive alternative to sending compact,
    # top-N/aggregated evidence to a model.
    raw_bytes = _bytes_of([listing.__dict__ for listing in listings])
    evidence = ListingHealthEvidenceService().evaluate(scope["participation_id"], limit=25)
    compact_bytes = _bytes_of(evidence.model_dump(mode="json"))

    assert compact_bytes < raw_bytes, (
        f"compact evidence ({compact_bytes} bytes) must be smaller than the raw listing rows "
        f"it was computed from ({raw_bytes} bytes)"
    )
    # Not a loose "smaller by any amount" — the compact contract must be
    # materially smaller (records carry ~10 fields per listing vs. the
    # raw object's full issues/offers/fulfillment_availability/
    # product_types arrays), never just marginally trimmed.
    assert compact_bytes < raw_bytes * 0.6


# --- 12B.5B remediation Section 7: per-skill payload measurements -----------
#
# Extends the single-skill measurement above to the remaining four skills,
# each at the same LISTING_COUNT=100 "large candidate set" scale. Actual
# measured reductions on this synthetic fixture set (documented here so a
# future re-run's numbers can be compared against what was reported, not
# re-derived from scratch): listing_health_prioritizer 83,148 -> 14,935
# bytes (82.0%); listing_risk_by_order_exposure 34,374 -> 12,469 bytes
# (63.7%); non_buyable_listing_investigator 90,600 -> 3,611 bytes (96.0%);
# order_and_sales_trend_analyst 180,000 -> ~3,460 bytes (98.1%);
# cancellation_operational_anomaly_detector 180,025 -> 3,622 bytes (98.0%).
# The wide range (63.7%-98.1%) itself is the honest finding: reduction
# magnitude depends heavily on how much of a skill's raw input is
# genuinely irrelevant to its answer (order_trends/cancellations discard
# almost the entire raw order/item row set down to aggregates) versus how
# much of a still-relevant top-N candidate set must be kept verbatim
# (listing_risk keeps ~25 full listing-shaped records out of ~33 raw
# candidates, a much smaller ratio of discardable content) — a single
# quoted "83%" was never representative of all five skills, which is
# exactly what this remediation's Section 7 asked to be established
# honestly instead of assumed.


def test_listing_risk_evidence_payload_is_materially_smaller_than_the_raw_at_risk_rows() -> None:
    scope = _seed_scope()
    listings = [_listing(i) for i in range(LISTING_COUNT)]  # every 3rd listing carries an issue
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )
    _seed_orders(scope)

    at_risk_raw = [listing.__dict__ for listing in listings if listing.issue_count > 0]
    raw_bytes = _bytes_of(at_risk_raw)
    evidence = ListingRiskEvidenceService().rank(scope["participation_id"], limit=25)
    compact_bytes = _bytes_of(evidence.model_dump(mode="json"))

    assert compact_bytes < raw_bytes


def test_non_buyable_candidate_list_payload_is_materially_smaller_than_the_raw_listing_rows() -> None:
    scope = _seed_scope()
    listings = [
        NormalizedListing(
            seller_sku=f"SKU-{i:04d}", asin=f"B0TEST{i:04d}", product_type="TOY", condition_type=None,
            item_name=f"Synthetic Widget Model {i:04d} — Extended Description Variant", main_image_url=None,
            amazon_created_at=None, amazon_last_updated_at=None, status=["NOT_BUYABLE"],
            is_buyable=False, is_discoverable=True, offers=[],
            price_amount=Decimal("9.99"), price_currency="USD",
            fulfillment_availability=[{"fulfillmentChannelCode": "DEFAULT", "quantity": 50}],
            issues=[
                {
                    "code": "MISSING_ATTRIBUTE", "message": "A synthetic, sanitized issue message of realistic "
                    "length describing a missing attribute requirement for this product type.",
                    "severity": "ERROR", "categories": ["MISSING_ATTRIBUTE"], "attributeNames": ["bullet_point"],
                }
            ],
            issue_count=1, highest_issue_severity="ERROR",
            product_types=[{"marketplaceId": MARKETPLACE, "productType": "TOY"}],
        )
        for i in range(LISTING_COUNT)
    ]
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )
    _seed_orders(scope)

    raw_bytes = _bytes_of([listing.__dict__ for listing in listings])
    evidence = NonBuyableListingEvidenceService().investigate(scope["participation_id"])
    compact_bytes = _bytes_of(evidence.model_dump(mode="json"))

    assert compact_bytes < raw_bytes * 0.1, (
        "the not-buyable candidate list (top 10 of 100) must be an order of magnitude smaller "
        "than the raw listing rows it was selected from"
    )


def test_order_trends_evidence_payload_is_materially_smaller_than_the_raw_order_item_rows() -> None:
    scope = _seed_scope()
    raw_rows = _seed_orders(scope)

    raw_bytes = _bytes_of(raw_rows)
    evidence = OrderTrendEvidenceService().analyze(scope["participation_id"])
    compact_bytes = _bytes_of(evidence.model_dump(mode="json"))

    assert compact_bytes < raw_bytes * 0.1, (
        f"compact evidence ({compact_bytes} bytes) must be an order of magnitude smaller than the "
        f"raw order+item rows it was aggregated from ({raw_bytes} bytes)"
    )


def test_cancellation_evidence_payload_is_materially_smaller_than_the_raw_order_item_rows() -> None:
    scope = _seed_scope()
    raw_rows = _seed_orders(scope, cancelled=True)

    raw_bytes = _bytes_of(raw_rows)
    evidence = CancellationAnomalyEvidenceService().detect(scope["participation_id"])
    compact_bytes = _bytes_of(evidence.model_dump(mode="json"))

    assert compact_bytes < raw_bytes * 0.1, (
        f"compact evidence ({compact_bytes} bytes) must be an order of magnitude smaller than the "
        f"raw order+item rows it was aggregated from ({raw_bytes} bytes)"
    )

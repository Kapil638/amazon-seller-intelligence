"""12B.5A — Skill 5: Listing Risk by Order Exposure.

Joins Listings and order items only through the one safe, marketplace-
scoped natural relationship the schema actually offers: `seller_sku`
within a single `marketplace_participation_id` (never across
participations, never across organizations, never a fuzzy/guessed
match). Reports listings currently carrying an ERROR or WARNING issue,
their recent order/unit activity, and the approved order-value exposure
those orders represent — grouped by currency, never combined or
converted.

This never claims revenue will be lost if the issue goes unfixed, and
never claims revenue was already lost *because of* the issue — only
that this much already-observed order value is currently associated
with a SKU that has an open issue (matching the skill matrix's own
Scenario 17 "no causal or predictive claim is possible" limitation
verbatim).
"""

from __future__ import annotations

from uuid import UUID

from app.amazon.listings_read import AmazonListingsReadService, ListingCollectionItem
from app.amazon.orders_read import AmazonOrdersReadService
from app.copilot.skills.contracts import SkillEvidence, incomplete_run, safe_deep_link
from app.copilot.skills.shared import CurrencySafeTotal, build_periods, fetch_all_pages

DEFAULT_RESULT_LIMIT = 25
MAX_RESULT_LIMIT = 100
_RISK_SEVERITIES = ("ERROR", "WARNING")


class ListingRiskEvidenceService:
    """Deterministic evidence for the `rank_listing_risk_by_order_exposure` tool."""

    def __init__(
        self,
        listings: AmazonListingsReadService | None = None,
        orders: AmazonOrdersReadService | None = None,
    ) -> None:
        self._listings = listings or AmazonListingsReadService()
        self._orders = orders or AmazonOrdersReadService()

    def rank(
        self,
        marketplace_participation_id: UUID,
        *,
        period_days: int | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> SkillEvidence:
        limit = max(1, min(limit, MAX_RESULT_LIMIT))
        analysis_period, _comparison = build_periods(period_days)

        listings_summary = self._listings.get_summary(marketplace_participation_id)
        at_risk_listings = self._fetch_at_risk_listings(marketplace_participation_id)

        item_rows = self._orders.list_order_items_for_window(
            marketplace_participation_id, created_after=analysis_period.start, created_before=analysis_period.end
        )
        orders_freshness = None
        try:
            orders_freshness = self._orders.get_summary(marketplace_participation_id).sync
        except Exception:
            orders_freshness = None

        exposure_by_sku: dict[str, dict] = {}
        skus_with_orders: set[str] = set()
        listing_skus = {row.seller_sku for row in at_risk_listings}
        window_total = CurrencySafeTotal()
        for row in item_rows:
            window_total.add(row.item_proceeds_amount, row.item_proceeds_currency)
            if row.seller_sku not in listing_skus:
                continue
            skus_with_orders.add(row.seller_sku)
            bucket = exposure_by_sku.setdefault(
                row.seller_sku, {"order_ids": set(), "units": 0, "value": CurrencySafeTotal()}
            )
            bucket["order_ids"].add(row.order_id)
            bucket["units"] += row.quantity_ordered
            bucket["value"].add(row.item_proceeds_amount, row.item_proceeds_currency)

        ranked = sorted(
            at_risk_listings,
            key=lambda listing: _risk_rank_key(listing, exposure_by_sku.get(listing.seller_sku)),
        )
        top = ranked[:limit]

        records = []
        exposure_total = CurrencySafeTotal()
        for listing in top:
            bucket = exposure_by_sku.get(listing.seller_sku)
            value_dict = bucket["value"].as_dict() if bucket else {}
            if bucket:
                for currency, amount in bucket["value"].totals.items():
                    exposure_total.totals[currency] += amount
            records.append(
                {
                    "seller_sku": listing.seller_sku,
                    "asin": listing.asin,
                    "highest_issue_severity": listing.highest_issue_severity,
                    "issue_count": listing.issue_count,
                    "is_buyable": listing.is_buyable,
                    "is_discoverable": listing.is_discoverable,
                    "recent_order_count": len(bucket["order_ids"]) if bucket else 0,
                    "recent_units": bucket["units"] if bucket else 0,
                    "recent_order_value_by_currency": value_dict,
                }
            )

        unmatched_listings_count = sum(1 for row in at_risk_listings if row.seller_sku not in skus_with_orders)
        unmatched_order_items_count = sum(
            1 for row in item_rows if row.seller_sku not in listing_skus
        )

        limitations = [
            "Does not mean order value will be lost if the issue is left unfixed.",
            "Does not mean order value was already lost because of the issue — no causal or "
            "predictive claim is possible from this data.",
            "Multi-currency totals are never combined or converted.",
            "Matches only by seller_sku within this one marketplace participation — never across "
            "participations or organizations, and never a guessed/fuzzy match.",
        ]

        freshness_incomplete = incomplete_run(listings_summary.sync.status) or (
            orders_freshness is not None and incomplete_run(orders_freshness.status)
        )

        return SkillEvidence(
            skill_id="listing_risk_by_order_exposure",
            skill_version="1.0.0",
            organization_id=_org_id(),
            marketplace_participation_ids=[marketplace_participation_id],
            analysis_period=analysis_period,
            listings_freshness=listings_summary.sync,
            orders_freshness=orders_freshness,
            has_newer_incomplete_run=freshness_incomplete,
            metrics={
                "at_risk_listing_count": len(at_risk_listings),
                "ranked_count": len(top),
                "exposed_order_value_by_currency": exposure_total.as_dict(),
                "unmatched_listings_count": unmatched_listings_count,
                "unmatched_order_items_count": unmatched_order_items_count,
            },
            records=records,
            limitations=limitations,
            confidence="insufficient_data" if not at_risk_listings else (
                "medium" if freshness_incomplete else "high"
            ),
            deep_links=[
                safe_deep_link(
                    f"/seller/listings?participation={marketplace_participation_id}&has_issues=true",
                    "View listings with issues",
                ),
                safe_deep_link(
                    f"/seller/orders?participation={marketplace_participation_id}",
                    "View orders for this marketplace",
                ),
            ],
        )

    def _fetch_at_risk_listings(self, marketplace_participation_id: UUID) -> list[ListingCollectionItem]:
        results: list[ListingCollectionItem] = []
        for severity in _RISK_SEVERITIES:

            def page(offset: int, limit: int, _severity=severity) -> tuple[list[ListingCollectionItem], int]:
                result = self._listings.list_listings(
                    marketplace_participation_id,
                    highest_issue_severity=_severity,  # type: ignore[arg-type]
                    offset=offset,
                    limit=limit,
                )
                return result.items, result.total

            results.extend(fetch_all_pages(page))
        return results


def _risk_rank_key(listing: ListingCollectionItem, exposure: dict | None) -> tuple:
    is_error = listing.highest_issue_severity == "ERROR"
    order_count = len(exposure["order_ids"]) if exposure else 0
    # `seller_sku` is an explicit final tie-break for listings equal on
    # every ranking signal above — never left to depend on the
    # incidental order the underlying paginated fetch happened to
    # return them in.
    return (0 if is_error else 1, -order_count, -listing.issue_count, listing.seller_sku)


def _org_id() -> UUID:
    from app.persistence.database import current_organization_id

    return current_organization_id()

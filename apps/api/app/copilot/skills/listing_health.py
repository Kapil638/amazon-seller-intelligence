"""12B.5A — Skill 1: Listing Health Prioritizer.

Answers: which Listings need attention first, why, and what verified
order exposure they have. Wraps `AmazonListingsReadService` and
`AmazonOrdersReadService`; never queries a table directly.

Ranking is a pure, documented, multi-key deterministic sort — never a
single opaque score. Components, in priority order:

1. Has an ERROR-severity issue (worse than any WARNING-only listing).
2. Issue count, when severity ties (more issues of the same worst
   severity ranks worse).
3. Has a WARNING-severity issue (worse than a clean listing).
4. Not buyable (worse than buyable).
5. Not active (worse than active).
6. Recent order count for this SKU, descending (more exposed listings
   surface first among otherwise-equal listings).

No step estimates "lost revenue" — `order_value_exposure` is the sum of
*already-observed* `item_proceeds_amount` for this SKU in the window,
grouped by currency, never a projection of what might happen if the
issue goes unfixed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

from app.amazon.listings_read import AmazonListingsReadService, ListingCollectionItem
from app.amazon.orders_read import AmazonOrdersReadService
from app.copilot.skills.contracts import SkillEvidence, incomplete_run, safe_deep_link
from app.copilot.skills.shared import CurrencySafeTotal, build_periods, fetch_all_pages
from app.persistence.database import current_organization_id

DEFAULT_RESULT_LIMIT = 25
MAX_RESULT_LIMIT = 100


@dataclass(frozen=True)
class _SkuExposure:
    order_count: int = 0
    units: int = 0
    value: CurrencySafeTotal | None = None


_EMPTY_EXPOSURE = _SkuExposure(value=CurrencySafeTotal())


class ListingHealthEvidenceService:
    """Deterministic evidence for the `prioritize_listing_health` tool."""

    def __init__(
        self,
        listings: AmazonListingsReadService | None = None,
        orders: AmazonOrdersReadService | None = None,
    ) -> None:
        self._listings = listings or AmazonListingsReadService()
        self._orders = orders or AmazonOrdersReadService()

    def evaluate(
        self,
        marketplace_participation_id: UUID,
        *,
        period_days: int | None = None,
        limit: int = DEFAULT_RESULT_LIMIT,
    ) -> SkillEvidence:
        limit = max(1, min(limit, MAX_RESULT_LIMIT))
        analysis_period, _comparison = build_periods(period_days)

        listings_summary = self._listings.get_summary(marketplace_participation_id)
        all_listings = self._fetch_all_listings(marketplace_participation_id)
        exposure_by_sku = self._exposure_by_sku(marketplace_participation_id, analysis_period)

        orders_freshness = None
        try:
            orders_summary = self._orders.get_summary(marketplace_participation_id)
            orders_freshness = orders_summary.sync
        except Exception:
            # Orders may legitimately be unavailable (never synced) for a
            # participation that only has Listings — degrade gracefully,
            # never fail the whole skill over a missing Orders signal.
            orders_freshness = None

        ranked = sorted(
            all_listings,
            key=lambda listing: _rank_key(listing, exposure_by_sku.get(listing.seller_sku, _EMPTY_EXPOSURE)),
        )
        top = ranked[:limit]

        records: list[dict] = []
        for listing in top:
            exposure = exposure_by_sku.get(listing.seller_sku, _EMPTY_EXPOSURE)
            records.append(
                {
                    "seller_sku": listing.seller_sku,
                    "asin": listing.asin,
                    "is_active": listing.is_active,
                    "is_buyable": listing.is_buyable,
                    "is_discoverable": listing.is_discoverable,
                    "issue_count": listing.issue_count,
                    "highest_issue_severity": listing.highest_issue_severity,
                    "recent_order_count": exposure.order_count,
                    "recent_units": exposure.units,
                    "recent_order_value_by_currency": (exposure.value or CurrencySafeTotal()).as_dict(),
                }
            )

        error_count = sum(1 for row in all_listings if row.highest_issue_severity == "ERROR")
        warning_count = sum(
            1 for row in all_listings if row.highest_issue_severity == "WARNING"
        )

        limitations = [
            "Cannot explain why Amazon raised an issue beyond its own code and severity.",
            "Cannot show an issue trend without comparing multiple syncs.",
            "Order-value exposure is observed proceeds already recorded in this window, never a revenue projection.",
        ]
        if len(all_listings) > len(ranked):
            limitations.append("Some listings could not be ranked and were excluded.")

        freshness_incomplete = incomplete_run(listings_summary.sync.status) or (
            orders_freshness is not None and incomplete_run(orders_freshness.status)
        )
        confidence = "insufficient_data" if not all_listings else ("medium" if freshness_incomplete else "high")

        return SkillEvidence(
            skill_id="listing_health_prioritizer",
            skill_version="1.0.0",
            organization_id=current_organization_id(),
            marketplace_participation_ids=[marketplace_participation_id],
            analysis_period=analysis_period,
            listings_freshness=listings_summary.sync,
            orders_freshness=orders_freshness,
            has_newer_incomplete_run=freshness_incomplete,
            metrics={
                "total_listings": listings_summary.total_listings,
                "with_issues_count": listings_summary.with_issues_count,
                "issue_severity_error_count": error_count,
                "issue_severity_warning_count": warning_count,
                "ranked_count": len(top),
            },
            records=records,
            limitations=limitations,
            confidence=confidence,
            deep_links=[
                safe_deep_link(
                    f"/seller/listings?participation={marketplace_participation_id}&sort_by=highest_issue_severity",
                    "View listings sorted by issue severity",
                )
            ],
        )

    def _fetch_all_listings(self, marketplace_participation_id: UUID) -> list[ListingCollectionItem]:
        def page(offset: int, limit: int) -> tuple[list[ListingCollectionItem], int]:
            result = self._listings.list_listings(marketplace_participation_id, offset=offset, limit=limit)
            return result.items, result.total

        return fetch_all_pages(page)

    def _exposure_by_sku(self, marketplace_participation_id: UUID, period) -> dict[str, _SkuExposure]:
        rows = self._orders.list_order_items_for_window(
            marketplace_participation_id, created_after=period.start, created_before=period.end
        )
        order_ids_by_sku: dict[str, set] = {}
        units_by_sku: dict[str, int] = {}
        value_by_sku: dict[str, CurrencySafeTotal] = {}
        for row in rows:
            order_ids_by_sku.setdefault(row.seller_sku, set()).add(row.order_id)
            units_by_sku[row.seller_sku] = units_by_sku.get(row.seller_sku, 0) + row.quantity_ordered
            value_by_sku.setdefault(row.seller_sku, CurrencySafeTotal()).add(
                row.item_proceeds_amount, row.item_proceeds_currency
            )
        return {
            sku: _SkuExposure(
                order_count=len(order_ids_by_sku.get(sku, set())),
                units=units_by_sku.get(sku, 0),
                value=value_by_sku.get(sku, CurrencySafeTotal()),
            )
            for sku in order_ids_by_sku
        }


def _rank_key(listing: ListingCollectionItem, exposure: _SkuExposure) -> tuple:
    is_error = listing.highest_issue_severity == "ERROR"
    is_warning = listing.highest_issue_severity == "WARNING"
    return (
        0 if is_error else 1,
        -listing.issue_count if is_error else 0,
        0 if is_warning else 1,
        -listing.issue_count if is_warning else 0,
        0 if not listing.is_buyable else 1,
        0 if not listing.is_active else 1,
        -exposure.order_count,
        # Explicit final tie-break for listings equal on every ranking
        # signal above — never left to depend on the incidental order
        # the underlying paginated fetch happened to return them in.
        listing.seller_sku,
    )

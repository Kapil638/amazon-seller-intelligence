"""12B.5A — Skill 2: Non-buyable Listing Investigator.

For one seller-identified SKU or ASIN: verifies ownership/marketplace
scope, reports active/buyable/discoverable state, summarizes Amazon-
reported issues by severity, shows recent order/unit evidence, and
reports last successful Listings/Orders synchronization.

When no SKU or ASIN is given at all (the "Why are my listings not
buyable?" launch card asks a plural, general question), this service
never guesses which listing the seller means — `.investigate()` instead
returns a prioritized *selection* of currently not-buyable listings
(`_select_candidates()`), so the seller (or a follow-up question naming
one specifically) can pick a target for the full investigation below.

Causal claim discipline (the skill's own required behavior): this
service only ever states "not buyable, and Amazon also has an
ERROR-severity issue on record for this SKU" as two separately observed
facts placed next to each other — it never states or implies the issue
*caused* the non-buyable state unless Amazon's own data makes exactly
that link explicit (which the pinned schema/contract never does — there
is no `causes`/`root_cause` field anywhere in `issues[]`). The evidence
records are tagged `kind="observed_fact"` for what the data states
directly and `kind="possible_explanation"` only for the single, clearly
hedged co-occurrence note — synthesis's own grounding rules
(`app/copilot/synthesis/validator.py`) forbid the LLM from going further
than a claim's own text already does.
"""

from __future__ import annotations

from uuid import UUID

from app.amazon.listings_read import AmazonListingsReadService, ListingCollectionItem, ListingDetail
from app.amazon.orders_read import AmazonOrdersReadService
from app.copilot.skills.contracts import SkillEvidence, incomplete_run, safe_deep_link
from app.copilot.skills.shared import CurrencySafeTotal, build_periods, fetch_all_pages
from app.core.exceptions import AmazonListingsParticipationNotFoundError, AmazonSellerListingNotFoundError
from app.core.validation import is_valid_asin, normalize_asin
from app.persistence.database import current_organization_id

_ERROR = "ERROR"
# Seller-facing card copy: "Why are my listings not buyable?" is
# deliberately plural/general — a seller usually has more than one
# affected listing, and guessing which one they mean would be a worse
# experience than a short prioritized list to choose from.
_CANDIDATE_SELECTION_LIMIT = 10


class ListingNotFoundForInvestigationError(Exception):
    """No listing in this participation matches the given ASIN/SKU.
    Sanitized on purpose — never distinguishes "wrong marketplace" from
    "never existed" any more finely than the read service already does."""


class NonBuyableListingEvidenceService:
    """Deterministic evidence for the `investigate_non_buyable_listing` tool."""

    def __init__(
        self,
        listings: AmazonListingsReadService | None = None,
        orders: AmazonOrdersReadService | None = None,
    ) -> None:
        self._listings = listings or AmazonListingsReadService()
        self._orders = orders or AmazonOrdersReadService()

    def investigate(
        self,
        marketplace_participation_id: UUID,
        *,
        seller_sku: str | None = None,
        asin: str | None = None,
        period_days: int | None = None,
    ) -> SkillEvidence:
        if not seller_sku and not asin:
            # No specific listing was named — rather than guess which one
            # the seller means, return a prioritized selection of
            # currently not-buyable listings instead of a single-listing
            # investigation. A follow-up question naming one specifically
            # gets the full detailed investigation below.
            return self._select_candidates(marketplace_participation_id, period_days=period_days)
        listing = self._resolve_listing(marketplace_participation_id, seller_sku=seller_sku, asin=asin)
        analysis_period, _comparison = build_periods(period_days)

        listings_summary = self._listings.get_summary(marketplace_participation_id)
        exposure = self._sku_exposure(marketplace_participation_id, listing.seller_sku, analysis_period)

        orders_freshness = None
        try:
            orders_freshness = self._orders.get_summary(marketplace_participation_id).sync
        except Exception:
            orders_freshness = None

        issues = listing.issues if isinstance(listing.issues, list) else []
        errors = [row for row in issues if isinstance(row, dict) and row.get("severity") == _ERROR]
        warnings = [row for row in issues if isinstance(row, dict) and row.get("severity") == "WARNING"]

        records: list[dict] = [
            {
                "kind": "observed_fact",
                "field": "is_buyable",
                "value": listing.is_buyable,
            },
            {
                "kind": "observed_fact",
                "field": "is_active",
                "value": listing.is_active,
            },
            {
                "kind": "observed_fact",
                "field": "is_discoverable",
                "value": listing.is_discoverable,
            },
            {
                "kind": "observed_fact",
                "field": "issue_summary",
                "error_count": len(errors),
                "warning_count": len(warnings),
                "issue_codes": sorted({str(row.get("code")) for row in issues if row.get("code")}),
            },
            {
                "kind": "observed_fact",
                "field": "recent_order_evidence",
                "order_count": exposure["order_count"],
                "units": exposure["units"],
                "order_value_by_currency": exposure["value"],
                "period": analysis_period.label,
            },
        ]
        if not listing.is_buyable and errors:
            records.append(
                {
                    "kind": "possible_explanation",
                    "note": (
                        "This listing is not buyable and currently has at least one ERROR-severity "
                        "issue on record. Amazon's data does not state that the issue caused the "
                        "non-buyable state — only that both are true at the same time."
                    ),
                }
            )
        elif not listing.is_buyable and not errors:
            records.append(
                {
                    "kind": "observed_fact",
                    "note": (
                        "This listing is not buyable, but no ERROR-severity issue is currently on "
                        "record for it — the cause cannot be attributed to any issue in this data."
                    ),
                }
            )

        limitations = [
            "Cannot state a root cause beyond what Amazon's status/issue fields already report.",
            "No buy-box or price-competition data exists in this schema.",
            "A fix cannot be confirmed until the next successful sync.",
        ]

        freshness_incomplete = incomplete_run(listings_summary.sync.status) or (
            orders_freshness is not None and incomplete_run(orders_freshness.status)
        )

        return SkillEvidence(
            skill_id="non_buyable_listing_investigator",
            skill_version="1.0.0",
            organization_id=current_organization_id(),
            marketplace_participation_ids=[marketplace_participation_id],
            analysis_period=analysis_period,
            listings_freshness=listings_summary.sync,
            orders_freshness=orders_freshness,
            has_newer_incomplete_run=freshness_incomplete,
            metrics={
                "is_buyable": listing.is_buyable,
                "is_active": listing.is_active,
                "is_discoverable": listing.is_discoverable,
                "issue_severity_error_count": len(errors),
                "issue_severity_warning_count": len(warnings),
                "seller_sku": listing.seller_sku,
                "asin": listing.asin,
            },
            records=records,
            limitations=limitations,
            confidence="high" if not freshness_incomplete else "medium",
            deep_links=[
                safe_deep_link(
                    f"/seller/listings?participation={marketplace_participation_id}&q={listing.seller_sku}",
                    "Open this listing",
                )
            ],
        )

    def _select_candidates(
        self, marketplace_participation_id: UUID, *, period_days: int | None
    ) -> SkillEvidence:
        analysis_period, _comparison = build_periods(period_days)
        listings_summary = self._listings.get_summary(marketplace_participation_id)
        candidates = self._fetch_not_buyable_listings(marketplace_participation_id)
        ranked = sorted(candidates, key=_candidate_rank_key)
        top = ranked[:_CANDIDATE_SELECTION_LIMIT]

        orders_freshness = None
        try:
            orders_freshness = self._orders.get_summary(marketplace_participation_id).sync
        except Exception:
            orders_freshness = None

        records = [
            {
                "seller_sku": row.seller_sku,
                "asin": row.asin,
                "issue_count": row.issue_count,
                "highest_issue_severity": row.highest_issue_severity,
            }
            for row in top
        ]

        limitations = [
            "This is a prioritized selection of not-buyable listings, not a single-listing "
            "investigation — ask about one specific SKU or ASIN for full detail.",
            "Cannot state a root cause beyond what Amazon's status/issue fields already report.",
        ]
        if len(candidates) > len(top):
            limitations.append(
                f"{len(candidates) - len(top)} additional not-buyable listing(s) are not shown here."
            )

        freshness_incomplete = incomplete_run(listings_summary.sync.status) or (
            orders_freshness is not None and incomplete_run(orders_freshness.status)
        )

        return SkillEvidence(
            skill_id="non_buyable_listing_investigator",
            skill_version="1.0.0",
            organization_id=current_organization_id(),
            marketplace_participation_ids=[marketplace_participation_id],
            analysis_period=analysis_period,
            listings_freshness=listings_summary.sync,
            orders_freshness=orders_freshness,
            has_newer_incomplete_run=freshness_incomplete,
            metrics={
                "not_buyable_count": len(candidates),
                "candidates_returned": len(records),
            },
            records=records,
            limitations=limitations,
            confidence="insufficient_data" if not candidates else ("medium" if freshness_incomplete else "high"),
            deep_links=[
                safe_deep_link(
                    f"/seller/listings?participation={marketplace_participation_id}&is_buyable=false",
                    "View not-buyable listings",
                )
            ],
        )

    def _fetch_not_buyable_listings(self, marketplace_participation_id: UUID) -> list[ListingCollectionItem]:
        def page(offset: int, limit: int) -> tuple[list[ListingCollectionItem], int]:
            result = self._listings.list_listings(
                marketplace_participation_id, is_buyable=False, offset=offset, limit=limit
            )
            return result.items, result.total

        return fetch_all_pages(page)

    def _resolve_listing(
        self, marketplace_participation_id: UUID, *, seller_sku: str | None, asin: str | None
    ) -> ListingDetail:
        search_term = seller_sku or asin or ""
        normalized_asin = normalize_asin(asin) if asin and is_valid_asin(asin.upper()) else None
        try:
            page = self._listings.list_listings(marketplace_participation_id, search=search_term, limit=25)
        except AmazonListingsParticipationNotFoundError:
            raise
        match = None
        for row in page.items:
            if seller_sku and row.seller_sku == seller_sku:
                match = row
                break
            if normalized_asin and row.asin == normalized_asin:
                match = row
                break
        if match is None:
            raise ListingNotFoundForInvestigationError(search_term)
        try:
            return self._listings.get_listing(marketplace_participation_id, match.id)
        except AmazonSellerListingNotFoundError as exc:
            raise ListingNotFoundForInvestigationError(search_term) from exc

    def _sku_exposure(self, marketplace_participation_id: UUID, seller_sku: str, period) -> dict:
        rows = self._orders.list_order_items_for_window(
            marketplace_participation_id, created_after=period.start, created_before=period.end
        )
        matching = [row for row in rows if row.seller_sku == seller_sku]
        total = CurrencySafeTotal()
        order_ids = set()
        units = 0
        for row in matching:
            order_ids.add(row.order_id)
            units += row.quantity_ordered
            total.add(row.item_proceeds_amount, row.item_proceeds_currency)
        return {"order_count": len(order_ids), "units": units, "value": total.as_dict()}


def _candidate_rank_key(listing: ListingCollectionItem) -> tuple:
    is_error = listing.highest_issue_severity == "ERROR"
    is_warning = listing.highest_issue_severity == "WARNING"
    return (
        0 if is_error else (1 if is_warning else 2),
        -listing.issue_count,
        listing.seller_sku,
    )

"""12B.6A — Sales and Traffic Business Report Read API (service layer).

Strictly read-only: no Amazon call, no secret resolution, no ingestion
trigger, no database write. Serves data already persisted by 12B.6A's
`AmazonSalesTrafficIngestionService`. Routes never accept
`organization_id` from the request — every method here derives it from
ASI's existing trusted context (`current_organization_id()`), exactly
like `AmazonOrdersReadService`.

**Grain discipline (handover doc §1a), enforced end to end:**
`salesAndTrafficByDate` (catalog-wide, dated) and `salesAndTrafficByAsin`
(product-level, never-dated, aggregated over its own exact requested
window) are two structurally different tables. This module never sums a
catalog-wide daily total together with a product-level window total as
if they were the same kind of number, and never presents `ordered
product sales` as revenue, proceeds, or profit anywhere in a response
model or docstring.

**Percentage aggregation, stated explicitly:** a percentage field can
never be correctly averaged across days/products by taking the mean of
already-computed percentages (that silently gives every row equal
weight regardless of how much traffic it represents). Every aggregated
percentage this module returns is *recomputed* from its own summed
numerator and denominator (e.g. `buy_box_percentage` over a range is
`sum(page-view-weighted buy-box exposure) / sum(page_views)`, not
`mean(daily buy_box_percentage)`), and is `None` when the denominator is
zero or no rows exist — never coerced to `0`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.core.exceptions import AmazonListingsParticipationNotFoundError, PersistenceNotConfiguredError
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSalesAndTrafficDailyFact, AmazonSalesAndTrafficProductFact
from app.persistence.repositories import (
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSalesTrafficDailyFactRepository,
    AmazonSalesTrafficProductFactRepository,
    AmazonSalesTrafficSyncCheckpointRepository,
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

SalesTrafficSyncStatus = Literal[
    "never_synchronized", "queued", "running", "waiting_to_retry", "succeeded", "failed", "partial", "timed_out"
]
ProductSortField = Literal["ordered_product_sales_amount", "units_ordered", "sessions", "unit_session_percentage"]
SortDirection = Literal["asc", "desc"]

_RUN_STATUS_TO_SYNC_STATUS: dict[str, SalesTrafficSyncStatus] = {
    "queued": "queued",
    "started": "running",
    "waiting_to_retry": "waiting_to_retry",
    "succeeded": "succeeded",
    "failed": "failed",
    "partial": "partial",
    "timed_out": "timed_out",
}


class SalesTrafficSyncEvidence(BaseModel):
    """Distinguishes "never synchronized" from every real run status.
    Built exclusively from `run_type='sales_and_traffic_report'` rows —
    a successful connection validation, Listings sync, or Orders sync is
    never a substitute for this."""

    model_config = ConfigDict(extra="forbid")

    status: SalesTrafficSyncStatus = "never_synchronized"
    failure_class: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    report_processing_status: str | None = None
    next_retry_at: datetime | None = None
    # The most recent run that actually *succeeded* — may be an earlier
    # run than `status` above describes, if the latest attempt failed.
    last_successful_synchronized_at: datetime | None = None
    # Product-level daily-ingestion high-water mark (handover doc §7) —
    # `None` until at least one single-day product-level report has
    # succeeded, independent of whatever catalog-wide-window runs (which
    # never advance this) may also have succeeded.
    synced_through_date: date | None = None


class SalesTrafficSummary(BaseModel):
    """Aggregate, catalog-wide totals for one marketplace participation
    over an explicit date range — `salesAndTrafficByDate` rows only.
    `None` fields mean "no rows in range" or "denominator was zero",
    never a silently-coerced zero. `currency_code` is `None` if the range
    spans more than one currency (should not happen in practice — one
    marketplace participation has one currency — but never silently
    assumed)."""

    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    start: date
    end: date
    days_with_data: int
    currency_code: str | None
    ordered_product_sales_amount: Decimal | None
    units_ordered: int | None
    total_order_items: int | None
    sessions: int | None
    page_views: int | None
    buy_box_percentage: Decimal | None
    unit_session_percentage: Decimal | None
    sync: SalesTrafficSyncEvidence


class DailyTrendPoint(BaseModel):
    """One `salesAndTrafficByDate` row, as originally reported — never
    re-aggregated, so callers building a chart see Amazon's own daily
    values directly."""

    model_config = ConfigDict(extra="forbid")

    report_date: date
    ordered_product_sales_amount: Decimal | None
    currency_code: str | None
    units_ordered: int | None
    sessions: int | None
    page_views: int | None
    buy_box_percentage: Decimal | None
    unit_session_percentage: Decimal | None


class DailyTrendResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    start: date
    end: date
    points: list[DailyTrendPoint] = Field(default_factory=list)


class CoverageRange(BaseModel):
    """One contiguous covered date range. Two selected windows are
    merged into a single `CoverageRange` only when they are exactly
    adjacent (no gap) — a genuine gap between covered periods is always
    preserved as two separate ranges, never smoothed over into one that
    would imply data that doesn't exist."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date


class ProductPerformanceRow(BaseModel):
    """One product's aggregate over every product-fact window selected by
    `_select_product_windows` for the query range (handover doc §1a — a
    product-fact row is never dated; this sums whole windows, never a
    fraction of one). Carries both traffic and conversion fields in one
    row so the UI's traffic-vs-conversion view is a client-side sort/
    filter of this same data, not a second query."""

    model_config = ConfigDict(extra="forbid")

    parent_asin: str
    child_asin: str | None
    seller_sku: str | None
    item_name: str | None
    currency_code: str | None
    ordered_product_sales_amount: Decimal | None
    units_ordered: int | None
    sessions: int | None
    page_views: int | None
    buy_box_percentage: Decimal | None
    unit_session_percentage: Decimal | None
    # Number of report windows actually summed into the fields above —
    # see `_select_product_windows`'s own docstring for the selection
    # policy. Never conflate this with "days of data": a single window
    # can span many days as one non-divisible aggregate.
    window_count: int
    # Coverage truthfulness (never silently implied by the fields above
    # alone) — added after the window-double-counting review found that
    # dropping every overlapping-but-uncovering window could silently
    # return partial evidence under a response shape that looked
    # complete. `coverage_complete` is True only when `covered_ranges`
    # is exactly `[(requested_start, requested_end)]` — a caller must
    # check this before treating the metrics above as covering the full
    # requested period.
    coverage_complete: bool
    covered_ranges: list[CoverageRange] = Field(default_factory=list)
    partial_coverage_reason: str | None = None
    excluded_overlapping_window_count: int = 0
    excluded_conflicting_granularity_window_count: int = 0


class ProductPerformanceResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    start: date
    end: date
    items: list[ProductPerformanceRow] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE


class SalesTrafficFreshness(BaseModel):
    """Coverage/staleness evidence, independent of `SalesTrafficSummary`
    — surfaced separately so the UI can show "queued, no data yet" or
    "stale" without conflating it with whatever numeric summary (possibly
    from an earlier successful run) is also on screen."""

    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    sync: SalesTrafficSyncEvidence
    earliest_daily_fact_date: date | None
    latest_daily_fact_date: date | None


def _weighted_percentage(numerator_rows: list[tuple[Decimal | None, int | None]]) -> Decimal | None:
    """Recomputes a percentage as `sum(value_i * weight_i) / sum(weight_i)`
    — never `mean(value_i)`. Skips rows with a `None` value or `None`/zero
    weight. Returns `None` if no row contributed (every row skipped, or
    the caller passed no rows at all) — a genuinely different case from
    "every weight was zero", but both are mathematically undefined
    (0/0), so both correctly collapse to the same `None`, never a
    fabricated `0`.

    **This is only mathematically correct when `weight` is the field's
    own documented denominator basis — never applied to an arbitrary
    "some other count" that merely happens to be available.** The one
    caller in this module that matters most, `buy_box_percentage`, is
    proven correct here rather than merely asserted:

    The pinned contract defines `buyBoxPercentage` as "the percentage of
    page views for which your offer was in the Buy Box" (handover doc
    §3) — i.e. for period *i*:

        buy_box_percentage_i = (buybox_page_views_i / page_views_i) * 100

    so `buy_box_percentage_i * page_views_i = buybox_page_views_i * 100`.
    Summing that identity over every period *i* and dividing by
    `sum(page_views_i)` gives:

        sum(buy_box_percentage_i * page_views_i) / sum(page_views_i)
      = 100 * sum(buybox_page_views_i) / sum(page_views_i)
      = the true, exact overall Buy Box percentage across every period
        combined — not an approximation, because `page_views` is
        *exactly* the denominator the field's own definition names.

    This is why `page_views` (not `sessions`, not a plain row count) is
    passed as the weight for every `buy_box_percentage` call in this
    module — using any other field as the weight would silently recover
    a different, incorrect number that merely looks plausible. Contrast
    with a field this module deliberately does *not* aggregate at all
    (e.g. `browser_session_percentage`): its own denominator
    (`sessions`, restricted further to a browser-vs-mobile split this
    module has no compatible combined weight for without also splitting
    every other summed field the same way) is not currently exposed on
    any response model, so no version of this function is ever called
    for it — silence, not a fabricated weighting, is the correct
    response to "the official schema doesn't expose a numerator/
    denominator pair usable here."
    """
    total_weight = 0
    weighted_sum = Decimal("0")
    for value, weight in numerator_rows:
        if value is None or not weight:
            continue
        weighted_sum += value * weight
        total_weight += weight
    if total_weight == 0:
        return None
    return weighted_sum / total_weight


def _sum_optional(values: list[int | None]) -> int | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present)


def _sum_optional_decimal(values: list[Decimal | None]) -> Decimal | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present, Decimal("0"))


@dataclass(frozen=True)
class _WindowSelection:
    """Internal result of `_select_product_windows` — never returned to
    an API caller directly; `list_product_performance` translates this
    into `ProductPerformanceRow`'s own coverage fields."""

    selected: list[AmazonSalesAndTrafficProductFact]
    excluded_overlapping: list[AmazonSalesAndTrafficProductFact]
    excluded_conflicting_granularity: list[AmazonSalesAndTrafficProductFact]
    covered_ranges: list[tuple[date, date]]
    coverage_complete: bool
    partial_coverage_reason: str | None


def _dedupe_identical_windows(
    rows: list[AmazonSalesAndTrafficProductFact],
) -> list[AmazonSalesAndTrafficProductFact]:
    """Collapses rows that share the exact same `(request_window_start,
    request_window_end)` down to one — the natural-key `UNIQUE`
    constraint (§4 of the handover doc) makes genuine duplicates
    impossible for real, persisted rows (an upsert always replaces the
    same row rather than inserting a second one), but this function is
    still defended independently of that DB guarantee, and doing this
    deterministically (sorted by the window itself, never by whatever
    order the caller's list happens to be in) is what makes the overall
    selection in `_select_product_windows` produce the identical result
    regardless of database row order."""
    canonical_order = sorted(rows, key=lambda r: (r.request_window_start, r.request_window_end))
    deduped: dict[tuple[date, date], AmazonSalesAndTrafficProductFact] = {}
    for row in canonical_order:
        deduped[(row.request_window_start, row.request_window_end)] = row
    return [deduped[key] for key in sorted(deduped)]


def _greedy_non_overlapping(
    rows: list[AmazonSalesAndTrafficProductFact],
) -> tuple[list[AmazonSalesAndTrafficProductFact], list[AmazonSalesAndTrafficProductFact]]:
    """Sorted by window length ascending (shortest/finest first, ties
    broken by start date — both intrinsic to the row, so the result is
    identical regardless of the input list's own order), greedily kept
    only if it does not overlap any window already kept. A window that
    overlaps an already-kept (necessarily shorter-or-equal) window is
    excluded entirely, never partially blended with it — blending would
    require attributing a sub-range of a non-divisible aggregate, which
    the report contract simply does not support."""
    selected: list[AmazonSalesAndTrafficProductFact] = []
    excluded: list[AmazonSalesAndTrafficProductFact] = []
    ordering_key = lambda r: ((r.request_window_end - r.request_window_start).days, r.request_window_start)  # noqa: E731
    for row in sorted(rows, key=ordering_key):
        overlaps_existing = any(
            row.request_window_start <= kept.request_window_end
            and kept.request_window_start <= row.request_window_end
            for kept in selected
        )
        if overlaps_existing:
            excluded.append(row)
        else:
            selected.append(row)
    return selected, excluded


def _merge_covered_ranges(rows: list[AmazonSalesAndTrafficProductFact]) -> list[tuple[date, date]]:
    """Merges a set of mutually non-overlapping windows into the minimal
    list of contiguous covered date ranges. Two windows merge into one
    range only when they are exactly adjacent — `end_i + 1 day ==
    start_{i+1}` — never merely close; a genuine gap (e.g. days 6-10
    missing between a 1-5 window and an 11-15 window) is preserved as
    two separate ranges, never smoothed into one that would imply
    coverage that doesn't exist."""
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: r.request_window_start)
    ranges: list[list[date]] = [[ordered[0].request_window_start, ordered[0].request_window_end]]
    for row in ordered[1:]:
        last = ranges[-1]
        if row.request_window_start <= last[1] + timedelta(days=1):
            if row.request_window_end > last[1]:
                last[1] = row.request_window_end
        else:
            ranges.append([row.request_window_start, row.request_window_end])
    return [(r[0], r[1]) for r in ranges]


def _select_product_windows(
    rows: list[AmazonSalesAndTrafficProductFact], *, requested_start: date, requested_end: date
) -> _WindowSelection:
    """A product-fact row's numbers are a non-divisible aggregate over
    its own exact `(request_window_start, request_window_end)` window
    (handover doc §1a — the contract gives no way to attribute part of
    that aggregate to a sub-range, so this function never proportionally
    splits one). If two report requests for the same product ever cover
    overlapping periods — e.g. 30 daily one-day windows *and* a single
    30-day catalog-style window independently requested with SKU
    granularity for the same period — summing every matching row
    unconditionally would double- (or worse) count the overlapping days.
    Nothing in this system's own request-building code prevents that:
    the sync trigger accepts an arbitrary `(date_granularity,
    asin_granularity, start, end)` combination on every call.

    **Policy, in priority order — never silently returns a partial
    answer while a complete one was available:**

    1. Deduplicate exact-identical windows (`_dedupe_identical_windows`)
       and drop any row whose `asin_granularity` conflicts with the
       majority grain present (`SKU` preferred over `CHILD` over
       `PARENT` when a conflict is somehow present — structurally
       shouldn't happen given the natural-key/CHECK-constraint
       relationship in the schema, but this function never trusts that
       guarantee blindly).
    2. Select the finest-grain mutually non-overlapping union
       (`_greedy_non_overlapping`). If this union's covered ranges
       exactly equal `[(requested_start, requested_end)]` — complete,
       gapless coverage of the whole requested period — use it. This is
       the common, intended case (e.g. daily windows fully covering a
       30-day query).
    3. Otherwise, look for a single available window whose own span
       exactly equals the full requested range. If one exists, prefer
       this single coarser-but-complete answer over a finer-but-partial
       one — a caller asking "what happened in this period" is better
       served by one honest complete number than a truncated finer
       breakdown.
    4. Otherwise, no combination and no single window achieves complete
       coverage — return the finer selection from step 2 *explicitly
       labeled partial*, with `covered_ranges` naming exactly what is
       and isn't covered, rather than silently presenting a subset as
       the whole answer.

    Never combines incompatible ASIN granularities (step 1) and never
    proportionally splits a wider window across dates it wasn't itself
    scoped to (no operation in this function ever divides a row's own
    values) — currency/marketplace separation is enforced by the caller
    (`list_product_performance`), which already scopes every query to
    one `marketplace_participation_id` and nulls the aggregated money
    field when selected windows disagree on currency.
    """
    if not rows:
        return _WindowSelection([], [], [], [], False, "no report windows available for this product in the requested range")

    granularities = {r.asin_granularity for r in rows}
    if len(granularities) > 1:
        preferred = next(g for g in ("SKU", "CHILD", "PARENT") if g in granularities)
        excluded_conflicting = [r for r in rows if r.asin_granularity != preferred]
        rows = [r for r in rows if r.asin_granularity == preferred]
    else:
        excluded_conflicting = []

    rows = _dedupe_identical_windows(rows)

    finer_selected, finer_excluded = _greedy_non_overlapping(rows)
    finer_covered = _merge_covered_ranges(finer_selected)
    if finer_covered == [(requested_start, requested_end)]:
        return _WindowSelection(finer_selected, finer_excluded, excluded_conflicting, finer_covered, True, None)

    exact_matches = [
        r for r in rows if r.request_window_start == requested_start and r.request_window_end == requested_end
    ]
    if exact_matches:
        chosen = exact_matches[0]  # `_dedupe_identical_windows` already guarantees at most one such row
        return _WindowSelection(
            [chosen], [r for r in rows if r is not chosen], excluded_conflicting,
            [(requested_start, requested_end)], True, None,
        )

    return _WindowSelection(
        finer_selected, finer_excluded, excluded_conflicting, finer_covered, False,
        "no available report window, or combination of non-overlapping windows, fully covers the requested period",
    )


def _sync_evidence(
    latest_run: AmazonIngestionRun | None,
    latest_successful_run: AmazonIngestionRun | None,
    synced_through_date: date | None,
) -> SalesTrafficSyncEvidence:
    if latest_run is None:
        return SalesTrafficSyncEvidence(status="never_synchronized", synced_through_date=synced_through_date)
    return SalesTrafficSyncEvidence(
        status=_RUN_STATUS_TO_SYNC_STATUS.get(latest_run.status, "never_synchronized"),
        failure_class=latest_run.failure_class,
        queued_at=latest_run.created_at,
        started_at=latest_run.started_at,
        completed_at=latest_run.completed_at,
        report_processing_status=latest_run.report_processing_status,
        next_retry_at=latest_run.next_retry_at,
        last_successful_synchronized_at=(
            latest_successful_run.completed_at if latest_successful_run is not None else None
        ),
        synced_through_date=synced_through_date,
    )


def _daily_point(row: AmazonSalesAndTrafficDailyFact) -> DailyTrendPoint:
    return DailyTrendPoint(
        report_date=row.report_date,
        ordered_product_sales_amount=row.ordered_product_sales_amount,
        currency_code=row.currency_code,
        units_ordered=row.units_ordered,
        sessions=row.sessions,
        page_views=row.page_views,
        buy_box_percentage=row.buy_box_percentage,
        unit_session_percentage=row.unit_session_percentage,
    )


class AmazonSalesTrafficReadService:
    """Read-only Sales and Traffic summary/trend/product/freshness
    evidence. No Amazon call, no secret resolution, no ingestion trigger,
    no write."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon sales and traffic read is not configured.")

    def _sync_for(self, session, marketplace_participation_id: UUID) -> SalesTrafficSyncEvidence:
        organization_id = self._org_id()
        run_repo = AmazonIngestionRunRepository(session)
        latest_run = run_repo.get_latest_sales_traffic_run(organization_id, marketplace_participation_id)
        latest_successful_run = run_repo.get_latest_successful_sales_traffic_run(
            organization_id, marketplace_participation_id
        )
        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(
            organization_id, marketplace_participation_id
        )
        return _sync_evidence(
            latest_run, latest_successful_run, checkpoint.synced_through_date if checkpoint is not None else None
        )

    def get_summary(self, marketplace_participation_id: UUID, *, start: date, end: date) -> SalesTrafficSummary:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            rows = AmazonSalesTrafficDailyFactRepository(session).list_for_range(
                organization_id, marketplace_participation_id, start=start, end=end
            )
            if rows is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))

            currencies = {r.currency_code for r in rows if r.currency_code is not None}
            currency_code = next(iter(currencies)) if len(currencies) == 1 else None
            sessions_total = _sum_optional([r.sessions for r in rows])
            units_total = _sum_optional([r.units_ordered for r in rows])
            # Money is summed only when every contributing row agrees on
            # currency — mirrors `OrdersSummary.order_value_sum`'s own
            # "both null together" convention. Summing raw decimal
            # amounts across genuinely different currencies would
            # silently add incompatible units together; `currency_code`
            # being `None` above is not merely a display nicety, it is
            # the caller's only signal that the amount below must not be
            # trusted either.
            ordered_product_sales_amount = (
                _sum_optional_decimal([r.ordered_product_sales_amount for r in rows])
                if len(currencies) <= 1
                else None
            )

            return SalesTrafficSummary(
                marketplace_participation_id=marketplace_participation_id,
                start=start,
                end=end,
                days_with_data=len(rows),
                currency_code=currency_code,
                ordered_product_sales_amount=ordered_product_sales_amount,
                units_ordered=units_total,
                total_order_items=_sum_optional([r.total_order_items for r in rows]),
                sessions=sessions_total,
                page_views=_sum_optional([r.page_views for r in rows]),
                buy_box_percentage=_weighted_percentage([(r.buy_box_percentage, r.page_views) for r in rows]),
                unit_session_percentage=(
                    (Decimal(units_total) / sessions_total * 100) if sessions_total else None
                ),
                sync=self._sync_for(session, marketplace_participation_id),
            )

    def get_daily_trend(self, marketplace_participation_id: UUID, *, start: date, end: date) -> DailyTrendResponse:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            rows = AmazonSalesTrafficDailyFactRepository(session).list_for_range(
                organization_id, marketplace_participation_id, start=start, end=end
            )
            if rows is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))
            return DailyTrendResponse(
                marketplace_participation_id=marketplace_participation_id,
                start=start, end=end,
                points=[_daily_point(r) for r in rows],
            )

    def list_product_performance(
        self,
        marketplace_participation_id: UUID,
        *,
        start: date,
        end: date,
        search: str | None = None,
        sort_by: ProductSortField = "ordered_product_sales_amount",
        sort_dir: SortDirection = "desc",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> ProductPerformanceResponse:
        self._require_persistence()
        limit = min(max(limit, 1), MAX_PAGE_SIZE)
        offset = max(offset, 0)
        organization_id = self._org_id()
        search = search.strip().upper() if search else None

        with session_scope() as session:
            rows = AmazonSalesTrafficProductFactRepository(session).list_for_window(
                organization_id, marketplace_participation_id, start=start, end=end
            )
            if rows is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))

            grouped: dict[tuple[str, str, str], list[AmazonSalesAndTrafficProductFact]] = {}
            for row in rows:
                key = (row.parent_asin, row.child_asin, row.seller_sku)
                grouped.setdefault(key, []).append(row)

            aggregated: list[ProductPerformanceRow] = []
            for (parent_asin, child_asin, seller_sku), raw_group in grouped.items():
                if search and search not in {parent_asin.upper(), child_asin.upper(), seller_sku.upper()}:
                    continue
                # Never sum across overlapping report windows for the
                # same product, and never silently present partial
                # coverage as complete — see _select_product_windows's
                # own docstring for the full policy.
                selection = _select_product_windows(raw_group, requested_start=start, requested_end=end)
                group = selection.selected
                currencies = {g.currency_code for g in group if g.currency_code is not None}
                sessions_total = _sum_optional([g.sessions for g in group])
                units_total = _sum_optional([g.units_ordered for g in group])
                # Money is summed only when every selected window agrees
                # on currency — see get_summary's identical reasoning.
                money_total = (
                    _sum_optional_decimal([g.ordered_product_sales_amount for g in group])
                    if len(currencies) <= 1
                    else None
                )
                aggregated.append(
                    ProductPerformanceRow(
                        parent_asin=parent_asin,
                        child_asin=child_asin or None,
                        seller_sku=seller_sku or None,
                        item_name=next((g.item_name for g in group if g.item_name), None),
                        currency_code=next(iter(currencies)) if len(currencies) == 1 else None,
                        ordered_product_sales_amount=money_total,
                        units_ordered=units_total,
                        sessions=sessions_total,
                        page_views=_sum_optional([g.page_views for g in group]),
                        buy_box_percentage=_weighted_percentage(
                            [(g.buy_box_percentage, g.page_views) for g in group]
                        ),
                        unit_session_percentage=(
                            (Decimal(units_total) / sessions_total * 100) if sessions_total else None
                        ),
                        window_count=len(group),
                        coverage_complete=selection.coverage_complete,
                        covered_ranges=[
                            CoverageRange(start=range_start, end=range_end)
                            for range_start, range_end in selection.covered_ranges
                        ],
                        partial_coverage_reason=selection.partial_coverage_reason,
                        excluded_overlapping_window_count=len(selection.excluded_overlapping),
                        excluded_conflicting_granularity_window_count=len(
                            selection.excluded_conflicting_granularity
                        ),
                    )
                )

            reverse = sort_dir == "desc"
            aggregated.sort(key=lambda r: (getattr(r, sort_by) is None, getattr(r, sort_by)), reverse=reverse)

            total = len(aggregated)
            page = aggregated[offset : offset + limit]
            return ProductPerformanceResponse(
                marketplace_participation_id=marketplace_participation_id,
                start=start, end=end, items=page, total=total, offset=offset, limit=limit,
            )

    def get_freshness(self, marketplace_participation_id: UUID) -> SalesTrafficFreshness:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            participation = AmazonMarketplaceParticipationRepository(session).get_by_id(
                organization_id, marketplace_participation_id
            )
            if participation is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))

            bounds = session.execute(
                select(
                    func.min(AmazonSalesAndTrafficDailyFact.report_date),
                    func.max(AmazonSalesAndTrafficDailyFact.report_date),
                ).where(AmazonSalesAndTrafficDailyFact.marketplace_participation_id == marketplace_participation_id)
            ).one()
            sync = self._sync_for(session, marketplace_participation_id)
            return SalesTrafficFreshness(
                marketplace_participation_id=marketplace_participation_id,
                sync=sync,
                earliest_daily_fact_date=bounds[0],
                latest_daily_fact_date=bounds[1],
            )


def get_amazon_sales_traffic_read_service() -> AmazonSalesTrafficReadService:
    return AmazonSalesTrafficReadService()

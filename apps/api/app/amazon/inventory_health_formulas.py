"""12B.6C — Inventory Health deterministic formulas.

Pure functions only: no I/O, no database session, no Amazon call, no
clock read except via an explicit `now` parameter every caller supplies.
Every function here is independently unit-testable without a database —
matching this codebase's own `RestartPolicy` precedent
(`scripts/supervisor.py`) for a pure, stateless value/formula layer kept
separate from the service that wires it to real data.

Corrections from the reviewed 12B.6C proposal (see the approval
message this module implements):

1. **Velocity is never `sum(units_ordered) / count(distinct windows)`.**
   A Sales & Traffic product fact is already an aggregate over its own
   exact `(request_window_start, request_window_end)` window — a single
   30-day fact is one row, not thirty. Velocity divides one selected
   fact's `units_ordered` by that fact's own *inclusive* day count.
   Overlapping windows (7-, 30-, 90-day facts covering the same days)
   are never summed — exactly one fact is selected per SKU.

2. **Absence of a product fact never implies zero demand.** Amazon's
   pinned Sales & Traffic contract documents no guarantee that a
   complete `SKU`-granularity report enumerates every seller SKU
   (verified: no such language exists in
   `docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md`). A SKU with no
   eligible fact returns `no_eligible_sales_traffic_fact`, never
   `no_recent_demand` — that classification is reserved for an eligible
   fact that explicitly reports `units_ordered == 0`.

`FORMULA_VERSION` must be bumped whenever any function in this module
changes in a way that would change a previously-computed result for the
same inputs — every evidence response this milestone returns carries it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal
from uuid import UUID

FORMULA_VERSION = "12b6c-1.0.0"

# Verified against this seller's own live, real FBA Inventory sync
# (2026-09-09, PR #26) — every one of 19 real summary rows reported
# `condition="NewItem"` verbatim, not assumed from memory or from a test
# fixture. `inventory_models.py`'s own module docstring confirms the
# pinned contract places no enum constraint on `condition` at all, so
# this is ASI product policy (V1 supports exactly one condition), not an
# Amazon-documented closed set.
SUPPORTED_CONDITION = "NewItem"

CoverageClassification = Literal["low_coverage", "healthy_coverage", "high_coverage", "unclassified"]
InventoryState = Literal["inactive", "out_of_stock", "low_coverage", "healthy_coverage", "high_coverage", "unclassified"]
DemandEligibility = Literal[
    "eligible", "no_eligible_sales_traffic_fact", "insufficient_window", "unsupported_condition", "missing_identity"
]
FreshnessState = Literal["fresh", "stale_inventory", "stale_sales", "stale_both"]
Overlay = Literal["demand_with_no_fulfillable_stock", "inbound_present", "unfulfillable_present", "researching_present", "no_recent_demand"]


@dataclass(frozen=True)
class ThresholdPolicy:
    """ASI/EWise product policy, not Amazon guidance — see
    `Settings.inventory_health_*` for where these are actually
    configured; this dataclass is the pure-function boundary the
    formulas below depend on, decoupled from `Settings` so tests never
    need a real `Settings` instance.

    Boundary behavior (all inclusive per the approved spec):
    exactly `low_coverage_days_threshold` -> healthy; exactly
    `high_coverage_days_threshold` -> healthy; below low -> low;
    above high -> high."""

    low_coverage_days_threshold: float = 14.0
    high_coverage_days_threshold: float = 90.0
    min_eligible_window_days: int = 7
    preferred_window_days: int = 30


@dataclass(frozen=True)
class EligibleProductFact:
    """The minimal, ORM-independent shape `select_canonical_product_fact`
    needs. Callers (the read service) are responsible for having already
    filtered to: same `marketplace_participation_id`, same `seller_sku`,
    `asin_granularity == "SKU"`, and an originating run whose `status ==
    "succeeded"` — this function performs *selection* among an
    already-eligible candidate set, never eligibility filtering itself."""

    id: UUID
    request_window_start: date
    request_window_end: date
    units_ordered: int | None
    ingestion_run_id: UUID


def covered_days(start: date, end: date) -> int:
    """Inclusive day count — a window from day N to day N is 1 covered
    day, not 0. Callers must ensure `start <= end` (already a database
    CHECK constraint on the source table); this raises rather than
    silently returning a negative count if that invariant is ever
    violated."""
    if start > end:
        raise ValueError(f"request_window_start ({start}) must not be after request_window_end ({end})")
    return (end - start).days + 1


def units_per_covered_day(units_ordered: int, days: int) -> float:
    if days <= 0:
        raise ValueError(f"covered_days must be positive, got {days}")
    return units_ordered / days


def select_canonical_product_fact(
    facts: list[EligibleProductFact], *, preferred_window_days: int = 30
) -> EligibleProductFact | None:
    """Deterministic selection of exactly one fact among an
    already-eligible candidate set for one (participation, SKU) —
    **never** a sum across multiple facts, which would double-count
    overlapping 7-/30-/90-day windows covering the same underlying
    days.

    Precedence (per the approved 12B.6C spec):
    1. Latest `request_window_end`.
    2. Among facts sharing that end date, prefer the one whose
       inclusive length exactly equals `preferred_window_days`.
    3. If none matches exactly, use the latest eligible window at that
       end date — chosen as the longest available window (the most
       information-dense single aggregate), never invented or padded.
       Its *actual* start/end/day-count are always what gets reported,
       never silently coerced to look like the preferred length.
    4. Final tie-break (only reachable if two facts somehow share both
       the same end date and the same inclusive length — the table's
       own natural key already makes this practically unreachable for
       one SKU/participation, but the tie-break exists so this function
       is provably total): earliest `request_window_start`, then `id`.

    Returns `None` for an empty candidate list — the caller reports
    `no_eligible_sales_traffic_fact` in that case, never fabricates a
    zero."""
    if not facts:
        return None

    max_end = max(f.request_window_end for f in facts)
    candidates = [f for f in facts if f.request_window_end == max_end]

    exact_preferred = [f for f in candidates if covered_days(f.request_window_start, f.request_window_end) == preferred_window_days]
    if exact_preferred:
        candidates = exact_preferred
    else:
        max_len = max(covered_days(f.request_window_start, f.request_window_end) for f in candidates)
        candidates = [f for f in candidates if covered_days(f.request_window_start, f.request_window_end) == max_len]

    candidates.sort(key=lambda f: (f.request_window_start, str(f.id)))
    return candidates[0]


@dataclass(frozen=True)
class VelocityResult:
    eligibility: DemandEligibility
    units_per_covered_day: float | None
    covered_days: int | None
    window_start: date | None
    window_end: date | None
    source_run_id: UUID | None


def compute_velocity(
    facts: list[EligibleProductFact],
    *,
    policy: ThresholdPolicy,
) -> VelocityResult:
    """Selects one canonical fact (never sums overlapping windows) and
    computes `units_per_covered_day` from it alone. `eligibility` is
    `"eligible"` only when a fact exists AND its actual inclusive window
    is at least `policy.min_eligible_window_days` — a real but
    too-short window (e.g. a lone 3-day fact) is `"insufficient_window"`,
    velocity/days both `None`, never a noisy extrapolation."""
    fact = select_canonical_product_fact(facts, preferred_window_days=policy.preferred_window_days)
    if fact is None:
        return VelocityResult(
            eligibility="no_eligible_sales_traffic_fact",
            units_per_covered_day=None,
            covered_days=None,
            window_start=None,
            window_end=None,
            source_run_id=None,
        )
    days = covered_days(fact.request_window_start, fact.request_window_end)
    if days < policy.min_eligible_window_days:
        return VelocityResult(
            eligibility="insufficient_window",
            units_per_covered_day=None,
            covered_days=days,
            window_start=fact.request_window_start,
            window_end=fact.request_window_end,
            source_run_id=fact.ingestion_run_id,
        )
    # `units_ordered` is a nullable Integer on the source table (Amazon
    # documents no guarantee it is always populated even on an eligible
    # row) — a present-but-null value is exactly as uninformative as an
    # absent fact, never coerced to 0.
    if fact.units_ordered is None:
        return VelocityResult(
            eligibility="no_eligible_sales_traffic_fact",
            units_per_covered_day=None,
            covered_days=days,
            window_start=fact.request_window_start,
            window_end=fact.request_window_end,
            source_run_id=fact.ingestion_run_id,
        )
    velocity = units_per_covered_day(fact.units_ordered, days)
    return VelocityResult(
        eligibility="eligible",
        units_per_covered_day=velocity,
        covered_days=days,
        window_start=fact.request_window_start,
        window_end=fact.request_window_end,
        source_run_id=fact.ingestion_run_id,
    )


def fulfillable_days_of_cover(fulfillable_quantity: int | None, units_per_day: float | None) -> float | None:
    """Excludes reserved, inbound, unfulfillable, and researching
    quantities entirely — only `fulfillable_quantity` ever appears in
    this formula. Returns `None` (never `0` and never a fabricated
    "infinite") whenever either input is unavailable, or when
    `units_per_day` is exactly `0` (an eligible fact reporting zero
    recent demand) — an explicit no-recent-demand fact must never be
    misread as "infinite runway"."""
    if fulfillable_quantity is None or units_per_day is None:
        return None
    if units_per_day == 0:
        return None
    return fulfillable_quantity / units_per_day


def classify_coverage(days_of_cover: float | None, *, policy: ThresholdPolicy) -> CoverageClassification:
    """Boundary behavior is inclusive at both ends of "healthy":
    exactly `low_coverage_days_threshold` is healthy; exactly
    `high_coverage_days_threshold` is healthy; strictly below low is
    low; strictly above high is high."""
    if days_of_cover is None:
        return "unclassified"
    if days_of_cover < policy.low_coverage_days_threshold:
        return "low_coverage"
    if days_of_cover <= policy.high_coverage_days_threshold:
        return "healthy_coverage"
    return "high_coverage"


def inventory_state(
    *, is_active: bool, fulfillable_quantity: int | None, coverage_classification: CoverageClassification
) -> InventoryState:
    """Factual precedence: inactive first (Amazon no longer reports this
    row at all), then a factual zero-fulfillable (`out_of_stock` — true
    regardless of whether any demand evidence exists), then the
    coverage-threshold classification. `fulfillable_quantity is None`
    (Amazon-unknown, not a real zero) never triggers `out_of_stock` —
    `0 == None` is `False` in Python, so this falls through to
    `coverage_classification` correctly, which will itself already be
    `"unclassified"` whenever cover could not be computed."""
    if not is_active:
        return "inactive"
    if fulfillable_quantity == 0:
        return "out_of_stock"
    return coverage_classification


@dataclass(frozen=True)
class PotentialUnitsResult:
    potential_units: int | None
    incomplete_inputs: bool


def potential_units(
    *,
    fulfillable_quantity: int | None,
    inbound_working_quantity: int | None,
    inbound_shipped_quantity: int | None,
    inbound_receiving_quantity: int | None,
) -> PotentialUnitsResult:
    """`potential_units = fulfillable + inbound_working + inbound_shipped
    + inbound_receiving` — a separate metric from fulfillable days of
    cover, never labeled "available stock" (inbound units are not yet
    fulfillable and may be delayed or rejected by Amazon).

    Strict all-or-nothing null policy, per review: the pinned FBA
    Inventory contract gives no guarantee that an absent quantity means
    zero — every one of `InventoryDetails`'s sub-fields (including the
    three inbound ones) is independently optional with no documented
    default (`inventory_models.py`'s own module docstring: no field in
    this schema documents a minimum/maximum, and `InventoryDetails`
    itself is absent entirely unless `details=true` was requested — an
    absent sub-field is "Amazon did not say," never "Amazon said
    zero"). `potential_units` is therefore `None` whenever **any** of
    the four required inputs is `None` — never a partial sum computed
    from only the components that happen to be present. Only when all
    four are known does this return a real, confirmed total; a real
    known `0` still contributes as `0` (a known zero is not "missing").
    `incomplete_inputs=True` is the caller's evidence reason for an
    incomplete-input warning (e.g. `incomplete_inbound_quantity_inputs`)
    whenever the result is `None` for this cause."""
    parts = [fulfillable_quantity, inbound_working_quantity, inbound_shipped_quantity, inbound_receiving_quantity]
    if any(p is None for p in parts):
        return PotentialUnitsResult(potential_units=None, incomplete_inputs=True)
    return PotentialUnitsResult(potential_units=sum(parts), incomplete_inputs=False)


def potential_days_of_cover(potential_units_value: int | None, units_per_day: float | None) -> float | None:
    """Exactly `fulfillable_days_of_cover`'s own null/zero policy,
    applied to the inbound-adjusted `potential_units` total instead of
    fulfillable alone — `None` when either input is unavailable
    (including when `potential_units` is itself `None` because an
    inbound component was unknown, per `potential_units`'s own strict
    all-or-nothing policy) or when `units_per_day == 0` (never rendered
    as infinite). Still never "available stock" — see `potential_units`'s
    own docstring for why inbound units are not yet fulfillable."""
    if potential_units_value is None or units_per_day is None:
        return None
    if units_per_day == 0:
        return None
    return potential_units_value / units_per_day


def freshness_state(
    *,
    inventory_observed_at: datetime | None,
    sales_ingestion_completed_at: datetime | None,
    now: datetime,
    max_inventory_age_seconds: float,
    max_sales_age_seconds: float,
) -> FreshnessState:
    """Two independent clocks, never blended into one figure. A `None`
    timestamp (never successfully synchronized at all) is treated as
    maximally stale for that side — folded into the same `"stale_*"`
    state as an old-but-present timestamp, since the approved state
    enum has exactly four values and a fifth "unknown" state was not
    approved; the caller's evidence response still exposes the raw
    `None` alongside this classification, so "never synced" is never
    actually hidden from an API consumer even though it maps to the
    same coarse state as "synced, but stale."""
    inventory_stale = inventory_observed_at is None or (now - inventory_observed_at).total_seconds() > max_inventory_age_seconds
    sales_stale = sales_ingestion_completed_at is None or (now - sales_ingestion_completed_at).total_seconds() > max_sales_age_seconds
    if inventory_stale and sales_stale:
        return "stale_both"
    if inventory_stale:
        return "stale_inventory"
    if sales_stale:
        return "stale_sales"
    return "fresh"


def demand_eligibility_for_condition(condition: str) -> DemandEligibility | None:
    """Returns `"unsupported_condition"` for any condition other than
    `SUPPORTED_CONDITION`, or `None` when the condition itself is
    supported (meaning: proceed to fact-based eligibility instead of
    short-circuiting here). Checked *before* any product-fact lookup —
    an unsupported-condition row must never consume/attach a SKU-level
    demand fact that a supported-condition row for the same SKU would
    also be entitled to (a Sales & Traffic product fact carries no
    condition dimension at all, so two condition-rows for one SKU could
    otherwise silently share one demand signal)."""
    if condition != SUPPORTED_CONDITION:
        return "unsupported_condition"
    return None


def overlays_for(
    *,
    fulfillable_quantity: int | None,
    demand_eligibility: DemandEligibility,
    units_per_day: float | None,
    inbound_working_quantity: int | None,
    inbound_shipped_quantity: int | None,
    inbound_receiving_quantity: int | None,
    unfulfillable_total_quantity: int | None,
    researching_total_quantity: int | None,
) -> list[Overlay]:
    """Informational overlays — never a replacement for `inventory_state`
    or `demand_eligibility`, always additive. A SKU can carry more than
    one overlay simultaneously (e.g. `low_coverage` inventory state
    *and* `inbound_present`)."""
    overlays: list[Overlay] = []
    if fulfillable_quantity == 0 and demand_eligibility == "eligible" and units_per_day is not None and units_per_day > 0:
        overlays.append("demand_with_no_fulfillable_stock")
    if (inbound_working_quantity or 0) > 0 or (inbound_shipped_quantity or 0) > 0 or (inbound_receiving_quantity or 0) > 0:
        overlays.append("inbound_present")
    if (unfulfillable_total_quantity or 0) > 0:
        overlays.append("unfulfillable_present")
    if (researching_total_quantity or 0) > 0:
        overlays.append("researching_present")
    if demand_eligibility == "eligible" and units_per_day == 0:
        overlays.append("no_recent_demand")
    return overlays

"""12B.6C — deterministic Inventory Health formula tests. No database, no
Amazon call, no AI call — every function under test is pure."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.amazon.inventory_health_formulas import (
    SUPPORTED_CONDITION,
    EligibleProductFact,
    PotentialUnitsResult,
    ThresholdPolicy,
    classify_coverage,
    compute_velocity,
    covered_days,
    demand_eligibility_for_condition,
    freshness_state,
    fulfillable_days_of_cover,
    inventory_state,
    overlays_for,
    potential_units,
    select_canonical_product_fact,
    units_per_covered_day,
)

UTC = timezone.utc


def _fact(start: date, end: date, units: int | None, *, fact_id: UUID | None = None, run_id: UUID | None = None) -> EligibleProductFact:
    return EligibleProductFact(
        id=fact_id or uuid4(),
        request_window_start=start,
        request_window_end=end,
        units_ordered=units,
        ingestion_run_id=run_id or uuid4(),
    )


# --- covered_days: inclusive arithmetic, leap days, month boundaries -------


def test_covered_days_single_day_window_is_one() -> None:
    assert covered_days(date(2026, 1, 1), date(2026, 1, 1)) == 1


def test_covered_days_thirty_day_window_is_thirty_inclusive() -> None:
    # Jan 1 .. Jan 30 inclusive = 30 distinct calendar days.
    assert covered_days(date(2026, 1, 1), date(2026, 1, 30)) == 30


def test_covered_days_crosses_a_month_boundary_correctly() -> None:
    # Jan 25, 26, 27, 28, 29, 30, 31, Feb 1, 2, 3 = 10 days.
    assert covered_days(date(2026, 1, 25), date(2026, 2, 3)) == 10


def test_covered_days_leap_year_february_is_twenty_nine() -> None:
    # 2028 is a leap year — Feb 1..Feb 29 inclusive = 29 days.
    assert covered_days(date(2028, 2, 1), date(2028, 2, 29)) == 29


def test_covered_days_non_leap_year_february_is_twenty_eight() -> None:
    # 2027 is not a leap year — Feb 1..Feb 28 inclusive = 28 days.
    assert covered_days(date(2027, 2, 1), date(2027, 2, 28)) == 28


def test_covered_days_spanning_a_leap_day_counts_it() -> None:
    # Feb 28, 29, Mar 1 in a leap year = 3 days (the leap day itself
    # must not be silently skipped by any date-diff shortcut).
    assert covered_days(date(2028, 2, 28), date(2028, 3, 1)) == 3


def test_covered_days_rejects_start_after_end() -> None:
    with pytest.raises(ValueError):
        covered_days(date(2026, 2, 1), date(2026, 1, 1))


# --- units_per_covered_day: the core corrected formula ----------------------


def test_300_units_over_30_inclusive_days_is_10_per_day() -> None:
    assert units_per_covered_day(300, 30) == 10.0


def test_one_30_day_row_does_not_divide_by_one() -> None:
    """The rejected formula (`sum(units_ordered) / count(distinct
    windows)`) would divide 300 by 1 (one row = one window) and report
    300 units/day. The corrected formula divides by the window's own
    inclusive day count instead."""
    fact = _fact(date(2026, 1, 1), date(2026, 1, 30), 300)
    result = compute_velocity([fact], policy=ThresholdPolicy())
    assert result.units_per_covered_day == pytest.approx(10.0)
    assert result.units_per_covered_day != 300.0


def test_units_per_covered_day_rejects_non_positive_days() -> None:
    with pytest.raises(ValueError):
        units_per_covered_day(10, 0)


# --- select_canonical_product_fact: never sum overlapping windows ----------


def test_overlapping_7_30_90_day_facts_are_never_summed() -> None:
    """Three facts all ending on the same date, covering 7/30/90 days
    respectively, all describe overlapping (not additive) periods —
    exactly one must be selected, never combined."""
    end = date(2026, 3, 31)
    seven = _fact(end - timedelta(days=6), end, 70)  # 7-day window, 10/day
    thirty = _fact(end - timedelta(days=29), end, 300)  # 30-day window, 10/day
    ninety = _fact(end - timedelta(days=89), end, 900)  # 90-day window, 10/day

    selected = select_canonical_product_fact([seven, thirty, ninety], preferred_window_days=30)

    assert selected is thirty
    # A summed/blended result would report units far outside any single
    # fact's own units_ordered — assert the selected fact's own value is
    # used verbatim, not a sum (70+300+900=1270) or an average.
    assert selected.units_ordered == 300


def test_selection_prefers_exact_preferred_window_length_at_latest_end_date() -> None:
    end = date(2026, 6, 30)
    fourteen_day = _fact(end - timedelta(days=13), end, 140)
    thirty_day = _fact(end - timedelta(days=29), end, 300)

    selected = select_canonical_product_fact([fourteen_day, thirty_day], preferred_window_days=30)
    assert selected is thirty_day


def test_selection_falls_back_to_longest_window_when_no_exact_preferred_length_exists() -> None:
    """Neither candidate is exactly 30 days — the longer of the two
    (most information-dense) is selected, and its *actual* length is
    what a caller must report, never coerced to look like 30."""
    end = date(2026, 6, 30)
    fourteen_day = _fact(end - timedelta(days=13), end, 140)
    twenty_one_day = _fact(end - timedelta(days=20), end, 210)

    selected = select_canonical_product_fact([fourteen_day, twenty_one_day], preferred_window_days=30)
    assert selected is twenty_one_day
    assert covered_days(selected.request_window_start, selected.request_window_end) == 21


def test_selection_always_prefers_the_latest_end_date_over_a_longer_older_window() -> None:
    older_ninety_day = _fact(date(2026, 1, 1), date(2026, 3, 31), 900)
    newer_seven_day = _fact(date(2026, 4, 25), date(2026, 5, 1), 70)

    selected = select_canonical_product_fact([older_ninety_day, newer_seven_day], preferred_window_days=30)
    assert selected is newer_seven_day


def test_selection_returns_none_for_an_empty_candidate_list() -> None:
    assert select_canonical_product_fact([], preferred_window_days=30) is None


def test_deterministic_tie_breaker_selects_exactly_one_fact() -> None:
    """Two facts sharing both the same end date and the same inclusive
    length (practically unreachable given the source table's own
    natural key, but this function must still be provably total) are
    broken by earliest start, then by id — never ambiguous, never
    raising, always exactly one winner."""
    end = date(2026, 5, 15)
    fact_a = _fact(end - timedelta(days=6), end, 70, fact_id=UUID(int=1))
    fact_b = _fact(end - timedelta(days=6), end, 71, fact_id=UUID(int=2))

    selected = select_canonical_product_fact([fact_a, fact_b], preferred_window_days=30)
    assert selected is fact_a  # lower id wins the tie, deterministically
    # Re-running with the same inputs must always produce the same winner.
    assert select_canonical_product_fact([fact_b, fact_a], preferred_window_days=30) is fact_a


# --- compute_velocity: eligibility gating -----------------------------------


def test_compute_velocity_no_facts_is_not_zero_demand() -> None:
    """The core correction: absence of a fact is 'no reliable evidence',
    never 'zero sales'."""
    result = compute_velocity([], policy=ThresholdPolicy())
    assert result.eligibility == "no_eligible_sales_traffic_fact"
    assert result.units_per_covered_day is None


def test_compute_velocity_window_shorter_than_minimum_is_insufficient() -> None:
    fact = _fact(date(2026, 1, 1), date(2026, 1, 3), 30)  # 3-day window
    result = compute_velocity([fact], policy=ThresholdPolicy(min_eligible_window_days=7))
    assert result.eligibility == "insufficient_window"
    assert result.units_per_covered_day is None
    assert result.covered_days == 3


def test_compute_velocity_eligible_fact_with_explicit_zero_units_is_eligible_not_missing() -> None:
    """An eligible fact that explicitly reports units_ordered=0 is real
    evidence of zero recent demand — distinct from no evidence at all."""
    fact = _fact(date(2026, 1, 1), date(2026, 1, 30), 0)
    result = compute_velocity([fact], policy=ThresholdPolicy())
    assert result.eligibility == "eligible"
    assert result.units_per_covered_day == 0.0


def test_compute_velocity_null_units_ordered_on_an_otherwise_eligible_fact_is_not_evidence() -> None:
    fact = _fact(date(2026, 1, 1), date(2026, 1, 30), None)
    result = compute_velocity([fact], policy=ThresholdPolicy())
    assert result.eligibility == "no_eligible_sales_traffic_fact"


# --- fulfillable_days_of_cover -----------------------------------------------


def test_fulfillable_days_of_cover_basic() -> None:
    assert fulfillable_days_of_cover(140, 10.0) == 14.0


def test_fulfillable_days_of_cover_null_fulfillable_is_null() -> None:
    assert fulfillable_days_of_cover(None, 10.0) is None


def test_fulfillable_days_of_cover_null_velocity_is_null() -> None:
    assert fulfillable_days_of_cover(100, None) is None


def test_fulfillable_days_of_cover_zero_velocity_is_null_never_infinite() -> None:
    """Zero recent demand must never render as 'infinite cover' — the
    absence of a computable number is `None`, and the UI/API layer is
    responsible for a "No recent demand" label, never a fabricated
    huge number or an actual float('inf')."""
    assert fulfillable_days_of_cover(500, 0.0) is None


def test_fulfillable_days_of_cover_excludes_every_other_quantity_bucket() -> None:
    """Only fulfillable_quantity and velocity are ever inputs — this is
    a structural assertion on the function's own signature (it accepts
    no reserved/inbound/unfulfillable/researching parameter at all),
    documented here as a regression guard against someone adding one."""
    import inspect

    params = set(inspect.signature(fulfillable_days_of_cover).parameters)
    assert params == {"fulfillable_quantity", "units_per_day"}


# --- classify_coverage: exact boundary behavior -----------------------------


@pytest.mark.parametrize(
    "days,expected",
    [
        (13.9, "low_coverage"),
        (14.0, "healthy_coverage"),  # exactly at the low boundary -> healthy
        (50.0, "healthy_coverage"),
        (90.0, "healthy_coverage"),  # exactly at the high boundary -> healthy
        (90.1, "high_coverage"),
        (0.0, "low_coverage"),
    ],
)
def test_classify_coverage_boundaries(days: float, expected: str) -> None:
    assert classify_coverage(days, policy=ThresholdPolicy()) == expected


def test_classify_coverage_null_is_unclassified() -> None:
    assert classify_coverage(None, policy=ThresholdPolicy()) == "unclassified"


# --- inventory_state: factual precedence ------------------------------------


def test_inventory_state_inactive_takes_precedence_over_everything() -> None:
    assert (
        inventory_state(is_active=False, fulfillable_quantity=500, coverage_classification="healthy_coverage")
        == "inactive"
    )


def test_inventory_state_zero_fulfillable_is_out_of_stock_regardless_of_demand_evidence() -> None:
    assert inventory_state(is_active=True, fulfillable_quantity=0, coverage_classification="unclassified") == "out_of_stock"


def test_inventory_state_null_fulfillable_is_never_out_of_stock() -> None:
    """None (Amazon-unknown) must never be conflated with a real zero."""
    result = inventory_state(is_active=True, fulfillable_quantity=None, coverage_classification="unclassified")
    assert result != "out_of_stock"
    assert result == "unclassified"


def test_inventory_state_uses_coverage_classification_otherwise() -> None:
    assert inventory_state(is_active=True, fulfillable_quantity=140, coverage_classification="low_coverage") == "low_coverage"


# --- potential_units: null policy -------------------------------------------


def test_potential_units_sums_fulfillable_and_all_inbound() -> None:
    result = potential_units(
        fulfillable_quantity=100, inbound_working_quantity=10, inbound_shipped_quantity=20, inbound_receiving_quantity=5
    )
    assert result == PotentialUnitsResult(potential_units=135, incomplete_inputs=False)


def test_potential_units_null_fulfillable_yields_null_result_and_incomplete_flag() -> None:
    result = potential_units(
        fulfillable_quantity=None, inbound_working_quantity=10, inbound_shipped_quantity=20, inbound_receiving_quantity=5
    )
    assert result.potential_units is None
    assert result.incomplete_inputs is True


def test_potential_units_missing_inbound_component_is_never_treated_as_confirmed_zero() -> None:
    """A null inbound_receiving_quantity is excluded from the sum (not
    padded with 0), and the result is flagged incomplete so the caller
    must warn — the returned number alone must never be presented as a
    confirmed total."""
    result = potential_units(
        fulfillable_quantity=100, inbound_working_quantity=10, inbound_shipped_quantity=20, inbound_receiving_quantity=None
    )
    assert result.potential_units == 130
    assert result.incomplete_inputs is True


def test_potential_units_all_inbound_null_still_excludes_not_zero_pads() -> None:
    result = potential_units(
        fulfillable_quantity=100, inbound_working_quantity=None, inbound_shipped_quantity=None, inbound_receiving_quantity=None
    )
    assert result.potential_units == 100
    assert result.incomplete_inputs is True


# --- freshness_state ----------------------------------------------------------


def test_freshness_state_both_fresh() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    result = freshness_state(
        inventory_observed_at=now - timedelta(hours=1),
        sales_ingestion_completed_at=now - timedelta(hours=2),
        now=now,
        max_inventory_age_seconds=48 * 3600,
        max_sales_age_seconds=48 * 3600,
    )
    assert result == "fresh"


def test_freshness_state_stale_inventory_only() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    result = freshness_state(
        inventory_observed_at=now - timedelta(hours=100),
        sales_ingestion_completed_at=now - timedelta(hours=1),
        now=now,
        max_inventory_age_seconds=48 * 3600,
        max_sales_age_seconds=48 * 3600,
    )
    assert result == "stale_inventory"


def test_freshness_state_stale_sales_only() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    result = freshness_state(
        inventory_observed_at=now - timedelta(hours=1),
        sales_ingestion_completed_at=now - timedelta(hours=100),
        now=now,
        max_inventory_age_seconds=48 * 3600,
        max_sales_age_seconds=48 * 3600,
    )
    assert result == "stale_sales"


def test_freshness_state_stale_both() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    result = freshness_state(
        inventory_observed_at=now - timedelta(hours=100),
        sales_ingestion_completed_at=now - timedelta(hours=100),
        now=now,
        max_inventory_age_seconds=48 * 3600,
        max_sales_age_seconds=48 * 3600,
    )
    assert result == "stale_both"


def test_freshness_state_never_synced_inventory_is_treated_as_stale_not_fresh() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    result = freshness_state(
        inventory_observed_at=None,
        sales_ingestion_completed_at=now - timedelta(hours=1),
        now=now,
        max_inventory_age_seconds=48 * 3600,
        max_sales_age_seconds=48 * 3600,
    )
    assert result == "stale_inventory"


def test_freshness_state_at_the_exact_age_boundary_is_not_stale() -> None:
    now = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)
    result = freshness_state(
        inventory_observed_at=now - timedelta(seconds=100),
        sales_ingestion_completed_at=now - timedelta(seconds=100),
        now=now,
        max_inventory_age_seconds=100,
        max_sales_age_seconds=100,
    )
    assert result == "fresh"


# --- condition handling -------------------------------------------------------


def test_supported_condition_is_new_item_verified_against_real_synced_data() -> None:
    assert SUPPORTED_CONDITION == "NewItem"


def test_demand_eligibility_for_condition_supported_returns_none_meaning_proceed() -> None:
    assert demand_eligibility_for_condition("NewItem") is None


@pytest.mark.parametrize("condition", ["UsedGood", "UsedLikeNew", "Refurbished", "", "newitem"])
def test_demand_eligibility_for_condition_unsupported_short_circuits(condition: str) -> None:
    assert demand_eligibility_for_condition(condition) == "unsupported_condition"


# --- overlays -----------------------------------------------------------------


def test_overlay_demand_with_no_fulfillable_stock_requires_eligible_positive_velocity() -> None:
    overlays = overlays_for(
        fulfillable_quantity=0,
        demand_eligibility="eligible",
        units_per_day=5.0,
        inbound_working_quantity=None,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=None,
        researching_total_quantity=None,
    )
    assert "demand_with_no_fulfillable_stock" in overlays


def test_overlay_out_of_stock_alone_without_eligible_demand_gets_no_overlay() -> None:
    """A factual `fulfillable_quantity = 0` may produce inventory_state
    'out_of_stock' even without sales evidence — but the higher-urgency
    overlay requires *eligible positive* velocity, never just a factual
    zero."""
    overlays = overlays_for(
        fulfillable_quantity=0,
        demand_eligibility="no_eligible_sales_traffic_fact",
        units_per_day=None,
        inbound_working_quantity=None,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=None,
        researching_total_quantity=None,
    )
    assert "demand_with_no_fulfillable_stock" not in overlays


def test_overlay_inbound_present() -> None:
    overlays = overlays_for(
        fulfillable_quantity=100,
        demand_eligibility="eligible",
        units_per_day=5.0,
        inbound_working_quantity=3,
        inbound_shipped_quantity=0,
        inbound_receiving_quantity=0,
        unfulfillable_total_quantity=None,
        researching_total_quantity=None,
    )
    assert "inbound_present" in overlays


def test_overlay_unfulfillable_present() -> None:
    overlays = overlays_for(
        fulfillable_quantity=100,
        demand_eligibility="eligible",
        units_per_day=5.0,
        inbound_working_quantity=None,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=4,
        researching_total_quantity=None,
    )
    assert "unfulfillable_present" in overlays


def test_overlay_researching_present() -> None:
    overlays = overlays_for(
        fulfillable_quantity=100,
        demand_eligibility="eligible",
        units_per_day=5.0,
        inbound_working_quantity=None,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=None,
        researching_total_quantity=2,
    )
    assert "researching_present" in overlays


def test_overlay_no_recent_demand_only_for_eligible_zero_velocity() -> None:
    overlays = overlays_for(
        fulfillable_quantity=100,
        demand_eligibility="eligible",
        units_per_day=0.0,
        inbound_working_quantity=None,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=None,
        researching_total_quantity=None,
    )
    assert "no_recent_demand" in overlays


def test_overlay_no_recent_demand_absent_when_eligibility_is_missing_not_zero() -> None:
    overlays = overlays_for(
        fulfillable_quantity=100,
        demand_eligibility="no_eligible_sales_traffic_fact",
        units_per_day=None,
        inbound_working_quantity=None,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=None,
        researching_total_quantity=None,
    )
    assert "no_recent_demand" not in overlays


def test_overlays_can_combine() -> None:
    overlays = overlays_for(
        fulfillable_quantity=0,
        demand_eligibility="eligible",
        units_per_day=5.0,
        inbound_working_quantity=3,
        inbound_shipped_quantity=None,
        inbound_receiving_quantity=None,
        unfulfillable_total_quantity=1,
        researching_total_quantity=None,
    )
    assert set(overlays) == {"demand_with_no_fulfillable_stock", "inbound_present", "unfulfillable_present"}

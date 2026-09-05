"""12B.6A — AmazonSalesTrafficReadService. Strictly read-only, seeded
directly through `AmazonSalesTrafficDailyFactRepository`/
`AmazonSalesTrafficProductFactRepository` — no SP-API client, ingestion
service, or worker anywhere in this file. Proves ownership scoping,
grain-correct aggregation (weighted percentages, never a bare mean;
never blending catalog-wide and product-level numbers), and the sync/
freshness evidence contract.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest

from app.amazon.sales_traffic_read import (
    AmazonSalesTrafficReadService,
    CoverageRange,
    _select_product_windows,
    _weighted_percentage,
)
from app.core.exceptions import AmazonListingsParticipationNotFoundError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonSalesAndTrafficProductFact
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSalesTrafficDailyFactRepository,
    AmazonSalesTrafficProductFactRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


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


def _enqueue_run(scope: dict, *, day: date):
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"], data_start_time=day, data_end_time=day,
            date_granularity="DAY", asin_granularity="SKU",
        )
        return claim.run_id


def _seed_daily(scope: dict, run_id, *, day: date, units: int, sessions: int, page_views: int, buy_box: str):
    with session_scope() as session:
        AmazonSalesTrafficDailyFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            report_date=day, date_granularity="DAY", last_ingestion_run_id=run_id,
            fields={
                "currency_code": "USD",
                "ordered_product_sales_amount": Decimal("100.00"),
                "units_ordered": units,
                "sessions": sessions,
                "page_views": page_views,
                "buy_box_percentage": Decimal(buy_box),
                "total_order_items": units,
            },
        )


def _seed_product(scope: dict, run_id, *, sku: str, units: int, sessions: int, sales: str):
    with session_scope() as session:
        AmazonSalesTrafficProductFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 1), asin_granularity="SKU",
            parent_asin="B0PARENT001", child_asin="B0CHILD001", seller_sku=sku, last_ingestion_run_id=run_id,
            fields={
                "currency_code": "USD", "units_ordered": units, "sessions": sessions,
                "ordered_product_sales_amount": Decimal(sales), "page_views": sessions,
            },
        )


# --- ownership ---------------------------------------------------------


def test_get_summary_raises_for_foreign_participation() -> None:
    _seed_scope()
    service = AmazonSalesTrafficReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.get_summary(uuid4(), start=date(2026, 8, 1), end=date(2026, 8, 1))


def test_get_daily_trend_raises_for_foreign_participation() -> None:
    _seed_scope()
    service = AmazonSalesTrafficReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.get_daily_trend(uuid4(), start=date(2026, 8, 1), end=date(2026, 8, 1))


def test_list_products_raises_for_foreign_participation() -> None:
    _seed_scope()
    service = AmazonSalesTrafficReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.list_product_performance(uuid4(), start=date(2026, 8, 1), end=date(2026, 8, 1))


def test_get_freshness_raises_for_foreign_participation() -> None:
    _seed_scope()
    service = AmazonSalesTrafficReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.get_freshness(uuid4())


# --- summary: never-synchronized baseline -------------------------------


def test_summary_never_synchronized_has_no_rows_and_correct_sync_status() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficReadService()
    summary = service.get_summary(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 31))
    assert summary.days_with_data == 0
    assert summary.ordered_product_sales_amount is None
    assert summary.sync.status == "never_synchronized"
    assert summary.sync.synced_through_date is None


# --- summary: grain-correct weighted aggregation ------------------------


def test_summary_aggregates_sums_and_recomputes_weighted_percentages() -> None:
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    # Day 1: small traffic, perfect buy box. Day 2: much larger traffic,
    # lower buy box — a naive mean of the two percentages would be wrong;
    # the page-view-weighted recomputation must favor day 2.
    _seed_daily(scope, run_id, day=date(2026, 8, 1), units=1, sessions=10, page_views=10, buy_box="100.00")
    _seed_daily(scope, run_id, day=date(2026, 8, 2), units=9, sessions=90, page_views=90, buy_box="80.00")

    service = AmazonSalesTrafficReadService()
    summary = service.get_summary(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 2))

    assert summary.days_with_data == 2
    assert summary.units_ordered == 10
    assert summary.sessions == 100
    assert summary.ordered_product_sales_amount == Decimal("200.00")
    # naive mean would be 90.00 — weighted-by-page-views is 82.00
    assert summary.buy_box_percentage == Decimal("82.00")
    # units_ordered / sessions * 100 = 10/100*100 = 10
    assert summary.unit_session_percentage == Decimal("10")


def test_daily_trend_returns_unaggregated_points_in_date_order() -> None:
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    _seed_daily(scope, run_id, day=date(2026, 8, 2), units=2, sessions=20, page_views=20, buy_box="50.00")
    _seed_daily(scope, run_id, day=date(2026, 8, 1), units=1, sessions=10, page_views=10, buy_box="100.00")

    service = AmazonSalesTrafficReadService()
    trend = service.get_daily_trend(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 2))

    assert [p.report_date for p in trend.points] == [date(2026, 8, 1), date(2026, 8, 2)]
    assert trend.points[0].units_ordered == 1
    assert trend.points[1].units_ordered == 2


# --- product performance -------------------------------------------------


def test_product_performance_aggregates_across_windows_and_sorts() -> None:
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    _seed_product(scope, run_id, sku="SKU-LOW", units=1, sessions=100, sales="10.00")
    _seed_product(scope, run_id, sku="SKU-HIGH", units=20, sessions=50, sales="500.00")

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert result.total == 2
    assert result.items[0].seller_sku == "SKU-HIGH"  # default sort: ordered_product_sales_amount desc
    assert result.items[0].ordered_product_sales_amount == Decimal("500.00")
    assert result.items[1].seller_sku == "SKU-LOW"


def test_product_performance_never_double_counts_overlapping_windows() -> None:
    """The exact scenario the grain rule (handover doc §1a) warns about:
    nothing stops a caller from independently requesting 30 daily 1-day
    windows *and* a single 30-day wide window at SKU granularity for the
    same product over the same period. Summing every matching row
    unconditionally would double-count every overlapping day. The finer
    (daily) windows must win; the wider, fully-overlapping window must be
    excluded entirely — never partially blended with it."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))

    with session_scope() as session:
        repo = AmazonSalesTrafficProductFactRepository(session)
        for offset in range(30):
            day = date(2026, 8, 1) + timedelta(days=offset)
            repo.upsert(
                organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
                request_window_start=day, request_window_end=day, asin_granularity="SKU",
                parent_asin="B0PARENT001", child_asin="B0CHILD001", seller_sku="SKU-OVERLAP",
                last_ingestion_run_id=run_id, fields={"units_ordered": 1, "sessions": 10, "page_views": 10},
            )
        # A second, independently-requested wide window covering the
        # exact same 30-day period at the same SKU granularity.
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 30), asin_granularity="SKU",
            parent_asin="B0PARENT001", child_asin="B0CHILD001", seller_sku="SKU-OVERLAP",
            last_ingestion_run_id=run_id, fields={"units_ordered": 300, "sessions": 300, "page_views": 300},
        )

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(
        scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 30)
    )

    assert result.total == 1
    row = result.items[0]
    # The 30 daily windows win (finer grain); the wide window is dropped
    # entirely — never 30 + 300 = 330.
    assert row.units_ordered == 30
    assert row.sessions == 300
    assert row.window_count == 30
    # Scenario 1 (complete daily coverage vs. one broad window): the
    # finer selection achieves complete coverage on its own, so it wins
    # outright — never falls back to the broad window at all.
    assert row.coverage_complete is True
    assert row.covered_ranges == [CoverageRange(start=date(2026, 8, 1), end=date(2026, 8, 30))]
    assert row.partial_coverage_reason is None
    assert row.excluded_overlapping_window_count == 1  # the dropped wide window


def test_product_performance_sums_genuinely_non_overlapping_windows_normally() -> None:
    """Two windows that do NOT overlap (e.g. two consecutive 15-day
    catalog-style windows covering the first and second half of a month)
    must still be summed together normally — the dedup logic must not be
    so conservative that it discards legitimate, non-overlapping data."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))

    with session_scope() as session:
        repo = AmazonSalesTrafficProductFactRepository(session)
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 15), asin_granularity="SKU",
            parent_asin="B0P", child_asin="B0C", seller_sku="SKU-SPLIT",
            last_ingestion_run_id=run_id, fields={"units_ordered": 10, "sessions": 100},
        )
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 16), request_window_end=date(2026, 8, 30), asin_granularity="SKU",
            parent_asin="B0P", child_asin="B0C", seller_sku="SKU-SPLIT",
            last_ingestion_run_id=run_id, fields={"units_ordered": 20, "sessions": 200},
        )

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(
        scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 30)
    )

    assert result.total == 1
    row = result.items[0]
    assert row.units_ordered == 30
    assert row.sessions == 300
    assert row.window_count == 2
    # Scenario 4 (adjacent non-overlapping windows): two exactly-adjacent
    # windows merge into a single contiguous covered range, and that
    # range equals the full requested period.
    assert row.coverage_complete is True
    assert row.covered_ranges == [CoverageRange(start=date(2026, 8, 1), end=date(2026, 8, 30))]


def test_product_performance_partial_daily_coverage_falls_back_to_a_complete_broad_window() -> None:
    """Scenario 2: daily windows cover only the first 5 of a requested
    30-day period, but a single window exists whose own span exactly
    equals the full requested period. The complete-but-coarser answer
    must win over the finer-but-partial one — never silently return only
    5 days of evidence under a response shape that looks like it answers
    the full 30-day question."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))

    with session_scope() as session:
        repo = AmazonSalesTrafficProductFactRepository(session)
        for offset in range(5):
            day = date(2026, 8, 1) + timedelta(days=offset)
            repo.upsert(
                organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
                request_window_start=day, request_window_end=day, asin_granularity="SKU",
                parent_asin="B0P", child_asin="B0C", seller_sku="SKU-PARTIAL",
                last_ingestion_run_id=run_id, fields={"units_ordered": 1, "sessions": 10},
            )
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 30), asin_granularity="SKU",
            parent_asin="B0P", child_asin="B0C", seller_sku="SKU-PARTIAL",
            last_ingestion_run_id=run_id, fields={"units_ordered": 300, "sessions": 3000},
        )

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(
        scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 30)
    )

    assert result.total == 1
    row = result.items[0]
    assert row.coverage_complete is True
    assert row.window_count == 1  # the single complete broad window, not the 5 partial daily ones
    assert row.units_ordered == 300
    assert row.sessions == 3000
    assert row.covered_ranges == [CoverageRange(start=date(2026, 8, 1), end=date(2026, 8, 30))]
    assert row.partial_coverage_reason is None


def test_product_performance_incomplete_coverage_with_no_complete_alternative_is_labeled_partial() -> None:
    """Scenario 3: only the first 5 days of a requested 30-day period
    have any data at all, and no single window covers the full period
    either. The response must say so explicitly — `coverage_complete=
    False`, `covered_ranges` naming exactly the 5 covered days, and a
    human-readable reason — never silently present 5 days of numbers as
    if they answered a 30-day question."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))

    with session_scope() as session:
        repo = AmazonSalesTrafficProductFactRepository(session)
        for offset in range(5):
            day = date(2026, 8, 1) + timedelta(days=offset)
            repo.upsert(
                organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
                request_window_start=day, request_window_end=day, asin_granularity="SKU",
                parent_asin="B0P", child_asin="B0C", seller_sku="SKU-NOALT",
                last_ingestion_run_id=run_id, fields={"units_ordered": 1, "sessions": 10},
            )

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(
        scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 30)
    )

    assert result.total == 1
    row = result.items[0]
    assert row.coverage_complete is False
    assert row.window_count == 5
    assert row.units_ordered == 5  # honest partial sum — only the 5 covered days
    assert row.covered_ranges == [CoverageRange(start=date(2026, 8, 1), end=date(2026, 8, 5))]
    assert row.partial_coverage_reason is not None
    assert "cover" in row.partial_coverage_reason.lower()


def _fact(
    *,
    window_start: date,
    window_end: date,
    asin_granularity: str = "SKU",
    parent_asin: str = "B0P",
    child_asin: str = "B0C",
    seller_sku: str = "SKU-X",
    currency_code: str | None = "USD",
    units_ordered: int | None = None,
) -> AmazonSalesAndTrafficProductFact:
    """Unpersisted fixture for direct, DB-free unit tests of the pure
    selection functions — never flushed to a session, so `.id` stays
    `None`; the selection algorithm must never depend on it (see
    `_dedupe_identical_windows`'s own docstring)."""
    return AmazonSalesAndTrafficProductFact(
        marketplace_participation_id=uuid4(),
        request_window_start=window_start, request_window_end=window_end,
        asin_granularity=asin_granularity, parent_asin=parent_asin, child_asin=child_asin, seller_sku=seller_sku,
        currency_code=currency_code, units_ordered=units_ordered,
    )


def test_select_product_windows_nested_window_falls_back_to_the_complete_outer_window() -> None:
    """Scenario 5: a fine window nested entirely inside a broad window
    that itself exactly equals the requested range. The nested window
    alone cannot achieve complete coverage (it's narrower than the
    request), so the algorithm must fall back to the complete outer
    window rather than presenting the nested subset as the answer."""
    outer = _fact(window_start=date(2026, 1, 1), window_end=date(2026, 1, 30))
    inner = _fact(window_start=date(2026, 1, 10), window_end=date(2026, 1, 20))

    result = _select_product_windows([outer, inner], requested_start=date(2026, 1, 1), requested_end=date(2026, 1, 30))

    assert result.coverage_complete is True
    assert result.selected == [outer]
    assert result.covered_ranges == [(date(2026, 1, 1), date(2026, 1, 30))]


def test_select_product_windows_deduplicates_identical_windows() -> None:
    """Scenario 6: two rows with the exact same window (a genuine
    duplicate is impossible for real, persisted rows — the natural-key
    UNIQUE constraint prevents it — but this function is defended
    independently of that DB guarantee)."""
    a = _fact(window_start=date(2026, 1, 1), window_end=date(2026, 1, 10), units_ordered=5)
    b = _fact(window_start=date(2026, 1, 1), window_end=date(2026, 1, 10), units_ordered=999)

    result = _select_product_windows([a, b], requested_start=date(2026, 1, 1), requested_end=date(2026, 1, 10))

    assert len(result.selected) == 1  # never both — that would double-count
    assert result.coverage_complete is True


def test_select_product_windows_excludes_conflicting_granularity_deterministically() -> None:
    """Scenario 7: rows with different `asin_granularity` values for what
    the caller grouped as "the same product" — structurally shouldn't
    happen given the schema's own CHECK constraint tying granularity to
    identifier shape, but this function never blends them regardless.
    SKU is deterministically preferred over CHILD over PARENT."""
    sku_row = _fact(window_start=date(2026, 1, 1), window_end=date(2026, 1, 10), asin_granularity="SKU")
    child_row = _fact(window_start=date(2026, 1, 1), window_end=date(2026, 1, 10), asin_granularity="CHILD")

    result = _select_product_windows(
        [sku_row, child_row], requested_start=date(2026, 1, 1), requested_end=date(2026, 1, 10)
    )

    assert result.selected == [sku_row]
    assert result.excluded_conflicting_granularity == [child_row]


def test_product_performance_never_combines_different_currencies() -> None:
    """Scenario 8 (currency half): if selected windows for the same
    product somehow disagree on currency, the money field must be `None`
    — never a nonsensical sum of incompatible units silently labeled
    with no currency at all. Non-monetary fields (units, sessions) are
    still safe to sum, since they carry no currency."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))

    with session_scope() as session:
        repo = AmazonSalesTrafficProductFactRepository(session)
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 15), asin_granularity="SKU",
            parent_asin="B0P", child_asin="B0C", seller_sku="SKU-CCY",
            last_ingestion_run_id=run_id,
            fields={"units_ordered": 10, "sessions": 100, "ordered_product_sales_amount": Decimal("100.00"), "currency_code": "USD"},
        )
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 16), request_window_end=date(2026, 8, 30), asin_granularity="SKU",
            parent_asin="B0P", child_asin="B0C", seller_sku="SKU-CCY",
            last_ingestion_run_id=run_id,
            fields={"units_ordered": 20, "sessions": 200, "ordered_product_sales_amount": Decimal("50.00"), "currency_code": "EUR"},
        )

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(
        scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 30)
    )

    row = result.items[0]
    assert row.currency_code is None
    assert row.ordered_product_sales_amount is None  # never summed across currencies
    assert row.units_ordered == 30  # currency-independent fields still sum normally
    assert row.sessions == 300


def test_select_product_windows_is_deterministic_regardless_of_input_row_order() -> None:
    """Scenario 9: the same set of rows, fed in two different orders,
    must produce byte-identical selection results — nothing about the
    algorithm may depend on whatever order a database query happened to
    return rows in."""
    windows = [_fact(window_start=date(2026, 1, 1) + timedelta(days=i), window_end=date(2026, 1, 1) + timedelta(days=i)) for i in range(10)]

    forward = _select_product_windows(windows, requested_start=date(2026, 1, 1), requested_end=date(2026, 1, 10))
    reversed_result = _select_product_windows(
        list(reversed(windows)), requested_start=date(2026, 1, 1), requested_end=date(2026, 1, 10)
    )
    shuffled = [windows[3], windows[0], windows[7], windows[1], windows[9], windows[2], windows[8], windows[4], windows[6], windows[5]]
    shuffled_result = _select_product_windows(
        shuffled, requested_start=date(2026, 1, 1), requested_end=date(2026, 1, 10)
    )

    assert {id(w) for w in forward.selected} == {id(w) for w in reversed_result.selected} == {id(w) for w in shuffled_result.selected}
    assert forward.covered_ranges == reversed_result.covered_ranges == shuffled_result.covered_ranges
    assert forward.coverage_complete == reversed_result.coverage_complete == shuffled_result.coverage_complete is True


def test_product_performance_search_filters_by_sku() -> None:
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    _seed_product(scope, run_id, sku="SKU-A", units=1, sessions=10, sales="10.00")
    _seed_product(scope, run_id, sku="SKU-B", units=1, sessions=10, sales="10.00")

    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(
        scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 1), search="sku-a"
    )
    assert result.total == 1
    assert result.items[0].seller_sku == "SKU-A"


def test_product_performance_never_returns_a_window_outside_the_query_range() -> None:
    """A product-fact row's window must be entirely inside [start, end] —
    never partially overlapping, which would misattribute a wider
    aggregate to a narrower period."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    with session_scope() as session:
        AmazonSalesTrafficProductFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 7, 1), request_window_end=date(2026, 8, 15), asin_granularity="SKU",
            parent_asin="B0X", child_asin="B0X1", seller_sku="SKU-WIDE", last_ingestion_run_id=run_id,
            fields={"units_ordered": 999},
        )
    service = AmazonSalesTrafficReadService()
    result = service.list_product_performance(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 31))
    assert result.total == 0


# --- freshness -----------------------------------------------------------


def test_freshness_reports_bounds_and_checkpoint() -> None:
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    _seed_daily(scope, run_id, day=date(2026, 8, 1), units=1, sessions=10, page_views=10, buy_box="50.00")
    service = AmazonSalesTrafficReadService()
    freshness = service.get_freshness(scope["participation_id"])
    assert freshness.earliest_daily_fact_date == date(2026, 8, 1)
    assert freshness.latest_daily_fact_date == date(2026, 8, 1)
    assert freshness.sync.status == "queued"  # run enqueued but not finalized in this test


# --- _weighted_percentage: direct unit proofs -------------------------------


def test_weighted_percentage_empty_input_returns_none() -> None:
    assert _weighted_percentage([]) is None


def test_weighted_percentage_zero_denominator_returns_none_not_zero() -> None:
    """A single row with an explicit zero weight (e.g. a day with zero
    page views) must produce `None` — the percentage is mathematically
    undefined (0/0), never coerced to `0`."""
    assert _weighted_percentage([(Decimal("50.00"), 0)]) is None


def test_weighted_percentage_null_denominator_returns_none() -> None:
    assert _weighted_percentage([(Decimal("50.00"), None)]) is None


def test_weighted_percentage_mixed_null_and_zero_denominators_skips_both() -> None:
    """A null weight (Amazon didn't report page views that day) and an
    explicit zero weight (Amazon reported zero page views) are two
    different real situations, but both must be excluded from the
    weighted sum identically — including either would divide by a
    quantity that carries no genuine information."""
    result = _weighted_percentage(
        [
            (Decimal("50.00"), 0),  # zero weight — excluded
            (None, 10),  # null value — excluded regardless of weight
            (Decimal("80.00"), 20),  # the only row that counts
        ]
    )
    assert result == Decimal("80.00")


def test_weighted_percentage_unequal_denominators_favors_the_larger_one() -> None:
    """Hand-computed proof: day A (10 page views, 100%) and day B (90
    page views, 80%) weighted-average to 82%, not the naive mean of 90%
    — (100*10 + 80*90) / 100 = (1000 + 7200) / 100 = 82."""
    result = _weighted_percentage([(Decimal("100.00"), 10), (Decimal("80.00"), 90)])
    assert result == Decimal("82.00")
    naive_mean = (Decimal("100.00") + Decimal("80.00")) / 2
    assert result != naive_mean


def test_weighted_percentage_preserves_fractional_precision() -> None:
    """Division is never truncated to an integer or a coarse rounding —
    a genuinely repeating result keeps enough fractional precision for
    the caller (ultimately a `Numeric(7,4)` column) to round correctly
    rather than losing precision inside this function first."""
    result = _weighted_percentage([(Decimal("33.3333"), 1), (Decimal("66.6667"), 2)])
    # (33.3333*1 + 66.6667*2) / 3 = 166.6667 / 3 = 55.5555666...
    assert result is not None
    assert abs(result - Decimal("55.55556666666666666666666667")) < Decimal("0.0001")


def test_summary_weighted_buy_box_percentage_can_legitimately_differ_from_a_naive_per_day_average() -> None:
    """End-to-end proof that the summary's recomputed Buy Box percentage
    is a genuine weighted recomputation, not merely echoing one day's
    supplied value or a naive average of the two days' supplied values —
    it must equal the page-view-weighted figure and disagree with the
    naive mean whenever the two days' traffic volumes differ."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    _seed_daily(scope, run_id, day=date(2026, 8, 1), units=1, sessions=10, page_views=10, buy_box="100.00")
    _seed_daily(scope, run_id, day=date(2026, 8, 2), units=9, sessions=90, page_views=90, buy_box="80.00")

    service = AmazonSalesTrafficReadService()
    summary = service.get_summary(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 2))

    naive_mean = (Decimal("100.00") + Decimal("80.00")) / 2
    assert summary.buy_box_percentage != naive_mean
    assert summary.buy_box_percentage == Decimal("82.00")


def test_daily_trend_preserves_amazons_own_supplied_percentage_unaggregated() -> None:
    """The daily trend endpoint must show Amazon's own supplied per-day
    value verbatim, never the recomputed cross-day aggregate — the two
    are different things and must never be conflated in the same
    response shape."""
    scope = _seed_scope()
    run_id = _enqueue_run(scope, day=date(2026, 8, 1))
    _seed_daily(scope, run_id, day=date(2026, 8, 1), units=1, sessions=10, page_views=10, buy_box="97.42")

    service = AmazonSalesTrafficReadService()
    trend = service.get_daily_trend(scope["participation_id"], start=date(2026, 8, 1), end=date(2026, 8, 1))

    assert trend.points[0].buy_box_percentage == Decimal("97.42")

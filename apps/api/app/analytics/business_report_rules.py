"""Deterministic Business Report analytics (business-analytics-v1).

Conversion uses Unit Session Percentage when present on enough rows;
otherwise units_ordered / sessions when both totals exist.

Heuristics are V1 defaults, not market-share evidence:
    HIGH_TRAFFIC_LOW_CONVERSION — sessions >= min AND conversion < threshold
    LOW_BUY_BOX_PERCENTAGE — sessions >= min AND buy box < threshold
    Low-volume ASINs are not flagged.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from app.core.config import get_settings
from app.models.reports import (
    BusinessPerformanceRow,
    BusinessSummary,
    BusinessTables,
    ProductPerformanceRow,
    ReportFinding,
    ReportFindingSeverity,
)
from app.reports.values import RATE_QUANT

BUSINESS_ANALYTICS_VERSION = "business-analytics-v1"


def analyze_business_report(
    rows: list[BusinessPerformanceRow],
) -> tuple[BusinessSummary, BusinessTables, list[ReportFinding]]:
    settings = get_settings()
    min_sessions = settings.business_low_conversion_min_sessions
    low_conversion = Decimal(str(settings.business_low_conversion))
    low_buybox = Decimal(str(settings.business_low_buybox))
    buybox_min_sessions = settings.business_low_buybox_min_sessions

    products = _aggregate_asins(rows)
    products.sort(key=lambda item: item.sessions, reverse=True)

    sessions = sum(item.sessions for item in products)
    page_view_values = [item.page_views for item in products if item.page_views is not None]
    units_values = [item.units_ordered for item in products if item.units_ordered is not None]
    sales_values = [
        item.ordered_product_sales for item in products if item.ordered_product_sales is not None
    ]
    page_views = sum(page_view_values) if page_view_values else None
    units = sum(units_values) if units_values else None
    sales = sum(sales_values, Decimal("0")).quantize(Decimal("0.01")) if sales_values else None

    conversion = _weighted_conversion(products, sessions, units)
    buy_box = _weighted_buy_box(products)

    summary = BusinessSummary(
        sessions=sessions,
        page_views=page_views,
        units_ordered=units,
        ordered_product_sales=sales,
        conversion=conversion,
        buy_box_percentage=buy_box,
        asin_count=len(products),
    )

    findings: list[ReportFinding] = []
    if products:
        top_traffic = max(products, key=lambda item: item.sessions)
        if top_traffic.sessions > 0:
            findings.append(
                ReportFinding(
                    code="HIGHEST_TRAFFIC",
                    severity=ReportFindingSeverity.INFO,
                    entity=top_traffic.asin,
                    message=(
                        f"{top_traffic.asin} has the highest observed sessions "
                        f"({top_traffic.sessions:,}) in this report."
                    ),
                )
            )
        with_sales = [item for item in products if item.ordered_product_sales]
        if with_sales:
            top_sales = max(with_sales, key=lambda item: item.ordered_product_sales or Decimal("0"))
            findings.append(
                ReportFinding(
                    code="HIGHEST_SALES",
                    severity=ReportFindingSeverity.INFO,
                    entity=top_sales.asin,
                    message=(
                        f"{top_sales.asin} has the highest observed ordered product sales "
                        f"in this report."
                    ),
                )
            )

    for item in products:
        if (
            item.sessions >= min_sessions
            and item.conversion is not None
            and item.conversion < low_conversion
        ):
            findings.append(
                ReportFinding(
                    code="HIGH_TRAFFIC_LOW_CONVERSION",
                    severity=ReportFindingSeverity.HIGH,
                    entity=item.asin,
                    message=(
                        f"{item.asin} has high traffic ({item.sessions:,} sessions) "
                        f"and low observed conversion ({_pct(item.conversion)})."
                    ),
                )
            )
        if (
            item.buy_box_percentage is not None
            and item.sessions >= buybox_min_sessions
            and item.buy_box_percentage < low_buybox
        ):
            findings.append(
                ReportFinding(
                    code="LOW_BUY_BOX_PERCENTAGE",
                    severity=ReportFindingSeverity.MEDIUM,
                    entity=item.asin,
                    message=(
                        f"{item.asin} has a low observed Buy Box percentage "
                        f"({_pct(item.buy_box_percentage)}) with {item.sessions:,} sessions."
                    ),
                )
            )

    return summary, BusinessTables(products=products), findings


def _aggregate_asins(rows: list[BusinessPerformanceRow]) -> list[ProductPerformanceRow]:
    buckets: dict[str, list[BusinessPerformanceRow]] = defaultdict(list)
    for row in rows:
        buckets[row.asin].append(row)
    products: list[ProductPerformanceRow] = []
    for asin, group in buckets.items():
        sessions = sum(item.sessions for item in group)
        page_values = [item.page_views for item in group if item.page_views is not None]
        unit_values = [item.units_ordered for item in group if item.units_ordered is not None]
        sales_values = [
            item.ordered_product_sales for item in group if item.ordered_product_sales is not None
        ]
        buy_box_values = [
            (item.buy_box_percentage, item.sessions)
            for item in group
            if item.buy_box_percentage is not None
        ]
        conversion_values = [
            (item.unit_session_percentage, item.sessions)
            for item in group
            if item.unit_session_percentage is not None
        ]
        units = sum(unit_values) if unit_values else None
        sales = (
            sum(sales_values, Decimal("0")).quantize(Decimal("0.01")) if sales_values else None
        )
        conversion = _session_weighted(conversion_values)
        if conversion is None and sessions > 0 and units is not None:
            conversion = (Decimal(units) / Decimal(sessions)).quantize(
                RATE_QUANT, rounding=ROUND_HALF_UP
            )
        title = next((item.title for item in group if item.title), None)
        sku = next((item.sku for item in group if item.sku), None)
        products.append(
            ProductPerformanceRow(
                asin=asin,
                title=title,
                sku=sku,
                sessions=sessions,
                page_views=sum(page_values) if page_values else None,
                units_ordered=units,
                ordered_product_sales=sales,
                conversion=conversion,
                buy_box_percentage=_session_weighted(buy_box_values),
            )
        )
    return products


def _weighted_conversion(
    products: list[ProductPerformanceRow],
    sessions: int,
    units: int | None,
) -> Decimal | None:
    weighted = [
        (item.conversion, item.sessions)
        for item in products
        if item.conversion is not None
    ]
    if weighted:
        return _session_weighted(weighted)
    if sessions > 0 and units is not None:
        return (Decimal(units) / Decimal(sessions)).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)
    return None


def _weighted_buy_box(products: list[ProductPerformanceRow]) -> Decimal | None:
    weighted = [
        (item.buy_box_percentage, item.sessions)
        for item in products
        if item.buy_box_percentage is not None
    ]
    return _session_weighted(weighted)


def _session_weighted(pairs: list[tuple[Decimal, int]]) -> Decimal | None:
    if not pairs:
        return None
    total_sessions = sum(sessions for _, sessions in pairs)
    if total_sessions <= 0:
        return None
    total = sum((value * Decimal(sessions) for value, sessions in pairs), Decimal("0"))
    return (total / Decimal(total_sessions)).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'))}%"

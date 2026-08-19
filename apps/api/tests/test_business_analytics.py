from decimal import Decimal

from app.models.reports import BusinessPerformanceRow
from app.services.business_analytics_service import BusinessAnalyticsService


def _row(**overrides: object) -> BusinessPerformanceRow:
    payload: dict[str, object] = {
        "asin": "B0CHILD0001",
        "title": "AuroraGlow Vitamin D3 Softgels",
        "sessions": 120,
        "page_views": 180,
        "units_ordered": 12,
        "ordered_product_sales": Decimal("4800"),
        "unit_session_percentage": Decimal("0.10"),
        "buy_box_percentage": Decimal("0.96"),
    }
    payload.update(overrides)
    return BusinessPerformanceRow.model_validate(payload)


def test_business_totals_and_asin_aggregation() -> None:
    rows = [
        _row(),
        _row(sessions=30, page_views=40, units_ordered=3, ordered_product_sales=Decimal("1200")),
        _row(
            asin="B0CHILD0002",
            title="NimbusFoam Memory Contour Pillow",
            sessions=80,
            page_views=90,
            units_ordered=1,
            ordered_product_sales=Decimal("799"),
            unit_session_percentage=Decimal("0.0125"),
            buy_box_percentage=Decimal("0.70"),
        ),
        _row(
            asin="B0CHILD0003",
            title="PeakPulse Resistance Bands Set",
            sessions=5,
            page_views=6,
            units_ordered=0,
            ordered_product_sales=Decimal("0"),
            unit_session_percentage=Decimal("0"),
            buy_box_percentage=Decimal("1"),
        ),
    ]
    summary, tables, findings = BusinessAnalyticsService().analyze(rows)
    assert summary.sessions == 235
    assert summary.page_views == 316
    assert summary.units_ordered == 16
    assert summary.ordered_product_sales == Decimal("6799.00")
    assert summary.asin_count == 3

    d3 = next(item for item in tables.products if item.asin == "B0CHILD0001")
    assert d3.sessions == 150
    assert d3.units_ordered == 15

    codes = {item.code: item for item in findings}
    assert codes["HIGHEST_TRAFFIC"].entity == "B0CHILD0001"
    assert codes["HIGHEST_SALES"].entity == "B0CHILD0001"
    low_conversion = [item for item in findings if item.code == "HIGH_TRAFFIC_LOW_CONVERSION"]
    assert any(item.entity == "B0CHILD0002" for item in low_conversion)
    assert all(item.entity != "B0CHILD0003" for item in low_conversion)
    buybox = [item for item in findings if item.code == "LOW_BUY_BOX_PERCENTAGE"]
    assert any(item.entity == "B0CHILD0002" for item in buybox)
    assert all(item.entity != "B0CHILD0003" for item in buybox)

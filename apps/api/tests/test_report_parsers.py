from decimal import Decimal

from app.reports.business_parser import BusinessReportParser
from app.reports.columns import normalize_header
from app.reports.search_term_parser import SearchTermReportParser
from app.reports.values import parse_money, parse_percentage
from app.services.report_analysis_service import ReportAnalysisService, UploadedReport
from tests.report_helpers import fixture_bytes


def test_money_parsing() -> None:
    assert parse_money("₹1,234.56") == Decimal("1234.56")
    assert parse_money("INR 1,234.56") == Decimal("1234.56")
    assert parse_money("1,234.56") == Decimal("1234.56")
    assert parse_money("") is None


def test_percentage_parsing() -> None:
    assert parse_percentage("14.5%") == Decimal("0.145000")
    assert parse_percentage("0.145") == Decimal("0.145000")
    assert parse_percentage("14.5") == Decimal("0.145000")
    assert parse_percentage("96%") == Decimal("0.960000")


def test_header_normalization() -> None:
    assert normalize_header("7 Day Total Sales ") == "7 day total sales"
    assert normalize_header("(Child) ASIN") == "child asin"
    assert normalize_header("Buy Box %") == "buy box percent"


def test_search_term_standard_and_alias_headers() -> None:
    parser = SearchTermReportParser()
    standard = ReportAnalysisService().analyze(
        UploadedReport("search_term_valid.csv", "text/csv", fixture_bytes("search_term_valid.csv"))
    )
    assert standard.report_type == "search_term_report"
    assert standard.meta.parser_version == "search-term-parser-v1"
    assert standard.meta.analytics_version == "ppc-analytics-v1"
    assert standard.meta.valid_rows == 4
    assert standard.summary.spend == Decimal("980.00")
    assert standard.summary.sales == Decimal("740.00")
    assert standard.summary.acos is not None
    assert standard.summary.roas is not None

    alias = parser.parse(
        [
            "Day",
            "Campaign",
            "Ad Group",
            "Customer Search Terms",
            "Impressions",
            "Clicks",
            "Cost",
            "14 Day Total Sales",
            "14 Day Total Orders (#)",
        ],
        [["2026-02-01", "Demo Campaign", "Demo Group", "whey isolate", "100", "10", "INR 1,234.56", "₹2,000.00", "5"]],
    )
    row = alias.rows[0]
    assert row.customer_search_term == "whey isolate"
    assert row.spend == Decimal("1234.56")
    assert row.sales == Decimal("2000.00")
    assert row.orders == 5
    assert row.campaign_name == "Demo Campaign"


def test_search_term_optional_fields_and_partial_invalid_rows() -> None:
    result = ReportAnalysisService().analyze(
        UploadedReport(
            "search_term_partial_invalid.csv",
            "text/csv",
            fixture_bytes("search_term_partial_invalid.csv"),
        )
    )
    assert result.meta.valid_rows == 2
    assert result.meta.invalid_rows == 1
    assert result.warnings
    assert all(item.units is None for item in result.tables.search_terms)


def test_search_term_zero_valid_rows() -> None:
    payload = (
        "Campaign Name,Customer Search Term,Impressions,Clicks,Spend,Sales,Orders\n"
        "Demo,bad term,x,x,nope,nope,x\n"
    ).encode()
    from app.core.exceptions import ReportSchemaError
    import pytest

    with pytest.raises(ReportSchemaError, match="no valid data rows"):
        ReportAnalysisService().analyze(UploadedReport("empty_rows.csv", "text/csv", payload))


def test_business_parser_standard_and_aliases() -> None:
    parser = BusinessReportParser()
    csv_result = ReportAnalysisService().analyze(
        UploadedReport("business_valid.csv", "text/csv", fixture_bytes("business_valid.csv"))
    )
    assert csv_result.report_type == "business_report"
    assert csv_result.meta.parser_version == "business-report-parser-v1"
    pillow = next(item for item in csv_result.tables.products if item.asin == "B0CHILD0002")
    assert pillow.buy_box_percentage == Decimal("0.700000")
    assert pillow.conversion == Decimal("0.012500")
    assert csv_result.summary.ordered_product_sales == Decimal("5599.00")

    parsed = parser.parse(
        [
            "Child ASIN",
            "Product Title",
            "Sessions",
            "Page Views",
            "Featured Offer (Buy Box) Percentage",
            "Units",
            "Product Sales",
        ],
        [["B0ALIAS0001", "Demo Whey 1kg", "40", "55", "0.9", "8", "3200"]],
    )
    row = parsed.rows[0]
    assert row.asin == "B0ALIAS0001"
    assert row.buy_box_percentage == Decimal("0.900000")
    assert row.ordered_product_sales == Decimal("3200.00")
    assert row.units_ordered == 8


def test_business_missing_optional_buy_box() -> None:
    payload = (
        "(Child) ASIN,Title,Sessions,Units Ordered,Ordered Product Sales\n"
        "B0NOBUYBOX1,Demo Product,20,2,400\n"
    ).encode()
    result = ReportAnalysisService().analyze(UploadedReport("biz.csv", "text/csv", payload))
    assert result.report_type == "business_report"
    assert result.tables.products[0].buy_box_percentage is None
    assert result.summary.buy_box_percentage is None

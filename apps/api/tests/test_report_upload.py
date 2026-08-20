from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.exceptions import ReportAmbiguousTypeError, ReportUnknownTypeError
from app.reports.detection import ReportDetectionService
from app.reports.file_loader import load_tabular_file
from tests.report_helpers import fixture_bytes, make_xlsx, make_xlsx_with_stale_dimension

import pytest


def test_detects_search_term_and_business_reports() -> None:
    detector = ReportDetectionService()
    search = load_tabular_file("search_term_valid.csv", fixture_bytes("search_term_valid.csv"))
    business = load_tabular_file("business_valid.csv", fixture_bytes("business_valid.csv"))
    assert detector.detect(search.headers, "search_term_valid.csv").value == "search_term_report"
    assert detector.detect(business.headers, "business_valid.csv").value == "business_report"


def test_unknown_and_filename_alone_is_not_enough() -> None:
    detector = ReportDetectionService()
    with pytest.raises(ReportUnknownTypeError):
        detector.detect(["Name", "Color", "Count"], "Sponsored-Products-Search-Term-Report.csv")


def test_ambiguous_headers() -> None:
    headers = [
        "Customer Search Term",
        "Match Type",
        "Campaign Name",
        "Spend",
        "Impressions",
        "(Child) ASIN",
        "Sessions",
        "Page Views",
        "Buy Box Percentage",
        "Units Ordered",
    ]
    with pytest.raises(ReportAmbiguousTypeError):
        ReportDetectionService().detect(headers, "mixed.csv")


def test_csv_and_xlsx_upload(client: TestClient) -> None:
    csv_response = client.post(
        "/api/v1/reports/analyze",
        files={"file": ("search_term_valid.csv", fixture_bytes("search_term_valid.csv"), "text/csv")},
    )
    assert csv_response.status_code == 200
    payload = csv_response.json()
    assert payload["report_type"] == "search_term_report"
    assert payload["meta"]["parser_version"] == "search-term-parser-v1"
    assert payload["meta"]["analytics_version"] == "ppc-analytics-v1"
    assert payload["tables"]["wasted_spend"]
    assert payload["tables"]["campaigns"]

    xlsx = make_xlsx(
        [
            "(Child) ASIN",
            "Title",
            "Sessions",
            "Page Views",
            "Buy Box Percentage",
            "Units Ordered",
            "Ordered Product Sales",
            "Unit Session Percentage",
        ],
        [["B0CHILD0001", "AuroraGlow Vitamin D3 Softgels", 120, 180, "96%", 12, 4800, "10%"]],
    )
    xlsx_response = client.post(
        "/api/v1/reports/analyze",
        files={
            "file": (
                "business_valid.xlsx",
                xlsx,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert xlsx_response.status_code == 200
    body = xlsx_response.json()
    assert body["report_type"] == "business_report"
    assert body["meta"]["parser_version"] == "business-report-parser-v1"
    assert body["meta"]["analytics_version"] == "business-analytics-v1"
    assert body["summary"]["sessions"] == 120


def test_unsupported_and_empty_file(client: TestClient) -> None:
    unsupported = client.post(
        "/api/v1/reports/analyze",
        files={"file": ("notes.pdf", b"%PDF-1.4", "application/pdf")},
    )
    assert unsupported.status_code == 400
    assert "csv" in unsupported.json()["detail"].casefold()

    empty = client.post(
        "/api/v1/reports/analyze",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    assert empty.status_code == 400
    assert "empty" in empty.json()["detail"].casefold()


def test_file_too_large(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.report_analysis_service.get_settings",
        lambda: get_settings().model_copy(update={"report_max_upload_bytes": 8}),
    )
    large = client.post(
        "/api/v1/reports/analyze",
        files={"file": ("big.csv", b"abcdefghij", "text/csv")},
    )
    assert large.status_code == 400
    assert "25 mb" in large.json()["detail"].casefold() or "larger" in large.json()["detail"].casefold()


def test_corrupt_xlsx(client: TestClient) -> None:
    corrupt = client.post(
        "/api/v1/reports/analyze",
        files={
            "file": (
                "broken.xlsx",
                b"this is not a spreadsheet",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert corrupt.status_code == 400
    assert "corrupted" in corrupt.json()["detail"].casefold() or "could not be read" in corrupt.json()["detail"].casefold()


def test_missing_columns_are_400(client: TestClient) -> None:
    response = client.post(
        "/api/v1/reports/analyze",
        files={"file": ("business_missing_columns.csv", fixture_bytes("business_missing_columns.csv"), "text/csv")},
    )
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "Business Report" in detail
    assert "ASIN" in detail or "(Child) ASIN" in detail


def test_unknown_report_is_400(client: TestClient) -> None:
    response = client.post(
        "/api/v1/reports/analyze",
        files={"file": ("unknown.csv", fixture_bytes("unknown.csv"), "text/csv")},
    )
    assert response.status_code == 400
    assert "header" in response.json()["detail"].casefold() or "supported" in response.json()["detail"].casefold()


def test_xlsx_with_stale_dimension_is_still_read() -> None:
    payload = make_xlsx_with_stale_dimension(
        [
            "Start Date",
            "Campaign Name",
            "Match Type",
            "Customer Search Term",
            "Impressions",
            "Clicks",
            "Spend",
            "7 Day Total Sales",
            "7 Day Total Orders (#)",
        ],
        [["2025-11-02", "Demo Campaign", "EXACT", "pulse oximeter", 12, 1, 2.31, 0, 0]],
    )
    table = load_tabular_file("Sponsored_Products_Search_term_report.xlsx", payload)
    assert "Customer Search Term" in table.headers
    assert table.rows
    assert table.rows[0][3] == "pulse oximeter"


def test_password_protected_xlsx(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_args: object, **_kwargs: object) -> None:
        raise Exception("password protected workbook")

    monkeypatch.setattr("app.reports.file_loader.load_workbook", boom)
    from app.core.exceptions import ReportParseError
    from app.reports.file_loader import load_tabular_file

    with pytest.raises(ReportParseError, match="Password-protected"):
        load_tabular_file("secret.xlsx", b"unused")

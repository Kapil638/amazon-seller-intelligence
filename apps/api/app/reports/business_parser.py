from __future__ import annotations

from app.core.exceptions import ReportSchemaError
from app.models.reports import BusinessPerformanceRow
from app.reports.base import ParseResult, ReportType
from app.reports.columns import (
    BUSINESS_FIELDS,
    BUSINESS_REQUIRED,
    display_names,
    map_headers,
)
from app.reports.values import parse_int, parse_money, parse_percentage, parse_text

BUSINESS_PARSER_VERSION = "business-report-parser-v1"


class BusinessReportParser:
    report_type = ReportType.BUSINESS_REPORT
    version = BUSINESS_PARSER_VERSION

    def parse(self, headers: list[str], rows: list[list[str]]) -> ParseResult:
        mapped = map_headers(headers, BUSINESS_FIELDS)
        missing = [field for field in BUSINESS_REQUIRED if field not in mapped]
        if missing:
            names = display_names(missing, BUSINESS_FIELDS)
            listed = "\n".join(f"- {name}" for name in names)
            raise ReportSchemaError(
                "This file resembles a Business Report but is missing required columns:\n"
                f"{listed}"
            )
        if "units_ordered" not in mapped and "ordered_product_sales" not in mapped:
            raise ReportSchemaError(
                "This file resembles a Business Report but is missing required columns:\n"
                "- Units Ordered\n"
                "- Ordered Product Sales"
            )

        index = {internal: headers.index(original) for internal, original in mapped.items()}
        parsed: list[BusinessPerformanceRow] = []
        warnings: list[str] = []
        invalid = 0

        for row_number, row in enumerate(rows, start=2):
            try:
                item = _parse_row(row, index)
            except ValueError as exc:
                invalid += 1
                warnings.append(f"Row {row_number} skipped: {exc}")
                continue
            parsed.append(item)

        if not parsed:
            raise ReportSchemaError(
                "This Business Report has no valid data rows. "
                "Check that ASIN, Sessions, and sales/units columns are populated."
            )

        return ParseResult(
            report_type=ReportType.BUSINESS_REPORT,
            parser_version=self.version,
            rows=parsed,
            valid_rows=len(parsed),
            invalid_rows=invalid,
            warnings=warnings,
            mapped_columns=mapped,
        )


def _parse_row(row: list[str], index: dict[str, int]) -> BusinessPerformanceRow:
    asin = parse_text(_at(row, index, "asin"))
    if not asin:
        raise ValueError("ASIN is missing")

    sessions = parse_int(_at(row, index, "sessions"))
    if sessions is None:
        raise ValueError("Sessions is not a number")
    if sessions < 0:
        raise ValueError("Sessions cannot be negative")

    units = parse_int(_at(row, index, "units_ordered")) if "units_ordered" in index else None
    sales = parse_money(_at(row, index, "ordered_product_sales")) if "ordered_product_sales" in index else None
    if "units_ordered" in index and units is None and _at(row, index, "units_ordered").strip():
        raise ValueError("Units Ordered is not a number")
    if "ordered_product_sales" in index and sales is None and _at(row, index, "ordered_product_sales").strip():
        raise ValueError("Ordered Product Sales is not a number")

    page_views = parse_int(_at(row, index, "page_views")) if "page_views" in index else None
    buy_box = parse_percentage(_at(row, index, "buy_box_percentage")) if "buy_box_percentage" in index else None
    conversion = (
        parse_percentage(_at(row, index, "unit_session_percentage"))
        if "unit_session_percentage" in index
        else None
    )

    return BusinessPerformanceRow(
        date=parse_text(_at(row, index, "date")),
        asin=asin.upper(),
        parent_asin=parse_text(_at(row, index, "parent_asin")),
        sku=parse_text(_at(row, index, "sku")),
        title=parse_text(_at(row, index, "title")),
        sessions=sessions,
        page_views=page_views if page_views is not None and page_views >= 0 else None,
        buy_box_percentage=buy_box,
        units_ordered=units if units is not None and units >= 0 else None,
        ordered_product_sales=sales,
        unit_session_percentage=conversion,
    )


def _at(row: list[str], index: dict[str, int], field: str) -> str:
    position = index.get(field)
    if position is None or position >= len(row):
        return ""
    return row[position]

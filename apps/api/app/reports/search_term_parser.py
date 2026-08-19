from __future__ import annotations

from app.core.exceptions import ReportSchemaError
from app.models.reports import SearchTermPerformanceRow
from app.reports.base import ParseResult, ReportType
from app.reports.columns import (
    SEARCH_TERM_FIELDS,
    SEARCH_TERM_REQUIRED,
    display_names,
    map_headers,
)
from app.reports.values import parse_int, parse_money, parse_text

SEARCH_TERM_PARSER_VERSION = "search-term-parser-v1"


class SearchTermReportParser:
    report_type = ReportType.SEARCH_TERM_REPORT
    version = SEARCH_TERM_PARSER_VERSION

    def parse(self, headers: list[str], rows: list[list[str]]) -> ParseResult:
        mapped = map_headers(headers, SEARCH_TERM_FIELDS)
        missing = [field for field in SEARCH_TERM_REQUIRED if field not in mapped]
        if missing:
            names = display_names(missing, SEARCH_TERM_FIELDS)
            listed = "\n".join(f"- {name}" for name in names)
            raise ReportSchemaError(
                "This file resembles a Search Term Report but is missing required columns:\n"
                f"{listed}"
            )

        index = {internal: headers.index(original) for internal, original in mapped.items()}
        parsed: list[SearchTermPerformanceRow] = []
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
                "This Search Term Report has no valid data rows. "
                "Check that Spend, Sales, and order columns contain numbers."
            )

        return ParseResult(
            report_type=ReportType.SEARCH_TERM_REPORT,
            parser_version=self.version,
            rows=parsed,
            valid_rows=len(parsed),
            invalid_rows=invalid,
            warnings=warnings,
            mapped_columns=mapped,
        )


def _parse_row(row: list[str], index: dict[str, int]) -> SearchTermPerformanceRow:
    term = parse_text(_at(row, index, "customer_search_term"))
    if not term:
        raise ValueError("Customer Search Term is missing")

    impressions = parse_int(_at(row, index, "impressions"))
    clicks = parse_int(_at(row, index, "clicks"))
    spend = parse_money(_at(row, index, "spend"))
    sales = parse_money(_at(row, index, "sales"))
    orders = parse_int(_at(row, index, "orders"))
    if impressions is None:
        raise ValueError("Impressions is not a number")
    if clicks is None:
        raise ValueError("Clicks is not a number")
    if spend is None:
        raise ValueError("Spend is not a number")
    if sales is None:
        raise ValueError("Sales is not a number")
    if orders is None:
        raise ValueError("Orders is not a number")
    if impressions < 0 or clicks < 0 or orders < 0:
        raise ValueError("numeric fields cannot be negative")

    units = parse_int(_at(row, index, "units")) if "units" in index else None
    return SearchTermPerformanceRow(
        date=parse_text(_at(row, index, "date")),
        campaign_name=parse_text(_at(row, index, "campaign_name")),
        campaign_id=parse_text(_at(row, index, "campaign_id")),
        ad_group_name=parse_text(_at(row, index, "ad_group_name")),
        ad_group_id=parse_text(_at(row, index, "ad_group_id")),
        targeting=parse_text(_at(row, index, "targeting")),
        match_type=parse_text(_at(row, index, "match_type")),
        customer_search_term=term,
        impressions=impressions,
        clicks=clicks,
        spend=spend,
        sales=sales,
        orders=orders,
        units=units if units is not None and units >= 0 else None,
        currency=parse_text(_at(row, index, "currency")),
    )


def _at(row: list[str], index: dict[str, int], field: str) -> str:
    position = index.get(field)
    if position is None or position >= len(row):
        return ""
    return row[position]

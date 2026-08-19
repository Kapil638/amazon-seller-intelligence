from __future__ import annotations

from app.core.exceptions import ReportAmbiguousTypeError, ReportUnknownTypeError
from app.reports.base import ReportType
from app.reports.columns import (
    BUSINESS_DISTINCTIVE,
    BUSINESS_FIELDS,
    SEARCH_TERM_DISTINCTIVE,
    SEARCH_TERM_FIELDS,
    map_headers,
)

DETECTION_THRESHOLD = 3


class ReportDetectionService:
    """Detect Amazon report type from headers. Filename is a weak hint only."""

    def detect(self, headers: list[str], filename: str = "") -> ReportType:
        search_mapped = map_headers(headers, SEARCH_TERM_FIELDS)
        business_mapped = map_headers(headers, BUSINESS_FIELDS)
        search_score = sum(1 for field in SEARCH_TERM_DISTINCTIVE if field in search_mapped)
        business_score = sum(1 for field in BUSINESS_DISTINCTIVE if field in business_mapped)

        search_hit = search_score >= DETECTION_THRESHOLD
        business_hit = business_score >= DETECTION_THRESHOLD

        if search_hit and business_hit:
            raise ReportAmbiguousTypeError(
                "This file matches both a Sponsored Products Search Term Report "
                "and a Business Report. Export one report type at a time."
            )
        if search_hit:
            return ReportType.SEARCH_TERM_REPORT
        if business_hit:
            return ReportType.BUSINESS_REPORT

        hinted = _filename_hint(filename)
        if hinted is ReportType.SEARCH_TERM_REPORT and search_score >= 2:
            return ReportType.SEARCH_TERM_REPORT
        if hinted is ReportType.BUSINESS_REPORT and business_score >= 2:
            return ReportType.BUSINESS_REPORT

        if not headers or all(not header.strip() for header in headers):
            raise ReportUnknownTypeError(
                "This file has no recognizable Amazon report headers."
            )
        raise ReportUnknownTypeError(
            "This file is not a supported Amazon Seller Central report. "
            "Upload a Sponsored Products Search Term Report or a Business Report."
        )


def _filename_hint(filename: str) -> ReportType | None:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].casefold()
    search_hints = ("search-term", "search_term", "search term", "sponsored")
    business_hints = ("business", "sales-and-traffic", "sales_and_traffic", "detail-page")
    search = any(token in name for token in search_hints)
    business = any(token in name for token in business_hints)
    if search and not business:
        return ReportType.SEARCH_TERM_REPORT
    if business and not search:
        return ReportType.BUSINESS_REPORT
    return None

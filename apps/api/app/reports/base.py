from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol


class ReportType(StrEnum):
    SEARCH_TERM_REPORT = "search_term_report"
    BUSINESS_REPORT = "business_report"
    UNKNOWN = "unknown"


@dataclass
class ParseResult:
    report_type: ReportType
    parser_version: str
    rows: list[Any]
    valid_rows: int
    invalid_rows: int
    warnings: list[str] = field(default_factory=list)
    mapped_columns: dict[str, str] = field(default_factory=dict)


class ReportParser(Protocol):
    report_type: ReportType
    version: str

    def parse(self, headers: list[str], rows: list[list[str]]) -> ParseResult: ...

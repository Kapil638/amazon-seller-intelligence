"""Orchestrate upload → detect → parse → analytics. No AI. No persistence."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from app.analytics.business_report_rules import BUSINESS_ANALYTICS_VERSION
from app.analytics.ppc_rules import PPC_ANALYTICS_VERSION
from app.core.config import get_settings
from app.core.exceptions import ReportUploadError
from app.models.reports import (
    BusinessReportAnalysis,
    ReportAnalysisMeta,
    ReportAnalysisResponse,
    SearchTermReportAnalysis,
)
from app.reports.base import ReportType
from app.reports.business_parser import BusinessReportParser
from app.reports.detection import ReportDetectionService
from app.reports.file_loader import SUPPORTED_EXTENSIONS, TabularFile, load_tabular_file
from app.reports.search_term_parser import SearchTermReportParser
from app.services.business_analytics_service import BusinessAnalyticsService
from app.services.ppc_analytics_service import PPCAnalyticsService

logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPES = {
    "text/csv",
    "application/csv",
    "text/plain",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream",
    "",
}


@dataclass(frozen=True)
class UploadedReport:
    filename: str
    content_type: str | None
    data: bytes


class ReportAnalysisService:
    def __init__(
        self,
        detection: ReportDetectionService | None = None,
        ppc: PPCAnalyticsService | None = None,
        business: BusinessAnalyticsService | None = None,
    ) -> None:
        self._detection = detection or ReportDetectionService()
        self._search_parser = SearchTermReportParser()
        self._business_parser = BusinessReportParser()
        self._ppc = ppc or PPCAnalyticsService()
        self._business = business or BusinessAnalyticsService()

    def analyze(self, upload: UploadedReport) -> ReportAnalysisResponse:
        started = time.perf_counter()
        self._validate_upload(upload)
        table = load_tabular_file(upload.filename, upload.data)
        report_type = self._detection.detect(table.headers, upload.filename)
        try:
            if report_type is ReportType.SEARCH_TERM_REPORT:
                result = self._analyze_search_term(upload, table)
            else:
                result = self._analyze_business(upload, table)
        except Exception:
            logger.info(
                "report_analyze failure type=%s size=%s format=%s",
                report_type.value,
                len(upload.data),
                table.source_format,
            )
            raise

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        logger.info(
            "report_analyze success type=%s size=%s rows=%s invalid=%s parser=%s latency_ms=%s",
            result.report_type,
            result.meta.file_size_bytes,
            result.meta.valid_rows,
            result.meta.invalid_rows,
            result.meta.parser_version,
            elapsed_ms,
        )
        return result

    def _analyze_search_term(
        self, upload: UploadedReport, table: TabularFile
    ) -> SearchTermReportAnalysis:
        parsed = self._search_parser.parse(table.headers, table.rows)
        summary, tables, findings = self._ppc.analyze(parsed.rows)
        return SearchTermReportAnalysis(
            summary=summary,
            findings=findings,
            tables=tables,
            warnings=parsed.warnings,
            meta=self._meta(
                upload,
                table,
                parsed.parser_version,
                PPC_ANALYTICS_VERSION,
                parsed.valid_rows,
                parsed.invalid_rows,
            ),
        )

    def _analyze_business(
        self, upload: UploadedReport, table: TabularFile
    ) -> BusinessReportAnalysis:
        parsed = self._business_parser.parse(table.headers, table.rows)
        summary, tables, findings = self._business.analyze(parsed.rows)
        return BusinessReportAnalysis(
            summary=summary,
            findings=findings,
            tables=tables,
            warnings=parsed.warnings,
            meta=self._meta(
                upload,
                table,
                parsed.parser_version,
                BUSINESS_ANALYTICS_VERSION,
                parsed.valid_rows,
                parsed.invalid_rows,
            ),
        )

    def _validate_upload(self, upload: UploadedReport) -> None:
        settings = get_settings()
        filename = upload.filename or ""
        extension = _extension(filename)
        if extension not in SUPPORTED_EXTENSIONS:
            raise ReportUploadError("Upload a .csv or .xlsx file.")
        content_type = (upload.content_type or "").split(";")[0].strip().casefold()
        if content_type and content_type not in ALLOWED_CONTENT_TYPES:
            raise ReportUploadError("Upload a .csv or .xlsx file.")
        if not upload.data:
            raise ReportUploadError("This file is empty.")
        if len(upload.data) > settings.report_max_upload_bytes:
            raise ReportUploadError(
                "This file is larger than the 25 MB upload limit."
            )

    def _meta(
        self,
        upload: UploadedReport,
        table: TabularFile,
        parser_version: str,
        analytics_version: str,
        valid_rows: int,
        invalid_rows: int,
    ) -> ReportAnalysisMeta:
        return ReportAnalysisMeta(
            parser_version=parser_version,
            analytics_version=analytics_version,
            filename=None,
            file_size_bytes=len(upload.data),
            source_format=table.source_format,
            valid_rows=valid_rows,
            invalid_rows=invalid_rows,
            currency="INR",
        )


def _extension(filename: str) -> str:
    name = filename.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip().casefold()
    if "." not in name:
        return ""
    return "." + name.rsplit(".", 1)[-1]

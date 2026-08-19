from app.analytics.business_report_rules import analyze_business_report
from app.models.reports import (
    BusinessPerformanceRow,
    BusinessSummary,
    BusinessTables,
    ReportFinding,
)


class BusinessAnalyticsService:
    """Deterministic Business Report metrics from normalized rows."""

    def analyze(
        self, rows: list[BusinessPerformanceRow]
    ) -> tuple[BusinessSummary, BusinessTables, list[ReportFinding]]:
        return analyze_business_report(rows)

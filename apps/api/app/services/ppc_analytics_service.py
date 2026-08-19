from app.analytics.ppc_rules import analyze_search_terms
from app.models.reports import ReportFinding, SearchTermPerformanceRow, SearchTermTables, PpcMetrics


class PPCAnalyticsService:
    """Deterministic PPC metrics from normalized search-term rows."""

    def analyze(
        self, rows: list[SearchTermPerformanceRow]
    ) -> tuple[PpcMetrics, SearchTermTables, list[ReportFinding]]:
        return analyze_search_terms(rows)

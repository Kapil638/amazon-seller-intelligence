"""History tools wrap AnalysisHistoryService. They do not recalculate scores."""

from __future__ import annotations

from app.copilot.budget import COST_NONE
from app.copilot.evidence import EvidenceEnvelope, claim, envelope
from app.copilot.registry import ToolDefinition, ToolRegistry
from app.copilot.schemas import GetSavedReportInput, ListSavedReportsInput
from app.services.analysis_history_service import AnalysisHistoryService


def register(registry: ToolRegistry, history: AnalysisHistoryService | None = None) -> None:
    service = history or AnalysisHistoryService()
    registry.register(
        ToolDefinition(
            name="get_saved_report",
            description="Retrieve a saved listing analysis without calling Amazon or OpenAI.",
            input_schema=GetSavedReportInput,
            handler=lambda payload: _get_saved_report(service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="list_saved_reports",
            description="List saved listing analyses, optionally filtered by ASIN.",
            input_schema=ListSavedReportsInput,
            handler=lambda payload: _list_saved_reports(service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )


def _get_saved_report(history: AnalysisHistoryService, payload: GetSavedReportInput) -> EvidenceEnvelope:
    detail = history.get_report(payload.report_id)
    findings = [
        {
            "code": item.code,
            "category": item.category,
            "severity": item.severity.value,
            "message": item.message,
        }
        for item in detail.analysis.findings
    ]
    source = "snapshot"
    as_of = detail.meta.analyzed_at
    return envelope(
        "get_saved_report",
        [
            claim("report_id", str(detail.report_id), kind="historical", source=source, as_of=as_of),
            claim("asin", detail.product.asin, kind="historical", source=source, as_of=as_of),
            claim("marketplace", detail.product.marketplace, kind="historical", source=source, as_of=as_of),
            claim(
                "analysis_timestamp",
                as_of.isoformat() if as_of else None,
                kind="historical",
                source=source,
                as_of=as_of,
            ),
            claim("display_name", detail.display_name, kind="historical", source=source, as_of=as_of),
            claim("status", detail.meta.status, kind="historical", source=source, as_of=as_of),
            claim(
                "listing_quality_score",
                detail.analysis.listing_quality_score,
                kind="historical",
                source=source,
                as_of=as_of,
                notes="Score from the saved analysis. Not recalculated.",
            ),
            claim("findings", findings, kind="historical", source=source, as_of=as_of),
        ],
    )


def _list_saved_reports(history: AnalysisHistoryService, payload: ListSavedReportsInput) -> EvidenceEnvelope:
    page = history.list_reports(asin=payload.asin, limit=payload.limit)
    reports = [
        {
            "report_id": str(item.report_id),
            "asin": item.asin,
            "listing_quality_score": item.listing_quality_score,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "status": item.status,
            "display_name": item.display_name,
        }
        for item in page.items
    ]
    return envelope(
        "list_saved_reports",
        [
            claim("total", page.total, kind="historical", source="snapshot"),
            claim("asin_filter", payload.asin, kind="historical", source="snapshot"),
            claim("reports", reports, kind="historical", source="snapshot"),
        ],
    )

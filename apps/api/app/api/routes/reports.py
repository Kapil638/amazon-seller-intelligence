from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response

from app.core.exceptions import (
    ArtifactStorageError,
    PersistenceNotConfiguredError,
    PdfGenerationError,
    PdfNotGeneratedError,
    ReportAmbiguousTypeError,
    ReportNotFoundError,
    ReportParseError,
    ReportSchemaError,
    ReportUnknownTypeError,
    ReportUploadError,
)
from app.models.reports import ReportAnalysisResponse
from app.models.saved_analysis import (
    ClientPdfGenerateResponse,
    SavedAnalysisDeleteResponse,
    SavedAnalysisDetail,
    SavedAnalysisListResponse,
)
from app.services.analysis_history_service import AnalysisHistoryService, get_analysis_history_service
from app.services.artifact_persistence_service import get_artifact_service
from app.services.client_report_service import ClientReportService, get_client_report_service
from app.services.report_analysis_service import ReportAnalysisService, UploadedReport

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


def get_report_analysis_service() -> ReportAnalysisService:
    return ReportAnalysisService()


@router.post("/analyze")
async def analyze_report(
    file: UploadFile = File(...),
    service: ReportAnalysisService = Depends(get_report_analysis_service),
) -> ReportAnalysisResponse:
    data = await file.read()
    try:
        result = service.analyze(
            UploadedReport(
                filename=file.filename or "",
                content_type=file.content_type,
                data=data,
            )
        )
    except (
        ReportUploadError,
        ReportUnknownTypeError,
        ReportAmbiguousTypeError,
        ReportSchemaError,
        ReportParseError,
    ) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save = get_artifact_service().save_seller_report_upload(
        filename=file.filename or "upload",
        data=data,
        report_type=result.report_type,
        parser_version=result.meta.parser_version,
        row_count=result.meta.valid_rows,
        analysis_payload=result.model_dump(mode="json"),
    )
    if save.get("duplicate"):
        result.warnings.append(
            "This file matches a previous upload (same SHA-256). The new copy was stored for audit."
        )
    if not save.get("persisted"):
        result.warnings.append("Analysis succeeded but the original file could not be saved to history.")
    return result


@router.get("", response_model=SavedAnalysisListResponse)
@router.get("/", response_model=SavedAnalysisListResponse, include_in_schema=False)
def list_saved_analyses(
    asin: str | None = Query(default=None),
    marketplace: str | None = Query(default=None),
    status: str | None = Query(default=None),
    created_from: datetime | None = Query(default=None),
    created_to: datetime | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
    history: AnalysisHistoryService = Depends(get_analysis_history_service),
) -> SavedAnalysisListResponse:
    try:
        return history.list_reports(
            asin=asin,
            marketplace=marketplace,
            status=status,
            created_from=created_from,
            created_to=created_to,
            offset=offset,
            limit=limit,
        )
    except PersistenceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/{report_id}", response_model=SavedAnalysisDetail)
def get_saved_analysis(
    report_id: UUID,
    history: AnalysisHistoryService = Depends(get_analysis_history_service),
) -> SavedAnalysisDetail:
    try:
        return history.get_report(report_id)
    except PersistenceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.delete("/{report_id}", response_model=SavedAnalysisDeleteResponse)
def delete_saved_analysis(
    report_id: UUID,
    history: AnalysisHistoryService = Depends(get_analysis_history_service),
) -> SavedAnalysisDeleteResponse:
    try:
        return history.soft_delete(report_id)
    except PersistenceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{report_id}/pdf", response_model=ClientPdfGenerateResponse)
def generate_saved_analysis_pdf(
    report_id: UUID,
    service: ClientReportService = Depends(get_client_report_service),
) -> ClientPdfGenerateResponse:
    try:
        return service.generate_pdf(report_id)
    except PersistenceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except ReportNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ArtifactStorageError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except PdfGenerationError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/{report_id}/pdf")
def download_saved_analysis_pdf(
    report_id: UUID,
    service: ClientReportService = Depends(get_client_report_service),
) -> Response:
    try:
        payload, filename = service.download_pdf(report_id)
    except PersistenceNotConfiguredError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except (ReportNotFoundError, PdfNotGeneratedError) as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    safe_name = "".join(ch if ch.isalnum() or ch in "-._" else "-" for ch in filename) or "analysis.pdf"
    return Response(
        content=payload,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}"'},
    )

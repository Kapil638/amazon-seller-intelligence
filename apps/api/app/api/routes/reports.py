from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.core.exceptions import (
    ReportAmbiguousTypeError,
    ReportParseError,
    ReportSchemaError,
    ReportUnknownTypeError,
    ReportUploadError,
)
from app.models.reports import ReportAnalysisResponse
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
        return service.analyze(
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

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

from app.bulk.excel import build_bulk_workbook
from app.bulk.runtime import get_bulk_job_service
from app.core.config import get_settings
from app.core.exceptions import BulkIngestError, BulkLimitExceededError, BulkLiveProviderForbiddenError
from app.models.bulk import BulkJobOptions, BulkJobResponse

router = APIRouter(prefix="/api/v1/bulk", tags=["bulk"])


def _read_limits(file: UploadFile, data: bytes) -> None:
    settings = get_settings()
    if len(data) > settings.report_max_upload_bytes:
        raise HTTPException(status_code=400, detail="This file is larger than the upload limit.")
    if not (file.filename or "").strip():
        raise HTTPException(status_code=400, detail="Upload a .csv or .xlsx file.")


@router.post("/preview")
async def preview_bulk_file(file: UploadFile = File(...)) -> dict:
    data = await file.read()
    _read_limits(file, data)
    try:
        stats, unique, failures = get_bulk_job_service().preview(file.filename or "upload.csv", data)
    except (BulkIngestError, BulkLimitExceededError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    payload = stats.model_dump()
    payload["unique_asin_list"] = unique
    payload["invalid_samples"] = [item.model_dump() for item in failures[:20]]
    return payload


@router.post("/jobs", response_model=BulkJobResponse)
async def create_bulk_job(
    file: UploadFile = File(...),
    analysis_mode: str = Form("standard"),
    ai_selection: str = Form("high_priority"),
    top_n: int = Form(10),
    marketplace: str = Form("amazon.in"),
) -> BulkJobResponse:
    data = await file.read()
    _read_limits(file, data)
    if analysis_mode not in {"standard", "deep_ai"}:
        raise HTTPException(status_code=400, detail="analysis_mode must be standard or deep_ai.")
    if ai_selection not in {"high_priority", "top_n", "all"}:
        raise HTTPException(status_code=400, detail="ai_selection must be high_priority, top_n, or all.")
    options = BulkJobOptions(
        analysis_mode=analysis_mode,  # type: ignore[arg-type]
        ai_selection=ai_selection,  # type: ignore[arg-type]
        top_n=top_n,
        marketplace=marketplace,
    )
    try:
        return get_bulk_job_service().create_job(file.filename or "upload.csv", data, options)
    except (BulkIngestError, BulkLimitExceededError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BulkLiveProviderForbiddenError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.get("/jobs/{job_id}", response_model=BulkJobResponse)
async def get_bulk_job(job_id: str) -> BulkJobResponse:
    job = get_bulk_job_service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk job was not found.")
    return job


@router.get("/jobs/{job_id}/report.xlsx")
async def download_bulk_report(job_id: str) -> Response:
    job = get_bulk_job_service().get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Bulk job was not found.")
    if job.status not in {"completed", "completed_with_errors"}:
        raise HTTPException(status_code=409, detail="The Excel report is available after the job completes.")
    payload = build_bulk_workbook(job)
    filename = f"bulk-due-diligence-{job.job_id[:8]}.xlsx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from app.core.config import get_settings
from app.core.exceptions import ArtifactStorageError, PersistenceNotConfiguredError
from app.models.bulk import BulkJobResponse
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.hashing import sha256_bytes
from app.persistence.repositories import (
    BulkRepository,
    GeneratedReportRepository,
    ReportUploadRepository,
    UsageEventRepository,
)
from app.persistence.storage import get_file_store
from app.reports.client_analysis_report import ANALYSIS_PDF_TYPE, REPORT_TEMPLATE_VERSION

logger = logging.getLogger("app.persistence.artifacts")


class ArtifactPersistenceService:
    def save_seller_report_upload(
        self,
        *,
        filename: str,
        data: bytes,
        report_type: str,
        parser_version: str | None,
        row_count: int | None,
        analysis_payload: dict[str, Any],
    ) -> dict[str, Any]:
        if not persistence_enabled():
            return {"persisted": False, "duplicate": False}
        settings = get_settings()
        file_hash = sha256_bytes(data)
        org_id = current_organization_id()
        try:
            with session_scope() as session:
                uploads = ReportUploadRepository(session)
                existing = uploads.find_by_hash(org_id, file_hash)
                path = f"{org_id}/{uuid4().hex}/{filename}"
                get_file_store().put(
                    settings.storage_uploads_bucket,
                    path,
                    data,
                    "application/octet-stream",
                )
                row = uploads.create(
                    organization_id=org_id,
                    report_type=report_type,
                    original_filename=filename,
                    storage_bucket=settings.storage_uploads_bucket,
                    storage_path=path,
                    file_hash=file_hash,
                    parser_version=parser_version,
                    row_count=row_count,
                    status="complete",
                    duplicate_of_id=existing.id if existing else None,
                    analysis_payload=analysis_payload,
                )
                return {
                    "persisted": True,
                    "upload_id": str(row.id),
                    "duplicate": existing is not None,
                    "duplicate_of": str(existing.id) if existing else None,
                    "file_hash": file_hash,
                }
        except Exception:
            logger.exception("Failed to persist seller report upload")
            return {"persisted": False, "duplicate": False}

    def save_bulk_job(self, job: BulkJobResponse, input_bytes: bytes | None, filename: str | None) -> None:
        if not persistence_enabled():
            return
        settings = get_settings()
        org_id = current_organization_id()
        try:
            with session_scope() as session:
                upload_id = None
                if input_bytes and filename:
                    uploads = ReportUploadRepository(session)
                    file_hash = sha256_bytes(input_bytes)
                    path = f"{org_id}/bulk/{job.job_id}/{filename}"
                    get_file_store().put(
                        settings.storage_uploads_bucket,
                        path,
                        input_bytes,
                        "application/octet-stream",
                    )
                    upload = uploads.create(
                        organization_id=org_id,
                        report_type="bulk_asin_list",
                        original_filename=filename,
                        storage_bucket=settings.storage_uploads_bucket,
                        storage_path=path,
                        file_hash=file_hash,
                        status=job.status,
                    )
                    upload_id = upload.id
                bulk = BulkRepository(session)
                completed = job.updated_at if job.status in {"completed", "completed_with_errors", "failed"} else None
                row = bulk.upsert_job(
                    organization_id=org_id,
                    external_job_id=job.job_id,
                    status=job.status,
                    total_items=job.progress.total,
                    processed_items=job.progress.processed,
                    successful_items=job.progress.successful,
                    failed_items=job.progress.failed,
                    settings=job.options.model_dump(mode="json"),
                    completed_at=completed,
                    input_file_id=upload_id,
                )
                items: list[dict[str, Any]] = []
                for result in job.results:
                    items.append(
                        {
                            "asin": result.asin,
                            "status": result.status,
                            "listing_analysis": result.listing_analysis.model_dump(mode="json"),
                            "error": None,
                        }
                    )
                for failure in job.failures:
                    items.append(
                        {
                            "asin": (failure.input_asin or "UNKNOWN")[:32],
                            "status": "failed",
                            "listing_analysis": None,
                            "error": failure.reason,
                        }
                    )
                bulk.replace_items(row, items)
        except Exception:
            logger.exception("Failed to persist bulk job")

    def save_generated_excel(self, job: BulkJobResponse, payload: bytes) -> UUID | None:
        if not persistence_enabled():
            return None
        settings = get_settings()
        org_id = current_organization_id()
        filename = f"bulk-due-diligence-{job.job_id[:8]}.xlsx"
        path = f"{org_id}/generated/{job.job_id}/{filename}"
        try:
            get_file_store().put(
                settings.storage_generated_bucket,
                path,
                payload,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            with session_scope() as session:
                bulk = BulkRepository(session).get_by_external_id(org_id, job.job_id)
                row = GeneratedReportRepository(session).create(
                    organization_id=org_id,
                    bulk_job_id=bulk.id if bulk else None,
                    report_type="bulk_excel",
                    storage_bucket=settings.storage_generated_bucket,
                    storage_path=path,
                    filename=filename,
                )
                return row.id
        except Exception:
            logger.exception("Failed to persist generated Excel report")
            return None

    def load_generated_excel(self, bulk_job_id: str) -> tuple[bytes, str] | None:
        if not persistence_enabled():
            return None
        org_id = current_organization_id()
        try:
            with session_scope() as session:
                bulk = BulkRepository(session).get_by_external_id(org_id, bulk_job_id)
                if bulk is None:
                    return None
                generated = GeneratedReportRepository(session).get_for_bulk_job(org_id, bulk.id)
                if generated is None:
                    return None
                data = get_file_store().get(generated.storage_bucket, generated.storage_path)
                if data is None:
                    return None
                return data, generated.filename
        except Exception:
            logger.exception("Failed to load generated Excel report")
            return None

    def save_analysis_pdf(
        self,
        *,
        analysis_run_id: UUID,
        filename: str,
        data: bytes,
        template_version: str = REPORT_TEMPLATE_VERSION,
        report_type: str = ANALYSIS_PDF_TYPE,
    ) -> UUID:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError()
        settings = get_settings()
        org_id = current_organization_id()
        path = f"{org_id}/analysis-pdf/{analysis_run_id}/{template_version}/{filename}"
        try:
            get_file_store().put(
                settings.storage_generated_bucket,
                path,
                data,
                "application/pdf",
            )
        except Exception as exc:
            logger.exception("Failed to upload analysis PDF")
            raise ArtifactStorageError() from exc
        try:
            with session_scope() as session:
                reports = GeneratedReportRepository(session)
                existing = reports.get_analysis_pdf(
                    org_id,
                    analysis_run_id,
                    report_type=report_type,
                    template_version=template_version,
                )
                if existing is not None:
                    existing.storage_bucket = settings.storage_generated_bucket
                    existing.storage_path = path
                    existing.filename = filename
                    session.flush()
                    return existing.id
                row = reports.create(
                    organization_id=org_id,
                    analysis_run_id=analysis_run_id,
                    report_type=report_type,
                    storage_bucket=settings.storage_generated_bucket,
                    storage_path=path,
                    filename=filename,
                    template_version=template_version,
                )
                return row.id
        except ArtifactStorageError:
            raise
        except Exception as exc:
            logger.exception("Failed to persist analysis PDF metadata")
            raise ArtifactStorageError() from exc

    def load_analysis_pdf(
        self,
        analysis_run_id: UUID,
        *,
        template_version: str = REPORT_TEMPLATE_VERSION,
        report_type: str = ANALYSIS_PDF_TYPE,
    ) -> tuple[bytes, str] | None:
        if not persistence_enabled():
            return None
        org_id = current_organization_id()
        try:
            with session_scope() as session:
                generated = GeneratedReportRepository(session).get_analysis_pdf(
                    org_id,
                    analysis_run_id,
                    report_type=report_type,
                    template_version=template_version,
                )
                if generated is None:
                    return None
                data = get_file_store().get(generated.storage_bucket, generated.storage_path)
                if data is None:
                    return None
                return data, generated.filename
        except Exception:
            logger.exception("Failed to load analysis PDF")
            return None

    def record_usage_event(self, **kwargs: Any) -> None:
        if not persistence_enabled():
            return
        try:
            with session_scope() as session:
                UsageEventRepository(session).create(
                    organization_id=current_organization_id(),
                    **kwargs,
                )
        except Exception:
            logger.exception("Failed to persist usage event")


def get_artifact_service() -> ArtifactPersistenceService:
    return ArtifactPersistenceService()

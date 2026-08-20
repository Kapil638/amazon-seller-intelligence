from __future__ import annotations

import logging
from collections.abc import Callable
from uuid import UUID

from app.core.exceptions import PdfGenerationError, PdfNotGeneratedError
from app.models.product import Product
from app.models.saved_analysis import ClientPdfGenerateResponse
from app.reports.client_analysis_report import (
    REPORT_TEMPLATE_VERSION,
    cover_image_url,
)
from app.reports.cover_image import load_cover_image_bytes
from app.reports.pdf_report_renderer import PdfReportRenderer
from app.reports.view_model import build_client_report_view
from app.services.analysis_history_service import AnalysisHistoryService
from app.services.artifact_persistence_service import ArtifactPersistenceService, get_artifact_service

logger = logging.getLogger("app.reports.client")

ImageLoader = Callable[[str | None], bytes | None]


class ClientReportService:
    """Build a client PDF from a persisted historical report. Zero provider calls."""

    def __init__(
        self,
        *,
        history: AnalysisHistoryService | None = None,
        artifacts: ArtifactPersistenceService | None = None,
        renderer: PdfReportRenderer | None = None,
        image_loader: ImageLoader | None = None,
    ) -> None:
        self.history = history or AnalysisHistoryService()
        self.artifacts = artifacts or get_artifact_service()
        self.renderer = renderer or PdfReportRenderer()
        self.image_loader = image_loader or load_cover_image_bytes

    def generate_pdf(self, report_id: UUID) -> ClientPdfGenerateResponse:
        detail = self.history.get_report(report_id)
        existing = self.artifacts.load_analysis_pdf(report_id)
        if existing is not None:
            payload, filename = existing
            if payload.startswith(b"%PDF"):
                return ClientPdfGenerateResponse(
                    report_id=report_id,
                    generated=True,
                    reused=True,
                    filename=filename,
                    template_version=REPORT_TEMPLATE_VERSION,
                )
        cover = self._safe_cover_image(detail.product)
        view = build_client_report_view(detail, cover_image_bytes=cover)
        try:
            pdf_bytes = self.renderer.render(view)
        except Exception as exc:
            logger.exception("PDF renderer failed for report %s", report_id)
            raise PdfGenerationError() from exc
        if not pdf_bytes.startswith(b"%PDF"):
            raise PdfGenerationError("The PDF renderer returned an invalid document.")
        self.artifacts.save_analysis_pdf(
            analysis_run_id=report_id,
            filename=view.filename,
            data=pdf_bytes,
            template_version=REPORT_TEMPLATE_VERSION,
        )
        return ClientPdfGenerateResponse(
            report_id=report_id,
            generated=True,
            reused=False,
            filename=view.filename,
            template_version=REPORT_TEMPLATE_VERSION,
        )

    def download_pdf(self, report_id: UUID) -> tuple[bytes, str]:
        self.history.get_report(report_id)
        loaded = self.artifacts.load_analysis_pdf(report_id)
        if loaded is None:
            raise PdfNotGeneratedError(str(report_id))
        return loaded

    def _safe_cover_image(self, product: Product) -> bytes | None:
        url = cover_image_url(product)
        try:
            return self.image_loader(url)
        except Exception:
            logger.info("Cover image loader failed; continuing without an image.")
            return None


def get_client_report_service() -> ClientReportService:
    return ClientReportService()

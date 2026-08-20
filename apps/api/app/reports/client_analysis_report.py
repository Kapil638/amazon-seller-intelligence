from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from app.models.ai_image_intelligence import AIImageIntelligence
from app.models.ai_listing_intelligence_v2 import AIListingIntelligenceV2
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.product import Product
from app.models.saved_analysis import SavedAnalysisDetail, SavedAnalysisMetadata
from app.models.scoring_profile import CustomScoreResult

REPORT_TEMPLATE_VERSION = "analysis-report-v2"
LEGACY_TEMPLATE_VERSION = "analysis-report-v1"
ANALYSIS_PDF_TYPE = "analysis_pdf"


@dataclass
class ClientAnalysisReport:
    """Presentation view of a persisted historical analysis. No live data."""

    report_id: UUID
    template_version: str
    filename: str
    product: Product
    analysis: ListingAnalysisV2
    custom_score: CustomScoreResult | None
    ai_intelligence: AIListingIntelligenceV2 | None
    image_intelligence: AIImageIntelligence | None
    meta: SavedAnalysisMetadata
    analyzed_label: str
    fetched_label: str
    source_label: str
    cover_image_bytes: bytes | None = None


def analysis_pdf_filename(asin: str, analyzed_at: datetime | None) -> str:
    day = (analyzed_at or datetime.now(UTC)).strftime("%Y-%m-%d")
    safe = "".join(ch for ch in (asin or "").upper() if ch.isalnum())[:10] or "ASIN"
    return f"Amazon-Listing-Analysis-{safe}-{day}.pdf"


def cover_image_url(product: Product) -> str | None:
    if not product.images:
        return None
    main = next((item for item in product.images if item.is_main), None)
    chosen = main or product.images[0]
    return chosen.url or None


def source_display(source: str | None) -> str:
    if source == "rainforest":
        return "Rainforest"
    if source == "mock":
        return "Mock catalog"
    if source == "manual":
        return "Manual"
    if source == "amazon_public":
        return "Amazon.in public"
    return source or "Not available"


def format_dt(value: datetime | None) -> str:
    if value is None:
        return "Not available"
    if value.tzinfo is not None:
        value = value.astimezone().replace(tzinfo=None)
    return value.strftime("%d %b %Y, %H:%M")


def from_saved_detail(
    detail: SavedAnalysisDetail,
    *,
    cover_image_bytes: bytes | None = None,
) -> ClientAnalysisReport:
    return ClientAnalysisReport(
        report_id=detail.report_id,
        template_version=REPORT_TEMPLATE_VERSION,
        filename=analysis_pdf_filename(detail.product.asin, detail.meta.analyzed_at),
        product=detail.product,
        analysis=detail.analysis,
        custom_score=detail.custom_score,
        ai_intelligence=detail.ai_intelligence,
        image_intelligence=detail.image_intelligence,
        meta=detail.meta,
        analyzed_label=format_dt(detail.meta.analyzed_at),
        fetched_label=format_dt(detail.meta.product_fetched_at),
        source_label=source_display(
            detail.meta.product_source.value
            if hasattr(detail.meta.product_source, "value")
            else (str(detail.meta.product_source) if detail.meta.product_source else None)
        ),
        cover_image_bytes=cover_image_bytes,
    )

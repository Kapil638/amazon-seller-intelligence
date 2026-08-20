from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.ai_image_intelligence import AIImageIntelligence
from app.models.ai_listing_intelligence_v2 import AIListingIntelligenceV2
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.product import Product, ProductSource
from app.models.scoring_profile import CustomScoreResult


class PersistMeta(BaseModel):
    report_id: UUID | None = None
    persisted: bool = False
    persistence_warning: str | None = None


class SavedAnalysisSummary(BaseModel):
    report_id: UUID
    asin: str
    product_title: str | None = None
    brand: str | None = None
    marketplace: str
    listing_quality_score: int | None = None
    custom_listing_quality_score: int | None = None
    scoring_profile_name: str | None = None
    source: str | None = None
    has_ai_strategy: bool = False
    has_image_intelligence: bool = False
    created_at: datetime
    completed_at: datetime | None = None
    status: str
    display_name: str | None = None


class SavedAnalysisListResponse(BaseModel):
    items: list[SavedAnalysisSummary] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 20


class SavedAnalysisMetadata(BaseModel):
    historical: bool = True
    analyzed_at: datetime | None = None
    product_fetched_at: datetime | None = None
    product_source: ProductSource | str | None = None
    listing_score_version: str | None = None
    ai_prompt_version: str | None = None
    image_prompt_version: str | None = None
    ai_provider: str | None = None
    ai_model: str | None = None
    image_provider: str | None = None
    image_model: str | None = None
    images_available: int | None = None
    images_selected: int | None = None
    images_skipped: int | None = None
    status: str


class SavedAnalysisDetail(BaseModel):
    report_id: UUID
    display_name: str | None = None
    product: Product
    analysis: ListingAnalysisV2
    custom_score: CustomScoreResult | None = None
    ai_intelligence: AIListingIntelligenceV2 | None = None
    image_intelligence: AIImageIntelligence | None = None
    meta: SavedAnalysisMetadata


class SavedAnalysisDeleteResponse(BaseModel):
    report_id: UUID
    deleted: bool = True


class ClientPdfGenerateResponse(BaseModel):
    report_id: UUID
    generated: bool
    reused: bool
    filename: str
    template_version: str

from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.ai_listing_intelligence import ActionPriority, AITokenUsage
from app.models.listing_analysis import FindingSeverity
from app.models.listing_analysis_v2 import EvidenceState, ListingAnalysisV2
from app.models.media_evidence import MediaSelectionResult
from app.models.product import Product, ProductSource


class VisualRole(StrEnum):
    PRODUCT_ONLY = "product_only"
    FEATURE = "feature"
    BENEFIT = "benefit"
    LIFESTYLE = "lifestyle"
    DIMENSIONS = "dimensions"
    HOW_TO_USE = "how_to_use"
    PACKAGING = "packaging"
    COMPARISON = "comparison"
    DETAIL_CLOSEUP = "detail_closeup"
    OTHER = "other"


class ImageFinding(BaseModel):
    severity: FindingSeverity
    image_ids: list[str] = Field(default_factory=list)
    evidence_type: str
    observation: str
    recommendation: str


class ImageAreaAnalysis(BaseModel):
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=4)
    concerns: list[str] = Field(default_factory=list, max_length=5)
    image_ids: list[str] = Field(default_factory=list)


class MainImageAnalysis(ImageAreaAnalysis):
    product_visibility: str | None = None
    background_characteristics: str | None = None
    embedded_text_notes: str | None = None


class GalleryAnalysis(BaseModel):
    assessment: str
    observed_roles: list[VisualRole] = Field(default_factory=list, max_length=10)
    coverage_opportunities: list[str] = Field(default_factory=list, max_length=6)
    image_ids: list[str] = Field(default_factory=list)


class APlusVisualAnalysis(BaseModel):
    evidence_state: EvidenceState
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=4)
    image_ids: list[str] = Field(default_factory=list)


class BrandStoryVisualAnalysis(BaseModel):
    evidence_state: EvidenceState
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=3)
    gaps: list[str] = Field(default_factory=list, max_length=3)
    image_ids: list[str] = Field(default_factory=list)


class MediaRoleCoverage(BaseModel):
    observed: list[VisualRole] = Field(default_factory=list, max_length=10)
    not_observed: list[VisualRole] = Field(default_factory=list, max_length=10)
    notes: list[str] = Field(default_factory=list, max_length=4)


class RecommendedImagePlanStep(BaseModel):
    step: int = Field(..., ge=1, le=8)
    slot: str
    purpose: str
    grounded_in: str


class PriorityVisualImprovement(BaseModel):
    priority: ActionPriority
    issue: str
    why_it_matters: str
    recommended_action: str
    image_ids: list[str] = Field(default_factory=list)


class AIImageIntelligence(BaseModel):
    """Qualitative visual intelligence. Not a listing-quality score."""

    executive_assessment: str = Field(..., min_length=1)
    visual_strengths: list[str] = Field(default_factory=list, max_length=5)
    priority_improvements: list[PriorityVisualImprovement] = Field(default_factory=list, max_length=5)
    main_image_analysis: MainImageAnalysis
    gallery_analysis: GalleryAnalysis
    a_plus_visual_analysis: APlusVisualAnalysis
    brand_story_analysis: BrandStoryVisualAnalysis
    media_role_coverage: MediaRoleCoverage
    redundancy_analysis: list[str] = Field(default_factory=list, max_length=5)
    image_findings: list[ImageFinding] = Field(default_factory=list, max_length=8)
    recommended_image_plan: list[RecommendedImagePlanStep] = Field(default_factory=list, max_length=7)
    confidence_notes: list[str] = Field(default_factory=list, max_length=6)


class AIImageIntelligenceRequest(BaseModel):
    product: Product
    analysis: ListingAnalysisV2
    source: ProductSource | None = None


class AIImageIntelligenceMeta(BaseModel):
    engine: str = "multimodal_ai"
    provider: str
    model: str
    prompt_version: str
    source: ProductSource | None = None
    images_available: int = 0
    images_selected: int = 0
    images_skipped: int = 0
    selection_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
    usage: AITokenUsage | None = None
    latency_ms: int | None = None
    media: MediaSelectionResult | None = None


class AIImageIntelligenceResponse(BaseModel):
    product: Product
    analysis: ListingAnalysisV2
    image_intelligence: AIImageIntelligence
    meta: AIImageIntelligenceMeta

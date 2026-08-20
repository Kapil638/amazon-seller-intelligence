from pydantic import BaseModel, Field

from app.models.ai_listing_intelligence import ActionPriority, AITokenUsage
from app.models.listing_analysis_v2 import EvidenceState, ListingAnalysisV2
from app.models.product import Product, ProductSource


class PriorityActionV2(BaseModel):
    priority: ActionPriority
    area: str
    issue: str
    why_it_matters: str
    recommended_action: str
    evidence_codes: list[str] = Field(default_factory=list)


class TitleContentInsight(BaseModel):
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=3)
    gaps: list[str] = Field(default_factory=list, max_length=3)


class BulletContentInsight(BaseModel):
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=5)
    seo_readiness_notes: list[str] = Field(default_factory=list, max_length=5)


class DescriptionContentInsight(BaseModel):
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=4)


class APlusContentInsight(BaseModel):
    evidence_state: EvidenceState
    assessment: str
    strengths: list[str] = Field(default_factory=list, max_length=4)
    gaps: list[str] = Field(default_factory=list, max_length=4)


class StructureContentInsight(BaseModel):
    assessment: str
    redundancy_notes: list[str] = Field(default_factory=list, max_length=4)
    coverage_gaps: list[str] = Field(default_factory=list, max_length=4)


class ContentAnalysisV2(BaseModel):
    title: TitleContentInsight
    bullets: BulletContentInsight
    description: DescriptionContentInsight
    a_plus: APlusContentInsight
    structure: StructureContentInsight


class SpecificationCoverage(BaseModel):
    represented: list[str] = Field(default_factory=list, max_length=8)
    missing_from_customer_copy: list[str] = Field(default_factory=list, max_length=8)
    not_recommended_for_copy: list[str] = Field(default_factory=list, max_length=6)


class RewriteSuggestions(BaseModel):
    suggested_title: str
    suggested_bullets: list[str] = Field(default_factory=list, max_length=5)
    optional_description_excerpt: str | None = None


class SellerActionStepV2(BaseModel):
    step: int = Field(..., ge=1, le=7)
    action: str
    priority: ActionPriority
    rationale: str


class AIListingIntelligenceV2(BaseModel):
    """Semantic listing-content strategy on top of ListingAnalysisV2. Not a score."""

    executive_assessment: str = Field(..., min_length=1)
    priority_actions: list[PriorityActionV2] = Field(default_factory=list, max_length=5)
    content_analysis: ContentAnalysisV2
    specification_coverage: SpecificationCoverage
    rewrite_suggestions: RewriteSuggestions
    seller_action_plan: list[SellerActionStepV2] = Field(default_factory=list, max_length=7)
    confidence_notes: list[str] = Field(default_factory=list, max_length=6)


class AIListingIntelligenceV2Request(BaseModel):
    product: Product
    analysis: ListingAnalysisV2
    source: ProductSource | None = None
    report_id: str | None = None


class AIListingIntelligenceV2Meta(BaseModel):
    engine: str = "ai"
    provider: str
    model: str
    prompt_version: str
    source: ProductSource | None = None
    usage: AITokenUsage | None = None
    latency_ms: int | None = None
    report_id: str | None = None
    persisted: bool = False
    persistence_warning: str | None = None


class AIListingIntelligenceV2Response(BaseModel):
    product: Product
    analysis: ListingAnalysisV2
    ai_intelligence: AIListingIntelligenceV2
    meta: AIListingIntelligenceV2Meta

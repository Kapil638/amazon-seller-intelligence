from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product, ProductSource


class ActionPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class PriorityAction(BaseModel):
    priority: ActionPriority
    title: str
    reason: str
    recommended_action: str


class TitleRecommendation(BaseModel):
    current_title: str
    suggested_title: str
    rationale: str


class BulletRecommendation(BaseModel):
    current: str
    suggested: str
    rationale: str


class SellerActionStep(BaseModel):
    step: int = Field(..., ge=1)
    action: str
    reason: str


class AIListingIntelligence(BaseModel):
    """Strategic interpretation layered on deterministic ListingAnalysis. Not a score."""

    executive_summary: str = Field(..., min_length=1)
    strengths: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    priority_actions: list[PriorityAction] = Field(default_factory=list)
    title_recommendation: TitleRecommendation
    bullet_recommendations: list[BulletRecommendation] = Field(default_factory=list)
    positioning_opportunities: list[str] = Field(default_factory=list)
    conversion_opportunities: list[str] = Field(default_factory=list)
    risks_and_cautions: list[str] = Field(default_factory=list)
    seller_action_plan: list[SellerActionStep] = Field(default_factory=list)


class AITokenUsage(BaseModel):
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


class AIListingIntelligenceRequest(BaseModel):
    product: Product
    analysis: ListingAnalysis
    source: ProductSource | None = None


class AIListingIntelligenceMeta(BaseModel):
    engine: str = "ai"
    provider: str
    model: str
    prompt_version: str
    source: ProductSource | None = None
    usage: AITokenUsage | None = None
    latency_ms: int | None = None


class AIListingIntelligenceResponse(BaseModel):
    product: Product
    analysis: ListingAnalysis
    ai_intelligence: AIListingIntelligence
    meta: AIListingIntelligenceMeta

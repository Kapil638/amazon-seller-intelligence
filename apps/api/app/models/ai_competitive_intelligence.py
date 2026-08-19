from pydantic import BaseModel, Field

from app.models.ai_listing_intelligence import ActionPriority, AITokenUsage
from app.models.competitor_comparison import COMPARISON_VERSION, CompetitorComparisonResponse


class CompetitivePoint(BaseModel):
    title: str
    evidence: str
    implication: str


class CompetitivePriorityGap(BaseModel):
    priority: ActionPriority
    dimension: str
    evidence: str
    recommended_action: str


class CompetitorObservation(BaseModel):
    asin: str
    observations: list[str] = Field(default_factory=list)


class PricePositioning(BaseModel):
    observation: str
    caution: str


class CompetitiveActionStep(BaseModel):
    step: int = Field(..., ge=1)
    action: str
    evidence: str
    reason: str


class AICompetitiveIntelligence(BaseModel):
    executive_summary: str = Field(..., min_length=1)
    competitive_position: str
    target_advantages: list[CompetitivePoint] = Field(default_factory=list)
    target_disadvantages: list[CompetitivePoint] = Field(default_factory=list)
    priority_gaps: list[CompetitivePriorityGap] = Field(default_factory=list)
    competitor_observations: list[CompetitorObservation] = Field(default_factory=list)
    content_opportunities: list[str] = Field(default_factory=list)
    price_positioning: PricePositioning
    seller_action_plan: list[CompetitiveActionStep] = Field(default_factory=list)


class AICompetitiveIntelligenceRequest(BaseModel):
    comparison: CompetitorComparisonResponse


class AICompetitiveIntelligenceMeta(BaseModel):
    engine: str = "ai"
    provider: str
    model: str
    prompt_version: str
    comparison_version: str = COMPARISON_VERSION
    usage: AITokenUsage | None = None
    latency_ms: int | None = None


class AICompetitiveIntelligenceResponse(BaseModel):
    comparison: CompetitorComparisonResponse
    ai_intelligence: AICompetitiveIntelligence
    meta: AICompetitiveIntelligenceMeta

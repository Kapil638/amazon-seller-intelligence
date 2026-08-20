from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.listing_analysis import AnalysisSection, Finding, SectionStatus
from app.models.product import BSR, Price, Product, ProductSource, RatingBreakdown, Seller


class EvidenceState(StrEnum):
    OBSERVED = "observed"
    REPORTED_ABSENT = "reported_absent"
    UNKNOWN = "unknown"


class RecommendationPriority(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class ListingQualitySections(BaseModel):
    title: AnalysisSection
    bullets: AnalysisSection
    description_a_plus: AnalysisSection
    media_coverage: AnalysisSection
    content_structure: AnalysisSection


class MarketSignals(BaseModel):
    """Factual marketplace observations. Not inputs to listing-quality score."""

    rating: float | None = None
    review_count: int | None = None
    price: Price | None = None
    availability: str | None = None
    availability_type: str | None = None
    is_sold_by_amazon: bool | None = None
    seller: Seller | None = None
    bsr_ranks: list[BSR] = Field(default_factory=list)
    recent_sales_text: str | None = None
    rating_breakdown: RatingBreakdown | None = None


class CoverageField(BaseModel):
    name: str
    evidence_state: EvidenceState
    available: bool
    note: str | None = None


class CoverageGroup(BaseModel):
    name: str
    available: int
    expected: int
    percentage: int = Field(..., ge=0, le=100)
    status: SectionStatus
    fields: list[CoverageField] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DataCoverage(BaseModel):
    """How much evidence was available. Not a listing-quality score."""

    overall_percentage: int = Field(..., ge=0, le=100)
    core_listing_content: CoverageGroup
    media: CoverageGroup
    enhanced_content: CoverageGroup
    category_context: CoverageGroup
    market_signals: CoverageGroup


class V2Recommendation(BaseModel):
    code: str
    category: str
    priority: RecommendationPriority
    action: str
    finding_code: str


class ListingAnalysisV2(BaseModel):
    listing_quality_score: int = Field(..., ge=0, le=100)
    score_version: str
    status: SectionStatus
    sections: ListingQualitySections
    market_signals: MarketSignals
    data_coverage: DataCoverage
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[V2Recommendation] = Field(default_factory=list)


class ListingAnalysisV2Response(BaseModel):
    product: Product
    analysis: ListingAnalysisV2
    meta: ListingAnalysisV2Meta


class ListingAnalysisV2Meta(BaseModel):
    engine: str = "deterministic"
    score_version: str
    source: ProductSource | None = None

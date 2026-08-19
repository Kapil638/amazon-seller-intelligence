from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product, ProductSource

COMPARISON_VERSION = "v1"


class GapSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class GapDirection(StrEnum):
    BELOW = "below"
    ABOVE = "above"
    MISSING = "missing"


class FailedCompetitor(BaseModel):
    asin: str
    reason: str


class ComparisonMetric(BaseModel):
    key: str
    label: str
    target_value: Any = None
    competitor_values: dict[str, Any] = Field(default_factory=dict)
    comparable: bool = True
    note: str | None = None


class PriceDelta(BaseModel):
    competitor_asin: str
    target_amount: float
    competitor_amount: float
    currency: str
    absolute_difference: float
    percentage_difference: float


class CompetitiveGap(BaseModel):
    dimension: str
    target_value: Any = None
    competitor_reference: Any = None
    competitor_asin: str | None = None
    direction: GapDirection
    severity: GapSeverity
    evidence: str


class ComparedListing(BaseModel):
    product: Product
    analysis: ListingAnalysis


class ComparisonSummary(BaseModel):
    requested_count: int
    retrieved_count: int
    listing_score_average: float | None = None
    listing_score_median: float | None = None
    target_listing_score: int
    target_vs_average: float | None = None


class CompetitorComparison(BaseModel):
    metrics: list[ComparisonMetric] = Field(default_factory=list)
    gaps: list[CompetitiveGap] = Field(default_factory=list)
    price_deltas: list[PriceDelta] = Field(default_factory=list)
    summary: ComparisonSummary


class CompetitorComparisonRequest(BaseModel):
    target_product: Product
    competitor_asins: list[str] = Field(..., min_length=1, max_length=3)
    marketplace: str | None = None
    source: ProductSource | None = None


class CompetitorComparisonMeta(BaseModel):
    source: str | None = None
    comparison_version: str = COMPARISON_VERSION
    score_version: str


class CompetitorComparisonResponse(BaseModel):
    target: ComparedListing
    competitors: list[ComparedListing]
    comparison: CompetitorComparison
    failed_competitors: list[FailedCompetitor] = Field(default_factory=list)
    meta: CompetitorComparisonMeta

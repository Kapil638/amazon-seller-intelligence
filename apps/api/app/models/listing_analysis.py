from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.product import Product, ProductSource


class SectionStatus(StrEnum):
    EXCELLENT = "excellent"
    GOOD = "good"
    FAIR = "fair"
    POOR = "poor"


class FindingSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class Finding(BaseModel):
    severity: FindingSeverity
    category: str
    code: str
    message: str


class Recommendation(BaseModel):
    code: str
    category: str
    message: str


class AnalysisSection(BaseModel):
    name: str
    score: int = Field(..., ge=0, le=100)
    max_score: int = 100
    status: SectionStatus
    metrics: dict[str, Any] = Field(default_factory=dict)
    findings: list[str] = Field(default_factory=list)


class AnalysisSections(BaseModel):
    title: AnalysisSection
    bullets: AnalysisSection
    description: AnalysisSection
    images: AnalysisSection
    completeness: AnalysisSection
    social_proof: AnalysisSection


class ListingAnalysis(BaseModel):
    overall_score: int = Field(..., ge=0, le=100)
    score_version: str
    sections: AnalysisSections
    findings: list[Finding] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)


class ListingAnalysisRequest(BaseModel):
    """Analyze the existing normalized Product. Optional source is echoed in meta."""

    product: Product
    source: ProductSource | None = None


class AnalysisMeta(BaseModel):
    engine: str = "deterministic"
    score_version: str
    source: ProductSource | None = None


class ListingAnalysisResponse(BaseModel):
    """Envelope consistent with product APIs: payload + meta, Product unchanged."""

    product: Product
    analysis: ListingAnalysis
    meta: AnalysisMeta

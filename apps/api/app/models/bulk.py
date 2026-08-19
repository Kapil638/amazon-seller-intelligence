from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.ai_listing_intelligence import AIListingIntelligence
from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product

BulkJobStatus = Literal["queued", "running", "completed", "completed_with_errors", "failed"]
BulkAnalysisMode = Literal["standard", "deep_ai"]
BulkAISelection = Literal["high_priority", "top_n", "all"]
BulkPriority = Literal["high", "medium", "low"]
BulkResultStatus = Literal["success"]
BulkFailureKind = Literal["invalid", "not_found", "transient", "provider"]


class BulkIngestStats(BaseModel):
    filename: str
    input_rows: int = 0
    valid_rows: int = 0
    invalid_rows: int = 0
    duplicate_rows_removed: int = 0
    unique_asins: int = 0
    asin_column: str | None = None


class BulkFailure(BaseModel):
    row: int | None = None
    input_asin: str
    reason: str
    kind: BulkFailureKind


class BulkASINProductResult(BaseModel):
    asin: str
    status: BulkResultStatus = "success"
    product: Product
    listing_analysis: ListingAnalysis
    ai_intelligence: AIListingIntelligence | None = None
    priority: BulkPriority
    cache_hit: bool = False
    ai_status: Literal["not_requested", "skipped", "mock", "cached"] = "not_requested"


class BulkUsageStats(BaseModel):
    product_provider: str
    ai_provider: str | None = None
    paid_api_usage: bool = False
    note: str = "Mock provider — no paid API usage"
    requested_asins: int = 0
    cache_hits: int = 0
    provider_calls: int = 0
    calls_saved: int = 0
    failures: int = 0
    retries: int = 0
    ai_eligible: int = 0
    ai_cache_hits: int = 0
    ai_provider_calls: int = 0
    ai_calls_saved: int = 0


class BulkPortfolioSummary(BaseModel):
    products_submitted: int = 0
    products_analyzed: int = 0
    products_failed: int = 0
    average_listing_score: float | None = None
    median_listing_score: float | None = None
    high_priority_count: int = 0
    medium_priority_count: int = 0
    low_priority_count: int = 0
    missing_description_count: int = 0
    low_image_count: int = 0
    weak_bullet_count: int = 0
    low_completeness_count: int = 0
    average_rating: float | None = None
    average_review_count: float | None = None
    average_image_count: float | None = None


class BulkJobProgress(BaseModel):
    total: int = 0
    processed: int = 0
    successful: int = 0
    failed: int = 0
    cache_hits: int = 0
    provider_calls: int = 0


class BulkJobOptions(BaseModel):
    analysis_mode: BulkAnalysisMode = "standard"
    ai_selection: BulkAISelection = "high_priority"
    top_n: int = 10
    marketplace: str = "amazon.in"


class BulkJobResponse(BaseModel):
    job_id: str
    status: BulkJobStatus
    options: BulkJobOptions
    ingest: BulkIngestStats
    progress: BulkJobProgress
    usage: BulkUsageStats
    summary: BulkPortfolioSummary | None = None
    results: list[BulkASINProductResult] = Field(default_factory=list)
    failures: list[BulkFailure] = Field(default_factory=list)
    attention: list[BulkASINProductResult] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime
    updated_at: datetime
    live_providers_enabled: bool = False

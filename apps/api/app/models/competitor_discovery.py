from pydantic import BaseModel, Field

from app.models.product import Product

DISCOVERY_VERSION = "v1"
MAX_DISPLAYED_CANDIDATES = 12


class DiscoveredProductCandidate(BaseModel):
    asin: str
    title: str
    brand: str | None = None
    price: float | None = None
    currency: str | None = None
    rating: float | None = None
    review_count: int | None = None
    image: str | None = None
    position: int | None = None
    is_sponsored: bool | None = None
    category: str | None = None
    search_query: str
    relevance_score: int = Field(..., ge=0, le=100)


class CompetitorDiscoveryMeta(BaseModel):
    provider: str
    marketplace: str
    discovery_version: str = DISCOVERY_VERSION
    query_generated: bool
    query_version: str
    relevance_version: str
    result_count: int
    displayed_count: int


class CompetitorDiscoveryResult(BaseModel):
    target_asin: str
    search_query: str
    candidates: list[DiscoveredProductCandidate] = Field(default_factory=list)
    meta: CompetitorDiscoveryMeta


class CompetitorDiscoveryRequest(BaseModel):
    target_product: Product
    search_query: str | None = None
    marketplace: str | None = None


class CompetitorSearchQueryRequest(BaseModel):
    target_product: Product


class CompetitorSearchQueryResponse(BaseModel):
    search_query: str
    meta: dict[str, str]

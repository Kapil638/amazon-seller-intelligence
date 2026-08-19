from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class Price(BaseModel):
    amount: float = Field(..., ge=0, description="Listed price amount")
    currency: str = Field(..., description="ISO 4217 currency code, e.g. INR")


class Image(BaseModel):
    url: str
    alt: str | None = None
    variant: str | None = None
    is_main: bool = False


class ProductVideo(BaseModel):
    title: str | None = None
    thumbnail_url: str | None = None
    video_url: str | None = None
    duration_seconds: int | None = None


class BSR(BaseModel):
    rank: int = Field(..., ge=1)
    category: str


class Seller(BaseModel):
    name: str
    id: str | None = None
    is_fba: bool | None = None
    rating: float | None = Field(default=None, ge=0, le=5)


class Variation(BaseModel):
    asin: str
    label: str
    attributes: dict[str, str] = Field(default_factory=dict)


class Product(BaseModel):
    """Normalized internal product model. All application layers use this schema."""

    asin: str
    marketplace: str = Field(
        ...,
        description="Amazon marketplace domain identifier, e.g. amazon.in",
    )
    title: str
    brand: str | None = None
    price: Price | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    bullet_points: list[str] = Field(default_factory=list)
    description: str | None = None
    images: list[Image] = Field(default_factory=list)
    videos: list[ProductVideo] = Field(default_factory=list)
    category: str | None = None
    bsr: BSR | None = None
    availability: str | None = None
    seller: Seller | None = None
    variations: list[Variation] = Field(default_factory=list)
    last_fetched_at: datetime


class ProductSource(StrEnum):
    """Provenance of a Product payload. Not stored on Product itself."""

    MOCK = "mock"
    MANUAL = "manual"
    AMAZON_PUBLIC = "amazon_public"
    RAINFOREST = "rainforest"


class ProductMeta(BaseModel):
    source: ProductSource


class ProductResponse(BaseModel):
    """HTTP envelope. Product stays source-agnostic; provenance lives in meta."""

    product: Product
    meta: ProductMeta

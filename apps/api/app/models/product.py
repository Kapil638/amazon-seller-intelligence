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
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class ProductVideo(BaseModel):
    title: str | None = None
    thumbnail_url: str | None = None
    video_url: str | None = None
    duration_seconds: int | None = None
    group_type: str | None = None
    group_id: str | None = None
    width: int | None = Field(default=None, ge=1)
    height: int | None = Field(default=None, ge=1)


class BSR(BaseModel):
    rank: int = Field(..., ge=1)
    category: str


class CategoryNode(BaseModel):
    name: str
    category_id: str | None = None


class Seller(BaseModel):
    name: str
    id: str | None = None
    is_fba: bool | None = None
    rating: float | None = Field(default=None, ge=0, le=5)


class Variation(BaseModel):
    asin: str
    label: str
    attributes: dict[str, str] = Field(default_factory=dict)
    is_current_product: bool | None = None


class ProductSpecification(BaseModel):
    name: str
    value: str


class ProductAttributes(BaseModel):
    """Optional structured attributes from a product payload. Not derived from title/bullets."""

    manufacturer: str | None = None
    ingredients: list[str] = Field(default_factory=list)
    diet_type: list[str] = Field(default_factory=list)
    listed: list[ProductSpecification] = Field(default_factory=list)


class APlusImage(BaseModel):
    url: str
    alt: str | None = None


class BrandStory(BaseModel):
    hero_image: str | None = None
    brand_logo: str | None = None
    description: str | None = None
    images: list[str] = Field(default_factory=list)


class APlusContent(BaseModel):
    """Optional A+ / Brand Story facts from a product payload. Presence is not quality."""

    has_a_plus_content: bool | None = None
    has_brand_story: bool | None = None
    third_party: bool | None = None
    company_logo: str | None = None
    company_description: str | None = None
    body_text: str | None = None
    images: list[APlusImage] = Field(default_factory=list)
    brand_story: BrandStory | None = None


class RatingBand(BaseModel):
    percentage: int | None = Field(default=None, ge=0, le=100)
    count: int | None = Field(default=None, ge=0)


class RatingBreakdown(BaseModel):
    five_star: RatingBand | None = None
    four_star: RatingBand | None = None
    three_star: RatingBand | None = None
    two_star: RatingBand | None = None
    one_star: RatingBand | None = None


class FeaturedReview(BaseModel):
    """A featured/top review shown on the product page. Not a full review corpus."""

    id: str | None = None
    title: str | None = None
    body: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    profile_name: str | None = None
    verified_purchase: bool | None = None
    date_raw: str | None = None
    date_utc: str | None = None


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
    bsr_ranks: list[BSR] = Field(default_factory=list)
    category_path: list[CategoryNode] = Field(default_factory=list)
    is_sold_by_amazon: bool | None = None
    availability_type: str | None = None
    videos_count: int | None = Field(default=None, ge=0)
    a_plus: APlusContent | None = None
    specifications: list[ProductSpecification] = Field(default_factory=list)
    specifications_flat: str | None = None
    attributes: ProductAttributes | None = None
    rating_breakdown: RatingBreakdown | None = None
    featured_reviews: list[FeaturedReview] = Field(default_factory=list)
    recent_sales_text: str | None = None


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

from pydantic import BaseModel, Field, field_validator

from app.core.validation import is_valid_asin, normalize_asin

MAX_BULLET_POINTS = 10


class ManualProductInput(BaseModel):
    """User-entered listing fields. Normalized into Product by ProductService."""

    asin: str
    title: str = Field(..., min_length=1)
    brand: str | None = None
    price: float | None = Field(default=None, ge=0)
    currency: str = "INR"
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    category: str | None = None
    bsr_rank: int | None = Field(default=None, ge=1)
    bsr_category: str | None = None
    availability: str | None = None
    seller: str | None = None
    description: str | None = None
    bullet_points: list[str] = Field(default_factory=list, max_length=MAX_BULLET_POINTS)
    image_urls: list[str] = Field(default_factory=list)
    marketplace: str | None = None

    @field_validator("asin")
    @classmethod
    def normalize_and_validate_asin(cls, value: str) -> str:
        normalized = normalize_asin(value)
        if not is_valid_asin(normalized):
            raise ValueError("Invalid ASIN format")
        return normalized

    @field_validator("title")
    @classmethod
    def require_title(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Title is required")
        return stripped

    @field_validator(
        "brand",
        "category",
        "availability",
        "seller",
        "description",
        "bsr_category",
        "marketplace",
    )
    @classmethod
    def blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator("bullet_points")
    @classmethod
    def clean_bullet_points(cls, value: list[str]) -> list[str]:
        cleaned = [item.strip() for item in value if item.strip()]
        if len(cleaned) > MAX_BULLET_POINTS:
            raise ValueError(f"A maximum of {MAX_BULLET_POINTS} bullet points is allowed")
        return cleaned

    @field_validator("image_urls")
    @classmethod
    def clean_image_urls(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item.strip()]

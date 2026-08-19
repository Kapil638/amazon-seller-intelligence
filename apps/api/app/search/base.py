from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class AmazonSearchHit(BaseModel):
    """Normalized Amazon search snippet. Incomplete by design; not a full Product."""

    asin: str
    title: str
    brand: str | None = None
    price: float | None = None
    currency: str | None = None
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    image: str | None = None
    position: int | None = Field(default=None, ge=1)
    is_sponsored: bool | None = None
    category: str | None = None


class AmazonSearchProvider(ABC):
    """Abstraction over Amazon search. Rainforest-specific code stays in the Rainforest provider."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    async def search(self, query: str, marketplace: str) -> list[AmazonSearchHit]:
        """Return search snippets for one query. One request. No pagination."""
        ...

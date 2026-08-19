from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.models.product import Product


class ProviderCapabilities(BaseModel):
    """Declares which product fields a provider can reasonably supply."""

    product_details: bool = False
    pricing: bool = False
    ratings: bool = False
    reviews: bool = False
    bsr: bool = False
    seller: bool = False
    variations: bool = False


class ProductDataProvider(ABC):
    """Abstraction over Amazon product data sources.

    The rest of the application depends only on this interface and the
    normalized Product model. Swap Mock → Rainforest → SP-API later
    without rewriting routes or the frontend.
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def capabilities(self) -> ProviderCapabilities: ...

    @abstractmethod
    async def get_product(self, asin: str, marketplace: str) -> Product | None:
        """Return a normalized Product, or None if the ASIN is unknown."""
        ...

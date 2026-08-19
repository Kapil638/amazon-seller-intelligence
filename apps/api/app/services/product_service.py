from datetime import UTC, datetime

from app.core.config import get_settings
from app.core.exceptions import ProductNotFoundError, UnsupportedMarketplaceError
from app.core.validation import is_valid_asin, normalize_asin
from app.models.manual import ManualProductInput
from app.models.product import BSR, Image, Price, Product, Seller
from app.providers.base import ProductDataProvider
from app.providers.mock import MockProductDataProvider


class ProductService:
    """Application service for product lookup and manual creation.

    Routes talk only to this layer. Later it can add caching, persistence,
    and provider routing without changing the API contract.
    """

    def __init__(
        self,
        provider: ProductDataProvider,
        mock_provider: MockProductDataProvider | None = None,
    ) -> None:
        self._provider = provider
        self._mock_provider = mock_provider or MockProductDataProvider()
        self._last_source = provider.name

    @property
    def provider_name(self) -> str:
        return self._last_source

    async def get_product(self, asin: str, marketplace: str | None = None) -> Product:
        product, source = await self.fetch_product(asin, marketplace)
        self._last_source = source
        return product

    async def fetch_product(self, asin: str, marketplace: str | None = None) -> tuple[Product, str]:
        """Return Product and provenance without racing shared last-source state."""
        settings = get_settings()
        resolved_marketplace = marketplace or settings.default_marketplace
        resolved_asin = normalize_asin(asin)

        if resolved_marketplace not in settings.supported_marketplaces:
            raise UnsupportedMarketplaceError(resolved_marketplace)

        if not is_valid_asin(resolved_asin):
            raise ValueError("Invalid ASIN format")

        if self._mock_provider.has_product(resolved_asin, resolved_marketplace):
            product = await self._mock_provider.get_product(resolved_asin, resolved_marketplace)
            if product is None:
                raise ProductNotFoundError(resolved_asin, resolved_marketplace)
            return product, self._mock_provider.name

        product = await self._provider.get_product(resolved_asin, resolved_marketplace)
        if product is None:
            raise ProductNotFoundError(resolved_asin, resolved_marketplace)
        return product, self._provider.name

    def create_from_manual(self, data: ManualProductInput) -> Product:
        """Normalize user-entered listing fields into the shared Product model."""
        settings = get_settings()
        marketplace = data.marketplace or settings.default_marketplace
        if marketplace not in settings.supported_marketplaces:
            raise UnsupportedMarketplaceError(marketplace)

        price = None
        if data.price is not None:
            price = Price(amount=data.price, currency=data.currency or "INR")

        bsr = None
        if data.bsr_rank is not None:
            bsr = BSR(
                rank=data.bsr_rank,
                category=data.bsr_category or data.category or "Overall",
            )

        seller = Seller(name=data.seller) if data.seller else None
        images = [Image(url=url) for url in data.image_urls]

        return Product(
            asin=data.asin,
            marketplace=marketplace,
            title=data.title,
            brand=data.brand,
            price=price,
            rating=data.rating,
            review_count=data.review_count,
            bullet_points=data.bullet_points,
            description=data.description,
            images=images,
            category=data.category,
            bsr=bsr,
            availability=data.availability,
            seller=seller,
            variations=[],
            last_fetched_at=datetime.now(UTC),
        )

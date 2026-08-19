from app.core.exceptions import ProductFetchFailedError
from app.models.product import Product
from app.providers.base import ProductDataProvider, ProviderCapabilities
from app.providers.mock import MOCK_CAPABILITIES, MockProductDataProvider
from app.bulk.mock_catalog import TRANSIENT_ASIN, bulk_fixture_catalog


class BulkMockProductDataProvider(ProductDataProvider):
    """Mock catalog for bulk jobs. Counts calls and can simulate one transient failure."""

    def __init__(self, inner: MockProductDataProvider | None = None) -> None:
        self._inner = inner or MockProductDataProvider()
        self._extra = bulk_fixture_catalog()
        self.calls: list[str] = []
        self._transient_attempts: dict[str, int] = {}

    @property
    def name(self) -> str:
        return "mock"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return MOCK_CAPABILITIES

    def reset_counts(self) -> None:
        self.calls.clear()
        self._transient_attempts.clear()

    async def get_product(self, asin: str, marketplace: str) -> Product | None:
        self.calls.append(asin)
        if asin == TRANSIENT_ASIN:
            attempt = self._transient_attempts.get(asin, 0)
            self._transient_attempts[asin] = attempt + 1
            if attempt == 0:
                raise ProductFetchFailedError(asin, marketplace, "mock transient failure")
        extra = self._extra.get((asin, marketplace))
        if extra is not None:
            return extra.model_copy(deep=True)
        return await self._inner.get_product(asin, marketplace)

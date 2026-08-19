from datetime import UTC, datetime

import httpx

from app.core.config import get_settings
from app.core.exceptions import (
    ProductFetchBlockedError,
    ProductFetchFailedError,
    ProductNotFoundError,
)
from app.models.product import Product
from app.parsers.amazon_product_parser import AmazonProductParser
from app.providers.base import ProductDataProvider, ProviderCapabilities
from app.providers.memory_cache import MemoryTtlCache

PRODUCT_URL = "https://www.amazon.in/dp/{asin}"

PUBLIC_CAPABILITIES = ProviderCapabilities(
    product_details=True,
    pricing=True,
    ratings=True,
    reviews=True,
    bsr=True,
    seller=True,
    variations=True,
)


class AmazonPublicProductDataProvider(ProductDataProvider):
    """Experimental public Amazon.in HTML lookup. Not an Amazon-supported API."""

    def __init__(
        self,
        parser: AmazonProductParser | None = None,
        cache: MemoryTtlCache | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        settings = get_settings()
        self._parser = parser or AmazonProductParser()
        self._cache = cache or MemoryTtlCache(settings.amazon_public_cache_ttl_seconds)
        self._transport = transport
        self._timeout = settings.amazon_public_timeout_seconds
        self._user_agent = settings.amazon_public_user_agent

    @property
    def name(self) -> str:
        return "amazon_public"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return PUBLIC_CAPABILITIES

    async def get_product(self, asin: str, marketplace: str) -> Product | None:
        if marketplace != "amazon.in":
            return None

        cached = self._cache.get(asin, marketplace)
        if cached is not None:
            return cached.model_copy(update={"last_fetched_at": datetime.now(UTC)})

        html = await self._fetch_html(asin, marketplace)
        product = self._parser.parse(html, asin, marketplace)
        self._cache.set(asin, marketplace, product)
        return product

    async def _fetch_html(self, asin: str, marketplace: str) -> str:
        url = PRODUCT_URL.format(asin=asin)
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "en-IN,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
                headers=headers,
            ) as client:
                response = await client.get(url)
        except httpx.TimeoutException as exc:
            raise ProductFetchFailedError(asin, marketplace, "Amazon.in request timed out") from exc
        except httpx.HTTPError as exc:
            raise ProductFetchFailedError(asin, marketplace, "Could not reach Amazon.in") from exc

        if response.status_code in {401, 403, 429, 503}:
            raise ProductFetchBlockedError(
                asin,
                marketplace,
                f"Amazon.in returned HTTP {response.status_code}",
            )
        if response.status_code == 404:
            raise ProductNotFoundError(asin, marketplace)
        if response.status_code != 200:
            raise ProductFetchFailedError(
                asin,
                marketplace,
                f"Amazon.in returned HTTP {response.status_code}",
            )
        return response.text

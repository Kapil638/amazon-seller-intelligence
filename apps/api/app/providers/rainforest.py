from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import (
    ProductFetchBlockedError,
    ProductFetchFailedError,
    ProductNotFoundError,
    ProductParseFailedError,
    ProviderConfigurationError,
)
from app.models.product import Product
from app.parsers.rainforest_product_mapper import map_rainforest_product
from app.providers.base import ProductDataProvider, ProviderCapabilities
from app.providers.memory_cache import MemoryTtlCache

RAINFOREST_CAPABILITIES = ProviderCapabilities(
    product_details=True,
    pricing=True,
    ratings=True,
    reviews=False,
    bsr=True,
    seller=True,
    variations=True,
)

MISSING_KEY_MESSAGE = (
    "Rainforest API is not configured. Set RAINFOREST_API_KEY in the backend environment."
)
INVALID_KEY_MESSAGE = (
    "Rainforest API authentication failed. Check RAINFOREST_API_KEY in the backend environment."
)
FETCH_FAILED_MESSAGE = (
    "Could not retrieve this Amazon.in listing. Try again later, or enter the listing manually."
)


class RainforestProductDataProvider(ProductDataProvider):
    """Rainforest Product Data API provider. Downstream code never sees Rainforest JSON."""

    def __init__(
        self,
        api_key: str | None = None,
        cache: MemoryTtlCache | None = None,
        transport: httpx.BaseTransport | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        if api_key is None:
            secret = settings.rainforest_api_key
            api_key = secret.get_secret_value() if secret is not None else ""
        self._api_key = api_key.strip()
        self._cache = cache or MemoryTtlCache(settings.rainforest_cache_ttl_seconds)
        self._transport = transport
        self._base_url = base_url or settings.rainforest_base_url
        self._timeout = timeout_seconds if timeout_seconds is not None else settings.rainforest_timeout_seconds
        self._supported_marketplaces = settings.supported_marketplaces

    @property
    def name(self) -> str:
        return "rainforest"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return RAINFOREST_CAPABILITIES

    def __repr__(self) -> str:
        return "RainforestProductDataProvider()"

    async def get_product(self, asin: str, marketplace: str) -> Product | None:
        if marketplace not in self._supported_marketplaces:
            return None
        if not self._api_key:
            raise ProviderConfigurationError(MISSING_KEY_MESSAGE)

        cached = self._cache.get(asin, marketplace)
        if cached is not None:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_rainforest_cache_hit("product")
            return cached.model_copy(update={"last_fetched_at": datetime.now(UTC)})

        try:
            payload = await self._fetch(asin, marketplace)
            self._raise_for_rainforest_status(payload, asin, marketplace)
            product = map_rainforest_product(payload, asin, marketplace)
        except (
            ProductFetchBlockedError,
            ProductFetchFailedError,
            ProductNotFoundError,
            ProductParseFailedError,
            ProviderConfigurationError,
        ):
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_rainforest_failure("product")
            raise
        from app.usage.ledger import get_usage_ledger

        get_usage_ledger().record_rainforest_product_call()
        self._cache.set(asin, marketplace, product)
        return product

    async def _fetch(self, asin: str, marketplace: str) -> dict[str, Any]:
        params = {
            "api_key": self._api_key,
            "type": "product",
            "amazon_domain": marketplace,
            "asin": asin,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(self._base_url, params=params)
        except httpx.TimeoutException:
            raise ProductFetchFailedError(asin, marketplace, "Rainforest request timed out") from None
        except httpx.HTTPError:
            raise ProductFetchFailedError(asin, marketplace, "Could not reach Rainforest API") from None

        return self._parse_http_response(response, asin, marketplace)

    def _parse_http_response(
        self,
        response: httpx.Response,
        asin: str,
        marketplace: str,
    ) -> dict[str, Any]:
        status = response.status_code
        if status == 401:
            raise ProviderConfigurationError(INVALID_KEY_MESSAGE)
        if status == 402:
            raise ProductFetchBlockedError(asin, marketplace, "Rainforest API credits are exhausted")
        if status == 429:
            raise ProductFetchBlockedError(asin, marketplace, "Rainforest API rate limit reached")
        if status == 503:
            raise ProductFetchBlockedError(asin, marketplace, "Rainforest API is temporarily unavailable")
        if status == 404:
            raise ProductNotFoundError(asin, marketplace)
        if status != 200:
            raise ProductFetchFailedError(
                asin,
                marketplace,
                f"Rainforest API returned HTTP {status}",
            )

        try:
            payload = response.json()
        except ValueError:
            raise ProductParseFailedError(asin, marketplace, "Rainforest response was not valid JSON") from None
        if not isinstance(payload, dict):
            raise ProductParseFailedError(asin, marketplace, "Rainforest response was not a JSON object")
        return payload

    def _raise_for_rainforest_status(
        self,
        payload: dict[str, Any],
        asin: str,
        marketplace: str,
    ) -> None:
        info = payload.get("request_info")
        if not isinstance(info, dict):
            return
        if info.get("success", True):
            return

        message = info.get("message")
        lowered = message.lower() if isinstance(message, str) else ""
        if self._api_key and self._api_key.lower() in lowered:
            lowered = ""
        if "invalid api key" in lowered or "unauthorized" in lowered:
            raise ProviderConfigurationError(INVALID_KEY_MESSAGE)
        if "404" in lowered or "not found" in lowered:
            raise ProductNotFoundError(asin, marketplace)
        raise ProductFetchFailedError(asin, marketplace, FETCH_FAILED_MESSAGE)

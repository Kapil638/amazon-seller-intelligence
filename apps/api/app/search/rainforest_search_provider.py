from typing import Any

import httpx

from app.core.config import get_settings
from app.core.exceptions import (
    ProviderConfigurationError,
    SearchBlockedError,
    SearchFetchFailedError,
    SearchParseFailedError,
    UnsupportedMarketplaceError,
)
from app.parsers.rainforest_search_mapper import map_rainforest_search
from app.providers.memory_cache import MemoryTtlValueCache
from app.search.base import AmazonSearchHit, AmazonSearchProvider

MISSING_KEY_MESSAGE = (
    "Rainforest API is not configured. Set RAINFOREST_API_KEY in the backend environment."
)
INVALID_KEY_MESSAGE = (
    "Rainforest API authentication failed. Check RAINFOREST_API_KEY in the backend environment."
)
UNAVAILABLE_MESSAGE = "Competitor discovery is temporarily unavailable."


class RainforestAmazonSearchProvider(AmazonSearchProvider):
    """Rainforest type=search. Downstream code never sees Rainforest JSON."""

    def __init__(
        self,
        api_key: str | None = None,
        cache: MemoryTtlValueCache | None = None,
        transport: httpx.BaseTransport | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        settings = get_settings()
        if api_key is None:
            secret = settings.rainforest_api_key
            api_key = secret.get_secret_value() if secret is not None else ""
        self._api_key = (api_key or "").strip()
        self._cache = cache if cache is not None else MemoryTtlValueCache(
            settings.rainforest_search_cache_ttl_seconds
        )
        self._transport = transport
        self._base_url = base_url or settings.rainforest_base_url
        self._timeout = timeout_seconds if timeout_seconds is not None else settings.rainforest_timeout_seconds
        self._supported_marketplaces = settings.supported_marketplaces

    @property
    def name(self) -> str:
        return "rainforest"

    def __repr__(self) -> str:
        return "RainforestAmazonSearchProvider()"

    async def search(self, query: str, marketplace: str) -> list[AmazonSearchHit]:
        if marketplace not in self._supported_marketplaces:
            raise UnsupportedMarketplaceError(marketplace)
        if not self._api_key:
            raise ProviderConfigurationError(MISSING_KEY_MESSAGE)

        cache_key = _cache_key(self.name, marketplace, query)
        cached = self._cache.get(cache_key)
        if cached is not None:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_rainforest_cache_hit("search")
            return [AmazonSearchHit.model_validate(item) for item in cached]

        try:
            payload = await self._fetch(query, marketplace)
            self._raise_for_rainforest_status(payload)
            hits = map_rainforest_search(payload, marketplace)
        except (
            ProviderConfigurationError,
            SearchBlockedError,
            SearchFetchFailedError,
            SearchParseFailedError,
        ):
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_rainforest_failure("search")
            raise
        from app.usage.ledger import get_usage_ledger

        get_usage_ledger().record_rainforest_search_call()
        self._cache.set(cache_key, [item.model_dump() for item in hits])
        return hits

    async def _fetch(self, query: str, marketplace: str) -> dict[str, Any]:
        params = {
            "api_key": self._api_key,
            "type": "search",
            "amazon_domain": marketplace,
            "search_term": query,
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(self._base_url, params=params)
        except httpx.TimeoutException:
            raise SearchFetchFailedError("Amazon search timed out.") from None
        except httpx.HTTPError:
            raise SearchFetchFailedError(UNAVAILABLE_MESSAGE) from None
        return self._parse_http_response(response)

    def _parse_http_response(self, response: httpx.Response) -> dict[str, Any]:
        status = response.status_code
        if status == 401:
            raise ProviderConfigurationError(INVALID_KEY_MESSAGE)
        if status == 402:
            raise SearchBlockedError("Competitor discovery is temporarily unavailable.")
        if status == 429:
            raise SearchBlockedError("Competitor discovery is temporarily unavailable.")
        if status == 503:
            raise SearchBlockedError("Competitor discovery is temporarily unavailable.")
        if status != 200:
            raise SearchFetchFailedError(UNAVAILABLE_MESSAGE)
        try:
            payload = response.json()
        except ValueError:
            raise SearchParseFailedError("Amazon search results could not be read.") from None
        if not isinstance(payload, dict):
            raise SearchParseFailedError("Amazon search results could not be read.")
        return payload

    def _raise_for_rainforest_status(self, payload: dict[str, Any]) -> None:
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
        raise SearchFetchFailedError(UNAVAILABLE_MESSAGE)


def _cache_key(provider: str, marketplace: str, query: str) -> str:
    normalized = " ".join(query.casefold().split())
    return f"rainforest-search|{provider}|{marketplace}|{normalized}"

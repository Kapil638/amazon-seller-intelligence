"""SP-API Sellers v1 client. Connection-scoped seller credentials only.

Does not ingest listings, orders, inventory, or reports. Refresh tokens are
injected as SecretStr and never read from the sandbox environment token.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.lwa import LwaClient
from app.amazon.models import GetMarketplaceParticipationsResponse, MarketplaceParticipation
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS, MARKETPLACE_PARTICIPATIONS_PATH, sandbox_base_url
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

PRODUCTION_BASE_URLS = {
    "na": "https://sellingpartnerapi-na.amazon.com",
    "eu": "https://sellingpartnerapi-eu.amazon.com",
    "fe": "https://sellingpartnerapi-fe.amazon.com",
}


def sp_api_base_url(
    *,
    region: str,
    environment: str,
    sandbox_override: str = "",
    production_override: str = "",
) -> str:
    """Resolve Sellers host from connection region and environment. Not a secret."""
    if (environment or "").strip().upper() == "SANDBOX":
        return sandbox_base_url(region, sandbox_override)
    if production_override.strip():
        return production_override.rstrip("/")
    key = (region or "eu").strip().lower()
    if key not in PRODUCTION_BASE_URLS:
        raise SpApiConfigurationError(f"Unsupported SP-API region: {region}")
    return PRODUCTION_BASE_URLS[key]


class AmazonSpApiSellersClient:
    """GET /sellers/v1/marketplaceParticipations with an injected seller refresh token."""

    def __init__(
        self,
        *,
        client_id: SecretStr | str | None,
        client_secret: SecretStr | str | None,
        refresh_token: SecretStr,
        token_url: str,
        base_url: str,
        region: str,
        timeout_seconds: float = 30,
        user_agent: str = "AmazonSellerIntelligence/12B.1D (Language=Python/3.12)",
        transport: httpx.BaseTransport | None = None,
        lwa: LwaClient | None = None,
    ) -> None:
        if not isinstance(refresh_token, SecretStr):
            raise SpApiConfigurationError("Amazon seller refresh token is not configured.")
        self._region = (region or "eu").strip().lower()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._transport = transport
        self._lwa = lwa or LwaClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=token_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "AmazonSpApiSellersClient()"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint_host(self) -> str:
        return urlparse(self._base_url).netloc

    async def get_marketplace_participations(self) -> GetMarketplaceParticipationsResponse:
        token = await self._lwa.fetch_access_token()
        url = f"{self._base_url}{MARKETPLACE_PARTICIPATIONS_PATH}"
        headers = {
            "x-amz-access-token": token.access_token.get_secret_value(),
            "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "user-agent": self._user_agent,
            "accept": "application/json",
        }
        del token
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            logger.warning("SP-API sellers request timed out operation=%s", GET_MARKETPLACE_PARTICIPATIONS)
            raise SpApiRequestFailedError("Amazon SP-API request timed out.") from None
        except httpx.HTTPError:
            logger.warning("SP-API sellers request failed operation=%s", GET_MARKETPLACE_PARTICIPATIONS)
            raise SpApiRequestFailedError("Could not reach Amazon SP-API.") from None
        return self._parse_sellers_response(response)

    def _parse_sellers_response(self, response: httpx.Response) -> GetMarketplaceParticipationsResponse:
        status = response.status_code
        if status in {401, 403}:
            logger.warning("SP-API sellers authentication failed status=%s", status)
            raise SpApiAuthenticationError("Amazon SP-API authentication failed.")
        if status == 429:
            logger.warning("SP-API sellers rate-limited status=%s", status)
            raise SpApiRateLimitedError("Amazon SP-API rate limit reached.")
        if status >= 500:
            logger.warning("SP-API sellers failed status=%s", status)
            raise SpApiRequestFailedError("Amazon SP-API request failed.")
        if status != 200:
            logger.warning("SP-API sellers unexpected status=%s", status)
            raise SpApiRequestFailedError("Amazon SP-API request failed.")
        try:
            body = response.json()
        except ValueError:
            raise SpApiParseFailedError("Amazon SP-API returned a non-JSON body.") from None
        try:
            parsed = GetMarketplaceParticipationsResponse.model_validate(body)
        except ValidationError:
            raise SpApiParseFailedError("Amazon SP-API payload was malformed.") from None
        if parsed.payload is None:
            raise SpApiParseFailedError("Amazon SP-API payload was missing.")
        return parsed


def participating_marketplaces(
    payload: list[MarketplaceParticipation],
) -> list[tuple[str, str]]:
    """Return (marketplace_id, country_code) for participating marketplaces."""
    found: list[tuple[str, str]] = []
    for item in payload:
        if not item.participation.is_participating:
            continue
        marketplace_id = (item.marketplace.id or "").strip()
        country_code = (item.marketplace.country_code or "").strip()
        if marketplace_id and country_code:
            found.append((marketplace_id, country_code))
    return found

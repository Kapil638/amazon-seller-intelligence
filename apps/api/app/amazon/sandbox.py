"""SP-API static sandbox client. Isolated from Copilot, Profit, Advertising, and Skills."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import UUID

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.lwa import MISSING_CREDENTIALS_MESSAGE, LwaClient
from app.amazon.models import (
    GetMarketplaceParticipationsResponse,
    MarketplaceParticipationsSandboxResult,
    SpApiSandboxProvenance,
)
from app.amazon.secrets import (
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    development_sandbox_token_reference,
    get_secret_provider,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

SELLERS_API = "sellers"
GET_MARKETPLACE_PARTICIPATIONS = "getMarketplaceParticipations"
MARKETPLACE_PARTICIPATIONS_PATH = "/sellers/v1/marketplaceParticipations"
SELLERS_MODEL_VERSION = "sellers-api-model/v1"
SANDBOX_BASE_URLS = {
    "na": "https://sandbox.sellingpartnerapi-na.amazon.com",
    "eu": "https://sandbox.sellingpartnerapi-eu.amazon.com",
    "fe": "https://sandbox.sellingpartnerapi-fe.amazon.com",
}


def sandbox_base_url(region: str, override: str = "") -> str:
    if override.strip():
        return override.rstrip("/")
    key = (region or "eu").strip().lower()
    if key not in SANDBOX_BASE_URLS:
        raise SpApiConfigurationError(f"Unsupported SP-API region: {region}")
    return SANDBOX_BASE_URLS[key]


def resolve_sandbox_refresh_token(
    *,
    secret_provider: SecretProvider | None = None,
    organization_id: UUID | str | None = None,
) -> SecretStr:
    """Resolve the seller refresh token through SecretProvider.

    Uses the development sandbox reference only. Database token_reference
    binding is out of scope for 12B.1B.3. App LWA client_id/client_secret
    stay in settings.
    """
    try:
        provider = secret_provider or get_secret_provider()
        return provider.get_secret(development_sandbox_token_reference(organization_id))
    except (SecretNotFoundError, SecretAccessError, InvalidSecretReferenceError):
        raise SpApiConfigurationError(MISSING_CREDENTIALS_MESSAGE) from None


class AmazonSpApiSandboxClient:
    """Authenticate with LWA, then call one Sellers static-sandbox operation."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        lwa: LwaClient | None = None,
        secret_provider: SecretProvider | None = None,
        transport: httpx.BaseTransport | None = None,
        base_url: str | None = None,
        region: str | None = None,
        timeout_seconds: float | None = None,
        user_agent: str | None = None,
    ) -> None:
        cfg = settings or get_settings()
        self._region = (region or cfg.sp_api_region or "na").strip().lower()
        self._base_url = base_url or sandbox_base_url(self._region, cfg.sp_api_sandbox_base_url)
        self._timeout = timeout_seconds if timeout_seconds is not None else cfg.sp_api_timeout_seconds
        self._user_agent = user_agent or cfg.sp_api_user_agent
        self._transport = transport
        if lwa is not None:
            self._lwa = lwa
        else:
            refresh_token = resolve_sandbox_refresh_token(
                secret_provider=secret_provider,
                organization_id=cfg.default_organization_id,
            )
            self._lwa = LwaClient(
                client_id=cfg.sp_api_lwa_client_id,
                client_secret=cfg.sp_api_lwa_client_secret,
                refresh_token=refresh_token,
                token_url=cfg.sp_api_lwa_token_url,
                timeout_seconds=self._timeout,
                transport=transport,
            )

    def __repr__(self) -> str:
        return "AmazonSpApiSandboxClient()"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint_host(self) -> str:
        return urlparse(self._base_url).netloc

    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult:
        token = await self._lwa.fetch_access_token()
        url = f"{self._base_url}{MARKETPLACE_PARTICIPATIONS_PATH}"
        headers = {
            "x-amz-access-token": token.access_token.get_secret_value(),
            "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "user-agent": self._user_agent,
            "accept": "application/json",
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.get(url, headers=headers)
        except httpx.TimeoutException:
            logger.warning("SP-API sandbox request timed out operation=%s", GET_MARKETPLACE_PARTICIPATIONS)
            raise SpApiRequestFailedError("Amazon SP-API sandbox request timed out.") from None
        except httpx.HTTPError:
            logger.warning("SP-API sandbox request failed operation=%s", GET_MARKETPLACE_PARTICIPATIONS)
            raise SpApiRequestFailedError("Could not reach Amazon SP-API sandbox.") from None
        parsed = self._parse_sellers_response(response)
        payload = parsed.payload or []
        return MarketplaceParticipationsSandboxResult(
            payload=payload,
            participation_count=len(payload),
            provenance=SpApiSandboxProvenance(
                operation=GET_MARKETPLACE_PARTICIPATIONS,
                region=self._region,
                endpoint_host=self.endpoint_host,
                fetched_at=datetime.now(UTC),
                http_status=response.status_code,
                api_model_version=SELLERS_MODEL_VERSION,
            ),
        )

    def _parse_sellers_response(self, response: httpx.Response) -> GetMarketplaceParticipationsResponse:
        status = response.status_code
        if status in {401, 403}:
            logger.warning("SP-API sandbox authentication failed status=%s", status)
            raise SpApiAuthenticationError("Amazon SP-API sandbox authentication failed.")
        if status == 429:
            logger.warning("SP-API sandbox rate-limited status=%s", status)
            raise SpApiRateLimitedError()
        if status >= 500:
            logger.warning("SP-API sandbox failed status=%s", status)
            raise SpApiRequestFailedError("Amazon SP-API sandbox request failed.")
        if status != 200:
            logger.warning("SP-API sandbox unexpected status=%s", status)
            raise SpApiRequestFailedError("Amazon SP-API sandbox request failed.")
        try:
            body = response.json()
        except ValueError as exc:
            raise SpApiParseFailedError("Amazon SP-API sandbox returned a non-JSON body.") from exc
        try:
            parsed = GetMarketplaceParticipationsResponse.model_validate(body)
        except ValidationError as exc:
            raise SpApiParseFailedError("Amazon SP-API sandbox payload was malformed.") from exc
        if parsed.payload is None:
            raise SpApiParseFailedError("Amazon SP-API sandbox payload was missing.")
        return parsed

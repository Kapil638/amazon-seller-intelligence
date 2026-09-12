"""Login with Amazon authorization-code exchange for the Amazon Ads OAuth
flow. Never logs or persists tokens. Mirrors `app.amazon.lwa_token`
exactly, reading `ads_*` settings instead of `sp_api_*` ones — Ads
credentials are never mixed with SP-API credentials (see
`app.core.config.Settings`).

Refresh-token grant reuses `app.amazon.lwa.LwaClient` directly (same LWA
token endpoint, same grant shape) rather than duplicating it — only the
authorization-code grant needed a distinct entry point, matching
`AmazonLwaTokenService`'s own reason for existing separately from
`LwaClient`.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL, LwaClient, _secret_text
from app.amazon.models import LwaAuthorizationGrant, LwaTokenResponse
from app.core.config import Settings
from app.core.exceptions import (
    AdsApiAuthenticationError,
    AdsApiParseFailedError,
    AdsApiRateLimitedError,
    AdsApiRequestFailedError,
    AdsConfigurationError,
)
from app.core.exceptions import (
    SpApiAuthenticationError as _SpApiAuthenticationError,
)
from app.core.exceptions import (
    SpApiConfigurationError as _SpApiConfigurationError,
)
from app.core.exceptions import (
    SpApiParseFailedError as _SpApiParseFailedError,
)
from app.core.exceptions import (
    SpApiRateLimitedError as _SpApiRateLimitedError,
)
from app.core.exceptions import (
    SpApiRequestFailedError as _SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

MISSING_ADS_OAUTH_CREDENTIALS_MESSAGE = "Amazon Ads application credentials are not configured."
MISSING_ADS_OAUTH_REDIRECT_URI_MESSAGE = "Amazon Ads OAuth redirect URI is not configured."


def _raise_for_ads_lwa_status(status: int) -> None:
    """Ads-domain equivalent of `app.amazon.lwa.raise_for_lwa_status` — kept
    separate so an Ads token failure never surfaces as an `SpApi*`
    exception type (credentials/connections/exceptions stay separate)."""
    if status == 200:
        return
    if status in {400, 401, 403}:
        logger.warning("Ads LWA token request rejected status=%s", status)
        raise AdsApiAuthenticationError("Amazon Ads LWA authentication failed.")
    if status == 429:
        logger.warning("Ads LWA token request rate-limited status=%s", status)
        raise AdsApiRateLimitedError("Amazon Ads LWA rate limit reached.")
    logger.warning("Ads LWA token request failed status=%s", status)
    raise AdsApiRequestFailedError("Amazon Ads LWA token request failed.")


def _read_ads_lwa_json(response: httpx.Response) -> dict:
    _raise_for_ads_lwa_status(response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise AdsApiParseFailedError("Amazon Ads LWA returned a non-JSON token response.") from None
    if not isinstance(payload, dict):
        raise AdsApiParseFailedError("Amazon Ads LWA token response was malformed.")
    return payload


def ads_oauth_application_credentials(cfg: Settings) -> tuple[SecretStr, SecretStr]:
    client_id = cfg.ads_lwa_client_id
    client_secret = cfg.ads_lwa_client_secret
    if _secret_text(client_id) and _secret_text(client_secret):
        assert client_id is not None
        assert client_secret is not None
        return client_id, client_secret
    raise AdsConfigurationError(MISSING_ADS_OAUTH_CREDENTIALS_MESSAGE)


class AdsLwaTokenService:
    """Exchange an Amazon Ads authorization code for LWA tokens. Never logs secrets."""

    def __init__(
        self,
        *,
        client_id: SecretStr | str | None,
        client_secret: SecretStr | str | None,
        redirect_uri: str,
        token_url: str,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = _secret_text(client_id)
        self._client_secret = _secret_text(client_secret)
        self._redirect_uri = (redirect_uri or "").strip()
        self._token_url = (token_url or DEFAULT_LWA_TOKEN_URL).rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def __repr__(self) -> str:
        return "AdsLwaTokenService()"

    @classmethod
    def from_settings(cls, cfg: Settings, *, transport: httpx.BaseTransport | None = None) -> AdsLwaTokenService:
        client_id, client_secret = ads_oauth_application_credentials(cfg)
        redirect_uri = (cfg.ads_oauth_redirect_uri or "").strip()
        if not redirect_uri:
            raise AdsConfigurationError(MISSING_ADS_OAUTH_REDIRECT_URI_MESSAGE)
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            token_url=cfg.ads_lwa_token_url,
            timeout_seconds=cfg.ads_api_timeout_seconds,
            transport=transport,
        )

    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        if not isinstance(authorization_code, SecretStr):
            raise AdsApiAuthenticationError("Amazon Ads LWA authentication failed.")
        code = _secret_text(authorization_code)
        if not code:
            raise AdsApiAuthenticationError("Amazon Ads LWA authentication failed.")
        if not self._client_id or not self._client_secret:
            raise AdsConfigurationError(MISSING_ADS_OAUTH_CREDENTIALS_MESSAGE)
        if not self._redirect_uri:
            raise AdsConfigurationError(MISSING_ADS_OAUTH_REDIRECT_URI_MESSAGE)
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                response = client.post(
                    self._token_url,
                    data={
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": self._redirect_uri,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                )
        except httpx.TimeoutException:
            logger.warning("Ads LWA authorization-code request timed out")
            raise AdsApiRequestFailedError("Amazon Ads LWA token request timed out.") from None
        except httpx.HTTPError:
            logger.warning("Ads LWA authorization-code request could not be completed")
            raise AdsApiRequestFailedError("Could not reach Amazon LWA token endpoint.") from None
        return self._parse_grant(response)

    def _parse_grant(self, response: httpx.Response) -> LwaAuthorizationGrant:
        payload = _read_ads_lwa_json(response)
        try:
            return LwaAuthorizationGrant.model_validate(payload)
        except ValidationError:
            raise AdsApiParseFailedError("Amazon Ads LWA token response was malformed.") from None


async def refresh_ads_access_token(
    *,
    client_id: SecretStr | str | None,
    client_secret: SecretStr | str | None,
    refresh_token: SecretStr | str | None,
    token_url: str,
    timeout_seconds: float = 30,
    transport: httpx.BaseTransport | None = None,
) -> LwaTokenResponse:
    """Refresh-token grant for Ads, via the shared `LwaClient` HTTP logic —
    but translated to `Ads*` exceptions at this boundary, since
    `LwaClient` itself always raises the `SpApi*` names. Keeps the two
    exception domains separate for callers without duplicating the HTTP
    client."""
    client = LwaClient(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
        token_url=token_url,
        timeout_seconds=timeout_seconds,
        transport=transport,
    )
    try:
        return await client.fetch_access_token()
    except _SpApiConfigurationError as exc:
        raise AdsConfigurationError(str(exc) or MISSING_ADS_OAUTH_CREDENTIALS_MESSAGE) from exc
    except _SpApiAuthenticationError as exc:
        raise AdsApiAuthenticationError("Amazon Ads LWA authentication failed.") from exc
    except _SpApiRateLimitedError as exc:
        raise AdsApiRateLimitedError(
            "Amazon Ads LWA rate limit reached.", retry_after_seconds=getattr(exc, "retry_after_seconds", None)
        ) from exc
    except _SpApiParseFailedError as exc:
        raise AdsApiParseFailedError("Amazon Ads LWA token response was malformed.") from exc
    except _SpApiRequestFailedError as exc:
        raise AdsApiRequestFailedError(str(exc) or "Amazon Ads LWA token request failed.") from exc

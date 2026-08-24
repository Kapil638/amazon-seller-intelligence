"""Login with Amazon authorization-code exchange. Never logs or persists tokens.

This module does not call SP-API, store secrets, or change connection status.
Refresh-token grant remains in `LwaClient`.
"""

from __future__ import annotations

import logging

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL, _secret_text, read_lwa_json
from app.amazon.models import LwaAuthorizationGrant
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

MISSING_OAUTH_CREDENTIALS_MESSAGE = (
    "Amazon application credentials are not configured for authorization."
)
MISSING_OAUTH_REDIRECT_URI_MESSAGE = "Amazon OAuth redirect URI is not configured."


def oauth_application_credentials(cfg: Settings) -> tuple[SecretStr, SecretStr]:
    """Prefer production/Draft LWA credentials; otherwise the sandbox pair."""
    production_id = cfg.sp_api_production_lwa_client_id
    production_secret = cfg.sp_api_production_lwa_client_secret
    if _secret_text(production_id) and _secret_text(production_secret):
        assert production_id is not None
        assert production_secret is not None
        return production_id, production_secret
    sandbox_id = cfg.sp_api_lwa_client_id
    sandbox_secret = cfg.sp_api_lwa_client_secret
    if _secret_text(sandbox_id) and _secret_text(sandbox_secret):
        assert sandbox_id is not None
        assert sandbox_secret is not None
        return sandbox_id, sandbox_secret
    raise SpApiConfigurationError(MISSING_OAUTH_CREDENTIALS_MESSAGE)


class AmazonLwaTokenService:
    """Exchange an authorization code for LWA tokens. Never logs secrets."""

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
        return "AmazonLwaTokenService()"

    @classmethod
    def from_settings(
        cls,
        cfg: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> AmazonLwaTokenService:
        client_id, client_secret = oauth_application_credentials(cfg)
        redirect_uri = (cfg.sp_api_oauth_redirect_uri or "").strip()
        if not redirect_uri:
            raise SpApiConfigurationError(MISSING_OAUTH_REDIRECT_URI_MESSAGE)
        return cls(
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=redirect_uri,
            token_url=cfg.sp_api_lwa_token_url,
            timeout_seconds=cfg.sp_api_timeout_seconds,
            transport=transport,
        )

    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        if not isinstance(authorization_code, SecretStr):
            raise SpApiAuthenticationError("Amazon LWA authentication failed.")
        code = _secret_text(authorization_code)
        if not code:
            raise SpApiAuthenticationError("Amazon LWA authentication failed.")
        if not self._client_id or not self._client_secret:
            raise SpApiConfigurationError(MISSING_OAUTH_CREDENTIALS_MESSAGE)
        if not self._redirect_uri:
            raise SpApiConfigurationError(MISSING_OAUTH_REDIRECT_URI_MESSAGE)
        try:
            with httpx.Client(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
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
            logger.warning("LWA authorization-code request timed out")
            raise SpApiRequestFailedError("Amazon LWA token request timed out.") from None
        except httpx.HTTPError:
            logger.warning("LWA authorization-code request could not be completed")
            raise SpApiRequestFailedError("Could not reach Amazon LWA token endpoint.") from None
        return self._parse_grant(response)

    def _parse_grant(self, response: httpx.Response) -> LwaAuthorizationGrant:
        payload = read_lwa_json(response)
        try:
            return LwaAuthorizationGrant.model_validate(payload)
        except ValidationError:
            raise SpApiParseFailedError("Amazon LWA token response was malformed.") from None

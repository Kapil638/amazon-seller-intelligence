"""Login with Amazon token client. Refresh-token grant only. Never logs secrets."""

from __future__ import annotations

import logging

import httpx
from pydantic import SecretStr, ValidationError

from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

from app.amazon.models import LwaTokenResponse

logger = logging.getLogger(__name__)

DEFAULT_LWA_TOKEN_URL = "https://api.amazon.com/auth/o2/token"
MISSING_CREDENTIALS_MESSAGE = (
    "SP-API sandbox is not configured. Set SP_API_LWA_CLIENT_ID, "
    "SP_API_LWA_CLIENT_SECRET, and SP_API_SANDBOX_REFRESH_TOKEN in the backend .env."
)


def _secret_text(value: SecretStr | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value().strip()
    return str(value).strip()


def credentials_configured(
    client_id: SecretStr | str | None,
    client_secret: SecretStr | str | None,
    refresh_token: SecretStr | str | None,
) -> bool:
    return bool(_secret_text(client_id) and _secret_text(client_secret) and _secret_text(refresh_token))


class LwaClient:
    """Exchange a selling-partner refresh token for a short-lived LWA access token."""

    def __init__(
        self,
        *,
        client_id: SecretStr | str | None,
        client_secret: SecretStr | str | None,
        refresh_token: SecretStr | str | None,
        token_url: str = DEFAULT_LWA_TOKEN_URL,
        timeout_seconds: float = 30,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client_id = _secret_text(client_id)
        self._client_secret = _secret_text(client_secret)
        self._refresh_token = _secret_text(refresh_token)
        self._token_url = token_url.rstrip("/")
        self._timeout = timeout_seconds
        self._transport = transport

    def __repr__(self) -> str:
        return "LwaClient()"

    async def fetch_access_token(self) -> LwaTokenResponse:
        if not self._client_id or not self._client_secret or not self._refresh_token:
            raise SpApiConfigurationError(MISSING_CREDENTIALS_MESSAGE)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._token_url,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": self._refresh_token,
                        "client_id": self._client_id,
                        "client_secret": self._client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
                )
        except httpx.TimeoutException:
            logger.warning("LWA token request timed out")
            raise SpApiRequestFailedError("Amazon LWA token request timed out.") from None
        except httpx.HTTPError:
            logger.warning("LWA token request could not be completed")
            raise SpApiRequestFailedError("Could not reach Amazon LWA token endpoint.") from None
        return self._parse_response(response)

    def _parse_response(self, response: httpx.Response) -> LwaTokenResponse:
        status = response.status_code
        if status in {400, 401, 403}:
            logger.warning("LWA token request rejected status=%s", status)
            raise SpApiAuthenticationError("Amazon LWA authentication failed.")
        if status == 429:
            logger.warning("LWA token request rate-limited status=%s", status)
            raise SpApiRateLimitedError("Amazon LWA rate limit reached.")
        if status >= 500:
            logger.warning("LWA token request failed status=%s", status)
            raise SpApiRequestFailedError("Amazon LWA token request failed.")
        if status != 200:
            logger.warning("LWA token request unexpected status=%s", status)
            raise SpApiRequestFailedError("Amazon LWA token request failed.")
        try:
            payload = response.json()
        except ValueError as exc:
            raise SpApiParseFailedError("Amazon LWA returned a non-JSON token response.") from exc
        try:
            return LwaTokenResponse.model_validate(payload)
        except ValidationError as exc:
            raise SpApiParseFailedError("Amazon LWA token response was malformed.") from exc

"""12B.1C.5 — LWA authorization-code exchange. Mocked HTTP only. No SP-API."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL
from app.amazon.lwa_token import (
    MISSING_OAUTH_CREDENTIALS_MESSAGE,
    MISSING_OAUTH_REDIRECT_URI_MESSAGE,
    AmazonLwaTokenService,
    oauth_application_credentials,
)
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiParseFailedError,
    SpApiRequestFailedError,
)

CLIENT_ID = "amzn1.application-oa2-client.oauth-test"
CLIENT_SECRET = "test-oauth-lwa-client-secret-value"
PRODUCTION_CLIENT_ID = "amzn1.application-oa2-client.production-test"
PRODUCTION_CLIENT_SECRET = "test-production-lwa-client-secret-value"
REDIRECT_URI = "https://app.example.test/api/v1/amazon/connection/callback"
AUTHORIZATION_CODE = "SplxlOexampleCallbackCode12B1C5"
ACCESS_TOKEN = "Atza|test-12b1c5-access-token"
REFRESH_TOKEN = "Atzr|test-12b1c5-refresh-token"


def _form(request: httpx.Request) -> dict[str, list[str]]:
    return parse_qs(request.content.decode("utf-8"))


def _service(transport: httpx.BaseTransport, **overrides) -> AmazonLwaTokenService:
    values = dict(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        redirect_uri=REDIRECT_URI,
        token_url=DEFAULT_LWA_TOKEN_URL,
        transport=transport,
    )
    values.update(overrides)
    return AmazonLwaTokenService(**values)


def _grant_payload() -> dict:
    return {
        "access_token": ACCESS_TOKEN,
        "refresh_token": REFRESH_TOKEN,
        "token_type": "bearer",
        "expires_in": 3600,
    }


def test_authorization_code_exchange_posts_expected_form() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert "sellingpartnerapi" not in str(request.url)
        assert "/sellers/" not in str(request.url)
        return httpx.Response(200, json=_grant_payload())

    grant = _service(httpx.MockTransport(handler)).exchange_authorization_code(
        SecretStr(AUTHORIZATION_CODE)
    )
    assert len(captured) == 1
    assert str(captured[0].url) == DEFAULT_LWA_TOKEN_URL
    form = _form(captured[0])
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == [AUTHORIZATION_CODE]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["client_id"] == [CLIENT_ID]
    assert form["client_secret"] == [CLIENT_SECRET]
    assert grant.token_type == "bearer"
    assert grant.expires_in == 3600
    assert grant.access_token.get_secret_value() == ACCESS_TOKEN
    assert grant.refresh_token.get_secret_value() == REFRESH_TOKEN
    dumped = grant.model_dump_json()
    assert ACCESS_TOKEN not in dumped
    assert REFRESH_TOKEN not in dumped
    assert CLIENT_SECRET not in dumped
    assert AUTHORIZATION_CODE not in dumped
    assert ACCESS_TOKEN not in repr(grant)
    assert REFRESH_TOKEN not in repr(grant)
    assert ACCESS_TOKEN not in repr(_service(httpx.MockTransport(handler)))


def test_invalid_authorization_code_is_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "invalid_grant"})

    with pytest.raises(SpApiAuthenticationError, match="Amazon LWA authentication failed") as exc_info:
        _service(httpx.MockTransport(handler)).exchange_authorization_code(
            SecretStr(AUTHORIZATION_CODE)
        )
    message = str(exc_info.value)
    assert AUTHORIZATION_CODE not in message
    assert CLIENT_SECRET not in message
    assert ACCESS_TOKEN not in message
    assert REFRESH_TOKEN not in message


def test_amazon_unavailable_and_malformed_responses(caplog) -> None:
    def unavailable(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "service_unavailable"})

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out")

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"token_type": "bearer", "expires_in": 3600})

    for handler, expected in (
        (unavailable, SpApiRequestFailedError),
        (timeout, SpApiRequestFailedError),
        (malformed, SpApiParseFailedError),
    ):
        with caplog.at_level("DEBUG"):
            with pytest.raises(expected) as exc_info:
                _service(httpx.MockTransport(handler)).exchange_authorization_code(
                    SecretStr(AUTHORIZATION_CODE)
                )
        message = str(exc_info.value)
        logs = "\n".join(record.getMessage() for record in caplog.records)
        assert AUTHORIZATION_CODE not in message
        assert CLIENT_SECRET not in message
        assert ACCESS_TOKEN not in message
        assert REFRESH_TOKEN not in message
        assert AUTHORIZATION_CODE not in logs
        assert CLIENT_SECRET not in logs
        caplog.clear()


def test_missing_application_credentials_and_redirect_uri() -> None:
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=_grant_payload()))
    with pytest.raises(SpApiConfigurationError, match=MISSING_OAUTH_CREDENTIALS_MESSAGE):
        _service(transport, client_id="", client_secret=CLIENT_SECRET).exchange_authorization_code(
            SecretStr(AUTHORIZATION_CODE)
        )
    with pytest.raises(SpApiConfigurationError, match=MISSING_OAUTH_REDIRECT_URI_MESSAGE):
        _service(transport, redirect_uri="").exchange_authorization_code(SecretStr(AUTHORIZATION_CODE))


def test_oauth_application_credentials_prefer_production() -> None:
    cfg = Settings(
        sp_api_lwa_client_id=SecretStr(CLIENT_ID),
        sp_api_lwa_client_secret=SecretStr(CLIENT_SECRET),
        sp_api_production_lwa_client_id=SecretStr(PRODUCTION_CLIENT_ID),
        sp_api_production_lwa_client_secret=SecretStr(PRODUCTION_CLIENT_SECRET),
        sp_api_oauth_redirect_uri=REDIRECT_URI,
        sp_api_lwa_token_url=DEFAULT_LWA_TOKEN_URL,
    )
    client_id, client_secret = oauth_application_credentials(cfg)
    assert client_id.get_secret_value() == PRODUCTION_CLIENT_ID
    assert client_secret.get_secret_value() == PRODUCTION_CLIENT_SECRET

    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_grant_payload())

    service = AmazonLwaTokenService.from_settings(cfg, transport=httpx.MockTransport(handler))
    service.exchange_authorization_code(SecretStr(AUTHORIZATION_CODE))
    form = _form(captured[0])
    assert form["client_id"] == [PRODUCTION_CLIENT_ID]
    assert form["grant_type"] == ["authorization_code"]


def test_from_settings_requires_redirect_uri() -> None:
    cfg = Settings(
        sp_api_lwa_client_id=SecretStr(CLIENT_ID),
        sp_api_lwa_client_secret=SecretStr(CLIENT_SECRET),
        sp_api_oauth_redirect_uri="",
    )
    with pytest.raises(SpApiConfigurationError, match=MISSING_OAUTH_REDIRECT_URI_MESSAGE):
        AmazonLwaTokenService.from_settings(cfg)

"""SP-API sandbox client resolves seller refresh tokens through SecretProvider."""

from __future__ import annotations

import inspect
import json
import logging
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.sandbox import AmazonSpApiSandboxClient, resolve_sandbox_refresh_token
from app.amazon.secrets import (
    DevelopmentSecretProvider,
    development_sandbox_token_reference,
    reset_secret_provider,
)
from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID, Settings
from app.core.exceptions import SpApiConfigurationError

FIXTURES = Path(__file__).parent / "fixtures" / "sp_api"
CLIENT_ID = "amzn1.application-oa2-client.test"
CLIENT_SECRET = "test-lwa-client-secret-value"
REFRESH_TOKEN = "Atzr|test-sandbox-refresh-token"
ACCESS_TOKEN = "Atza|test-sandbox-access-token"


def _sandbox_payload() -> dict:
    return json.loads((FIXTURES / "get_marketplace_participations.sandbox.json").read_text(encoding="utf-8"))


def _lwa_success() -> dict:
    return {"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 3600}


def _settings(*, refresh_token: str | None = REFRESH_TOKEN) -> Settings:
    token = SecretStr(refresh_token) if refresh_token is not None else None
    return Settings(
        sp_api_lwa_client_id=SecretStr(CLIENT_ID),
        sp_api_lwa_client_secret=SecretStr(CLIENT_SECRET),
        sp_api_sandbox_refresh_token=token,
        default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        sp_api_region="eu",
    )


def _provider(*, token: str | None = REFRESH_TOKEN) -> DevelopmentSecretProvider:
    sandbox = SecretStr(token) if token is not None else None
    return DevelopmentSecretProvider(
        sandbox_refresh_token=sandbox,
        default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
    )


def _assert_no_secrets(text: str) -> None:
    assert REFRESH_TOKEN not in text
    assert ACCESS_TOKEN not in text
    assert CLIENT_SECRET not in text
    assert "Atzr|" not in text
    assert "Atza|" not in text


@pytest.mark.asyncio
async def test_sandbox_client_resolves_refresh_token_through_secret_provider() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        return httpx.Response(200, json=_sandbox_payload())

    client = AmazonSpApiSandboxClient(
        settings=_settings(refresh_token=None),
        secret_provider=_provider(),
        transport=httpx.MockTransport(handler),
        region="eu",
    )
    result = await client.get_marketplace_participations()
    form = parse_qs(captured[0].content.decode("utf-8"))
    assert form["refresh_token"] == [REFRESH_TOKEN]
    assert result.participation_count == 1
    assert ACCESS_TOKEN not in result.model_dump_json()


def test_development_secret_provider_sandbox_fallback_still_works() -> None:
    token = resolve_sandbox_refresh_token(
        secret_provider=_provider(),
        organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
    )
    assert isinstance(token, SecretStr)
    assert token.get_secret_value() == REFRESH_TOKEN
    assert token.get_secret_value() == _provider().get_secret(
        development_sandbox_token_reference(DEFAULT_DEVELOPMENT_ORGANIZATION_ID)
    ).get_secret_value()
    _assert_no_secrets(repr(token))


def test_sandbox_client_credential_path_does_not_read_environment() -> None:
    import app.amazon.sandbox as sandbox_module

    source = inspect.getsource(sandbox_module)
    assert "sp_api_sandbox_refresh_token" not in source
    assert "SP_API_SANDBOX_REFRESH_TOKEN" not in source
    assert "os.getenv" not in source
    assert "os.environ" not in source
    settings = _settings(refresh_token=REFRESH_TOKEN)
    provider = _provider(token=None)
    with pytest.raises(SpApiConfigurationError) as exc_info:
        AmazonSpApiSandboxClient(settings=settings, secret_provider=provider)
    _assert_no_secrets(str(exc_info.value))


def test_missing_secret_raises_safe_configuration_error() -> None:
    with pytest.raises(SpApiConfigurationError) as exc_info:
        resolve_sandbox_refresh_token(
            secret_provider=_provider(token=None),
            organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID,
        )
    message = str(exc_info.value)
    _assert_no_secrets(message)
    assert "SP_API_LWA_CLIENT_ID" in message


@pytest.mark.asyncio
async def test_secret_values_are_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        return httpx.Response(200, json=_sandbox_payload())

    with caplog.at_level(logging.DEBUG):
        client = AmazonSpApiSandboxClient(
            settings=_settings(refresh_token=None),
            secret_provider=_provider(),
            transport=httpx.MockTransport(handler),
            region="eu",
        )
        await client.get_marketplace_participations()
        with pytest.raises(SpApiConfigurationError):
            AmazonSpApiSandboxClient(
                settings=_settings(),
                secret_provider=_provider(token=None),
            )
    _assert_no_secrets(caplog.text)
    _assert_no_secrets(repr(client))


def test_injected_lwa_bypasses_secret_provider() -> None:
    reset_secret_provider()
    from app.amazon.lwa import LwaClient

    lwa = LwaClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=REFRESH_TOKEN,
    )
    client = AmazonSpApiSandboxClient(
        settings=_settings(refresh_token=None),
        lwa=lwa,
        secret_provider=_provider(token=None),
    )
    assert repr(client) == "AmazonSpApiSandboxClient()"

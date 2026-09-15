from __future__ import annotations

import logging

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.ads_client import (
    ADS_API_BACKEND_DISABLED,
    ADS_API_BACKEND_HTTP,
    ADS_API_BACKEND_MOCK,
    AdsRequestContext,
    DisabledAmazonAdsApiClient,
    HttpAmazonAdsApiClient,
    MockAmazonAdsApiClient,
    build_amazon_ads_api_client,
    resolve_ads_api_backend,
)
from app.amazon.ads_connection import AmazonAdsConnectionService, get_amazon_ads_connection_service
from app.core.config import Settings
from app.core.exceptions import AdsConfigurationError


def _ctx() -> AdsRequestContext:
    return AdsRequestContext(
        access_token=SecretStr("Atza|fake"), client_id="client", region="NA", profile_id="1", correlation_id="c"
    )


def test_default_settings_resolve_to_disabled_backend() -> None:
    assert resolve_ads_api_backend(Settings(database_url="sqlite://")) == ADS_API_BACKEND_DISABLED


def test_default_client_is_disabled_never_mock() -> None:
    """The core "cannot instantiate a mock accidentally" guarantee: an
    unconfigured Settings object must never yield a client that returns
    canned data — it must yield one that refuses."""
    client = build_amazon_ads_api_client(Settings(database_url="sqlite://"))
    assert isinstance(client, DisabledAmazonAdsApiClient)
    assert not isinstance(client, MockAmazonAdsApiClient)


def test_connection_service_default_dependency_never_defaults_to_mock() -> None:
    """`get_amazon_ads_connection_service` is the real FastAPI dependency
    factory — its default-constructed service must resolve the same
    fail-closed client, not silently fall back to mock data for a real
    (if unconfigured) deployment."""
    service = get_amazon_ads_connection_service()
    assert isinstance(service._ads_client, DisabledAmazonAdsApiClient)  # noqa: SLF001 - white-box guarantee check


@pytest.mark.parametrize(
    ("backend", "expected_type"),
    [
        (ADS_API_BACKEND_DISABLED, DisabledAmazonAdsApiClient),
        (ADS_API_BACKEND_MOCK, MockAmazonAdsApiClient),
        (ADS_API_BACKEND_HTTP, HttpAmazonAdsApiClient),
    ],
)
def test_each_recognized_backend_constructs_its_own_type(backend, expected_type) -> None:
    client = build_amazon_ads_api_client(Settings(ads_api_backend=backend, database_url="sqlite://"))
    assert isinstance(client, expected_type)


def test_unrecognized_backend_raises_rather_than_falling_back_to_mock() -> None:
    settings = Settings(ads_api_backend="production", database_url="sqlite://")  # a plausible typo, not a real value
    with pytest.raises(AdsConfigurationError):
        build_amazon_ads_api_client(settings)


@pytest.mark.asyncio
async def test_disabled_client_refuses_every_method_with_no_network_call() -> None:
    client = DisabledAmazonAdsApiClient()
    ctx = _ctx()
    with pytest.raises(AdsConfigurationError):
        await client.list_profiles(ctx)
    with pytest.raises(AdsConfigurationError):
        await client.list_campaigns(ctx)
    with pytest.raises(AdsConfigurationError):
        await client.create_report(ctx, configuration=None)
    with pytest.raises(AdsConfigurationError):
        await client.get_report_status(ctx, "r-1")
    with pytest.raises(AdsConfigurationError):
        await client.download_report(ctx, "https://example.invalid", max_bytes=100)
    # Structural guarantee, not just behavioral: this client holds no
    # transport/httpx client attribute at all, so there is no path by
    # which it could ever open a socket regardless of future edits.
    assert not hasattr(client, "_transport")
    assert not hasattr(client, "_timeout")


@pytest.mark.asyncio
async def test_disabled_authorization_callback_refuses_clearly_after_token_exchange() -> None:
    """End-to-end: even after a (mocked) successful LWA token exchange,
    profile discovery through a disabled backend must surface as a clear
    configuration error, never a silent "0 profiles found" success."""
    from app.amazon.secrets import DevelopmentSecretProvider
    from app.core.config import DEFAULT_DEVELOPMENT_ORGANIZATION_ID
    from app.amazon.models import LwaAuthorizationGrant
    from app.persistence.database import reset_persistence

    reset_persistence()
    try:
        settings = Settings(
            ads_lwa_client_id=SecretStr("client"),
            ads_lwa_client_secret=SecretStr("secret"),
            ads_oauth_redirect_uri="https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/callback",
            ads_api_backend=ADS_API_BACKEND_DISABLED,
            database_url="sqlite://",
        )

        class _FakeLwa:
            def exchange_authorization_code(self, code):
                return LwaAuthorizationGrant(
                    access_token=SecretStr("Atza|x"),
                    refresh_token=SecretStr("Atzr|x"),
                    token_type="bearer",
                    expires_in=3600,
                )

        service = AmazonAdsConnectionService(
            settings=settings,
            secret_provider=DevelopmentSecretProvider(default_organization_id=DEFAULT_DEVELOPMENT_ORGANIZATION_ID),
            lwa_token_service=_FakeLwa(),
        )
        assert isinstance(service._ads_client, DisabledAmazonAdsApiClient)  # noqa: SLF001

        start = service.start_authorization()
        import urllib.parse

        raw_state = urllib.parse.unquote(start.authorization_url.split("state=")[1].split("&")[0])

        with pytest.raises(AdsConfigurationError):
            await service.complete_authorization_callback(state=raw_state, code="auth-code", error=None)
    finally:
        reset_persistence()


def test_http_backend_never_logs_secrets(caplog: pytest.LogCaptureFixture) -> None:
    """A real HTTP-shaped request (fake transport, no real socket) must
    never leak the bearer token or client id into logs — only the
    redacted headers."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"campaigns": [], "nextToken": None})

    transport = httpx.MockTransport(handler)
    client = HttpAmazonAdsApiClient(transport=transport)
    ctx = AdsRequestContext(
        access_token=SecretStr("Atza|super-secret-access-token"),
        client_id="amzn1.application-oa2-client.super-secret-id",
        region="NA",
        profile_id="123",
        correlation_id="corr-1",
    )
    with caplog.at_level(logging.INFO):
        import asyncio

        asyncio.run(client.list_campaigns(ctx))

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "super-secret-access-token" not in log_text
    assert "super-secret-id" not in log_text
    assert "[redacted]" in log_text

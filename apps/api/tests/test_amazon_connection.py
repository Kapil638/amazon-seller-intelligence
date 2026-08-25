from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import SecretStr, ValidationError

from app.amazon.common import public_model_dump, reject_secret_fields
from app.amazon.connection import AmazonConnectionService, get_amazon_connection_service
from app.amazon.models import (
    MarketplaceParticipationsSandboxResult,
    SpApiSandboxProvenance,
)
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiRequestFailedError,
)
from app.main import app

CONNECTION_URL = "/api/v1/amazon/connection"
TEST_URL = "/api/v1/amazon/connection/test"
SECRET_MARKERS = (
    "Atza|",
    "Atzr|",
    "client_secret",
    "refresh_token",
    "access_token",
    "x-amz-access-token",
    "Authorization",
    "test-lwa-client-secret-value",
)


class _BoomChecker:
    def __init__(self) -> None:
        self.calls = 0

    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult:
        self.calls += 1
        raise AssertionError("Amazon SP-API must not be called in this test.")


class _OkChecker:
    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult:
        return MarketplaceParticipationsSandboxResult(
            payload=[],
            participation_count=0,
            provenance=SpApiSandboxProvenance(
                operation=GET_MARKETPLACE_PARTICIPATIONS,
                region="eu",
                endpoint_host="sandbox.sellingpartnerapi-eu.amazon.com",
                fetched_at=datetime.now(UTC),
                http_status=200,
                api_model_version="sellers-api-model/v1",
            ),
        )


class _AuthFailChecker:
    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult:
        raise SpApiAuthenticationError("Amazon SP-API sandbox authentication failed.")


class _UnavailableChecker:
    async def get_marketplace_participations(self) -> MarketplaceParticipationsSandboxResult:
        raise SpApiRequestFailedError("Could not reach Amazon SP-API sandbox.")


def _configured_settings() -> Settings:
    return Settings(
        sp_api_lwa_client_id=SecretStr("amzn1.application-oa2-client.test"),
        sp_api_lwa_client_secret=SecretStr("test-lwa-client-secret-value"),
        sp_api_sandbox_refresh_token=SecretStr("Atzr|test-sandbox-refresh-token"),
        sp_api_application_name="EWise",
        default_marketplace="amazon.in",
    )


def _assert_public(payload: object) -> None:
    text = str(payload)
    for marker in SECRET_MARKERS:
        assert marker not in text
    reject_secret_fields(payload)


def test_overview_is_config_based_and_does_not_call_amazon() -> None:
    boom = _BoomChecker()
    service = AmazonConnectionService(sandbox_client_factory=lambda: boom)
    overview = service.overview()
    assert overview.status == "NOT_CONNECTED"
    assert overview.provider == "SP_API"
    assert overview.environment == "PRODUCTION"
    assert overview.marketplace == (
        "amazon.com" if overview.region.lower() in {"na", "us"} else "amazon.in"
    )
    assert overview.application == "EWise"
    assert overview.credentials_configured is False
    assert overview.last_test_at is None
    assert overview.ads_api.status == "NOT_CONNECTED"
    assert overview.ads_api.provider == "ADS_API"
    assert boom.calls == 0
    _assert_public(public_model_dump(overview))


@pytest.mark.asyncio
async def test_missing_credentials_does_not_call_amazon() -> None:
    boom = _BoomChecker()
    service = AmazonConnectionService(sandbox_client_factory=lambda: boom)
    result = await service.test_sp_api()
    assert result.status == "NOT_CONNECTED"
    assert result.provider == "SP_API"
    assert result.environment == "SANDBOX"
    assert result.operation == GET_MARKETPLACE_PARTICIPATIONS
    assert "SP-API sandbox is not configured" in (result.message or "")
    assert boom.calls == 0
    dumped = public_model_dump(result)
    _assert_public(dumped)
    assert "access_token" not in dumped
    assert "refresh_token" not in dumped
    assert "client_secret" not in dumped


@pytest.mark.asyncio
async def test_mocked_sandbox_success_returns_sanitized_connected_status() -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    result = await service.test_sp_api()
    assert result.status == "CONNECTED"
    assert result.provider == "SP_API"
    assert result.environment == "SANDBOX"
    assert result.marketplace == "amazon.in"
    assert result.operation == GET_MARKETPLACE_PARTICIPATIONS
    assert result.message is None
    dumped = public_model_dump(result)
    _assert_public(dumped)
    assert set(dumped) == {
        "status",
        "provider",
        "environment",
        "marketplace",
        "operation",
        "tested_at",
        "message",
    }


@pytest.mark.asyncio
async def test_authentication_failure_returns_failed_status() -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_AuthFailChecker,
    )
    result = await service.test_sp_api()
    assert result.status == "FAILED"
    assert result.operation == GET_MARKETPLACE_PARTICIPATIONS
    assert "authentication failed" in (result.message or "").lower()
    _assert_public(public_model_dump(result))


@pytest.mark.asyncio
async def test_sandbox_unavailable_returns_failed_status() -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_UnavailableChecker,
    )
    result = await service.test_sp_api()
    assert result.status == "FAILED"
    assert "could not reach" in (result.message or "").lower()
    _assert_public(public_model_dump(result))


def test_public_models_forbid_secret_fields() -> None:
    with pytest.raises(ValidationError):
        from app.amazon.connection import AmazonConnectionTestResult

        AmazonConnectionTestResult.model_validate(
            {
                "status": "CONNECTED",
                "provider": "SP_API",
                "environment": "SANDBOX",
                "marketplace": "amazon.in",
                "operation": GET_MARKETPLACE_PARTICIPATIONS,
                "tested_at": datetime.now(UTC),
                "access_token": "Atza|leaked",
            }
        )


def test_get_connection_does_not_call_amazon(client) -> None:
    boom = _BoomChecker()
    service = AmazonConnectionService(sandbox_client_factory=lambda: boom)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_CONNECTED"
    assert body["provider"] == "SP_API"
    assert body["environment"] == "PRODUCTION"
    assert body["marketplace"] == (
        "amazon.com" if str(body["region"]).lower() in {"na", "us"} else "amazon.in"
    )
    assert body["application"] == "EWise"
    assert body["credentials_configured"] is False
    assert body["ads_api"]["status"] == "NOT_CONNECTED"
    assert boom.calls == 0
    _assert_public(body)


def test_post_test_missing_credentials(client) -> None:
    boom = _BoomChecker()
    service = AmazonConnectionService(sandbox_client_factory=lambda: boom)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post(TEST_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_CONNECTED"
    assert body["operation"] == GET_MARKETPLACE_PARTICIPATIONS
    assert boom.calls == 0
    _assert_public(body)


def test_post_test_mocked_success(client) -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post(TEST_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CONNECTED"
    assert body["provider"] == "SP_API"
    assert body["environment"] == "SANDBOX"
    assert body["marketplace"] == "amazon.in"
    assert body["operation"] == GET_MARKETPLACE_PARTICIPATIONS
    assert body["tested_at"]
    _assert_public(body)


def test_post_test_authentication_failure(client) -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_AuthFailChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post(TEST_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    _assert_public(body)


def test_post_test_sandbox_unavailable(client) -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_UnavailableChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post(TEST_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    _assert_public(body)

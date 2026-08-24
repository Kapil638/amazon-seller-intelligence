"""12B.1A.4 — Amazon connection HTTP API. No OAuth, SecretProvider, or frontend."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import func, select

from app.amazon.common import reject_secret_fields
from app.amazon.connection import (
    AmazonConnectionOverview,
    AmazonConnectionService,
    AmazonConnectionTestResult,
    get_amazon_connection_service,
)
from app.amazon.models import MarketplaceParticipationsSandboxResult, SpApiSandboxProvenance
from app.amazon.sandbox import GET_MARKETPLACE_PARTICIPATIONS
from app.core.config import Settings
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonConnection, Organization
from app.persistence.repositories import AmazonConnectionRepository

CONNECTION_URL = "/api/v1/amazon/connection"
TEST_URL = "/api/v1/amazon/connection/test"
SECRET_MARKERS = (
    "Atza|",
    "Atzr|",
    "client_secret",
    "refresh_token",
    "access_token",
    "x-amz-access-token",
    "token_reference",
    "client_id",
)
FORBIDDEN_SCHEMA_FIELDS = (
    "token_reference",
    "refresh_token",
    "access_token",
    "client_secret",
    "client_id",
)


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


def _configured_settings() -> Settings:
    return Settings(
        sp_api_lwa_client_id=SecretStr("amzn1.application-oa2-client.test"),
        sp_api_lwa_client_secret=SecretStr("test-lwa-client-secret-value"),
        sp_api_sandbox_refresh_token=SecretStr("Atzr|test-sandbox-refresh-token"),
        sp_api_application_name="EWise",
        default_marketplace="amazon.in",
        sp_api_region="eu",
    )


def _count_connections() -> int:
    with session_scope() as session:
        return int(session.scalar(select(func.count()).select_from(AmazonConnection)) or 0)


def _assert_public(payload: object) -> None:
    text = str(payload)
    for marker in SECRET_MARKERS:
        assert marker not in text
    reject_secret_fields(payload)
    if isinstance(payload, dict):
        for name in FORBIDDEN_SCHEMA_FIELDS:
            assert name not in payload


def test_get_overview_returns_persisted_metadata(client) -> None:
    AmazonConnectionService().create_connection(
        provider="SP_API",
        environment="SANDBOX",
        region="eu",
        selling_partner_id="A1SELLERID",
    )
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_CONNECTED"
    assert body["connection_status"] == "not_connected"
    assert body["persisted"] is True
    assert body["provider"] == "SP_API"
    assert body["environment"] == "SANDBOX"
    assert body["region"] == "eu"
    assert body["selling_partner_id"] == "A1SELLERID"
    assert body["marketplace"] == "amazon.in"
    assert body["application"] == "EWise"
    assert body["ads_api"]["status"] == "NOT_CONNECTED"
    assert "token_reference" not in body
    _assert_public(body)


def test_get_overview_falls_back_when_no_row(client) -> None:
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "NOT_CONNECTED"
    assert body["connection_status"] == "not_connected"
    assert body["persisted"] is False
    assert body["provider"] == "SP_API"
    assert body["environment"] == "SANDBOX"
    assert body["selling_partner_id"] is None
    assert body["last_test_at"] is None
    _assert_public(body)


def test_get_fallback_does_not_create_database_records(client) -> None:
    assert _count_connections() == 0
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    assert response.json()["persisted"] is False
    assert _count_connections() == 0


def test_post_connection_test_returns_sandbox_result(client) -> None:
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post(TEST_URL, json={})
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CONNECTED"
    assert body["provider"] == "SP_API"
    assert body["environment"] == "SANDBOX"
    assert body["operation"] == GET_MARKETPLACE_PARTICIPATIONS
    assert body["tested_at"]
    _assert_public(body)


def test_post_connection_test_does_not_persist_connected(client) -> None:
    AmazonConnectionService().create_connection(
        provider="SP_API",
        environment="SANDBOX",
        region="eu",
    )
    service = AmazonConnectionService(
        settings=_configured_settings(),
        sandbox_client_factory=_OkChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        tested = client.post(TEST_URL, json={})
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert tested.status_code == 200
    assert tested.json()["status"] == "CONNECTED"
    body = overview.json()
    assert body["persisted"] is True
    assert body["status"] == "NOT_CONNECTED"
    assert body["connection_status"] == "not_connected"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert stored is not None
        assert stored.status == "not_connected"
        assert stored.token_reference is None
    _assert_public(body)


def test_get_cannot_read_other_organization_connection(client) -> None:
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        AmazonConnectionRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            selling_partner_id="B2OTHER",
        )
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is False
    assert body["selling_partner_id"] is None
    assert body["organization_id"] == str(current_organization_id())
    assert body["organization_id"] != str(other_org)
    _assert_public(body)


def test_secret_fields_cannot_be_returned(client) -> None:
    with session_scope() as session:
        session.add(
            AmazonConnection(
                organization_id=current_organization_id(),
                provider="SP_API",
                environment="SANDBOX",
                region="eu",
                status="not_connected",
                token_reference="asi:dev:must-not-appear",
            )
        )
    response = client.get(CONNECTION_URL)
    assert response.status_code == 200
    body = response.json()
    assert body["persisted"] is True
    assert "token_reference" not in body
    assert "asi:dev:must-not-appear" not in str(body)
    _assert_public(body)

    overview_schema = AmazonConnectionOverview.model_json_schema()
    test_schema = AmazonConnectionTestResult.model_json_schema()
    for name in FORBIDDEN_SCHEMA_FIELDS:
        assert name not in overview_schema.get("properties", {})
        assert name not in test_schema.get("properties", {})

    rejected = client.post(TEST_URL, json={"refresh_token": "x"})
    assert rejected.status_code == 400

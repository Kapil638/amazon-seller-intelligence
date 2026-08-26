"""12B.1D — Seller connection validation via SP-API Sellers. No ingest."""

from __future__ import annotations

import ast
import inspect
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.connection import AmazonConnectionService, get_amazon_connection_service
from app.amazon.secrets import (
    DevelopmentSecretProvider,
    SecretAccessError,
    build_asi_secret_reference,
)
from app.amazon.seller_validation import AmazonSellerValidationService
from app.amazon.sellers import MARKETPLACE_PARTICIPATIONS_PATH, sp_api_base_url
from app.core.config import Settings
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization
from app.persistence.repositories import AmazonConnectionRepository

FIXTURES = Path(__file__).parent / "fixtures" / "sp_api"
CLIENT_ID = "amzn1.application-oa2-client.seller-validation"
CLIENT_SECRET = "test-seller-validation-lwa-secret"
REFRESH_TOKEN = "Atzr|test-12b1d-seller-refresh-token"
ACCESS_TOKEN = "Atza|test-12b1d-seller-access-token"
SELLING_PARTNER_ID = "A3FHEXAMPLEYWS"
CONNECTION_URL = "/api/v1/amazon/connection"
TEST_URL = "/api/v1/amazon/connection/test"
INGEST_MARKERS = ("/listings/", "/orders/", "/fba/", "/reports/", "/finances/")


def _participations_payload(*, selling_partner_id: str | None = SELLING_PARTNER_ID) -> dict:
    body = json.loads((FIXTURES / "get_marketplace_participations.sandbox.json").read_text(encoding="utf-8"))
    if selling_partner_id:
        body["sellingPartnerId"] = selling_partner_id
    return body


def _settings(**overrides) -> Settings:
    values = dict(
        sp_api_lwa_client_id=SecretStr(CLIENT_ID),
        sp_api_lwa_client_secret=SecretStr(CLIENT_SECRET),
        sp_api_lwa_token_url="https://api.amazon.com/auth/o2/token",
        sp_api_region="eu",
        default_marketplace="amazon.in",
        sp_api_application_name="EWise",
        cors_origins=["http://localhost:3000"],
    )
    values.update(overrides)
    return Settings(**values)


def _lwa_success() -> dict:
    return {"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 3600}


def _mock_transport(
    *,
    sellers_status: int = 200,
    sellers_json: dict | None = None,
    lwa_status: int = 200,
    timeout: bool = False,
) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for marker in INGEST_MARKERS:
            assert marker not in url
        if request.url.host == "api.amazon.com":
            if lwa_status != 200:
                return httpx.Response(lwa_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json=_lwa_success())
        if timeout:
            raise httpx.TimeoutException("timed out")
        assert request.url.path == MARKETPLACE_PARTICIPATIONS_PATH
        return httpx.Response(sellers_status, json=sellers_json or _participations_payload())

    return httpx.MockTransport(handler)


def _seed_pending_validation(
    *,
    provider: DevelopmentSecretProvider,
    status: str = "pending_validation",
    organization_id=None,
    environment: str = "SANDBOX",
    region: str = "eu",
) -> tuple[object, str]:
    org_id = organization_id or current_organization_id()
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        row = repo.create(
            organization_id=org_id,
            provider="SP_API",
            environment=environment,
            region=region,
            status=status,
        )
        reference = build_asi_secret_reference(
            provider="SP_API",
            environment=environment,
            organization_id=org_id,
            connection_id=row.id,
        )
        repo.bind_token_reference(org_id, row.id, reference)
        repo.update(org_id, row.id, status=status)
        connection_id = row.id
    provider.put_secret(reference, SecretStr(REFRESH_TOKEN))
    return connection_id, reference


class _BoomSandbox:
    def __init__(self) -> None:
        raise AssertionError("seller validation must not use the sandbox env-token client")


def _service(
    *,
    secrets: DevelopmentSecretProvider | None = None,
    transport: httpx.BaseTransport | None = None,
    validator=None,
) -> tuple[AmazonConnectionService, DevelopmentSecretProvider]:
    provider = secrets or DevelopmentSecretProvider()
    service = AmazonConnectionService(
        settings=_settings(),
        secret_provider=provider,
        seller_validator=validator
        or AmazonSellerValidationService(
            settings=_settings(),
            secret_provider=provider,
            transport=transport or _mock_transport(),
        ),
        sandbox_client_factory=_BoomSandbox,
    )
    return service, provider


def _assert_no_secrets(*values: object) -> None:
    for value in values:
        text = str(value)
        assert REFRESH_TOKEN not in text
        assert ACCESS_TOKEN not in text
        assert CLIENT_SECRET not in text
        assert "Atzr|" not in text
        assert "Atza|" not in text


def test_sp_api_base_url_uses_connection_environment() -> None:
    assert sp_api_base_url(region="eu", environment="SANDBOX") == (
        "https://sandbox.sellingpartnerapi-eu.amazon.com"
    )
    assert sp_api_base_url(region="eu", environment="PRODUCTION") == (
        "https://sellingpartnerapi-eu.amazon.com"
    )


@pytest.mark.asyncio
async def test_successful_seller_validation_marks_connected(caplog) -> None:
    service, provider = _service()
    connection_id, reference = _seed_pending_validation(
        provider=provider,
        environment="PRODUCTION",
        region="na",
    )
    with caplog.at_level("DEBUG"):
        result = await service.validate_seller_connection(environment="PRODUCTION")
    assert result.valid is True
    assert result.connection_status == "connected"
    assert result.selling_partner_id == SELLING_PARTNER_ID
    assert result.marketplaces[0].marketplace_id == "ATVPDKIKX0DER"
    assert result.marketplaces[0].country_code == "US"
    _assert_no_secrets(result, caplog.text)
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(
            current_organization_id(), connection_id
        )
        assert stored is not None
        assert stored.status == "connected"
        assert stored.status != "pending_validation"
        assert stored.selling_partner_id == SELLING_PARTNER_ID
        assert stored.token_reference == reference
        assert stored.last_successful_validation_at is not None
        assert stored.last_error_code is None
        for value in (getattr(stored, column) for column in stored.__table__.c.keys()):
            _assert_no_secrets(value)
    assert provider.get_secret(reference).get_secret_value() == REFRESH_TOKEN
    overview = service.overview()
    assert overview.connection_status == "connected"
    assert overview.status == "NOT_CONNECTED"
    assert overview.selling_partner_id == SELLING_PARTNER_ID
    assert "token_reference" not in overview.model_dump()
    _assert_no_secrets(overview)


@pytest.mark.asyncio
async def test_identity_conflict_between_callback_and_validation_is_not_reconciled() -> None:
    service, provider = _service()
    connection_id, reference = _seed_pending_validation(provider=provider)
    with session_scope() as session:
        AmazonConnectionRepository(session).update(
            current_organization_id(),
            connection_id,
            selling_partner_id="CALLBACKCAPTUREDID",
        )
    result = await service.validate_seller_connection()
    assert result.valid is False
    assert result.reason == "identity_conflict"
    assert result.connection_status == "pending_validation"
    assert result.selling_partner_id is None
    _assert_no_secrets(result)
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(
            current_organization_id(), connection_id
        )
        assert stored is not None
        # Last known-good identity (captured earlier, e.g. at OAuth callback) is
        # preserved; the disagreeing Sellers API value is not reconciled in.
        assert stored.selling_partner_id == "CALLBACKCAPTUREDID"
        assert stored.status == "pending_validation"
        assert stored.status != "connected"
        assert stored.last_error_code == "identity_conflict"
        assert stored.token_reference == reference


@pytest.mark.asyncio
async def test_test_connection_uses_secret_provider_and_sellers_path() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        for marker in INGEST_MARKERS:
            assert marker not in str(request.url)
        if request.url.host == "api.amazon.com":
            return httpx.Response(200, json=_lwa_success())
        assert request.url.host == "sandbox.sellingpartnerapi-eu.amazon.com"
        assert request.url.path == MARKETPLACE_PARTICIPATIONS_PATH
        assert request.headers["x-amz-access-token"] == ACCESS_TOKEN
        return httpx.Response(200, json=_participations_payload())

    provider = DevelopmentSecretProvider()
    service, _ = _service(secrets=provider, transport=httpx.MockTransport(handler))
    _seed_pending_validation(provider=provider)
    result = await service.test_sp_api()
    assert result.status == "CONNECTED"
    assert result.operation == "getMarketplaceParticipations"
    hosts = [request.url.host for request in captured]
    assert "api.amazon.com" in hosts
    assert "sandbox.sellingpartnerapi-eu.amazon.com" in hosts
    assert all("/listings/" not in str(request.url) for request in captured)
    _assert_no_secrets(result)


@pytest.mark.asyncio
async def test_invalid_refresh_token_moves_to_error() -> None:
    service, provider = _service(transport=_mock_transport(lwa_status=401))
    connection_id, reference = _seed_pending_validation(provider=provider)
    result = await service.validate_seller_connection()
    assert result.valid is False
    assert result.connection_status == "error"
    assert result.reason == "requires_reauth"
    _assert_no_secrets(result)
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(
            current_organization_id(), connection_id
        )
        assert stored is not None
        assert stored.status == "error"
        assert stored.token_reference is None
        assert stored.last_error_code == "requires_reauth"
    assert provider.exists(reference) is False


@pytest.mark.asyncio
async def test_sp_api_failure_keeps_secret_and_marks_degraded() -> None:
    service, provider = _service(transport=_mock_transport(sellers_status=503))
    connection_id, reference = _seed_pending_validation(provider=provider)
    result = await service.validate_seller_connection()
    assert result.valid is False
    assert result.connection_status == "degraded"
    assert result.reason == "sp_api_unavailable"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(
            current_organization_id(), connection_id
        )
        assert stored is not None
        assert stored.status == "degraded"
        assert stored.status != "connected"
        assert stored.token_reference == reference
        assert stored.last_error_code == "sp_api_unavailable"
    assert provider.get_secret(reference).get_secret_value() == REFRESH_TOKEN


@pytest.mark.asyncio
async def test_secret_provider_failure_stays_pending_validation() -> None:
    class _FailingSecrets(DevelopmentSecretProvider):
        def get_secret(self, reference: str) -> SecretStr:
            raise SecretAccessError()

    seeded = DevelopmentSecretProvider()
    connection_id, reference = _seed_pending_validation(provider=seeded)
    provider = _FailingSecrets()
    service, _ = _service(secrets=provider)
    result = await service.validate_seller_connection()
    assert result.valid is False
    assert result.connection_status == "pending_validation"
    assert result.reason == "secret_access_failed"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(
            current_organization_id(), connection_id
        )
        assert stored is not None
        assert stored.status == "pending_validation"
        assert stored.status != "connected"
        assert stored.token_reference == reference
        assert stored.last_error_code == "secret_access_failed"


@pytest.mark.asyncio
async def test_organization_cannot_validate_another_org_token() -> None:
    other_org = uuid4()
    provider = DevelopmentSecretProvider()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
    connection_id, reference = _seed_pending_validation(
        provider=provider, organization_id=other_org
    )
    service, _ = _service(secrets=provider)
    result = await service.validate_seller_connection()
    assert result.valid is False
    assert result.reason == "not_ready"
    validator = AmazonSellerValidationService(
        settings=_settings(),
        secret_provider=provider,
        transport=_mock_transport(),
    )
    with session_scope() as session:
        foreign = AmazonConnectionRepository(session).get_by_id(other_org, connection_id)
        assert foreign is not None
        rejected = await validator.validate(
            organization_id=current_organization_id(),
            connection=foreign,
        )
    assert rejected.valid is False
    assert rejected.reason == "secret_reference_invalid"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(other_org, connection_id)
        assert stored is not None
        assert stored.status == "pending_validation"
        assert stored.token_reference == reference


@pytest.mark.asyncio
async def test_missing_marketplace_participation_does_not_connect() -> None:
    empty = {"payload": [], "sellingPartnerId": SELLING_PARTNER_ID}
    service, provider = _service(transport=_mock_transport(sellers_json=empty))
    connection_id, reference = _seed_pending_validation(provider=provider)
    result = await service.validate_seller_connection()
    assert result.valid is False
    assert result.reason == "seller_identity_unavailable"
    with session_scope() as session:
        stored = AmazonConnectionRepository(session).get_by_id(
            current_organization_id(), connection_id
        )
        assert stored is not None
        assert stored.status == "pending_validation"
        assert stored.token_reference == reference


def test_validation_modules_do_not_import_ingest_or_intelligence() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "amazon"
    forbidden = {
        "app.copilot",
        "app.services.profit_modeling_service",
        "app.services.advertising_modeling_service",
        "app.services.listing_analysis_v2_service",
    }
    imported: set[str] = set()
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    for name in forbidden:
        assert name not in imported
    sellers_source = inspect.getsource(AmazonSellerValidationService.validate)
    assert "get_marketplace_participations" in sellers_source
    assert "listings" not in sellers_source
    assert "orders" not in sellers_source
    assert "inventory" not in sellers_source
    assert "reports" not in sellers_source


def test_post_test_validates_pending_seller_grant(client) -> None:
    service, provider = _service()
    _seed_pending_validation(provider=provider, environment="PRODUCTION", region="na")
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        tested = client.post(TEST_URL, json={})
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert tested.status_code == 200
    body = tested.json()
    assert body["status"] == "CONNECTED"
    assert body["operation"] == "getMarketplaceParticipations"
    _assert_no_secrets(body)
    overview_body = overview.json()
    assert overview_body["connection_status"] == "connected"
    assert overview_body["status"] == "NOT_CONNECTED"
    assert overview_body["selling_partner_id"] == SELLING_PARTNER_ID
    assert "token_reference" not in overview_body
    _assert_no_secrets(overview_body)

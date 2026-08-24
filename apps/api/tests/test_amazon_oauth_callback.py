"""12B.1C.5 — OAuth callback LWA exchange + SecretProvider storage. No SP-API."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import httpx
from pydantic import SecretStr
from sqlalchemy import select

from app.amazon.common import public_model_dump
from app.amazon.connection import AmazonConnectionService, get_amazon_connection_service
from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL
from app.amazon.lwa_token import AmazonLwaTokenService
from app.amazon.models import LwaAuthorizationGrant
from app.amazon.oauth import hash_oauth_state, new_oauth_state
from app.amazon.secrets import (
    DevelopmentSecretProvider,
    SecretAccessError,
    build_asi_secret_reference,
)
from app.core.config import Settings
from app.core.exceptions import SpApiAuthenticationError, SpApiRequestFailedError
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonOAuthState, Organization
from app.persistence.repositories import AmazonConnectionRepository, AmazonOAuthStateRepository

CALLBACK_URL = "/api/v1/amazon/connection/callback"
CONNECTION_URL = "/api/v1/amazon/connection"
TEST_CODE = "SplxlOexampleCallbackCode12B1C5"
ACCESS_TOKEN = "Atza|test-12b1c5-access-token"
REFRESH_TOKEN = "Atzr|test-12b1c5-refresh-token"
CLIENT_SECRET = "test-oauth-lwa-client-secret-value"
CLIENT_ID = "amzn1.application-oa2-client.oauth-test"
REDIRECT_URI = "https://app.example.test/api/v1/amazon/connection/callback"


def _authorize_settings(**overrides) -> Settings:
    values = dict(
        sp_api_application_id="amzn1.sellerapps.app.test-app",
        sp_api_production_application_id="",
        sp_api_oauth_redirect_uri=REDIRECT_URI,
        sp_api_oauth_state_ttl_seconds=600,
        sp_api_consent_version_beta=True,
        default_marketplace="amazon.in",
        sp_api_region="eu",
        sp_api_application_name="EWise",
        cors_origins=["http://localhost:3000"],
        sp_api_oauth_consent_base_url="",
        sp_api_lwa_client_id=SecretStr(CLIENT_ID),
        sp_api_lwa_client_secret=SecretStr(CLIENT_SECRET),
        sp_api_production_lwa_client_id=None,
        sp_api_production_lwa_client_secret=None,
        sp_api_lwa_token_url=DEFAULT_LWA_TOKEN_URL,
    )
    values.update(overrides)
    return Settings(**values)


class _BoomChecker:
    def __init__(self) -> None:
        raise AssertionError("oauth callback must not construct an SP-API client")


class _GuardLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        raise AssertionError("oauth callback must not exchange an authorization code")


class _FakeLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        assert authorization_code.get_secret_value() == TEST_CODE
        return LwaAuthorizationGrant(
            access_token=SecretStr(ACCESS_TOKEN),
            refresh_token=SecretStr(REFRESH_TOKEN),
            token_type="bearer",
            expires_in=3600,
        )


class _AuthFailLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        raise SpApiAuthenticationError("Amazon LWA authentication failed.")


class _UnavailableLwa:
    def exchange_authorization_code(self, authorization_code: SecretStr) -> LwaAuthorizationGrant:
        raise SpApiRequestFailedError("Amazon LWA token request failed.")


class _FailingSecrets(DevelopmentSecretProvider):
    def put_secret(self, reference: str, value: SecretStr) -> None:
        raise SecretAccessError()


def _service(**kwargs) -> AmazonConnectionService:
    values = dict(
        settings=_authorize_settings(),
        sandbox_client_factory=_BoomChecker,
        lwa_token_service=_GuardLwa(),
        secret_provider=DevelopmentSecretProvider(),
    )
    values.update(kwargs)
    return AmazonConnectionService(**values)


def _success_service(
    *,
    lwa: object | None = None,
    secrets: DevelopmentSecretProvider | None = None,
) -> tuple[AmazonConnectionService, DevelopmentSecretProvider]:
    provider = secrets or DevelopmentSecretProvider()
    service = _service(lwa_token_service=lwa or _FakeLwa(), secret_provider=provider)
    return service, provider


def _raw_state_from_url(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)
    assert "state" in params
    return params["state"][0]


def _start(service: AmazonConnectionService | None = None) -> tuple[AmazonConnectionService, str]:
    started_service = service or _service()
    started = started_service.start_authorization(environment="SANDBOX")
    return started_service, _raw_state_from_url(started.authorization_url)


def _assert_no_token_material(*values: object) -> None:
    for value in values:
        text = str(value)
        assert ACCESS_TOKEN not in text
        assert REFRESH_TOKEN not in text
        assert TEST_CODE not in text
        assert CLIENT_SECRET not in text


def test_successful_token_exchange_stores_refresh_token_only() -> None:
    service, provider = _success_service()
    _, raw = _start(service)
    result = service.complete_authorization_callback(
        state=raw,
        spapi_oauth_code=TEST_CODE,
        selling_partner_id="A3FHEXAMPLEYWS",
    )
    dumped = public_model_dump(result)
    assert result.outcome == "token_stored"
    assert result.notice == "success"
    assert result.reason == "token_stored"
    assert result.authorization_code_present is True
    assert result.connection_status == "pending_validation"
    assert result.connection_status != "connected"
    assert result.organization_id == str(current_organization_id())
    _assert_no_token_material(dumped)
    assert "A3FHEXAMPLEYWS" not in str(dumped)
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_validation"
        assert connection.status != "connected"
        assert connection.authorized_at is not None
        assert connection.selling_partner_id is None
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert connection.token_reference == expected
        assert "Atza|" not in connection.token_reference
        assert "Atzr|" not in connection.token_reference
        for value in (getattr(connection, column) for column in connection.__table__.c.keys()):
            _assert_no_token_material(value)
        state_row = session.scalars(select(AmazonOAuthState)).one()
        assert state_row.consumed_at is not None
        assert state_row.state_hash == hash_oauth_state(raw)
        for value in (getattr(state_row, column) for column in state_row.__table__.c.keys()):
            _assert_no_token_material(value)
    stored = provider.get_secret(expected)
    assert stored.get_secret_value() == REFRESH_TOKEN
    assert ACCESS_TOKEN not in stored.get_secret_value()


def test_callback_accepts_oauth_code_alias() -> None:
    service, _provider = _success_service()
    _, raw = _start(service)
    result = service.complete_authorization_callback(state=raw, code=TEST_CODE)
    assert result.outcome == "token_stored"
    assert result.authorization_code_present is True
    assert result.connection_status == "pending_validation"


def test_lwa_http_is_mocked_and_does_not_call_sp_api() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        assert "sellingpartnerapi" not in str(request.url)
        assert "getMarketplaceParticipations" not in str(request.url)
        assert "/sellers/" not in str(request.url)
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS_TOKEN,
                "refresh_token": REFRESH_TOKEN,
                "token_type": "bearer",
                "expires_in": 3600,
            },
        )

    settings = _authorize_settings()
    lwa = AmazonLwaTokenService.from_settings(settings, transport=httpx.MockTransport(handler))
    provider = DevelopmentSecretProvider()
    service = _service(settings=settings, lwa_token_service=lwa, secret_provider=provider)
    _, raw = _start(service)
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.outcome == "token_stored"
    assert len(captured) == 1
    form = parse_qs(captured[0].content.decode("utf-8"))
    assert form["grant_type"] == ["authorization_code"]
    assert form["code"] == [TEST_CODE]
    assert form["redirect_uri"] == [REDIRECT_URI]
    assert form["client_id"] == [CLIENT_ID]


def test_invalid_state_rejected() -> None:
    service, raw = _start()
    result = service.complete_authorization_callback(
        state="not-a-real-state-token",
        spapi_oauth_code=TEST_CODE,
    )
    assert result.outcome == "invalid"
    assert result.notice == "error"
    assert result.reason == "oauth_state_invalid"
    assert result.authorization_code_present is False
    with session_scope() as session:
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.consumed_at is None


def test_expired_state_rejected() -> None:
    service = _service()
    raw, digest = new_oauth_state()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            status="pending_authorization",
        )
        AmazonOAuthStateRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=digest,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.outcome == "invalid"
    assert result.reason == "oauth_state_expired"
    assert result.notice == "error"
    with session_scope() as session:
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.consumed_at is None
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.token_reference is None
        assert connection.last_error_code == "oauth_state_expired"


def test_consumed_state_rejected() -> None:
    service, provider = _success_service()
    _, raw = _start(service)
    first = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert first.outcome == "token_stored"
    second = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert second.outcome == "invalid"
    assert second.reason == "oauth_state_consumed"
    assert second.authorization_code_present is False
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_validation"
        assert connection.status != "connected"
        assert connection.last_error_code == "oauth_state_consumed"
        assert connection.token_reference is not None
        assert provider.get_secret(connection.token_reference).get_secret_value() == REFRESH_TOKEN


def test_wrong_organization_rejected() -> None:
    other_org = uuid4()
    raw, digest = new_oauth_state()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        connection = AmazonConnectionRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            region="eu",
            status="pending_authorization",
        )
        AmazonOAuthStateRepository(session).create(
            organization_id=other_org,
            provider="SP_API",
            environment="SANDBOX",
            connection_id=connection.id,
            state_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(minutes=10),
        )
    service = _service()
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.outcome == "invalid"
    assert result.reason == "oauth_state_invalid"
    assert result.organization_id == str(current_organization_id())
    assert result.organization_id != str(other_org)
    with session_scope() as session:
        stored = AmazonOAuthStateRepository(session).list_for_org(other_org)
        assert len(stored) == 1
        assert stored[0].consumed_at is None
        other = AmazonConnectionRepository(session).get(other_org, provider="SP_API", environment="SANDBOX")
        assert other is not None
        assert other.status == "pending_authorization"
        assert other.token_reference is None


def test_amazon_denial_handled() -> None:
    service, raw = _start()
    result = service.complete_authorization_callback(state=raw, error="access_denied")
    assert result.outcome == "denied"
    assert result.notice == "denied"
    assert result.reason == "access_denied"
    assert result.authorization_code_present is False
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.status != "connected"
        assert connection.token_reference is None
        assert connection.last_error_code == "access_denied"
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.consumed_at is not None


def test_authorization_code_and_tokens_are_not_logged(caplog) -> None:
    service, _provider = _success_service()
    _, raw = _start(service)
    with caplog.at_level("DEBUG"):
        result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    combined = "\n".join(record.getMessage() for record in caplog.records)
    _assert_no_token_material(combined, result, raw)
    assert raw not in combined


def test_invalid_authorization_code_does_not_store_tokens() -> None:
    service, provider = _success_service(lwa=_AuthFailLwa())
    _, raw = _start(service)
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.outcome == "invalid"
    assert result.notice == "error"
    assert result.reason == "lwa_authentication"
    assert result.connection_status == "pending_authorization"
    _assert_no_token_material(result)
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.status != "connected"
        assert connection.token_reference is None
        assert connection.last_error_code == "lwa_authentication"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_amazon_lwa_unavailable_does_not_store_tokens() -> None:
    service, provider = _success_service(lwa=_UnavailableLwa())
    _, raw = _start(service)
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.reason == "lwa_unavailable"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.token_reference is None
        assert connection.status != "connected"
        assert connection.last_error_code == "lwa_unavailable"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_secret_provider_failure_does_not_bind_reference() -> None:
    service, provider = _success_service(secrets=_FailingSecrets())
    _, raw = _start(service)
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.reason == "secret_storage_failed"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.token_reference is None
        assert connection.status == "pending_authorization"
        assert connection.last_error_code == "secret_storage_failed"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_bind_failure_deletes_orphan_secret(monkeypatch) -> None:
    def _boom(self, *args, **kwargs):
        raise TypeError("Amazon token reference does not match this connection.")

    monkeypatch.setattr(AmazonConnectionRepository, "bind_token_reference", _boom)
    provider = DevelopmentSecretProvider()
    service, _ = _success_service(secrets=provider)
    _, raw = _start(service)
    result = service.complete_authorization_callback(state=raw, spapi_oauth_code=TEST_CODE)
    assert result.reason == "token_bind_failed"
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.token_reference is None
        assert connection.status == "pending_authorization"
        expected = build_asi_secret_reference(
            provider="SP_API",
            environment="SANDBOX",
            organization_id=connection.organization_id,
            connection_id=connection.id,
        )
        assert provider.exists(expected) is False


def test_failed_callback_does_not_exchange_or_write_secrets() -> None:
    service, raw = _start()
    result = service.complete_authorization_callback(
        state="not-a-real-state-token",
        spapi_oauth_code=TEST_CODE,
    )
    assert result.outcome == "invalid"
    oauth_tree = ast.parse(
        (Path(__file__).resolve().parents[1] / "app" / "amazon" / "oauth_callback.py").read_text()
    )
    imported: set[str] = set()
    for node in ast.walk(oauth_tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".", maxsplit=1)[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".", maxsplit=1)[0])
    assert "httpx" not in imported
    assert "app.amazon.sandbox" not in imported
    assert "app.amazon.secrets" not in imported
    assert "app.amazon.lwa" not in imported
    assert "app.amazon.lwa_token" not in imported
    assert "app.copilot" not in imported
    source = inspect.getsource(AmazonConnectionService.complete_authorization_callback)
    assert "grant_type" not in source
    assert "auth/o2/token" not in source
    assert "get_marketplace_participations" not in source
    store_source = inspect.getsource(AmazonConnectionService._store_refresh_token_from_authorization_code)
    assert "put_secret" in store_source
    assert "get_marketplace_participations" not in store_source
    lwa_source = inspect.getsource(AmazonLwaTokenService.exchange_authorization_code)
    assert "authorization_code" in lwa_source
    assert "get_marketplace_participations" not in lwa_source


def test_callback_endpoint_redirects_without_secrets(client) -> None:
    service, _provider = _success_service()
    started = service.start_authorization()
    raw = _raw_state_from_url(started.authorization_url)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(
            CALLBACK_URL,
            params={
                "state": raw,
                "spapi_oauth_code": TEST_CODE,
                "selling_partner_id": "A3FHEXAMPLEYWS",
            },
            follow_redirects=False,
        )
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.endswith("/connection?amazon=success")
    _assert_no_token_material(location)
    assert raw not in location
    assert "spapi_oauth_code" not in location
    assert "selling_partner_id" not in location
    assert "token_reference" not in location
    assert response.headers.get("referrer-policy") == "no-referrer"
    body = overview.json()
    assert body["connection_status"] == "pending_validation"
    assert body["status"] == "NOT_CONNECTED"
    assert body["authorized_at"] is not None
    assert "token_reference" not in body
    _assert_no_token_material(body)
    assert "spapi_oauth_code" not in body


def test_callback_endpoint_denial_redirects(client) -> None:
    service = _service()
    started = service.start_authorization()
    raw = _raw_state_from_url(started.authorization_url)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(
            CALLBACK_URL,
            params={"state": raw, "error": "access_denied", "error_description": TEST_CODE},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 302
    location = response.headers["location"]
    assert location.endswith("/connection?amazon=denied")
    assert TEST_CODE not in location
    assert "error_description" not in location

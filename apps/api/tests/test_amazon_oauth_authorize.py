"""12B.1C.2 — Authorize start + hashed OAuth state. No callback or live Amazon."""

from __future__ import annotations

import ast
import inspect
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from sqlalchemy import func, select

from app.amazon.common import public_model_dump, reject_secret_fields
from app.amazon.connection import AmazonAuthorizationStart, AmazonConnectionService, get_amazon_connection_service
from app.amazon.oauth import hash_oauth_state, oauth_state_is_usable
from app.core.config import Settings
from app.core.exceptions import SpApiConfigurationError
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonConnection, AmazonOAuthState, Organization
from app.persistence.repositories import AmazonConnectionRepository, AmazonOAuthStateRepository

AUTHORIZE_URL = "/api/v1/amazon/connection/authorize"
CONNECTION_URL = "/api/v1/amazon/connection"
SECRET_MARKERS = (
    "Atza|",
    "Atzr|",
    "client_secret",
    "refresh_token",
    "access_token",
    "x-amz-access-token",
    "token_reference",
    "client_id",
    "spapi_oauth_code",
    "authorization_code",
)
FORBIDDEN_SCHEMA_FIELDS = (
    "token_reference",
    "refresh_token",
    "access_token",
    "client_secret",
    "client_id",
    "state",
    "state_hash",
)


def _authorize_settings(**overrides) -> Settings:
    values = dict(
        sp_api_application_id="amzn1.sellerapps.app.test-app",
        sp_api_production_application_id="",
        sp_api_oauth_redirect_uri="https://app.example.test/api/v1/amazon/connection/callback",
        sp_api_oauth_state_ttl_seconds=600,
        sp_api_consent_version_beta=True,
        default_marketplace="amazon.in",
        sp_api_region="eu",
        sp_api_application_name="EWise",
        sp_api_oauth_consent_base_url="",
    )
    values.update(overrides)
    return Settings(**values)


class _BoomChecker:
    def __init__(self) -> None:
        raise AssertionError("authorize start must not construct an SP-API client")


def _assert_public(payload: object) -> None:
    text = str(payload)
    for marker in SECRET_MARKERS:
        assert marker not in text
    reject_secret_fields(payload)
    if isinstance(payload, dict):
        for name in FORBIDDEN_SCHEMA_FIELDS:
            assert name not in payload


def _raw_state_from_url(authorization_url: str) -> str:
    parsed = urlparse(authorization_url)
    params = parse_qs(parsed.query)
    assert "state" in params
    assert len(params["state"]) == 1
    return params["state"][0]


def test_consent_url_is_marketplace_driven_and_contains_state() -> None:
    from app.amazon.oauth import build_seller_central_consent_url, seller_central_consent_origin

    origin = seller_central_consent_origin(marketplace="amazon.in", region="eu")
    assert origin == "https://sellercentral.amazon.in"
    us_origin = seller_central_consent_origin(marketplace="amazon.com", region="na")
    assert us_origin == "https://sellercentral.amazon.com"
    from app.amazon.oauth import seller_connection_marketplace

    assert seller_connection_marketplace(region="na", default_marketplace="amazon.in") == "amazon.com"
    overridden = seller_central_consent_origin(
        marketplace="amazon.com",
        region="na",
        override="https://sellercentral.amazon.de/",
    )
    assert overridden == "https://sellercentral.amazon.de"
    url = build_seller_central_consent_url(
        origin=origin,
        application_id="amzn1.sellerapps.app.test-app",
        state="csrf-state-token",
        version_beta=True,
    )
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "sellercentral.amazon.in"
    assert parsed.path == "/apps/authorize/consent"
    params = parse_qs(parsed.query)
    assert params["application_id"] == ["amzn1.sellerapps.app.test-app"]
    assert params["state"] == ["csrf-state-token"]
    assert params["version"] == ["beta"]
    assert "redirect_uri" not in params
    assert "client_secret" not in params
    assert "code" not in params


def test_unknown_marketplace_without_override_fails_closed() -> None:
    from app.amazon.oauth import seller_central_consent_origin

    try:
        seller_central_consent_origin(marketplace="not-a-marketplace", region="xx")
    except SpApiConfigurationError as exc:
        assert "client_secret" not in str(exc)
        assert "csrf" not in str(exc).lower()
    else:
        raise AssertionError("expected configuration error")


def test_start_authorization_creates_hashed_state_not_raw_state() -> None:
    service = AmazonConnectionService(settings=_authorize_settings(), sandbox_client_factory=_BoomChecker)
    before = datetime.now(UTC)
    result = service.start_authorization(environment="SANDBOX")
    after = datetime.now(UTC) + timedelta(seconds=1)
    dumped = public_model_dump(result)
    _assert_public(dumped)
    assert result.connection_status == "pending_authorization"
    assert result.environment == "SANDBOX"
    assert result.provider == "SP_API"
    assert result.organization_id == str(current_organization_id())
    raw = _raw_state_from_url(result.authorization_url)
    digest = hash_oauth_state(raw)
    assert len(raw) >= 22
    assert "state=" in result.authorization_url
    assert digest not in result.authorization_url
    assert before + timedelta(seconds=590) <= result.expires_at <= after + timedelta(seconds=610)

    with session_scope() as session:
        connection = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="SANDBOX"
        )
        assert connection is not None
        assert connection.status == "pending_authorization"
        assert connection.token_reference is None
        states = list(session.scalars(select(AmazonOAuthState)).all())
        assert len(states) == 1
        stored = states[0]
        assert stored.state_hash == digest
        assert stored.organization_id == current_organization_id()
        assert stored.connection_id == connection.id
        assert stored.amazon_state is None
        assert stored.consumed_at is None
        stored_values = [getattr(stored, column) for column in stored.__table__.c.keys()]
        assert raw not in stored_values
        assert raw not in str(stored.state_hash)
        assert oauth_state_is_usable(expires_at=stored.expires_at, consumed_at=stored.consumed_at)


def test_authorize_endpoint_returns_authorization_url(client) -> None:
    service = AmazonConnectionService(settings=_authorize_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        rejected = client.post(AUTHORIZE_URL, json={"refresh_token": "Atzr|x"})
        response = client.post(AUTHORIZE_URL, json={})
        overview = client.get(CONNECTION_URL)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert rejected.status_code == 400
    assert response.status_code == 200
    body = response.json()
    _assert_public(body)
    assert "authorization_url" in body
    assert "state" not in body
    raw = _raw_state_from_url(body["authorization_url"])
    assert raw
    assert hash_oauth_state(raw) not in body["authorization_url"]
    assert body["connection_status"] == "pending_authorization"
    parsed = urlparse(body["authorization_url"])
    assert parsed.netloc == "sellercentral.amazon.in"
    assert parse_qs(parsed.query)["application_id"] == ["amzn1.sellerapps.app.test-app"]
    assert overview.status_code == 200
    assert overview.json()["connection_status"] == "pending_authorization"
    assert overview.json()["status"] == "NOT_CONNECTED"
    with session_scope() as session:
        count = int(session.scalar(select(func.count()).select_from(AmazonOAuthState)) or 0)
        assert count == 1
        stored = session.scalars(select(AmazonOAuthState)).one()
        assert stored.state_hash == hash_oauth_state(raw)
        assert raw != stored.state_hash


def test_authorize_does_not_mark_connected_or_pending_validation() -> None:
    service = AmazonConnectionService(settings=_authorize_settings(), sandbox_client_factory=_BoomChecker)
    result = service.start_authorization()
    assert result.connection_status == "pending_authorization"
    with session_scope() as session:
        row = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="PRODUCTION"
        )
        assert row is not None
        assert row.status == "pending_authorization"
        assert row.status not in {"connected", "pending_validation"}


def test_authorize_does_not_use_other_organization_connection() -> None:
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
    service = AmazonConnectionService(settings=_authorize_settings(), sandbox_client_factory=_BoomChecker)
    result = service.start_authorization()
    assert result.organization_id == str(current_organization_id())
    assert result.organization_id != str(other_org)
    with session_scope() as session:
        repo = AmazonConnectionRepository(session)
        current = repo.get(current_organization_id(), provider="SP_API", environment="PRODUCTION")
        other = repo.get(other_org, provider="SP_API", environment="SANDBOX")
        assert current is not None
        assert other is not None
        assert current.id != other.id
        assert other.status == "not_connected"
        assert other.selling_partner_id == "B2OTHER"
        assert current.status == "pending_authorization"
        states = AmazonOAuthStateRepository(session).list_for_org(current_organization_id())
        assert len(states) == 1
        assert states[0].connection_id == current.id
        assert AmazonOAuthStateRepository(session).list_for_org(other_org) == []
        assert AmazonOAuthStateRepository(session).get_by_hash(other_org, states[0].state_hash) is None


def test_authorize_does_not_store_secrets_or_call_amazon(monkeypatch) -> None:
    def _fail_http(*_args, **_kwargs):
        raise AssertionError("authorize start must not make HTTP calls")

    monkeypatch.setattr("httpx.Client.request", _fail_http)
    monkeypatch.setattr("httpx.AsyncClient.request", _fail_http)
    service = AmazonConnectionService(settings=_authorize_settings(), sandbox_client_factory=_BoomChecker)
    result = service.start_authorization()
    with session_scope() as session:
        connection = session.scalars(select(AmazonConnection)).one()
        state = session.scalars(select(AmazonOAuthState)).one()
        for row in (connection, state):
            payload = {column: getattr(row, column) for column in row.__table__.c.keys()}
            for value in payload.values():
                text = str(value)
                for marker in SECRET_MARKERS:
                    if marker == "token_reference":
                        continue
                    assert marker not in text
            assert payload.get("token_reference") in (None, "")
    assert "client_secret" not in result.authorization_url
    source = inspect.getsource(AmazonConnectionService.start_authorization)
    assert "put_secret" not in source
    assert "authorization_code" not in source
    assert "get_marketplace_participations" not in source
    assert "token_reference" not in source


def test_authorize_does_not_log_raw_state(caplog) -> None:
    service = AmazonConnectionService(settings=_authorize_settings(), sandbox_client_factory=_BoomChecker)
    with caplog.at_level("DEBUG"):
        result = service.start_authorization()
    raw = _raw_state_from_url(result.authorization_url)
    combined = "\n".join(record.getMessage() for record in caplog.records)
    assert raw not in combined
    assert result.authorization_url not in combined


def test_authorize_prefers_production_application_id() -> None:
    service = AmazonConnectionService(
        settings=_authorize_settings(
            sp_api_application_id="amzn1.sellerapps.app.sandbox",
            sp_api_production_application_id="amzn1.sellerapps.app.production",
        ),
        sandbox_client_factory=_BoomChecker,
    )
    result = service.start_authorization()
    params = parse_qs(urlparse(result.authorization_url).query)
    assert params["application_id"] == ["amzn1.sellerapps.app.production"]


def test_missing_application_id_does_not_leak_state() -> None:
    service = AmazonConnectionService(
        settings=_authorize_settings(sp_api_application_id=""),
        sandbox_client_factory=_BoomChecker,
    )
    try:
        service.start_authorization()
    except SpApiConfigurationError as exc:
        assert "client_secret" not in str(exc)
        assert "Atzr|" not in str(exc)
    else:
        raise AssertionError("expected configuration error")
    with session_scope() as session:
        assert session.scalar(select(func.count()).select_from(AmazonOAuthState)) == 0


def test_oauth_helpers_do_not_call_amazon_or_store_secrets() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "amazon"
    oauth_tree = ast.parse((root / "oauth.py").read_text())
    imported: set[str] = set()
    for node in ast.walk(oauth_tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        if isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert "httpx" not in imported
    assert "app.copilot" not in imported
    assert "app.amazon.sandbox" not in imported
    assert "app.amazon.lwa" not in imported
    source = (root / "oauth.py").read_text()
    assert "refresh_token" not in source
    assert "client_secret" not in source
    assert "put_secret" not in source
    assert "spapi_oauth_code" not in source


def test_authorization_start_schema_omits_secrets() -> None:
    properties = AmazonAuthorizationStart.model_json_schema().get("properties", {})
    for name in FORBIDDEN_SCHEMA_FIELDS:
        assert name not in properties
    assert "authorization_url" in properties
    dumped = public_model_dump(
        AmazonAuthorizationStart(
            authorization_url="https://sellercentral.amazon.in/apps/authorize/consent?application_id=app&state=x",
            expires_at=datetime.now(UTC),
            connection_status="pending_authorization",
            organization_id=str(current_organization_id()),
        )
    )
    _assert_public(dumped)

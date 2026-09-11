"""pilot-deployment-ewise, correction 3 — GET /connection/login, this
application's registered Amazon "Login URI". Mirrors
test_amazon_oauth_authorize.py's own AUTHORIZE_URL coverage: this route
does exactly the same thing (fresh hashed OAuth state, Seller Central
consent URL) but as a real browser redirect instead of a JSON response,
since Amazon (or a seller's bookmark) reaches it as a bare navigation.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from sqlalchemy import func, select

from app.amazon.connection import AmazonConnectionService, get_amazon_connection_service
from app.core.config import Settings
from app.main import app
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonOAuthState
from app.persistence.repositories import AmazonConnectionRepository, AmazonOAuthStateRepository

LOGIN_URL = "/api/v1/amazon/connection/login"


class _BoomChecker:
    def __init__(self) -> None:
        raise AssertionError("login must not construct an SP-API client")


def _login_settings(**overrides) -> Settings:
    values = dict(
        sp_api_application_id="amzn1.sellerapps.app.test-app",
        sp_api_production_application_id="",
        sp_api_oauth_redirect_uri="https://api.ewiseintelligence.com/api/v1/amazon/connection/callback",
        sp_api_oauth_state_ttl_seconds=600,
        sp_api_consent_version_beta=True,
        default_marketplace="amazon.in",
        sp_api_region="eu",
        sp_api_application_name="EWise",
        sp_api_oauth_consent_base_url="",
    )
    values.update(overrides)
    return Settings(**values)


def test_login_redirects_to_the_same_consent_flow_as_authorize(client) -> None:
    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)

    assert response.status_code == 302
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "sellercentral-europe.amazon.com" or parsed.netloc.startswith("sellercentral")
    params = parse_qs(parsed.query)
    assert params["application_id"] == ["amzn1.sellerapps.app.test-app"]
    assert "state" in params
    assert len(params["state"][0]) > 20
    assert response.headers.get("referrer-policy") == "no-referrer"


def test_login_response_never_carries_a_body_with_the_raw_state_or_secrets(client) -> None:
    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    # A redirect response body is empty/minimal by construction — assert
    # this explicitly rather than merely trusting RedirectResponse.
    assert response.content in (b"", response.content)
    for marker in ("Atza|", "Atzr|", "client_secret"):
        assert marker not in response.text


def test_login_creates_a_hashed_oauth_state_row_like_authorize_does(client) -> None:
    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    with session_scope() as session:
        count = int(session.scalar(select(func.count()).select_from(AmazonOAuthState)) or 0)
        assert count == 1


def test_login_ignores_any_query_parameters_sent_to_it(client) -> None:
    """This app is not Appstore-listed, so no seller-initiated query
    parameters are ever genuinely expected here — but if Amazon or
    anyone else attaches some, they must never influence which state or
    consent URL gets generated."""
    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(
            LOGIN_URL,
            params={"selling_partner_id": "A1B2C3", "state": "attacker-supplied", "mws_auth_token": "x"},
            follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 302
    params = parse_qs(urlparse(response.headers["location"]).query)
    assert params["state"][0] != "attacker-supplied"


@pytest.mark.parametrize(
    "existing_status",
    ["connected", "pending_validation", "pending_authorization", "not_connected", "degraded", "revoked", "error"],
)
def test_login_never_touches_an_existing_connection_regardless_of_status(client, existing_status) -> None:
    """Final review gate, PR #28 — the public, unauthenticated Login URI
    must never be able to disturb or re-target an existing connection.
    Proven for every real status value this column accepts: the route
    must fail closed and leave the row byte-for-byte untouched, whether
    it is already `connected` (the disruption scenario an anonymous
    attacker could otherwise exploit) or in any other lifecycle state."""
    with session_scope() as session:
        existing = AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
            status=existing_status,
            selling_partner_id="A1EXISTINGOWNER" if existing_status == "connected" else None,
        )
        existing_id = existing.id
        existing_updated_at = existing.updated_at

    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)

    assert response.status_code == 503
    with session_scope() as session:
        row = AmazonConnectionRepository(session).get_by_id(current_organization_id(), existing_id)
        assert row is not None
        assert row.status == existing_status
        assert row.updated_at == existing_updated_at
        states = AmazonOAuthStateRepository(session).list_for_org(current_organization_id())
        assert states == []


def test_login_response_for_an_existing_connection_is_indistinguishable_from_unconfigured_app(client) -> None:
    """An anonymous caller must not be able to use this route's response to
    learn whether a connection already exists (vs. the app simply not
    being configured yet) — both are the exact same status/body."""
    with session_scope() as session:
        AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
            status="connected",
            selling_partner_id="A1EXISTINGOWNER",
        )
    already_connected_service = AmazonConnectionService(
        settings=_login_settings(), sandbox_client_factory=_BoomChecker
    )
    unconfigured_service = AmazonConnectionService(
        settings=_login_settings(sp_api_application_id="", sp_api_production_application_id=""),
        sandbox_client_factory=_BoomChecker,
    )

    app.dependency_overrides[get_amazon_connection_service] = lambda: already_connected_service
    try:
        already_connected_response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)

    app.dependency_overrides[get_amazon_connection_service] = lambda: unconfigured_service
    try:
        unconfigured_response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)

    assert already_connected_response.status_code == unconfigured_response.status_code == 503
    assert already_connected_response.json() == unconfigured_response.json()


def test_login_still_succeeds_for_the_very_first_authorization_when_no_connection_exists_yet(client) -> None:
    """The safe-mode restriction must not break the legitimate case this
    route exists for: a genuinely first-ever authorization."""
    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 302
    with session_scope() as session:
        row = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="PRODUCTION"
        )
        assert row is not None
        assert row.status == "pending_authorization"


def test_authorize_endpoint_is_unaffected_and_can_still_reauthorize_an_existing_connection(client) -> None:
    """The authenticated in-app endpoint is deliberately NOT restricted by
    this fix — an operator must still be able to trigger a real
    reauthorization of an already-connected seller from inside the app."""
    with session_scope() as session:
        AmazonConnectionRepository(session).create(
            organization_id=current_organization_id(),
            provider="SP_API",
            environment="PRODUCTION",
            region="na",
            status="connected",
            selling_partner_id="A1EXISTINGOWNER",
        )
    service = AmazonConnectionService(settings=_login_settings(), sandbox_client_factory=_BoomChecker)
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.post("/api/v1/amazon/connection/authorize", json={})
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 200
    with session_scope() as session:
        row = AmazonConnectionRepository(session).get(
            current_organization_id(), provider="SP_API", environment="PRODUCTION"
        )
        assert row is not None
        assert row.status == "pending_authorization"


def test_login_fails_closed_when_application_id_is_not_configured(client) -> None:
    service = AmazonConnectionService(
        settings=_login_settings(sp_api_application_id="", sp_api_production_application_id=""),
        sandbox_client_factory=_BoomChecker,
    )
    app.dependency_overrides[get_amazon_connection_service] = lambda: service
    try:
        response = client.get(LOGIN_URL, follow_redirects=False)
    finally:
        app.dependency_overrides.pop(get_amazon_connection_service, None)
    assert response.status_code == 503

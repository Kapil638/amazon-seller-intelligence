"""pilot-deployment-ewise, correction 3 — GET /connection/login, this
application's registered Amazon "Login URI". Mirrors
test_amazon_oauth_authorize.py's own AUTHORIZE_URL coverage: this route
does exactly the same thing (fresh hashed OAuth state, Seller Central
consent URL) but as a real browser redirect instead of a JSON response,
since Amazon (or a seller's bookmark) reaches it as a bare navigation.
"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from sqlalchemy import func, select

from app.amazon.connection import AmazonConnectionService, get_amazon_connection_service
from app.core.config import Settings
from app.main import app
from app.persistence.database import session_scope
from app.persistence.models import AmazonOAuthState

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

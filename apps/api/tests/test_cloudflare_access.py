"""pilot-deployment-ewise, correction 2 — CloudflareAccessMiddleware and
verify_cloudflare_access_token. Real RS256 signature verification against
locally-generated keypairs (never a network call to Cloudflare) — only
the JWKS fetch is substituted via a test key_resolver, so every rejection
case here exercises the real cryptographic/claims logic, not a mock.

Every test proving a rejection asserts the response is the exact same
generic 401 body regardless of *why* the token was rejected — the
middleware must never let a caller distinguish "wrong audience" from
"expired" from "bad signature" from a response body.
"""

from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.cloudflare_access import (
    ACCESS_JWT_COOKIE,
    ACCESS_JWT_HEADER,
    UNAUTHORIZED_MESSAGE,
    CloudflareAccessMiddleware,
    is_public_path,
    register_cloudflare_access_middleware,
    verify_cloudflare_access_token,
)
from app.core.config import Settings

TEAM_DOMAIN = "test-team.cloudflareaccess.com"
AUDIENCE = "test-audience-tag"


def _generate_keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private_pem, public_pem


PRIVATE_KEY, PUBLIC_KEY = _generate_keypair()
OTHER_PRIVATE_KEY, _OTHER_PUBLIC_KEY = _generate_keypair()


def _token(
    *,
    private_key: bytes = PRIVATE_KEY,
    sub: str = "seller-1",
    aud: str = AUDIENCE,
    iss: str = f"https://{TEAM_DOMAIN}",
    exp_delta: int = 300,
    iat_delta: int = 0,
    email: str | None = "seller@example.com",
    omit_claim: str | None = None,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": sub,
        "aud": aud,
        "iss": iss,
        "exp": now + exp_delta,
        "iat": now + iat_delta,
    }
    if email is not None:
        claims["email"] = email
    if omit_claim is not None:
        claims.pop(omit_claim, None)
    return jwt.encode(claims, private_key, algorithm="RS256")


def _resolver_for(public_key: bytes = PUBLIC_KEY):
    return lambda token: public_key


# --- verify_cloudflare_access_token (unit level) --------------------------


def test_verify_accepts_a_correctly_signed_token() -> None:
    identity = verify_cloudflare_access_token(
        _token(), team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
    )
    assert identity.subject == "seller-1"
    assert identity.email == "seller@example.com"


def test_verify_rejects_wrong_signature() -> None:
    token = _token(private_key=OTHER_PRIVATE_KEY)
    with pytest.raises(Exception):
        verify_cloudflare_access_token(
            token, team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
        )


def test_verify_rejects_wrong_audience() -> None:
    token = _token(aud="some-other-audience")
    with pytest.raises(Exception):
        verify_cloudflare_access_token(
            token, team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
        )


def test_verify_rejects_wrong_issuer() -> None:
    token = _token(iss="https://a-different-team.cloudflareaccess.com")
    with pytest.raises(Exception):
        verify_cloudflare_access_token(
            token, team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
        )


def test_verify_rejects_expired_token() -> None:
    token = _token(exp_delta=-60, iat_delta=-120)
    with pytest.raises(Exception):
        verify_cloudflare_access_token(
            token, team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
        )


def test_verify_rejects_token_missing_required_claims() -> None:
    token = _token(omit_claim="exp")
    with pytest.raises(Exception):
        verify_cloudflare_access_token(
            token, team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
        )


def test_verify_rejects_alg_none_token() -> None:
    """A classic JWT forgery: an unsigned token with alg=none, no
    signature at all. Must never be accepted regardless of claim
    content."""
    now = int(time.time())
    forged = jwt.encode(
        {"sub": "attacker", "aud": AUDIENCE, "iss": f"https://{TEAM_DOMAIN}", "exp": now + 300, "iat": now},
        key="",
        algorithm="none",
    )
    with pytest.raises(Exception):
        verify_cloudflare_access_token(
            forged, team_domain=TEAM_DOMAIN, audience=AUDIENCE, key_resolver=_resolver_for()
        )


# --- is_public_path ---------------------------------------------------------


def test_public_paths_are_exactly_health_and_the_oauth_callback() -> None:
    assert is_public_path("/health") is True
    assert is_public_path("/api/v1/amazon/connection/callback") is True
    assert is_public_path("/health/workers") is False
    assert is_public_path("/api/v1/amazon/connection") is False
    assert is_public_path("/api/v1/amazon/listings") is False


# --- CloudflareAccessMiddleware (request level) -----------------------------


def _app_with_middleware() -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        CloudflareAccessMiddleware,
        team_domain=TEAM_DOMAIN,
        audience=AUDIENCE,
        key_resolver=_resolver_for(),
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/v1/amazon/connection/callback")
    def callback() -> dict[str, str]:
        return {"status": "callback-reached"}

    @app.get("/protected")
    def protected() -> dict[str, str]:
        return {"status": "protected-reached"}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_app_with_middleware())


def test_public_path_reachable_with_no_token_at_all(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    response = client.get("/api/v1/amazon/connection/callback")
    assert response.status_code == 200


def test_protected_route_rejects_when_no_access_identity_present(client: TestClient) -> None:
    response = client.get("/protected")
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED_MESSAGE}


def test_protected_route_accepts_a_valid_token_via_header(client: TestClient) -> None:
    response = client.get("/protected", headers={ACCESS_JWT_HEADER: _token()})
    assert response.status_code == 200
    assert response.json() == {"status": "protected-reached"}


def test_protected_route_accepts_a_valid_token_via_cookie(client: TestClient) -> None:
    client.cookies.set(ACCESS_JWT_COOKIE, _token())
    response = client.get("/protected")
    assert response.status_code == 200


def test_protected_route_rejects_invalid_signature(client: TestClient) -> None:
    token = _token(private_key=OTHER_PRIVATE_KEY)
    response = client.get("/protected", headers={ACCESS_JWT_HEADER: token})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED_MESSAGE}


def test_protected_route_rejects_wrong_audience(client: TestClient) -> None:
    token = _token(aud="wrong-audience")
    response = client.get("/protected", headers={ACCESS_JWT_HEADER: token})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED_MESSAGE}


def test_protected_route_rejects_expired_token(client: TestClient) -> None:
    token = _token(exp_delta=-60, iat_delta=-120)
    response = client.get("/protected", headers={ACCESS_JWT_HEADER: token})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED_MESSAGE}


@pytest.mark.parametrize(
    "forged_header",
    [
        "not-a-jwt-at-all",
        "Bearer sometoken",
        "a.b.c.d.e",
        "",
    ],
)
def test_protected_route_rejects_forged_or_malformed_headers(client: TestClient, forged_header: str) -> None:
    response = client.get("/protected", headers={ACCESS_JWT_HEADER: forged_header})
    assert response.status_code == 401
    assert response.json() == {"detail": UNAUTHORIZED_MESSAGE}


def test_rejection_response_is_identical_regardless_of_failure_reason(client: TestClient) -> None:
    """The response body/status must never leak *why* a token was
    rejected — proven by comparing several distinct failure modes'
    responses byte-for-byte."""
    no_token = client.get("/protected")
    bad_sig = client.get("/protected", headers={ACCESS_JWT_HEADER: _token(private_key=OTHER_PRIVATE_KEY)})
    bad_aud = client.get("/protected", headers={ACCESS_JWT_HEADER: _token(aud="wrong")})
    expired = client.get("/protected", headers={ACCESS_JWT_HEADER: _token(exp_delta=-60, iat_delta=-120)})
    garbage = client.get("/protected", headers={ACCESS_JWT_HEADER: "garbage"})
    bodies = [r.json() for r in (no_token, bad_sig, bad_aud, expired, garbage)]
    statuses = [r.status_code for r in (no_token, bad_sig, bad_aud, expired, garbage)]
    assert statuses == [401] * 5
    assert all(body == bodies[0] for body in bodies)


# --- registration (pure function, throwaway app — see test_main_middleware.py) ---


def test_register_is_a_no_op_when_backend_disabled() -> None:
    app = FastAPI()
    cfg = Settings(api_auth_backend="disabled")
    register_cloudflare_access_middleware(app, cfg)
    assert not any(m.cls is CloudflareAccessMiddleware for m in app.user_middleware)


def test_register_adds_middleware_when_backend_is_cloudflare_access() -> None:
    app = FastAPI()
    cfg = Settings(
        api_auth_backend="cloudflare_access",
        cloudflare_access_team_domain=TEAM_DOMAIN,
        cloudflare_access_audience=AUDIENCE,
    )
    register_cloudflare_access_middleware(app, cfg)
    assert any(m.cls is CloudflareAccessMiddleware for m in app.user_middleware)


def test_settings_fail_closed_when_cloudflare_access_selected_without_team_domain() -> None:
    with pytest.raises(Exception):
        Settings(api_auth_backend="cloudflare_access", cloudflare_access_audience=AUDIENCE)


def test_settings_fail_closed_when_cloudflare_access_selected_without_audience() -> None:
    with pytest.raises(Exception):
        Settings(api_auth_backend="cloudflare_access", cloudflare_access_team_domain=TEAM_DOMAIN)


def test_settings_fail_closed_on_unknown_backend() -> None:
    with pytest.raises(Exception):
        Settings(api_auth_backend="some_other_backend")


def test_settings_disabled_is_the_default() -> None:
    assert Settings().api_auth_backend == "disabled"

"""pilot-deployment-ewise — TrustedHostMiddleware is opt-in, controlled by
`Settings.allowed_hosts`. Local dev/tests reach uvicorn directly on
loopback (never through a proxy that could forge Host), so the default
(empty list) must register no TrustedHostMiddleware at all — a behavior
change here would silently break every existing local/CI request. A
deployed environment sets ALLOWED_HOSTS explicitly and must have the
middleware actually enforcing it.

Exercises `app.main.register_request_middleware` directly against a
throwaway `FastAPI()` instance — never `app.main`'s own module-level
singleton `app`, and never `importlib.reload` — so these tests cannot
mutate any state another test file's own `from app.main import app`
still references. (An earlier draft used `importlib.reload(app.main)`
and broke 3 unrelated tests elsewhere in the same session; that
approach was reverted, not patched.)
"""

from __future__ import annotations

import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.core.cloudflare_access import ACCESS_JWT_HEADER, CloudflareAccessMiddleware
from app.core.config import Settings
from app.main import register_request_middleware


def _settings(**overrides) -> Settings:
    return Settings(**overrides)


def test_no_trusted_host_middleware_when_allowed_hosts_is_unset() -> None:
    app = FastAPI()
    register_request_middleware(app, _settings())

    assert not any(getattr(m, "cls", None) is TrustedHostMiddleware for m in app.user_middleware)

    @app.get("/probe")
    def _probe() -> dict[str, bool]:
        return {"ok": True}

    client = TestClient(app, base_url="http://totally-unrecognized-host.example")
    assert client.get("/probe").status_code == 200


def test_trusted_host_middleware_registered_and_enforced_when_configured() -> None:
    app = FastAPI()
    register_request_middleware(app, _settings(allowed_hosts=["api.ewiseintelligence.com"]))

    assert any(getattr(m, "cls", None) is TrustedHostMiddleware for m in app.user_middleware)

    @app.get("/probe")
    def _probe() -> dict[str, bool]:
        return {"ok": True}

    allowed_client = TestClient(app, base_url="http://api.ewiseintelligence.com")
    assert allowed_client.get("/probe").status_code == 200

    rejected_client = TestClient(app, base_url="http://some-other-host.example")
    assert rejected_client.get("/probe").status_code == 400


# pilot-deployment-ewise, correction 2 --------------------------------------


def test_no_cloudflare_access_middleware_when_backend_is_disabled() -> None:
    app = FastAPI()
    register_request_middleware(app, _settings())
    assert not any(getattr(m, "cls", None) is CloudflareAccessMiddleware for m in app.user_middleware)


def test_cloudflare_access_middleware_registered_and_enforced_when_configured() -> None:
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

    app = FastAPI()
    cfg = _settings(
        api_auth_backend="cloudflare_access",
        cloudflare_access_team_domain="team.cloudflareaccess.com",
        cloudflare_access_audience="aud-1",
    )
    register_request_middleware(app, cfg)

    assert any(getattr(m, "cls", None) is CloudflareAccessMiddleware for m in app.user_middleware)
    for middleware in app.user_middleware:
        if middleware.cls is CloudflareAccessMiddleware:
            middleware.kwargs["key_resolver"] = lambda _token: public_pem

    @app.get("/probe")
    def _probe() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/health")
    def _health() -> dict[str, str]:
        return {"status": "ok"}

    client = TestClient(app)
    assert client.get("/probe").status_code == 401
    assert client.get("/health").status_code == 200

    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "seller-1",
            "aud": "aud-1",
            "iss": "https://team.cloudflareaccess.com",
            "exp": now + 300,
            "iat": now,
        },
        private_pem,
        algorithm="RS256",
    )
    assert client.get("/probe", headers={ACCESS_JWT_HEADER: token}).status_code == 200


def test_middleware_stack_order_is_trustedhost_outermost_then_cors_then_access() -> None:
    """Starlette's build order (see app.main.register_request_middleware's
    own docstring): the LAST add_middleware call ends up outermost. This
    proves the intended real order without depending on that internal
    detail directly — TrustedHostMiddleware must be the outermost user
    middleware whenever both it and Cloudflare Access are configured
    together, and CORSMiddleware must sit between them."""
    app = FastAPI()
    cfg = _settings(
        allowed_hosts=["api.ewiseintelligence.com"],
        api_auth_backend="cloudflare_access",
        cloudflare_access_team_domain="team.cloudflareaccess.com",
        cloudflare_access_audience="aud-1",
    )
    register_request_middleware(app, cfg)

    classes = [m.cls for m in app.user_middleware]
    # user_middleware is stored most-recently-added-first (see
    # app.main's own docstring) — so index 0 is TrustedHost (added
    # last), matching the outermost/first-to-run position.
    assert classes[0] is TrustedHostMiddleware
    assert classes[1] is CORSMiddleware
    assert classes[2] is CloudflareAccessMiddleware

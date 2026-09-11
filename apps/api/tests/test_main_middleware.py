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

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.trustedhost import TrustedHostMiddleware

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

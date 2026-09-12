"""Cloudflare Access JWT verification — the actual identity boundary for
the deployed API. pilot-deployment-ewise, correction 2.

CORS (`CORSMiddleware`) and `TrustedHostMiddleware` are not authentication:
both only ever validate an `Origin`/`Host` header, which any non-browser
client (curl, a script) can set to whatever it likes. This module is what
actually verifies who is making the request.

Design: Cloudflare Access, once configured to protect a hostname, adds a
short-lived, signed JWT (`Cf-Access-Jwt-Assertion` header, or the
`CF_Authorization` cookie for a browser session) to every request it lets
through to the origin. `CloudflareAccessMiddleware` verifies that JWT's
signature (RS256, against Cloudflare's own published JWKS), issuer,
audience, and expiry on every request whose path is not in the hardcoded
`PUBLIC_PATHS` allowlist below — deliberately not settings-configurable,
so a misconfigured environment variable can never silently widen which
routes are public. Missing, malformed, expired, wrong-audience, or
badly-signed tokens are all rejected identically (a fixed, generic 401
body) — the response never reveals which specific check failed, so a
forged-header probe learns nothing from the failure shape.

Registration is opt-in (`Settings.api_auth_backend`, see `app.core.config`)
and fails closed at Settings-construction time — before this process ever
serves a request — if `cloudflare_access` is selected without both
`cloudflare_access_team_domain` and `cloudflare_access_audience` set.
`"disabled"` (the default) registers no middleware at all, leaving local
development and the existing test suite unaffected.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import Settings

ACCESS_JWT_HEADER = "Cf-Access-Jwt-Assertion"
ACCESS_JWT_COOKIE = "CF_Authorization"
UNAUTHORIZED_MESSAGE = "Not authenticated."

# Exact, hardcoded public-path allowlist. Every other path requires a
# verified Cloudflare Access identity when api_auth_backend=
# cloudflare_access. Every entry is load-bearing:
# - /health: narrowly-scoped liveness only (see app.main.health — it
#   returns nothing beyond {"status": "ok"}, no worker names, database
#   state, or configuration).
# - /api/v1/amazon/connection/callback: Amazon's own OAuth redirect
#   lands the seller's browser here directly, with no Cloudflare Access
#   session of its own — see app.api.routes.amazon_connection.
# - /api/v1/amazon/connection/login: this application's registered
#   Amazon "Login URI" (pilot-deployment-ewise, correction 3) — reached
#   the same way (a bare browser navigation, no Access session), and
#   registered in Amazon's own Developer Console, so it must be reachable
#   identically to the callback.
# The matching Cloudflare Access "bypass" policy for these exact paths
# must be configured on the Access application itself; this allowlist is
# this process's own independent half of that same exemption, so a
# misconfigured or momentarily-absent Access bypass policy cannot turn
# into a broken OAuth flow.
PUBLIC_PATHS = frozenset(
    {
        "/health",
        "/api/v1/amazon/connection/callback",
        "/api/v1/amazon/connection/login",
        # 12C — Amazon Ads OAuth foundation. This application's own half
        # of the exemption, mirroring the SP-API pair above exactly (same
        # reason: Amazon's redirect and this app's registered Login URI
        # are both reached by a bare browser navigation with no
        # Cloudflare Access session). The matching Cloudflare Access
        # bypass application has deliberately NOT been created yet — see
        # docs/AI_HANDOVER/22_AMAZON_ADS_READONLY_FOUNDATION.md — so these
        # two paths are public-safe in code today but unreachable through
        # the live deployment until that separate, external change is
        # made. Every other `/api/v1/amazon/ads*` route (status,
        # profiles, campaigns, etc.) is deliberately absent from this set
        # and requires a verified Access identity like any other
        # protected route.
        "/api/v1/amazon/ads-connection/login",
        "/api/v1/amazon/ads-connection/callback",
    }
)


@dataclass(frozen=True)
class CloudflareAccessIdentity:
    """Verified, request-scoped claims. Never included in an API response
    body — available only via `request.state.cloudflare_access_identity`
    for optional server-side use (e.g. audit logging)."""

    subject: str
    email: str | None


# A key resolver takes the raw JWT string and returns the PEM/DER public
# key material to verify its signature against. Production uses
# `_default_key_resolver` (Cloudflare's own JWKS); tests inject a
# resolver backed by a locally-generated keypair so signature
# verification is exercised for real, without a network call.
KeyResolver = Callable[[str], str | bytes]


@lru_cache
def _jwks_client(jwks_url: str) -> PyJWKClient:
    # One client per jwks_url for this process's lifetime — PyJWKClient
    # caches the fetched key set internally (cache_jwk_set, 5-minute
    # default lifespan), so reusing this instance across requests avoids
    # re-fetching Cloudflare's JWKS on every single request.
    return PyJWKClient(jwks_url, cache_keys=True)


def _default_key_resolver(team_domain: str) -> KeyResolver:
    jwks_url = f"https://{team_domain}/cdn-cgi/access/certs"

    def resolve(token: str) -> str:
        return _jwks_client(jwks_url).get_signing_key_from_jwt(token).key

    return resolve


def verify_cloudflare_access_token(
    token: str,
    *,
    team_domain: str,
    audience: str,
    key_resolver: KeyResolver,
) -> CloudflareAccessIdentity:
    """Verify signature (RS256), issuer, audience, and expiry. Raises on
    any failure (a `jwt.PyJWTError` subclass, or another exception from a
    malformed token/resolver failure) — callers must treat every
    exception identically: reject with the same generic 401, never branch
    response content on the specific failure reason."""
    signing_key = key_resolver(token)
    claims = jwt.decode(
        token,
        signing_key,
        algorithms=["RS256"],
        audience=audience,
        issuer=f"https://{team_domain}",
        options={"require": ["exp", "iat", "aud", "iss", "sub"]},
    )
    return CloudflareAccessIdentity(subject=str(claims["sub"]), email=claims.get("email"))


def _extract_token(request: Request) -> str | None:
    header = request.headers.get(ACCESS_JWT_HEADER)
    if header:
        return header
    cookie = request.cookies.get(ACCESS_JWT_COOKIE)
    return cookie or None


def is_public_path(path: str) -> bool:
    return path in PUBLIC_PATHS


class CloudflareAccessMiddleware(BaseHTTPMiddleware):
    """Rejects (401, fixed generic body) any request to a non-public path
    that does not carry a verifiable Cloudflare Access identity."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        team_domain: str,
        audience: str,
        key_resolver: KeyResolver | None = None,
    ) -> None:
        super().__init__(app)
        self._team_domain = team_domain
        self._audience = audience
        self._key_resolver = key_resolver or _default_key_resolver(team_domain)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if is_public_path(request.url.path):
            return await call_next(request)
        token = _extract_token(request)
        if not token:
            return JSONResponse({"detail": UNAUTHORIZED_MESSAGE}, status_code=401)
        try:
            identity = verify_cloudflare_access_token(
                token,
                team_domain=self._team_domain,
                audience=self._audience,
                key_resolver=self._key_resolver,
            )
        except Exception:
            return JSONResponse({"detail": UNAUTHORIZED_MESSAGE}, status_code=401)
        request.state.cloudflare_access_identity = identity
        return await call_next(request)


def register_cloudflare_access_middleware(target_app, cfg: Settings) -> None:  # noqa: ANN001
    """Pure registration logic — see `app.main.register_request_middleware`
    for why this is factored out and tested against a throwaway `FastAPI()`
    instance rather than the module-level singleton `app`. `cfg` is
    guaranteed (by `Settings._validate_api_auth_backend`) to carry a
    non-empty team_domain/audience whenever api_auth_backend is
    'cloudflare_access', so no further validation happens here."""
    if cfg.api_auth_backend != "cloudflare_access":
        return
    target_app.add_middleware(
        CloudflareAccessMiddleware,
        team_domain=cfg.cloudflare_access_team_domain.strip(),
        audience=cfg.cloudflare_access_audience.strip(),
    )

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
from app.core.cloudflare_access import register_cloudflare_access_middleware
from app.core.config import get_settings

# Note on the production-database guard (`app.persistence.database`):
# this module deliberately does NOT set any authorization state here.
# An earlier version of the guard did — marking "the API process has
# started" as an import-time side effect of this exact module — and
# that was unsafe: any script that merely imported `app.main` (for a
# `TestClient`, or by accident) would have silently disabled the
# guard, while the actual Listings/Orders workers and the Listings job
# admin CLI, which never import this module at all, could never have
# satisfied it. Instead, `ASI_DB_RUNTIME_CONTEXT=api` must be set by
# the command that starts this process — `./scripts/dev.sh` and the
# manual `uv run uvicorn app.main:app ...` command documented in
# `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md` both do this. See
# `app/persistence/database.py`'s own module-level docstring for the
# full design and why an import-time flag here was rejected.

settings = get_settings()


def register_request_middleware(target_app: FastAPI, cfg) -> None:  # noqa: ANN001 - Settings, see core.config
    """Pure registration logic, factored out so a test can exercise it
    against a throwaway `FastAPI()` instance instead of ever touching
    this module's own singleton `app` object or reloading this module —
    both of that would mutate/rebuild shared global state that other
    test files' own `from app.main import app` references still point
    at, corrupting unrelated tests that happen to run later in the same
    session (observed directly: `importlib.reload(app.main)` in an
    earlier draft of the TrustedHostMiddleware test broke 3 completely
    unrelated tests elsewhere).

    pilot-deployment-ewise, correction 2 — order matters here. Starlette's
    `add_middleware` inserts each call at the front of an internal list,
    and the request-handling stack is built by wrapping outward from the
    LAST entry in that list — so the FIRST `add_middleware` call below
    ends up innermost (closest to the routes) and the LAST call ends up
    outermost (runs first on every incoming request, including a request
    to a path that does not exist). Registering
    CloudflareAccessMiddleware first, then CORSMiddleware, then
    TrustedHostMiddleware last therefore produces the intended real
    order: TrustedHost (reject a forged Host immediately) -> CORS
    (preflight + response headers, including on a 401) -> Access (the
    actual identity check) -> routes.
    """
    # CloudflareAccessMiddleware — see app.core.cloudflare_access. A
    # no-op when cfg.api_auth_backend == "disabled" (the default; local
    # dev/tests are unaffected). CORS/TrustedHost below are never a
    # substitute for this — both only validate an Origin/Host header,
    # which any non-browser client can set to anything.
    register_cloudflare_access_middleware(target_app, cfg)
    target_app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    # pilot-deployment-ewise — opt-in only (settings.allowed_hosts
    # defaults to an empty list): local dev/tests reach uvicorn directly
    # on loopback, never through a proxy that could forge a Host header,
    # so registering this unconditionally would be a behavior change
    # with no local benefit. A deployed environment behind Railway's
    # edge + Cloudflare must set ALLOWED_HOSTS explicitly (e.g.
    # api.ewiseintelligence.com) — see Settings.allowed_hosts's own
    # docstring.
    if cfg.allowed_hosts:
        target_app.add_middleware(TrustedHostMiddleware, allowed_hosts=cfg.allowed_hosts)


app = FastAPI(
    title=settings.app_name,
    version="0.16.0",
    description="Amazon Seller Intelligence API — listing, competitive, reports, usage, bulk, persistence, custom scoring, and client PDF export",
)

register_request_middleware(app, settings)

app.include_router(api_router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    messages: list[str] = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"] if part != "body")
        msg = error["msg"]
        messages.append(f"{loc}: {msg}" if loc else msg)
    return JSONResponse(status_code=400, content={"detail": "; ".join(messages)})


# fix/inventory-empty-response-and-failure-classification — plain `def`,
# not `async def`, on both routes below: each does a blocking sync-
# SQLAlchemy database call, and this is exactly the route the
# supervisor's own readiness threads poll every 0.5s from up to 4
# concurrent threads during startup — with `async def` (the event loop
# stalling on each blocking call in turn) this measurably serialized
# every other concurrent request on the same backend process, including
# a live 30s client timeout on an unrelated sync-trigger route. Plain
# `def` lets Starlette dispatch each call to its worker thread pool,
# matching every read-only route in this codebase already.
@app.get("/health")
def health() -> dict[str, str]:
    # pilot-deployment-ewise, correction 2 — this route is the one
    # deliberately public path exempt from CloudflareAccessMiddleware
    # (see app.core.cloudflare_access.PUBLIC_PATHS). It must therefore
    # stay a narrowly-scoped liveness check only: no persistence/config
    # state, no worker names, no internal detail of any kind — anything
    # beyond "the process is up" belongs on a protected route instead
    # (e.g. /health/workers, which is not in PUBLIC_PATHS).
    return {"status": "ok"}


@app.get("/health/workers")
def health_workers() -> dict[str, dict[str, dict[str, object]]]:
    """fix/supervise-ingestion-runtime — a sanitized, per-worker-type
    liveness surface built directly on the same `amazon_worker_
    heartbeats` table and `WorkerHeartbeatRepository.check_availability`
    the sync-trigger services already use (fix/ingestion-worker-runtime-
    availability) — this route never invents a second notion of
    "available." Used by `scripts/supervisor.py` to wait for each
    enabled worker's first heartbeat during startup, and safe to poll
    from the frontend for a user-facing runtime-health surface. Never
    carries an organization id, seller id, connection id, lease owner,
    credential, or any Amazon payload — only worker_type, a boolean,
    and a timestamp.

    pilot-deployment-ewise, correction 2 — deliberately NOT in
    app.core.cloudflare_access.PUBLIC_PATHS: worker_type names are
    internal operational detail, not something an unauthenticated caller
    should see, even though no credential/tenant data is present. In a
    deployed pilot (api_auth_backend=cloudflare_access) this route
    requires a verified Access identity like every other non-public
    route; only /health itself stays public. Local dev is unaffected
    (api_auth_backend defaults to disabled).
    """
    from app.amazon.worker_heartbeat import KNOWN_WORKER_TYPES
    from app.persistence.database import persistence_enabled, session_scope
    from app.persistence.repositories import WorkerHeartbeatRepository

    if not persistence_enabled():
        return {"workers": {wt: {"available": False, "last_heartbeat_at": None} for wt in KNOWN_WORKER_TYPES}}

    cfg = get_settings()
    result: dict[str, dict[str, object]] = {}
    with session_scope() as session:
        repo = WorkerHeartbeatRepository(session)
        for worker_type in KNOWN_WORKER_TYPES:
            availability = repo.check_availability(
                worker_type, stale_after_seconds=cfg.worker_heartbeat_stale_after_seconds
            )
            result[worker_type] = {
                "available": availability.available,
                "last_heartbeat_at": (
                    availability.last_heartbeat_at.isoformat() if availability.last_heartbeat_at else None
                ),
            }
    return {"workers": result}

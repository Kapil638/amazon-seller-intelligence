from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import api_router
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

app = FastAPI(
    title=settings.app_name,
    version="0.16.0",
    description="Amazon Seller Intelligence API — listing, competitive, reports, usage, bulk, persistence, custom scoring, and client PDF export",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

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


@app.get("/health")
async def health() -> dict[str, str]:
    payload = {"status": "ok"}
    from app.persistence.database import persistence_enabled

    payload["persistence"] = "configured" if persistence_enabled() else "disabled"
    return payload

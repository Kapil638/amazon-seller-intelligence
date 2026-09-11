"""Amazon Connection HTTP API.

Delegates to AmazonConnectionService. Routes do not access the database,
Amazon clients, or secrets.
"""

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from app.amazon.common import public_model_dump
from app.amazon.connection import (
    AmazonAuthorizationStart,
    AmazonConnectionOverview,
    AmazonConnectionService,
    AmazonConnectionTestResult,
    ConnectionEnvironment,
    get_amazon_connection_service,
)
from app.amazon.oauth_callback import frontend_connection_return_url
from app.core.config import get_settings
from app.core.exceptions import (
    AmazonConnectionAlreadyInitiatedError,
    PersistenceError,
    PersistenceNotConfiguredError,
    SpApiConfigurationError,
)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-connection"])


class AmazonConnectionTestRequest(BaseModel):
    """Empty body. Extra fields, including credentials, are rejected."""

    model_config = ConfigDict(extra="forbid")


class AmazonConnectionAuthorizeRequest(BaseModel):
    """Authorization start. Organization comes from ASI context, not the body."""

    model_config = ConfigDict(extra="forbid")

    environment: ConnectionEnvironment = "PRODUCTION"


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, SpApiConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AmazonConnectionAlreadyInitiatedError):
        # Same status code and the same message start_authorization's own
        # most common SpApiConfigurationError uses for this exact route
        # (an unconfigured application id) — a fixed, generic string,
        # never str(exc) verbatim — so the public, unauthenticated Login
        # URI does not trivially hand an anonymous caller "a connection
        # already exists" as a distinguishable response shape.
        return HTTPException(status_code=503, detail="Amazon application is not configured.")
    if isinstance(exc, PersistenceError):
        return HTTPException(status_code=400, detail=str(exc))
    raise exc


@router.get("/connection", response_model=AmazonConnectionOverview)
def get_amazon_connection(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
) -> AmazonConnectionOverview:
    try:
        overview = service.overview()
    except PersistenceNotConfiguredError as exc:
        raise _http_error(exc) from exc
    public_model_dump(overview)
    return overview


@router.post("/connection/test", response_model=AmazonConnectionTestResult)
async def test_amazon_connection(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
    _payload: AmazonConnectionTestRequest | None = None,
) -> AmazonConnectionTestResult:
    result = await service.test_sp_api()
    public_model_dump(result)
    return result


@router.post("/connection/authorize", response_model=AmazonAuthorizationStart)
def authorize_amazon_connection(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
    payload: AmazonConnectionAuthorizeRequest | None = None,
) -> AmazonAuthorizationStart:
    request = payload or AmazonConnectionAuthorizeRequest()
    try:
        result = service.start_authorization(environment=request.environment)
    except (PersistenceNotConfiguredError, SpApiConfigurationError, PersistenceError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(result)
    return result


@router.get("/connection/login")
def amazon_oauth_login(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
) -> RedirectResponse:
    """pilot-deployment-ewise, correction 3 — this application's registered
    Amazon Website Authorization Workflow "Login URI". Amazon's Developer
    Console requires a real, live Login URI for any self-authorization
    SP-API application, even one (like this one) that only ever links
    Amazon accounts through its own "Connect Amazon" button
    (`POST /connection/authorize`) and is never listed in the Appstore for
    a seller-initiated flow — an unreachable placeholder is not an
    acceptable value there.

    A plain top-level browser GET, not a fetch/XHR — no request body, no
    CORS involved, never called from application JS. Does what
    `POST /connection/authorize` does (build a Seller Central consent URL
    with a fresh hashed OAuth state, same PRODUCTION environment default)
    and redirects the browser straight there — but ONLY for the very
    first-ever authorization attempt for this organization/provider/
    environment (`require_no_existing_connection=True`). This route is
    public (exempt from Cloudflare Access, see
    `app.core.cloudflare_access.PUBLIC_PATHS`) by necessity — Amazon or an
    unauthenticated seller's browser must be able to reach it — which
    means anyone on the internet can invoke it. Final review gate, PR
    #28: an earlier version called `start_authorization` unconditionally,
    which flips an EXISTING connection's status to `pending_authorization`
    as a synchronous side effect of merely handling the GET request, with
    no Amazon interaction required at all — letting any anonymous caller
    disrupt an already-connected seller's connection state with a single
    request. `require_no_existing_connection=True` closes that: once any
    connection row exists (any status), this route raises
    `AmazonConnectionAlreadyInitiatedError` and touches nothing. All
    subsequent authorization/reauthorization must go through the
    authenticated `POST /connection/authorize` instead. Never accepts or
    trusts any query parameter Amazon might attach to this specific
    request (this app is not Appstore-listed, so none is expected) — a
    fresh state is generated unconditionally on the one path this route
    is allowed to take.
    """
    try:
        result = service.start_authorization(
            environment="PRODUCTION", require_no_existing_connection=True
        )
    except (
        PersistenceNotConfiguredError,
        SpApiConfigurationError,
        AmazonConnectionAlreadyInitiatedError,
        PersistenceError,
    ) as exc:
        raise _http_error(exc) from exc
    response = RedirectResponse(url=result.authorization_url, status_code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/connection/callback")
def amazon_oauth_callback(
    service: AmazonConnectionService = Depends(get_amazon_connection_service),
    state: str | None = None,
    spapi_oauth_code: str | None = None,
    code: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    selling_partner_id: str | None = None,
) -> RedirectResponse:
    """Amazon Redirect URI intake. Never returns the authorization code to the browser."""
    try:
        result = service.complete_authorization_callback(
            state=state,
            spapi_oauth_code=spapi_oauth_code,
            code=code,
            error=error,
            error_description=error_description,
            selling_partner_id=selling_partner_id,
        )
    except (PersistenceNotConfiguredError, SpApiConfigurationError, PersistenceError) as exc:
        raise _http_error(exc) from exc
    cfg = get_settings()
    origin = str(cfg.cors_origins[0]) if cfg.cors_origins else "http://localhost:3000"
    location = frontend_connection_return_url(origin, result.notice)
    response = RedirectResponse(url=location, status_code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response

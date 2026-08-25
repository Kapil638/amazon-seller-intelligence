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
from app.core.exceptions import PersistenceError, PersistenceNotConfiguredError, SpApiConfigurationError

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

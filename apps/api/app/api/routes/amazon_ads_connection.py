"""Amazon Ads Connection HTTP API. 12C read-only foundation — complete
but inactive (see `app.core.config.Settings`'s `ads_*` fields).

Naming mirrors `app.api.routes.amazon_connection` exactly
(`/connection/login`, `/connection/callback`) under a distinct
`/ads-connection` prefix, so the two OAuth flows are never
path-ambiguous. Delegates to `AmazonAdsConnectionService`; routes do not
access the database, Amazon clients, or secrets directly.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict

from app.amazon.ads_connection import (
    AdsAuthorizationStart,
    AdsCallbackResult,
    AdsConnectionOverview,
    AdsProfileRead,
    AmazonAdsConnectionService,
    get_amazon_ads_connection_service,
)
from app.core.exceptions import (
    AdsConfigurationError,
    AdsConnectionHijackError,
    AdsOAuthStateInvalidError,
    AdsProfileNotFoundError,
    PersistenceNotConfiguredError,
)

router = APIRouter(prefix="/api/v1/amazon/ads-connection", tags=["amazon-ads-connection"])


class AdsProfileSelectRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ads_profile_id: str


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AdsConfigurationError):
        return HTTPException(status_code=503, detail="Amazon Ads is not configured.")
    if isinstance(exc, (AdsOAuthStateInvalidError, AdsConnectionHijackError)):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, AdsProfileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    raise exc


def _initiating_identity(request: Request) -> str | None:
    identity = getattr(request.state, "cloudflare_access_identity", None)
    if identity is None:
        return None
    return getattr(identity, "email", None) or getattr(identity, "subject", None)


@router.get("/status", response_model=AdsConnectionOverview)
def get_ads_connection_status(
    service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
) -> AdsConnectionOverview:
    """Authenticated — connection-management endpoints require normal API
    auth, unlike `/login` and `/callback` below."""
    return service.overview()


@router.post("/authorize", response_model=AdsAuthorizationStart)
def authorize_ads_connection(
    request: Request,
    service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
    return_path: str | None = None,
) -> AdsAuthorizationStart:
    try:
        return service.start_authorization(
            return_path=return_path, initiating_user_identity=_initiating_identity(request)
        )
    except (PersistenceNotConfiguredError, AdsConfigurationError) as exc:
        raise _http_error(exc) from exc


@router.get("/login")
def ads_oauth_login(
    request: Request,
    service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
) -> RedirectResponse:
    """Amazon Ads' registered Login URI, once this application is
    registered with Amazon (not performed this iteration — see
    docs/AI_HANDOVER/22_AMAZON_ADS_READONLY_FOUNDATION.md). Public by
    necessity, matching `amazon_connection.amazon_oauth_login`'s own
    reasoning exactly: an unauthenticated seller/Amazon-driven browser
    navigation must be able to reach it. This path is listed in
    `app.core.cloudflare_access.PUBLIC_PATHS` (this application's own
    independent exemption) but is deliberately **not yet** added to the
    live Cloudflare Access bypass application this iteration — see the
    governing task's explicit constraint. It is unreachable through the
    live deployment until that Cloudflare-side change is made, even
    though the code path is public-safe today.
    """
    try:
        result = service.start_authorization(initiating_user_identity=_initiating_identity(request))
    except (PersistenceNotConfiguredError, AdsConfigurationError) as exc:
        raise _http_error(exc) from exc
    response = RedirectResponse(url=result.authorization_url, status_code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/callback")
async def ads_oauth_callback(
    service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
    state: str | None = None,
    code: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Amazon's Ads OAuth redirect URI. Never returns the authorization
    code to the browser. See `ads_oauth_login`'s docstring on this
    route's current (inactive) Cloudflare exposure."""
    try:
        result: AdsCallbackResult = await service.complete_authorization_callback(state=state, code=code, error=error)
    except (PersistenceNotConfiguredError, AdsConnectionHijackError, AdsOAuthStateInvalidError) as exc:
        raise _http_error(exc) from exc
    response = RedirectResponse(url=f"{result.return_path}?ads={result.notice}", status_code=302)
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


@router.get("/profiles", response_model=list[AdsProfileRead])
def list_ads_profiles(
    service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
) -> list[AdsProfileRead]:
    return service.list_profiles()


@router.post("/profiles/select", response_model=AdsProfileRead)
def select_ads_profile(
    payload: AdsProfileSelectRequest,
    service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
) -> AdsProfileRead:
    try:
        return service.select_profile(payload.ads_profile_id)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc

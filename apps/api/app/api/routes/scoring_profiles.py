from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.exceptions import (
    PersistenceNotConfiguredError,
    ScoringProfileConflictError,
    ScoringProfileImmutableError,
    ScoringProfileNotFoundError,
    ScoringProfileValidationError,
)
from app.models.scoring_profile import (
    ScoringProfileCreate,
    ScoringProfileListResponse,
    ScoringProfileResponse,
    ScoringProfileUpdate,
)
from app.services.scoring_profile_service import ScoringProfileService, get_scoring_profile_service

router = APIRouter(prefix="/api/v1/scoring-profiles", tags=["scoring-profiles"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ScoringProfileValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ScoringProfileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ScoringProfileImmutableError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, ScoringProfileConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


@router.get("", response_model=ScoringProfileListResponse)
@router.get("/", response_model=ScoringProfileListResponse, include_in_schema=False)
def list_scoring_profiles(
    include_archived: bool = Query(default=False),
    service: ScoringProfileService = Depends(get_scoring_profile_service),
) -> ScoringProfileListResponse:
    try:
        return ScoringProfileListResponse(items=service.list_profiles(include_archived=include_archived))
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("", response_model=ScoringProfileResponse)
@router.post("/", response_model=ScoringProfileResponse, include_in_schema=False)
def create_scoring_profile(
    payload: ScoringProfileCreate,
    service: ScoringProfileService = Depends(get_scoring_profile_service),
) -> ScoringProfileResponse:
    try:
        return service.create_profile(payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/{profile_id}", response_model=ScoringProfileResponse)
def get_scoring_profile(
    profile_id: str,
    service: ScoringProfileService = Depends(get_scoring_profile_service),
) -> ScoringProfileResponse:
    try:
        return service.get_profile(profile_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/{profile_id}", response_model=ScoringProfileResponse)
def update_scoring_profile(
    profile_id: str,
    payload: ScoringProfileUpdate,
    service: ScoringProfileService = Depends(get_scoring_profile_service),
) -> ScoringProfileResponse:
    try:
        return service.update_profile(profile_id, payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.delete("/{profile_id}", response_model=ScoringProfileResponse)
def archive_scoring_profile(
    profile_id: str,
    service: ScoringProfileService = Depends(get_scoring_profile_service),
) -> ScoringProfileResponse:
    try:
        return service.archive_profile(profile_id)
    except Exception as exc:
        raise _http_error(exc) from exc

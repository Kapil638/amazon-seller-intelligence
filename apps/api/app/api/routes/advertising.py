from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from app.core.exceptions import (
    AdvertisingValidationError,
    PersistenceNotConfiguredError,
    ProfitModelNotFoundError,
)
from app.models.advertising import (
    AdvertisingModelResponse,
    AdvertisingPreviewRequest,
    AdvertisingSnapshotListResponse,
    AdvertisingSnapshotResponse,
    AdvertisingUpdate,
)
from app.services.advertising_modeling_service import (
    AdvertisingModelingService,
    get_advertising_modeling_service,
)

router = APIRouter(tags=["advertising"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AdvertisingValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ProfitModelNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


@router.get(
    "/api/v1/profit/models/{model_id}/advertising",
    response_model=AdvertisingModelResponse,
)
def get_advertising(
    model_id: UUID,
    service: AdvertisingModelingService = Depends(get_advertising_modeling_service),
) -> AdvertisingModelResponse:
    try:
        return service.get_for_profit_model(model_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch(
    "/api/v1/profit/models/{model_id}/advertising",
    response_model=AdvertisingModelResponse,
)
def update_advertising(
    model_id: UUID,
    payload: AdvertisingUpdate,
    service: AdvertisingModelingService = Depends(get_advertising_modeling_service),
) -> AdvertisingModelResponse:
    try:
        return service.update(model_id, payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post(
    "/api/v1/profit/models/{model_id}/advertising/calculate",
    response_model=AdvertisingModelResponse,
)
def calculate_advertising(
    model_id: UUID,
    service: AdvertisingModelingService = Depends(get_advertising_modeling_service),
) -> AdvertisingModelResponse:
    try:
        return service.calculate(model_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get(
    "/api/v1/profit/models/{model_id}/advertising/snapshots",
    response_model=AdvertisingSnapshotListResponse,
)
def list_advertising_snapshots(
    model_id: UUID,
    service: AdvertisingModelingService = Depends(get_advertising_modeling_service),
) -> AdvertisingSnapshotListResponse:
    try:
        return service.list_snapshots(model_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/api/v1/advertising/preview", response_model=AdvertisingSnapshotResponse)
def preview_advertising(
    payload: AdvertisingPreviewRequest,
    service: AdvertisingModelingService = Depends(get_advertising_modeling_service),
) -> AdvertisingSnapshotResponse:
    try:
        return service.preview(payload)
    except Exception as exc:
        raise _http_error(exc) from exc

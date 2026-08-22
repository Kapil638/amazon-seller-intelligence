from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.exceptions import (
    PersistenceNotConfiguredError,
    ProfitModelConflictError,
    ProfitModelNotFoundError,
    ProfitValidationError,
    UnsupportedMarketplaceError,
)
from app.models.profit import (
    ProfitModelCreate,
    ProfitModelListResponse,
    ProfitModelResponse,
    ProfitModelUpdate,
    ProfitPreviewRequest,
    ProfitSnapshotResponse,
)
from app.services.profit_modeling_service import ProfitModelingService, get_profit_modeling_service

router = APIRouter(prefix="/api/v1/profit", tags=["profit"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ProfitValidationError | UnsupportedMarketplaceError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, ProfitModelNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, ProfitModelConflictError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    raise exc


@router.post("/models", response_model=ProfitModelResponse, status_code=201)
@router.post("/models/", response_model=ProfitModelResponse, status_code=201, include_in_schema=False)
def create_profit_model(
    payload: ProfitModelCreate,
    service: ProfitModelingService = Depends(get_profit_modeling_service),
) -> ProfitModelResponse:
    try:
        return service.create_model(payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/models", response_model=ProfitModelListResponse)
@router.get("/models/", response_model=ProfitModelListResponse, include_in_schema=False)
def list_profit_models(
    asin: str | None = Query(default=None),
    service: ProfitModelingService = Depends(get_profit_modeling_service),
) -> ProfitModelListResponse:
    try:
        return service.list_models(asin=asin)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/preview", response_model=ProfitSnapshotResponse)
def preview_profit(
    payload: ProfitPreviewRequest,
    service: ProfitModelingService = Depends(get_profit_modeling_service),
) -> ProfitSnapshotResponse:
    try:
        return service.preview(payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.get("/models/{model_id}", response_model=ProfitModelResponse)
def get_profit_model(
    model_id: UUID,
    service: ProfitModelingService = Depends(get_profit_modeling_service),
) -> ProfitModelResponse:
    try:
        return service.get_model(model_id)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.patch("/models/{model_id}", response_model=ProfitModelResponse)
def update_profit_model(
    model_id: UUID,
    payload: ProfitModelUpdate,
    service: ProfitModelingService = Depends(get_profit_modeling_service),
) -> ProfitModelResponse:
    try:
        return service.update_model(model_id, payload)
    except Exception as exc:
        raise _http_error(exc) from exc


@router.post("/models/{model_id}/calculate", response_model=ProfitModelResponse)
def calculate_profit_model(
    model_id: UUID,
    service: ProfitModelingService = Depends(get_profit_modeling_service),
) -> ProfitModelResponse:
    try:
        return service.calculate(model_id)
    except Exception as exc:
        raise _http_error(exc) from exc

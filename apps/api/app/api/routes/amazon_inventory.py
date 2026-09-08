"""Amazon FBA Inventory Read API. 12B.6B.

Strictly read-only: no Amazon call, no ingestion trigger, no database
write. Routes never accept `organization_id` from the request — it is
always derived from ASI's trusted context inside
`AmazonInventoryReadService`. Every route is scoped by
`marketplace_participation_id`, which the service re-validates against
the caller's organization on every call; a foreign or nonexistent
participation (or inventory row) produces the same sanitized 404.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.amazon.common import public_model_dump
from app.amazon.inventory_read import (
    AmazonInventoryReadService,
    InventoryCollectionResponse,
    InventoryDetail,
    InventorySortField,
    InventorySummaryCountsResponse,
    SortDirection,
    get_amazon_inventory_read_service,
)
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerInventoryNotFoundError,
    PersistenceNotConfiguredError,
)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-inventory"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (AmazonListingsParticipationNotFoundError, AmazonSellerInventoryNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    raise exc


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/inventory/summary",
    response_model=InventorySummaryCountsResponse,
)
def get_inventory_summary(
    marketplace_participation_id: UUID,
    service: AmazonInventoryReadService = Depends(get_amazon_inventory_read_service),
) -> InventorySummaryCountsResponse:
    try:
        summary = service.get_summary(marketplace_participation_id)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(summary)
    return summary


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/inventory",
    response_model=InventoryCollectionResponse,
)
def list_inventory(
    marketplace_participation_id: UUID,
    q: str | None = Query(default=None, max_length=180, description="Search seller SKU, FNSKU, or ASIN"),
    is_active: bool | None = Query(default=None),
    sort_by: InventorySortField = Query(default="last_seen_at"),
    sort_dir: SortDirection = Query(default="desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    service: AmazonInventoryReadService = Depends(get_amazon_inventory_read_service),
) -> InventoryCollectionResponse:
    try:
        result = service.list_inventory(
            marketplace_participation_id,
            search=q,
            is_active=is_active,
            sort_by=sort_by,
            sort_dir=sort_dir,
            offset=offset,
            limit=limit,
        )
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(result)
    return result


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/inventory/{inventory_id}",
    response_model=InventoryDetail,
)
def get_inventory_item(
    marketplace_participation_id: UUID,
    inventory_id: UUID,
    service: AmazonInventoryReadService = Depends(get_amazon_inventory_read_service),
) -> InventoryDetail:
    try:
        detail = service.get_inventory_item(marketplace_participation_id, inventory_id)
    except (PersistenceNotConfiguredError, AmazonSellerInventoryNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(detail)
    return detail

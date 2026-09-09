"""12B.6C — Inventory Health Read API. Strictly read-only: no Amazon
call, no AI call, no ingestion trigger, no database write. Routes never
accept `organization_id` from the request — it is always derived from
ASI's trusted context inside `AmazonInventoryHealthReadService`. Every
route is scoped by `marketplace_participation_id`, which the service
re-validates against the caller's organization on every call; a foreign
or nonexistent participation (or inventory row) produces the same
sanitized 404. The per-SKU evidence endpoint takes the Inventory row's
own UUID, never a raw seller SKU in the path — the collection response
carries `inventory_id` precisely so a client never needs to construct
the evidence URL from an unencoded SKU."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.amazon.common import public_model_dump
from app.amazon.inventory_health_read import (
    AmazonInventoryHealthReadService,
    InventoryHealthCollectionResponse,
    InventoryHealthEvidence,
    InventoryHealthSummaryResponse,
    InventorySortField,
    SortDirection,
    get_amazon_inventory_health_read_service,
)
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerInventoryNotFoundError,
    PersistenceNotConfiguredError,
)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-inventory-health"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (AmazonListingsParticipationNotFoundError, AmazonSellerInventoryNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    raise exc


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/inventory-health/summary",
    response_model=InventoryHealthSummaryResponse,
)
def get_inventory_health_summary(
    marketplace_participation_id: UUID,
    service: AmazonInventoryHealthReadService = Depends(get_amazon_inventory_health_read_service),
) -> InventoryHealthSummaryResponse:
    try:
        summary = service.get_summary(marketplace_participation_id)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(summary)
    return summary


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/inventory-health",
    response_model=InventoryHealthCollectionResponse,
)
def list_inventory_health(
    marketplace_participation_id: UUID,
    q: str | None = Query(default=None, max_length=180, description="Search seller SKU, FNSKU, or ASIN"),
    is_active: bool | None = Query(default=None),
    sort_by: InventorySortField = Query(default="last_seen_at"),
    sort_dir: SortDirection = Query(default="desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    service: AmazonInventoryHealthReadService = Depends(get_amazon_inventory_health_read_service),
) -> InventoryHealthCollectionResponse:
    try:
        result = service.list_inventory_health(
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
    "/marketplace-participations/{marketplace_participation_id}/inventory-health/{inventory_id}/evidence",
    response_model=InventoryHealthEvidence,
)
def get_inventory_health_evidence(
    marketplace_participation_id: UUID,
    inventory_id: UUID,
    service: AmazonInventoryHealthReadService = Depends(get_amazon_inventory_health_read_service),
) -> InventoryHealthEvidence:
    try:
        evidence = service.get_evidence(marketplace_participation_id, inventory_id)
    except (PersistenceNotConfiguredError, AmazonSellerInventoryNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(evidence)
    return evidence

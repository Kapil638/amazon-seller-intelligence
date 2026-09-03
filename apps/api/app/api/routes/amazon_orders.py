"""Amazon Seller Orders Read API. 12B.4D.

Strictly read-only: no Amazon call, no ingestion trigger, no database
write. Routes never accept `organization_id` from the request — it is
always derived from ASI's trusted context inside `AmazonOrdersReadService`.
Every route is scoped by `marketplace_participation_id`, which the service
re-validates against the caller's organization on every call; a foreign or
nonexistent participation (or order) produces the same sanitized 404.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.amazon.common import public_model_dump
from app.amazon.orders_read import (
    AmazonOrdersReadService,
    AmazonSellerOrderNotFoundError,
    FulfilledBy,
    FulfillmentStatus,
    OrderCollectionResponse,
    OrderDetail,
    OrderSortField,
    OrdersSummary,
    SortDirection,
    get_amazon_orders_read_service,
)
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    PersistenceNotConfiguredError,
)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-orders"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (AmazonListingsParticipationNotFoundError, AmazonSellerOrderNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    raise exc


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/orders/summary",
    response_model=OrdersSummary,
)
def get_orders_summary(
    marketplace_participation_id: UUID,
    service: AmazonOrdersReadService = Depends(get_amazon_orders_read_service),
) -> OrdersSummary:
    try:
        summary = service.get_summary(marketplace_participation_id)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(summary)
    return summary


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/orders",
    response_model=OrderCollectionResponse,
)
def list_orders(
    marketplace_participation_id: UUID,
    q: str | None = Query(default=None, max_length=64, description="Search Amazon order id, seller SKU, or ASIN"),
    fulfillment_status: FulfillmentStatus | None = Query(default=None),
    fulfilled_by: FulfilledBy | None = Query(default=None),
    created_after: str | None = Query(default=None, description="ISO-8601 lower bound on amazon_created_at"),
    created_before: str | None = Query(default=None, description="ISO-8601 upper bound on amazon_created_at"),
    sort_by: OrderSortField = Query(default="amazon_last_updated_at"),
    sort_dir: SortDirection = Query(default="desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    service: AmazonOrdersReadService = Depends(get_amazon_orders_read_service),
) -> OrderCollectionResponse:
    parsed_after = datetime.fromisoformat(created_after) if created_after else None
    parsed_before = datetime.fromisoformat(created_before) if created_before else None
    try:
        result = service.list_orders(
            marketplace_participation_id,
            search=q,
            fulfillment_status=fulfillment_status,
            fulfilled_by=fulfilled_by,
            created_after=parsed_after,
            created_before=parsed_before,
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
    "/marketplace-participations/{marketplace_participation_id}/orders/{order_id}",
    response_model=OrderDetail,
)
def get_order(
    marketplace_participation_id: UUID,
    order_id: UUID,
    service: AmazonOrdersReadService = Depends(get_amazon_orders_read_service),
) -> OrderDetail:
    try:
        detail = service.get_order(marketplace_participation_id, order_id)
    except (PersistenceNotConfiguredError, AmazonSellerOrderNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(detail)
    return detail

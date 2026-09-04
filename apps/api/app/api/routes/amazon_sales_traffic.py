"""Amazon Sales and Traffic Business Report Read API. 12B.6A.

Strictly read-only: no Amazon call, no ingestion trigger, no database
write. Routes never accept `organization_id` from the request — it is
always derived from ASI's trusted context inside
`AmazonSalesTrafficReadService`. Every route is scoped by
`marketplace_participation_id`, which the service re-validates against
the caller's organization on every call; a foreign or nonexistent
participation produces the same sanitized 404.
"""

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.amazon.common import public_model_dump
from app.amazon.sales_traffic_read import (
    AmazonSalesTrafficReadService,
    DailyTrendResponse,
    ProductPerformanceResponse,
    ProductSortField,
    SalesTrafficFreshness,
    SalesTrafficSummary,
    SortDirection,
    get_amazon_sales_traffic_read_service,
)
from app.core.exceptions import AmazonListingsParticipationNotFoundError, PersistenceNotConfiguredError

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-sales-traffic"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, AmazonListingsParticipationNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    raise exc


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/sales-traffic/summary",
    response_model=SalesTrafficSummary,
)
def get_sales_traffic_summary(
    marketplace_participation_id: UUID,
    start: date = Query(..., description="Inclusive range start (calendar date)"),
    end: date = Query(..., description="Inclusive range end (calendar date)"),
    service: AmazonSalesTrafficReadService = Depends(get_amazon_sales_traffic_read_service),
) -> SalesTrafficSummary:
    try:
        summary = service.get_summary(marketplace_participation_id, start=start, end=end)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(summary)
    return summary


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/sales-traffic/daily-trend",
    response_model=DailyTrendResponse,
)
def get_sales_traffic_daily_trend(
    marketplace_participation_id: UUID,
    start: date = Query(...),
    end: date = Query(...),
    service: AmazonSalesTrafficReadService = Depends(get_amazon_sales_traffic_read_service),
) -> DailyTrendResponse:
    try:
        trend = service.get_daily_trend(marketplace_participation_id, start=start, end=end)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(trend)
    return trend


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/sales-traffic/products",
    response_model=ProductPerformanceResponse,
)
def list_sales_traffic_products(
    marketplace_participation_id: UUID,
    start: date = Query(...),
    end: date = Query(...),
    q: str | None = Query(default=None, max_length=64, description="Search parent ASIN, child ASIN, or seller SKU"),
    sort_by: ProductSortField = Query(default="ordered_product_sales_amount"),
    sort_dir: SortDirection = Query(default="desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    service: AmazonSalesTrafficReadService = Depends(get_amazon_sales_traffic_read_service),
) -> ProductPerformanceResponse:
    try:
        result = service.list_product_performance(
            marketplace_participation_id,
            start=start, end=end, search=q, sort_by=sort_by, sort_dir=sort_dir, offset=offset, limit=limit,
        )
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(result)
    return result


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/sales-traffic/freshness",
    response_model=SalesTrafficFreshness,
)
def get_sales_traffic_freshness(
    marketplace_participation_id: UUID,
    service: AmazonSalesTrafficReadService = Depends(get_amazon_sales_traffic_read_service),
) -> SalesTrafficFreshness:
    try:
        freshness = service.get_freshness(marketplace_participation_id)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(freshness)
    return freshness

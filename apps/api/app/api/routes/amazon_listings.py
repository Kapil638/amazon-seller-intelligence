"""Amazon Seller Listings Read API. 12B.3E.

Strictly read-only: no Amazon call, no ingestion trigger, no database
write. Routes never accept `organization_id` from the request — it is
always derived from ASI's trusted context inside `AmazonListingsReadService`.
Every route is scoped by `marketplace_participation_id`, which the service
re-validates against the caller's organization on every call; a foreign or
nonexistent participation (or listing) produces the same sanitized 404.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from app.amazon.common import public_model_dump
from app.amazon.listings_read import (
    AmazonListingsReadService,
    IssueSeverity,
    ListingCollectionResponse,
    ListingDetail,
    ListingSortField,
    ListingsSummary,
    SortDirection,
    get_amazon_listings_read_service,
)
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerListingNotFoundError,
    PersistenceNotConfiguredError,
)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-listings"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, PersistenceNotConfiguredError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, (AmazonListingsParticipationNotFoundError, AmazonSellerListingNotFoundError)):
        return HTTPException(status_code=404, detail=str(exc))
    raise exc


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/listings/summary",
    response_model=ListingsSummary,
)
def get_listings_summary(
    marketplace_participation_id: UUID,
    service: AmazonListingsReadService = Depends(get_amazon_listings_read_service),
) -> ListingsSummary:
    try:
        summary = service.get_summary(marketplace_participation_id)
    except (PersistenceNotConfiguredError, AmazonListingsParticipationNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(summary)
    return summary


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/listings",
    response_model=ListingCollectionResponse,
)
def list_listings(
    marketplace_participation_id: UUID,
    q: str | None = Query(default=None, max_length=180, description="Search seller SKU or ASIN"),
    is_active: bool | None = Query(default=None),
    is_buyable: bool | None = Query(default=None),
    is_discoverable: bool | None = Query(default=None),
    has_issues: bool | None = Query(default=None),
    highest_issue_severity: IssueSeverity | None = Query(default=None),
    product_type: str | None = Query(default=None, max_length=64),
    sort_by: ListingSortField = Query(default="last_seen_at"),
    sort_dir: SortDirection = Query(default="desc"),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=25, ge=1, le=100),
    service: AmazonListingsReadService = Depends(get_amazon_listings_read_service),
) -> ListingCollectionResponse:
    try:
        result = service.list_listings(
            marketplace_participation_id,
            search=q,
            is_active=is_active,
            is_buyable=is_buyable,
            is_discoverable=is_discoverable,
            has_issues=has_issues,
            highest_issue_severity=highest_issue_severity,
            product_type=product_type,
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
    "/marketplace-participations/{marketplace_participation_id}/listings/{listing_id}",
    response_model=ListingDetail,
)
def get_listing(
    marketplace_participation_id: UUID,
    listing_id: UUID,
    service: AmazonListingsReadService = Depends(get_amazon_listings_read_service),
) -> ListingDetail:
    try:
        detail = service.get_listing(marketplace_participation_id, listing_id)
    except (PersistenceNotConfiguredError, AmazonSellerListingNotFoundError) as exc:
        raise _http_error(exc) from exc
    public_model_dump(detail)
    return detail

"""12B.3E — Seller Listings Read API (service layer).

Strictly read-only: no Amazon call, no secret resolution, no ingestion
trigger, no database write. Serves data already persisted by 12B.3D's
`AmazonListingsIngestionService`. Routes never accept `organization_id`
from the request — every method here derives it from ASI's existing
trusted context (`current_organization_id()`), exactly like
`AmazonConnectionService`.

Ownership chain enforced by every method: organization -> seller account
(implicitly, via participation) -> marketplace participation -> listing.
Possession of a `marketplace_participation_id` or listing `id` alone is
never sufficient — every repository call in this module re-validates the
participation belongs to the caller's organization before touching any
listing row (see `AmazonSellerListingRepository`'s read methods in
`app.persistence.repositories`). A foreign and a nonexistent participation
(or listing) are indistinguishable to the caller: both raise the same
sanitized not-found error, disclosing nothing about which case occurred.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerListingNotFoundError,
    PersistenceNotConfiguredError,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSellerListing
from app.persistence.repositories import AmazonIngestionRunRepository, AmazonSellerListingRepository

# Conservative ceiling: this is a seller's own catalog (hundreds to low
# thousands of SKUs per the 12B.3B schema docstring), not an unbounded feed.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

ListingsSyncStatus = Literal["never_synchronized", "running", "succeeded", "failed", "partial", "timed_out"]
ListingSortField = Literal["last_seen_at", "first_seen_at", "seller_sku", "asin", "issue_count", "price_amount"]
SortDirection = Literal["asc", "desc"]
IssueSeverity = Literal["ERROR", "WARNING", "INFO"]

_RUN_STATUS_TO_SYNC_STATUS: dict[str, ListingsSyncStatus] = {
    "started": "running",
    "succeeded": "succeeded",
    "failed": "failed",
    "partial": "partial",
    "timed_out": "timed_out",
}


class ListingsSyncEvidence(BaseModel):
    """Distinguishes "never synchronized" from every real run status. A
    successful *connection* validation is never a substitute for this —
    this is built exclusively from `run_type='listings'` rows."""

    model_config = ConfigDict(extra="forbid")

    status: ListingsSyncStatus = "never_synchronized"
    failure_class: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pages_fetched: int | None = None
    records_received: int | None = None
    records_accepted: int | None = None
    records_rejected: int | None = None
    reported_total_results: int | None = None
    pagination_complete: bool | None = None
    # The most recent run that actually *succeeded* — may be an earlier
    # run than the one `status` above describes, if the latest attempt
    # failed. This is the "how fresh is the data actually on screen"
    # signal; `status`/`completed_at` describe the latest attempt, which
    # is not always the same thing.
    last_successful_synchronized_at: datetime | None = None


class ListingsSummary(BaseModel):
    """Aggregate counts for one marketplace participation's listings.
    `active`, `buyable`, and `discoverable` are independent facts, not
    synonyms — a listing may be active but not buyable, or buyable but not
    discoverable."""

    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    total_listings: int
    active_count: int
    inactive_count: int
    buyable_count: int
    not_buyable_count: int
    discoverable_count: int
    not_discoverable_count: int
    with_issues_count: int
    without_issues_count: int
    issue_severity_error_count: int
    issue_severity_warning_count: int
    issue_severity_info_count: int
    with_asin_count: int
    with_consumer_price_count: int
    with_fulfillment_availability_count: int
    sync: ListingsSyncEvidence


class ListingCollectionItem(BaseModel):
    """One row for the forthcoming listings table. Never carries an
    organization id, seller-account id, connection id, secret reference,
    token, lease owner, page token, or raw Amazon payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    seller_sku: str
    asin: str | None
    product_type: str | None
    is_active: bool
    is_buyable: bool
    is_discoverable: bool
    price_amount: Decimal | None
    price_currency: str | None
    issue_count: int
    highest_issue_severity: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_successful_sync_at: datetime | None


class ListingCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ListingCollectionItem] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE


class ListingDetail(BaseModel):
    """One listing's approved fields and approved stored JSON structures.
    Never carries a raw Amazon response, `attributes`, `relationships`,
    procurement data, a page/next token, a credential/secret reference,
    internal lease metadata, or an unrelated ingestion run."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    seller_sku: str
    asin: str | None
    item_name: str | None
    product_type: str | None
    is_active: bool
    is_buyable: bool
    is_discoverable: bool
    price_amount: Decimal | None
    price_currency: str | None
    status: list[str]
    offers: list[dict]
    fulfillment_availability: list[dict]
    issues: list[dict]
    product_types: list[dict]
    issue_count: int
    highest_issue_severity: str | None
    first_seen_at: datetime
    last_seen_at: datetime
    last_successful_sync_at: datetime | None


def _sync_evidence(
    latest_run: AmazonIngestionRun | None,
    latest_successful_run: AmazonIngestionRun | None,
) -> ListingsSyncEvidence:
    if latest_run is None:
        return ListingsSyncEvidence(status="never_synchronized")
    return ListingsSyncEvidence(
        status=_RUN_STATUS_TO_SYNC_STATUS.get(latest_run.status, "never_synchronized"),
        failure_class=latest_run.failure_class,
        started_at=latest_run.started_at,
        completed_at=latest_run.completed_at,
        pages_fetched=latest_run.pages_fetched,
        records_received=latest_run.records_received,
        records_accepted=latest_run.records_accepted,
        records_rejected=latest_run.records_rejected,
        reported_total_results=latest_run.reported_total_results,
        pagination_complete=latest_run.pagination_complete,
        last_successful_synchronized_at=(
            latest_successful_run.completed_at if latest_successful_run is not None else None
        ),
    )


def _collection_item(row: AmazonSellerListing) -> ListingCollectionItem:
    return ListingCollectionItem(
        id=row.id,
        seller_sku=row.seller_sku,
        asin=row.asin,
        product_type=row.product_type,
        is_active=row.is_active,
        is_buyable=row.is_buyable,
        is_discoverable=row.is_discoverable,
        price_amount=row.price_amount,
        price_currency=row.price_currency,
        issue_count=row.issue_count,
        highest_issue_severity=row.highest_issue_severity,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        last_successful_sync_at=row.last_successful_sync_at,
    )


def _detail(row: AmazonSellerListing) -> ListingDetail:
    return ListingDetail(
        id=row.id,
        seller_sku=row.seller_sku,
        asin=row.asin,
        item_name=row.item_name,
        product_type=row.product_type,
        is_active=row.is_active,
        is_buyable=row.is_buyable,
        is_discoverable=row.is_discoverable,
        price_amount=row.price_amount,
        price_currency=row.price_currency,
        status=list(row.status or []),
        offers=list(row.offers or []),
        fulfillment_availability=list(row.fulfillment_availability or []),
        issues=list(row.issues or []),
        product_types=list(row.product_types or []),
        issue_count=row.issue_count,
        highest_issue_severity=row.highest_issue_severity,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        last_successful_sync_at=row.last_successful_sync_at,
    )


class AmazonListingsReadService:
    """Read-only listings summary/collection/detail. No Amazon call, no
    secret resolution, no ingestion trigger, no write."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon listings read is not configured.")

    def get_summary(self, marketplace_participation_id: UUID) -> ListingsSummary:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            counts = AmazonSellerListingRepository(session).get_summary_counts(
                organization_id, marketplace_participation_id
            )
            if counts is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))

            run_repo = AmazonIngestionRunRepository(session)
            latest_run = run_repo.get_latest_listings_run(organization_id, marketplace_participation_id)
            latest_successful_run = run_repo.get_latest_successful_listings_run(
                organization_id, marketplace_participation_id
            )

            return ListingsSummary(
                marketplace_participation_id=marketplace_participation_id,
                total_listings=counts.total,
                active_count=counts.active,
                inactive_count=counts.inactive,
                buyable_count=counts.buyable,
                not_buyable_count=counts.not_buyable,
                discoverable_count=counts.discoverable,
                not_discoverable_count=counts.not_discoverable,
                with_issues_count=counts.with_issues,
                without_issues_count=counts.without_issues,
                issue_severity_error_count=counts.severity_error,
                issue_severity_warning_count=counts.severity_warning,
                issue_severity_info_count=counts.severity_info,
                with_asin_count=counts.with_asin,
                with_consumer_price_count=counts.with_price,
                with_fulfillment_availability_count=counts.with_fulfillment_availability,
                sync=_sync_evidence(latest_run, latest_successful_run),
            )

    def list_listings(
        self,
        marketplace_participation_id: UUID,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        is_buyable: bool | None = None,
        is_discoverable: bool | None = None,
        has_issues: bool | None = None,
        highest_issue_severity: IssueSeverity | None = None,
        product_type: str | None = None,
        sort_by: ListingSortField = "last_seen_at",
        sort_dir: SortDirection = "desc",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> ListingCollectionResponse:
        self._require_persistence()
        limit = min(max(limit, 1), MAX_PAGE_SIZE)
        offset = max(offset, 0)
        organization_id = self._org_id()
        search = search.strip() if search else None

        with session_scope() as session:
            result = AmazonSellerListingRepository(session).list_page(
                organization_id,
                marketplace_participation_id,
                search=search,
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
            if result is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))
            rows, total = result
            items = [_collection_item(row) for row in rows]

        return ListingCollectionResponse(items=items, total=total, offset=offset, limit=limit)

    def get_listing(self, marketplace_participation_id: UUID, listing_id: UUID) -> ListingDetail:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            row = AmazonSellerListingRepository(session).get_detail(
                organization_id, marketplace_participation_id, listing_id
            )
            if row is None:
                # Deliberately the same error regardless of *why* the
                # lookup failed (unknown/foreign participation, or a
                # listing id that does not belong to it) — see the module
                # docstring.
                raise AmazonSellerListingNotFoundError(str(listing_id))
            return _detail(row)


def get_amazon_listings_read_service() -> AmazonListingsReadService:
    return AmazonListingsReadService()

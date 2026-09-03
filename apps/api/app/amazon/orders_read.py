"""12B.4D — Seller Orders Read API (service layer).

Strictly read-only: no Amazon call, no secret resolution, no ingestion
trigger, no database write. Serves data already persisted by 12B.4D's
`AmazonOrdersIngestionService`. Routes never accept `organization_id`
from the request — every method here derives it from ASI's existing
trusted context (`current_organization_id()`), exactly like
`AmazonListingsReadService`.

Ownership chain enforced by every method: organization -> marketplace
participation -> order -> order item. Possession of a `marketplace_
participation_id` or order `id` alone is never sufficient — every
repository call in this module re-validates the participation belongs to
the caller's organization before touching any order row. A foreign and a
nonexistent participation (or order) are indistinguishable to the caller:
both raise the same sanitized not-found error.

No PII field is ever surfaced here — the underlying `AmazonSellerOrder`/
`AmazonSellerOrderItem` tables have none by design (12B.4B), and this
service adds no new field beyond what those tables already store.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerListingNotFoundError,
    PersistenceNotConfiguredError,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSellerOrder
from app.persistence.repositories import (
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
)

# Conservative ceiling, matching Listings' own MAX_PAGE_SIZE precedent —
# this is a seller's own order history, not an unbounded public feed.
MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

OrdersSyncStatus = Literal[
    "never_synchronized", "queued", "running", "waiting_to_retry", "succeeded", "failed", "partial", "timed_out"
]
OrderSortField = Literal["amazon_last_updated_at", "amazon_created_at", "order_total_amount"]
SortDirection = Literal["asc", "desc"]
FulfillmentStatus = Literal[
    "PENDING_AVAILABILITY", "PENDING", "UNSHIPPED", "PARTIALLY_SHIPPED", "SHIPPED", "CANCELLED", "UNFULFILLABLE"
]
FulfilledBy = Literal["MERCHANT", "AMAZON"]

# 12B.4D: mirrors listings_read._RUN_STATUS_TO_SYNC_STATUS exactly — same
# durable-job lifecycle vocabulary, restricted to run_type='orders' rows
# only (12B.4D Phase 6's explicit requirement).
_RUN_STATUS_TO_SYNC_STATUS: dict[str, OrdersSyncStatus] = {
    "queued": "queued",
    "started": "running",
    "waiting_to_retry": "waiting_to_retry",
    "succeeded": "succeeded",
    "failed": "failed",
    "partial": "partial",
    "timed_out": "timed_out",
}

# AmazonSellerListingNotFoundError/AmazonListingsParticipationNotFoundError
# are reused here (not redefined) since they already carry the exact
# "missing vs. foreign are indistinguishable" sanitized semantics this
# module needs, and their names are generic enough ("participation",
# "listing"→ order id here is still an ASI-internal id) to apply without
# implying they are Listings-specific — see app.core.exceptions for their
# definitions. A dedicated AmazonSellerOrderNotFoundError is defined below
# instead of reusing AmazonSellerListingNotFoundError, so error messages
# stay accurate to the actual resource type.


class AmazonSellerOrderNotFoundError(Exception):
    """An order could not be resolved within its (already-validated)
    marketplace participation — same sanitized shape for missing,
    foreign, or cross-participation order ids."""

    def __init__(self, order_id: str) -> None:
        self.order_id = order_id
        super().__init__(f"Order {order_id} was not found.")


class OrdersSyncEvidence(BaseModel):
    """Distinguishes "never synchronized" from every real run status.
    Built exclusively from `run_type='orders'` rows — a successful
    *connection* validation or a Listings sync is never a substitute for
    this (12B.4D Phase 6's explicit requirement)."""

    model_config = ConfigDict(extra="forbid")

    status: OrdersSyncStatus = "never_synchronized"
    failure_class: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pages_fetched: int | None = None
    orders_received: int | None = None
    orders_accepted: int | None = None
    orders_rejected: int | None = None
    items_received: int | None = None
    items_accepted: int | None = None
    items_rejected: int | None = None
    pagination_complete: bool | None = None
    # The most recent run that actually *succeeded* — may be an earlier
    # run than the one `status` above describes, if the latest attempt
    # failed. This is the "how fresh is the data actually on screen"
    # signal, independently available after a later failed run (12B.4D
    # Phase 6's explicit requirement).
    last_successful_synchronized_at: datetime | None = None
    next_retry_at: datetime | None = None


class OrdersSummary(BaseModel):
    """Aggregate counts for one marketplace participation's orders.
    `order_value_sum`/`order_value_currency` are populated only when
    every order in scope shares exactly one currency — never silently
    summed across currencies and presented as one ambiguous number."""

    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    total_orders: int
    cancelled_count: int
    business_order_count: int
    prime_order_count: int
    status_counts: dict[str, int]
    order_value_sum: Decimal | None
    order_value_currency: str | None
    sync: OrdersSyncEvidence


class OrderItemRow(BaseModel):
    """One sanitized item row. Never carries a gift message, cancellation
    free-text reason, customization data, serial numbers, or any raw
    Amazon payload — the underlying table has no such column."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    seller_sku: str
    asin: str | None
    item_name: str | None
    condition_type: str | None
    quantity_ordered: int
    quantity_fulfilled: int | None
    quantity_unfulfilled: int | None
    unit_price_amount: Decimal | None
    unit_price_currency: str | None
    item_proceeds_amount: Decimal | None
    item_proceeds_currency: str | None


class OrderCollectionItem(BaseModel):
    """One row for the Orders table. Never carries an organization id,
    seller-account id, connection id, secret reference, token, lease
    owner, pagination token, or raw Amazon payload."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    amazon_order_id: str
    fulfillment_status: str | None
    fulfilled_by: str | None
    sales_channel_marketplace_name: str | None
    is_business_order: bool
    is_prime: bool
    was_cancelled: bool
    order_total_amount: Decimal | None
    order_total_currency: str | None
    amazon_created_at: datetime | None
    amazon_last_updated_at: datetime | None
    item_count: int


class OrderCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[OrderCollectionItem] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE


class OrderDetail(BaseModel):
    """One order's approved fields, plus its sanitized item rows. Never
    carries a raw Amazon response, a pagination token, a credential/
    secret reference, internal lease metadata, or an unrelated ingestion
    run."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    amazon_order_id: str
    fulfillment_status: str | None
    fulfilled_by: str | None
    sales_channel_name: str | None
    sales_channel_marketplace_id: str | None
    sales_channel_marketplace_name: str | None
    is_business_order: bool
    is_prime: bool
    was_cancelled: bool
    items_shipped_count: int | None
    items_unshipped_count: int | None
    order_total_amount: Decimal | None
    order_total_currency: str | None
    amazon_created_at: datetime | None
    amazon_last_updated_at: datetime | None
    first_seen_at: datetime
    last_seen_at: datetime
    items: list[OrderItemRow]


def _sync_evidence(
    latest_run: AmazonIngestionRun | None,
    latest_successful_run: AmazonIngestionRun | None,
) -> OrdersSyncEvidence:
    if latest_run is None:
        return OrdersSyncEvidence(status="never_synchronized")
    return OrdersSyncEvidence(
        status=_RUN_STATUS_TO_SYNC_STATUS.get(latest_run.status, "never_synchronized"),
        failure_class=latest_run.failure_class,
        queued_at=latest_run.created_at,
        started_at=latest_run.started_at,
        completed_at=latest_run.completed_at,
        pages_fetched=latest_run.pages_fetched,
        orders_received=latest_run.orders_received,
        orders_accepted=latest_run.orders_accepted,
        orders_rejected=latest_run.orders_rejected,
        items_received=latest_run.items_received,
        items_accepted=latest_run.items_accepted,
        items_rejected=latest_run.items_rejected,
        pagination_complete=latest_run.pagination_complete,
        last_successful_synchronized_at=(
            latest_successful_run.completed_at if latest_successful_run is not None else None
        ),
        next_retry_at=latest_run.next_retry_at,
    )


def _collection_item(row: AmazonSellerOrder, item_count: int) -> OrderCollectionItem:
    return OrderCollectionItem(
        id=row.id,
        amazon_order_id=row.amazon_order_id,
        fulfillment_status=row.fulfillment_status,
        fulfilled_by=row.fulfilled_by,
        sales_channel_marketplace_name=row.sales_channel_marketplace_name,
        is_business_order=row.is_business_order,
        is_prime=row.is_prime,
        was_cancelled=row.was_cancelled,
        order_total_amount=row.order_total_amount,
        order_total_currency=row.order_total_currency,
        amazon_created_at=row.amazon_created_at,
        amazon_last_updated_at=row.amazon_last_updated_at,
        item_count=item_count,
    )


def _item_row(item) -> OrderItemRow:
    return OrderItemRow(
        id=item.id,
        seller_sku=item.seller_sku,
        asin=item.asin,
        item_name=item.item_name,
        condition_type=item.condition_type,
        quantity_ordered=item.quantity_ordered,
        quantity_fulfilled=item.quantity_fulfilled,
        quantity_unfulfilled=item.quantity_unfulfilled,
        unit_price_amount=item.unit_price_amount,
        unit_price_currency=item.unit_price_currency,
        item_proceeds_amount=item.item_proceeds_amount,
        item_proceeds_currency=item.item_proceeds_currency,
    )


class AmazonOrdersReadService:
    """Read-only orders summary/collection/detail. No Amazon call, no
    secret resolution, no ingestion trigger, no write."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon orders read is not configured.")

    def get_summary(self, marketplace_participation_id: UUID) -> OrdersSummary:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            counts = AmazonSellerOrderRepository(session).get_summary_counts(
                organization_id, marketplace_participation_id
            )
            if counts is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))

            currencies = {
                c
                for (c,) in session.execute(
                    select(AmazonSellerOrder.order_total_currency).where(
                        AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id,
                        AmazonSellerOrder.order_total_amount.is_not(None),
                    )
                ).all()
                if c is not None
            }
            order_value_sum = counts.order_value_sum if len(currencies) == 1 else None
            order_value_currency = next(iter(currencies)) if len(currencies) == 1 else None

            run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
            latest_run = run_repo.get_latest_orders_run_for_participation(organization_id, marketplace_participation_id)
            latest_successful_run = run_repo.get_latest_successful_orders_run_for_participation(
                organization_id, marketplace_participation_id
            )

            status_counts = {(k or "UNKNOWN"): v for k, v in counts.status_counts.items()}

            return OrdersSummary(
                marketplace_participation_id=marketplace_participation_id,
                total_orders=counts.total,
                cancelled_count=counts.cancelled,
                business_order_count=counts.business,
                prime_order_count=counts.prime,
                status_counts=status_counts,
                order_value_sum=order_value_sum,
                order_value_currency=order_value_currency,
                sync=_sync_evidence(latest_run, latest_successful_run),
            )

    def list_orders(
        self,
        marketplace_participation_id: UUID,
        *,
        search: str | None = None,
        fulfillment_status: FulfillmentStatus | None = None,
        fulfilled_by: FulfilledBy | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        sort_by: OrderSortField = "amazon_last_updated_at",
        sort_dir: SortDirection = "desc",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> OrderCollectionResponse:
        self._require_persistence()
        limit = min(max(limit, 1), MAX_PAGE_SIZE)
        offset = max(offset, 0)
        organization_id = self._org_id()
        search = search.strip() if search else None

        with session_scope() as session:
            result = AmazonSellerOrderRepository(session).list_page(
                organization_id,
                marketplace_participation_id,
                search=search,
                fulfillment_status=fulfillment_status,
                fulfilled_by=fulfilled_by,
                created_after=created_after,
                created_before=created_before,
                sort_by=sort_by,
                sort_dir=sort_dir,
                offset=offset,
                limit=limit,
            )
            if result is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))
            rows, total = result
            item_repo = AmazonSellerOrderItemRepository(session)
            items = [
                _collection_item(
                    row, len(item_repo.list_for_order(organization_id, marketplace_participation_id, row.id))
                )
                for row in rows
            ]

        return OrderCollectionResponse(items=items, total=total, offset=offset, limit=limit)

    def get_order(self, marketplace_participation_id: UUID, order_id: UUID) -> OrderDetail:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            order_repo = AmazonSellerOrderRepository(session)
            row = order_repo.get_order_detail(organization_id, marketplace_participation_id, order_id)
            if row is None:
                raise AmazonSellerOrderNotFoundError(str(order_id))
            item_rows = AmazonSellerOrderItemRepository(session).list_for_order(
                organization_id, marketplace_participation_id, order_id
            )
            return OrderDetail(
                id=row.id,
                amazon_order_id=row.amazon_order_id,
                fulfillment_status=row.fulfillment_status,
                fulfilled_by=row.fulfilled_by,
                sales_channel_name=row.sales_channel_name,
                sales_channel_marketplace_id=row.sales_channel_marketplace_id,
                sales_channel_marketplace_name=row.sales_channel_marketplace_name,
                is_business_order=row.is_business_order,
                is_prime=row.is_prime,
                was_cancelled=row.was_cancelled,
                items_shipped_count=row.items_shipped_count,
                items_unshipped_count=row.items_unshipped_count,
                order_total_amount=row.order_total_amount,
                order_total_currency=row.order_total_currency,
                amazon_created_at=row.amazon_created_at,
                amazon_last_updated_at=row.amazon_last_updated_at,
                first_seen_at=row.first_seen_at,
                last_seen_at=row.last_seen_at,
                items=[_item_row(item) for item in item_rows],
            )


def get_amazon_orders_read_service() -> AmazonOrdersReadService:
    return AmazonOrdersReadService()

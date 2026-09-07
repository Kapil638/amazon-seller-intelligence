"""12B.6B — FBA Inventory Read API (service layer).

Strictly read-only: no Amazon call, no secret resolution, no ingestion
trigger, no database write. Serves data already persisted by
`AmazonInventoryIngestionService`. Routes never accept `organization_id`
from the request — every method here derives it from ASI's existing
trusted context (`current_organization_id()`), exactly like
`AmazonListingsReadService`.

Ownership chain enforced by every method: organization -> seller account
(implicitly, via participation) -> marketplace participation -> inventory
row. Possession of a `marketplace_participation_id` or inventory row `id`
alone is never sufficient.

**FBA-fulfilled inventory only** — every value here comes from
`getInventorySummaries`, which has no visibility into merchant-fulfilled
stock. Callers building a UI on top of this service must label it
accordingly (see `docs/AI_HANDOVER/12B6B_FBA_INVENTORY_INGESTION.md`).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerInventoryNotFoundError,
    PersistenceNotConfiguredError,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSellerInventory
from app.persistence.repositories import AmazonIngestionRunRepository, AmazonSellerInventoryRepository

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

InventorySyncStatus = Literal[
    "never_synchronized", "queued", "running", "waiting_to_retry", "succeeded", "failed", "timed_out"
]
InventorySortField = Literal["last_seen_at", "first_seen_at", "seller_sku", "asin", "fulfillable_quantity", "total_quantity"]
SortDirection = Literal["asc", "desc"]

# Inventory runs never complete as `partial` (accumulate-then-reconcile —
# see `inventory_ingestion.py`'s own docstring), so that status is
# deliberately absent from `InventorySyncStatus` above — exposing it
# would imply a state this domain can never actually produce.
_RUN_STATUS_TO_SYNC_STATUS: dict[str, InventorySyncStatus] = {
    "queued": "queued",
    "started": "running",
    "waiting_to_retry": "waiting_to_retry",
    "succeeded": "succeeded",
    "failed": "failed",
    "timed_out": "timed_out",
}


class InventorySyncEvidence(BaseModel):
    """Distinguishes "never synchronized" from every real run status.
    Mirrors `ListingsSyncEvidence` — see its own docstring."""

    model_config = ConfigDict(extra="forbid")

    status: InventorySyncStatus = "never_synchronized"
    failure_class: str | None = None
    queued_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    pages_fetched: int | None = None
    records_received: int | None = None
    records_accepted: int | None = None
    records_rejected: int | None = None
    pagination_complete: bool | None = None
    # The most recent run that actually *succeeded* — the "how fresh is
    # the data actually on screen" signal, distinct from `status`/
    # `completed_at`, which describe the latest *attempt*.
    last_successful_synchronized_at: datetime | None = None
    next_retry_at: datetime | None = None


class InventorySummaryCountsResponse(BaseModel):
    """Aggregate counts for one marketplace participation's FBA
    inventory."""

    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    total: int
    active_count: int
    inactive_count: int
    with_asin_count: int
    with_fnsku_count: int
    zero_fulfillable_count: int
    sync: InventorySyncEvidence


class InventoryCollectionItem(BaseModel):
    """One row for the FBA Inventory table. Never carries an organization
    id, seller-account id, connection id, secret reference, token, lease
    owner, or raw Amazon payload. Only the top-level quantity buckets are
    exposed here — the granular damage/reserved sub-breakdown is stored
    (see `get_detail`) but deliberately not surfaced in the list view,
    matching the 12B.6B audit's narrowed first product surface."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    seller_sku: str
    condition: str
    asin: str | None
    fnsku: str | None
    product_name: str | None
    total_quantity: int | None
    fulfillable_quantity: int | None
    reserved_total_quantity: int | None
    inbound_total_quantity: int | None
    unfulfillable_total_quantity: int | None
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    amazon_last_updated_time: datetime | None


class InventoryCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[InventoryCollectionItem] = []
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE


class InventoryDetail(BaseModel):
    """One inventory row's full, approved field set — every quantity
    bucket the pinned contract documents, not just the top-level ones
    `InventoryCollectionItem` exposes. Never carries a credential/secret
    reference, internal lease metadata, or an unrelated ingestion run."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    seller_sku: str
    condition: str
    asin: str | None
    fnsku: str | None
    product_name: str | None
    total_quantity: int | None
    fulfillable_quantity: int | None
    inbound_working_quantity: int | None
    inbound_shipped_quantity: int | None
    inbound_receiving_quantity: int | None
    reserved_total_quantity: int | None
    reserved_pending_customer_order_quantity: int | None
    reserved_pending_transshipment_quantity: int | None
    reserved_fc_processing_quantity: int | None
    unfulfillable_total_quantity: int | None
    unfulfillable_customer_damaged_quantity: int | None
    unfulfillable_warehouse_damaged_quantity: int | None
    unfulfillable_distributor_damaged_quantity: int | None
    unfulfillable_carrier_damaged_quantity: int | None
    unfulfillable_defective_quantity: int | None
    unfulfillable_expired_quantity: int | None
    researching_total_quantity: int | None
    researching_quantity_short_term: int | None
    researching_quantity_mid_term: int | None
    researching_quantity_long_term: int | None
    is_active: bool
    first_seen_at: datetime
    last_seen_at: datetime
    amazon_last_updated_time: datetime | None


def _sync_evidence(
    latest_run: AmazonIngestionRun | None,
    latest_successful_run: AmazonIngestionRun | None,
) -> InventorySyncEvidence:
    if latest_run is None:
        return InventorySyncEvidence(status="never_synchronized")
    return InventorySyncEvidence(
        status=_RUN_STATUS_TO_SYNC_STATUS.get(latest_run.status, "never_synchronized"),
        failure_class=latest_run.failure_class,
        queued_at=latest_run.created_at,
        started_at=latest_run.started_at,
        completed_at=latest_run.completed_at,
        pages_fetched=latest_run.pages_fetched,
        records_received=latest_run.records_received,
        records_accepted=latest_run.records_accepted,
        records_rejected=latest_run.records_rejected,
        pagination_complete=latest_run.pagination_complete,
        last_successful_synchronized_at=(
            latest_successful_run.completed_at if latest_successful_run is not None else None
        ),
        next_retry_at=latest_run.next_retry_at,
    )


def _inbound_total(row: AmazonSellerInventory) -> int | None:
    """Amazon's `inventoryDetails` has no single "inbound total" field —
    only the three-way working/shipped/receiving split. Summing them here
    is a display convenience, computed only from fields Amazon itself
    provided (never fabricating a number Amazon didn't give), and is
    distinct from `total_quantity`, which is never recomputed — see
    `AmazonInventoryReadService`'s own module docstring on why
    `total_quantity` is always shown verbatim, never re-derived."""
    parts = [row.inbound_working_quantity, row.inbound_shipped_quantity, row.inbound_receiving_quantity]
    present = [p for p in parts if p is not None]
    if not present:
        return None
    return sum(present)


def _collection_item(row: AmazonSellerInventory) -> InventoryCollectionItem:
    return InventoryCollectionItem(
        id=row.id,
        seller_sku=row.seller_sku,
        condition=row.condition,
        asin=row.asin,
        fnsku=row.fnsku,
        product_name=row.product_name,
        total_quantity=row.total_quantity,
        fulfillable_quantity=row.fulfillable_quantity,
        reserved_total_quantity=row.reserved_total_quantity,
        inbound_total_quantity=_inbound_total(row),
        unfulfillable_total_quantity=row.unfulfillable_total_quantity,
        is_active=row.is_active,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        amazon_last_updated_time=row.amazon_last_updated_time,
    )


def _detail(row: AmazonSellerInventory) -> InventoryDetail:
    return InventoryDetail(
        id=row.id,
        seller_sku=row.seller_sku,
        condition=row.condition,
        asin=row.asin,
        fnsku=row.fnsku,
        product_name=row.product_name,
        total_quantity=row.total_quantity,
        fulfillable_quantity=row.fulfillable_quantity,
        inbound_working_quantity=row.inbound_working_quantity,
        inbound_shipped_quantity=row.inbound_shipped_quantity,
        inbound_receiving_quantity=row.inbound_receiving_quantity,
        reserved_total_quantity=row.reserved_total_quantity,
        reserved_pending_customer_order_quantity=row.reserved_pending_customer_order_quantity,
        reserved_pending_transshipment_quantity=row.reserved_pending_transshipment_quantity,
        reserved_fc_processing_quantity=row.reserved_fc_processing_quantity,
        unfulfillable_total_quantity=row.unfulfillable_total_quantity,
        unfulfillable_customer_damaged_quantity=row.unfulfillable_customer_damaged_quantity,
        unfulfillable_warehouse_damaged_quantity=row.unfulfillable_warehouse_damaged_quantity,
        unfulfillable_distributor_damaged_quantity=row.unfulfillable_distributor_damaged_quantity,
        unfulfillable_carrier_damaged_quantity=row.unfulfillable_carrier_damaged_quantity,
        unfulfillable_defective_quantity=row.unfulfillable_defective_quantity,
        unfulfillable_expired_quantity=row.unfulfillable_expired_quantity,
        researching_total_quantity=row.researching_total_quantity,
        researching_quantity_short_term=row.researching_quantity_short_term,
        researching_quantity_mid_term=row.researching_quantity_mid_term,
        researching_quantity_long_term=row.researching_quantity_long_term,
        is_active=row.is_active,
        first_seen_at=row.first_seen_at,
        last_seen_at=row.last_seen_at,
        amazon_last_updated_time=row.amazon_last_updated_time,
    )


class AmazonInventoryReadService:
    """Read-only inventory summary/collection/detail. No Amazon call, no
    secret resolution, no ingestion trigger, no write.

    **`total_quantity` is always Amazon's own field, verbatim** — never
    independently summed from the sub-buckets. Amazon does not document
    that the sub-buckets are guaranteed to add up to it (12B.6B audit
    finding), so this service never invents that formula."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon inventory read is not configured.")

    def get_summary(self, marketplace_participation_id: UUID) -> InventorySummaryCountsResponse:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            counts = AmazonSellerInventoryRepository(session).get_summary_counts(
                organization_id, marketplace_participation_id
            )
            if counts is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))

            run_repo = AmazonIngestionRunRepository(session)
            latest_run = run_repo.get_latest_inventory_run(organization_id, marketplace_participation_id)
            latest_successful_run = run_repo.get_latest_successful_inventory_run(
                organization_id, marketplace_participation_id
            )

            return InventorySummaryCountsResponse(
                marketplace_participation_id=marketplace_participation_id,
                total=counts.total,
                active_count=counts.active,
                inactive_count=counts.inactive,
                with_asin_count=counts.with_asin,
                with_fnsku_count=counts.with_fnsku,
                zero_fulfillable_count=counts.zero_fulfillable,
                sync=_sync_evidence(latest_run, latest_successful_run),
            )

    def list_inventory(
        self,
        marketplace_participation_id: UUID,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        sort_by: InventorySortField = "last_seen_at",
        sort_dir: SortDirection = "desc",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> InventoryCollectionResponse:
        self._require_persistence()
        limit = min(max(limit, 1), MAX_PAGE_SIZE)
        offset = max(offset, 0)
        organization_id = self._org_id()
        search = search.strip() if search else None

        with session_scope() as session:
            result = AmazonSellerInventoryRepository(session).list_page(
                organization_id,
                marketplace_participation_id,
                search=search,
                is_active=is_active,
                sort_by=sort_by,
                sort_dir=sort_dir,
                offset=offset,
                limit=limit,
            )
            if result is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))
            rows, total = result
            items = [_collection_item(row) for row in rows]

        return InventoryCollectionResponse(items=items, total=total, offset=offset, limit=limit)

    def get_inventory_item(self, marketplace_participation_id: UUID, inventory_id: UUID) -> InventoryDetail:
        self._require_persistence()
        organization_id = self._org_id()
        with session_scope() as session:
            row = AmazonSellerInventoryRepository(session).get_detail(
                organization_id, marketplace_participation_id, inventory_id
            )
            if row is None:
                raise AmazonSellerInventoryNotFoundError(str(inventory_id))
            return _detail(row)


def get_amazon_inventory_read_service() -> AmazonInventoryReadService:
    return AmazonInventoryReadService()

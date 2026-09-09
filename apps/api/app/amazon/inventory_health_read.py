"""12B.6C — Inventory Health Read API (service layer).

Strictly read-only: no Amazon call, no AI call, no secret resolution, no
ingestion trigger, no database write. Serves data already persisted by
the existing Inventory (12B.6B) and Sales & Traffic (12B.6A) ingestion
services — this milestone adds no new ingestion path, no new worker, no
new source table.

Computation happens entirely on read (approved for V1 — no metric
snapshot table). Every formula lives in `inventory_health_formulas.py`
as a pure function; this module's only job is fetching the exact rows
those functions need, in a bounded, N+1-free number of queries, and
assembling the sanitized response models.

Ownership chain enforced by every method: organization -> marketplace
participation -> inventory row, identical to `AmazonInventoryReadService`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.amazon.inventory_health_formulas import (
    FORMULA_VERSION,
    EligibleProductFact,
    ThresholdPolicy,
    classify_coverage,
    compute_velocity,
    demand_eligibility_for_condition,
    freshness_state,
    fulfillable_days_of_cover,
    inventory_state,
    overlays_for,
    potential_days_of_cover,
    potential_units,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    AmazonListingsParticipationNotFoundError,
    AmazonSellerInventoryNotFoundError,
    PersistenceNotConfiguredError,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSalesAndTrafficProductFact, AmazonSellerInventory
from app.persistence.repositories import (
    AmazonIngestionRunRepository,
    AmazonSalesTrafficProductFactRepository,
    AmazonSellerInventoryRepository,
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

InventorySortField = Literal["last_seen_at", "first_seen_at", "seller_sku", "asin", "fulfillable_quantity", "total_quantity"]
SortDirection = Literal["asc", "desc"]

InventorySyncStatus = Literal[
    "never_synchronized", "queued", "running", "waiting_to_retry", "succeeded", "failed", "timed_out"
]
SalesTrafficSyncStatus = Literal[
    "never_synchronized", "queued", "running", "waiting_to_retry", "succeeded", "failed", "partial", "timed_out"
]
_INVENTORY_RUN_STATUS_TO_SYNC_STATUS: dict[str, InventorySyncStatus] = {
    "queued": "queued",
    "started": "running",
    "waiting_to_retry": "waiting_to_retry",
    "succeeded": "succeeded",
    "failed": "failed",
    "timed_out": "timed_out",
}
_SALES_RUN_STATUS_TO_SYNC_STATUS: dict[str, SalesTrafficSyncStatus] = {
    "queued": "queued",
    "started": "running",
    "waiting_to_retry": "waiting_to_retry",
    "succeeded": "succeeded",
    "failed": "failed",
    "partial": "partial",
    "timed_out": "timed_out",
}


class ThresholdsUsed(BaseModel):
    """The exact policy values a response was computed under — ASI/EWise
    product policy, never presented as Amazon guidance. Returned
    alongside `formula_version` on every evidence-bearing response so a
    later threshold change is always visible, never silently rewriting
    the meaning of a previously-seen classification."""

    model_config = ConfigDict(extra="forbid")

    low_coverage_days_threshold: float
    high_coverage_days_threshold: float
    min_eligible_window_days: int
    preferred_window_days: int


def _thresholds_used(cfg: Settings) -> ThresholdsUsed:
    return ThresholdsUsed(
        low_coverage_days_threshold=cfg.inventory_health_low_coverage_days_threshold,
        high_coverage_days_threshold=cfg.inventory_health_high_coverage_days_threshold,
        min_eligible_window_days=cfg.inventory_health_min_eligible_window_days,
        preferred_window_days=cfg.inventory_health_preferred_window_days,
    )


def _policy(cfg: Settings) -> ThresholdPolicy:
    return ThresholdPolicy(
        low_coverage_days_threshold=cfg.inventory_health_low_coverage_days_threshold,
        high_coverage_days_threshold=cfg.inventory_health_high_coverage_days_threshold,
        min_eligible_window_days=cfg.inventory_health_min_eligible_window_days,
        preferred_window_days=cfg.inventory_health_preferred_window_days,
    )


class InventoryHealthInventorySyncEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: InventorySyncStatus = "never_synchronized"
    last_successful_synchronized_at: datetime | None = None


class InventoryHealthSalesTrafficSyncEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SalesTrafficSyncStatus = "never_synchronized"
    last_successful_synchronized_at: datetime | None = None


class InventoryHealthSummaryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    marketplace_participation_id: UUID
    total: int
    counts_by_inventory_state: dict[str, int]
    counts_by_demand_eligibility: dict[str, int]
    formula_version: str
    thresholds: ThresholdsUsed
    inventory_sync: InventoryHealthInventorySyncEvidence
    sales_traffic_sync: InventoryHealthSalesTrafficSyncEvidence


class InventoryHealthRow(BaseModel):
    """One SKU/condition's Inventory Health facts + classification.
    Every numeric field distinguishes a real `0` from `null`
    (unavailable/ineligible) — never one collapsed into the other."""

    model_config = ConfigDict(extra="forbid")

    inventory_id: UUID
    seller_sku: str
    condition: str
    asin: str | None
    fnsku: str | None
    product_name: str | None
    is_active: bool

    # Factual current FBA quantities — Amazon's own fields, verbatim.
    fulfillable_quantity: int | None
    reserved_total_quantity: int | None
    inbound_working_quantity: int | None
    inbound_shipped_quantity: int | None
    inbound_receiving_quantity: int | None
    unfulfillable_total_quantity: int | None
    researching_total_quantity: int | None
    total_quantity: int | None

    # Demand / coverage.
    demand_eligibility: str
    units_per_covered_day: float | None
    sales_window_start: date | None
    sales_window_end: date | None
    sales_covered_days: int | None
    fulfillable_days_of_cover: float | None

    # Inbound-adjusted, separately labeled — never "available stock."
    # Both are strictly all-or-nothing: null whenever any one of
    # fulfillable/inbound_working/inbound_shipped/inbound_receiving is
    # itself null — never a partial sum from only the components that
    # happened to be present. `potential_units_incomplete_inputs=True`
    # is the evidence reason (equivalent to
    # incomplete_inbound_quantity_inputs) whenever that happened.
    potential_units: int | None
    potential_days_of_cover: float | None
    potential_units_incomplete_inputs: bool

    # Classification.
    inventory_state: str
    freshness_state: str
    overlays: list[str]

    # Freshness provenance.
    inventory_observed_at: datetime | None
    amazon_last_updated_time: datetime | None
    sales_traffic_ingestion_completed_at: datetime | None


class InventoryHealthCollectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[InventoryHealthRow] = []
    total: int = 0
    offset: int = 0
    limit: int = DEFAULT_PAGE_SIZE
    formula_version: str = FORMULA_VERSION
    thresholds: ThresholdsUsed


class InventoryHealthEvidence(InventoryHealthRow):
    """Full evidence for one SKU/condition — every input the formulas
    consumed, plus the exact run ids that produced them, so a reader can
    independently verify the classification without trusting the
    computed fields alone."""

    model_config = ConfigDict(extra="forbid")

    inventory_ingestion_run_id: UUID | None
    sales_traffic_ingestion_run_id: UUID | None
    formula_version: str
    thresholds: ThresholdsUsed


def _ensure_aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive `datetime`s for a stored `DateTime(timezone=
    True)` value even though PostgreSQL returns aware ones — the same
    dialect difference `WorkerHeartbeatRepository.check_availability`
    already documents and works around (`repositories.py`), narrowly
    duplicated here rather than imported since this is a read-service
    formatting concern, not a persistence-layer one. Every timestamp
    this module hands to `freshness_state` (which does real
    `now - value` arithmetic) must be normalized first, or SQLite-backed
    tests raise `TypeError: can't subtract offset-naive and
    offset-aware datetimes` while PostgreSQL never would."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _inventory_observed_at(row: AmazonSellerInventory, runs_by_id: dict[UUID, AmazonIngestionRun]) -> datetime | None:
    """ASI provenance, never Amazon's own (optional, sometimes-empty)
    `amazon_last_updated_time` — the correction this milestone's review
    required. The **only** source is the row's own `last_ingestion_
    run_id` joined to that run's `completed_at`, gated on the run
    actually being recorded `status == "succeeded"`.

    No `last_seen_at` fallback — proven unnecessary, not merely
    dropped for caution. `AmazonSellerInventoryRepository._upsert` sets
    `last_ingestion_run_id` and `last_seen_at` together, unconditionally,
    on every call; the *only* caller of `_upsert` is `reconcile_snapshot`,
    called from exactly one place — `AmazonInventoryIngestionService.
    _reconcile` (`inventory_ingestion.py`) — where `AmazonIngestionRun
    Repository.complete_inventory_run(status="succeeded", ...)` (which
    sets `completed_at=func.now()` in the same call) and `reconcile_
    snapshot(...)` both execute inside the **same** `with session_
    scope() as session:` block. `session_scope` (`database.py`) commits
    exactly once at the end of that block and rolls back everything in
    it on any exception — verified directly, not assumed. So whenever
    `last_seen_at` is updated, the very same atomic transaction already
    guarantees `last_ingestion_run_id` points at a run recorded
    `status == "succeeded"` with a non-null `completed_at` in that same
    commit — there is no code path where `last_seen_at` could be newer,
    older, or otherwise divergent from that run's own `completed_at`,
    so a separate fallback read of `last_seen_at` could never add real
    information, only a false sense of a second source.

    A row failing every one of these checks — `last_ingestion_run_id`
    unset, the referenced run not found (wrong organization, or simply
    absent from the batched `runs_by_id` lookup), the run not recorded
    `succeeded`, or `completed_at` still `None` — has provably never
    been reconciled through this path. Its freshness is unknown, not
    silently assumed fresh from any other timestamp on the row; `None`
    here folds into `freshness_state`'s existing `stale_inventory`
    handling for a `None` timestamp, and the response's own
    `inventory_observed_at: null` remains the explicit, visible
    evidence of that — never hidden behind a coarse label alone."""
    run_id = row.last_ingestion_run_id
    if run_id is None:
        return None
    run = runs_by_id.get(run_id)
    if run is None or run.status != "succeeded" or run.completed_at is None:
        return None
    return _ensure_aware(run.completed_at)


def _row_evidence(
    row: AmazonSellerInventory,
    *,
    facts_by_sku: dict[str, list[EligibleProductFact]],
    runs_by_id: dict[UUID, AmazonIngestionRun],
    policy: ThresholdPolicy,
    now: datetime,
    max_inventory_age_seconds: float,
    max_sales_age_seconds: float,
) -> tuple[InventoryHealthRow, UUID | None, UUID | None]:
    """Returns (row, inventory_run_id, sales_traffic_run_id) — the two
    run ids are split out separately so `InventoryHealthEvidence` can
    expose them without `InventoryHealthCollectionResponse`'s lighter
    rows needing to carry them."""
    condition_gate = demand_eligibility_for_condition(row.condition)
    if not row.seller_sku.strip():
        # Defensive only — `inventory_normalization.py` already rejects
        # a blank seller_sku before any row reaches this table, so this
        # branch documents the contract rather than ever firing on real
        # data (see AmazonSellerInventory's own docstring).
        velocity = None
        demand_eligibility = "missing_identity"
        sales_run_id: UUID | None = None
        window_start = window_end = None
        covered = None
    elif condition_gate is not None:
        velocity = None
        demand_eligibility = condition_gate
        sales_run_id = None
        window_start = window_end = None
        covered = None
    else:
        result = compute_velocity(facts_by_sku.get(row.seller_sku, []), policy=policy)
        velocity = result.units_per_covered_day
        demand_eligibility = result.eligibility
        sales_run_id = result.source_run_id
        window_start = result.window_start
        window_end = result.window_end
        covered = result.covered_days

    cover = fulfillable_days_of_cover(row.fulfillable_quantity, velocity)
    coverage_class = classify_coverage(cover, policy=policy)
    state = inventory_state(
        is_active=row.is_active, fulfillable_quantity=row.fulfillable_quantity, coverage_classification=coverage_class
    )
    potential = potential_units(
        fulfillable_quantity=row.fulfillable_quantity,
        inbound_working_quantity=row.inbound_working_quantity,
        inbound_shipped_quantity=row.inbound_shipped_quantity,
        inbound_receiving_quantity=row.inbound_receiving_quantity,
    )
    potential_cover = potential_days_of_cover(potential.potential_units, velocity)
    overlays = overlays_for(
        fulfillable_quantity=row.fulfillable_quantity,
        demand_eligibility=demand_eligibility,
        units_per_day=velocity,
        inbound_working_quantity=row.inbound_working_quantity,
        inbound_shipped_quantity=row.inbound_shipped_quantity,
        inbound_receiving_quantity=row.inbound_receiving_quantity,
        unfulfillable_total_quantity=row.unfulfillable_total_quantity,
        researching_total_quantity=row.researching_total_quantity,
    )

    inventory_observed_at = _inventory_observed_at(row, runs_by_id)
    sales_completed_at = (
        _ensure_aware(runs_by_id[sales_run_id].completed_at)
        if sales_run_id is not None and sales_run_id in runs_by_id
        else None
    )
    freshness = freshness_state(
        inventory_observed_at=inventory_observed_at,
        sales_ingestion_completed_at=sales_completed_at,
        now=now,
        max_inventory_age_seconds=max_inventory_age_seconds,
        max_sales_age_seconds=max_sales_age_seconds,
    )

    health_row = InventoryHealthRow(
        inventory_id=row.id,
        seller_sku=row.seller_sku,
        condition=row.condition,
        asin=row.asin,
        fnsku=row.fnsku,
        product_name=row.product_name,
        is_active=row.is_active,
        fulfillable_quantity=row.fulfillable_quantity,
        reserved_total_quantity=row.reserved_total_quantity,
        inbound_working_quantity=row.inbound_working_quantity,
        inbound_shipped_quantity=row.inbound_shipped_quantity,
        inbound_receiving_quantity=row.inbound_receiving_quantity,
        unfulfillable_total_quantity=row.unfulfillable_total_quantity,
        researching_total_quantity=row.researching_total_quantity,
        total_quantity=row.total_quantity,
        demand_eligibility=demand_eligibility,
        units_per_covered_day=velocity,
        sales_window_start=window_start,
        sales_window_end=window_end,
        sales_covered_days=covered,
        fulfillable_days_of_cover=cover,
        potential_units=potential.potential_units,
        potential_days_of_cover=potential_cover,
        potential_units_incomplete_inputs=potential.incomplete_inputs,
        inventory_state=state,
        freshness_state=freshness,
        overlays=list(overlays),
        inventory_observed_at=inventory_observed_at,
        amazon_last_updated_time=row.amazon_last_updated_time,
        sales_traffic_ingestion_completed_at=sales_completed_at,
    )
    return health_row, row.last_ingestion_run_id, sales_run_id


def _to_eligible_facts(rows: list[AmazonSalesAndTrafficProductFact]) -> list[EligibleProductFact]:
    return [
        EligibleProductFact(
            id=r.id,
            request_window_start=r.request_window_start,
            request_window_end=r.request_window_end,
            units_ordered=r.units_ordered,
            ingestion_run_id=r.last_ingestion_run_id,
        )
        for r in rows
    ]


class AmazonInventoryHealthReadService:
    """Read-only Inventory Health summary/collection/evidence. No Amazon
    call, no AI call, no write. See module docstring."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _org_id(self) -> UUID:
        return current_organization_id()

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Amazon inventory health read is not configured.")

    def _compute_rows(
        self, session, organization_id: UUID, marketplace_participation_id: UUID, inventory_rows: list[AmazonSellerInventory]
    ) -> list[tuple[InventoryHealthRow, UUID | None, UUID | None]]:
        cfg = self._cfg()
        policy = _policy(cfg)
        seller_skus = {row.seller_sku for row in inventory_rows}
        facts_result = AmazonSalesTrafficProductFactRepository(session).get_eligible_sku_facts(
            organization_id, marketplace_participation_id, seller_skus=seller_skus
        )
        facts_by_sku = {sku: _to_eligible_facts(rows) for sku, rows in (facts_result or {}).items()}

        run_ids: set[UUID] = set()
        for row in inventory_rows:
            if row.last_ingestion_run_id is not None:
                run_ids.add(row.last_ingestion_run_id)
        for facts in facts_by_sku.values():
            for fact in facts:
                run_ids.add(fact.ingestion_run_id)
        runs_by_id = AmazonIngestionRunRepository(session).get_by_ids(organization_id, run_ids)

        now = datetime.now(UTC)
        return [
            _row_evidence(
                row,
                facts_by_sku=facts_by_sku,
                runs_by_id=runs_by_id,
                policy=policy,
                now=now,
                max_inventory_age_seconds=cfg.inventory_health_max_inventory_age_seconds,
                max_sales_age_seconds=cfg.inventory_health_max_sales_age_seconds,
            )
            for row in inventory_rows
        ]

    def get_summary(self, marketplace_participation_id: UUID) -> InventoryHealthSummaryResponse:
        self._require_persistence()
        cfg = self._cfg()
        organization_id = self._org_id()
        with session_scope() as session:
            inventory_repo = AmazonSellerInventoryRepository(session)
            # Summary counts intentionally cover every row for this
            # participation (not just one page) — the documented, small
            # expected catalog size (hundreds to low thousands, per
            # AmazonSellerListing's own precedent) makes this a bounded,
            # constant-small number of queries, never one per row.
            page_result = inventory_repo.list_page(organization_id, marketplace_participation_id, offset=0, limit=100_000)
            if page_result is None:
                raise AmazonListingsParticipationNotFoundError(str(marketplace_participation_id))
            all_rows, _total = page_result
            computed = self._compute_rows(session, organization_id, marketplace_participation_id, all_rows)

            counts_by_state: dict[str, int] = {}
            counts_by_eligibility: dict[str, int] = {}
            for health_row, _inv_run, _sales_run in computed:
                counts_by_state[health_row.inventory_state] = counts_by_state.get(health_row.inventory_state, 0) + 1
                counts_by_eligibility[health_row.demand_eligibility] = (
                    counts_by_eligibility.get(health_row.demand_eligibility, 0) + 1
                )

            run_repo = AmazonIngestionRunRepository(session)
            latest_inv_run = run_repo.get_latest_inventory_run(organization_id, marketplace_participation_id)
            latest_successful_inv_run = run_repo.get_latest_successful_inventory_run(
                organization_id, marketplace_participation_id
            )
            latest_sales_run = run_repo.get_latest_sales_traffic_run(organization_id, marketplace_participation_id)
            latest_successful_sales_run = run_repo.get_latest_successful_sales_traffic_run(
                organization_id, marketplace_participation_id
            )

            return InventoryHealthSummaryResponse(
                marketplace_participation_id=marketplace_participation_id,
                total=len(computed),
                counts_by_inventory_state=counts_by_state,
                counts_by_demand_eligibility=counts_by_eligibility,
                formula_version=FORMULA_VERSION,
                thresholds=_thresholds_used(cfg),
                inventory_sync=InventoryHealthInventorySyncEvidence(
                    status=(
                        _INVENTORY_RUN_STATUS_TO_SYNC_STATUS.get(latest_inv_run.status, "never_synchronized")
                        if latest_inv_run is not None
                        else "never_synchronized"
                    ),
                    last_successful_synchronized_at=(
                        latest_successful_inv_run.completed_at if latest_successful_inv_run is not None else None
                    ),
                ),
                sales_traffic_sync=InventoryHealthSalesTrafficSyncEvidence(
                    status=(
                        _SALES_RUN_STATUS_TO_SYNC_STATUS.get(latest_sales_run.status, "never_synchronized")
                        if latest_sales_run is not None
                        else "never_synchronized"
                    ),
                    last_successful_synchronized_at=(
                        latest_successful_sales_run.completed_at if latest_successful_sales_run is not None else None
                    ),
                ),
            )

    def list_inventory_health(
        self,
        marketplace_participation_id: UUID,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        sort_by: InventorySortField = "last_seen_at",
        sort_dir: SortDirection = "desc",
        offset: int = 0,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> InventoryHealthCollectionResponse:
        self._require_persistence()
        cfg = self._cfg()
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
            computed = self._compute_rows(session, organization_id, marketplace_participation_id, rows)
            items = [health_row for health_row, _inv_run, _sales_run in computed]

        return InventoryHealthCollectionResponse(
            items=items, total=total, offset=offset, limit=limit, formula_version=FORMULA_VERSION, thresholds=_thresholds_used(cfg)
        )

    def get_evidence(self, marketplace_participation_id: UUID, inventory_id: UUID) -> InventoryHealthEvidence:
        self._require_persistence()
        cfg = self._cfg()
        organization_id = self._org_id()
        with session_scope() as session:
            row = AmazonSellerInventoryRepository(session).get_detail(
                organization_id, marketplace_participation_id, inventory_id
            )
            if row is None:
                raise AmazonSellerInventoryNotFoundError(str(inventory_id))
            computed = self._compute_rows(session, organization_id, marketplace_participation_id, [row])
            health_row, inv_run_id, sales_run_id = computed[0]

        return InventoryHealthEvidence(
            **health_row.model_dump(),
            inventory_ingestion_run_id=inv_run_id,
            sales_traffic_ingestion_run_id=sales_run_id,
            formula_version=FORMULA_VERSION,
            thresholds=_thresholds_used(cfg),
        )


def get_amazon_inventory_health_read_service() -> AmazonInventoryHealthReadService:
    return AmazonInventoryHealthReadService()

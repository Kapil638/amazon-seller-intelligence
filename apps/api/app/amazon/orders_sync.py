"""12B.4D — Orders synchronization *trigger* (HTTP-facing wrapper).

This module never performs ingestion and never makes an Amazon HTTP
request — it only validates ownership/eligibility and enqueues (or
reports the existing) durable `run_type='orders'` job row via
`AmazonIngestionRunRepository`/`AmazonIngestionRunMarketplaceParticipation
Repository`. A separate worker process (`app.amazon.orders_worker`)
claims and processes queued jobs; nothing in this module blocks waiting
for that to happen.

Organization is never accepted from the caller: it is always
`current_organization_id()`, exactly like every other Amazon service in
this codebase.

Reuses `AmazonOrdersIngestionService._check_scope` for ownership/
eligibility validation rather than duplicating those checks — the same
helper the durable worker path also uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.amazon.common import ensure_utc
from app.amazon.orders_ingestion import AmazonOrdersIngestionService, _ClaimFailure
from app.core.config import Settings, get_settings
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonIngestionRunMarketplaceParticipation
from app.persistence.repositories import (
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonIngestionRunRepository,
)


class OrdersSyncJobStatus(BaseModel):
    """Sanitized durable-job progress contract. Never carries an
    organization id, seller id, connection id, lease owner, credential,
    token reference, pagination token, or raw Amazon response — only
    ASI's own run id (already scoped to the caller's organization by the
    endpoint that returned it), the participation ids the caller already
    supplied, and truthful counters/timestamps."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    run_type: str
    status: str
    marketplace_participation_ids: tuple[UUID, ...]
    pages_fetched: int
    orders_received: int
    orders_accepted: int
    orders_rejected: int
    items_received: int
    items_accepted: int
    items_rejected: int
    pagination_complete: bool
    attempt_count: int
    queued_at: datetime
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    next_retry_at: datetime | None
    completed_at: datetime | None
    failure_class: str | None


@dataclass(frozen=True)
class OrdersSyncTriggerOutcome:
    """`reason` is one of: `"queued"` (a new durable job was created),
    `"already_running"`, `"cooldown"`, `"queue_backlog_limit_reached"`, or
    one of `_check_scope`'s own failure reasons (`"scope_not_found"`,
    `"scope_inactive"`, `"scope_ambiguous"`, `"identity_missing"`,
    `"connection_unresolvable"`). `job` is populated for `"queued"` and
    `"already_running"` (and, where available, `"cooldown"`); always
    `None` for a scope failure, since no run exists to describe.

    Deliberately absent: any reason tied to worker execution capacity
    being full — a legitimate new job is never rejected merely because
    workers are busy; it is accepted as `queued` and simply waits.
    """

    reason: str
    job: OrdersSyncJobStatus | None = None
    retry_allowed_at: datetime | None = None


def _job_status_from_row(row: AmazonIngestionRun, participation_ids: tuple[UUID, ...]) -> OrdersSyncJobStatus:
    attempt_count = row.retry_count + (1 if row.started_at is not None else 0)
    return OrdersSyncJobStatus(
        run_id=row.id,
        run_type=row.run_type,
        status=row.status,
        marketplace_participation_ids=participation_ids,
        pages_fetched=row.pages_fetched,
        orders_received=row.orders_received,
        orders_accepted=row.orders_accepted,
        orders_rejected=row.orders_rejected,
        items_received=row.items_received,
        items_accepted=row.items_accepted,
        items_rejected=row.items_rejected,
        pagination_complete=row.pagination_complete,
        attempt_count=attempt_count,
        queued_at=row.created_at,
        started_at=row.started_at,
        last_heartbeat_at=row.last_heartbeat_at,
        next_retry_at=row.next_retry_at,
        completed_at=row.completed_at,
        failure_class=row.failure_class,
    )


class AmazonOrdersSyncTriggerService:
    """Enqueues a durable Orders job, or reports the caller's existing
    one for the same `(seller_account, region, environment)` scope. Never
    resolves secrets and never calls Amazon itself —
    `AmazonOrdersIngestionService`/`app.amazon.orders_worker` own all of
    that, out of band from this request."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _participation_ids_for_run(self, session, run_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            session.scalars(
                select(AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id).where(
                    AmazonIngestionRunMarketplaceParticipation.ingestion_run_id == run_id
                )
            )
        )

    def trigger(
        self, *, seller_account_id: UUID, marketplace_participation_ids: list[UUID]
    ) -> OrdersSyncTriggerOutcome:
        organization_id = current_organization_id()
        cfg = self._cfg()

        with session_scope() as session:
            try:
                _selling_partner_id, connection_snapshot, region, environment, _participations = (
                    AmazonOrdersIngestionService._check_scope(
                        session,
                        organization_id=organization_id,
                        seller_account_id=seller_account_id,
                        marketplace_participation_ids=marketplace_participation_ids,
                    )
                )
            except _ClaimFailure as exc:
                return OrdersSyncTriggerOutcome(reason=exc.reason)

            runs = AmazonIngestionRunRepository(session)

            active = runs.get_active_orders_run(organization_id, seller_account_id, region, environment)
            if active is not None:
                participation_ids = self._participation_ids_for_run(session, active.id)
                return OrdersSyncTriggerOutcome(
                    reason="already_running", job=_job_status_from_row(active, participation_ids)
                )

            latest = runs.get_latest_cooldown_relevant_orders_run(
                organization_id, seller_account_id, region, environment
            )
            if latest is not None and cfg.orders_sync_trigger_cooldown_seconds > 0:
                anchor = latest.completed_at if latest.completed_at is not None else latest.created_at
                db_now = session.execute(select(func.now())).scalar_one()
                retry_allowed_at = ensure_utc(anchor) + timedelta(seconds=cfg.orders_sync_trigger_cooldown_seconds)
                if ensure_utc(db_now) < retry_allowed_at:
                    participation_ids = self._participation_ids_for_run(session, latest.id)
                    return OrdersSyncTriggerOutcome(
                        reason="cooldown",
                        job=_job_status_from_row(latest, participation_ids),
                        retry_allowed_at=retry_allowed_at,
                    )

            if runs.count_queued_orders_runs_for_organization(organization_id) >= cfg.orders_sync_max_queued_per_organization:
                return OrdersSyncTriggerOutcome(reason="queue_backlog_limit_reached")

            claim = AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                connection_id=connection_snapshot.id,
                marketplace_participation_ids=marketplace_participation_ids,
                region=region,
                environment=environment,
            )
            if not claim.claimed:
                raced = runs.get_active_orders_run(organization_id, seller_account_id, region, environment)
                if raced is None:
                    return OrdersSyncTriggerOutcome(reason="already_running")
                participation_ids = self._participation_ids_for_run(session, raced.id)
                return OrdersSyncTriggerOutcome(
                    reason="already_running", job=_job_status_from_row(raced, participation_ids)
                )

            session.flush()
            new_row = session.get(AmazonIngestionRun, claim.run_id)
            participation_ids = self._participation_ids_for_run(session, claim.run_id)
            return OrdersSyncTriggerOutcome(reason="queued", job=_job_status_from_row(new_row, participation_ids))

    def get_status(self, run_id: UUID) -> OrdersSyncJobStatus | None:
        """Independently re-validates organization ownership (never
        trusts that `run_id` was ever legitimately handed to this caller)
        and requires the row to be an Orders run. Returns `None` — never
        raises — for a foreign, wrong-run-type, or nonexistent run."""
        organization_id = current_organization_id()
        with session_scope() as session:
            row = session.get(AmazonIngestionRun, run_id)
            if row is None or row.organization_id != organization_id or row.run_type != "orders":
                return None
            participation_ids = self._participation_ids_for_run(session, run_id)
            return _job_status_from_row(row, participation_ids)


def get_amazon_orders_sync_service() -> AmazonOrdersSyncTriggerService:
    return AmazonOrdersSyncTriggerService()

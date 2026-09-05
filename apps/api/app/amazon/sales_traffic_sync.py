"""12B.6A — Sales and Traffic report synchronization *trigger*
(HTTP-facing wrapper).

Mirrors `app.amazon.orders_sync`'s own scope/shape exactly, simplified for
this report type's single-participation scope (no association table, no
multi-participation request shape — the pinned Reports API contract
allows exactly one `marketplaceId` per report request, handover doc §1).

This module never performs ingestion and never makes an Amazon HTTP
request — it only validates ownership/eligibility and enqueues (or
reports the existing) durable `run_type='sales_and_traffic_report'` job
row via `AmazonIngestionRunRepository.enqueue_sales_traffic_run`. A
separate worker process (`app.amazon.sales_traffic_worker`) claims and
processes queued jobs; nothing here blocks waiting for that to happen.

Organization is never accepted from the caller: it is always
`current_organization_id()`, exactly like every other Amazon service in
this codebase.

**Deliberately out of scope for this pass** (honestly deferred, not
silently missing): there is no automatic incremental scheduler that
reads the sync checkpoint and computes the next window on its own — the
caller supplies the requested window explicitly on every trigger. Wiring
an automatic "resume from checkpoint" scheduler is a natural 12B.6B
follow-up, not invented here without an explicit approved slice for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from uuid import UUID

from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from app.amazon.common import ensure_utc
from app.core.config import Settings, get_settings
from app.core.exceptions import SpApiConfigurationError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

_VALID_DATE_GRANULARITIES = frozenset({"DAY", "WEEK", "MONTH"})
_VALID_ASIN_GRANULARITIES = frozenset({"PARENT", "CHILD", "SKU"})


class SalesTrafficSyncJobStatus(BaseModel):
    """Sanitized durable-job progress contract. Never carries an
    organization id, seller id, connection id, lease owner, report id,
    report document id, or raw Amazon response — only ASI's own run id
    (already scoped to the caller's organization by the endpoint that
    returned it) and truthful status/window/timestamps."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    run_type: str
    status: str
    marketplace_participation_id: UUID
    data_start_time: date | None
    data_end_time: date | None
    date_granularity: str | None
    report_processing_status: str | None
    queued_at: datetime
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    next_retry_at: datetime | None
    completed_at: datetime | None
    failure_class: str | None


@dataclass(frozen=True)
class SalesTrafficSyncTriggerOutcome:
    """`reason` is one of: `"queued"`, `"already_running"`, `"cooldown"`,
    `"scope_not_found"`, `"scope_inactive"`, `"connection_unresolvable"`,
    or `"invalid_request"`. `job` is populated for `"queued"`,
    `"already_running"`, and `"cooldown"`; always `None` for a scope or
    validation failure, since no run exists to describe."""

    reason: str
    job: SalesTrafficSyncJobStatus | None = None
    retry_allowed_at: datetime | None = None


def _job_status_from_row(row: AmazonIngestionRun) -> SalesTrafficSyncJobStatus:
    return SalesTrafficSyncJobStatus(
        run_id=row.id,
        run_type=row.run_type,
        status=row.status,
        marketplace_participation_id=row.marketplace_participation_id,
        data_start_time=row.report_data_start_time,
        data_end_time=row.report_data_end_time,
        date_granularity=row.report_date_granularity,
        report_processing_status=row.report_processing_status,
        queued_at=row.created_at,
        started_at=row.started_at,
        last_heartbeat_at=row.last_heartbeat_at,
        next_retry_at=row.next_retry_at,
        completed_at=row.completed_at,
        failure_class=row.failure_class,
    )


class AmazonSalesTrafficSyncTriggerService:
    """Enqueues a durable Sales and Traffic report job, or reports the
    caller's existing one for the same marketplace participation. Never
    resolves secrets and never calls Amazon itself —
    `AmazonSalesTrafficIngestionService`/`app.amazon.sales_traffic_worker`
    own all of that, out of band from this request."""

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def trigger(
        self,
        *,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
        data_start_time: date,
        data_end_time: date,
        date_granularity: str = "DAY",
        asin_granularity: str = "SKU",
    ) -> SalesTrafficSyncTriggerOutcome:
        organization_id = current_organization_id()
        cfg = self._cfg()

        if date_granularity not in _VALID_DATE_GRANULARITIES:
            return SalesTrafficSyncTriggerOutcome(reason="invalid_request")
        if asin_granularity not in _VALID_ASIN_GRANULARITIES:
            return SalesTrafficSyncTriggerOutcome(reason="invalid_request")
        if data_start_time > data_end_time:
            return SalesTrafficSyncTriggerOutcome(reason="invalid_request")

        with session_scope() as session:
            seller_account = AmazonSellerAccountRepository(session).get_by_id(organization_id, seller_account_id)
            if seller_account is None:
                return SalesTrafficSyncTriggerOutcome(reason="scope_not_found")
            participation = AmazonMarketplaceParticipationRepository(session).get_by_id(
                organization_id, marketplace_participation_id
            )
            if participation is None or participation.seller_account_id != seller_account_id:
                return SalesTrafficSyncTriggerOutcome(reason="scope_not_found")
            connection = AmazonConnectionRepository(session).get_by_id(organization_id, participation.connection_id)
            if connection is None or connection.token_reference is None:
                return SalesTrafficSyncTriggerOutcome(reason="connection_unresolvable")
            if connection.status != "connected":
                return SalesTrafficSyncTriggerOutcome(reason="scope_inactive")

            runs = AmazonIngestionRunRepository(session)

            active = runs.get_active_sales_traffic_run(organization_id, marketplace_participation_id)
            if active is not None:
                return SalesTrafficSyncTriggerOutcome(reason="already_running", job=_job_status_from_row(active))

            latest = runs.get_latest_sales_traffic_run(organization_id, marketplace_participation_id)
            if (
                latest is not None
                and latest.status in {"succeeded", "partial", "failed", "timed_out"}
                and cfg.sales_traffic_sync_trigger_cooldown_seconds > 0
            ):
                anchor = latest.completed_at if latest.completed_at is not None else latest.created_at
                db_now = session.execute(select(func.now())).scalar_one()
                retry_allowed_at = ensure_utc(anchor) + timedelta(
                    seconds=cfg.sales_traffic_sync_trigger_cooldown_seconds
                )
                if ensure_utc(db_now) < retry_allowed_at:
                    return SalesTrafficSyncTriggerOutcome(
                        reason="cooldown", job=_job_status_from_row(latest), retry_allowed_at=retry_allowed_at
                    )

            try:
                claim = runs.enqueue_sales_traffic_run(
                    organization_id=organization_id,
                    seller_account_id=seller_account_id,
                    marketplace_participation_id=marketplace_participation_id,
                    region=participation.region,
                    environment=connection.environment,
                    connection_id=connection.id,
                    data_start_time=data_start_time,
                    data_end_time=data_end_time,
                    date_granularity=date_granularity,
                    asin_granularity=asin_granularity,
                )
            except (TypeError, SpApiConfigurationError):
                return SalesTrafficSyncTriggerOutcome(reason="invalid_request")

            if not claim.claimed:
                raced = runs.get_active_sales_traffic_run(organization_id, marketplace_participation_id)
                if raced is None:
                    return SalesTrafficSyncTriggerOutcome(reason="already_running")
                return SalesTrafficSyncTriggerOutcome(reason="already_running", job=_job_status_from_row(raced))

            session.flush()
            new_row = session.get(AmazonIngestionRun, claim.run_id)
            return SalesTrafficSyncTriggerOutcome(reason="queued", job=_job_status_from_row(new_row))

    def get_status(self, run_id: UUID) -> SalesTrafficSyncJobStatus | None:
        """Independently re-validates organization ownership (never
        trusts that `run_id` was ever legitimately handed to this caller)
        and requires the row to be a Sales and Traffic run. Returns
        `None` — never raises — for a foreign, wrong-run-type, or
        nonexistent run."""
        organization_id = current_organization_id()
        with session_scope() as session:
            row = session.get(AmazonIngestionRun, run_id)
            if row is None or row.organization_id != organization_id or row.run_type != "sales_and_traffic_report":
                return None
            return _job_status_from_row(row)


def get_amazon_sales_traffic_sync_service() -> AmazonSalesTrafficSyncTriggerService:
    return AmazonSalesTrafficSyncTriggerService()

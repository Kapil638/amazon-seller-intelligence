"""12B.3G — Listings synchronization *trigger* (HTTP-facing wrapper).

Durable-job version. This module never performs ingestion and never makes
an Amazon HTTP request — it only validates ownership/eligibility and
enqueues (or reports the existing) durable `run_type='listings'` job row
via `AmazonIngestionRunRepository`. A separate worker process
(`app.amazon.listings_worker`) claims and processes queued jobs; nothing
in this module blocks waiting for that to happen.

Organization is never accepted from the caller: it is always
`current_organization_id()`, exactly like every other Amazon service in
this codebase.

Reuses `AmazonListingsIngestionService._check_scope` for ownership/
eligibility validation rather than duplicating those checks — the same
helper the synchronous path and the durable worker path both already use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.amazon.common import ensure_utc
from app.amazon.listings_ingestion import AmazonListingsIngestionService, _ClaimFailure
from app.core.config import Settings, get_settings
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun
from app.persistence.repositories import (
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
)


class ListingsSyncJobStatus(BaseModel):
    """Sanitized durable-job progress contract (12B.3G). Never carries an
    organization id, seller id, connection id, lease owner, credential,
    token reference, page token, or raw Amazon response — only ASI's own
    run id (already scoped to the caller's organization by the endpoint
    that returned it), the participation id the caller already supplied,
    and truthful counters/timestamps."""

    model_config = ConfigDict(extra="forbid")

    run_id: UUID
    run_type: str
    status: str
    marketplace_participation_id: UUID
    pages_fetched: int
    records_received: int
    records_accepted: int
    records_rejected: int
    reported_total_results: int | None
    pagination_complete: bool
    attempt_count: int
    queued_at: datetime
    started_at: datetime | None
    last_heartbeat_at: datetime | None
    next_retry_at: datetime | None
    completed_at: datetime | None
    failure_class: str | None


@dataclass(frozen=True)
class ListingsSyncTriggerOutcome:
    """`reason` is one of: `"queued"` (a new durable job was created),
    `"already_running"`, `"cooldown"`, `"queue_backlog_limit_reached"`
    (this organization's *queue* — never worker execution capacity — has
    grown unreasonably large; see `count_queued_listings_runs_for_
    organization`), or one of `_check_scope`'s own failure reasons
    (`"scope_not_found"`, `"scope_inactive"`, `"identity_missing"`,
    `"connection_unresolvable"`). `job` is populated for `"queued"` and
    `"already_running"` (and, where available, `"cooldown"`) so the caller
    has something concrete to show/poll; it is always `None` for a scope
    failure, since no run exists to describe.

    Deliberately absent: any reason tied to worker execution capacity
    being full. A legitimate new job is never rejected merely because
    workers are busy — it is accepted as `queued` and simply waits;
    `claim_next_listings_job`'s own `started`-only counts are the only
    place execution capacity is enforced, strictly at claim time."""

    reason: str
    job: ListingsSyncJobStatus | None = None


def _job_status_from_row(row: AmazonIngestionRun) -> ListingsSyncJobStatus:
    # `retry_count` only increments on a genuine re-claim from
    # `waiting_to_retry` (see `claim_next_listings_job`); a job that has
    # been claimed at least once (`started_at` set) is on at least its
    # first attempt, so attempt_count is 0 only while still `queued`.
    attempt_count = row.retry_count + (1 if row.started_at is not None else 0)
    return ListingsSyncJobStatus(
        run_id=row.id,
        run_type=row.run_type,
        status=row.status,
        marketplace_participation_id=row.marketplace_participation_id,
        pages_fetched=row.pages_fetched,
        records_received=row.records_received,
        records_accepted=row.records_accepted,
        records_rejected=row.records_rejected,
        reported_total_results=row.reported_total_results,
        pagination_complete=row.pagination_complete,
        attempt_count=attempt_count,
        queued_at=row.created_at,
        started_at=row.started_at,
        last_heartbeat_at=row.last_heartbeat_at,
        next_retry_at=row.next_retry_at,
        completed_at=row.completed_at,
        failure_class=row.failure_class,
    )


class AmazonListingsSyncTriggerService:
    """Enqueues a durable Listings job, or reports the caller's existing
    one. Never resolves secrets and never calls Amazon itself —
    `AmazonListingsIngestionService`/`app.amazon.listings_worker` own all
    of that, out of band from this request.
    """

    def __init__(self, *, settings: Settings | None = None) -> None:
        self._settings = settings

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def trigger(self, marketplace_participation_id: UUID) -> ListingsSyncTriggerOutcome:
        organization_id = current_organization_id()
        cfg = self._cfg()

        with session_scope() as session:
            participation = AmazonMarketplaceParticipationRepository(session).get_by_id(
                organization_id, marketplace_participation_id
            )
            if participation is None:
                return ListingsSyncTriggerOutcome(reason="scope_not_found")
            seller_account_id = participation.seller_account_id

            try:
                _selling_partner_id, _marketplace_id, connection_snapshot, region, environment = (
                    AmazonListingsIngestionService._check_scope(
                        session,
                        organization_id=organization_id,
                        seller_account_id=seller_account_id,
                        marketplace_participation_id=marketplace_participation_id,
                    )
                )
            except _ClaimFailure as exc:
                return ListingsSyncTriggerOutcome(reason=exc.reason)

            runs = AmazonIngestionRunRepository(session)

            active = runs.get_active_listings_run(organization_id, marketplace_participation_id)
            if active is not None:
                return ListingsSyncTriggerOutcome(reason="already_running", job=_job_status_from_row(active))

            latest = runs.get_latest_listings_run(organization_id, marketplace_participation_id)
            if latest is not None and latest.created_at is not None:
                elapsed_seconds = (datetime.now(UTC) - ensure_utc(latest.created_at)).total_seconds()
                if elapsed_seconds < cfg.listings_sync_trigger_cooldown_seconds:
                    return ListingsSyncTriggerOutcome(reason="cooldown", job=_job_status_from_row(latest))

            # Queue-backlog safety valve only — deliberately NOT a worker-
            # execution-capacity check. A legitimate new job must never be
            # rejected merely because workers are currently busy; that is
            # enforced separately, only at claim time, by
            # `claim_next_listings_job`'s own `started`-only counts.
            if (
                runs.count_queued_listings_runs_for_organization(organization_id)
                >= cfg.listings_sync_max_queued_per_organization
            ):
                return ListingsSyncTriggerOutcome(reason="queue_backlog_limit_reached")

            claim = runs.enqueue_listings_run(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                region=region,
                environment=environment,
                connection_id=connection_snapshot.id,
            )
            if not claim.claimed:
                # Lost a race with a concurrent trigger/worker between our
                # own active-run check and the enqueue attempt itself — the
                # partial unique index is the actual source of truth here,
                # this is only a friendlier response than a raw conflict.
                raced = runs.get_active_listings_run(organization_id, marketplace_participation_id)
                return ListingsSyncTriggerOutcome(
                    reason="already_running",
                    job=_job_status_from_row(raced) if raced is not None else None,
                )

            session.flush()
            new_row = session.get(AmazonIngestionRun, claim.run_id)
            return ListingsSyncTriggerOutcome(reason="queued", job=_job_status_from_row(new_row))

    def get_status(self, marketplace_participation_id: UUID, run_id: UUID) -> ListingsSyncJobStatus | None:
        """Independently re-validates organization ownership (never trusts
        that `run_id` was ever legitimately handed to this caller) and
        requires the row to be a Listings run for this exact
        participation. Returns `None` — never raises — for a foreign,
        mismatched-participation, wrong-run-type, or nonexistent run: all
        four cases must be indistinguishable to the caller."""
        organization_id = current_organization_id()
        with session_scope() as session:
            participation = AmazonMarketplaceParticipationRepository(session).get_by_id(
                organization_id, marketplace_participation_id
            )
            if participation is None:
                return None
            row = session.get(AmazonIngestionRun, run_id)
            if (
                row is None
                or row.organization_id != organization_id
                or row.run_type != "listings"
                or row.marketplace_participation_id != marketplace_participation_id
            ):
                return None
            return _job_status_from_row(row)


def get_amazon_listings_sync_service() -> AmazonListingsSyncTriggerService:
    return AmazonListingsSyncTriggerService()

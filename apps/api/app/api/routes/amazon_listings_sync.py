"""Amazon Seller Listings Synchronization Trigger. 12B.3G — durable job.

Deliberately a *separate* router/file from `amazon_listings.py`, whose own
docstring documents it as strictly read-only — this remains the one and
only write/ingestion-triggering endpoint for Seller Listings.

Neither route here performs Amazon ingestion or blocks on it. `POST
.../listings/sync` enqueues (or reports the existing) durable
`run_type='listings'` job and returns immediately (`202` for a newly
queued job); `GET .../listings/sync/{run_id}` reports that job's current
sanitized progress. A separate worker process
(`app.amazon.listings_worker`) is what actually claims and processes
queued jobs — this module never calls Amazon directly, never resolves
secrets, and never bypasses the existing single-writer guarantee.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.amazon.common import public_model_dump
from app.amazon.listings_sync import (
    AmazonListingsSyncTriggerService,
    ListingsSyncJobStatus,
    get_amazon_listings_sync_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-listings-sync"])

_TRIGGER_MESSAGES: dict[str, str] = {
    "already_running": "A Listings synchronization is already running for this marketplace.",
    "scope_not_found": "This marketplace was not found.",
    "scope_inactive": "This marketplace or seller account is not active.",
    "identity_missing": "This seller account is missing required identity information.",
    "connection_unresolvable": "No Amazon connection is bound to this marketplace.",
    "cooldown": "Please wait a moment before synchronizing this marketplace again.",
    "queue_backlog_limit_reached": (
        "Too many Listings synchronizations are already queued for this account. Try again shortly."
    ),
}
_DEFAULT_FAILURE_MESSAGE = "Listings synchronization could not be started."
_JOB_NOT_FOUND_MESSAGE = "This synchronization job was not found."


class ListingsSyncTriggerResponse(BaseModel):
    """Sanitized trigger outcome. Never carries a seller ID, marketplace
    ID, token, lease owner, page token, or raw Amazon payload."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    message: str | None = None
    job: ListingsSyncJobStatus | None = None
    # Populated only for reason="cooldown" — the database-computed moment
    # after which a new trigger will be accepted again. Never derived
    # from or compared against the caller's own clock.
    retry_allowed_at: datetime | None = None


def _status_for_trigger_reason(reason: str) -> int:
    if reason == "scope_not_found":
        return 404
    if reason in {"scope_inactive", "identity_missing", "connection_unresolvable"}:
        return 503
    if reason == "already_running":
        return 409
    if reason in {"cooldown", "queue_backlog_limit_reached"}:
        return 429
    return 500


@router.post(
    "/marketplace-participations/{marketplace_participation_id}/listings/sync",
    response_model=ListingsSyncTriggerResponse,
    status_code=202,
)
async def sync_listings(
    marketplace_participation_id: UUID,
    service: AmazonListingsSyncTriggerService = Depends(get_amazon_listings_sync_service),
) -> ListingsSyncTriggerResponse:
    try:
        outcome = service.trigger(marketplace_participation_id)
    except Exception as exc:
        # Enqueueing is a short, local database operation with no Amazon
        # call in its path — an unexpected exception here is a genuine
        # defect, not an ordinary business outcome. Never let its own
        # message reach the client.
        logger.warning("amazon listings sync trigger raised an unexpected exception")
        raise HTTPException(status_code=500, detail=_DEFAULT_FAILURE_MESSAGE) from exc

    response = ListingsSyncTriggerResponse(
        reason=outcome.reason,
        message=None if outcome.reason == "queued" else _TRIGGER_MESSAGES.get(outcome.reason, _DEFAULT_FAILURE_MESSAGE),
        job=outcome.job,
        retry_allowed_at=outcome.retry_allowed_at,
    )
    public_model_dump(response)

    if outcome.reason == "queued":
        return response

    status_code = _status_for_trigger_reason(outcome.reason)
    raise HTTPException(status_code=status_code, detail=response.model_dump(mode="json"))


@router.get(
    "/marketplace-participations/{marketplace_participation_id}/listings/sync/{run_id}",
    response_model=ListingsSyncJobStatus,
)
def get_listings_sync_status(
    marketplace_participation_id: UUID,
    run_id: UUID,
    service: AmazonListingsSyncTriggerService = Depends(get_amazon_listings_sync_service),
) -> ListingsSyncJobStatus:
    job = service.get_status(marketplace_participation_id, run_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_JOB_NOT_FOUND_MESSAGE)
    public_model_dump(job)
    return job

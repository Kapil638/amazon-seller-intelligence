"""Amazon Seller Orders Synchronization Trigger. 12B.4D — durable job.

Deliberately a *separate* router/file from `amazon_orders.py`, whose own
docstring documents it as strictly read-only — this remains the one and
only write/ingestion-triggering endpoint for Seller Orders.

Neither route here performs Amazon ingestion or blocks on it. `POST
.../orders/sync` enqueues (or reports the existing) durable
`run_type='orders'` job and returns immediately (`202` for a newly queued
job); `GET .../orders/sync/{run_id}` reports that job's current sanitized
progress. A separate worker process (`app.amazon.orders_worker`) is what
actually claims and processes queued jobs — this module never calls
Amazon directly, never resolves secrets, and never bypasses the existing
single-writer guarantee.
"""

from __future__ import annotations

import logging
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.amazon.common import public_model_dump
from app.amazon.orders_sync import (
    AmazonOrdersSyncTriggerService,
    OrdersSyncJobStatus,
    get_amazon_orders_sync_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-orders-sync"])

_TRIGGER_MESSAGES: dict[str, str] = {
    "already_running": "An Orders synchronization is already running for this seller account.",
    "scope_not_found": "This seller account or marketplace was not found.",
    "scope_inactive": "This marketplace or seller account is not active.",
    "scope_ambiguous": "The requested marketplaces are not all served by the same connection/region.",
    "identity_missing": "This seller account is missing required identity information.",
    "connection_unresolvable": "No Amazon connection is bound to this seller account.",
    "cooldown": "Please wait a moment before synchronizing Orders again.",
    "queue_backlog_limit_reached": (
        "Too many Orders synchronizations are already queued for this account. Try again shortly."
    ),
}
_DEFAULT_FAILURE_MESSAGE = "Orders synchronization could not be started."
_JOB_NOT_FOUND_MESSAGE = "This synchronization job was not found."


class OrdersSyncTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_account_id: UUID
    marketplace_participation_ids: list[UUID]


class OrdersSyncTriggerResponse(BaseModel):
    """Sanitized trigger outcome. Never carries a seller ID, marketplace
    ID, token, lease owner, pagination token, or raw Amazon payload."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    message: str | None = None
    job: OrdersSyncJobStatus | None = None
    retry_allowed_at: datetime | None = None


def _status_for_trigger_reason(reason: str) -> int:
    if reason == "scope_not_found":
        return 404
    if reason in {"scope_inactive", "scope_ambiguous", "identity_missing", "connection_unresolvable"}:
        return 503
    if reason == "already_running":
        return 409
    if reason in {"cooldown", "queue_backlog_limit_reached"}:
        return 429
    return 500


@router.post("/orders/sync", response_model=OrdersSyncTriggerResponse, status_code=202)
async def sync_orders(
    request: OrdersSyncTriggerRequest,
    service: AmazonOrdersSyncTriggerService = Depends(get_amazon_orders_sync_service),
) -> OrdersSyncTriggerResponse:
    try:
        outcome = service.trigger(
            seller_account_id=request.seller_account_id,
            marketplace_participation_ids=request.marketplace_participation_ids,
        )
    except Exception as exc:
        logger.warning("amazon orders sync trigger raised an unexpected exception")
        raise HTTPException(status_code=500, detail=_DEFAULT_FAILURE_MESSAGE) from exc

    response = OrdersSyncTriggerResponse(
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


@router.get("/orders/sync/{run_id}", response_model=OrdersSyncJobStatus)
def get_orders_sync_status(
    run_id: UUID,
    service: AmazonOrdersSyncTriggerService = Depends(get_amazon_orders_sync_service),
) -> OrdersSyncJobStatus:
    job = service.get_status(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_JOB_NOT_FOUND_MESSAGE)
    public_model_dump(job)
    return job

"""Amazon Sales and Traffic Report Synchronization Trigger. 12B.6A —
durable job.

Deliberately a *separate* router/file from `amazon_sales_traffic.py`,
whose own docstring documents it as strictly read-only — this remains
the one and only write/ingestion-triggering endpoint for Sales and
Traffic reports.

Neither route here performs Amazon ingestion or blocks on it. `POST
.../sales-traffic/sync` enqueues (or reports the existing) durable
`run_type='sales_and_traffic_report'` job and returns immediately (`202`
for a newly queued job); `GET .../sales-traffic/sync/{run_id}` reports
that job's current sanitized progress. A separate worker process
(`app.amazon.sales_traffic_worker`) is what actually claims and
processes queued jobs — this module never calls Amazon directly, never
resolves secrets, and never bypasses the existing single-writer
guarantee (the DB-level partial unique index this milestone's migration
adds).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict

from app.amazon.common import public_model_dump
from app.amazon.sales_traffic_sync import (
    AmazonSalesTrafficSyncTriggerService,
    SalesTrafficSyncJobStatus,
    get_amazon_sales_traffic_sync_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/amazon", tags=["amazon-sales-traffic-sync"])

_TRIGGER_MESSAGES: dict[str, str] = {
    "already_running": "A Sales and Traffic report synchronization is already running for this marketplace.",
    "scope_not_found": "This seller account or marketplace participation was not found.",
    "scope_inactive": "This marketplace connection is not active.",
    "connection_unresolvable": "No Amazon connection is bound to this seller account.",
    "cooldown": "Please wait a moment before synchronizing Sales and Traffic again.",
    "invalid_request": "The requested date range or granularity was invalid.",
}
_DEFAULT_FAILURE_MESSAGE = "Sales and Traffic synchronization could not be started."
_JOB_NOT_FOUND_MESSAGE = "This synchronization job was not found."


class SalesTrafficSyncTriggerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seller_account_id: UUID
    marketplace_participation_id: UUID
    data_start_time: date
    data_end_time: date
    date_granularity: str = "DAY"
    asin_granularity: str = "SKU"


class SalesTrafficSyncTriggerResponse(BaseModel):
    """Sanitized trigger outcome. Never carries a seller ID, marketplace
    ID, token, lease owner, report id, report document id, or raw Amazon
    payload."""

    model_config = ConfigDict(extra="forbid")

    reason: str
    message: str | None = None
    job: SalesTrafficSyncJobStatus | None = None
    retry_allowed_at: datetime | None = None


def _status_for_trigger_reason(reason: str) -> int:
    if reason == "scope_not_found":
        return 404
    if reason in {"scope_inactive", "connection_unresolvable"}:
        return 503
    if reason == "already_running":
        return 409
    if reason == "cooldown":
        return 429
    if reason == "invalid_request":
        return 422
    return 500


@router.post("/sales-traffic/sync", response_model=SalesTrafficSyncTriggerResponse, status_code=202)
async def sync_sales_traffic(
    request: SalesTrafficSyncTriggerRequest,
    service: AmazonSalesTrafficSyncTriggerService = Depends(get_amazon_sales_traffic_sync_service),
) -> SalesTrafficSyncTriggerResponse:
    try:
        outcome = service.trigger(
            seller_account_id=request.seller_account_id,
            marketplace_participation_id=request.marketplace_participation_id,
            data_start_time=request.data_start_time,
            data_end_time=request.data_end_time,
            date_granularity=request.date_granularity,
            asin_granularity=request.asin_granularity,
        )
    except Exception as exc:
        logger.warning("amazon sales and traffic sync trigger raised an unexpected exception")
        raise HTTPException(status_code=500, detail=_DEFAULT_FAILURE_MESSAGE) from exc

    response = SalesTrafficSyncTriggerResponse(
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


@router.get("/sales-traffic/sync/{run_id}", response_model=SalesTrafficSyncJobStatus)
def get_sales_traffic_sync_status(
    run_id: UUID,
    service: AmazonSalesTrafficSyncTriggerService = Depends(get_amazon_sales_traffic_sync_service),
) -> SalesTrafficSyncJobStatus:
    job = service.get_status(run_id)
    if job is None:
        raise HTTPException(status_code=404, detail=_JOB_NOT_FOUND_MESSAGE)
    public_model_dump(job)
    return job

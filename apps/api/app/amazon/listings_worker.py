"""12B.3G — Durable Listings synchronization worker.

A separate, explicit, long-running process. Never started by importing
this module, never started by the API process, never started implicitly
during tests — only `if __name__ == "__main__"` below invokes
`run_forever()`. Run it as its own hosted process:

    cd apps/api
    uv run python -m app.amazon.listings_worker

Multiple worker processes may run concurrently against the same database;
the database is the *only* coordination point — no in-memory queue, no
thread pool, no external message broker, nothing this process holds that
another process could not equally well hold. Every `run_once()` call is
independent: this class carries no state between claims other than its
own `lease_owner` identity.

Claims one eligible job at a time via `AmazonIngestionRunRepository.
claim_next_listings_job` (PostgreSQL `SELECT ... FOR UPDATE SKIP LOCKED`
— see that method's own docstring for why SQLite cannot prove this
class's concurrency-safety), then processes it to a terminal or
`waiting_to_retry` outcome via `AmazonListingsIngestionService.
process_claimed_job` — all pagination, heartbeat renewal, retry-vs-
terminal classification, and reconciliation logic already lives there;
this module is only the polling loop and process entry point around it.

Logs are structured but deliberately minimal: only ASI's own internal
run id (an opaque UUID, not a seller identifier) and outcome/status
strings drawn from the same fixed, sanitized vocabulary already used in
API responses. Never a seller id, SKU, ASIN, token, page token, connection
id, or raw Amazon payload.
"""

from __future__ import annotations

import asyncio
import logging
import secrets as _secrets_module
import sys

from app.amazon.listings_ingestion import AmazonListingsIngestionService
from app.core.config import Settings, get_settings
from app.persistence.database import session_scope
from app.persistence.repositories import AmazonIngestionRunRepository

logger = logging.getLogger(__name__)

DEFAULT_IDLE_POLL_SECONDS = 5.0


def _default_lease_owner() -> str:
    return f"listings-worker-{_secrets_module.token_hex(8)}"


class ListingsWorker:
    """One polling worker loop. See module docstring for the process-level
    contract (separate process, database-only coordination, no state
    carried between claims)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        ingestion_service: AmazonListingsIngestionService | None = None,
        lease_owner: str | None = None,
        idle_poll_seconds: float = DEFAULT_IDLE_POLL_SECONDS,
    ) -> None:
        self._settings = settings
        self._ingestion_service = ingestion_service or AmazonListingsIngestionService(settings=settings)
        self._lease_owner = lease_owner or _default_lease_owner()
        self._idle_poll_seconds = idle_poll_seconds
        self._stop_requested = False

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def request_stop(self) -> None:
        """Cooperative shutdown: checked only between claim attempts, never
        interrupts a job already in flight. A job still running when a
        stop is requested is left to finish naturally (or to this same
        lease-expiry recovery a hard process kill would also rely on —
        see the module docstring's crash-recovery note)."""
        self._stop_requested = True

    async def run_forever(self) -> None:
        while not self._stop_requested:
            claimed_something = await self.run_once()
            if not claimed_something:
                await asyncio.sleep(self._idle_poll_seconds)

    async def run_once(self) -> bool:
        """Claims and processes at most one job. Returns True if a job was
        claimed (regardless of its resulting outcome — succeeded, failed,
        or rescheduled for retry), False if none was eligible right now."""
        cfg = self._cfg()
        with session_scope() as session:
            claimed_row = AmazonIngestionRunRepository(session).claim_next_listings_job(
                lease_owner=self._lease_owner,
                lease_duration_seconds=cfg.listings_sync_lease_duration_seconds,
                max_global_active=cfg.listings_sync_max_global_concurrent_jobs,
                max_active_per_organization=cfg.listings_sync_max_concurrent_jobs_per_organization,
            )
            run_id = claimed_row.id if claimed_row is not None else None

        if run_id is None:
            return False

        logger.info("amazon listings worker claimed a job run_id=%s", run_id)
        try:
            outcome = await self._ingestion_service.process_claimed_job(run_id)
        except Exception:
            # An exception this deep (outside every boundary
            # `process_claimed_job`/`_reconcile` already wraps in their
            # own sanitized failure recording) is a genuine defect, not
            # an ordinary business outcome. The run's lease will simply
            # expire and become reclaimable — identical recovery to a
            # hard process crash — so nothing further needs to happen
            # here beyond not letting one bad job kill the whole loop.
            logger.exception("amazon listings worker job processing raised an unexpected exception run_id=%s", run_id)
            return True

        logger.info(
            "amazon listings worker finished a job run_id=%s succeeded=%s reason=%s",
            run_id, outcome.succeeded, outcome.reason,
        )
        return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    worker = ListingsWorker()

    async def _run() -> None:
        await worker.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        worker.request_stop()
        sys.exit(0)


if __name__ == "__main__":
    main()

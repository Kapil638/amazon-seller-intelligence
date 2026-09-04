"""12B.4D — Durable Orders synchronization worker.

A separate, explicit, long-running process — structurally identical in
every operational convention to `app.amazon.listings_worker`, deliberately
**not** merged into that same process. Run it as its own hosted process:

    cd apps/api
    uv run python -m app.amazon.orders_worker

**Why a dedicated worker instead of one worker claiming both run types**
(12B.4D Phase 5 design decision): Listings' `claim_next_listings_job` is a
heavily-reviewed, concurrency-proof piece of SQL (advisory lock +
`FOR UPDATE SKIP LOCKED` single-row claim) whose predicate shape is
specific to Listings' own `(seller_account, marketplace_participation)`
scope. Orders' equivalent claim (`claim_next_orders_job`, added in
12B.4D) is scoped to the coarser `(seller_account, region, environment)`
tuple instead. Genuinely unifying these into one query that fairly
interleaves both run types while preserving each one's own proven
concurrency guarantees would mean modifying `claim_next_listings_job`
itself — risking the one thing this milestone's brief explicitly
protects: "existing Listings behavior must remain unchanged." A dedicated
worker keeps `listings_worker.py` (and every test against it) completely
untouched, and trivially satisfies "Orders failures must not crash or
corrupt Listings processing" — they are different processes with no
shared state beyond the database itself. "No starvation between run
types" (12B.4D Phase 5) follows directly from each type having its own
dedicated worker capacity, rather than from either worker's claim query
having to interleave; "no starvation between organizations" is still
enforced within each worker's own claim query exactly as it already is
for Listings (`max_active_per_organization`).

Multiple Orders worker processes may run concurrently against the same
database — the database is the only coordination point, identical to
Listings. Claims one eligible job at a time via
`AmazonIngestionRunRepository.claim_next_orders_job`, then processes it to
a terminal or `waiting_to_retry` outcome via
`AmazonOrdersIngestionService.process_claimed_job` — all pagination,
per-page persistence, heartbeat renewal, retry-vs-terminal classification,
and finalization logic already lives there; this module is only the
polling loop and process entry point around it.

**`ASI_ORDERS_WORKER_ENABLED`** — explicit, fail-closed authorization
gate, independent of `ASI_LISTINGS_WORKER_ENABLED` (either may be enabled
without the other). See `listings_worker.py`'s own docstring for the full
"why this exists" reasoning (unchanged here): starting the unified local
dev stack, or running this module directly, must never silently begin
claiming and processing real jobs against whatever `DATABASE_URL` happens
to be configured.

SIGTERM/SIGINT handling, poll-error backoff scope
(`OperationalError`/`OSError` only), and configuration-error fail-closed
behavior are all identical in shape to `listings_worker.py` — see that
module's docstring for the full reasoning, not repeated here.

Logs are structured but deliberately minimal: only ASI's own internal run
id (an opaque UUID) and outcome/status strings drawn from the same fixed,
sanitized vocabulary already used in API responses. Never a database URL,
credential, seller id, participation id, order id, marketplace id, token,
pagination token, connection id, or raw Amazon payload.

**Production status**: identical caveat to `listings_worker.py` — this
module improves local operability and this process's own resilience. It
does not make production Orders synchronization functional on its own;
that additionally requires an actual deployed worker service, which has
not been deployed anywhere as of 12B.4D. Until that happens, a production
trigger enqueues a durable row that no process is running to ever claim.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets as _secrets_module
import signal
import sys

from pydantic import ValidationError
from sqlalchemy.exc import OperationalError

from app.amazon.orders_ingestion import AmazonOrdersIngestionService
from app.core.config import Settings, get_settings
from app.persistence.database import session_scope
from app.persistence.repositories import AmazonIngestionRunRepository

logger = logging.getLogger(__name__)

DEFAULT_IDLE_POLL_SECONDS = 5.0
DEFAULT_POLL_ERROR_BASE_BACKOFF_SECONDS = 2.0
DEFAULT_POLL_ERROR_MAX_BACKOFF_SECONDS = 60.0

# See listings_worker.py's own `_RECOVERABLE_POLL_ERRORS` docstring for
# why this set is deliberately narrow — identical reasoning here.
_RECOVERABLE_POLL_ERRORS = (OperationalError, OSError)

_WORKER_ENABLED_ENV_VAR = "ASI_ORDERS_WORKER_ENABLED"
_WORKER_ENABLED_TRUE_VALUES = {"1", "true"}

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 2
EXIT_DISABLED = 3


def is_worker_enabled() -> bool:
    """Reads `ASI_ORDERS_WORKER_ENABLED` directly from the environment —
    independent of `ASI_LISTINGS_WORKER_ENABLED` and of general
    application configuration. See `listings_worker.is_worker_enabled`'s
    docstring for the identical fail-closed reasoning."""
    return os.environ.get(_WORKER_ENABLED_ENV_VAR, "").strip().lower() in _WORKER_ENABLED_TRUE_VALUES


def _default_lease_owner() -> str:
    return f"orders-worker-{_secrets_module.token_hex(8)}"


class OrdersWorker:
    """One polling worker loop. See module docstring for the process-level
    contract (separate process, database-only coordination, no state
    carried between claims)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        ingestion_service: AmazonOrdersIngestionService | None = None,
        lease_owner: str | None = None,
        idle_poll_seconds: float | None = None,
        poll_error_base_backoff_seconds: float | None = None,
        poll_error_max_backoff_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._ingestion_service = ingestion_service or AmazonOrdersIngestionService(settings=settings)
        self._lease_owner = lease_owner or _default_lease_owner()
        cfg = self._cfg()
        self._idle_poll_seconds = (
            idle_poll_seconds if idle_poll_seconds is not None else cfg.orders_worker_idle_poll_seconds
        )
        self._poll_error_base_backoff_seconds = (
            poll_error_base_backoff_seconds
            if poll_error_base_backoff_seconds is not None
            else cfg.orders_worker_poll_error_base_backoff_seconds
        )
        self._poll_error_max_backoff_seconds = (
            poll_error_max_backoff_seconds
            if poll_error_max_backoff_seconds is not None
            else cfg.orders_worker_poll_error_max_backoff_seconds
        )
        self._stop_requested = False
        self._current_poll_error_backoff_seconds = self._poll_error_base_backoff_seconds

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def request_stop(self) -> None:
        """Cooperative shutdown — see `ListingsWorker.request_stop`'s
        docstring for the identical guarantee (checked only between claim
        attempts, never interrupts a job already in flight)."""
        if not self._stop_requested:
            logger.info("amazon orders worker graceful shutdown requested")
        self._stop_requested = True

    async def run_forever(self) -> None:
        logger.info("amazon orders worker started")
        while not self._stop_requested:
            try:
                claimed_something = await self.run_once()
            except _RECOVERABLE_POLL_ERRORS:
                delay = self._current_poll_error_backoff_seconds
                logger.warning(
                    "amazon orders worker poll failed; retrying in %.1fs (recoverable, no job affected)", delay
                )
                logger.debug("amazon orders worker poll failure detail", exc_info=True)
                self._current_poll_error_backoff_seconds = min(delay * 2, self._poll_error_max_backoff_seconds)
                await asyncio.sleep(delay)
                continue

            self._current_poll_error_backoff_seconds = self._poll_error_base_backoff_seconds
            if not claimed_something:
                await asyncio.sleep(self._idle_poll_seconds)
        logger.info("amazon orders worker stopped")

    async def run_once(self) -> bool:
        """Claims and processes at most one job. Returns True if a job was
        claimed (regardless of its resulting outcome), False if none was
        eligible right now. A failure in the claim step itself propagates
        to the caller — see `ListingsWorker.run_once`'s identical
        docstring for why."""
        cfg = self._cfg()
        with session_scope() as session:
            claimed_row = AmazonIngestionRunRepository(session).claim_next_orders_job(
                lease_owner=self._lease_owner,
                lease_duration_seconds=cfg.orders_sync_lease_duration_seconds,
                max_global_active=cfg.orders_sync_max_global_concurrent_jobs,
                max_active_per_organization=cfg.orders_sync_max_concurrent_jobs_per_organization,
            )
            run_id = claimed_row.id if claimed_row is not None else None

        if run_id is None:
            return False

        logger.info("amazon orders worker claimed a job run_id=%s", run_id)
        try:
            outcome = await self._ingestion_service.process_claimed_job(run_id)
        except Exception:
            # See ListingsWorker.run_once's identical docstring: the
            # run's lease will simply expire and become reclaimable —
            # this must never be treated as a poll-step failure.
            logger.exception("amazon orders worker job processing raised an unexpected exception run_id=%s", run_id)
            return True

        logger.info(
            "amazon orders worker finished a job run_id=%s succeeded=%s reason=%s",
            run_id, outcome.succeeded, outcome.reason,
        )
        return True


def _install_shutdown_signal_handlers(worker: OrdersWorker, loop: asyncio.AbstractEventLoop) -> None:
    """Identical to `listings_worker._install_shutdown_signal_handlers` —
    see that function's docstring for the full reasoning."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            logger.warning(
                "amazon orders worker could not install a graceful handler for %s on this platform "
                "(POSIX-only) — graceful shutdown for this signal is not guaranteed",
                sig.name,
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    if not is_worker_enabled():
        logger.error(
            "amazon orders worker is disabled — set %s=true to allow this process to "
            "claim and process jobs (refusing to start, exit code %d)",
            _WORKER_ENABLED_ENV_VAR, EXIT_DISABLED,
        )
        return EXIT_DISABLED

    # Declares this already-authorized process to `app.persistence.
    # database`'s production-database guard. Reached only after the
    # explicit ASI_ORDERS_WORKER_ENABLED check above has already
    # passed — see `listings_worker.main`'s identical comment.
    os.environ["ASI_DB_RUNTIME_CONTEXT"] = "orders_worker"

    try:
        settings = get_settings()
    except ValidationError:
        logger.error(
            "amazon orders worker configuration is invalid; refusing to start "
            "(exit code %d) — check environment variables, not application code",
            EXIT_CONFIGURATION_ERROR,
        )
        return EXIT_CONFIGURATION_ERROR

    worker = OrdersWorker(settings=settings)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        _install_shutdown_signal_handlers(worker, loop)
        await worker.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        worker.request_stop()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

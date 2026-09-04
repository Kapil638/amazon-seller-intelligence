"""12B.6A — Durable Sales and Traffic report synchronization worker.

A separate, explicit, long-running process — structurally identical in
every operational convention to `app.amazon.orders_worker` and
`app.amazon.listings_worker`, deliberately **not** merged into either.
Run it as its own hosted process:

    cd apps/api
    uv run python -m app.amazon.sales_traffic_worker

**Why a dedicated worker** (same reasoning as Orders vs Listings,
`orders_worker.py`'s own docstring): this report type's claim
(`claim_next_sales_traffic_job`) uses its own dedicated PostgreSQL
advisory-lock key and its own single-participation scope shape — a
distinct, independently reviewed piece of SQL. A dedicated worker keeps
`orders_worker.py`/`listings_worker.py` (and every test against them)
completely untouched, and trivially satisfies "a Sales and Traffic
report failure must never crash or corrupt Orders/Listings processing"
— they are different processes with no shared state beyond the database
itself.

Claims one eligible job at a time via `AmazonIngestionRunRepository.
claim_next_sales_traffic_job`, then processes it to a terminal or
`waiting_to_retry` outcome via `AmazonSalesTrafficIngestionService.
process_claimed_job` (`sales_traffic_ingestion.py`) — all
`createReport`/`getReport`/`getReportDocument` orchestration, durable
report-id recording, persistence, and checkpoint advancement already
lives there; this module is only the polling loop and process entry
point around it.

**`ASI_SALES_TRAFFIC_WORKER_ENABLED`** — explicit, fail-closed
authorization gate, independent of `ASI_ORDERS_WORKER_ENABLED` and
`ASI_LISTINGS_WORKER_ENABLED` (any subset may be enabled at once). See
`listings_worker.py`'s own docstring for the full "why this exists"
reasoning (unchanged here): starting the unified local dev stack, or
running this module directly, must never silently begin claiming and
processing real jobs — and, for this report type specifically, never
silently begin spending this report type's own scarce three-per-five-
minutes `createReport` budget — against whatever `DATABASE_URL` happens
to be configured.

SIGTERM/SIGINT handling, poll-error backoff scope
(`OperationalError`/`OSError` only), and configuration-error fail-closed
behavior are all identical in shape to `orders_worker.py` — see that
module's docstring for the full reasoning, not repeated here.

Logs are structured but deliberately minimal: only ASI's own internal
run id (an opaque UUID) and outcome/reason strings drawn from
`SalesTrafficIngestionOutcome`'s own fixed, sanitized vocabulary. Never
a database URL, credential, seller id, participation id, report id,
report document id, marketplace id, token, or raw Amazon payload — see
`sales_traffic_ingestion.py`'s own docstring for why `outcome`/`reason`
are sanitized at the source.

**Production status**: identical caveat to `orders_worker.py` — this
module improves local operability and this process's own resilience. It
does not make production Sales and Traffic synchronization functional on
its own; that additionally requires an actual deployed worker service,
which has not been deployed anywhere as of 12B.6A. Until that happens, a
queued run sits durably in the database with no process claiming it —
never silently lost, never silently retried by something unauthorized.
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

from app.amazon.sales_traffic_ingestion import AmazonSalesTrafficIngestionService
from app.core.config import Settings, get_settings
from app.persistence.database import session_scope
from app.persistence.repositories import AmazonIngestionRunRepository

logger = logging.getLogger(__name__)

DEFAULT_IDLE_POLL_SECONDS = 5.0
DEFAULT_POLL_ERROR_BASE_BACKOFF_SECONDS = 2.0
DEFAULT_POLL_ERROR_MAX_BACKOFF_SECONDS = 60.0

# See listings_worker.py's own `_RECOVERABLE_POLL_ERRORS` docstring for why
# this set is deliberately narrow — identical reasoning here. A poll-step
# failure is a database/transport hiccup in the *claim* step itself, never
# a signal to swallow a genuine programming error.
_RECOVERABLE_POLL_ERRORS = (OperationalError, OSError)

_WORKER_ENABLED_ENV_VAR = "ASI_SALES_TRAFFIC_WORKER_ENABLED"
_WORKER_ENABLED_TRUE_VALUES = {"1", "true"}

EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 2
EXIT_DISABLED = 3


def is_worker_enabled() -> bool:
    """Reads `ASI_SALES_TRAFFIC_WORKER_ENABLED` directly from the
    environment — independent of `ASI_ORDERS_WORKER_ENABLED` and
    `ASI_LISTINGS_WORKER_ENABLED`. See `listings_worker.is_worker_enabled`'s
    docstring for the identical fail-closed reasoning."""
    return os.environ.get(_WORKER_ENABLED_ENV_VAR, "").strip().lower() in _WORKER_ENABLED_TRUE_VALUES


def _default_lease_owner() -> str:
    return f"sales-traffic-worker-{_secrets_module.token_hex(8)}"


class SalesTrafficWorker:
    """One polling worker loop. See module docstring for the process-level
    contract (separate process, database-only coordination, no state
    carried between claims)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        ingestion_service: AmazonSalesTrafficIngestionService | None = None,
        lease_owner: str | None = None,
        idle_poll_seconds: float | None = None,
        poll_error_base_backoff_seconds: float | None = None,
        poll_error_max_backoff_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._ingestion_service = ingestion_service or AmazonSalesTrafficIngestionService(settings=settings)
        self._lease_owner = lease_owner or _default_lease_owner()
        cfg = self._cfg()
        self._idle_poll_seconds = (
            idle_poll_seconds if idle_poll_seconds is not None else cfg.sales_traffic_worker_idle_poll_seconds
        )
        self._poll_error_base_backoff_seconds = (
            poll_error_base_backoff_seconds
            if poll_error_base_backoff_seconds is not None
            else cfg.sales_traffic_worker_poll_error_base_backoff_seconds
        )
        self._poll_error_max_backoff_seconds = (
            poll_error_max_backoff_seconds
            if poll_error_max_backoff_seconds is not None
            else cfg.sales_traffic_worker_poll_error_max_backoff_seconds
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
            logger.info("amazon sales and traffic worker graceful shutdown requested")
        self._stop_requested = True

    async def run_forever(self) -> None:
        logger.info("amazon sales and traffic worker started")
        while not self._stop_requested:
            try:
                claimed_something = await self.run_once()
            except _RECOVERABLE_POLL_ERRORS:
                delay = self._current_poll_error_backoff_seconds
                logger.warning(
                    "amazon sales and traffic worker poll failed; retrying in %.1fs (recoverable, no job affected)",
                    delay,
                )
                logger.debug("amazon sales and traffic worker poll failure detail", exc_info=True)
                self._current_poll_error_backoff_seconds = min(delay * 2, self._poll_error_max_backoff_seconds)
                await asyncio.sleep(delay)
                continue

            self._current_poll_error_backoff_seconds = self._poll_error_base_backoff_seconds
            if not claimed_something:
                await asyncio.sleep(self._idle_poll_seconds)
        logger.info("amazon sales and traffic worker stopped")

    async def run_once(self) -> bool:
        """Claims and processes at most one job. Returns True if a job was
        claimed (regardless of its resulting outcome), False if none was
        eligible right now. A failure in the claim step itself propagates
        to the caller — see `OrdersWorker.run_once`'s identical
        docstring for why."""
        cfg = self._cfg()
        with session_scope() as session:
            claimed_row = AmazonIngestionRunRepository(session).claim_next_sales_traffic_job(
                lease_owner=self._lease_owner,
                lease_duration_seconds=cfg.sales_traffic_sync_lease_duration_seconds,
                max_global_active=cfg.sales_traffic_sync_max_global_concurrent_jobs,
                max_active_per_organization=cfg.sales_traffic_sync_max_concurrent_jobs_per_organization,
            )
            run_id = claimed_row.id if claimed_row is not None else None

        if run_id is None:
            return False

        logger.info("amazon sales and traffic worker claimed a job run_id=%s", run_id)
        try:
            outcome = await self._ingestion_service.process_claimed_job(run_id)
        except Exception:
            # See OrdersWorker.run_once's identical docstring: the run's
            # lease will simply expire and become reclaimable — this must
            # never be treated as a poll-step failure.
            logger.exception(
                "amazon sales and traffic worker job processing raised an unexpected exception run_id=%s", run_id
            )
            return True

        logger.info(
            "amazon sales and traffic worker finished a job run_id=%s outcome=%s reason=%s",
            run_id, outcome.outcome, outcome.reason,
        )
        return True


def _install_shutdown_signal_handlers(worker: SalesTrafficWorker, loop: asyncio.AbstractEventLoop) -> None:
    """Identical to `listings_worker._install_shutdown_signal_handlers` —
    see that function's docstring for the full reasoning."""
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            logger.warning(
                "amazon sales and traffic worker could not install a graceful handler for %s on this platform "
                "(POSIX-only) — graceful shutdown for this signal is not guaranteed",
                sig.name,
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    if not is_worker_enabled():
        logger.error(
            "amazon sales and traffic worker is disabled — set %s=true to allow this process to "
            "claim and process jobs (refusing to start, exit code %d)",
            _WORKER_ENABLED_ENV_VAR, EXIT_DISABLED,
        )
        return EXIT_DISABLED

    # Declares this already-authorized process to `app.persistence.
    # database`'s production-database guard. Reached only after the
    # explicit ASI_SALES_TRAFFIC_WORKER_ENABLED check above has already
    # passed — see `listings_worker.main`'s identical comment.
    os.environ["ASI_DB_RUNTIME_CONTEXT"] = "sales_traffic_worker"

    try:
        settings = get_settings()
    except ValidationError:
        logger.error(
            "amazon sales and traffic worker configuration is invalid; refusing to start "
            "(exit code %d) — check environment variables, not application code",
            EXIT_CONFIGURATION_ERROR,
        )
        return EXIT_CONFIGURATION_ERROR

    worker = SalesTrafficWorker(settings=settings)

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

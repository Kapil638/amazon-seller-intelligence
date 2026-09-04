"""12B.3G/12B.3H — Durable Listings synchronization worker.

A separate, explicit, long-running process. Never started by importing
this module, never started by the API process, never started implicitly
during tests — only `if __name__ == "__main__"` below invokes
`main()`. Run it as its own hosted process:

    cd apps/api
    uv run python -m app.amazon.listings_worker

Or, for local development, via the unified `./scripts/dev.sh`, which
starts exactly one of these alongside the frontend and API — see that
script and `docs/AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md`.

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

12B.3H additions (all in this module — the claim/ingestion contract
above is unchanged):

- **`ASI_LISTINGS_WORKER_ENABLED` — explicit, fail-closed authorization
  gate.** `main()` refuses to start at all unless this environment
  variable is exactly `"1"` or `"true"` (case-insensitive) — unset,
  empty, `"false"`, or anything else refuses to start. This exists
  specifically so that starting the unified local dev stack
  (`./scripts/dev.sh`) — or running this module directly — can never
  *silently* begin claiming and processing real jobs (real Amazon calls,
  against whatever `DATABASE_URL` happens to be configured, which in
  this repository's actual local `.env` is a live Supabase project, not
  a disposable one) merely because a developer ran a command without
  thinking about it. `./scripts/dev.sh` checks the identical variable
  itself before even attempting to start a worker child, so the gate is
  enforced twice — once by the orchestrator, once by the worker itself,
  so running the worker module directly is never a way to bypass it.
  The API and frontend never read this variable — it exists purely to
  gate this one process. The exact same variable is the intended
  mechanism for deliberately enabling the worker on a future deployed
  worker service (see `docs/AI_HANDOVER/
  12B3H_LISTINGS_WORKER_OPERATIONS.md`) — nothing else changes between
  local and production use of this flag.
- The claim/poll step itself (not job *processing*, which already has
  its own sanitized failure handling) is now wrapped in bounded
  exponential backoff — but *only* for exceptions that are plausibly
  recoverable database/transport failures (`sqlalchemy.exc.
  OperationalError`, and `OSError`/its subclasses `ConnectionError`/
  `TimeoutError` for a raw driver-level failure SQLAlchemy did not wrap).
  A database connectivity failure — e.g. Supabase pausing, a transient
  network interruption — no longer crashes the process; the worker logs
  a sanitized retry notice, sleeps with doubling backoff up to a cap,
  and resets to the base delay the moment a poll succeeds again (whether
  or not it found a job). Deliberately **not** a bare `except Exception`:
  a genuine programming error in the claim path (`TypeError`,
  `AttributeError`, an assertion/invariant violation) is a defect that
  must surface and crash the process for a supervisor to restart and an
  operator to notice — silently retrying it forever behind "recoverable
  backoff" would hide a real bug indefinitely instead.
- `SIGTERM` is now handled identically to `SIGINT`/Ctrl-C: both request
  a cooperative stop rather than killing the process outright. Neither
  signal interrupts a job already being processed — `request_stop()`
  is only ever checked between claim attempts, so a hosting platform's
  graceful-shutdown window is honored naturally rather than fought.
  Concretely: if `SIGTERM` arrives while `process_claimed_job` is
  mid-flight (e.g. awaiting an Amazon page request), that request is
  **not** aborted — it runs to its own natural conclusion (success,
  retryable failure, or terminal failure, each already recorded
  normally), and only *then* does `run_forever`'s loop notice the
  pending stop flag and exit. If the supervisor's own grace period is
  shorter than that remaining work and it escalates to `SIGKILL`, the
  process is torn down mid-flight with no chance to run any further
  code at all — but this is still safely recoverable, not silent data
  loss: the claimed row's `lease_expires_at` was already set at claim
  time and is never extended by anything this process does not
  explicitly commit, so once it passes, `claim_next_listings_job`'s own
  stale-lease reclaim step (unchanged by 12B.3H) marks the row
  `timed_out` and frees its scope for a new attempt — identical recovery
  to any other hard process crash, and identical to the pre-12B.3H
  worker's own crash-recovery story. Only the *outcome* of that one
  specific in-flight attempt is lost (recorded later as `timed_out`
  rather than whatever it would have finished as); no row is left
  permanently stuck, and no `amazon_seller_listings` write from a
  half-finished page is ever left partially applied (the ingestion
  service's own reconciliation is already transactional per page).
- Configuration errors (e.g. an unparseable `Settings`) are fail-closed:
  the process exits immediately with a distinct, documented exit code
  rather than looping or half-starting.

Logs are structured but deliberately minimal: only ASI's own internal
run id (an opaque UUID, not a seller identifier) and outcome/status
strings drawn from the same fixed, sanitized vocabulary already used in
API responses. Never a database URL, credential, seller id, participation
id, SKU, ASIN, token, page token, connection id, or raw Amazon payload.

**Production status**: this module (and `./scripts/dev.sh`) improve
local operability and this process's own resilience. Neither makes
production Amazon.com Listings synchronization functional on its own —
that additionally requires an actual deployed worker service (see the
runbook in `docs/AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md`),
which has not been deployed anywhere as of 12B.3H. Until that happens,
a production trigger would enqueue a durable row that no process is
running to ever claim — identical in effect to the local "no worker
running" state this milestone otherwise fixes.
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

from app.amazon.listings_ingestion import AmazonListingsIngestionService
from app.core.config import Settings, get_settings
from app.persistence.database import session_scope
from app.persistence.repositories import AmazonIngestionRunRepository

logger = logging.getLogger(__name__)

DEFAULT_IDLE_POLL_SECONDS = 5.0
DEFAULT_POLL_ERROR_BASE_BACKOFF_SECONDS = 2.0
DEFAULT_POLL_ERROR_MAX_BACKOFF_SECONDS = 60.0

# Exceptions treated as a recoverable poll-step failure (see
# `run_forever`) — deliberately narrow. `OperationalError` is what
# SQLAlchemy wraps a lost/refused database connection in (confirmed
# directly against this exact failure mode earlier in this project's own
# operational history — a paused Supabase project surfaces as exactly
# this); `OSError` (whose subclasses already include `ConnectionError`
# and `TimeoutError`) covers a raw driver/network failure that reaches
# this code without being wrapped. Anything else — `TypeError`,
# `AttributeError`, an assertion failure — is a genuine defect and must
# propagate, not be retried forever.
_RECOVERABLE_POLL_ERRORS = (OperationalError, OSError)

# The single explicit, fail-closed authorization gate for this process —
# see the module docstring's "ASI_LISTINGS_WORKER_ENABLED" section.
_WORKER_ENABLED_ENV_VAR = "ASI_LISTINGS_WORKER_ENABLED"
_WORKER_ENABLED_TRUE_VALUES = {"1", "true"}

# Documented, stable exit codes — suitable for a supervisor (systemd,
# Docker, a PaaS restart policy) to distinguish "asked to stop" from
# "cannot start at all" without parsing log text.
EXIT_OK = 0
EXIT_CONFIGURATION_ERROR = 2
EXIT_DISABLED = 3


def is_worker_enabled() -> bool:
    """Reads `ASI_LISTINGS_WORKER_ENABLED` directly from the environment
    (not through `Settings`) — this is a process-startup authorization
    gate, deliberately independent of the general application
    configuration surface the API/frontend also read, so enabling or
    disabling it can never have any effect on anything but this one
    process. Accepts `"1"` or `"true"` (case-insensitive); everything
    else — unset, empty, `"false"`, a typo — is treated as disabled.
    Fail-closed by construction: there is no code path in `main()` that
    starts the worker without this returning `True` first."""
    return os.environ.get(_WORKER_ENABLED_ENV_VAR, "").strip().lower() in _WORKER_ENABLED_TRUE_VALUES


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
        idle_poll_seconds: float | None = None,
        poll_error_base_backoff_seconds: float | None = None,
        poll_error_max_backoff_seconds: float | None = None,
    ) -> None:
        self._settings = settings
        self._ingestion_service = ingestion_service or AmazonListingsIngestionService(settings=settings)
        self._lease_owner = lease_owner or _default_lease_owner()
        cfg = self._cfg()
        self._idle_poll_seconds = (
            idle_poll_seconds if idle_poll_seconds is not None else cfg.listings_worker_idle_poll_seconds
        )
        self._poll_error_base_backoff_seconds = (
            poll_error_base_backoff_seconds
            if poll_error_base_backoff_seconds is not None
            else cfg.listings_worker_poll_error_base_backoff_seconds
        )
        self._poll_error_max_backoff_seconds = (
            poll_error_max_backoff_seconds
            if poll_error_max_backoff_seconds is not None
            else cfg.listings_worker_poll_error_max_backoff_seconds
        )
        self._stop_requested = False
        self._current_poll_error_backoff_seconds = self._poll_error_base_backoff_seconds

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def request_stop(self) -> None:
        """Cooperative shutdown: checked only between claim attempts, never
        interrupts a job already in flight. A job still running when a
        stop is requested is left to finish naturally (or to this same
        lease-expiry recovery a hard process kill would also rely on —
        see the module docstring's crash-recovery note). Safe to call
        from a signal handler — it only ever sets a flag, never awaits or
        touches the database."""
        if not self._stop_requested:
            logger.info("amazon listings worker graceful shutdown requested")
        self._stop_requested = True

    async def run_forever(self) -> None:
        logger.info("amazon listings worker started")
        while not self._stop_requested:
            try:
                claimed_something = await self.run_once()
            except _RECOVERABLE_POLL_ERRORS:
                # A database/transport failure *claiming* work (most
                # plausibly Supabase pausing, or a transient network
                # interruption) is recoverable and must never crash the
                # whole process the way an unhandled exception would.
                # Deliberately not a bare `except Exception` — see
                # `_RECOVERABLE_POLL_ERRORS`'s own comment for why a
                # genuine programming error must still propagate and
                # crash the process rather than being retried forever.
                # Bounded exponential backoff, never a busy loop: each
                # consecutive failure waits longer, up to a cap.
                delay = self._current_poll_error_backoff_seconds
                logger.warning(
                    "amazon listings worker poll failed; retrying in %.1fs (recoverable, no job affected)",
                    delay,
                )
                logger.debug("amazon listings worker poll failure detail", exc_info=True)
                self._current_poll_error_backoff_seconds = min(
                    delay * 2, self._poll_error_max_backoff_seconds
                )
                await asyncio.sleep(delay)
                continue

            # A successful poll — whether or not it found a job — proves
            # the database is reachable again; any accumulated backoff no
            # longer applies to the *next* failure, which starts fresh.
            self._current_poll_error_backoff_seconds = self._poll_error_base_backoff_seconds
            if not claimed_something:
                await asyncio.sleep(self._idle_poll_seconds)
        logger.info("amazon listings worker stopped")

    async def run_once(self) -> bool:
        """Claims and processes at most one job. Returns True if a job was
        claimed (regardless of its resulting outcome — succeeded, failed,
        or rescheduled for retry), False if none was eligible right now.
        A failure in the claim step itself (e.g. the database is
        unreachable) propagates to the caller, which is exactly what lets
        `run_forever`'s backoff distinguish "no job available" (a normal,
        successful poll) from "the poll itself failed" (recoverable, but
        worth backing off before retrying)."""
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
            # Deliberately NOT re-raised: a defect in one job's
            # processing must never be treated as a poll-step failure by
            # run_forever's backoff, which exists for a different concern.
            logger.exception("amazon listings worker job processing raised an unexpected exception run_id=%s", run_id)
            return True

        logger.info(
            "amazon listings worker finished a job run_id=%s succeeded=%s reason=%s",
            run_id, outcome.succeeded, outcome.reason,
        )
        return True


def _install_shutdown_signal_handlers(worker: ListingsWorker, loop: asyncio.AbstractEventLoop) -> None:
    """SIGTERM (a supervisor's normal stop signal — systemd, Docker,
    most PaaS restart/scale-down flows) and SIGINT (Ctrl-C) both request
    the same cooperative shutdown. Using the event loop's own signal
    handling (rather than Python's default signal delivery, which raises
    `KeyboardInterrupt` at an arbitrary bytecode point) means the current
    `await` — mid-claim, mid-Amazon-call, mid-heartbeat — is never
    interrupted; `request_stop()` only flips a flag that `run_forever`'s
    loop condition checks between iterations, so shutdown always happens
    at the same safe boundary regardless of which signal triggered it.

    `loop.add_signal_handler` is POSIX-only; this project's documented
    development/deployment targets are macOS and Linux (see
    `docs/AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md`), so no
    Windows fallback is implemented. If unavailable for any reason, this
    degrades to Python's default SIGINT-as-KeyboardInterrupt behavior
    (still caught in `main()`) with SIGTERM left at its default
    (immediate termination, no cleanup) — logged loudly so that gap is
    never silent.
    """
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, worker.request_stop)
        except NotImplementedError:
            logger.warning(
                "amazon listings worker could not install a graceful handler for %s on this platform "
                "(POSIX-only) — graceful shutdown for this signal is not guaranteed",
                sig.name,
            )


def main() -> int:
    logging.basicConfig(level=logging.INFO)

    if not is_worker_enabled():
        # Fail closed, checked before anything else — before even
        # resolving Settings, so a disabled worker never touches the
        # database or any configuration at all. See the module
        # docstring's "ASI_LISTINGS_WORKER_ENABLED" section for why this
        # exists: starting the unified dev stack (or running this module
        # directly) must never silently begin claiming and processing
        # real jobs against a real database.
        logger.error(
            "amazon listings worker is disabled — set %s=true to allow this process to "
            "claim and process jobs (refusing to start, exit code %d)",
            _WORKER_ENABLED_ENV_VAR, EXIT_DISABLED,
        )
        return EXIT_DISABLED

    # Declares this already-authorized process to `app.persistence.
    # database`'s production-database guard. Reached only after the
    # explicit ASI_LISTINGS_WORKER_ENABLED check above has already
    # passed — this is not a new opt-in surface, merely a declaration
    # of which already-authorized process is now running, made by the
    # process itself rather than inferred from an unrelated import.
    os.environ["ASI_DB_RUNTIME_CONTEXT"] = "listings_worker"

    try:
        settings = get_settings()
    except ValidationError:
        # Fail closed: a configuration error cannot recover on its own —
        # looping or retrying would only produce the same error forever.
        # Never logs the validation error's own text, which can echo back
        # invalid field values.
        logger.error(
            "amazon listings worker configuration is invalid; refusing to start "
            "(exit code %d) — check environment variables, not application code",
            EXIT_CONFIGURATION_ERROR,
        )
        return EXIT_CONFIGURATION_ERROR

    worker = ListingsWorker(settings=settings)

    async def _run() -> None:
        loop = asyncio.get_running_loop()
        _install_shutdown_signal_handlers(worker, loop)
        await worker.run_forever()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        # Reached only if signal-handler installation itself was
        # unavailable (see `_install_shutdown_signal_handlers`) and
        # Python's default Ctrl-C behavior fired instead.
        worker.request_stop()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())

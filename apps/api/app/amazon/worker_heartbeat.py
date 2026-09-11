"""fix/ingestion-worker-runtime-availability — shared, database-backed
worker liveness heartbeat.

Root cause this module fixes: a Sync click enqueues a durable job
regardless of whether any matching worker process is actually running to
claim it. If the app was started without that worker (the normal way
this repository's own `scripts/dev.sh` is documented to run — see
`docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md` — or, worse, started
through some other command that bypasses `scripts/dev.sh` entirely), the
job sits `queued` forever with no actionable signal anywhere. This
module lets each domain's sync-trigger service ask, before enqueueing:
"is a worker of this type actually alive right now?" and refuse clearly
(`reason="worker_unavailable"`, mapped to `503` by the route) instead of
silently accepting a job nothing will ever claim.

Used by exactly two kinds of caller:

- Each worker process (`listings_worker.py`, `orders_worker.py`,
  `sales_traffic_worker.py`) calls `record_heartbeat_loop()` as a
  background task, independent of its own claim/poll loop — so a worker
  legitimately busy processing one long-running job still reports itself
  alive on schedule, never appearing "unavailable" merely because the
  current job has not returned yet.
- Each domain's sync-trigger service (`listings_sync.py`, `orders_sync.py`,
  `sales_traffic_sync.py`) calls `check_availability()` once, synchronously,
  before enqueueing a new job.

Deliberately **not** a lease. This heartbeat has zero interaction with
`amazon_ingestion_runs`' own lease/claim columns — a stale or missing
heartbeat can prevent a *new* job from being accepted, but can never
block, delay, or otherwise affect the existing lease-expiry recovery of
an *already-queued or already-claimed* job. See
`AmazonWorkerHeartbeat`'s own docstring in `app/persistence/models.py`.

Worker type identifiers reuse `amazon_ingestion_runs.run_type`'s own
vocabulary rather than inventing a second one. The canonical list of
valid worker types (`"listings"`, `"orders"`, `"sales_and_traffic_report"`,
`"inventory"`) lives in `app.persistence.repositories.KNOWN_WORKER_TYPES`
and is re-exported here — see that module for why (fix/supervise-
ingestion-runtime's PR #22 rebase: this used to be a second, independently
maintained copy that silently drifted out of sync with the DB-level one).
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets as _secrets_module
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from app.core.config import Settings, get_settings
from app.persistence.database import session_scope
from app.persistence.repositories import (
    KNOWN_WORKER_TYPES,
    WorkerAvailability,
    WorkerHeartbeatRepository,
)

logger = logging.getLogger(__name__)


def new_instance_id() -> str:
    """A random, per-process, non-sensitive identifier — regenerated on
    every worker start/restart. Never derived from a seller, org, or
    connection identifier; purely so an operator reading heartbeat rows
    can tell "the same process has been heartbeating since X" from "a
    new process took over.\""""
    return _secrets_module.token_hex(8)


def record_heartbeat(worker_type: str, *, instance_id: str, pid: int | None = None) -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat(worker_type, instance_id=instance_id, pid=pid)


def check_availability(
    worker_type: str, *, settings: Settings | None = None, stale_after_seconds: float | None = None
) -> WorkerAvailability:
    cfg = settings or get_settings()
    threshold = stale_after_seconds if stale_after_seconds is not None else cfg.worker_heartbeat_stale_after_seconds
    with session_scope() as session:
        return WorkerHeartbeatRepository(session).check_availability(worker_type, stale_after_seconds=threshold)


def invalidate_heartbeat(worker_type: str, *, instance_id: str) -> None:
    """fix/supervise-ingestion-runtime — deletes this exact instance's
    heartbeat row (a no-op if a newer instance has already taken it
    over — see `WorkerHeartbeatRepository.invalidate`'s own docstring
    for why that check matters). A failure here is logged and swallowed,
    never raised: shutdown must always complete even if this one best-
    effort step cannot reach the database — the row will still expire
    on its own via `worker_heartbeat_stale_after_seconds` either way,
    this call only makes the common, clean-shutdown case immediate."""
    try:
        with session_scope() as session:
            WorkerHeartbeatRepository(session).invalidate(worker_type, instance_id=instance_id)
    except Exception:
        logger.warning(
            "worker heartbeat invalidation failed for worker_type=%s (non-fatal, row will expire "
            "naturally instead)",
            worker_type,
        )
        logger.debug("worker heartbeat invalidation failure detail", exc_info=True)


class _HeartbeatWatchdog:
    """pilot-deployment-ewise — a last-resort self-watchdog running on a
    genuinely separate OS thread, not another `asyncio` task.

    `scripts/supervisor.py`'s own hung-worker detection (heartbeat
    polled *externally*, by a different process) has no equivalent on
    Railway, where each worker is deployed as its own isolated service
    with nothing else watching it. A worker task on the *same* stuck
    event loop could never notice that loop is stuck — by definition,
    it would never get scheduled to check either. Only a separate OS
    thread, whose own `threading.Event.wait()` is serviced by the OS
    scheduler independent of the (possibly frozen) asyncio loop, can
    observe "my own process's heartbeat task has not run in a very long
    time" and act on it.

    Deliberately the most drastic possible response — `os._exit(1)`,
    not `sys.exit` (which only raises `SystemExit` on the main thread
    and could itself never be serviced by a truly stuck interpreter) —
    because by the time this threshold is reached, graceful shutdown
    has already had `worker_watchdog_stale_after_seconds` (default
    600s) of opportunity and evidently cannot happen. A real process
    supervisor (Railway's restart-on-exit policy) is expected to notice
    the exit and start a fresh instance."""

    def __init__(self, worker_type: str, *, stale_after_seconds: float, check_interval_seconds: float) -> None:
        self._worker_type = worker_type
        self._stale_after_seconds = stale_after_seconds
        self._check_interval_seconds = check_interval_seconds
        self._lock = threading.Lock()
        self._last_success_monotonic = time.monotonic()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def mark_success(self) -> None:
        with self._lock:
            self._last_success_monotonic = time.monotonic()

    def _run(self) -> None:
        while not self._stop_event.wait(self._check_interval_seconds):
            with self._lock:
                elapsed = time.monotonic() - self._last_success_monotonic
            if elapsed > self._stale_after_seconds:
                logger.critical(
                    "worker heartbeat watchdog: worker_type=%s has not recorded a successful "
                    "heartbeat write in %.0fs (threshold %.0fs) — exiting immediately (os._exit) "
                    "so a real process supervisor restarts this worker; see _HeartbeatWatchdog's "
                    "own docstring for why this runs on a separate OS thread",
                    self._worker_type, elapsed, self._stale_after_seconds,
                )
                os._exit(1)
                # os._exit() never returns in a real process — this
                # thread's loop should never reach the next iteration.
                # Returning here is only reachable when a test has
                # deliberately replaced os._exit with a non-terminating
                # spy; without it, that same test would observe this
                # thread re-firing the same critical log every
                # check_interval_seconds instead of exactly once.
                return

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name=f"{self._worker_type}-heartbeat-watchdog", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()


@dataclass(frozen=True)
class HeartbeatLoopHandle:
    """Returned by `start_heartbeat_loop()`. Callers must `await stop()`
    during their own graceful shutdown so the background task is
    cancelled cleanly rather than left dangling when the event loop is
    torn down, and so availability drops immediately rather than only
    once the row goes stale (fix/supervise-ingestion-runtime)."""

    task: asyncio.Task[None]
    worker_type: str
    instance_id: str
    watchdog: _HeartbeatWatchdog | None = field(default=None)

    async def stop(self) -> None:
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        if self.watchdog is not None:
            self.watchdog.stop()
        invalidate_heartbeat(self.worker_type, instance_id=self.instance_id)


async def _heartbeat_loop(
    worker_type: str, *, instance_id: str, interval_seconds: float, watchdog: _HeartbeatWatchdog | None
) -> None:
    pid = os.getpid()
    while True:
        try:
            record_heartbeat(worker_type, instance_id=instance_id, pid=pid)
            if watchdog is not None:
                watchdog.mark_success()
        except Exception:
            # A heartbeat write failure (e.g. a transient database
            # connectivity issue — the same class of failure the claim
            # loop's own bounded backoff already tolerates) must never
            # crash the worker process outright; it only means this one
            # tick's liveness signal was not recorded. The next tick
            # tries again. Logged at WARNING, not ERROR: a single missed
            # heartbeat is expected to self-heal and is exactly what
            # worker_heartbeat_stale_after_seconds's own multi-interval
            # tolerance is for. The watchdog (if enabled) is deliberately
            # NOT marked on a failed write — repeated failures across
            # worker_watchdog_stale_after_seconds is exactly the
            # unrecoverable case it exists to catch.
            logger.warning(
                "worker heartbeat write failed for worker_type=%s (recoverable, retrying next interval)",
                worker_type,
            )
            logger.debug("worker heartbeat write failure detail", exc_info=True)
        await asyncio.sleep(interval_seconds)


def start_heartbeat_loop(
    worker_type: str,
    *,
    instance_id: str,
    interval_seconds: float | None = None,
    settings: Settings | None = None,
    enable_watchdog: bool = True,
) -> HeartbeatLoopHandle:
    """Writes one heartbeat immediately (before returning), then
    continues writing one every `interval_seconds` in the background,
    fully decoupled from the caller's own claim/poll loop — a worker
    processing one long-running job still reports itself alive on
    schedule. The immediate first write is what gives a freshly-started
    worker a heartbeat within milliseconds rather than only after its
    first idle-poll sleep — the practical basis for 'a normal combined
    launcher (frontend + API + workers all starting together) does not
    produce a false unavailable result': by the time a developer's
    browser has loaded the page and they click Sync, the heartbeat this
    call wrote already exists.

    Also starts a same-process self-watchdog (`enable_watchdog=True`,
    the default) — see `_HeartbeatWatchdog`'s own docstring for why this
    exists and why it runs on a separate OS thread rather than another
    `asyncio` task on the same loop.
    """
    cfg = settings or get_settings()
    effective_interval = interval_seconds if interval_seconds is not None else cfg.worker_heartbeat_interval_seconds
    record_heartbeat(worker_type, instance_id=instance_id, pid=os.getpid())
    watchdog: _HeartbeatWatchdog | None = None
    if enable_watchdog:
        watchdog = _HeartbeatWatchdog(
            worker_type,
            stale_after_seconds=cfg.worker_watchdog_stale_after_seconds,
            check_interval_seconds=cfg.worker_watchdog_check_interval_seconds,
        )
        watchdog.start()
    task = asyncio.create_task(
        _heartbeat_loop(worker_type, instance_id=instance_id, interval_seconds=effective_interval, watchdog=watchdog)
    )
    return HeartbeatLoopHandle(task=task, worker_type=worker_type, instance_id=instance_id, watchdog=watchdog)

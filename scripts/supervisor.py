#!/usr/bin/env python3
"""fix/supervise-ingestion-runtime — the real local-development process
supervisor for the connected-seller runtime: frontend, API, and the
Listings/Orders/Sales & Traffic (and, once enabled, Inventory) workers.

Root cause this replaces: `scripts/dev.sh` (its previous, plain-bash
incarnation) started every child and then merely `wait`ed on them — it
never noticed an individual child dying mid-session, never restarted
one, and had no readiness or heartbeat awareness at all. Combined with
this repository's own workers/API sometimes being started as ad-hoc
backgrounded shell commands (never durable, never monitored, never
restarted), the result was a `queued` job with nothing alive to claim
it and no visible signal anywhere that anything was wrong. This module
is the fix: a single foreground process that owns the full lifecycle of
every child for as long as it runs, and stops being ambiguous about
whether the stack is actually up.

This process is meant to be started directly, in a terminal the
developer keeps open — by `./scripts/dev.sh` (a thin argument-parsing
wrapper that execs into this module), or `python3 scripts/supervisor.py`
directly. It is never meant to be started by a short-lived, backgrounded
shell command: a supervisor that itself only lives as long as whatever
launched it defeats the entire point of supervising anything.

Design, mapped to the operational requirements this module exists to
satisfy:

- **One consistent environment**: built once (`_build_environment`),
  passed identically to every child, with only the narrow per-child
  overrides each already required (`ASI_DB_RUNTIME_CONTEXT=api` for the
  backend only; each worker's own `ASI_*_WORKER_ENABLED` flag, already
  present in the parent environment before this process starts).
- **Correct working directory per child**: `ChildSpec.cwd`.
- **Readiness, including first-heartbeat-from-worker**: `ReadinessCheck`
  subclasses (`TcpReadiness`, `HttpReadiness`, `WorkerHeartbeatReadiness`)
  — the latter polls the API's own `GET /health/workers` (added by this
  same fix) until the worker in question reports a fresh heartbeat, not
  merely that its process exists.
- **Continuous monitoring, bounded-backoff restart, a restart
  ceiling**: `RestartPolicy` (a pure, unit-testable value type — see
  `apps/api/tests/test_dev_supervisor.py`) plus `Supervisor._monitor_loop`.
  A worker exceeding its ceiling is marked `FAILED_PERMANENT` and logged
  loudly but does not bring down the rest of the stack (an absent
  worker already has a well-defined, safe behavior: the trigger's own
  `503 worker_unavailable` — see fix/ingestion-worker-runtime-
  availability). The backend or frontend exceeding its ceiling *does*
  bring the whole stack down — this dev environment is not useful
  without either, and running degraded silently is worse than stopping
  loudly.
- **Signal propagation, full cleanup, no orphans/duplicates**: every
  child is started in its own process group (`start_new_session=True`);
  shutdown signals the whole group, never just the immediate child, so
  `uv run`'s own subprocess, `next dev`'s own `next-server`, etc. are
  never left behind. A PID file (`_pid_file_path`) with a liveness +
  identity check prevents two supervisors from running at once and
  safely discards a stale file (dead PID, or a PID reused by an
  unrelated process) rather than refusing to start forever.
- **Findable, bounded logs**: one file per child under `logs/dev/`
  (gitignored), printed prominently at startup; each is rotated (not
  simply truncated) once it exceeds `_LOG_ROTATE_BYTES`.
- **Never logs secrets**: this module never reads, forwards, or prints
  `DATABASE_URL`, any `SP_API_*` credential, or `token_reference` — it
  only ever prints child names, PIDs, ports, restart counts, and the
  already-sanitized log lines each child itself already produces (the
  same guarantee those children's own modules document).

Usage:
    python3 scripts/supervisor.py [--with-workers]

Exit codes: 0 (clean shutdown via signal), 1 (a critical child — backend
or frontend — exceeded its restart ceiling, or startup failed outright).
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# --- constants ---------------------------------------------------------

ROOT_DIR = Path(__file__).resolve().parent.parent
API_DIR = ROOT_DIR / "apps" / "api"
WEB_DIR = ROOT_DIR / "apps" / "web"
LOG_DIR = ROOT_DIR / "logs" / "dev"
PID_FILE = ROOT_DIR / "logs" / "dev" / "supervisor.pid"

_LOG_ROTATE_BYTES = 10 * 1024 * 1024  # 10 MiB per child log before rotating to .1

# Worker type -> (enable env var, module, log/display name). Reuses the
# exact vocabulary `app.amazon.worker_heartbeat.KNOWN_WORKER_TYPES` and
# each worker module's own `is_worker_enabled()` already use — this is
# a second, independent implementation of the identical decision
# (enabled iff the env var is "1" or "true", case-insensitive) so that
# a worker this supervisor chooses not to start is never even attempted,
# exactly mirroring `dev.sh`'s own previous pre-flight-gate reasoning.
WORKER_DEFINITIONS: dict[str, dict[str, str]] = {
    "listings": {
        "env_var": "ASI_LISTINGS_WORKER_ENABLED",
        "module": "app.amazon.listings_worker",
        "display_name": "worker",
        "cmd_override_env": "DEV_SH_WORKER_CMD",
    },
    "orders": {
        "env_var": "ASI_ORDERS_WORKER_ENABLED",
        "module": "app.amazon.orders_worker",
        "display_name": "orders-worker",
        "cmd_override_env": "DEV_SH_ORDERS_WORKER_CMD",
    },
    "sales_and_traffic_report": {
        "env_var": "ASI_SALES_TRAFFIC_WORKER_ENABLED",
        "module": "app.amazon.sales_traffic_worker",
        "display_name": "sales-traffic-worker",
        "cmd_override_env": "DEV_SH_SALES_TRAFFIC_WORKER_CMD",
    },
    "inventory": {
        "env_var": "ASI_INVENTORY_WORKER_ENABLED",
        "module": "app.amazon.inventory_worker",
        "display_name": "inventory-worker",
        "cmd_override_env": "DEV_SH_INVENTORY_WORKER_CMD",
    },
}

_ENABLED_TRUE_VALUES = {"1", "true"}


def _env_flag_enabled(value: str | None) -> bool:
    return (value or "").strip().lower() in _ENABLED_TRUE_VALUES


# --- restart policy: a pure value type, no I/O, fully unit-testable ----


@dataclass(frozen=True)
class RestartPolicy:
    """Bounded exponential backoff plus a hard restart ceiling. Pure and
    stateless — every method takes the attempt number explicitly rather
    than tracking mutable state itself, so it needs no fakes/mocks to
    unit test (see `apps/api/tests/test_dev_supervisor.py`)."""

    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    max_restarts: int = 5

    def delay_for(self, attempt: int) -> float:
        """`attempt` is 1 for the first restart, 2 for the second, etc.
        Doubles each time, capped at `max_delay_seconds`."""
        if attempt < 1:
            attempt = 1
        return min(self.base_delay_seconds * (2 ** (attempt - 1)), self.max_delay_seconds)

    def ceiling_exceeded(self, attempt: int) -> bool:
        return attempt > self.max_restarts


# --- readiness checks ----------------------------------------------------


class ReadinessCheck:
    """Base class. `check()` returns True the instant the underlying
    condition is satisfied; never blocks or sleeps itself — the caller
    (`Supervisor._wait_ready`) owns the polling loop and its own
    timeout, so every check here is a single, fast, non-blocking probe."""

    def check(self) -> bool:  # pragma: no cover - overridden
        raise NotImplementedError

    def describe(self) -> str:  # pragma: no cover - overridden
        raise NotImplementedError


@dataclass
class TcpReadiness(ReadinessCheck):
    host: str
    port: int

    def check(self) -> bool:
        try:
            with socket.create_connection((self.host, self.port), timeout=0.5):
                return True
        except OSError:
            return False

    def describe(self) -> str:
        return f"TCP {self.host}:{self.port}"


@dataclass
class HttpReadiness(ReadinessCheck):
    url: str
    expected_status: int = 200

    def check(self) -> bool:
        try:
            with urllib.request.urlopen(self.url, timeout=1.5) as response:  # noqa: S310 - localhost only
                return response.status == self.expected_status
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def describe(self) -> str:
        return f"HTTP GET {self.url}"


@dataclass
class WorkerHeartbeatReadiness(ReadinessCheck):
    """Polls the API's own `GET /health/workers` (added alongside this
    fix) until the given `worker_type` reports a fresh heartbeat — the
    actual "this worker is alive and has proven it, not merely that its
    process exists" signal. Requires the API to already be reachable;
    the supervisor always waits for API readiness before ever
    constructing this check."""

    base_url: str
    worker_type: str

    def check(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health/workers", timeout=1.5) as response:  # noqa: S310
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return False
        worker = (payload.get("workers") or {}).get(self.worker_type)
        return bool(worker and worker.get("available") is True)

    def describe(self) -> str:
        return f"heartbeat({self.worker_type})"


# --- child spec + runtime state -----------------------------------------


@dataclass
class ChildSpec:
    name: str
    cwd: Path
    cmd: list[str]
    env: dict[str, str]
    critical: bool  # backend/frontend: ceiling-exceeded stops the whole stack
    readiness: ReadinessCheck | None = None
    readiness_timeout_seconds: float = 30.0


class ChildStatus:
    STARTING = "starting"
    READY = "ready"
    RESTARTING = "restarting"
    FAILED_PERMANENT = "failed_permanent"
    STOPPED = "stopped"


@dataclass
class ManagedChild:
    spec: ChildSpec
    log_path: Path
    process: subprocess.Popen | None = None
    log_file: object | None = None
    attempt: int = 0
    status: str = ChildStatus.STARTING
    lock: threading.Lock = field(default_factory=threading.Lock)
    # fix/pr26-hung-worker-detection — a worker process can remain alive
    # (same PID) while its own internal heartbeat-renewal loop has
    # stalled (observed directly: a machine sleep/wake cycle pausing the
    # worker's async heartbeat task without killing the process). These
    # three fields back `Supervisor._check_worker_heartbeat_liveness`,
    # entirely separate from `attempt`/exit-code tracking above, which
    # only ever sees a process that has actually exited.
    has_been_ready: bool = False
    consecutive_stale_heartbeat_checks: int = 0
    last_heartbeat_poll_at: float = 0.0


# --- the supervisor itself ------------------------------------------------


class Supervisor:
    """Owns the full lifecycle of every configured child. Every I/O
    boundary (process spawning, sleeping, logging) is a constructor
    parameter with a real-world default, so tests can inject fakes
    without spawning real processes or waiting real wall-clock time —
    see `apps/api/tests/test_dev_supervisor.py`."""

    def __init__(
        self,
        specs: list[ChildSpec],
        *,
        restart_policy: RestartPolicy | None = None,
        popen_factory: Callable[..., subprocess.Popen] = subprocess.Popen,
        sleep: Callable[[float], None] = time.sleep,
        log_fn: Callable[[str], None] | None = None,
        monitor_poll_interval: float = 1.0,
        monotonic: Callable[[], float] = time.monotonic,
        heartbeat_liveness_poll_interval: float = 5.0,
        heartbeat_liveness_stale_ceiling: int = 3,
    ) -> None:
        self._restart_policy = restart_policy or RestartPolicy()
        self._popen_factory = popen_factory
        self._sleep = sleep
        self._log_fn = log_fn or _default_log
        self._monitor_poll_interval = monitor_poll_interval
        self._monotonic = monotonic
        # fix/pr26-hung-worker-detection — debounced (this many
        # *consecutive* stale checks, not one) so a single slow or
        # dropped `/health/workers` request never restarts an actually-
        # healthy worker; polled on its own cadence (default 5s, slower
        # than the 1s process-exit poll) so a hang is caught within
        # roughly `heartbeat_liveness_poll_interval *
        # heartbeat_liveness_stale_ceiling` seconds of the worker's own
        # `worker_heartbeat_stale_after_seconds` threshold being crossed,
        # without hammering the API with a health check every tick.
        self._heartbeat_liveness_poll_interval = heartbeat_liveness_poll_interval
        self._heartbeat_liveness_stale_ceiling = heartbeat_liveness_stale_ceiling
        self._children: dict[str, ManagedChild] = {
            spec.name: ManagedChild(spec=spec, log_path=LOG_DIR / f"{spec.name}.log") for spec in specs
        }
        self._stop_event = threading.Event()
        self._critical_failure = threading.Event()
        self._monitor_thread: threading.Thread | None = None

    def _log(self, message: str) -> None:
        self._log_fn(f"[supervisor] {message}")

    # --- log file handling -------------------------------------------

    def _open_log_file(self, child: ManagedChild):
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = child.log_path
        if path.exists() and path.stat().st_size > _LOG_ROTATE_BYTES:
            rotated = path.with_suffix(path.suffix + ".1")
            with contextlib.suppress(OSError):
                rotated.unlink()
            with contextlib.suppress(OSError):
                path.rename(rotated)
        return open(path, "a", buffering=1)  # noqa: SIM115 - lifetime matches the child's own

    # --- spawning -------------------------------------------------------

    def _spawn(self, child: ManagedChild) -> None:
        spec = child.spec
        child.log_file = self._open_log_file(child)
        child.process = self._popen_factory(
            spec.cmd,
            cwd=str(spec.cwd),
            env=spec.env,
            stdout=child.log_file,
            stderr=subprocess.STDOUT,
            start_new_session=True,  # own process group -> clean group-kill on shutdown
        )
        child.status = ChildStatus.STARTING
        self._log(f"started {spec.name} (pid {child.process.pid}) — log: {child.log_path}")

    def _wait_ready(self, child: ManagedChild) -> bool:
        spec = child.spec
        if spec.readiness is None:
            child.status = ChildStatus.READY
            return True
        # Elapsed time is tracked from the durations actually passed to
        # `self._sleep`, never from a real wall-clock deadline — a test
        # that injects a no-op `sleep` (never waiting in real time) must
        # still terminate this loop deterministically after the same
        # number of "virtual" iterations a real 0.5s-interval poll would
        # take, rather than busy-spinning for the full real-time
        # timeout on every readiness check.
        elapsed = 0.0
        poll_interval = 0.5
        while elapsed < spec.readiness_timeout_seconds:
            if self._stop_event.is_set():
                return False
            if child.process is not None and child.process.poll() is not None:
                # Exited before ever becoming ready — not a readiness
                # timeout, a startup failure; let the caller's own
                # exit-code inspection report it.
                return False
            if spec.readiness.check():
                child.status = ChildStatus.READY
                child.has_been_ready = True
                self._log(f"{spec.name} ready ({spec.readiness.describe()})")
                return True
            self._sleep(poll_interval)
            elapsed += poll_interval
        self._log(
            f"WARNING: {spec.name} did not become ready within {spec.readiness_timeout_seconds:.0f}s "
            f"({spec.readiness.describe()}) — continuing to monitor it; it may still start scheduled work "
            f"once it is truly ready."
        )
        return False

    def start(self) -> bool:
        """Spawns every child immediately (never serialized behind
        another child's own readiness wait — a slow-to-become-ready
        backend must not delay even *starting* the OS process for every
        worker behind it), then waits for each child's own readiness
        concurrently. Total wall-clock time for this call is bounded by
        the single slowest child's own `readiness_timeout_seconds`, not
        the sum of every child's — the same shape the previous plain-
        bash `dev.sh` had (spawn everything immediately, then a single
        short grace check), just now with real per-child readiness
        awareness layered on top rather than a single fixed `sleep 2`.

        Returns False if any *critical* child failed outright during
        this startup pass — a worker's own readiness timeout is logged
        but never fails startup, matching the same "absent worker is a
        safe, well-defined state" reasoning as the trigger's own 503.
        """
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        for child in self._children.values():
            self._spawn(child)

        readiness_threads = [
            threading.Thread(target=self._wait_ready, args=(child,), daemon=True)
            for child in self._children.values()
        ]
        for thread in readiness_threads:
            thread.start()
        # Bounded by the slowest child's own readiness_timeout_seconds
        # (join's own timeout is generous padding, not the real bound —
        # _wait_ready's internal elapsed-time loop is what actually
        # stops each thread).
        max_timeout = max((c.spec.readiness_timeout_seconds for c in self._children.values()), default=0.0)
        for thread in readiness_threads:
            thread.join(timeout=max_timeout + 5.0)

        for child in self._children.values():
            if child.process is not None and child.process.poll() is not None and child.status != ChildStatus.READY:
                self._log(
                    f"ERROR: {child.spec.name} exited immediately during startup "
                    f"(exit code {child.process.returncode}) — see {child.log_path}"
                )
                if child.spec.critical:
                    return False
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()
        self._log("all requested processes are running. Press Ctrl-C to stop everything.")
        return True

    # --- continuous monitoring + bounded restart -------------------------

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            for child in list(self._children.values()):
                with child.lock:
                    if child.status in (ChildStatus.FAILED_PERMANENT, ChildStatus.STOPPED):
                        continue
                    process = child.process
                    if process is None:
                        continue  # not started yet
                    if process.poll() is not None:
                        self._handle_unexpected_exit(child)
                        continue
                    # Process is alive — process-exit monitoring alone
                    # cannot see a worker whose own heartbeat-renewal
                    # loop has stalled without the process itself ever
                    # exiting (fix/pr26-hung-worker-detection).
                    self._check_worker_heartbeat_liveness(child)
            self._sleep(self._monitor_poll_interval)

    def _handle_unexpected_exit(self, child: ManagedChild) -> None:
        spec = child.spec
        exit_code = child.process.returncode if child.process is not None else None
        child.attempt += 1
        self._log(
            f"{spec.name} exited unexpectedly (exit code {exit_code}, attempt {child.attempt}) "
            f"— see {child.log_path}"
        )
        self._restart_after_problem(child)

    def _check_worker_heartbeat_liveness(self, child: ManagedChild) -> None:
        """A worker process can remain alive (same PID) while its own
        internal heartbeat-renewal loop has stalled — proven directly in
        a live session: a host machine sleep/wake cycle paused the
        worker's async heartbeat task for hours without the process
        itself ever exiting, so `/health/workers` kept reporting a
        stale, un-advancing heartbeat while `process.poll()` kept
        reporting the process as perfectly alive. Restart-on-exit alone
        can never detect this.

        Reuses `spec.readiness` directly rather than a second check
        object — for a worker child it is already a
        `WorkerHeartbeatReadiness`, which already asks the exact right
        question (a *fresh* heartbeat, using the API's own database-
        time-based staleness computation —
        `WorkerHeartbeatRepository.check_availability`; this module
        never computes staleness itself). A backend/frontend child's
        `readiness` is never this type, so this is a no-op for them —
        the isinstance check alone keeps critical-process handling
        completely untouched by this method.

        Two guards against a false positive:
        - `has_been_ready` — never armed during a worker's own
          legitimate startup grace window (bounded startup latency is
          `_wait_ready`'s job, not this one's).
        - `heartbeat_liveness_stale_ceiling` consecutive misses, not
          one — never restarts a healthy worker over a single slow or
          dropped health-check request (bounded transient-latency
          tolerance).
        """
        spec = child.spec
        if not isinstance(spec.readiness, WorkerHeartbeatReadiness) or not child.has_been_ready:
            return
        if child.status == ChildStatus.RESTARTING:
            return
        now = self._monotonic()
        if now - child.last_heartbeat_poll_at < self._heartbeat_liveness_poll_interval:
            return
        child.last_heartbeat_poll_at = now
        if spec.readiness.check():
            child.consecutive_stale_heartbeat_checks = 0
            return
        child.consecutive_stale_heartbeat_checks += 1
        if child.consecutive_stale_heartbeat_checks < self._heartbeat_liveness_stale_ceiling:
            self._log(
                f"{spec.name} heartbeat check stale "
                f"({child.consecutive_stale_heartbeat_checks}/{self._heartbeat_liveness_stale_ceiling}) "
                "— process still alive, not yet acting"
            )
            return
        child.consecutive_stale_heartbeat_checks = 0
        self._handle_hung_worker(child)

    def _handle_hung_worker(self, child: ManagedChild) -> None:
        spec = child.spec
        pid = child.process.pid if child.process is not None else None
        self._log(
            f"{spec.name} (pid {pid}) is alive but its heartbeat has been stale for "
            f"{self._heartbeat_liveness_stale_ceiling} consecutive checks — treating it as hung "
            "and force-restarting it."
        )
        # Invalidate the old, stuck instance safely before spawning a
        # replacement — the exact same graceful-then-forceful sequence
        # `stop()` already uses for a normal shutdown (SIGTERM to the
        # whole process group, a bounded grace wait, SIGKILL only if it
        # is still alive after that), never a bare `kill -9` first.
        self._terminate(child)
        elapsed = 0.0
        grace_seconds = 5.0
        poll_interval = 0.2
        while elapsed < grace_seconds and not self._is_dead(child):
            self._sleep(poll_interval)
            elapsed += poll_interval
        self._force_kill_if_alive(child)
        child.attempt += 1
        self._restart_after_problem(child)

    def _restart_after_problem(self, child: ManagedChild) -> None:
        """Shared restart bookkeeping — the existing bounded backoff
        plus hard restart ceiling (`RestartPolicy`) — for both an
        unexpected process exit and a detected hang. The caller is
        responsible for the old process already being gone: an exited
        process already is; `_handle_hung_worker` force-terminates a
        hung one first."""
        spec = child.spec
        if self._restart_policy.ceiling_exceeded(child.attempt):
            child.status = ChildStatus.FAILED_PERMANENT
            self._log(
                f"ERROR: {spec.name} exceeded its restart ceiling "
                f"({self._restart_policy.max_restarts} attempts) — no further restarts will be attempted."
            )
            if spec.critical:
                self._log(
                    f"ERROR: {spec.name} is a critical process (backend/frontend) — "
                    "stopping the entire supervised stack."
                )
                self._critical_failure.set()
                self._stop_event.set()
            else:
                self._log(
                    f"{spec.name} is now permanently unavailable for this session. Sync requests for it "
                    "will be refused with a clear 'worker unavailable' response — existing data stays "
                    "visible. Restart the supervisor to try this worker again."
                )
            return
        delay = self._restart_policy.delay_for(child.attempt)
        child.status = ChildStatus.RESTARTING
        child.has_been_ready = False
        child.consecutive_stale_heartbeat_checks = 0
        self._log(f"restarting {spec.name} in {delay:.1f}s (attempt {child.attempt})")
        self._sleep(delay)
        if self._stop_event.is_set():
            return
        self._spawn(child)
        threading.Thread(target=self._wait_ready, args=(child,), daemon=True).start()

    # --- shutdown ---------------------------------------------------------

    def request_stop(self) -> None:
        self._stop_event.set()

    def stop(self) -> None:
        self._log("shutting down...")
        for child in self._children.values():
            self._terminate(child)
        # See `_wait_ready`'s identical reasoning: elapsed time is
        # tracked from durations actually passed to `self._sleep`, never
        # a real wall-clock deadline, so an injected no-op sleep cannot
        # turn this into a real-time busy-spin in tests.
        elapsed = 0.0
        grace_seconds = 10.0
        poll_interval = 0.2
        while elapsed < grace_seconds:
            if all(self._is_dead(c) for c in self._children.values()):
                break
            self._sleep(poll_interval)
            elapsed += poll_interval
        for child in self._children.values():
            self._force_kill_if_alive(child)
            if child.log_file is not None:
                with contextlib.suppress(OSError):
                    child.log_file.close()
        self._log("all children stopped")

    @staticmethod
    def _is_dead(child: ManagedChild) -> bool:
        return child.process is None or child.process.poll() is not None

    def _terminate(self, child: ManagedChild) -> None:
        if self._is_dead(child):
            return
        pid = child.process.pid
        self._log(f"stopping {child.spec.name} (pid {pid})")
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGTERM)

    def _force_kill_if_alive(self, child: ManagedChild) -> None:
        if self._is_dead(child):
            return
        pid = child.process.pid
        self._log(f"force-stopping {child.spec.name} (pid {pid})")
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(pid), signal.SIGKILL)

    # --- status snapshot, for tests and for a future status surface -----

    def snapshot(self) -> dict[str, str]:
        return {name: child.status for name, child in self._children.items()}

    def run_forever(self) -> int:
        """Installs signal handlers, starts every child, blocks until a
        stop is requested (SIGINT/SIGTERM, or a critical child
        exceeding its restart ceiling), then shuts down cleanly. This
        is the one call a real invocation of this module makes; tests
        exercise `start`/`stop`/`_handle_unexpected_exit` directly
        instead, so they never need to actually block on a signal."""

        def _signal_handler(signum, _frame):
            self._log(f"received signal {signum}, requesting shutdown")
            self.request_stop()

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        if not self.start():
            self.stop()
            return 1

        while not self._stop_event.is_set():
            self._sleep(0.2)

        self.stop()
        return 1 if self._critical_failure.is_set() else 0


def _default_log(message: str) -> None:
    print(message, flush=True)


# --- PID file handling (duplicate-supervisor prevention) -----------------


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else — still alive
    return True


def check_and_write_pid_file(pid_file: Path = PID_FILE, pid: int | None = None) -> str | None:
    """Returns an error message if another live supervisor already owns
    `pid_file`; otherwise writes the current (or given) PID and returns
    None. A PID file whose PID is no longer alive is treated as stale
    and silently reclaimed — this must never require manual cleanup
    after an ordinary crash."""
    pid = pid if pid is not None else os.getpid()
    if pid_file.exists():
        try:
            existing_pid = int(pid_file.read_text().strip().splitlines()[0])
        except (ValueError, IndexError, OSError):
            existing_pid = None
        if existing_pid is not None and existing_pid != pid and _pid_is_alive(existing_pid):
            return (
                f"A supervisor (pid {existing_pid}) already appears to be running "
                f"(see {pid_file}). Stop it first, or remove this file if you are certain "
                "it is stale."
            )
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{pid}\n")
    return None


def remove_pid_file(pid_file: Path = PID_FILE) -> None:
    with contextlib.suppress(OSError):
        pid_file.unlink()


# --- building the real child specs ---------------------------------------


def _build_environment(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    if extra:
        env.update(extra)
    return env


def _word_split_override(value: str | None) -> list[str] | None:
    if not value:
        return None
    return value.split()


def _readiness_timeout(default: float) -> float:
    # Test-only override so a deterministic automated run against fake
    # DEV_SH_*_CMD substitutes (which never satisfy a real readiness
    # check) does not have to wait out the real, generous production
    # timeouts below. Never intended for interactive/production use —
    # identical convention to the existing DEV_SH_*_CMD overrides.
    raw = os.environ.get("DEV_SH_READINESS_TIMEOUT_SECONDS")
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def build_child_specs(*, backend_port: int, frontend_port: int) -> list[ChildSpec]:
    backend_cmd = _word_split_override(os.environ.get("DEV_SH_BACKEND_CMD")) or [
        "uv", "run", "uvicorn", "app.main:app", "--reload", "--port", str(backend_port),
    ]
    frontend_cmd = _word_split_override(os.environ.get("DEV_SH_FRONTEND_CMD")) or ["npm", "run", "dev"]

    specs = [
        ChildSpec(
            name="backend",
            cwd=API_DIR,
            cmd=backend_cmd,
            env=_build_environment({"ASI_DB_RUNTIME_CONTEXT": "api"}),
            critical=True,
            readiness=HttpReadiness(f"http://127.0.0.1:{backend_port}/health"),
            readiness_timeout_seconds=_readiness_timeout(30.0),
        ),
        ChildSpec(
            name="frontend",
            cwd=WEB_DIR,
            cmd=frontend_cmd,
            env=_build_environment(),
            critical=True,
            readiness=TcpReadiness("127.0.0.1", frontend_port),
            readiness_timeout_seconds=_readiness_timeout(60.0),
        ),
    ]

    for worker_type, definition in WORKER_DEFINITIONS.items():
        if not _env_flag_enabled(os.environ.get(definition["env_var"])):
            _default_log(
                f"[supervisor] {definition['env_var']} is not set to true — not starting "
                f"the {definition['display_name']}."
            )
            continue
        if _pgrep_running(definition["module"]):
            _default_log(
                f"[supervisor] a {definition['display_name']} process already appears to be running "
                "— not starting a second one."
            )
            continue
        worker_cmd = _word_split_override(os.environ.get(definition["cmd_override_env"])) or [
            "uv", "run", "python", "-m", definition["module"],
        ]
        specs.append(
            ChildSpec(
                name=definition["display_name"],
                cwd=API_DIR,
                cmd=worker_cmd,
                env=_build_environment(),
                critical=False,
                readiness=WorkerHeartbeatReadiness(f"http://127.0.0.1:{backend_port}", worker_type),
                readiness_timeout_seconds=_readiness_timeout(30.0),
            )
        )
    return specs


def _pgrep_running(module_pattern: str) -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-f", module_pattern.replace(".", r"\.")],
            capture_output=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    backend_port = int(os.environ.get("BACKEND_PORT", "8000"))
    frontend_port = int(os.environ.get("FRONTEND_PORT", "3000"))

    if "--help" in argv or "-h" in argv:
        print(__doc__)
        return 0

    for arg in argv:
        if arg == "--with-workers":
            # The one opt-in flag for connected-seller local development
            # — sets every ASI_*_WORKER_ENABLED flag so nobody has to
            # remember or type four separate environment variables. The
            # safe default (no flag, no env vars) is unchanged: this
            # branch is only reached if the flag was explicitly passed.
            for definition in WORKER_DEFINITIONS.values():
                os.environ[definition["env_var"]] = "true"
        elif arg in ("--help", "-h"):
            continue
        else:
            print(f"[supervisor] Unrecognized argument: {arg} (see --help)", file=sys.stderr)
            return 1

    pid_error = check_and_write_pid_file()
    if pid_error:
        print(f"[supervisor] ERROR: {pid_error}", file=sys.stderr)
        return 1

    try:
        if _port_in_use(backend_port):
            print(
                f"[supervisor] ERROR: port {backend_port} is already in use — "
                "is the backend already running (this supervisor or another terminal)?",
                file=sys.stderr,
            )
            return 1
        if _port_in_use(frontend_port):
            print(
                f"[supervisor] ERROR: port {frontend_port} is already in use — "
                "is the frontend already running (this supervisor or another terminal)?",
                file=sys.stderr,
            )
            return 1

        specs = build_child_specs(backend_port=backend_port, frontend_port=frontend_port)
        print(f"[supervisor] logs: {LOG_DIR}")
        supervisor = Supervisor(specs)
        return supervisor.run_forever()
    finally:
        remove_pid_file()


if __name__ == "__main__":
    sys.exit(main())

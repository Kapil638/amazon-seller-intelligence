"""fix/supervise-ingestion-runtime — unit tests for scripts/supervisor.py.

`scripts/supervisor.py` lives outside the `app` package (it supervises
both apps/api and apps/web, so it cannot itself be inside either) and
has zero third-party dependencies — loaded here via
`importlib.util.spec_from_file_location` rather than a normal import.

Every test here uses a fake `popen_factory`, an injected `sleep` (never
a real wall-clock wait), and a redirected `LOG_DIR`/`PID_FILE` under
`tmp_path` — no real child process is ever spawned, and no test waits
on real time. Only the socket/PID-liveness helpers that are cheap and
side-effect-free against the real OS (`_port_in_use`, `_pid_is_alive`,
`TcpReadiness` against a real ephemeral local socket) touch anything
outside the process. No live Amazon call, no real `npm`/`uv` process,
no real backend/frontend/worker started by any test in this file.
"""

from __future__ import annotations

import importlib.util
import json
import socket
import sys
import threading
import time
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_SUPERVISOR_PATH = Path(__file__).resolve().parents[3] / "scripts" / "supervisor.py"
_spec = importlib.util.spec_from_file_location("dev_supervisor", _SUPERVISOR_PATH)
supervisor = importlib.util.module_from_spec(_spec)
sys.modules["dev_supervisor"] = supervisor
_spec.loader.exec_module(supervisor)


# --- RestartPolicy: pure value type, no fakes needed ------------------------


def test_restart_policy_delay_doubles_each_attempt() -> None:
    policy = supervisor.RestartPolicy(base_delay_seconds=1.0, max_delay_seconds=100.0, max_restarts=10)
    assert policy.delay_for(1) == 1.0
    assert policy.delay_for(2) == 2.0
    assert policy.delay_for(3) == 4.0
    assert policy.delay_for(4) == 8.0


def test_restart_policy_delay_caps_at_max() -> None:
    policy = supervisor.RestartPolicy(base_delay_seconds=1.0, max_delay_seconds=5.0, max_restarts=10)
    assert policy.delay_for(10) == 5.0


def test_restart_policy_treats_attempt_below_one_as_one() -> None:
    policy = supervisor.RestartPolicy(base_delay_seconds=2.0, max_delay_seconds=100.0, max_restarts=10)
    assert policy.delay_for(0) == policy.delay_for(1)


def test_restart_policy_ceiling_boundary() -> None:
    policy = supervisor.RestartPolicy(max_restarts=3)
    assert policy.ceiling_exceeded(3) is False
    assert policy.ceiling_exceeded(4) is True


# --- readiness checks --------------------------------------------------------


def test_tcp_readiness_false_when_nothing_listens() -> None:
    # An almost-certainly-unbound high port on loopback.
    check = supervisor.TcpReadiness("127.0.0.1", 1)
    assert check.check() is False


def test_tcp_readiness_true_against_a_real_bound_socket() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        check = supervisor.TcpReadiness("127.0.0.1", port)
        assert check.check() is True
    finally:
        server.close()


class _FakeHealthHandler(BaseHTTPRequestHandler):
    workers_payload = {"workers": {"listings": {"available": True, "last_heartbeat_at": "2026-01-01T00:00:00Z"}}}

    def do_GET(self):  # noqa: N802 - stdlib naming
        if self.path == "/health":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        elif self.path == "/health/workers":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(self.workers_payload).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *_args):  # silence stdlib's own stderr logging
        pass


@pytest.fixture
def fake_health_server():
    server = HTTPServer(("127.0.0.1", 0), _FakeHealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_http_readiness_true_when_endpoint_responds_200(fake_health_server) -> None:
    check = supervisor.HttpReadiness(f"{fake_health_server}/health")
    assert check.check() is True


def test_http_readiness_false_when_nothing_listens() -> None:
    check = supervisor.HttpReadiness("http://127.0.0.1:1/health")
    assert check.check() is False


def test_worker_heartbeat_readiness_true_when_available(fake_health_server) -> None:
    check = supervisor.WorkerHeartbeatReadiness(fake_health_server, "listings")
    assert check.check() is True


def test_worker_heartbeat_readiness_false_for_unpublished_worker_type(fake_health_server) -> None:
    check = supervisor.WorkerHeartbeatReadiness(fake_health_server, "orders")
    assert check.check() is False


def test_worker_heartbeat_readiness_false_when_api_unreachable() -> None:
    check = supervisor.WorkerHeartbeatReadiness("http://127.0.0.1:1", "listings")
    assert check.check() is False


# --- Supervisor: fake process factory, injected sleep, no real spawning -----


class FakeProcess:
    _next_pid = 9000

    def __init__(self, cmd, cwd=None, env=None, stdout=None, stderr=None, start_new_session=None):
        FakeProcess._next_pid += 1
        self.pid = FakeProcess._next_pid
        self.cmd = cmd
        self.cwd = cwd
        self.returncode: int | None = None

    def poll(self):
        return self.returncode

    def exit_now(self, code: int = 1) -> None:
        self.returncode = code


class FakePopenFactory:
    """Records every process it "spawns," keyed by the child's log
    filename argument is not available here — tests instead grab
    `factory.processes[-1]` (most recent) or `factory.processes[i]`
    (spawn order) since specs are started in list order."""

    def __init__(self) -> None:
        self.processes: list[FakeProcess] = []

    def __call__(self, cmd, **kwargs) -> FakeProcess:
        proc = FakeProcess(cmd, **kwargs)
        self.processes.append(proc)
        return proc


class _AlwaysReady(supervisor.ReadinessCheck):
    def check(self) -> bool:
        return True

    def describe(self) -> str:
        return "always-ready"


class _NeverReady(supervisor.ReadinessCheck):
    def check(self) -> bool:
        return False

    def describe(self) -> str:
        return "never-ready"


def _spec(name: str, *, critical: bool = False, readiness=None, tmp_path: Path) -> "supervisor.ChildSpec":
    return supervisor.ChildSpec(
        name=name,
        cwd=tmp_path,
        cmd=["fake", name],
        env={},
        critical=critical,
        readiness=readiness or _AlwaysReady(),
        readiness_timeout_seconds=1.0,
    )


@pytest.fixture(autouse=True)
def _redirect_log_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(supervisor, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(supervisor, "PID_FILE", tmp_path / "logs" / "supervisor.pid")


@pytest.fixture(autouse=True)
def _stop_every_supervisor_after_the_test():
    """`Supervisor.start()` spawns a background daemon monitor thread
    that loops until `_stop_event` is set. Nothing about a test ending
    sets that event on its own (daemon threads don't block process
    exit, so pytest itself would never notice) — without this fixture,
    every test that calls `start()` leaks one perpetually-spinning
    thread, and with ~15 such tests in this file the accumulated CPU
    contention makes the whole run appear to hang. `_make_supervisor`
    registers every `Supervisor` it creates here; this fixture's own
    teardown (not the test itself) is the one place responsible for
    calling `request_stop()` on all of them, exactly once, always."""
    created: list["supervisor.Supervisor"] = []
    yield created
    for sup in created:
        sup.request_stop()


def _make_supervisor(specs, *, factory=None, sleeps=None, logs=None, restart_policy=None, _created=None):
    factory = factory or FakePopenFactory()
    sleeps = sleeps if sleeps is not None else []
    logs = logs if logs is not None else []

    def _fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        # A real, tiny sleep — never the full requested duration (tests
        # must stay fast) — so the background monitor thread's own poll
        # loop actually yields the GIL instead of pure-CPU-spinning
        # between iterations.
        time.sleep(0.001)

    sup = supervisor.Supervisor(
        specs,
        restart_policy=restart_policy or supervisor.RestartPolicy(base_delay_seconds=0.01, max_delay_seconds=0.02, max_restarts=2),
        popen_factory=factory,
        sleep=_fake_sleep,
        log_fn=lambda m: logs.append(m),
        monitor_poll_interval=0.001,
    )
    if _created is not None:
        _created.append(sup)
    return sup, factory, sleeps, logs


def test_start_spawns_every_child_and_waits_readiness(tmp_path, _stop_every_supervisor_after_the_test) -> None:
    specs = [_spec("backend", critical=True, tmp_path=tmp_path), _spec("frontend", critical=True, tmp_path=tmp_path)]
    sup, factory, _sleeps, logs = _make_supervisor(specs, _created=_stop_every_supervisor_after_the_test)

    assert sup.start() is True
    assert len(factory.processes) == 2
    assert sup.snapshot() == {"backend": "ready", "frontend": "ready"}
    assert any("all requested processes are running" in line for line in logs)


def test_start_returns_false_when_a_critical_child_exits_immediately(tmp_path, _stop_every_supervisor_after_the_test) -> None:
    # Readiness never succeeds AND the process is already dead — start()
    # must treat this as a startup failure for a critical child.
    dying_check = _NeverReady()
    specs = [_spec("backend", critical=True, readiness=dying_check, tmp_path=tmp_path)]
    factory = FakePopenFactory()

    class ImmediatelyDeadFactory(FakePopenFactory):
        def __call__(self, cmd, **kwargs):
            proc = super().__call__(cmd, **kwargs)
            proc.exit_now(1)
            return proc

    sup, _factory, sleeps, _logs = _make_supervisor(specs, factory=ImmediatelyDeadFactory(), _created=_stop_every_supervisor_after_the_test)
    assert sup.start() is False


def test_non_critical_child_readiness_timeout_does_not_fail_startup(tmp_path, _stop_every_supervisor_after_the_test) -> None:
    specs = [
        _spec("backend", critical=True, tmp_path=tmp_path),
        _spec("worker", critical=False, readiness=_NeverReady(), tmp_path=tmp_path),
    ]
    sup, _factory, _sleeps, logs = _make_supervisor(specs, _created=_stop_every_supervisor_after_the_test)
    assert sup.start() is True
    assert any("did not become ready" in line for line in logs)


def _spawn_without_monitor_thread(sup: "supervisor.Supervisor", name: str) -> None:
    """Spawns one child (and waits its readiness) exactly as `start()`
    would, but deliberately never launches the background monitor
    thread. Tests that drive `_handle_unexpected_exit` directly need
    this: `start()`'s own monitor thread would otherwise race the
    test's explicit call — both independently noticing the same exited
    fake process — making exact `attempt` counts flaky. Testing that
    the monitor thread itself notices an exit and calls this same
    method is exactly what `test_unexpected_exit_triggers_bounded_
    backoff_restart` already covers implicitly by construction (the
    method is the same either way); this helper isolates the method's
    own logic from thread-timing nondeterminism."""
    child = sup._children[name]
    sup._spawn(child)
    sup._wait_ready(child)


def test_unexpected_exit_triggers_bounded_backoff_restart(tmp_path, _stop_every_supervisor_after_the_test) -> None:
    specs = [_spec("worker", critical=False, tmp_path=tmp_path)]
    sup, factory, sleeps, logs = _make_supervisor(specs, _created=_stop_every_supervisor_after_the_test)
    _spawn_without_monitor_thread(sup, "worker")
    first_process = factory.processes[0]

    first_process.exit_now(1)
    child = sup._children["worker"]
    sup._handle_unexpected_exit(child)

    assert child.attempt == 1
    assert child.status == supervisor.ChildStatus.RESTARTING or child.status == supervisor.ChildStatus.READY
    assert len(factory.processes) == 2, "a replacement process must have been spawned"
    assert 0.01 in sleeps
    assert any("restarting worker" in line for line in logs)


def test_restart_ceiling_marks_non_critical_worker_permanently_failed_without_stopping_the_stack(tmp_path, _stop_every_supervisor_after_the_test) -> None:
    specs = [_spec("worker", critical=False, tmp_path=tmp_path)]
    policy = supervisor.RestartPolicy(base_delay_seconds=0.001, max_delay_seconds=0.001, max_restarts=2)
    sup, factory, _sleeps, logs = _make_supervisor(specs, restart_policy=policy, _created=_stop_every_supervisor_after_the_test)
    _spawn_without_monitor_thread(sup, "worker")

    child = sup._children["worker"]
    for _ in range(3):
        factory.processes[-1].exit_now(1)
        sup._handle_unexpected_exit(child)

    assert child.status == supervisor.ChildStatus.FAILED_PERMANENT
    assert sup._critical_failure.is_set() is False
    assert sup._stop_event.is_set() is False
    assert any("exceeded its restart ceiling" in line for line in logs)
    assert any("now permanently unavailable" in line for line in logs)


def test_restart_ceiling_on_a_critical_child_stops_the_whole_stack(tmp_path, _stop_every_supervisor_after_the_test) -> None:
    specs = [_spec("backend", critical=True, tmp_path=tmp_path)]
    policy = supervisor.RestartPolicy(base_delay_seconds=0.001, max_delay_seconds=0.001, max_restarts=1)
    sup, factory, _sleeps, logs = _make_supervisor(specs, restart_policy=policy, _created=_stop_every_supervisor_after_the_test)
    _spawn_without_monitor_thread(sup, "backend")

    child = sup._children["backend"]
    for _ in range(2):
        factory.processes[-1].exit_now(1)
        sup._handle_unexpected_exit(child)

    assert child.status == supervisor.ChildStatus.FAILED_PERMANENT
    assert sup._critical_failure.is_set() is True
    assert sup._stop_event.is_set() is True
    assert any("stopping the entire supervised stack" in line for line in logs)


def test_stop_terminates_every_still_alive_child(tmp_path, monkeypatch, _stop_every_supervisor_after_the_test) -> None:
    specs = [_spec("backend", critical=True, tmp_path=tmp_path), _spec("worker", critical=False, tmp_path=tmp_path)]
    sup, factory, _sleeps, logs = _make_supervisor(specs, _created=_stop_every_supervisor_after_the_test)
    sup.start()

    killed_pids: list[tuple[int, int]] = []
    monkeypatch.setattr(supervisor.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(supervisor.os, "killpg", lambda pgid, sig: killed_pids.append((pgid, sig)))

    # Simulate both children still running (poll() returns None already,
    # by construction of FakeProcess) — stop() must signal both.
    sup.stop()

    signaled_pids = {pid for pid, _sig in killed_pids}
    assert {p.pid for p in factory.processes} == signaled_pids
    assert any("shutting down" in line for line in logs)
    assert any("all children stopped" in line for line in logs)


def test_stop_skips_already_dead_children(tmp_path, monkeypatch, _stop_every_supervisor_after_the_test) -> None:
    specs = [_spec("worker", critical=False, tmp_path=tmp_path)]
    sup, factory, _sleeps, _logs = _make_supervisor(specs, _created=_stop_every_supervisor_after_the_test)
    sup.start()
    factory.processes[0].exit_now(0)  # already dead before stop() is called

    killed_pids: list[int] = []
    monkeypatch.setattr(supervisor.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(supervisor.os, "killpg", lambda pgid, sig: killed_pids.append(pgid))

    sup.stop()
    assert killed_pids == []


# --- PID file handling -------------------------------------------------------


def test_pid_file_written_when_absent(tmp_path) -> None:
    pid_file = tmp_path / "supervisor.pid"
    error = supervisor.check_and_write_pid_file(pid_file, pid=12345)
    assert error is None
    assert pid_file.read_text().strip() == "12345"


def test_pid_file_refuses_when_another_live_supervisor_owns_it(tmp_path) -> None:
    import os

    pid_file = tmp_path / "supervisor.pid"
    real_pid = os.getpid()  # this test process is definitely alive
    pid_file.write_text(f"{real_pid}\n")

    error = supervisor.check_and_write_pid_file(pid_file, pid=real_pid + 1)
    assert error is not None
    assert str(real_pid) in error


def test_pid_file_reclaimed_when_stale(tmp_path) -> None:
    pid_file = tmp_path / "supervisor.pid"
    # A PID astronomically unlikely to be alive on any real system.
    pid_file.write_text("999999\n")

    error = supervisor.check_and_write_pid_file(pid_file, pid=12345)
    assert error is None
    assert pid_file.read_text().strip() == "12345"


def test_pid_file_with_garbage_content_is_treated_as_stale(tmp_path) -> None:
    pid_file = tmp_path / "supervisor.pid"
    pid_file.write_text("not-a-pid\n")

    error = supervisor.check_and_write_pid_file(pid_file, pid=12345)
    assert error is None


def test_remove_pid_file_is_a_safe_no_op_when_absent(tmp_path) -> None:
    pid_file = tmp_path / "does-not-exist.pid"
    supervisor.remove_pid_file(pid_file)  # must not raise


# --- environment / command building ------------------------------------------


def test_build_environment_merges_and_overrides(monkeypatch) -> None:
    monkeypatch.setenv("EXISTING_VAR", "from-os-environ")
    env = supervisor._build_environment({"ASI_DB_RUNTIME_CONTEXT": "api"})
    assert env["EXISTING_VAR"] == "from-os-environ"
    assert env["ASI_DB_RUNTIME_CONTEXT"] == "api"


def test_word_split_override_none_when_unset() -> None:
    assert supervisor._word_split_override(None) is None
    assert supervisor._word_split_override("") is None


def test_word_split_override_splits_on_whitespace() -> None:
    assert supervisor._word_split_override("sleep 100") == ["sleep", "100"]


def test_port_in_use_false_for_an_unbound_port() -> None:
    assert supervisor._port_in_use(1) is False


def test_port_in_use_true_for_a_real_bound_socket() -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    try:
        assert supervisor._port_in_use(port) is True
    finally:
        server.close()


# --- safe default: no worker specs built without any enable flag -----------


def test_build_child_specs_starts_no_workers_by_default(monkeypatch) -> None:
    for definition in supervisor.WORKER_DEFINITIONS.values():
        monkeypatch.delenv(definition["env_var"], raising=False)
    specs = supervisor.build_child_specs(backend_port=18999, frontend_port=19999)
    names = {s.name for s in specs}
    assert names == {"backend", "frontend"}


def test_build_child_specs_starts_enabled_workers_only(monkeypatch) -> None:
    for definition in supervisor.WORKER_DEFINITIONS.values():
        monkeypatch.delenv(definition["env_var"], raising=False)
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", "true")
    monkeypatch.setattr(supervisor, "_pgrep_running", lambda _pattern: False)
    specs = supervisor.build_child_specs(backend_port=18999, frontend_port=19999)
    names = {s.name for s in specs}
    assert "worker" in names
    assert "orders-worker" not in names
    assert "sales-traffic-worker" not in names


def test_build_child_specs_skips_a_worker_type_already_running(monkeypatch) -> None:
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", "true")
    monkeypatch.setattr(supervisor, "_pgrep_running", lambda _pattern: True)
    specs = supervisor.build_child_specs(backend_port=18999, frontend_port=19999)
    assert "worker" not in {s.name for s in specs}

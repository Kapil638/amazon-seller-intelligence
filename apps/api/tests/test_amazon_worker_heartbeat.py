"""fix/ingestion-worker-runtime-availability — `AmazonWorkerHeartbeat`
repository primitives, the shared `app.amazon.worker_heartbeat` module,
and its background heartbeat-loop task.

Covers requirements 6-9 from the governing task: a healthy heartbeat
means available, a missing/stale one means unavailable, and a freshly
started loop is available immediately (startup grace) rather than only
after its first idle-poll-length sleep.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from app.amazon.worker_heartbeat import (
    check_availability,
    invalidate_heartbeat,
    new_instance_id,
    record_heartbeat,
    start_heartbeat_loop,
)
from app.core.config import get_settings
from app.persistence.database import session_scope
from app.persistence.models import AmazonWorkerHeartbeat
from app.persistence.repositories import WorkerHeartbeatRepository


def _test_settings():
    return get_settings()


def test_unknown_worker_type_is_rejected() -> None:
    with session_scope() as session:
        with pytest.raises(TypeError):
            WorkerHeartbeatRepository(session).record_heartbeat("not_a_real_worker_type", instance_id="x", pid=1)


def test_no_heartbeat_row_means_unavailable() -> None:
    with session_scope() as session:
        availability = WorkerHeartbeatRepository(session).check_availability("orders", stale_after_seconds=45)
    assert availability.available is False
    assert availability.last_heartbeat_at is None


def test_fresh_heartbeat_means_available() -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("listings", instance_id="inst-1", pid=1234)
        session.commit()
    with session_scope() as session:
        availability = WorkerHeartbeatRepository(session).check_availability("listings", stale_after_seconds=45)
    assert availability.available is True
    assert availability.last_heartbeat_at is not None


def test_stale_heartbeat_means_unavailable() -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat(
            "sales_and_traffic_report", instance_id="inst-1", pid=1234
        )
        session.commit()
    # Simulate a heartbeat that stopped arriving a long time ago.
    with session_scope() as session:
        row = session.get(AmazonWorkerHeartbeat, "sales_and_traffic_report")
        row.last_heartbeat_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()
    with session_scope() as session:
        availability = WorkerHeartbeatRepository(session).check_availability(
            "sales_and_traffic_report", stale_after_seconds=45
        )
    assert availability.available is False
    assert availability.last_heartbeat_at is not None


def test_record_heartbeat_is_idempotent_and_advances_last_heartbeat_at() -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="inst-1", pid=1)
        session.commit()
    with session_scope() as session:
        first = session.get(AmazonWorkerHeartbeat, "orders").last_heartbeat_at
    with session_scope() as session:
        row = session.get(AmazonWorkerHeartbeat, "orders")
        row.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=30)
        session.commit()
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="inst-1", pid=1)
        session.commit()
    with session_scope() as session:
        row = session.get(AmazonWorkerHeartbeat, "orders")
        assert row.last_heartbeat_at > first - timedelta(seconds=30)
        assert row.instance_id == "inst-1"


def test_record_heartbeat_updates_started_at_when_a_new_instance_takes_over() -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="inst-old", pid=111)
        session.commit()
    with session_scope() as session:
        original_started_at = session.get(AmazonWorkerHeartbeat, "orders").started_at

    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="inst-new", pid=222)
        session.commit()
    with session_scope() as session:
        row = session.get(AmazonWorkerHeartbeat, "orders")
        assert row.instance_id == "inst-new"
        assert row.pid == 222
        assert row.started_at >= original_started_at


def test_module_level_record_and_check_helpers_round_trip() -> None:
    instance_id = new_instance_id()
    record_heartbeat("listings", instance_id=instance_id, pid=999)
    availability = check_availability("listings", settings=_test_settings())
    assert availability.available is True


def test_check_availability_respects_configured_stale_after_seconds() -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("listings", instance_id="inst-1", pid=1)
        row = session.get(AmazonWorkerHeartbeat, "listings")
        row.last_heartbeat_at = datetime.now(UTC) - timedelta(seconds=20)
        session.commit()
    # 20 seconds old: available under a 45s threshold, unavailable under a 10s one.
    assert check_availability("listings", stale_after_seconds=45).available is True
    assert check_availability("listings", stale_after_seconds=10).available is False


@pytest.mark.asyncio
async def test_heartbeat_loop_writes_immediately_startup_grace() -> None:
    """The core 'startup grace' guarantee: availability is true the
    instant the loop starts, not only after its first interval sleep —
    so a normal combined launcher (frontend + API + workers starting
    together) does not produce a false unavailable result for a Sync
    click that happens moments later."""
    handle = start_heartbeat_loop("orders", instance_id="grace-test", interval_seconds=60.0)
    try:
        assert check_availability("orders", stale_after_seconds=5).available is True
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_heartbeat_loop_keeps_writing_on_its_interval() -> None:
    handle = start_heartbeat_loop("orders", instance_id="interval-test", interval_seconds=0.05)
    try:
        with session_scope() as session:
            first = session.get(AmazonWorkerHeartbeat, "orders").last_heartbeat_at
        await asyncio.sleep(0.2)
        with session_scope() as session:
            second = session.get(AmazonWorkerHeartbeat, "orders").last_heartbeat_at
        assert second > first
    finally:
        await handle.stop()


@pytest.mark.asyncio
async def test_heartbeat_loop_stop_cancels_the_background_task_cleanly() -> None:
    handle = start_heartbeat_loop("orders", instance_id="stop-test", interval_seconds=0.05)
    await handle.stop()
    assert handle.task.cancelled() or handle.task.done()


@pytest.mark.asyncio
async def test_heartbeat_loop_survives_a_transient_write_failure(monkeypatch) -> None:
    """A single failed heartbeat write (e.g. a transient database
    hiccup — the same class of failure the claim loop's own bounded
    backoff already tolerates) must never crash the background task;
    the next tick tries again."""
    import app.amazon.worker_heartbeat as module

    calls = {"count": 0}
    real_record = module.record_heartbeat

    def _flaky_record(worker_type: str, *, instance_id: str, pid=None) -> None:
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("simulated transient failure")
        real_record(worker_type, instance_id=instance_id, pid=pid)

    monkeypatch.setattr(module, "record_heartbeat", _flaky_record)
    handle = start_heartbeat_loop("orders", instance_id="flaky-test", interval_seconds=0.03)
    try:
        await asyncio.sleep(0.15)
        assert calls["count"] >= 3
    finally:
        await handle.stop()


# --- fix/supervise-ingestion-runtime: graceful-shutdown invalidation ----


def test_invalidate_heartbeat_deletes_the_owning_instances_row() -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("listings", instance_id="inst-a", pid=1)
        session.commit()

    invalidate_heartbeat("listings", instance_id="inst-a")

    with session_scope() as session:
        assert session.get(AmazonWorkerHeartbeat, "listings") is None
    assert check_availability("listings").available is False


def test_invalidate_heartbeat_never_deletes_a_newer_instances_row() -> None:
    """The exact race this exists to close: an old process's delayed
    shutdown call must never delete a *different*, newer process's
    already-published heartbeat."""
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="inst-old", pid=1)
        session.commit()

    # A newer instance takes over the row before the old one's shutdown
    # invalidation call arrives (record_heartbeat's own upsert already
    # proves this transition — see test_record_heartbeat_updates_
    # started_at_when_a_new_instance_takes_over).
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="inst-new", pid=2)
        session.commit()

    invalidate_heartbeat("orders", instance_id="inst-old")

    with session_scope() as session:
        row = session.get(AmazonWorkerHeartbeat, "orders")
        assert row is not None
        assert row.instance_id == "inst-new"
    assert check_availability("orders").available is True


def test_invalidate_heartbeat_is_a_safe_no_op_when_no_row_exists() -> None:
    invalidate_heartbeat("sales_and_traffic_report", instance_id="never-started")  # must not raise


def test_invalidate_heartbeat_swallows_a_database_failure(monkeypatch) -> None:
    import app.amazon.worker_heartbeat as module

    def _raise(*_args, **_kwargs):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(module, "session_scope", _raise)
    invalidate_heartbeat("listings", instance_id="inst-a")  # must not raise


@pytest.mark.asyncio
async def test_heartbeat_loop_stop_invalidates_availability_immediately() -> None:
    """The actual production behavior this fix adds: a worker's
    graceful shutdown must make it unavailable *immediately*, not only
    once worker_heartbeat_stale_after_seconds has elapsed."""
    handle = start_heartbeat_loop("sales_and_traffic_report", instance_id="grace-shutdown-test", interval_seconds=60.0)
    assert check_availability("sales_and_traffic_report", stale_after_seconds=45.0).available is True

    await handle.stop()

    assert check_availability("sales_and_traffic_report", stale_after_seconds=45.0).available is False
    with session_scope() as session:
        assert session.get(AmazonWorkerHeartbeat, "sales_and_traffic_report") is None

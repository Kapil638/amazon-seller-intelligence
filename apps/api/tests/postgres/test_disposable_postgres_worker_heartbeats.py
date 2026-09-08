"""fix/ingestion-worker-runtime-availability — Disposable PostgreSQL
validation for migration 0015 (`amazon_worker_heartbeats`).

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. This table carries no durable business data —
`downgrade()` is a plain `drop_table`, so there is no "refuse to
downgrade" case to prove here, unlike every other migration's own
Postgres-guarded suite.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.repositories import WorkerHeartbeatRepository
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@contextmanager
def _alembic_environment(url: str):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
def disposable_engine():
    url = _guard.disposable_url()
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        if existing_tables - {"alembic_version"}:
            pytest.fail(
                "POSTGRES_DISPOSABLE_TEST_URL points at a non-empty database "
                f"({len(existing_tables)} existing table(s)) — refusing to run "
                "destructive migration tests against it. Use a genuinely fresh "
                "disposable instance."
            )
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


# 1: empty-database upgrade produces the expected table/constraint shape.
def test_empty_postgres_upgrade_produces_expected_heartbeat_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_worker_heartbeats")

    inspector = inspect(disposable_engine)
    assert "amazon_worker_heartbeats" in set(inspector.get_table_names())
    checks = {c["name"] for c in inspector.get_check_constraints("amazon_worker_heartbeats")}
    assert "ck_amazon_worker_heartbeats_worker_type" in checks
    columns = {c["name"] for c in inspector.get_columns("amazon_worker_heartbeats")}
    assert {"worker_type", "instance_id", "pid", "started_at", "last_heartbeat_at"}.issubset(columns)


# 2: an unrecognized worker_type is rejected by the real CHECK constraint,
# not merely by the application-layer TypeError this same value would hit
# through the repository — proven by inserting it via raw SQL, bypassing
# the application layer entirely.
def test_unknown_worker_type_rejected_by_real_postgres_check_constraint(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    with disposable_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(
                text(
                    "INSERT INTO amazon_worker_heartbeats "
                    "(worker_type, instance_id, started_at, last_heartbeat_at) "
                    "VALUES ('not_a_real_worker_type', 'x', now(), now())"
                )
            )


# 3: downgrade is a clean drop — no durable data, nothing to refuse.
def test_downgrade_0015_to_0014_drops_the_table_cleanly(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_worker_heartbeats")
        command.downgrade(cfg, "0014_sales_traffic_foundation")

    inspector = inspect(disposable_engine)
    assert "amazon_worker_heartbeats" not in set(inspector.get_table_names())


# 4: concurrent first-heartbeat writes for the SAME worker_type under REAL
# PostgreSQL — proves WorkerHeartbeatRepository.record_heartbeat's own
# SAVEPOINT-and-retry-on-IntegrityError path against a genuine primary-key
# race, not merely SQLite's single-connection semantics (where two
# sessions never actually race at the database level in this test suite).
@dataclass
class _HeartbeatAttemptOutcome:
    label: str
    error: str | None


def _heartbeat_attempt(*, engine, label, barrier, outcomes, lock):
    try:
        barrier.wait(timeout=10)
        with Session(engine) as session:
            WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id=label, pid=1)
            session.commit()
        with lock:
            outcomes.append(_HeartbeatAttemptOutcome(label=label, error=None))
    except Exception as exc:  # noqa: BLE001 - captured for the assertion below, not swallowed silently.
        with lock:
            outcomes.append(_HeartbeatAttemptOutcome(label=label, error=repr(exc)))


def test_concurrent_first_heartbeat_writes_for_the_same_worker_type_never_crash_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    barrier = threading.Barrier(2)
    outcomes: list[_HeartbeatAttemptOutcome] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_heartbeat_attempt,
            kwargs=dict(engine=disposable_engine, label=label, barrier=barrier, outcomes=outcomes, lock=lock),
        )
        for label in ("worker-a", "worker-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    errors = [o for o in outcomes if o.error is not None]
    assert errors == [], errors
    assert len(outcomes) == 2

    with Session(disposable_engine) as session:
        row = WorkerHeartbeatRepository(session).get_heartbeat("orders")
        assert row is not None
        assert row.instance_id in ("worker-a", "worker-b")


# 5: fix/inventory-heartbeat-check-constraint — the actual live bug this
# migration closes, reproduced directly against real PostgreSQL: an
# 'inventory' heartbeat is rejected by the CHECK constraint as it stood
# after 0015/0016 (the Inventory worker crashed on every startup attempt
# with exactly this IntegrityError), and accepted once 0017 has run.
def test_inventory_heartbeat_rejected_before_0017_and_accepted_after(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    insert_inventory_heartbeat = text(
        "INSERT INTO amazon_worker_heartbeats "
        "(worker_type, instance_id, started_at, last_heartbeat_at) "
        "VALUES ('inventory', 'x', now(), now())"
    )

    with _alembic_environment(url):
        command.upgrade(cfg, "0016_inventory_foundation")
    with disposable_engine.begin() as conn:
        with pytest.raises(IntegrityError):
            conn.execute(insert_inventory_heartbeat)

    # A failed statement leaves the transaction unusable in PostgreSQL —
    # the fixture's own connection needs a fresh one for the next attempt.
    with _alembic_environment(url):
        command.upgrade(cfg, "0017_inventory_heartbeat")
    with disposable_engine.begin() as conn:
        conn.execute(insert_inventory_heartbeat)  # must not raise
        count = conn.execute(
            text("SELECT COUNT(*) FROM amazon_worker_heartbeats WHERE worker_type = 'inventory'")
        ).scalar()
        assert count == 1


# 6: downgrade narrows the CHECK back — proven to fail loudly (not
# silently corrupt data) if an 'inventory' row still exists, exactly the
# "unsafe to downgrade with the wider type still in use" case a CHECK
# narrowing must guard against.
def test_downgrade_0017_refuses_while_an_inventory_row_still_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0017_inventory_heartbeat")
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_worker_heartbeats "
                "(worker_type, instance_id, started_at, last_heartbeat_at) "
                "VALUES ('inventory', 'x', now(), now())"
            )
        )

    with _alembic_environment(url):
        with pytest.raises(IntegrityError):
            command.downgrade(cfg, "0016_inventory_foundation")

"""fix/supervise-ingestion-runtime — `GET /health/workers`, the
sanitized runtime-health surface `scripts/supervisor.py` polls for
worker-heartbeat readiness and that a future UI surface can poll
directly. Built on the exact same `WorkerHeartbeatRepository.
check_availability` the sync-trigger services already use — never a
second notion of "available."
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.amazon.worker_heartbeat import KNOWN_WORKER_TYPES
from app.persistence.database import session_scope
from app.persistence.repositories import WorkerHeartbeatRepository


def test_health_workers_reports_all_known_types_unavailable_when_no_heartbeat_exists(client: TestClient) -> None:
    response = client.get("/health/workers")
    assert response.status_code == 200
    workers = response.json()["workers"]
    assert set(workers.keys()) == set(KNOWN_WORKER_TYPES)
    for worker_type in KNOWN_WORKER_TYPES:
        assert workers[worker_type]["available"] is False
        assert workers[worker_type]["last_heartbeat_at"] is None


def test_health_workers_reports_a_fresh_heartbeat_as_available(client: TestClient) -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("listings", instance_id="test-instance", pid=1)
        session.commit()

    response = client.get("/health/workers")
    assert response.status_code == 200
    workers = response.json()["workers"]
    assert workers["listings"]["available"] is True
    assert workers["listings"]["last_heartbeat_at"] is not None
    # Every other known type is still unavailable — this route never
    # conflates one worker type's heartbeat with another's.
    for worker_type in KNOWN_WORKER_TYPES:
        if worker_type != "listings":
            assert workers[worker_type]["available"] is False


def test_health_workers_never_carries_an_organization_seller_or_connection_identifier(client: TestClient) -> None:
    with session_scope() as session:
        WorkerHeartbeatRepository(session).record_heartbeat("orders", instance_id="test-instance", pid=1)
        session.commit()

    response = client.get("/health/workers")
    body = response.json()
    # The only keys this route may ever expose per worker type.
    for worker_type, payload in body["workers"].items():
        assert set(payload.keys()) == {"available", "last_heartbeat_at"}

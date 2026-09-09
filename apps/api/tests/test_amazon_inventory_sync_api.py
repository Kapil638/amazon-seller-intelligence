"""12B.6B — `POST .../inventory/sync` (enqueue) and `GET .../inventory/
sync/{run_id}` (job status) HTTP routes. No live Amazon call: the
underlying trigger service is faked via `app.dependency_overrides`,
exactly like `test_amazon_listings_sync_api.py`'s established pattern.
This file tests only the routes' status-code mapping and response
sanitization; ownership/enqueue/admission-control behavior is covered by
`test_amazon_inventory_sync_trigger.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.amazon.inventory_sync import (
    InventorySyncJobStatus,
    InventorySyncTriggerOutcome,
    get_amazon_inventory_sync_service,
)
from app.main import app

PARTICIPATION_ID = uuid4()
RUN_ID = uuid4()


def _job(**overrides) -> InventorySyncJobStatus:
    base = dict(
        run_id=RUN_ID,
        run_type="inventory",
        status="queued",
        marketplace_participation_id=PARTICIPATION_ID,
        pages_fetched=0,
        records_received=0,
        records_accepted=0,
        records_rejected=0,
        reported_total_results=None,
        pagination_complete=False,
        attempt_count=0,
        queued_at=datetime.now(UTC),
        started_at=None,
        last_heartbeat_at=None,
        next_retry_at=None,
        completed_at=None,
        failure_class=None,
    )
    base.update(overrides)
    return InventorySyncJobStatus(**base)


class _FakeTriggerService:
    def __init__(self, outcome: InventorySyncTriggerOutcome | None = None, status_result=None) -> None:
        self._outcome = outcome
        self._status_result = status_result
        self.trigger_calls: list = []
        self.status_calls: list = []

    def trigger(self, marketplace_participation_id):
        self.trigger_calls.append(marketplace_participation_id)
        return self._outcome

    def get_status(self, marketplace_participation_id, run_id):
        self.status_calls.append((marketplace_participation_id, run_id))
        return self._status_result


def _url(participation_id=PARTICIPATION_ID) -> str:
    return f"/api/v1/amazon/marketplace-participations/{participation_id}/inventory/sync"


def _status_url(participation_id=PARTICIPATION_ID, run_id=RUN_ID) -> str:
    return f"/api/v1/amazon/marketplace-participations/{participation_id}/inventory/sync/{run_id}"


def _use(fake) -> None:
    app.dependency_overrides[get_amazon_inventory_sync_service] = lambda: fake


def _reset() -> None:
    app.dependency_overrides.pop(get_amazon_inventory_sync_service, None)


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    _reset()


_FORBIDDEN_FIELDS = {
    "organization_id", "seller_account_id", "connection_id",
    "token_reference", "refresh_token", "access_token", "client_secret", "lease_owner", "page_token",
}


# --- POST .../inventory/sync -------------------------------------------


def test_new_job_returns_202_with_sanitized_job(client) -> None:
    fake = _FakeTriggerService(outcome=InventorySyncTriggerOutcome(reason="queued", job=_job()))
    _use(fake)
    response = client.post(_url())
    assert response.status_code == 202
    body = response.json()
    assert body["reason"] == "queued"
    assert body["job"]["run_id"] == str(RUN_ID)
    assert body["job"]["status"] == "queued"
    assert fake.trigger_calls == [PARTICIPATION_ID]
    assert _FORBIDDEN_FIELDS.isdisjoint(body["job"].keys())


def test_already_running_returns_409_with_the_existing_job(client) -> None:
    fake = _FakeTriggerService(
        outcome=InventorySyncTriggerOutcome(reason="already_running", job=_job(status="started"))
    )
    _use(fake)
    response = client.post(_url())
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["reason"] == "already_running"
    assert detail["job"]["status"] == "started"


def test_scope_not_found_returns_sanitized_404(client) -> None:
    fake = _FakeTriggerService(outcome=InventorySyncTriggerOutcome(reason="scope_not_found"))
    _use(fake)
    response = client.post(_url())
    assert response.status_code == 404
    assert response.json()["detail"]["job"] is None


@pytest.mark.parametrize(
    "reason,expected_status",
    [
        ("scope_inactive", 503),
        ("identity_missing", 503),
        ("connection_unresolvable", 503),
        ("worker_unavailable", 503),
        ("cooldown", 429),
        ("queue_backlog_limit_reached", 429),
    ],
)
def test_reason_to_status_mapping_distinguishes_failure_categories(client, reason, expected_status) -> None:
    fake = _FakeTriggerService(outcome=InventorySyncTriggerOutcome(reason=reason))
    _use(fake)
    response = client.post(_url())
    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert detail["reason"] == reason


def test_unexpected_raised_exception_never_leaks_its_message(client) -> None:
    class _RaisingFake:
        def trigger(self, marketplace_participation_id):
            raise RuntimeError("super-secret-internal-detail-12345")

    _use(_RaisingFake())
    response = client.post(_url())
    assert response.status_code == 500
    assert "super-secret-internal-detail-12345" not in response.text


def test_malformed_uuid_rejected_with_sanitized_validation_error(client) -> None:
    response = client.post("/api/v1/amazon/marketplace-participations/not-a-uuid/inventory/sync")
    assert response.status_code == 400


# --- GET .../inventory/sync/{run_id} -------------------------------------


def test_job_status_returns_200_with_sanitized_progress(client) -> None:
    fake = _FakeTriggerService(status_result=_job(status="started", pages_fetched=3, records_accepted=42))
    _use(fake)
    response = client.get(_status_url())
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "started"
    assert body["pages_fetched"] == 3
    assert body["records_accepted"] == 42
    assert fake.status_calls == [(PARTICIPATION_ID, RUN_ID)]


def test_job_status_returns_404_for_none_result(client) -> None:
    fake = _FakeTriggerService(status_result=None)
    _use(fake)
    response = client.get(_status_url())
    assert response.status_code == 404


def test_job_status_never_exposes_forbidden_fields(client) -> None:
    fake = _FakeTriggerService(status_result=_job())
    _use(fake)
    response = client.get(_status_url())
    body = response.json()
    assert _FORBIDDEN_FIELDS.isdisjoint(body.keys())


def test_job_status_malformed_run_id_rejected(client) -> None:
    response = client.get(_status_url(run_id="not-a-uuid"))
    assert response.status_code == 400


# --- fix/inventory-empty-response-and-failure-classification ---------------
# Investigation 3: a live 30s client timeout was traced to `async def`
# route handlers calling blocking sync-database code directly, stalling
# the single shared event loop for every other concurrent request. The
# fix (this route, and `/health`/`/health/workers`, are now plain `def`
# — Starlette dispatches those to its own worker thread pool) is proven
# here directly: a slow concurrent request never delays a fast one.


def test_enqueue_route_does_not_block_a_concurrent_request(client) -> None:
    import threading
    import time

    class _SlowTriggerService(_FakeTriggerService):
        def trigger(self, marketplace_participation_id):
            time.sleep(1.0)  # simulates a slow blocking database call
            return super().trigger(marketplace_participation_id)

    slow_fake = _SlowTriggerService(outcome=InventorySyncTriggerOutcome(reason="queued", job=_job()))
    _use(slow_fake)

    slow_done = threading.Event()

    def _run_slow_request() -> None:
        client.post(_url())
        slow_done.set()

    slow_thread = threading.Thread(target=_run_slow_request)
    slow_thread.start()
    time.sleep(0.1)  # let the slow request actually start first

    fast_start = time.monotonic()
    fast_response = client.get("/health")
    fast_elapsed = time.monotonic() - fast_start

    slow_thread.join(timeout=5)
    assert fast_response.status_code == 200
    # The whole point: the fast request must not have waited behind the
    # slow one's own ~1s sleep — a blocking `async def` route would have
    # made this take close to 1s too (event-loop-serialized); a plain
    # `def` route lets Starlette's thread pool run them concurrently.
    assert fast_elapsed < 0.5, f"fast request took {fast_elapsed:.2f}s — appears serialized behind the slow one"
    assert slow_done.is_set()

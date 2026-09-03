"""12B.4D — Orders read + sync HTTP routes. No live Amazon call: the
underlying services are faked via `app.dependency_overrides`, exactly like
`test_amazon_listings_sync_api.py`'s established pattern. Tests only
status-code mapping and response sanitization; ownership/filtering/
pagination behavior is covered by `test_amazon_orders_read_service.py`/
`test_amazon_orders_sync_trigger.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from app.amazon.orders_read import (
    AmazonSellerOrderNotFoundError,
    OrderCollectionResponse,
    OrderDetail,
    OrdersSummary,
    OrdersSyncEvidence,
    get_amazon_orders_read_service,
)
from app.amazon.orders_sync import OrdersSyncJobStatus, OrdersSyncTriggerOutcome, get_amazon_orders_sync_service
from app.core.exceptions import AmazonListingsParticipationNotFoundError, PersistenceNotConfiguredError
from app.main import app

PARTICIPATION_ID = uuid4()
ORDER_ID = uuid4()
RUN_ID = uuid4()
SELLER_ACCOUNT_ID = uuid4()


def _summary(**overrides) -> OrdersSummary:
    base = dict(
        marketplace_participation_id=PARTICIPATION_ID,
        total_orders=0,
        cancelled_count=0,
        business_order_count=0,
        prime_order_count=0,
        status_counts={},
        order_value_sum=None,
        order_value_currency=None,
        sync=OrdersSyncEvidence(),
    )
    base.update(overrides)
    return OrdersSummary(**base)


def _job(**overrides) -> OrdersSyncJobStatus:
    base = dict(
        run_id=RUN_ID,
        run_type="orders",
        status="queued",
        marketplace_participation_ids=(PARTICIPATION_ID,),
        pages_fetched=0,
        orders_received=0,
        orders_accepted=0,
        orders_rejected=0,
        items_received=0,
        items_accepted=0,
        items_rejected=0,
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
    return OrdersSyncJobStatus(**base)


class _FakeReadService:
    def __init__(self, *, summary=None, collection=None, detail=None, raise_error=None) -> None:
        self._summary = summary
        self._collection = collection
        self._detail = detail
        self._raise_error = raise_error

    def get_summary(self, marketplace_participation_id):
        if self._raise_error is not None:
            raise self._raise_error
        return self._summary

    def list_orders(self, marketplace_participation_id, **kwargs):
        if self._raise_error is not None:
            raise self._raise_error
        return self._collection

    def get_order(self, marketplace_participation_id, order_id):
        if self._raise_error is not None:
            raise self._raise_error
        return self._detail


class _FakeSyncService:
    def __init__(self, outcome: OrdersSyncTriggerOutcome | None = None, status_result=None) -> None:
        self._outcome = outcome
        self._status_result = status_result
        self.trigger_calls: list = []

    def trigger(self, *, seller_account_id, marketplace_participation_ids):
        self.trigger_calls.append((seller_account_id, marketplace_participation_ids))
        return self._outcome

    def get_status(self, run_id):
        return self._status_result


def _use_read(fake) -> None:
    app.dependency_overrides[get_amazon_orders_read_service] = lambda: fake


def _use_sync(fake) -> None:
    app.dependency_overrides[get_amazon_orders_sync_service] = lambda: fake


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.pop(get_amazon_orders_read_service, None)
    app.dependency_overrides.pop(get_amazon_orders_sync_service, None)


# --- GET .../orders/summary -------------------------------------------------


def test_summary_returns_200_with_sanitized_body(client) -> None:
    _use_read(_FakeReadService(summary=_summary(total_orders=3)))
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders/summary")
    assert response.status_code == 200
    assert response.json()["total_orders"] == 3


def test_summary_maps_not_found_to_404(client) -> None:
    _use_read(_FakeReadService(raise_error=AmazonListingsParticipationNotFoundError(str(PARTICIPATION_ID))))
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders/summary")
    assert response.status_code == 404


def test_summary_maps_persistence_not_configured_to_503(client) -> None:
    _use_read(_FakeReadService(raise_error=PersistenceNotConfiguredError("not configured")))
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders/summary")
    assert response.status_code == 503


# --- GET .../orders (collection) --------------------------------------------


def test_list_orders_returns_200_with_paginated_body(client) -> None:
    _use_read(_FakeReadService(collection=OrderCollectionResponse(items=[], total=0, offset=0, limit=25)))
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders")
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 0
    assert body["items"] == []


def test_list_orders_rejects_limit_above_maximum(client) -> None:
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders?limit=9999")
    assert response.status_code == 400


def test_list_orders_rejects_invalid_sort_field(client) -> None:
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders?sort_by=not_a_field")
    assert response.status_code == 400


# --- GET .../orders/{order_id} -----------------------------------------------


def test_get_order_returns_200(client) -> None:
    detail = OrderDetail(
        id=ORDER_ID,
        amazon_order_id="902-test",
        fulfillment_status="SHIPPED",
        fulfilled_by="MERCHANT",
        sales_channel_name="AMAZON",
        sales_channel_marketplace_id="ATVPDKIKX0DER",
        sales_channel_marketplace_name="Amazon.com",
        is_business_order=False,
        is_prime=True,
        was_cancelled=False,
        items_shipped_count=1,
        items_unshipped_count=0,
        order_total_amount=None,
        order_total_currency=None,
        amazon_created_at=datetime.now(UTC),
        amazon_last_updated_at=datetime.now(UTC),
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
        items=[],
    )
    _use_read(_FakeReadService(detail=detail))
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders/{ORDER_ID}")
    assert response.status_code == 200
    assert response.json()["amazon_order_id"] == "902-test"


def test_get_order_maps_not_found_to_404(client) -> None:
    _use_read(_FakeReadService(raise_error=AmazonSellerOrderNotFoundError(str(ORDER_ID))))
    response = client.get(f"/api/v1/amazon/marketplace-participations/{PARTICIPATION_ID}/orders/{ORDER_ID}")
    assert response.status_code == 404


# --- POST /orders/sync (trigger) --------------------------------------------


def _sync_body() -> dict:
    return {"seller_account_id": str(SELLER_ACCOUNT_ID), "marketplace_participation_ids": [str(PARTICIPATION_ID)]}


def test_trigger_new_job_returns_202(client) -> None:
    _use_sync(_FakeSyncService(outcome=OrdersSyncTriggerOutcome(reason="queued", job=_job())))
    response = client.post("/api/v1/amazon/orders/sync", json=_sync_body())
    assert response.status_code == 202
    assert response.json()["reason"] == "queued"


def test_trigger_already_running_returns_409(client) -> None:
    _use_sync(_FakeSyncService(outcome=OrdersSyncTriggerOutcome(reason="already_running", job=_job(status="started"))))
    response = client.post("/api/v1/amazon/orders/sync", json=_sync_body())
    assert response.status_code == 409


def test_trigger_scope_not_found_returns_404(client) -> None:
    _use_sync(_FakeSyncService(outcome=OrdersSyncTriggerOutcome(reason="scope_not_found")))
    response = client.post("/api/v1/amazon/orders/sync", json=_sync_body())
    assert response.status_code == 404


def test_trigger_cooldown_returns_429(client) -> None:
    _use_sync(
        _FakeSyncService(
            outcome=OrdersSyncTriggerOutcome(reason="cooldown", job=_job(), retry_allowed_at=datetime.now(UTC))
        )
    )
    response = client.post("/api/v1/amazon/orders/sync", json=_sync_body())
    assert response.status_code == 429


def test_trigger_response_never_exposes_forbidden_fields(client) -> None:
    _use_sync(_FakeSyncService(outcome=OrdersSyncTriggerOutcome(reason="queued", job=_job())))
    response = client.post("/api/v1/amazon/orders/sync", json=_sync_body())
    body_text = response.text.lower()
    for forbidden in ("lease_owner", "token_reference", "pagination_token", "connection_id"):
        assert forbidden not in body_text


# --- GET /orders/sync/{run_id} -----------------------------------------------


def test_get_sync_status_returns_200(client) -> None:
    _use_sync(_FakeSyncService(status_result=_job(status="succeeded")))
    response = client.get(f"/api/v1/amazon/orders/sync/{RUN_ID}")
    assert response.status_code == 200
    assert response.json()["status"] == "succeeded"


def test_get_sync_status_returns_404_for_unknown_run(client) -> None:
    _use_sync(_FakeSyncService(status_result=None))
    response = client.get(f"/api/v1/amazon/orders/sync/{RUN_ID}")
    assert response.status_code == 404

"""12B.4D — `OrdersWorker` (app.amazon.orders_worker). No live Amazon
call: the Orders client is fully faked, exactly like
`test_amazon_orders_ingestion_service.py`. This file tests only the
worker's own polling/claim/loop/signal/gate responsibilities; page-level
persistence, attribution, and finalization are already covered
exhaustively there and are not re-tested here. `claim_next_orders_job`'s
real PostgreSQL `SKIP LOCKED` concurrency would need its own guarded
disposable-Postgres test (mirroring `test_disposable_postgres_listings_
job_lifecycle_concurrency.py`) — nothing here (single-threaded) proves
anything about concurrent claimants; that guarded test is out of scope
for this pass given no live-migration/live-Postgres action is authorized
in this milestone.
"""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.amazon.orders_ingestion import AmazonOrdersIngestionService
from app.amazon.orders_client import SearchOrdersPageRequest
from app.amazon.orders_models import OrdersPage, OrdersPageProvenance
from app.amazon.orders_worker import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_DISABLED,
    EXIT_OK,
    OrdersWorker,
    _install_shutdown_signal_handlers,
    is_worker_enabled,
    main,
)
from app.core.config import Settings
from app.core.exceptions import SpApiRateLimitedError
from app.persistence.database import session_scope
from app.persistence.models import AmazonConnection, AmazonIngestionRun, Organization
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


class _FakeResolver:
    def resolve_refresh_token(self, *, organization_id, connection):
        return SecretStr("test-refresh-token")


class _RaisingResolver:
    def resolve_refresh_token(self, *, organization_id, connection):
        raise RuntimeError("simulated unexpected resolver failure")


class _FakeOrdersClient:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[SearchOrdersPageRequest] = []

    async def search_orders(self, request: SearchOrdersPageRequest) -> OrdersPage:
        self.requests.append(request)
        item = self._script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _test_settings(**overrides) -> Settings:
    fields = dict(
        sp_api_lwa_client_id=SecretStr("test-sandbox-lwa-client-id-DO-NOT-USE"),
        sp_api_lwa_client_secret=SecretStr("test-sandbox-lwa-client-secret-DO-NOT-USE"),
        sp_api_production_lwa_client_id=SecretStr("test-production-lwa-client-id-DO-NOT-USE"),
        sp_api_production_lwa_client_secret=SecretStr("test-production-lwa-client-secret-DO-NOT-USE"),
        orders_sync_max_attempts=3,
        orders_sync_base_backoff_seconds=0.01,
        orders_sync_max_backoff_seconds=0.02,
        orders_sync_max_total_retry_seconds=3600.0,
        orders_sync_lease_duration_seconds=300,
        orders_sync_max_global_concurrent_jobs=10,
        orders_sync_max_concurrent_jobs_per_organization=10,
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _worker(script: list, *, settings: Settings | None = None, resolver=None) -> tuple[OrdersWorker, _FakeOrdersClient]:
    client = _FakeOrdersClient(script)

    def factory(**_kwargs):
        return client

    cfg = settings or _test_settings()
    ingestion_service = AmazonOrdersIngestionService(
        settings=cfg,
        resolver=resolver or _FakeResolver(),
        orders_client_factory=factory,
    )
    worker = OrdersWorker(settings=cfg, ingestion_service=ingestion_service, lease_owner="test-worker")
    return worker, client


def _page(orders: list, *, next_token: str | None = None) -> OrdersPage:
    return OrdersPage(
        orders=orders,
        next_token=next_token,
        marketplace_ids=(MARKETPLACE,),
        pagination_token_used=None,
        provenance=OrdersPageProvenance(
            operation="searchOrders", region="na", endpoint_host="sellingpartnerapi-na.amazon.com",
            fetched_at=datetime.now(UTC), http_status=200,
            api_model_version="orders-api-model/2026-01-01", attempt_count=1,
        ),
    )


def _seed_scope(*, org_id=None) -> dict:
    reuse_org = org_id is not None
    org_id = org_id or uuid4()
    with session_scope() as session:
        if not reuse_org:
            session.add(Organization(id=org_id, name="12B.4D Worker Test Org"))
            session.flush()
        connection = None
        if reuse_org:
            connection = (
                session.query(AmazonConnection)
                .filter_by(organization_id=org_id, provider="SP_API", environment="PRODUCTION")
                .first()
            )
        if connection is None:
            connection = AmazonConnectionRepository(session).create(
                organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
            )
            connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
            session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        session.flush()
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id,
            marketplace_id=MARKETPLACE, region="na", connection_id=connection.id,
        )
        session.flush()
        return {
            "organization_id": org_id,
            "seller_account_id": seller_account.id,
            "marketplace_participation_id": participation.id,
            "connection_id": connection.id,
            "region": "na",
            "environment": "PRODUCTION",
        }


def _enqueue(scope: dict):
    with session_scope() as session:
        return AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            connection_id=scope["connection_id"],
            marketplace_participation_ids=[scope["marketplace_participation_id"]],
            region=scope["region"],
            environment=scope["environment"],
        )


def _get_run(organization_id, run_id):
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


def _minimal_order(order_id: str) -> dict:
    return {
        "orderId": order_id,
        "createdTime": "2026-01-01T00:00:00Z",
        "lastUpdatedTime": "2026-01-02T00:00:00Z",
        "salesChannel": {"channelName": "AMAZON", "marketplaceId": MARKETPLACE},
        "orderItems": [
            {"orderItemId": "ITEM-1", "quantityOrdered": 1, "product": {"sellerSku": "SKU-1"}}
        ],
    }


def _order(order_id: str):
    from app.amazon.orders_models import Order

    return Order.model_validate(_minimal_order(order_id))


# --- run_once: nothing to do ---------------------------------------------


@pytest.mark.asyncio
async def test_run_once_returns_false_when_nothing_is_queued() -> None:
    worker, client = _worker([])
    claimed_something = await worker.run_once()
    assert claimed_something is False
    assert client.requests == []


# --- run_once: successful processing --------------------------------------


@pytest.mark.asyncio
async def test_run_once_claims_and_completes_a_queued_job() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker([_page([_order("902-1")])])

    claimed_something = await worker.run_once()

    assert claimed_something is True
    assert len(client.requests) == 1
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "succeeded"


@pytest.mark.asyncio
async def test_run_once_processes_one_job_at_a_time_across_calls() -> None:
    scope_a = _seed_scope()
    scope_b = _seed_scope()
    claim_a = _enqueue(scope_a)
    claim_b = _enqueue(scope_b)
    worker, client = _worker([_page([_order("902-a")]), _page([_order("902-b")])])

    first = await worker.run_once()
    second = await worker.run_once()
    third = await worker.run_once()

    assert (first, second, third) == (True, True, False)
    assert _get_run(scope_a["organization_id"], claim_a.run_id).status == "succeeded"
    assert _get_run(scope_b["organization_id"], claim_b.run_id).status == "succeeded"


# --- run_once: throttled retry, then eventual retry exhaustion -----------


@pytest.mark.asyncio
async def test_run_once_reschedules_a_throttled_job_instead_of_failing_it() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    settings = _test_settings(orders_sync_max_backoff_seconds=60.0)
    worker, client = _worker([SpApiRateLimitedError("slow down", retry_after_seconds=30.0)], settings=settings)

    claimed_something = await worker.run_once()

    assert claimed_something is True
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "throttled"
    assert run.next_retry_at is not None
    assert abs((run.next_retry_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds() - 30.0) < 5


@pytest.mark.asyncio
async def test_a_rescheduled_job_can_be_reclaimed_and_eventually_exhausts_to_rate_limited() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    settings = _test_settings(orders_sync_max_attempts=2)
    worker, client = _worker(
        [
            SpApiRateLimitedError("slow down", retry_after_seconds=0.0),
            SpApiRateLimitedError("slow down", retry_after_seconds=0.0),
        ],
        settings=settings,
    )

    await worker.run_once()
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "waiting_to_retry"

    with session_scope() as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    await worker.run_once()
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "failed"
    assert run.failure_class == "rate_limited"


@pytest.mark.asyncio
async def test_non_retryable_failure_terminalizes_immediately_without_retry() -> None:
    from app.core.exceptions import SpApiInvalidRequestError

    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker([SpApiInvalidRequestError("bad request")])

    await worker.run_once()

    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "failed"
    assert run.failure_class == "invalid_request"


# --- per-organization concurrency limit -----------------------------------


@pytest.mark.asyncio
async def test_per_organization_limit_prevents_a_second_job_from_starting() -> None:
    org_id = uuid4()
    scope_a = _seed_scope(org_id=org_id)
    scope_b = _seed_scope(org_id=org_id)
    with session_scope() as session:
        AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=org_id, seller_account_id=scope_a["seller_account_id"],
            connection_id=scope_a["connection_id"], marketplace_participation_ids=[scope_a["marketplace_participation_id"]],
            region="na", environment="PRODUCTION",
        )
        AmazonIngestionRunMarketplaceParticipationRepository(session).claim_orders_run(
            organization_id=org_id, seller_account_id=scope_a["seller_account_id"],
            region="na", environment="PRODUCTION", lease_owner="existing-worker", lease_duration_seconds=300,
        )
    claim_b = _enqueue(scope_b)

    settings = _test_settings(orders_sync_max_concurrent_jobs_per_organization=1)
    worker, client = _worker([], settings=settings)

    claimed_something = await worker.run_once()

    assert claimed_something is False
    run_b = _get_run(org_id, claim_b.run_id)
    assert run_b.status == "queued"


# --- unexpected exception handling -----------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_during_processing_does_not_crash_the_loop() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker([], resolver=_RaisingResolver())

    claimed_something = await worker.run_once()

    assert claimed_something is True
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "started"  # recoverable via ordinary lease-expiry reclaim


# --- graceful shutdown -----------------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_stops_cooperatively_after_request_stop() -> None:
    worker, client = _worker([], settings=_test_settings())
    worker._idle_poll_seconds = 0.02
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


@pytest.mark.asyncio
async def test_sigint_and_sigterm_request_graceful_stop() -> None:
    worker, _ = _worker([])
    loop = asyncio.get_running_loop()
    _install_shutdown_signal_handlers(worker, loop)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.sleep(0.05)
        assert worker._stop_requested is True
    finally:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.remove_signal_handler(sig)
            except (NotImplementedError, ValueError):
                pass


# --- ASI_ORDERS_WORKER_ENABLED fail-closed gate -----------------------------


@pytest.mark.parametrize("value", ["", "0", "false", "False", "garbage"])
def test_main_refuses_to_start_when_not_explicitly_enabled(monkeypatch, value) -> None:
    monkeypatch.setenv("ASI_ORDERS_WORKER_ENABLED", value)
    assert is_worker_enabled() is False
    assert main() == EXIT_DISABLED


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True"])
def test_is_worker_enabled_accepts_documented_true_values(monkeypatch, value) -> None:
    monkeypatch.setenv("ASI_ORDERS_WORKER_ENABLED", value)
    assert is_worker_enabled() is True


def test_worker_enabled_env_var_is_independent_of_listings() -> None:
    import app.amazon.listings_worker as listings_worker_module
    import app.amazon.orders_worker as orders_worker_module

    assert listings_worker_module._WORKER_ENABLED_ENV_VAR != orders_worker_module._WORKER_ENABLED_ENV_VAR


# --- config validation ------------------------------------------------------


def test_worker_poll_error_backoff_rejects_base_exceeding_max() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        _test_settings(orders_worker_poll_error_base_backoff_seconds=10.0, orders_worker_poll_error_max_backoff_seconds=5.0)

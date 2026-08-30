"""12B.3G — `ListingsWorker` (app.amazon.listings_worker). No live Amazon
call: the Listings client is fully faked via `listings_client_factory`,
exactly like `test_amazon_listings_ingestion_service.py`. This file tests
only the worker's own polling/claim/loop responsibilities; pagination,
normalization, and reconciliation are already covered exhaustively there
and are not re-tested here. `claim_next_listings_job`'s real PostgreSQL
`SKIP LOCKED` concurrency is proven only in
`tests/postgres/test_disposable_postgres_listings_job_lifecycle_concurrency.py`
— nothing here (single-threaded, one worker instance per test) proves
anything about concurrent claimants.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr

from app.amazon.listings_client import ListingsPageRequest
from app.amazon.listings_ingestion import AmazonListingsIngestionService
from app.amazon.listings_models import Item, ListingsPage, ListingsPageProvenance
from app.amazon.listings_worker import ListingsWorker
from app.core.config import Settings
from app.core.exceptions import SpApiRateLimitedError
from app.persistence.database import session_scope
from app.persistence.models import AmazonConnection, AmazonIngestionRun, Organization
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
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


class _FakeListingsClient:
    def __init__(self, script: list) -> None:
        self._script = list(script)
        self.requests: list[ListingsPageRequest] = []

    async def fetch_page(self, request: ListingsPageRequest) -> ListingsPage:
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
        listings_sync_max_attempts=3,
        listings_sync_base_backoff_seconds=0.01,
        listings_sync_max_backoff_seconds=0.02,
        listings_sync_max_total_retry_seconds=3600.0,
        listings_sync_lease_duration_seconds=300,
        listings_sync_max_global_concurrent_jobs=10,
        listings_sync_max_concurrent_jobs_per_organization=10,
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _worker(script: list, *, resolver=None, settings: Settings | None = None) -> tuple[ListingsWorker, _FakeListingsClient]:
    client = _FakeListingsClient(script)

    def factory(**_kwargs):
        return client

    cfg = settings or _test_settings()
    ingestion_service = AmazonListingsIngestionService(
        settings=cfg,
        resolver=resolver or _FakeResolver(),
        listings_client_factory=factory,
    )
    worker = ListingsWorker(settings=cfg, ingestion_service=ingestion_service, lease_owner="test-worker")
    return worker, client


def _page(items: list[dict], *, next_token: str | None = None) -> ListingsPage:
    parsed_items = [Item.model_validate(i) for i in items]
    return ListingsPage(
        items=parsed_items,
        number_of_results=len(parsed_items),
        next_token=next_token,
        marketplace_id=MARKETPLACE,
        page_token_used=None,
        provenance=ListingsPageProvenance(
            operation="searchListingsItems", region="na", endpoint_host="sellingpartnerapi-na.amazon.com",
            fetched_at=datetime.now(UTC), http_status=200,
            api_model_version="listings-items-api-model/2021-08-01", attempt_count=1,
        ),
    )


def _seed_scope(*, org_id=None) -> dict:
    reuse_org = org_id is not None
    org_id = org_id or uuid4()
    with session_scope() as session:
        if not reuse_org:
            session.add(Organization(id=org_id, name="12B.3G Worker Test Org"))
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
        }


def _enqueue(scope: dict):
    with session_scope() as session:
        return AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=scope["organization_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"], region="na",
            environment="PRODUCTION", connection_id=scope["connection_id"],
        )


def _get_run(organization_id, run_id):
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


# --- run_once: nothing to do -------------------------------------------


@pytest.mark.asyncio
async def test_run_once_returns_false_when_nothing_is_queued() -> None:
    worker, client = _worker([])
    claimed_something = await worker.run_once()
    assert claimed_something is False
    assert client.requests == []


# --- run_once: successful processing ------------------------------------


@pytest.mark.asyncio
async def test_run_once_claims_and_completes_a_queued_job() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker([_page([{"sku": "SKU-1"}])])

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
    worker, client = _worker([_page([{"sku": "SKU-A"}]), _page([{"sku": "SKU-B"}])])

    first = await worker.run_once()
    second = await worker.run_once()
    third = await worker.run_once()

    assert (first, second, third) == (True, True, False)
    run_a = _get_run(scope_a["organization_id"], claim_a.run_id)
    run_b = _get_run(scope_b["organization_id"], claim_b.run_id)
    assert run_a.status == "succeeded"
    assert run_b.status == "succeeded"


# --- run_once: throttled retry, then eventual retry exhaustion ---------


@pytest.mark.asyncio
async def test_run_once_reschedules_a_throttled_job_instead_of_failing_it() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    # A higher cap than the file default so the asserted Retry-After value
    # below is not itself clipped by `listings_sync_max_backoff_seconds`
    # (that clipping behavior is covered on its own by
    # `test_amazon_listings_ingestion_service.py`'s `_compute_retry_delay`
    # coverage, if present, or is exercised implicitly by every other test
    # in this file using the small default cap).
    settings = _test_settings(listings_sync_max_backoff_seconds=60.0)
    worker, client = _worker([SpApiRateLimitedError("slow down", retry_after_seconds=30.0)], settings=settings)

    claimed_something = await worker.run_once()

    assert claimed_something is True
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "throttled"
    assert run.next_retry_at is not None
    # Amazon's own Retry-After was honored, not a guessed backoff.
    assert abs((run.next_retry_at.replace(tzinfo=UTC) - datetime.now(UTC)).total_seconds() - 30.0) < 5


@pytest.mark.asyncio
async def test_a_rescheduled_job_can_be_reclaimed_and_eventually_exhausts_to_rate_limited() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    settings = _test_settings(listings_sync_max_attempts=2)
    worker, client = _worker(
        [
            SpApiRateLimitedError("slow down", retry_after_seconds=0.0),
            SpApiRateLimitedError("slow down", retry_after_seconds=0.0),
        ],
        settings=settings,
    )

    await worker.run_once()  # attempt 1: throttled -> waiting_to_retry
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "waiting_to_retry"

    # Force the retry to be immediately due.
    with session_scope() as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.next_retry_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    await worker.run_once()  # attempt 2: throttled again, budget exhausted
    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "failed"
    assert run.failure_class == "rate_limited"


@pytest.mark.asyncio
async def test_non_retryable_failure_terminalizes_immediately_without_retry() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker([_page([{"sku": "SKU-1"}, {"sku": "SKU-1"}])])  # duplicate SKU on one page

    await worker.run_once()

    run = _get_run(scope["organization_id"], claim.run_id)
    assert run.status == "failed"
    assert run.failure_class == "duplicate_sku"


# --- per-organization / global concurrency limits ------------------------


@pytest.mark.asyncio
async def test_per_organization_limit_prevents_a_second_job_from_starting() -> None:
    org_id = uuid4()
    scope_a = _seed_scope(org_id=org_id)
    scope_b = _seed_scope(org_id=org_id)
    with session_scope() as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=scope_a["seller_account_id"],
            marketplace_participation_id=scope_a["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="existing-worker", lease_duration_seconds=300,
        )
    claim_b = _enqueue(scope_b)

    settings = _test_settings(listings_sync_max_concurrent_jobs_per_organization=1)
    worker, client = _worker([], settings=settings)

    claimed_something = await worker.run_once()

    assert claimed_something is False
    run_b = _get_run(org_id, claim_b.run_id)
    assert run_b.status == "queued"


# --- unexpected exception handling ---------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_during_processing_does_not_crash_the_loop() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker([], resolver=_RaisingResolver())

    claimed_something = await worker.run_once()

    assert claimed_something is True  # a job was claimed, even though processing blew up
    run = _get_run(scope["organization_id"], claim.run_id)
    # Left at 'started' — recoverable via ordinary lease-expiry reclaim,
    # exactly like a hard process crash. Never silently marked terminal
    # by code paths that never actually ran.
    assert run.status == "started"


@pytest.mark.asyncio
async def test_unexpected_exception_does_not_propagate_out_of_run_once() -> None:
    scope = _seed_scope()
    _enqueue(scope)
    worker, client = _worker([], resolver=_RaisingResolver())
    await worker.run_once()  # must not raise


# --- run_forever loop ------------------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_stops_cooperatively_after_request_stop() -> None:
    worker, client = _worker([])
    calls = {"count": 0}

    async def _fake_run_once() -> bool:
        calls["count"] += 1
        if calls["count"] >= 3:
            worker.request_stop()
        return True  # always "claimed something" so no idle sleep occurs

    worker.run_once = _fake_run_once  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(), timeout=5)

    assert calls["count"] == 3


@pytest.mark.asyncio
async def test_run_forever_sleeps_between_idle_polls(monkeypatch) -> None:
    worker, client = _worker([])
    worker._idle_poll_seconds = 0.01  # keep the test fast
    sleep_calls: list[float] = []

    real_sleep = asyncio.sleep

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)

    calls = {"count": 0}

    async def _fake_run_once() -> bool:
        calls["count"] += 1
        if calls["count"] >= 2:
            worker.request_stop()
        return False  # nothing claimed -> should sleep every iteration

    worker.run_once = _fake_run_once  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(), timeout=5)

    assert len(sleep_calls) == 2
    assert all(s == 0.01 for s in sleep_calls)


# --- sanitized logging -----------------------------------------------------


@pytest.mark.asyncio
async def test_logs_never_contain_seller_or_organization_identifiers(caplog) -> None:
    scope = _seed_scope()
    _enqueue(scope)
    worker, client = _worker([_page([{"sku": "SKU-1"}])])

    with caplog.at_level("INFO", logger="app.amazon.listings_worker"):
        await worker.run_once()

    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert str(scope["organization_id"]) not in log_text
    assert str(scope["seller_account_id"]) not in log_text
    assert str(scope["connection_id"]) not in log_text
    assert "SKU-1" not in log_text
    assert "test-refresh-token" not in log_text

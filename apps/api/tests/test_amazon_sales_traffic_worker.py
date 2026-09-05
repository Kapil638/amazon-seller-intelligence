"""12B.6A — `SalesTrafficWorker` (app.amazon.sales_traffic_worker). No live
Amazon call: the Reports client is fully faked, exactly like
`test_amazon_sales_traffic_ingestion.py`. This file tests only the
worker's own polling/claim/loop/signal/gate responsibilities; report
lifecycle, persistence, and finalization are already covered exhaustively
there and are not re-tested here. `claim_next_sales_traffic_job`'s real
PostgreSQL `SKIP LOCKED`/advisory-lock concurrency would need its own
guarded disposable-Postgres test — out of scope for this pass given no
live-migration/live-Postgres action is authorized in this milestone
(mirrors `test_amazon_orders_worker.py`'s own identical scoping note).
"""

from __future__ import annotations

import asyncio
import os
import signal
from datetime import date
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError

from app.amazon.reports_client import ReportStatus
from app.amazon.sales_traffic_ingestion import AmazonSalesTrafficIngestionService
from app.amazon.sales_traffic_worker import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_DISABLED,
    EXIT_OK,
    SalesTrafficWorker,
    _install_shutdown_signal_handlers,
    is_worker_enabled,
    main,
)
from app.core.config import Settings
from app.core.exceptions import SpApiRateLimitedError
from app.persistence.database import session_scope
from app.persistence.models import AmazonIngestionRun
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


class _FakeReportsClient:
    def __init__(self, *, create_report_script: list | None = None, get_report_script: list | None = None) -> None:
        self._create_report_script = list(create_report_script or [])
        self._get_report_script = list(get_report_script or [])
        self.create_report_calls: list = []

    async def create_report(self, request):
        self.create_report_calls.append(request)
        item = self._create_report_script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get_report(self, report_id: str) -> ReportStatus:
        item = self._get_report_script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _test_settings(**overrides) -> Settings:
    fields = dict(
        sp_api_lwa_client_id=SecretStr("test-sandbox-lwa-client-id-DO-NOT-USE"),
        sp_api_lwa_client_secret=SecretStr("test-sandbox-lwa-client-secret-DO-NOT-USE"),
        sp_api_production_lwa_client_id=SecretStr("test-production-lwa-client-id-DO-NOT-USE"),
        sp_api_production_lwa_client_secret=SecretStr("test-production-lwa-client-secret-DO-NOT-USE"),
        sales_traffic_sync_lease_duration_seconds=300,
        sales_traffic_sync_max_global_concurrent_jobs=10,
        sales_traffic_sync_max_concurrent_jobs_per_organization=10,
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _worker(
    *, create_report_script: list | None = None, get_report_script: list | None = None,
    settings: Settings | None = None, resolver=None,
) -> tuple[SalesTrafficWorker, _FakeReportsClient]:
    client = _FakeReportsClient(create_report_script=create_report_script, get_report_script=get_report_script)

    def factory(**_kwargs):
        return client

    cfg = settings or _test_settings()
    ingestion_service = AmazonSalesTrafficIngestionService(
        settings=cfg, resolver=resolver or _FakeResolver(), reports_client_factory=factory,
    )
    worker = SalesTrafficWorker(settings=cfg, ingestion_service=ingestion_service, lease_owner="test-worker")
    return worker, client


def _seed_scope() -> dict:
    org_id = uuid4()
    with session_scope() as session:
        from app.persistence.models import Organization

        session.add(Organization(id=org_id, name="12B.6A Worker Test Org"))
        session.flush()
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
            connection_id=connection.id,
        )
        session.flush()
        return {
            "organization_id": org_id,
            "seller_account_id": seller_account.id,
            "marketplace_participation_id": participation.id,
            "connection_id": connection.id,
        }


def _enqueue(scope: dict, *, day: date | None = None):
    day = day or date(2026, 8, 1)
    with session_scope() as session:
        return AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=scope["organization_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"], data_start_time=day, data_end_time=day,
            date_granularity="DAY", asin_granularity="SKU",
        )


def _get_run(run_id):
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


# --- run_once: nothing to do ------------------------------------------------


@pytest.mark.asyncio
async def test_run_once_returns_false_when_nothing_is_queued() -> None:
    worker, client = _worker()
    claimed_something = await worker.run_once()
    assert claimed_something is False
    assert client.create_report_calls == []


# --- run_once: successful claim + createReport -----------------------------


@pytest.mark.asyncio
async def test_run_once_claims_and_creates_a_report_for_a_queued_job() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker(create_report_script=[("amzn-report-1", 1)])

    claimed_something = await worker.run_once()

    assert claimed_something is True
    assert len(client.create_report_calls) == 1
    run = _get_run(claim.run_id)
    assert run.status == "started"
    assert run.report_id == "amzn-report-1"


@pytest.mark.asyncio
async def test_run_once_processes_one_job_at_a_time_across_calls() -> None:
    scope_a = _seed_scope()
    scope_b = _seed_scope()
    claim_a = _enqueue(scope_a)
    claim_b = _enqueue(scope_b)
    worker, client = _worker(create_report_script=[("amzn-report-a", 1), ("amzn-report-b", 1)])

    first = await worker.run_once()
    second = await worker.run_once()
    third = await worker.run_once()

    assert (first, second, third) == (True, True, False)
    # Claim order between two unrelated scopes is not guaranteed (ties on
    # created_at fall back to id, a random UUID) — only that each queued
    # job was claimed exactly once and got a distinct report id.
    assert {_get_run(claim_a.run_id).report_id, _get_run(claim_b.run_id).report_id} == {
        "amzn-report-a", "amzn-report-b",
    }


# --- run_once: throttled reschedule ------------------------------------------


@pytest.mark.asyncio
async def test_run_once_reschedules_a_throttled_job_instead_of_failing_it() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker(create_report_script=[SpApiRateLimitedError("slow down", retry_after_seconds=30.0)])

    claimed_something = await worker.run_once()

    assert claimed_something is True
    run = _get_run(claim.run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "throttled_or_transient"


@pytest.mark.asyncio
async def test_non_terminal_processing_status_reschedules_without_failing() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    with session_scope() as session:
        session.get(AmazonIngestionRun, claim.run_id).report_id = "amzn-report-1"
        session.flush()
    worker, client = _worker(get_report_script=[ReportStatus("amzn-report-1", "IN_PROGRESS", None)])

    claimed_something = await worker.run_once()

    assert claimed_something is True
    run = _get_run(claim.run_id)
    assert run.status == "waiting_to_retry"
    assert run.failure_class == "polling"


@pytest.mark.asyncio
async def test_non_retryable_failure_terminalizes_immediately_without_retry() -> None:
    from app.core.exceptions import SpApiInvalidRequestError

    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker(create_report_script=[SpApiInvalidRequestError("bad request")])

    await worker.run_once()

    run = _get_run(claim.run_id)
    assert run.status == "failed"
    assert run.failure_class == "invalid_request"


# --- per-organization concurrency limit -------------------------------------


@pytest.mark.asyncio
async def test_per_organization_limit_prevents_a_second_job_from_starting() -> None:
    scope_a = _seed_scope()
    org_id = scope_a["organization_id"]
    scope_b = dict(scope_a)
    with session_scope() as session:
        seller_account_b = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        session.flush()
        participation_b = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account_b.id, marketplace_id="A1PA6795UKMFR9",
            region="na", connection_id=scope_a["connection_id"],
        )
        session.flush()
        scope_b["seller_account_id"] = seller_account_b.id
        scope_b["marketplace_participation_id"] = participation_b.id

    claim_a = _enqueue(scope_a)
    with session_scope() as session:
        AmazonIngestionRunRepository(session).claim_next_sales_traffic_job(
            lease_owner="existing-worker", lease_duration_seconds=300, max_global_active=10,
            max_active_per_organization=10,
        )
    claim_b = _enqueue(scope_b)

    settings = _test_settings(sales_traffic_sync_max_concurrent_jobs_per_organization=1)
    worker, client = _worker(settings=settings)

    claimed_something = await worker.run_once()

    assert claimed_something is False
    assert _get_run(claim_b.run_id).status == "queued"


# --- unexpected exception handling -------------------------------------------


@pytest.mark.asyncio
async def test_unexpected_exception_during_processing_does_not_crash_the_loop() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    worker, client = _worker(resolver=_RaisingResolver())

    claimed_something = await worker.run_once()

    assert claimed_something is True
    run = _get_run(claim.run_id)
    assert run.status == "started"  # recoverable via ordinary lease-expiry reclaim


# --- graceful shutdown --------------------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_stops_cooperatively_after_request_stop() -> None:
    worker, client = _worker(settings=_test_settings())
    worker._idle_poll_seconds = 0.02
    task = asyncio.create_task(worker.run_forever())
    await asyncio.sleep(0.05)
    worker.request_stop()
    await asyncio.wait_for(task, timeout=2.0)
    assert task.done()


@pytest.mark.asyncio
async def test_sigint_and_sigterm_request_graceful_stop() -> None:
    worker, _ = _worker()
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


# --- ASI_SALES_TRAFFIC_WORKER_ENABLED fail-closed gate -----------------------


@pytest.mark.parametrize("value", ["", "0", "false", "False", "garbage"])
def test_main_refuses_to_start_when_not_explicitly_enabled(monkeypatch, value) -> None:
    monkeypatch.setenv("ASI_SALES_TRAFFIC_WORKER_ENABLED", value)
    assert is_worker_enabled() is False
    assert main() == EXIT_DISABLED


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True"])
def test_is_worker_enabled_accepts_documented_true_values(monkeypatch, value) -> None:
    monkeypatch.setenv("ASI_SALES_TRAFFIC_WORKER_ENABLED", value)
    assert is_worker_enabled() is True


def test_worker_enabled_env_var_is_independent_of_orders_and_listings() -> None:
    import app.amazon.listings_worker as listings_worker_module
    import app.amazon.orders_worker as orders_worker_module
    import app.amazon.sales_traffic_worker as sales_traffic_worker_module

    env_vars = {
        listings_worker_module._WORKER_ENABLED_ENV_VAR,
        orders_worker_module._WORKER_ENABLED_ENV_VAR,
        sales_traffic_worker_module._WORKER_ENABLED_ENV_VAR,
    }
    assert len(env_vars) == 3


# --- final safety/bounded-evidence review: production-database guard --------
# --- runtime-context declaration --------------------------------------------


def test_disabled_worker_never_declares_a_db_runtime_context(monkeypatch) -> None:
    monkeypatch.delenv("ASI_SALES_TRAFFIC_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("ASI_DB_RUNTIME_CONTEXT", raising=False)
    exit_code = main()
    assert exit_code == EXIT_DISABLED
    assert os.environ.get("ASI_DB_RUNTIME_CONTEXT") is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_enabled_worker_declares_the_sales_traffic_worker_db_runtime_context(monkeypatch, value) -> None:
    monkeypatch.setenv("ASI_SALES_TRAFFIC_WORKER_ENABLED", value)
    monkeypatch.delenv("ASI_DB_RUNTIME_CONTEXT", raising=False)

    def _raise_invalid(*_args, **_kwargs):
        raise ValidationError.from_exception_data("Settings", [])

    monkeypatch.setattr("app.amazon.sales_traffic_worker.get_settings", _raise_invalid)
    try:
        exit_code = main()
        assert exit_code == EXIT_CONFIGURATION_ERROR
        assert os.environ.get("ASI_DB_RUNTIME_CONTEXT") == "sales_traffic_worker"
    finally:
        os.environ.pop("ASI_DB_RUNTIME_CONTEXT", None)


# --- config validation --------------------------------------------------------


def test_worker_poll_error_backoff_rejects_base_exceeding_max() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        _test_settings(
            sales_traffic_worker_poll_error_base_backoff_seconds=10.0,
            sales_traffic_worker_poll_error_max_backoff_seconds=5.0,
        )

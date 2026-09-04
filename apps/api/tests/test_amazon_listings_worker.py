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
import os
import signal
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy.exc import OperationalError

from app.amazon.listings_client import ListingsPageRequest
from app.amazon.listings_ingestion import AmazonListingsIngestionService
from app.amazon.listings_models import Item, ListingsPage, ListingsPageProvenance
from app.amazon.listings_worker import (
    EXIT_CONFIGURATION_ERROR,
    EXIT_DISABLED,
    EXIT_OK,
    ListingsWorker,
    _install_shutdown_signal_handlers,
    main,
)
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


# --- 12B.3H: worker poll-error backoff configuration validation -----------


def test_worker_poll_error_backoff_rejects_a_negative_or_zero_base() -> None:
    with pytest.raises(ValidationError):
        _test_settings(listings_worker_poll_error_base_backoff_seconds=0.0)
    with pytest.raises(ValidationError):
        _test_settings(listings_worker_poll_error_base_backoff_seconds=-1.0)


def test_worker_poll_error_backoff_rejects_a_negative_or_zero_max() -> None:
    with pytest.raises(ValidationError):
        _test_settings(listings_worker_poll_error_max_backoff_seconds=0.0)
    with pytest.raises(ValidationError):
        _test_settings(listings_worker_poll_error_max_backoff_seconds=-1.0)


def test_worker_poll_error_backoff_rejects_base_exceeding_max() -> None:
    with pytest.raises(ValidationError, match="must not exceed"):
        _test_settings(
            listings_worker_poll_error_base_backoff_seconds=100.0,
            listings_worker_poll_error_max_backoff_seconds=10.0,
        )


def test_worker_poll_error_backoff_accepts_base_equal_to_max() -> None:
    # Equal is a valid, if degenerate, configuration (no doubling ever
    # matters since the first failure is already at the cap) — only a
    # base that *exceeds* the cap is an actual inversion worth rejecting.
    settings = _test_settings(
        listings_worker_poll_error_base_backoff_seconds=5.0,
        listings_worker_poll_error_max_backoff_seconds=5.0,
    )
    assert settings.listings_worker_poll_error_base_backoff_seconds == 5.0


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


# --- 12B.3H: poll-error backoff --------------------------------------------


@pytest.mark.asyncio
async def test_run_forever_backs_off_after_a_recoverable_poll_error_and_resets_on_success(monkeypatch) -> None:
    worker, client = _worker([])
    worker._idle_poll_seconds = 0.01
    worker._poll_error_base_backoff_seconds = 0.05
    worker._poll_error_max_backoff_seconds = 10.0
    worker._current_poll_error_backoff_seconds = worker._poll_error_base_backoff_seconds
    sleep_calls: list[float] = []

    real_sleep = asyncio.sleep

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)

    calls = {"count": 0}

    async def _fake_run_once() -> bool:
        calls["count"] += 1
        if calls["count"] <= 2:
            raise OperationalError("simulated statement", {}, Exception("simulated database connectivity failure"))
        if calls["count"] >= 4:
            worker.request_stop()
        return False  # a *successful* poll that simply found no job

    worker.run_once = _fake_run_once  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(), timeout=5)

    # Two failures back off with doubling (0.05, then 0.10); the third
    # call succeeds and sleeps the normal idle interval, proving the
    # error backoff was reset rather than carried forward.
    assert sleep_calls[0] == pytest.approx(0.05)
    assert sleep_calls[1] == pytest.approx(0.10)
    assert sleep_calls[2] == pytest.approx(0.01)
    assert worker._current_poll_error_backoff_seconds == pytest.approx(0.05)


@pytest.mark.asyncio
async def test_run_forever_poll_error_backoff_is_capped(monkeypatch) -> None:
    worker, client = _worker([])
    worker._poll_error_base_backoff_seconds = 0.01
    worker._poll_error_max_backoff_seconds = 0.03
    worker._current_poll_error_backoff_seconds = worker._poll_error_base_backoff_seconds
    sleep_calls: list[float] = []

    real_sleep = asyncio.sleep

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)

    calls = {"count": 0}

    async def _fake_run_once() -> bool:
        calls["count"] += 1
        if calls["count"] >= 6:
            worker.request_stop()
            return False
        raise OperationalError("simulated statement", {}, Exception("simulated persistent recoverable failure"))

    worker.run_once = _fake_run_once  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(), timeout=5)

    # 0.01, 0.02, then capped at 0.03 for every subsequent failure —
    # never a busy loop (no zero/near-zero delay) and never unbounded.
    assert sleep_calls[0] == pytest.approx(0.01)
    assert sleep_calls[1] == pytest.approx(0.02)
    assert all(s == pytest.approx(0.03) for s in sleep_calls[2:-1])


@pytest.mark.asyncio
async def test_a_job_processing_exception_never_triggers_poll_error_backoff(monkeypatch) -> None:
    """A defect inside job *processing* (already handled by `run_once`
    itself, which returns True rather than raising) must never be
    mistaken for a poll/claim-step failure — the two are deliberately
    different concerns with different recovery semantics."""
    scope = _seed_scope()
    _enqueue(scope)
    worker, client = _worker([], resolver=_RaisingResolver())
    sleep_calls: list[float] = []

    async def _tracking_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", _tracking_sleep)
    claimed_something = await worker.run_once()

    assert claimed_something is True
    assert sleep_calls == []  # no backoff sleep — this was not a poll-step failure


@pytest.mark.asyncio
async def test_a_genuine_programming_error_in_the_claim_step_is_not_swallowed() -> None:
    """`run_forever`'s poll-error backoff must only catch plausibly
    recoverable database/transport failures — a real defect (`TypeError`,
    `AttributeError`, an invariant violation) has to propagate and crash
    the process so a supervisor restarts it and an operator notices,
    never be silently retried forever behind "recoverable backoff"."""
    worker, client = _worker([])

    async def _buggy_run_once() -> bool:
        raise TypeError("simulated genuine programming defect, not a connectivity issue")

    worker.run_once = _buggy_run_once  # type: ignore[method-assign]
    with pytest.raises(TypeError, match="simulated genuine programming defect"):
        await asyncio.wait_for(worker.run_forever(), timeout=5)


@pytest.mark.asyncio
async def test_an_attribute_error_in_the_claim_step_is_not_swallowed() -> None:
    worker, client = _worker([])

    async def _buggy_run_once() -> bool:
        raise AttributeError("simulated invariant violation")

    worker.run_once = _buggy_run_once  # type: ignore[method-assign]
    with pytest.raises(AttributeError):
        await asyncio.wait_for(worker.run_forever(), timeout=5)


# --- 12B.3H: graceful shutdown signals ---------------------------------


@pytest.mark.asyncio
async def test_sigint_requests_graceful_stop() -> None:
    worker, _client = _worker([])
    loop = asyncio.get_running_loop()
    _install_shutdown_signal_handlers(worker, loop)
    try:
        os.kill(os.getpid(), signal.SIGINT)
        await asyncio.wait_for(_poll_until(lambda: worker._stop_requested), timeout=2)
        assert worker._stop_requested is True
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)


@pytest.mark.asyncio
async def test_sigterm_requests_graceful_stop() -> None:
    worker, _client = _worker([])
    loop = asyncio.get_running_loop()
    _install_shutdown_signal_handlers(worker, loop)
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(_poll_until(lambda: worker._stop_requested), timeout=2)
        assert worker._stop_requested is True
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)


async def _poll_until(predicate, *, interval: float = 0.01) -> None:
    while not predicate():
        await asyncio.sleep(interval)


@pytest.mark.asyncio
async def test_shutdown_prevents_another_claim() -> None:
    worker, client = _worker([])
    worker.request_stop()  # requested before run_forever ever starts polling
    calls = {"count": 0}

    async def _fake_run_once() -> bool:
        calls["count"] += 1
        return False

    worker.run_once = _fake_run_once  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(), timeout=2)

    assert calls["count"] == 0  # the loop condition is checked before any claim attempt


@pytest.mark.asyncio
async def test_no_task_survives_run_forever_after_shutdown() -> None:
    worker, client = _worker([])
    worker._idle_poll_seconds = 0.01
    before = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}

    async def _fake_run_once() -> bool:
        worker.request_stop()
        return False

    worker.run_once = _fake_run_once  # type: ignore[method-assign]
    await asyncio.wait_for(worker.run_forever(), timeout=2)

    after = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
    assert after - before == set(), "run_forever left a background task running after shutdown"


# --- 12B.3H: fail-closed configuration errors --------------------------


def test_main_exits_with_configuration_error_code_and_never_raises(monkeypatch) -> None:
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", "true")

    def _raise_invalid(*_args, **_kwargs):
        raise ValidationError.from_exception_data("Settings", [])

    monkeypatch.setattr("app.amazon.listings_worker.get_settings", _raise_invalid)
    exit_code = main()
    assert exit_code == EXIT_CONFIGURATION_ERROR
    assert EXIT_CONFIGURATION_ERROR != EXIT_OK


def test_configuration_error_log_never_echoes_the_validation_error_text(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", "true")

    def _raise_invalid(*_args, **_kwargs):
        raise ValidationError.from_exception_data(
            "Settings", [{"type": "missing", "loc": ("sp_api_super_secret_field",), "input": "SECRET-LOOKING-VALUE"}]
        )

    monkeypatch.setattr("app.amazon.listings_worker.get_settings", _raise_invalid)
    with caplog.at_level("ERROR", logger="app.amazon.listings_worker"):
        main()
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "SECRET-LOOKING-VALUE" not in log_text


# --- 12B.3H: explicit worker-enable authorization gate ---------------------


@pytest.mark.parametrize("value", [None, "", "false", "0", "no", "TRU"])
def test_main_refuses_to_start_when_not_explicitly_enabled(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("ASI_LISTINGS_WORKER_ENABLED", raising=False)
    else:
        monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", value)
    settings_calls = {"count": 0}

    def _tracking_get_settings(*_args, **_kwargs):
        settings_calls["count"] += 1
        return _test_settings()

    monkeypatch.setattr("app.amazon.listings_worker.get_settings", _tracking_get_settings)
    exit_code = main()
    assert exit_code == EXIT_DISABLED
    assert exit_code not in (EXIT_OK, EXIT_CONFIGURATION_ERROR)
    # Fail closed *before* touching configuration at all — a disabled
    # worker must never resolve Settings, let alone the database.
    assert settings_calls["count"] == 0


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "True", " true ", "1 "])
def test_main_proceeds_past_the_gate_when_explicitly_enabled(monkeypatch, value) -> None:
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", value)

    def _raise_invalid(*_args, **_kwargs):
        # Stops execution at the very next step (configuration) rather
        # than actually starting the full asyncio loop — this test only
        # needs to prove the gate did not block, not run the worker.
        raise ValidationError.from_exception_data("Settings", [])

    monkeypatch.setattr("app.amazon.listings_worker.get_settings", _raise_invalid)
    exit_code = main()
    assert exit_code == EXIT_CONFIGURATION_ERROR  # reached past the gate


# --- final safety/bounded-evidence review: production-database guard -------
# --- runtime-context declaration ---------------------------------------


def test_disabled_worker_never_declares_a_db_runtime_context(monkeypatch) -> None:
    """`app.persistence.database`'s production-database guard is
    authorized per-process by `ASI_DB_RUNTIME_CONTEXT` — a disabled
    worker must never set it, matching "fail closed before touching
    configuration at all" for the database guard's authorization
    surface too, not just for Settings."""
    monkeypatch.delenv("ASI_LISTINGS_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("ASI_DB_RUNTIME_CONTEXT", raising=False)
    exit_code = main()
    assert exit_code == EXIT_DISABLED
    assert os.environ.get("ASI_DB_RUNTIME_CONTEXT") is None


@pytest.mark.parametrize("value", ["1", "true", "TRUE"])
def test_enabled_worker_declares_the_listings_worker_db_runtime_context(monkeypatch, value) -> None:
    """Proves the real `main()` sets `ASI_DB_RUNTIME_CONTEXT=listings_
    worker` — the exact declaration `app.persistence.database`'s guard
    checks — after its own explicit enable gate has passed, and before
    it reaches Settings/the asyncio loop. Stops at a forced Settings
    failure (the same technique `test_main_proceeds_past_the_gate_when_
    explicitly_enabled` uses) so this never spins up the real worker
    loop."""
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", value)
    monkeypatch.delenv("ASI_DB_RUNTIME_CONTEXT", raising=False)

    def _raise_invalid(*_args, **_kwargs):
        raise ValidationError.from_exception_data("Settings", [])

    monkeypatch.setattr("app.amazon.listings_worker.get_settings", _raise_invalid)
    try:
        exit_code = main()
        assert exit_code == EXIT_CONFIGURATION_ERROR
        assert os.environ.get("ASI_DB_RUNTIME_CONTEXT") == "listings_worker"
    finally:
        # `main()` sets this directly via `os.environ[...] = ...`, not
        # via `monkeypatch` — it must be cleaned up explicitly here or
        # it would otherwise leak into every later test in the suite.
        os.environ.pop("ASI_DB_RUNTIME_CONTEXT", None)


def test_disabled_worker_log_never_leaks_the_env_var_value(monkeypatch, caplog) -> None:
    monkeypatch.setenv("ASI_LISTINGS_WORKER_ENABLED", "definitely-not-a-real-token-but-should-still-never-appear")
    with caplog.at_level("ERROR", logger="app.amazon.listings_worker"):
        main()
    log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "definitely-not-a-real-token-but-should-still-never-appear" not in log_text


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

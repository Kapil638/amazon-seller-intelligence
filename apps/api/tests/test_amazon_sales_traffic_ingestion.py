"""12B.6A — AmazonSalesTrafficIngestionService. No live Amazon call: the
Reports client is fully faked via `reports_client_factory` (its actual
HTTP/parsing behavior is already covered by `test_amazon_reports_client.
py`). Uses the shared, per-test-isolated SQLite database, matching
`test_amazon_orders_ingestion_service.py`'s established pattern.

Covers the lifecycle `sales_traffic_ingestion.py`'s own docstring
describes: durable `report_id` recording before anything else after
`createReport`, durable-polling-without-holding-a-slot (release to
`waiting_to_retry` rather than an in-process poll loop), every terminal
authorization/parse/cancellation outcome, and atomic persistence +
checkpoint advancement scoped to the exact requested window.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import SecretStr
from sqlalchemy.exc import IntegrityError

from app.amazon.reports_client import (
    CreateSalesAndTrafficReportRequest,
    ReportDocumentInfo,
    ReportStatus,
)
from app.amazon.sales_traffic_ingestion import (
    AmazonSalesTrafficIngestionService,
    SalesTrafficIngestionOutcome,
)
from app.amazon.sales_traffic_models import SalesAndTrafficReport
from app.amazon.secrets import SecretAccessError, SecretNotFoundError
from app.core.config import Settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
)
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSalesAndTrafficDailyFact, AmazonSalesAndTrafficProductFact
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSalesTrafficSyncCheckpointRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


# --- fakes ---------------------------------------------------------------


class _FakeResolver:
    def __init__(self, token: str = "test-refresh-token", raise_error: Exception | None = None) -> None:
        self._token = token
        self._raise_error = raise_error

    def resolve_refresh_token(self, *, organization_id, connection):
        if self._raise_error is not None:
            raise self._raise_error
        return SecretStr(self._token)


class _FakeReportsClient:
    """Scripted stand-in for `AmazonSpApiReportsClient`. Each of
    `create_report`/`get_report`/`get_report_document`/
    `download_report_document` pops its own next scripted result/exception,
    independently — the ingestion service calls at most one of them per
    `process_claimed_job` invocation, so a per-method queue (rather than one
    shared queue) matches how the real client is actually used."""

    def __init__(
        self,
        *,
        create_report_script: list | None = None,
        get_report_script: list | None = None,
        get_report_document_script: list | None = None,
        download_script: list | None = None,
    ) -> None:
        self._create_report_script = list(create_report_script or [])
        self._get_report_script = list(get_report_script or [])
        self._get_report_document_script = list(get_report_document_script or [])
        self._download_script = list(download_script or [])
        self.create_report_calls: list[CreateSalesAndTrafficReportRequest] = []
        self.get_report_calls: list[str] = []

    async def create_report(self, request: CreateSalesAndTrafficReportRequest):
        self.create_report_calls.append(request)
        item = self._create_report_script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get_report(self, report_id: str) -> ReportStatus:
        self.get_report_calls.append(report_id)
        item = self._get_report_script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def get_report_document(self, report_document_id: str) -> ReportDocumentInfo:
        item = self._get_report_document_script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    async def download_report_document(self, document_info: ReportDocumentInfo) -> SalesAndTrafficReport:
        item = self._download_script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _test_settings(**overrides) -> Settings:
    fields = dict(
        sp_api_lwa_client_id=SecretStr("test-sandbox-lwa-client-id-DO-NOT-USE"),
        sp_api_lwa_client_secret=SecretStr("test-sandbox-lwa-client-secret-DO-NOT-USE"),
        sp_api_production_lwa_client_id=SecretStr("test-production-lwa-client-id-DO-NOT-USE"),
        sp_api_production_lwa_client_secret=SecretStr("test-production-lwa-client-secret-DO-NOT-USE"),
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _service(client: _FakeReportsClient, **kwargs) -> AmazonSalesTrafficIngestionService:
    def factory(**_kwargs):
        return client

    resolver = kwargs.pop("resolver", None) or _FakeResolver()
    settings = kwargs.pop("settings", None) or _test_settings()
    return AmazonSalesTrafficIngestionService(
        settings=settings,
        resolver=resolver,
        reports_client_factory=factory,
        lease_owner_factory=kwargs.pop("lease_owner_factory", None) or (lambda: f"lease-{uuid4().hex[:8]}"),
        **kwargs,
    )


def _seed_scope() -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
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
            "org_id": org_id,
            "seller_account_id": seller_account.id,
            "participation_id": participation.id,
            "connection_id": connection.id,
        }


def _enqueue_and_claim(scope: dict, *, start: date | None = None, end: date | None = None):
    start = start or date(2026, 8, 1)
    end = end or start
    with session_scope() as session:
        AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"], data_start_time=start, data_end_time=end,
            date_granularity="DAY", asin_granularity="SKU",
        )
    with session_scope() as session:
        claimed = AmazonIngestionRunRepository(session).claim_next_sales_traffic_job(
            lease_owner="test-lease", lease_duration_seconds=300, max_global_active=10, max_active_per_organization=10
        )
        return claimed.id


def _get_run(run_id) -> AmazonIngestionRun:
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


def _minimal_report_payload(*, date_str: str = "2026-08-01") -> dict:
    amount = {"amount": "16.79", "currencyCode": "USD"}
    return {
        "reportSpecification": {
            "reportType": "GET_SALES_AND_TRAFFIC_REPORT",
            "dataStartTime": date_str,
            "dataEndTime": date_str,
            "marketplaceIds": [MARKETPLACE],
        },
        "salesAndTrafficByDate": [
            {
                "date": date_str,
                "salesByDate": {
                    "orderedProductSales": amount,
                    "unitsOrdered": 1,
                    "totalOrderItems": 1,
                    "averageSalesPerOrderItem": amount,
                    "averageUnitsPerOrderItem": "1.00",
                    "averageSellingPrice": amount,
                    "unitsRefunded": 0,
                    "refundRate": "0.00",
                    "claimsGranted": 0,
                    "claimsAmount": {"amount": "0.00", "currencyCode": "USD"},
                    "shippedProductSales": amount,
                    "unitsShipped": 1,
                    "ordersShipped": 1,
                },
                "trafficByDate": {
                    "browserPageViews": 10, "mobileAppPageViews": 5, "pageViews": 15,
                    "browserSessions": 8, "mobileAppSessions": 4, "sessions": 12,
                    "buyBoxPercentage": "95.00", "orderItemSessionPercentage": "8.33",
                    "unitSessionPercentage": "8.33", "averageOfferCount": 1, "averageParentItems": 1,
                    "feedbackReceived": 0, "negativeFeedbackReceived": 0, "receivedNegativeFeedbackRate": "0.00",
                },
            }
        ],
        "salesAndTrafficByAsin": [
            {
                "parentAsin": "B123456789",
                "childAsin": "B123456789",
                "sku": "SKU-1",
                "salesByAsin": {"unitsOrdered": 1, "orderedProductSales": amount, "totalOrderItems": 1},
                "trafficByAsin": {
                    "browserSessions": 8, "mobileAppSessions": 4, "sessions": 12,
                    "browserSessionPercentage": "66.67", "mobileAppSessionPercentage": "33.33",
                    "sessionPercentage": "100.00", "browserPageViews": 10, "mobileAppPageViews": 5,
                    "pageViews": 15, "browserPageViewsPercentage": "66.67", "mobileAppPageViewsPercentage": "33.33",
                    "pageViewsPercentage": "100.00", "buyBoxPercentage": "95.00", "unitSessionPercentage": "8.33",
                },
            }
        ],
    }


def _report(*, date_str: str = "2026-08-01") -> SalesAndTrafficReport:
    return SalesAndTrafficReport.model_validate(_minimal_report_payload(date_str=date_str))


# --- createReport durably recorded before anything else -------------------


@pytest.mark.asyncio
async def test_process_claimed_job_creates_report_and_heartbeats_report_id_without_polling() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeReportsClient(create_report_script=[("amzn-report-1", 1)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome == SalesTrafficIngestionOutcome(run_id=run_id, outcome="created")
    assert client.get_report_calls == []  # never polls in the same attempt it created the report
    run = _get_run(run_id)
    assert run.status == "started"  # lease retained — not released to waiting_to_retry
    assert run.report_id == "amzn-report-1"
    assert run.report_processing_status == "IN_QUEUE"


@pytest.mark.asyncio
async def test_process_claimed_job_skips_create_report_when_report_id_already_recorded() -> None:
    """Once `report_id` has been *durably recorded* on the run row (this
    test sets it directly, simulating a worker restarting after an
    earlier attempt's heartbeat committed it), a later attempt must never
    call `createReport` again — the scarce three-per-five-minutes budget
    this report type shares across every legitimate use for the seller.
    This does NOT cover the narrower crash window between `createReport`
    returning successfully and that heartbeat's own commit — a crash in
    that specific gap is a genuine, accepted at-least-once request
    boundary (see `sales_traffic_ingestion.py`'s own module docstring and
    handover doc §11), not something this test claims to close."""
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, run_id)
        run.report_id = "amzn-report-existing"
        session.flush()

    client = _FakeReportsClient(get_report_script=[ReportStatus("amzn-report-existing", "IN_PROGRESS", None)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert client.create_report_calls == []
    assert client.get_report_calls == ["amzn-report-existing"]
    assert outcome.outcome == "rescheduled"
    assert outcome.reason == "polling"


# --- durable polling: release the claim, never busy-poll -------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["IN_QUEUE", "IN_PROGRESS"])
async def test_non_terminal_status_reschedules_and_releases_the_claim(status: str) -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()
    client = _FakeReportsClient(get_report_script=[ReportStatus("amzn-report-1", status, None)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "rescheduled"
    assert outcome.reason == "polling"
    run = _get_run(run_id)
    assert run.status == "waiting_to_retry"
    assert run.lease_owner is None  # claim released — a different worker may pick this up next
    assert run.next_retry_at is not None
    assert run.report_processing_status == status


# --- terminal report outcomes: CANCELLED / FATAL ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected_failure_class", [("CANCELLED", "report_cancelled"), ("FATAL", "report_fatal")])
async def test_cancelled_or_fatal_report_terminalizes_as_failed(status: str, expected_failure_class: str) -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()
    client = _FakeReportsClient(get_report_script=[ReportStatus("amzn-report-1", status, None)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == expected_failure_class
    run = _get_run(run_id)
    assert run.status == "failed"
    assert run.failure_class == expected_failure_class


# --- authorization / invalid-request / parse failures terminalize ---------


@pytest.mark.asyncio
async def test_authentication_failure_terminalizes_never_retries() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeReportsClient(create_report_script=[SpApiAuthenticationError("missing Brand Analytics role")])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "authentication_failed"
    assert _get_run(run_id).status == "failed"


@pytest.mark.asyncio
async def test_invalid_request_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeReportsClient(create_report_script=[SpApiInvalidRequestError("bad request")])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "invalid_request"


@pytest.mark.asyncio
async def test_malformed_report_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()
    client = _FakeReportsClient(
        get_report_script=[ReportStatus("amzn-report-1", "DONE", "doc-1")],
        get_report_document_script=[ReportDocumentInfo(url="https://example.test/doc", compression_algorithm=None)],
        download_script=[SpApiParseFailedError("did not match the pinned contract shape")],
    )
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "malformed_report"


@pytest.mark.asyncio
async def test_rate_limited_reschedules_rather_than_failing() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeReportsClient(create_report_script=[SpApiRateLimitedError("slow down", retry_after_seconds=30.0)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "rescheduled"
    assert outcome.reason == "throttled_or_transient"
    run = _get_run(run_id)
    assert run.status == "waiting_to_retry"


# --- secret resolution failure terminalizes without any network call ------


@pytest.mark.asyncio
async def test_unresolvable_connection_secret_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeReportsClient()
    service = _service(client, resolver=_FakeResolver(raise_error=SecretNotFoundError("gone")))

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "connection_unresolvable"
    assert client.create_report_calls == []


@pytest.mark.asyncio
async def test_secret_access_error_terminalizes() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    client = _FakeReportsClient()
    service = _service(client, resolver=_FakeResolver(raise_error=SecretAccessError("denied")))

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "connection_unresolvable"


# --- DONE: persistence + checkpoint advancement -----------------------------


@pytest.mark.asyncio
async def test_done_persists_facts_and_advances_checkpoint_for_a_single_day_window() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope, start=date(2026, 8, 1), end=date(2026, 8, 1))
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()
    client = _FakeReportsClient(
        get_report_script=[ReportStatus("amzn-report-1", "DONE", "doc-1")],
        get_report_document_script=[ReportDocumentInfo(url="https://example.test/doc", compression_algorithm=None)],
        download_script=[_report(date_str="2026-08-01")],
    )
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "persisted"
    run = _get_run(run_id)
    assert run.status == "succeeded"

    with session_scope() as session:
        daily = session.query(AmazonSalesAndTrafficDailyFact).filter_by(
            marketplace_participation_id=scope["participation_id"], report_date=date(2026, 8, 1)
        ).one()
        assert daily.units_ordered == 1
        assert daily.buy_box_percentage == Decimal("95.0000")

        product = session.query(AmazonSalesAndTrafficProductFact).filter_by(
            marketplace_participation_id=scope["participation_id"], seller_sku="SKU-1"
        ).one()
        assert product.parent_asin == "B123456789"
        assert product.request_window_start == date(2026, 8, 1)
        assert product.request_window_end == date(2026, 8, 1)

        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"]
        )
        assert checkpoint is not None
        assert checkpoint.synced_through_date == date(2026, 8, 1)


@pytest.mark.asyncio
async def test_done_persists_but_never_advances_checkpoint_for_a_wider_window() -> None:
    """A catalog-wide trend request (e.g. a 30-day window) still persists
    its facts and still marks the run succeeded, but must never move the
    incremental *daily* checkpoint — that checkpoint's entire purpose is
    "how far has daily product-level ingestion progressed", not "was any
    report ever successfully requested"."""
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope, start=date(2026, 8, 1), end=date(2026, 8, 30))
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()
    client = _FakeReportsClient(
        get_report_script=[ReportStatus("amzn-report-1", "DONE", "doc-1")],
        get_report_document_script=[ReportDocumentInfo(url="https://example.test/doc", compression_algorithm=None)],
        download_script=[_report(date_str="2026-08-01")],
    )
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "persisted"
    assert _get_run(run_id).status == "succeeded"
    with session_scope() as session:
        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"]
        )
        assert checkpoint is None or checkpoint.synced_through_date is None


@pytest.mark.asyncio
async def test_persistence_failure_rolls_back_every_row_and_leaves_the_run_claimable() -> None:
    """A malformed report is already rejected at the client's own parse
    step (`test_amazon_reports_client.py`), long before this module ever
    sees it — this proves the *other* half: a genuine database-constraint
    violation partway through persistence (here, a percentage the pinned
    contract itself bounds at 100, corrupted to a value outside it) must
    roll back every row this attempt would have written, including the
    already-persisted daily fact from the *same* transaction, and must
    never silently mark the run succeeded. `process_claimed_job` re-raises
    unexpected exceptions rather than terminalizing them (see its own
    docstring: "programming errors remain visible") — the run's lease is
    simply left to expire and become reclaimable, exactly like the
    worker's identical handling of an unexpected exception."""
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope, start=date(2026, 8, 1), end=date(2026, 8, 1))
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()

    payload = _minimal_report_payload(date_str="2026-08-01")
    payload["salesAndTrafficByDate"][0]["trafficByDate"]["buyBoxPercentage"] = "150.00"
    report = SalesAndTrafficReport.model_validate(payload)

    client = _FakeReportsClient(
        get_report_script=[ReportStatus("amzn-report-1", "DONE", "doc-1")],
        get_report_document_script=[ReportDocumentInfo(url="https://example.test/doc", compression_algorithm=None)],
        download_script=[report],
    )
    service = _service(client)

    with pytest.raises(IntegrityError):
        await service.process_claimed_job(run_id)

    run = _get_run(run_id)
    assert run.status == "started"  # never marked succeeded; lease left to expire naturally
    with session_scope() as session:
        assert (
            session.query(AmazonSalesAndTrafficDailyFact)
            .filter_by(marketplace_participation_id=scope["participation_id"])
            .count()
            == 0
        )
        assert (
            session.query(AmazonSalesAndTrafficProductFact)
            .filter_by(marketplace_participation_id=scope["participation_id"])
            .count()
            == 0
        )


# --- retry attempt / elapsed-time budgets ----------------------------------


@pytest.mark.asyncio
async def test_retry_attempt_budget_exhausted_terminalizes_without_any_network_call() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, run_id)
        run.retry_count = 6  # == MAX_RETRY_ATTEMPTS
        session.flush()

    client = _FakeReportsClient(create_report_script=[RuntimeError("must never be called")])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "retry_budget_exhausted"
    assert client.create_report_calls == []
    run = _get_run(run_id)
    assert run.status == "failed"
    assert run.failure_class == "retry_budget_exhausted"


@pytest.mark.asyncio
async def test_elapsed_time_budget_exhausted_terminalizes_without_any_network_call() -> None:
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, run_id)
        run.created_at = datetime.now(UTC) - timedelta(hours=7)  # past DEFAULT_MAX_TOTAL_RETRY_SECONDS (6h)
        session.flush()

    client = _FakeReportsClient(create_report_script=[RuntimeError("must never be called")])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "failed"
    assert outcome.reason == "retry_budget_exhausted"
    assert client.create_report_calls == []


@pytest.mark.asyncio
async def test_retry_budget_does_not_block_a_run_still_within_bounds() -> None:
    """A run with some retries but still under both budgets must proceed
    normally — the budget check must never be an off-by-one that blocks
    legitimate in-budget attempts."""
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope)
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, run_id)
        run.retry_count = 2
        session.flush()

    client = _FakeReportsClient(create_report_script=[("amzn-report-1", 1)])
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "created"
    assert client.create_report_calls == [
        CreateSalesAndTrafficReportRequest(
            marketplace_id="ATVPDKIKX0DER", data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
        )
    ]


# --- zero-row DONE report -----------------------------------------------


@pytest.mark.asyncio
async def test_zero_row_done_report_advances_checkpoint_without_inventing_facts() -> None:
    """A genuinely empty (no sales, no traffic) day must still let the
    daily checkpoint advance — the run legitimately succeeded at checking
    that day, and re-requesting it forever would never converge — without
    ever fabricating a fact row to represent "nothing happened"."""
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope, start=date(2026, 8, 1), end=date(2026, 8, 1))
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()
    empty_payload = _minimal_report_payload(date_str="2026-08-01")
    empty_payload["salesAndTrafficByDate"] = []
    empty_payload["salesAndTrafficByAsin"] = []
    empty_report = SalesAndTrafficReport.model_validate(empty_payload)

    client = _FakeReportsClient(
        get_report_script=[ReportStatus("amzn-report-1", "DONE", "doc-1")],
        get_report_document_script=[ReportDocumentInfo(url="https://example.test/doc", compression_algorithm=None)],
        download_script=[empty_report],
    )
    service = _service(client)

    outcome = await service.process_claimed_job(run_id)

    assert outcome.outcome == "persisted"
    run = _get_run(run_id)
    assert run.status == "succeeded"
    with session_scope() as session:
        assert (
            session.query(AmazonSalesAndTrafficDailyFact)
            .filter_by(marketplace_participation_id=scope["participation_id"])
            .count()
            == 0
        )
        assert (
            session.query(AmazonSalesAndTrafficProductFact)
            .filter_by(marketplace_participation_id=scope["participation_id"])
            .count()
            == 0
        )
        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"]
        )
        assert checkpoint is not None
        assert checkpoint.synced_through_date == date(2026, 8, 1)


# --- stale lease cannot finalize -------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_worker_that_lost_its_lease_cannot_finalize_or_persist() -> None:
    """Simulates a worker whose lease was reassigned to a replacement
    worker (e.g. after a long GC pause or network partition mistaken for
    a crash) *during* the download step — after `_load_claim` has already
    captured this attempt's own `lease_owner`, but before persistence
    starts. The original worker's `process_claimed_job` call must fail
    loudly (never silently succeed under an ownership it no longer
    holds), and must never leave partially-persisted facts behind.

    Mutating the run's `lease_owner` *before* calling `process_claimed_
    job` would not actually test this: `_load_claim` simply re-reads
    whatever `lease_owner` is currently on the row as its own identity
    for the rest of the call, so the two would trivially match. The real
    race is one where the row changes *after* `_load_claim` already ran
    — modeled here as a side effect of the awaited `download_report_
    document` call, the last network step before persistence."""
    scope = _seed_scope()
    run_id = _enqueue_and_claim(scope, start=date(2026, 8, 1), end=date(2026, 8, 1))
    with session_scope() as session:
        session.get(AmazonIngestionRun, run_id).report_id = "amzn-report-1"
        session.flush()

    class _LeaseStealingClient(_FakeReportsClient):
        async def download_report_document(self, document_info: ReportDocumentInfo) -> SalesAndTrafficReport:
            with session_scope() as session:
                run = session.get(AmazonIngestionRun, run_id)
                run.lease_owner = "a-different-worker-that-stole-the-lease"
                session.flush()
            return await super().download_report_document(document_info)

    client = _LeaseStealingClient(
        get_report_script=[ReportStatus("amzn-report-1", "DONE", "doc-1")],
        get_report_document_script=[ReportDocumentInfo(url="https://example.test/doc", compression_algorithm=None)],
        download_script=[_report(date_str="2026-08-01")],
    )
    service = _service(client)

    with pytest.raises(RuntimeError, match="lease was lost"):
        await service.process_claimed_job(run_id)

    with session_scope() as session:
        assert (
            session.query(AmazonSalesAndTrafficDailyFact)
            .filter_by(marketplace_participation_id=scope["participation_id"])
            .count()
            == 0
        )
        run = session.get(AmazonIngestionRun, run_id)
        assert run.status == "started"
        assert run.lease_owner == "a-different-worker-that-stole-the-lease"  # untouched by the losing attempt

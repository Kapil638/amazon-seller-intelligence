"""12B.6A — Sales and Traffic report ingestion: durable, resumable,
single-marketplace-scoped.

Connects the read-only `AmazonSpApiReportsClient` (`reports_client.py`) to
PostgreSQL. Read-only to Amazon (`createReport` is the one write-shaped
call, and it creates a report *object* on Amazon's own side — it never
writes seller business data), write-to-ASI. No HTTP route. No live Amazon
call is made by anything in this module by itself — it is exercised only
through an injected client/transport in tests.

Lifecycle per claimed attempt (`process_claimed_job`, the single entry
point the worker calls):

    short DB transaction:  re-read the claimed run's own frozen request
                            (marketplace, window, granularities, and
                            whatever report_id/report_document_id/
                            report_processing_status an earlier, crashed
                            attempt may already have recorded)
            |
    if no report_id yet: network createReport call, then a short DB
                          transaction (heartbeat) durably records the
                          returned report_id before this attempt does
                          anything else — so **once that heartbeat has
                          committed**, every later attempt for this run
                          sees the recorded report_id and never calls
                          `createReport` again. This does NOT cover
                          every crash: the Reports API and PostgreSQL
                          are two separate systems that cannot share one
                          atomic transaction, so a crash in the gap
                          between `createReport` returning successfully
                          and this heartbeat's own commit leaves a
                          genuine, orphaned Amazon report this run's own
                          `report_id` never records — the next attempt
                          has no way to know it exists and will call
                          `createReport` again. This is an accepted,
                          honestly-documented at-least-once request
                          boundary, not an exactly-once guarantee — see
                          `docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md`
                          §11's own explicit statement of this crash
                          window. What IS guaranteed regardless: a
                          duplicate `createReport` call can never
                          duplicate *business facts* once the resulting
                          report is eventually downloaded and persisted
                          (idempotent upsert on the fact tables' own
                          natural keys, §8).
            |
    network getReport call (exactly once per attempt — see below for why
                             this is not an in-process poll loop)
            |
    if not yet DONE/CANCELLED/FATAL: reschedule_sales_traffic_run_for_
                                      retry with a short next_retry_at —
                                      releases the claim entirely rather
                                      than holding a worker slot in a
                                      busy-poll loop; the *next* claim
                                      (by this worker or another) resumes
                                      exactly where this attempt left off,
                                      because report_id was already
                                      durably recorded above
            |
    if CANCELLED or FATAL: complete_sales_traffic_run_as_failed — a
                            distinct, truthful failure_class from a
                            transient/retryable one, never silently
                            retried
            |
    if DONE: network getReportDocument + download_report_document, then
             a single DB transaction validates and persists every fact
             row this report contains AND calls
             finalize_successful_sales_traffic_run in the same
             transaction — persistence and checkpoint advancement are
             never split across two commits (Phase 8's own requirement)

**Why one `getReport` call per attempt, not an in-process poll loop:**
`getReport`'s own rate-limit budget (2 req/s) is not the bottleneck —
this report type's `createReport` budget (three requests per five
minutes) is. A worker that held its claim (and its process slot) in a
tight `sleep`-then-`getReport` loop for however many minutes Amazon takes
to finish generating the report would tie up that worker process for the
report's *entire* processing time, for no reason: nothing about polling
requires holding the run's lease. Releasing to `waiting_to_retry` between
checks (this module's actual design) means the same worker (or a
different one) can claim and process *other* eligible jobs while this
report is still generating, and a crash between polls loses nothing —
the next claim resumes from the durably-recorded `report_id` exactly as
described above. This is "durable polling without holding a worker slot
unnecessarily," implemented by reusing the existing retry/reschedule
machinery rather than a separate poll-holding mechanism.

**Authorization failures terminalize immediately, at every network call**
(`createReport`, `getReport`, `getReportDocument`, the document download
itself) — a missing Brand Analytics role or an invalid/expired grant is
never transient (handover doc §2); retrying it would only waste this
report type's already-scarce rate-limit budget.

**Persistence is atomic at the report's own genuine grain** (Phase 8): a
`salesAndTrafficByDate` row is upserted per catalog-wide date bucket
(`AmazonSalesTrafficDailyFactRepository`); a `salesAndTrafficByAsin` row
is upserted per product, tagged with the *exact* requested window (never
a fabricated date) via `AmazonSalesTrafficProductFactRepository`. A
malformed report (already rejected earlier, at the client's own parse
step, before this module ever sees it) never reaches persistence at all;
a persistence failure partway through this transaction rolls back every
row this attempt would have written, including the checkpoint advance —
proven directly in `tests/test_amazon_sales_traffic_ingestion.py`.

**Checkpoint advancement is deliberately scoped to the product-level
daily path only**: `finalize_successful_sales_traffic_run` only receives
a `synced_through_date` when `report_data_start_time ==
report_data_end_time` (a genuine single calendar day) — a wider catalog-
trend request (e.g. a 90-day `salesAndTrafficByDate` backfill) still
persists its facts and still marks the run `succeeded`, but never moves
the incremental daily checkpoint, since that checkpoint's entire purpose
is "how far has *daily product-level* ingestion progressed" (handover
doc §7), not "was any report ever successfully requested for this
participation."
"""

from __future__ import annotations

import asyncio
import logging
import secrets as _secrets_module
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import SecretStr

from app.amazon.common import ensure_utc
from app.amazon.connection_secrets import AmazonConnectionSecretResolver
from app.amazon.lwa_token import oauth_application_credentials
from app.amazon.reports_client import (
    TERMINAL_PROCESSING_STATUSES,
    AmazonSpApiReportsClient,
    CreateSalesAndTrafficReportRequest,
    ReportDocumentInfo,
)
from app.amazon.sales_traffic_models import SalesAndTrafficReport
from app.amazon.secrets import SecretAccessError, SecretNotFoundError, SecretProvider, get_secret_provider
from app.amazon.sellers import sp_api_base_url
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)
from app.persistence.database import session_scope
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSalesTrafficDailyFactRepository,
    AmazonSalesTrafficProductFactRepository,
    AmazonSellerAccountRepository,
)

logger = logging.getLogger(__name__)

DEFAULT_LEASE_DURATION_SECONDS = 300
DEFAULT_POLL_RETRY_SECONDS = 45
MAX_RETRY_ATTEMPTS = 6
# Bounds total elapsed wall-clock time (from the run's own `created_at`,
# never a per-attempt clock) a single run may spend retrying before
# exhausting to a terminal failure — mirrors `orders_sync_max_total_
# retry_seconds`'s identical reasoning. 6 hours comfortably covers the
# worst documented case in the handover doc's own backfill table (a
# single-day report, retried under sustained IN_PROGRESS polling and
# occasional 429s) without ever being unbounded.
DEFAULT_MAX_TOTAL_RETRY_SECONDS = 21600.0


def _default_lease_owner() -> str:
    return f"sales-traffic-ingest-{_secrets_module.token_hex(8)}"


class ReportsClientFactoryProtocol(Protocol):
    def __call__(self, **kwargs) -> AmazonSpApiReportsClient: ...


@dataclass(frozen=True)
class _ClaimFailure(Exception):
    reason: str


@dataclass(frozen=True)
class SalesTrafficIngestionOutcome:
    """Sanitized outcome — never carries a report id, report document id,
    seller identifier, or raw payload."""

    run_id: UUID
    outcome: str  # "created", "polling", "persisted", "failed", "rescheduled"
    reason: str | None = None


class AmazonSalesTrafficIngestionService:
    """Deterministic Sales and Traffic report ingestion for one claimed
    run. Never called from an HTTP route — only from
    `SalesTrafficWorker.run_once` (`sales_traffic_worker.py`)."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        secret_provider: SecretProvider | None = None,
        resolver: AmazonConnectionSecretResolver | None = None,
        reports_client_factory: ReportsClientFactoryProtocol | None = None,
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        lease_owner_factory=_default_lease_owner,
        poll_retry_seconds: int = DEFAULT_POLL_RETRY_SECONDS,
        max_retry_attempts: int = MAX_RETRY_ATTEMPTS,
        max_total_retry_seconds: float = DEFAULT_MAX_TOTAL_RETRY_SECONDS,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._resolver = resolver
        self._reports_client_factory = reports_client_factory
        self._transport = transport
        self._download_transport = download_transport
        self._lease_duration_seconds = lease_duration_seconds
        self._lease_owner_factory = lease_owner_factory
        self._poll_retry_seconds = poll_retry_seconds
        self._max_retry_attempts = max_retry_attempts
        self._max_total_retry_seconds = max_total_retry_seconds

    def __repr__(self) -> str:
        return "AmazonSalesTrafficIngestionService()"

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _secret_resolver(self) -> AmazonConnectionSecretResolver:
        return self._resolver or AmazonConnectionSecretResolver(secret_provider=self._secrets())

    def _client(self, *, refresh_token: SecretStr, region: str, environment: str) -> AmazonSpApiReportsClient:
        cfg = self._cfg()
        client_id, client_secret = oauth_application_credentials(cfg)
        factory = self._reports_client_factory or AmazonSpApiReportsClient
        return factory(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=cfg.sp_api_lwa_token_url,
            base_url=sp_api_base_url(
                region=region,
                environment=environment,
                sandbox_override=cfg.sp_api_sandbox_base_url,
                production_override=cfg.sp_api_production_base_url,
            ),
            region=region,
            timeout_seconds=cfg.sp_api_timeout_seconds,
            user_agent=cfg.sp_api_user_agent,
            transport=self._transport,
            download_transport=self._download_transport,
        )

    async def process_claimed_job(self, run_id: UUID) -> SalesTrafficIngestionOutcome:
        lease_owner, scope = self._load_claim(run_id)

        # Attempt- and elapsed-time budgets, checked before any network
        # call for this attempt — a claim that has already exhausted
        # either bound must terminalize immediately rather than spend
        # this report type's own scarce createReport budget (or simply
        # poll forever) on a run that will never be allowed to retry
        # again anyway. `retry_count` is incremented by `claim_next_
        # sales_traffic_job` itself on every `waiting_to_retry` reclaim
        # (never by this service), so it reflects genuine prior attempts,
        # not merely elapsed heartbeats.
        elapsed_seconds = (datetime.now(UTC) - ensure_utc(scope["created_at"])).total_seconds()
        if scope["retry_count"] >= self._max_retry_attempts or elapsed_seconds >= self._max_total_retry_seconds:
            return self._terminalize_failed(run_id, lease_owner, failure_class="retry_budget_exhausted")

        try:
            client = self._client(
                refresh_token=self._secret_resolver().resolve_refresh_token(
                    organization_id=scope["organization_id"], connection=scope["connection"]
                ),
                region=scope["region"],
                environment=scope["environment"],
            )
        except (SecretNotFoundError, SecretAccessError, SpApiConfigurationError):
            return self._terminalize_failed(run_id, lease_owner, failure_class="connection_unresolvable")

        report_id = scope["report_id"]
        try:
            if report_id is None:
                report_id, _attempts = await client.create_report(
                    CreateSalesAndTrafficReportRequest(
                        marketplace_id=scope["marketplace_id"],
                        data_start_time=scope["data_start_time"],
                        data_end_time=scope["data_end_time"],
                        date_granularity=scope["date_granularity"],
                        asin_granularity=scope["asin_granularity"],
                    )
                )
                self._heartbeat(run_id, lease_owner, report_id=report_id, report_processing_status="IN_QUEUE")
                return SalesTrafficIngestionOutcome(run_id=run_id, outcome="created")

            status = await client.get_report(report_id)
            if status.processing_status not in TERMINAL_PROCESSING_STATUSES:
                self._heartbeat(run_id, lease_owner, report_processing_status=status.processing_status)
                return self._reschedule(run_id, lease_owner, failure_class="polling")

            if status.processing_status in {"CANCELLED", "FATAL"}:
                return self._terminalize_failed(
                    run_id, lease_owner, failure_class=f"report_{status.processing_status.lower()}"
                )

            # DONE. `report_document_id` is guaranteed non-None here by
            # `AmazonSpApiReportsClient.get_report` itself (it rejects a
            # DONE response missing one as a contract violation before
            # this service ever sees it) — the assert below documents
            # that invariant for a type checker, it is not this module's
            # own validation boundary.
            assert status.report_document_id is not None
            document_info: ReportDocumentInfo = await client.get_report_document(status.report_document_id)
            # Renew the lease immediately before the one potentially
            # long-running network step (downloading up to
            # `MAX_DOCUMENT_BYTES` and decompressing up to
            # `MAX_DECOMPRESSED_BYTES`) so a download that runs close to
            # `DOWNLOAD_TIMEOUT_SECONDS` can never expire the lease out
            # from under `_persist_and_finalize`'s own lease-owner
            # compare-and-set below — see `finalize_successful_sales_
            # traffic_run`'s docstring for why that check exists at all.
            self._heartbeat(run_id, lease_owner, report_processing_status=status.processing_status)
            report: SalesAndTrafficReport = await client.download_report_document(document_info)
        except SpApiAuthenticationError:
            return self._terminalize_failed(run_id, lease_owner, failure_class="authentication_failed")
        except SpApiInvalidRequestError:
            return self._terminalize_failed(run_id, lease_owner, failure_class="invalid_request")
        except (SpApiRateLimitedError, SpApiRequestFailedError):
            return self._reschedule(run_id, lease_owner, failure_class="throttled_or_transient")
        except SpApiParseFailedError:
            return self._terminalize_failed(run_id, lease_owner, failure_class="malformed_report")

        try:
            self._persist_and_finalize(run_id, lease_owner, scope=scope, report=report)
        except Exception:
            logger.exception("sales and traffic report persistence raised an unexpected exception")
            raise
        return SalesTrafficIngestionOutcome(run_id=run_id, outcome="persisted")

    # --- DB-only steps, each its own short transaction ---------------------

    def _load_claim(self, run_id: UUID) -> tuple[str, dict]:
        with session_scope() as session:
            run = session.get(
                __import__("app.persistence.models", fromlist=["AmazonIngestionRun"]).AmazonIngestionRun, run_id
            )
            if run is None or run.run_type != "sales_and_traffic_report" or run.status != "started":
                raise _ClaimFailure("run_not_claimable")
            participation = AmazonMarketplaceParticipationRepository(session).get_by_id(
                run.organization_id, run.marketplace_participation_id
            )
            connection = AmazonConnectionRepository(session).get_by_id(run.organization_id, run.connection_id)
            return run.lease_owner, {
                "organization_id": run.organization_id,
                "seller_account_id": run.seller_account_id,
                "marketplace_participation_id": run.marketplace_participation_id,
                "marketplace_id": participation.marketplace_id,
                "connection": connection,
                "region": run.region,
                "environment": run.environment,
                "data_start_time": run.report_data_start_time,
                "data_end_time": run.report_data_end_time,
                "date_granularity": run.report_date_granularity,
                "asin_granularity": run.report_asin_granularity,
                "report_id": run.report_id,
                "retry_count": run.retry_count,
                "created_at": run.created_at,
            }

    def _heartbeat(
        self, run_id: UUID, lease_owner: str, *, report_id: str | None = None, report_processing_status: str | None = None
    ) -> None:
        with session_scope() as session:
            AmazonIngestionRunRepository(session).heartbeat_sales_traffic_run(
                self._load_org_id(session, run_id), run_id, lease_owner=lease_owner,
                lease_duration_seconds=self._lease_duration_seconds, report_id=report_id,
                report_processing_status=report_processing_status,
            )

    def _reschedule(self, run_id: UUID, lease_owner: str, *, failure_class: str) -> SalesTrafficIngestionOutcome:
        with session_scope() as session:
            org_id = self._load_org_id(session, run_id)
            AmazonIngestionRunRepository(session).reschedule_sales_traffic_run_for_retry(
                org_id, run_id, lease_owner=lease_owner,
                next_retry_at=datetime.now(UTC) + timedelta(seconds=self._poll_retry_seconds),
                failure_class=failure_class,
            )
        return SalesTrafficIngestionOutcome(run_id=run_id, outcome="rescheduled", reason=failure_class)

    def _terminalize_failed(self, run_id: UUID, lease_owner: str, *, failure_class: str) -> SalesTrafficIngestionOutcome:
        with session_scope() as session:
            org_id = self._load_org_id(session, run_id)
            AmazonIngestionRunRepository(session).complete_sales_traffic_run_as_failed(
                org_id, run_id, lease_owner=lease_owner, status="failed", failure_class=failure_class
            )
        return SalesTrafficIngestionOutcome(run_id=run_id, outcome="failed", reason=failure_class)

    def _load_org_id(self, session, run_id: UUID) -> UUID:
        from app.persistence.models import AmazonIngestionRun

        run = session.get(AmazonIngestionRun, run_id)
        return run.organization_id

    def _persist_and_finalize(self, run_id: UUID, lease_owner: str, *, scope: dict, report: SalesAndTrafficReport) -> None:
        with session_scope() as session:
            org_id = scope["organization_id"]
            participation_id = scope["marketplace_participation_id"]

            for by_date in report.sales_and_traffic_by_date:
                fields = _daily_fact_fields(by_date)
                AmazonSalesTrafficDailyFactRepository(session).upsert(
                    organization_id=org_id, marketplace_participation_id=participation_id,
                    report_date=datetime.strptime(by_date.date, "%Y-%m-%d").date(),
                    date_granularity=scope["date_granularity"], last_ingestion_run_id=run_id, fields=fields,
                )

            for by_asin in report.sales_and_traffic_by_asin:
                fields = _product_fact_fields(by_asin)
                AmazonSalesTrafficProductFactRepository(session).upsert(
                    organization_id=org_id, marketplace_participation_id=participation_id,
                    request_window_start=scope["data_start_time"], request_window_end=scope["data_end_time"],
                    asin_granularity=scope["asin_granularity"], parent_asin=by_asin.parent_asin,
                    child_asin=by_asin.child_asin or "", seller_sku=by_asin.sku or "",
                    last_ingestion_run_id=run_id, fields=fields,
                )

            synced_through_date = (
                scope["data_end_time"] if scope["data_start_time"] == scope["data_end_time"] else None
            )
            ok = AmazonIngestionRunRepository(session).finalize_successful_sales_traffic_run(
                org_id, run_id, lease_owner=lease_owner, marketplace_participation_id=participation_id,
                seller_account_id=scope["seller_account_id"], synced_through_date=synced_through_date,
            )
            if not ok:
                raise RuntimeError("sales and traffic run lease was lost before finalization could complete")


def _amount_fields(column_name: str, amount) -> dict:
    """`column_name` must be the exact destination column name (already
    including its own `_amount`/`_amount_b2b` suffix) — the B2B suffix
    trails the whole column name, never `_b2b_amount`, so this cannot be
    a templated `f"{prefix}_amount"` without producing a nonexistent
    column name for every `_b2b` field (caught by `test_amazon_sales_
    traffic_ingestion.py`'s persistence tests, which failed against every
    report, B2B or not, before this fix)."""
    if amount is None:
        return {column_name: None}
    return {column_name: amount.amount}


def _daily_fact_fields(by_date) -> dict:
    sales = by_date.sales_by_date
    traffic = by_date.traffic_by_date
    currency_code = sales.ordered_product_sales.currency_code
    fields: dict = {"currency_code": currency_code}
    fields.update(_amount_fields("ordered_product_sales_amount", sales.ordered_product_sales))
    fields.update(_amount_fields("ordered_product_sales_amount_b2b", sales.ordered_product_sales_b2b))
    fields["units_ordered"] = sales.units_ordered
    fields["units_ordered_b2b"] = sales.units_ordered_b2b
    fields["total_order_items"] = sales.total_order_items
    fields["total_order_items_b2b"] = sales.total_order_items_b2b
    fields.update(_amount_fields("average_sales_per_order_item_amount", sales.average_sales_per_order_item))
    fields.update(_amount_fields("average_sales_per_order_item_amount_b2b", sales.average_sales_per_order_item_b2b))
    fields["average_units_per_order_item"] = sales.average_units_per_order_item
    fields["average_units_per_order_item_b2b"] = sales.average_units_per_order_item_b2b
    fields.update(_amount_fields("average_selling_price_amount", sales.average_selling_price))
    fields.update(_amount_fields("average_selling_price_amount_b2b", sales.average_selling_price_b2b))
    fields["units_refunded"] = sales.units_refunded
    fields["refund_rate"] = sales.refund_rate
    fields["claims_granted"] = sales.claims_granted
    fields.update(_amount_fields("claims_amount", sales.claims_amount))
    fields.update(_amount_fields("shipped_product_sales_amount", sales.shipped_product_sales))
    fields["units_shipped"] = sales.units_shipped
    fields["orders_shipped"] = sales.orders_shipped

    for attr in (
        "browser_page_views", "browser_page_views_b2b", "mobile_app_page_views", "mobile_app_page_views_b2b",
        "page_views", "page_views_b2b", "browser_sessions", "browser_sessions_b2b", "mobile_app_sessions",
        "mobile_app_sessions_b2b", "sessions", "sessions_b2b", "buy_box_percentage", "buy_box_percentage_b2b",
        "order_item_session_percentage", "order_item_session_percentage_b2b", "unit_session_percentage",
        "unit_session_percentage_b2b", "average_offer_count", "average_parent_items", "feedback_received",
        "negative_feedback_received", "received_negative_feedback_rate",
    ):
        fields[attr] = getattr(traffic, attr)
    return fields


def _product_fact_fields(by_asin) -> dict:
    sales = by_asin.sales_by_asin
    traffic = by_asin.traffic_by_asin
    fields: dict = {
        "item_name": None,
        "currency_code": sales.ordered_product_sales.currency_code,
        "units_ordered": sales.units_ordered,
        "units_ordered_b2b": sales.units_ordered_b2b,
        "total_order_items": sales.total_order_items,
        "total_order_items_b2b": sales.total_order_items_b2b,
    }
    fields.update(_amount_fields("ordered_product_sales_amount", sales.ordered_product_sales))
    fields.update(_amount_fields("ordered_product_sales_amount_b2b", sales.ordered_product_sales_b2b))
    for attr in (
        "browser_sessions", "browser_sessions_b2b", "mobile_app_sessions", "mobile_app_sessions_b2b", "sessions",
        "sessions_b2b", "browser_session_percentage", "browser_session_percentage_b2b",
        "mobile_app_session_percentage", "mobile_app_session_percentage_b2b", "session_percentage",
        "session_percentage_b2b", "browser_page_views", "browser_page_views_b2b", "mobile_app_page_views",
        "mobile_app_page_views_b2b", "page_views", "page_views_b2b", "browser_page_views_percentage",
        "browser_page_views_percentage_b2b", "mobile_app_page_views_percentage",
        "mobile_app_page_views_percentage_b2b", "page_views_percentage", "page_views_percentage_b2b",
        "buy_box_percentage", "buy_box_percentage_b2b", "unit_session_percentage", "unit_session_percentage_b2b",
    ):
        fields[attr] = getattr(traffic, attr)
    return fields

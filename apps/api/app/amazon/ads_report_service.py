"""Amazon Ads Reporting v3 async state machine. 12C read-only foundation.

Sequence (item 5 of the governing task): create report request -> persist
Amazon's own report id -> poll with bounded backoff -> detect completion/
terminal failure -> download -> decompress+validate -> normalize rows ->
upsert metrics transactionally -> advance the sync checkpoint only after
success.

Every database write here is a short, separate `session_scope()` block —
polling (`_poll_until_terminal`) sleeps *between* HTTP calls with no
transaction open across that wait, per the governing task's explicit
"report polling must not hold a database transaction open." The one
exception is the final ingest step, which upserts all of one report's
rows plus the checkpoint advance in a single transaction — that is
intentionally atomic (partial ingestion of one report must never persist
as if the report were fully processed), and it holds no HTTP wait inside
it, only database writes.

Never logs a report body or raw row payload — only counts, ids, and
status strings. `failure_detail` stored on `AmazonAdsReportRun` is always
`str(exception)` for one of this module's own typed exceptions, which are
themselves never constructed with secret-shaped text (see
`app.core.exceptions`'s `Ads*` exceptions).
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import InvalidOperation
from uuid import UUID

from pydantic import ValidationError

from app.amazon.ads_client import AdsRequestContext, AmazonAdsApiClient
from app.amazon.ads_lwa_token import refresh_ads_access_token
from app.amazon.ads_models import AdsReportConfigurationBody, AdsReportRequestConfiguration, AdsReportRow
from app.amazon.secrets import SecretNotFoundError, SecretProvider
from app.core.config import Settings
from app.core.exceptions import (
    AdsApiAuthenticationError,
    AdsApiDuplicateReportError,
    AdsApiInvalidRequestError,
    AdsApiParseFailedError,
    AdsApiRateLimitedError,
    AdsApiRequestFailedError,
    AdsReportFailedError,
    AdsReportOversizedError,
)
from app.persistence.database import session_scope
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsDailyPerformanceFactRepository,
    AmazonAdsReportRunRepository,
    AmazonAdsSyncCheckpointRepository,
    AmazonAdsSyncErrorRepository,
)

logger = logging.getLogger(__name__)

REPORT_TYPE_SPONSORED_PRODUCTS_DAILY = "sponsored_products_daily"
_TERMINAL_SUCCESS = "COMPLETED"
_TERMINAL_FAILURE_STATUSES = frozenset({"CANCELLED", "FAILURE"})


@dataclass(frozen=True)
class ReportJobOutcome:
    report_run_id: UUID
    outcome: str  # "succeeded" | "retrying" | "failed" | "no_job"
    records_ingested: int = 0


def compute_backoff_delay(
    attempt_count: int, *, base_seconds: float, max_seconds: float, rng: "random.Random | None" = None
) -> float:
    """Full-jitter exponential backoff: `random(0, min(max, base * 2^n))`.
    Bounded by `max_seconds` regardless of how large `attempt_count`
    grows, so a long-failing job never waits unboundedly long between
    retries. `rng` is injectable for deterministic tests; defaults to
    the module-level `random` functions."""
    capped = min(max_seconds, base_seconds * (2 ** max(attempt_count, 0)))
    if rng is not None:
        return rng.uniform(0, capped)
    return random.uniform(0, capped)


def report_name_for_run(report_run_id: UUID, start_date: date, end_date: date) -> str:
    """A deterministic, safe report name: contains only this internal
    (non-secret, non-seller-identifying) ledger row id and the requested
    date range — never a seller name, profile id, or account identifier.
    Unique per run so two ledger rows can never collide on Amazon's side
    even if they happen to cover the same date range. Well under any
    documented Amazon report-name length limit."""
    return f"asi-sp-campaigns-{report_run_id}-{start_date.isoformat()}-{end_date.isoformat()}"


def next_sync_window(
    *, synced_through_date: date | None, today: date, lookback_days: int
) -> tuple[date, date]:
    """Incremental window with a rolling lookback: re-requests the last
    `lookback_days` even when already synced, so late Amazon attribution
    adjustments are refreshed rather than permanently skipped. A
    never-synced profile starts from `lookback_days` before today."""
    if synced_through_date is None:
        start = today - timedelta(days=lookback_days)
    else:
        start = min(synced_through_date - timedelta(days=lookback_days), today)
    end = today
    if start > end:
        start = end
    return start, end


class AmazonAdsReportService:
    def __init__(
        self,
        *,
        settings: Settings,
        secret_provider: SecretProvider,
        ads_client: AmazonAdsApiClient,
        lease_owner: str,
    ) -> None:
        self._cfg = settings
        self._secrets = secret_provider
        self._client = ads_client
        self._lease_owner = lease_owner

    def create_report_request(
        self, *, organization_id: UUID, ads_profile_id: UUID, start_date: date, end_date: date
    ) -> UUID:
        with session_scope() as session:
            run = AmazonAdsReportRunRepository(session).create(
                organization_id,
                ads_profile_id,
                report_type=REPORT_TYPE_SPONSORED_PRODUCTS_DAILY,
                start_date=start_date,
                end_date=end_date,
            )
            return run.id

    async def process_one_claimed_job(self) -> ReportJobOutcome:
        """Claim exactly one eligible job and drive it to a terminal or
        retry-scheduled state. Never claims/starts a second job for the
        same profile concurrently (see `claim_next_report_job`'s
        per-profile cap) and never enqueues/starts anything itself — the
        caller (a future Ads worker, or a test) decides when this runs."""
        with session_scope() as session:
            run = AmazonAdsReportRunRepository(session).claim_next_report_job(
                lease_owner=self._lease_owner,
                lease_duration_seconds=self._cfg.ads_report_lease_duration_seconds,
                max_global_active=self._cfg.ads_sync_max_global_concurrent_jobs,
                max_active_per_profile=self._cfg.ads_sync_max_concurrent_jobs_per_profile,
            )
            if run is None:
                return ReportJobOutcome(report_run_id=UUID(int=0), outcome="no_job")
            run_id = run.id
            organization_id = run.organization_id
            ads_profile_id = run.ads_profile_id
            start_date, end_date = run.start_date, run.end_date
            amazon_report_id = run.amazon_report_id
            attempt_count = run.attempt_count

        # Resolve the profile's connection/token outside any transaction —
        # the LWA refresh + Ads HTTP calls below never run while a
        # database transaction is open.
        with session_scope() as session:
            from app.persistence.repositories import AmazonAdsProfileRepository

            profile = AmazonAdsProfileRepository(session).get_owned(organization_id, ads_profile_id)
            if profile is None:
                AmazonAdsReportRunRepository(session).mark_failed(
                    run_id, failure_class="profile_missing", failure_detail="Advertiser profile no longer exists."
                )
                return ReportJobOutcome(report_run_id=run_id, outcome="failed")
            connection = AmazonAdsConnectionRepository(session).get_by_id(organization_id, profile.connection_id)
            if connection is None or not connection.token_reference:
                AmazonAdsReportRunRepository(session).mark_failed(
                    run_id, failure_class="connection_missing", failure_detail="Amazon Ads connection is not authorized."
                )
                return ReportJobOutcome(report_run_id=run_id, outcome="failed")
            token_reference = connection.token_reference
            region = profile.region
            profile_id_str = profile.profile_id

        try:
            refresh_token = self._secrets.get_secret(token_reference)
        except SecretNotFoundError:
            with session_scope() as session:
                AmazonAdsReportRunRepository(session).mark_failed(
                    run_id, failure_class="secret_missing", failure_detail="Stored Ads refresh token was not found."
                )
            return ReportJobOutcome(report_run_id=run_id, outcome="failed")

        try:
            access_token_response = await refresh_ads_access_token(
                client_id=self._cfg.ads_lwa_client_id,
                client_secret=self._cfg.ads_lwa_client_secret,
                refresh_token=refresh_token,
                token_url=self._cfg.ads_lwa_token_url,
                timeout_seconds=self._cfg.ads_api_timeout_seconds,
            )
        except Exception as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="token_refresh_failed", detail=str(exc),
            )

        ctx = AdsRequestContext(
            access_token=access_token_response.access_token,
            client_id=self._cfg.ads_lwa_client_id.get_secret_value() if self._cfg.ads_lwa_client_id else "",
            region=region,
            profile_id=profile_id_str,
            correlation_id=str(run_id),
        )

        try:
            if not amazon_report_id:
                created = await self._client.create_report(
                    ctx,
                    AdsReportRequestConfiguration(
                        name=report_name_for_run(run_id, start_date, end_date),
                        startDate=start_date,
                        endDate=end_date,
                        configuration=AdsReportConfigurationBody(
                            adProduct="SPONSORED_PRODUCTS",
                            reportTypeId="spCampaigns",
                            timeUnit="DAILY",
                            groupBy=["campaign"],
                            columns=["date", "campaignId", "impressions", "clicks", "cost", "sales14d", "purchases14d"],
                        ),
                    ),
                )
                amazon_report_id = created.report_id
                with session_scope() as session:
                    AmazonAdsReportRunRepository(session).set_amazon_report(
                        run_id, amazon_report_id=amazon_report_id, amazon_report_status=created.status
                    )
                if created.status == _TERMINAL_SUCCESS and created.url:
                    return await self._download_and_ingest(
                        run_id, organization_id, ads_profile_id, ctx, created.url
                    )
        except AdsApiDuplicateReportError as exc:
            # HTTP 425 on create only (see _raise_for_status's
            # treat_425_as_duplicate_report scoping). Never
            # AdsApiInvalidRequestError, never a fabricated report id —
            # see AdsApiDuplicateReportError's own docstring for exactly
            # what is and is not officially confirmed here.
            if exc.existing_report_id:
                # A cautiously usable id was present in the response —
                # adopt it exactly like a normal successful create and
                # fall through to polling below. No second create is
                # ever issued for it. (Whether Amazon's 425 body always
                # names the existing report this way is NOT confirmed —
                # see the exception's docstring; this is the safer of
                # two imperfect options, not a confirmed contract.)
                amazon_report_id = exc.existing_report_id
                with session_scope() as session:
                    AmazonAdsReportRunRepository(session).set_amazon_report(
                        run_id, amazon_report_id=amazon_report_id, amazon_report_status="PENDING"
                    )
            else:
                # No id was discoverable in the response (undocumented
                # schema — see the exception's docstring). Uses a
                # deliberately separate, small budget
                # (ads_report_duplicate_create_max_attempts, default 3)
                # — NEVER ads_report_poll_max_attempts (~40) — since
                # Amazon is telling us an identical report already
                # exists; that many repeated identical creates would be
                # far too aggressive for a condition this narrow. Reuses
                # the ledger row's existing attempt_count counter (no
                # schema change) against this smaller threshold rather
                # than introducing a second persisted counter; a run
                # that already accumulated attempts from unrelated
                # earlier failures before hitting a 425 will exhaust
                # this budget sooner, which is conservative, not wrong.
                # Prefers Amazon's own Retry-After when present (425
                # does not typically carry one, but this is not assumed
                # either way); handled directly here rather than through
                # _retry_or_fail, which stays untouched for the separate
                # resumability PR.
                if attempt_count >= self._cfg.ads_report_duplicate_create_max_attempts:
                    with session_scope() as session:
                        AmazonAdsReportRunRepository(session).mark_failed(
                            run_id, failure_class="report_create_duplicate_unresolved", failure_detail=str(exc)
                        )
                        AmazonAdsSyncErrorRepository(session).record(
                            organization_id, ads_profile_id, error_code="report_create_duplicate_unresolved",
                            error_message=str(exc), report_run_id=run_id,
                        )
                    return ReportJobOutcome(report_run_id=run_id, outcome="failed")
                delay = (
                    exc.retry_after_seconds
                    if exc.retry_after_seconds is not None
                    else self._cfg.ads_report_duplicate_create_retry_seconds
                )
                next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                with session_scope() as session:
                    AmazonAdsReportRunRepository(session).mark_retry(
                        run_id, next_retry_at=next_retry_at,
                        failure_class="report_create_duplicate_unresolved", failure_detail=str(exc),
                    )
                    AmazonAdsSyncErrorRepository(session).record(
                        organization_id, ads_profile_id, error_code="report_create_duplicate_unresolved",
                        error_message=str(exc), report_run_id=run_id,
                    )
                return ReportJobOutcome(report_run_id=run_id, outcome="retrying")
        except AdsApiInvalidRequestError as exc:
            # Permanent: Amazon rejected the request shape outright —
            # unrelated to the 425 duplicate-report case above (a 425
            # never reaches this branch; see _raise_for_status). Fails
            # immediately rather than leaving the row `started` until
            # lease recovery — this is a real contract/schema error, not
            # a transient condition retrying could ever resolve.
            with session_scope() as session:
                AmazonAdsReportRunRepository(session).mark_failed(
                    run_id, failure_class="report_create_invalid_request", failure_detail=str(exc)
                )
                AmazonAdsSyncErrorRepository(session).record(
                    organization_id, ads_profile_id, error_code="report_create_invalid_request",
                    error_message=str(exc), report_run_id=run_id,
                )
            return ReportJobOutcome(report_run_id=run_id, outcome="failed")
        except AdsApiAuthenticationError as exc:
            # Distinct, visible terminal classification — a credential/
            # authorization failure will not resolve itself by retrying
            # the same request.
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_create_authentication_failed", detail=str(exc),
            )
        except AdsApiParseFailedError as exc:
            # Amazon returned a 2xx that this client could not parse as
            # the documented create/status response shape — a contract
            # mismatch, not a transient condition.
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_create_contract_mismatch", detail=str(exc),
            )
        except AdsApiRateLimitedError as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="report_create_rate_limited", detail=str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            )
        except AdsApiRequestFailedError as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="report_create_failed", detail=str(exc),
            )

        return await self._poll_until_terminal(run_id, organization_id, ads_profile_id, attempt_count, ctx, amazon_report_id)

    async def _poll_until_terminal(
        self,
        run_id: UUID,
        organization_id: UUID,
        ads_profile_id: UUID,
        attempt_count: int,
        ctx: AdsRequestContext,
        amazon_report_id: str,
    ) -> ReportJobOutcome:
        for _attempt in range(self._cfg.ads_report_poll_max_attempts):
            with session_scope() as session:
                AmazonAdsReportRunRepository(session).heartbeat(
                    run_id, lease_owner=self._lease_owner, lease_duration_seconds=self._cfg.ads_report_lease_duration_seconds
                )
            try:
                status_response = await self._client.get_report_status(ctx, amazon_report_id)
            except AdsApiRateLimitedError as exc:
                await asyncio.sleep(exc.retry_after_seconds or self._cfg.ads_report_poll_interval_seconds)
                continue
            except AdsApiAuthenticationError as exc:
                return await self._fail_permanently(
                    run_id, organization_id, ads_profile_id,
                    failure_class="report_poll_authentication_failed", detail=str(exc),
                )
            except AdsApiInvalidRequestError as exc:
                # Covers both an ordinary malformed poll request AND a
                # defensively-adopted 425 report id (see
                # AdsApiDuplicateReportError's docstring) that Amazon's
                # status endpoint does not actually recognize — either
                # way this is a permanent condition: the report id this
                # run is polling is unusable, and retrying the identical
                # status request will not change that. Terminalizes
                # cleanly (lease cleared by mark_failed) and never
                # triggers a second create request — amazon_report_id
                # stays exactly what it was, and the create branch above
                # is never re-entered for this run.
                return await self._fail_permanently(
                    run_id, organization_id, ads_profile_id,
                    failure_class="report_poll_invalid_request", detail=str(exc),
                )
            except AdsApiParseFailedError as exc:
                return await self._fail_permanently(
                    run_id, organization_id, ads_profile_id,
                    failure_class="report_poll_contract_mismatch", detail=str(exc),
                )
            except AdsApiRequestFailedError as exc:
                return await self._retry_or_fail(
                    run_id, organization_id, ads_profile_id, attempt_count,
                    failure_class="report_poll_failed", detail=str(exc),
                )

            with session_scope() as session:
                AmazonAdsReportRunRepository(session).update_amazon_status(
                    run_id, amazon_report_status=status_response.status
                )
            if status_response.status == _TERMINAL_SUCCESS and status_response.url:
                return await self._download_and_ingest(run_id, organization_id, ads_profile_id, ctx, status_response.url)
            if status_response.status in _TERMINAL_FAILURE_STATUSES:
                return await self._retry_or_fail(
                    run_id, organization_id, ads_profile_id, attempt_count,
                    failure_class="report_failed",
                    detail=status_response.failure_reason or "Amazon Ads report reached a terminal failure status.",
                )
            await asyncio.sleep(self._cfg.ads_report_poll_interval_seconds)

        return await self._retry_or_fail(
            run_id, organization_id, ads_profile_id, attempt_count,
            failure_class="report_poll_exhausted", detail="Exceeded bounded poll attempts.",
        )

    async def _download_and_ingest(
        self, run_id: UUID, organization_id: UUID, ads_profile_id: UUID, ctx: AdsRequestContext, url: str
    ) -> ReportJobOutcome:
        with session_scope() as session:
            attempt_count = AmazonAdsReportRunRepository(session).get_owned(organization_id, run_id).attempt_count

        try:
            body = await self._client.download_report(ctx, url, max_bytes=self._cfg.ads_report_max_download_bytes)
        except AdsApiAuthenticationError as exc:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_download_authentication_failed", detail=str(exc),
            )
        except AdsApiInvalidRequestError as exc:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_download_invalid_request", detail=str(exc),
            )
        except AdsApiRateLimitedError as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="report_download_rate_limited", detail=str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            )
        except AdsReportOversizedError as exc:
            # Permanent: nothing about retrying an identical download
            # would produce a smaller report. No evidence (documented or
            # observed) suggests an oversized report is ever transient.
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_oversized", detail=str(exc),
            )
        except AdsApiRequestFailedError as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="report_download_failed", detail=str(exc),
            )

        try:
            parsed = _parse_report_body(body, max_bytes=self._cfg.ads_report_max_download_bytes)
        except AdsReportFailedError as exc:
            # Permanent and visible: decompression, JSON, or top-level
            # shape failure is deterministic given these exact bytes —
            # retrying would re-download and fail identically, so this
            # fails immediately rather than consuming the shared retry
            # budget.
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_malformed", detail=str(exc),
            )

        if parsed.total_rows > 0 and not parsed.rows:
            # A nonempty report where every row failed schema validation
            # is a deterministic row-contract mismatch, not a transient
            # failure — retrying would reprocess the exact same bytes
            # and fail identically, so this fails immediately rather
            # than consuming retry attempts. Never marked "succeeded":
            # zero facts are persisted and the checkpoint never
            # advances (both happen only in the block below, which this
            # branch returns before reaching).
            detail = (
                f"Amazon returned total={parsed.total_rows} accepted=0 "
                f"rejected={parsed.rejected_rows} — report row-contract mismatch."
            )
            logger.warning(
                "ads report rejected in full run_id=%s total_rows=%s rejected_rows=%s",
                run_id, parsed.total_rows, parsed.rejected_rows,
            )
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="report_row_contract_mismatch", detail=detail,
            )

        with session_scope() as session:
            fact_repo = AmazonAdsDailyPerformanceFactRepository(session)
            ingested = 0
            for row in parsed.rows:
                if row.campaign_id is None or row.report_date is None:
                    continue
                fact_repo.upsert(
                    organization_id,
                    ads_profile_id,
                    {
                        "entity_type": "campaign",
                        "entity_external_id": row.campaign_id,
                        "fact_date": row.report_date,
                        "attribution_window": "14d",
                        "currency_code": row.currency,
                        "impressions": row.impressions,
                        "clicks": row.clicks,
                        "cost": row.cost,
                        "attributed_sales": row.attributed_sales_14d,
                        "attributed_conversions": row.attributed_conversions_14d,
                    },
                    report_run_id=run_id,
                )
                ingested += 1
            if parsed.rejected_rows:
                # Partial rejection: some rows failed schema validation
                # but not all (the all-rejected case is handled above
                # and never reaches here). The run still succeeds — only
                # the accepted rows are missing data, not the whole
                # report — but the counts are recorded so a partial
                # rejection is queryable/visible rather than silently
                # swallowed into the success outcome.
                AmazonAdsSyncErrorRepository(session).record(
                    organization_id, ads_profile_id,
                    error_code="report_partial_row_rejection",
                    error_message=(
                        f"total={parsed.total_rows} accepted={ingested} rejected={parsed.rejected_rows}"
                    ),
                    report_run_id=run_id,
                )
            run = AmazonAdsReportRunRepository(session).get_owned(organization_id, run_id)
            AmazonAdsReportRunRepository(session).mark_succeeded(run_id, records_ingested=ingested)
            if run is not None:
                AmazonAdsSyncCheckpointRepository(session).advance(
                    organization_id, ads_profile_id, synced_through_date=run.end_date, report_run_id=run_id
                )
        logger.info("ads report ingested run_id=%s records=%s", run_id, ingested)
        return ReportJobOutcome(report_run_id=run_id, outcome="succeeded", records_ingested=ingested)

    async def _fail_permanently(
        self,
        run_id: UUID,
        organization_id: UUID,
        ads_profile_id: UUID,
        *,
        failure_class: str,
        detail: str,
    ) -> ReportJobOutcome:
        """Terminalize immediately — never leaves the row `started`,
        never schedules a retry. For failure classes that are
        deterministic given the exact request/response already
        observed (a permanently-wrong request, an auth failure, a
        contract mismatch): retrying would reproduce the identical
        outcome, so this does not consume any part of the retry
        budget."""
        with session_scope() as session:
            AmazonAdsReportRunRepository(session).mark_failed(run_id, failure_class=failure_class, failure_detail=detail)
            AmazonAdsSyncErrorRepository(session).record(
                organization_id, ads_profile_id, error_code=failure_class, error_message=detail, report_run_id=run_id
            )
        return ReportJobOutcome(report_run_id=run_id, outcome="failed")

    async def _retry_or_fail(
        self,
        run_id: UUID,
        organization_id: UUID,
        ads_profile_id: UUID,
        attempt_count: int,
        *,
        failure_class: str,
        detail: str,
        retry_after_seconds: float | None = None,
    ) -> ReportJobOutcome:
        """For genuinely transient failures (transport errors, rate
        limits, Amazon-side terminal failure statuses, exhausted poll
        attempts) — bounded by `ads_report_poll_max_attempts` regardless
        of delay. The delay itself honors Amazon's own `Retry-After`
        when the caller supplies one; otherwise it is bounded
        exponential backoff with full jitter (`compute_backoff_delay`),
        never the old flat `ads_report_poll_interval_seconds` wait."""
        with session_scope() as session:
            run_repo = AmazonAdsReportRunRepository(session)
            if attempt_count >= self._cfg.ads_report_poll_max_attempts:
                run_repo.mark_failed(run_id, failure_class=failure_class, failure_detail=detail)
                outcome = "failed"
            else:
                delay = (
                    retry_after_seconds
                    if retry_after_seconds is not None
                    else compute_backoff_delay(
                        attempt_count,
                        base_seconds=self._cfg.ads_report_retry_base_seconds,
                        max_seconds=self._cfg.ads_report_retry_max_seconds,
                    )
                )
                next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                run_repo.mark_retry(
                    run_id, next_retry_at=next_retry_at, failure_class=failure_class, failure_detail=detail
                )
                outcome = "retrying"
            AmazonAdsSyncErrorRepository(session).record(
                organization_id, ads_profile_id, error_code=failure_class, error_message=detail, report_run_id=run_id
            )
        return ReportJobOutcome(report_run_id=run_id, outcome=outcome)


@dataclass(frozen=True)
class ParsedReportBody:
    rows: list[AdsReportRow]
    total_rows: int
    rejected_rows: int


def _parse_report_body(body: bytes, *, max_bytes: int) -> ParsedReportBody:
    """Validate content before trusting it as report data: bounded size
    (already enforced by the client's own download path — re-checked
    here defensively), valid JSON, and a JSON array — never a bare
    object or scalar. Rows that fail schema validation are skipped and
    counted, never silently coerced. Returns total/rejected counts
    alongside the accepted rows so the caller can tell "a few rows had
    unrelated issues" apart from "every row failed" (see
    `_download_and_ingest`'s explicit all-rejected check — a genuinely
    different outcome, not just a smaller version of the same thing)."""
    if len(body) > max_bytes:
        raise AdsReportFailedError("Amazon Ads report exceeded the allowed size.")
    try:
        payload = json.loads(body)
    except (UnicodeDecodeError, ValueError) as exc:
        raise AdsReportFailedError("Amazon Ads report body was not valid JSON.") from exc
    if not isinstance(payload, list):
        raise AdsReportFailedError("Amazon Ads report body was not a JSON array of rows.")
    rows: list[AdsReportRow] = []
    rejected = 0
    for raw_row in payload:
        if not isinstance(raw_row, dict):
            rejected += 1
            continue
        try:
            rows.append(AdsReportRow.model_validate(raw_row))
        except (ValidationError, InvalidOperation):
            rejected += 1
    if rejected:
        logger.warning("ads report parse skipped malformed rows count=%s total=%s", rejected, len(payload))
    return ParsedReportBody(rows=rows, total_rows=len(payload), rejected_rows=rejected)

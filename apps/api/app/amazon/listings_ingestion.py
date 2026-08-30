"""12B.3D — Marketplace-scoped Seller Listings ingestion and reconciliation.

Connects the read-only 12B.3C `AmazonSpApiListingsClient` to PostgreSQL.
Read-only to Amazon, write-to-ASI. No HTTP route. No live Amazon call is
made by anything in this module by itself — it is exercised only through
an injected client/transport in tests, exactly like
`AmazonMarketplaceReconciliationService`.

Lifecycle (mirrors the suggested design exactly):

    short DB transaction:  validate scope + claim run
            |
    network page fetch, no DB session open
            |
    short DB transaction:  heartbeat/progress (once per page)
            |
    repeat
            |
    single final DB transaction: reconcile + complete run

On any failure after the run is claimed, the run is completed as failed in
a fresh, separate transaction — never inside a transaction that also rolled
back listing writes, and never leaving the run row stuck at `'started'`.

Ownership-chain validation deliberately reports one of only two generic
reasons for anything touching a *different* organization's data
(`scope_not_found` for missing/cross-organization/mismatched resources,
`scope_inactive` for the caller's own inactive resources) — see
`_validate_and_claim` for exactly which checks collapse into which reason
and why.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets as _secrets_module
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy.exc import SQLAlchemyError

from app.amazon.common import ensure_utc
from app.amazon.connection_secrets import AmazonConnectionSecretResolver
from app.amazon.listings_client import (
    LISTINGS_PAGE_SIZE,
    AmazonSpApiListingsClient,
    ListingsPageRequest,
)
from app.amazon.listings_normalization import ListingNormalizationError, NormalizedListing, normalize_item
from app.amazon.lwa_token import oauth_application_credentials
from app.amazon.secrets import InvalidSecretReferenceError, SecretAccessError, SecretNotFoundError, SecretProvider, get_secret_provider
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
from app.persistence.models import AmazonIngestionRun
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerListingRepository,
)

# 12B.3G — durable worker retry policy: which traversal failure classes are
# worth rescheduling for a later attempt versus terminal immediately. A
# genuine data anomaly (duplicate SKU, ambiguous summary, a malformed
# price, a cyclic pagination token) will very likely reproduce identically
# on retry, so retrying it only delays a truthful failure — not included.
# `authentication_failed`/`invalid_request`/`configuration_error` are
# request-shape problems retrying with identical parameters cannot fix —
# not included either, matching `SpApiInvalidRequestError`'s own
# documented "never retried" semantics at the client layer.
RETRYABLE_LISTINGS_FAILURE_CLASSES = frozenset(
    {"throttled", "transient_request_failed", "malformed_page", "record_count_inconsistent"}
)

logger = logging.getLogger(__name__)

# Amazon's documented hard ceiling: at most 1000 items are retrievable
# through pagination for this operation, regardless of how many
# `numberOfResults` reports actually matching. At LISTINGS_PAGE_SIZE=20,
# that is exactly 50 pages. Reaching this cap while a `nextToken` is still
# present is the single, documented reason a complete-looking traversal is
# not authoritative — see the module docstring in `listings_client.py`'s
# `ListingsPageProvenance`/`reported_total_results` for the same fact
# documented at the schema layer. This same constant also serves as the
# "excessive pages" defensive bound: nothing in a correctly-functioning
# traversal should ever need more than this many pages, so hitting it is
# always treated as the ceiling case, not a separate failure class.
LISTINGS_RESULT_CEILING = 1000
MAX_LISTINGS_PAGES = LISTINGS_RESULT_CEILING // LISTINGS_PAGE_SIZE

DEFAULT_LEASE_DURATION_SECONDS = 300


class _ConnectionSnapshot(BaseModel):
    """Plain, session-independent copy of exactly the fields
    `AmazonConnectionSecretResolver` needs. Never carries a token."""

    model_config = ConfigDict(extra="ignore")

    organization_id: UUID
    id: UUID
    provider: str
    environment: str
    token_reference: str | None


@dataclass(frozen=True)
class ListingsIngestionOutcome:
    """Sanitized public outcome. Never carries a seller ID, marketplace ID,
    token, lease owner, or raw Amazon payload — only ASI's own internal
    UUIDs (already known to any caller that supplied them as scope) and
    truthful counters."""

    succeeded: bool
    seller_account_id: UUID
    marketplace_participation_id: UUID
    reason: str | None = None
    ingestion_run_id: UUID | None = None
    pages_fetched: int = 0
    records_received: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    reported_total_results: int | None = None
    pagination_complete: bool = False


@dataclass(frozen=True)
class _ClaimedRun:
    run_id: UUID
    seller_account_id: UUID
    lease_owner: str
    marketplace_id: str
    selling_partner_id: str
    region: str
    environment: str
    connection: _ConnectionSnapshot


class _ClaimFailure(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class ListingsClientFactoryProtocol(Protocol):
    def __call__(self, **kwargs: object) -> AmazonSpApiListingsClient: ...


def _default_lease_owner() -> str:
    """Random, non-secret — an opaque process/attempt identifier, never a
    credential and never derived from anything Amazon-supplied."""
    return _secrets_module.token_hex(16)


class AmazonListingsIngestionService:
    """Fetches all safely retrievable Listings Items pages for one
    (seller_account, marketplace_participation) scope and reconciles them
    into `amazon_seller_listings`."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        secret_provider: SecretProvider | None = None,
        resolver: AmazonConnectionSecretResolver | None = None,
        listings_client_factory: ListingsClientFactoryProtocol | None = None,
        transport: httpx.BaseTransport | None = None,
        lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        lease_owner_factory: Callable[[], str] = _default_lease_owner,
        max_pages: int = MAX_LISTINGS_PAGES,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._resolver = resolver
        self._listings_client_factory = listings_client_factory
        self._transport = transport
        self._lease_duration_seconds = lease_duration_seconds
        self._lease_owner_factory = lease_owner_factory
        self._max_pages = max_pages

    def __repr__(self) -> str:
        return "AmazonListingsIngestionService()"

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _secret_resolver(self) -> AmazonConnectionSecretResolver:
        return self._resolver or AmazonConnectionSecretResolver(secret_provider=self._secrets())

    def _client(self, *, refresh_token: SecretStr, region: str, environment: str) -> AmazonSpApiListingsClient:
        cfg = self._cfg()
        client_id, client_secret = oauth_application_credentials(cfg)
        factory = self._listings_client_factory or AmazonSpApiListingsClient
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
        )

    # --- public entry point ------------------------------------------------

    async def sync(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
    ) -> ListingsIngestionOutcome:
        try:
            claimed = self._validate_and_claim(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
            )
        except _ClaimFailure as exc:
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=exc.reason,
            )

        try:
            refresh_token = self._secret_resolver().resolve_refresh_token(
                organization_id=organization_id,
                connection=claimed.connection,
            )
        except (InvalidSecretReferenceError, SecretNotFoundError, SecretAccessError):
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason="secret_unresolvable",
            )
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason="secret_unresolvable",
                ingestion_run_id=claimed.run_id,
            )

        try:
            client = self._client(
                refresh_token=refresh_token, region=claimed.region, environment=claimed.environment
            )
        finally:
            del refresh_token

        traversal = await self._traverse(
            client=client,
            organization_id=organization_id,
            claimed=claimed,
        )
        del client

        if traversal.failure_class is not None:
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason=traversal.failure_class,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=traversal.failure_class,
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                records_rejected=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )

        return self._reconcile(
            organization_id=organization_id,
            marketplace_participation_id=marketplace_participation_id,
            claimed=claimed,
            traversal=traversal,
        )

    # --- 12B.3G: durable worker entry point ---------------------------------

    async def process_claimed_job(self, run_id: UUID) -> ListingsIngestionOutcome:
        """Processes a Listings job the caller has *already* claimed via
        `AmazonIngestionRunRepository.claim_next_listings_job` (status is
        `started`, a lease is held). Called only by the durable worker
        (`app.amazon.listings_worker`), never by an HTTP route directly —
        the trigger route only enqueues (`AmazonListingsSyncTriggerService`
        in `app.amazon.listings_sync`).

        Re-validates ownership/eligibility freshly with `_check_scope`
        (conditions may have changed since the job was queued — this
        never trusts enqueue-time validation as still current) using the
        SAME checks `sync()` uses, then fetches pages and reconciles via
        the exact same `_traverse`/`_reconcile` this class already uses
        for the immediate path — no ingestion logic is duplicated. The
        only genuinely new behavior is what happens on a *retryable*
        traversal failure: instead of a terminal failure, the run is
        rescheduled (`waiting_to_retry`) for a bounded number of further
        attempts — see `RETRYABLE_LISTINGS_FAILURE_CLASSES` and
        `_handle_worker_failure`.
        """
        cfg = self._cfg()
        with session_scope() as session:
            run_row = session.get(AmazonIngestionRun, run_id)
            if run_row is None or run_row.run_type != "listings" or run_row.status != "started":
                # Nothing to do: not a real, currently-claimed Listings
                # job (already finished by someone else, or a caller
                # error). Never fabricate progress for a run that isn't
                # genuinely in this worker's hands.
                return ListingsIngestionOutcome(
                    succeeded=False,
                    seller_account_id=run_row.seller_account_id if run_row else run_id,
                    marketplace_participation_id=(
                        run_row.marketplace_participation_id if run_row else run_id
                    ),
                    reason="not_claimed",
                )
            organization_id = run_row.organization_id
            seller_account_id = run_row.seller_account_id
            marketplace_participation_id = run_row.marketplace_participation_id
            lease_owner = run_row.lease_owner
            attempt_number = run_row.retry_count + 1
            first_started_at = run_row.started_at

            try:
                selling_partner_id, marketplace_id, connection_snapshot, region, environment = (
                    self._check_scope(
                        session,
                        organization_id=organization_id,
                        seller_account_id=seller_account_id,
                        marketplace_participation_id=marketplace_participation_id,
                    )
                )
            except _ClaimFailure as exc:
                placeholder = _ClaimedRun(
                    run_id=run_id,
                    seller_account_id=seller_account_id,
                    lease_owner=lease_owner,
                    marketplace_id="",
                    selling_partner_id="",
                    region=run_row.region,
                    environment=run_row.environment,
                    connection=_ConnectionSnapshot(
                        organization_id=organization_id,
                        id=run_row.connection_id or organization_id,
                        provider="SP_API",
                        environment=run_row.environment,
                        token_reference=None,
                    ),
                )
                self._fail_claimed_run(organization_id=organization_id, run=placeholder, reason=exc.reason)
                return ListingsIngestionOutcome(
                    succeeded=False,
                    seller_account_id=seller_account_id,
                    marketplace_participation_id=marketplace_participation_id,
                    reason=exc.reason,
                    ingestion_run_id=run_id,
                )

            claimed = _ClaimedRun(
                run_id=run_id,
                seller_account_id=seller_account_id,
                lease_owner=lease_owner,
                marketplace_id=marketplace_id,
                selling_partner_id=selling_partner_id,
                region=region,
                environment=environment,
                connection=connection_snapshot,
            )

        try:
            refresh_token = self._secret_resolver().resolve_refresh_token(
                organization_id=organization_id,
                connection=claimed.connection,
            )
        except (InvalidSecretReferenceError, SecretNotFoundError, SecretAccessError):
            self._fail_claimed_run(organization_id=organization_id, run=claimed, reason="secret_unresolvable")
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason="secret_unresolvable",
                ingestion_run_id=run_id,
            )

        try:
            client = self._client(
                refresh_token=refresh_token, region=claimed.region, environment=claimed.environment
            )
        finally:
            del refresh_token

        traversal = await self._traverse(client=client, organization_id=organization_id, claimed=claimed)
        del client

        if traversal.failure_class is not None:
            return self._handle_worker_failure(
                organization_id=organization_id,
                marketplace_participation_id=marketplace_participation_id,
                claimed=claimed,
                traversal=traversal,
                attempt_number=attempt_number,
                first_started_at=first_started_at,
                cfg=cfg,
            )

        return self._reconcile(
            organization_id=organization_id,
            marketplace_participation_id=marketplace_participation_id,
            claimed=claimed,
            traversal=traversal,
        )

    def _compute_retry_delay(self, retry_after_seconds: float | None, attempt_number: int, cfg: Settings) -> float:
        """Prefer Amazon's own `Retry-After` signal when present; otherwise
        bounded exponential backoff with *full* jitter (a random delay
        between 0 and the exponential bound, not the bound itself — the
        standard mitigation against many workers retrying in lockstep).
        Either way, the result is capped at `listings_sync_max_backoff_
        seconds` — even a very large `Retry-After` value never blocks a
        job indefinitely."""
        if retry_after_seconds is not None and retry_after_seconds >= 0:
            delay = retry_after_seconds
        else:
            bound = cfg.listings_sync_base_backoff_seconds * (2 ** max(attempt_number - 1, 0))
            delay = random.uniform(0, bound)
        return min(delay, cfg.listings_sync_max_backoff_seconds)

    def _handle_worker_failure(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        claimed: _ClaimedRun,
        traversal: "_TraversalResult",
        attempt_number: int,
        first_started_at,
        cfg: Settings,
    ) -> ListingsIngestionOutcome:
        """Decides retry vs. terminal for a failed traversal attempt. Never
        retries a non-retryable failure class (see
        `RETRYABLE_LISTINGS_FAILURE_CLASSES`). A retryable failure is
        rescheduled (`waiting_to_retry`) unless the attempt or elapsed-time
        budget is exhausted, in which case it becomes the sanitized
        terminal failure class `rate_limited` — deliberately that name
        regardless of which retryable class triggered the *most recent*
        attempt, since from the caller's perspective the meaningful fact
        is "this job could not complete within its retry budget", not
        which specific attempt happened to fail last.
        """
        if traversal.failure_class not in RETRYABLE_LISTINGS_FAILURE_CLASSES:
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason=traversal.failure_class,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=traversal.failure_class,
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                records_rejected=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )

        elapsed_seconds = (
            (datetime.now(UTC) - ensure_utc(first_started_at)).total_seconds()
            if first_started_at is not None
            else 0.0
        )
        budget_exhausted = (
            attempt_number >= cfg.listings_sync_max_attempts
            or elapsed_seconds >= cfg.listings_sync_max_total_retry_seconds
        )
        if budget_exhausted:
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason="rate_limited",
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason="rate_limited",
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                records_rejected=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )

        delay = self._compute_retry_delay(traversal.retry_after_seconds, attempt_number, cfg)
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        with session_scope() as session:
            rescheduled = AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
                organization_id,
                claimed.run_id,
                lease_owner=claimed.lease_owner,
                next_retry_at=next_retry_at,
                failure_class=traversal.failure_class,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
            )
        reason = "waiting_to_retry" if rescheduled else "lease_lost"
        return ListingsIngestionOutcome(
            succeeded=False,
            seller_account_id=claimed.seller_account_id,
            marketplace_participation_id=marketplace_participation_id,
            reason=reason,
            ingestion_run_id=claimed.run_id,
            pages_fetched=traversal.pages_fetched,
            records_received=traversal.records_received,
            records_rejected=traversal.records_received,
            reported_total_results=traversal.reported_total_results,
            pagination_complete=traversal.pagination_complete,
        )

    # --- phase A: validate + claim ------------------------------------------

    @staticmethod
    def _check_scope(
        session,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
    ) -> tuple[str, str, _ConnectionSnapshot, str, str]:
        """Shared ownership/eligibility checks, used by both the immediate
        `sync()` path and the durable worker path (12B.3G). Returns
        `(selling_partner_id, marketplace_id, connection_snapshot, region,
        environment)` or raises `_ClaimFailure` — never claims or mutates
        anything itself."""
        seller_account_repo = AmazonSellerAccountRepository(session)
        participation_repo = AmazonMarketplaceParticipationRepository(session)
        connection_repo = AmazonConnectionRepository(session)

        seller_account = seller_account_repo.get_by_id(organization_id, seller_account_id)
        participation = participation_repo.get_by_id(organization_id, marketplace_participation_id)

        # Collapsed on purpose: missing, cross-organization, and
        # mismatched-ownership resources must be indistinguishable to
        # the caller — see the module docstring.
        if (
            seller_account is None
            or participation is None
            or participation.seller_account_id != seller_account_id
        ):
            raise _ClaimFailure("scope_not_found")

        if seller_account.status != "active" or not participation.is_active:
            raise _ClaimFailure("scope_inactive")

        selling_partner_id = (seller_account.selling_partner_id or "").strip()
        if not selling_partner_id:
            raise _ClaimFailure("identity_missing")

        marketplace_id = (participation.marketplace_id or "").strip()
        if not marketplace_id:
            raise _ClaimFailure("scope_not_found")

        connection_id = participation.connection_id
        connection = connection_repo.get_by_id(organization_id, connection_id) if connection_id else None
        if connection is None:
            raise _ClaimFailure("connection_unresolvable")

        connection_snapshot = _ConnectionSnapshot(
            organization_id=connection.organization_id,
            id=connection.id,
            provider=connection.provider,
            environment=connection.environment,
            token_reference=connection.token_reference,
        )
        return selling_partner_id, marketplace_id, connection_snapshot, connection.region, connection.environment

    def _validate_and_claim(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
    ) -> _ClaimedRun:
        with session_scope() as session:
            selling_partner_id, marketplace_id, connection_snapshot, region, environment = self._check_scope(
                session,
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
            )

            lease_owner = self._lease_owner_factory()
            claim = AmazonIngestionRunRepository(session).claim_listings_run(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                region=region,
                environment=environment,
                connection_id=connection_snapshot.id,
                lease_owner=lease_owner,
                lease_duration_seconds=self._lease_duration_seconds,
            )
            if not claim.claimed:
                raise _ClaimFailure(claim.reason or "already_running")

            return _ClaimedRun(
                run_id=claim.run_id,
                seller_account_id=seller_account_id,
                lease_owner=lease_owner,
                marketplace_id=marketplace_id,
                selling_partner_id=selling_partner_id,
                region=region,
                environment=environment,
                connection=connection_snapshot,
            )

    # --- phase B: network traversal ------------------------------------------

    async def _traverse(
        self,
        *,
        client: AmazonSpApiListingsClient,
        organization_id: UUID,
        claimed: _ClaimedRun,
    ) -> "_TraversalResult":
        pages_fetched = 0
        records_received = 0
        reported_total_results: int | None = None
        seen_tokens: set[str] = set()
        seen_skus: set[str] = set()
        normalized: list[NormalizedListing] = []
        next_token: str | None = None
        failure_class: str | None = None
        pagination_complete = False
        retry_after_seconds: float | None = None

        while True:
            # A background task renews the lease on a fixed wall-clock
            # cadence WHILE this single fetch is in flight — see
            # `_renew_lease_while_awaiting`'s docstring for why this,
            # not the page-count-based heartbeat below, is what actually
            # guarantees the lease cannot expire mid-request.
            lease_lost_during_request: dict[str, bool] = {"lost": False}
            renewal_task = asyncio.create_task(
                self._renew_lease_while_awaiting(
                    organization_id=organization_id,
                    run_id=claimed.run_id,
                    lease_owner=claimed.lease_owner,
                    pages_fetched=pages_fetched,
                    interval_seconds=self._cfg().listings_sync_heartbeat_time_interval_seconds,
                    lost=lease_lost_during_request,
                )
            )
            try:
                page = await client.fetch_page(
                    ListingsPageRequest(
                        seller_id=claimed.selling_partner_id,
                        marketplace_id=claimed.marketplace_id,
                        page_token=next_token,
                    )
                )
            except SpApiAuthenticationError:
                failure_class = "authentication_failed"
                break
            except SpApiRateLimitedError as exc:
                failure_class = "throttled"
                retry_after_seconds = exc.retry_after_seconds
                break
            except SpApiInvalidRequestError:
                failure_class = "invalid_request"
                break
            except SpApiConfigurationError:
                failure_class = "configuration_error"
                break
            except SpApiParseFailedError:
                failure_class = "malformed_page"
                break
            except SpApiRequestFailedError:
                failure_class = "transient_request_failed"
                break
            finally:
                renewal_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal_task

            if lease_lost_during_request["lost"]:
                # The page technically arrived, but a renewal already
                # failed while we were waiting for it — this worker can no
                # longer prove it still holds the lease. Never continue
                # processing (or advance pagination) under a claim that
                # may already belong to someone else.
                failure_class = "lease_lost"
                break

            pages_fetched += 1
            if reported_total_results is None:
                reported_total_results = page.number_of_results
                # Independent of how traversal itself ends (naturally, via
                # the ceiling page count, or otherwise): a reported total
                # already above the documented 1000-item ceiling means this
                # snapshot can never be trusted as complete, even if what we
                # actually retrieve happens to look internally consistent.
                if reported_total_results > LISTINGS_RESULT_CEILING:
                    failure_class = "result_ceiling_exceeded"
                    break
            elif page.number_of_results != reported_total_results:
                failure_class = "record_count_inconsistent"
                break

            page_failure = self._process_page_items(
                items=page.items,
                marketplace_id=claimed.marketplace_id,
                seen_skus=seen_skus,
                normalized=normalized,
            )
            records_received += len(page.items)
            if page_failure is not None:
                failure_class = page_failure
                break

            # `listings_sync_heartbeat_interval_pages` (default 1 = every
            # page) trades DB writes for lease-renewal frequency — an
            # operator raising it must keep it well below the lease
            # duration's safe margin, since a lease is only ever renewed
            # on a page boundary where this fires.
            heartbeat_due = pages_fetched % max(self._cfg().listings_sync_heartbeat_interval_pages, 1) == 0
            if heartbeat_due:
                heartbeat_ok = self._heartbeat(
                    organization_id=organization_id,
                    run_id=claimed.run_id,
                    lease_owner=claimed.lease_owner,
                    pages_fetched=pages_fetched,
                )
                if not heartbeat_ok:
                    failure_class = "lease_lost"
                    break

            next_token = page.next_token
            if next_token is None:
                pagination_complete = True
                break
            if next_token in seen_tokens:
                failure_class = "cyclic_pagination_token"
                break
            seen_tokens.add(next_token)

            if pages_fetched >= self._max_pages:
                failure_class = "result_ceiling_exceeded"
                break

        if failure_class is None and pagination_complete:
            if records_received != (reported_total_results or 0):
                failure_class = "record_count_inconsistent"

        return _TraversalResult(
            pages_fetched=pages_fetched,
            records_received=records_received,
            reported_total_results=reported_total_results,
            pagination_complete=pagination_complete,
            failure_class=failure_class,
            listings=normalized,
            retry_after_seconds=retry_after_seconds,
        )

    def _process_page_items(
        self,
        *,
        items: list,
        marketplace_id: str,
        seen_skus: set[str],
        normalized: list[NormalizedListing],
    ) -> str | None:
        """Returns a sanitized failure reason, or None on success. Appends
        to `normalized` in place only while every item on this page
        continues to validate — a failure partway through a page still
        leaves `normalized` holding only fully-valid entries, but the
        caller always discards the whole traversal on any failure, so a
        partially-filled list here is never mistaken for a complete one."""
        for item in items:
            if item.sku in seen_skus:
                return "duplicate_sku"
            seen_skus.add(item.sku)
            try:
                normalized.append(normalize_item(item, marketplace_id=marketplace_id))
            except ListingNormalizationError as exc:
                return exc.reason
        return None

    def _heartbeat(self, *, organization_id: UUID, run_id: UUID, lease_owner: str, pages_fetched: int) -> bool:
        with session_scope() as session:
            return AmazonIngestionRunRepository(session).heartbeat_listings_run(
                organization_id,
                run_id,
                lease_owner=lease_owner,
                lease_duration_seconds=self._lease_duration_seconds,
                pages_fetched=pages_fetched,
            )

    async def _renew_lease_while_awaiting(
        self,
        *,
        organization_id: UUID,
        run_id: UUID,
        lease_owner: str,
        pages_fetched: int,
        interval_seconds: float,
        lost: dict[str, bool],
    ) -> None:
        """Runs concurrently with exactly one in-flight `client.fetch_page()`
        call (started and cancelled around that single await — see its call
        site in `_traverse`), renewing the lease on a fixed wall-clock
        cadence independent of whether that page ever completes.

        This is the actual guarantee that a lease cannot expire mid-request,
        regardless of how slow one Amazon call is: the page-count-based
        heartbeat in `_traverse`'s main loop only ever fires *after* a page
        finishes, so without this, one sufficiently slow request (the SP-API
        client's own short-retry loop can legitimately take up to roughly
        `timeout_seconds * max_attempts` plus backoff sleeps — see
        `app.amazon.listings_client`'s documented worst case) could
        otherwise outlast the lease with nothing renewing it in the
        meantime. `interval_seconds` only needs to stay comfortably below
        the lease duration; it is unrelated to `listings_sync_heartbeat_
        interval_pages`, which governs progress-reporting cadence, not
        lease safety.

        Sets `lost["lost"] = True` and returns (without raising) the moment
        a renewal attempt fails OR raises — the caller checks this
        immediately after the awaited request completes, whether that
        request itself succeeded or failed, and treats it as `lease_lost`
        either way: continuing to process a page after this fires would
        mean writing under a lease this worker no longer verifiably holds.

        An unexpected exception from `_heartbeat` itself (e.g. a
        transient database/connection error, as opposed to an ordinary
        "the compare-and-set didn't match" `False`) is deliberately
        treated the same fail-safe way, not re-raised: this task's own
        exception would otherwise propagate out through the caller's
        `await renewal_task` in its `finally` block, surfacing as an
        unhandled error instead of the routine, already-tested
        `lease_lost` outcome — and in a state (unable to even confirm
        whether the lease is still held) where "assume it may be lost"
        is the only safe assumption anyway. `asyncio.CancelledError` is
        the sole exception re-raised, since that is this task's own
        expected cooperative-cancellation signal, not a failure.
        """
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    renewed = self._heartbeat(
                        organization_id=organization_id,
                        run_id=run_id,
                        lease_owner=lease_owner,
                        pages_fetched=pages_fetched,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning(
                        "amazon listings lease renewal raised an unexpected exception run_id=%s", run_id
                    )
                    renewed = False
                if not renewed:
                    lost["lost"] = True
                    return
        except asyncio.CancelledError:
            raise

    # --- phase C: reconcile or fail ------------------------------------------

    def _reconcile(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        claimed: _ClaimedRun,
        traversal: "_TraversalResult",
    ) -> ListingsIngestionOutcome:
        try:
            with session_scope() as session:
                runs = AmazonIngestionRunRepository(session)
                completed = runs.complete_listings_run(
                    organization_id,
                    claimed.run_id,
                    lease_owner=claimed.lease_owner,
                    status="succeeded",
                    records_received=traversal.records_received,
                    records_accepted=traversal.records_received,
                    records_rejected=0,
                    pages_fetched=traversal.pages_fetched,
                    reported_total_results=traversal.reported_total_results,
                    pagination_complete=True,
                    failure_class=None,
                )
                if not completed:
                    raise _ClaimFailure("lease_lost")

                # One validated, organization-scoped write boundary — see
                # `AmazonSellerListingRepository.reconcile_snapshot` for why
                # this replaced a per-item upsert loop plus a separate
                # deactivate_missing call: ownership is now checked exactly
                # once here, not implicitly trusted from the caller.
                AmazonSellerListingRepository(session).reconcile_snapshot(
                    organization_id=organization_id,
                    marketplace_participation_id=marketplace_participation_id,
                    listings=traversal.listings,
                    last_ingestion_run_id=claimed.run_id,
                )
        except _ClaimFailure as exc:
            logger.warning(
                "amazon listings ingestion lost its lease before final reconciliation reason=%s",
                exc.reason,
            )
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=exc.reason,
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=traversal.pagination_complete,
            )
        except SQLAlchemyError:
            # A genuine database-level rejection (constraint violation,
            # connection failure, etc.) is an ordinary, expected
            # reconciliation outcome — sanitized and recorded, not raised.
            logger.warning(
                "amazon listings ingestion final reconciliation failed run_id=%s",
                claimed.run_id,
            )
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason="reconciliation_failed",
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=True,
            )
            return ListingsIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason="reconciliation_failed",
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=True,
            )
        except Exception:
            # Anything else is not a recognized database/reconciliation
            # failure and not a lost lease — most likely a genuine
            # programming defect (e.g. the organization-ownership check
            # tripping on scope that claim-time validation should already
            # have guaranteed). A sanitized failure is still recorded so
            # the run is never left stuck at 'started', but the exception
            # is re-raised rather than disguised as an ordinary business
            # outcome — it must surface as a real error, not a routine
            # "reconciliation_failed" result.
            logger.warning(
                "amazon listings ingestion final reconciliation raised an unexpected exception run_id=%s",
                claimed.run_id,
            )
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason="unexpected_error",
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                reported_total_results=traversal.reported_total_results,
                pagination_complete=True,
            )
            raise

        return ListingsIngestionOutcome(
            succeeded=True,
            seller_account_id=claimed.seller_account_id,
            marketplace_participation_id=marketplace_participation_id,
            ingestion_run_id=claimed.run_id,
            pages_fetched=traversal.pages_fetched,
            records_received=traversal.records_received,
            records_accepted=traversal.records_received,
            records_rejected=0,
            reported_total_results=traversal.reported_total_results,
            pagination_complete=True,
        )

    def _fail_claimed_run(
        self,
        *,
        organization_id: UUID,
        run: _ClaimedRun,
        reason: str,
        pages_fetched: int = 0,
        records_received: int = 0,
        reported_total_results: int | None = None,
        pagination_complete: bool = False,
    ) -> None:
        try:
            with session_scope() as session:
                AmazonIngestionRunRepository(session).complete_listings_run(
                    organization_id,
                    run.run_id,
                    lease_owner=run.lease_owner,
                    status="failed",
                    records_received=records_received,
                    records_accepted=0,
                    records_rejected=records_received,
                    pages_fetched=pages_fetched,
                    reported_total_results=reported_total_results,
                    pagination_complete=pagination_complete,
                    failure_class=reason,
                )
        except Exception:
            # Last-resort logging only: this is already inside failure
            # handling, with no further fallback to escalate to — swallow
            # broadly here rather than raise a second exception out of an
            # exception handler, which would only mask the original reason
            # this was called at all.
            logger.warning(
                "amazon listings ingestion could not record failure run_id=%s reason=%s",
                run.run_id,
                reason,
            )


@dataclass(frozen=True)
class _TraversalResult:
    pages_fetched: int
    records_received: int
    reported_total_results: int | None
    pagination_complete: bool
    failure_class: str | None
    listings: list[NormalizedListing] = field(default_factory=list)
    # 12B.3G: only set when `failure_class == "throttled"` and Amazon's
    # response included a usable `Retry-After` signal — the durable
    # worker's retry scheduler reads this; the pre-existing synchronous
    # `sync()` path ignores it entirely.
    retry_after_seconds: float | None = None

"""12B.6B — Marketplace-scoped FBA Inventory ingestion and reconciliation.

Connects the read-only `AmazonSpApiInventoryClient` to PostgreSQL. Read-only
to Amazon, write-to-ASI. No HTTP route. No live Amazon call is made by
anything in this module by itself — it is exercised only through an
injected client/transport in tests, exactly like
`AmazonListingsIngestionService`.

Lifecycle (Listings-shaped, not Orders/Sales-Traffic-shaped — see
`inventory_client.py`'s module docstring for why `nextToken`'s 30-second
lifetime rules out a durable, cross-attempt pagination token the way
Orders uses one):

    short DB transaction:  re-validate scope, already claimed by the
                            caller (`AmazonIngestionRunRepository.
                            claim_next_inventory_job`)
            |
    network page fetch, no DB session open (proactively self-throttled to
    respect the pinned 2 req/s ceiling — see `_traverse`)
            |
    short DB transaction:  heartbeat/progress (once per page)
            |
    repeat until pagination ends naturally (no further `nextToken`), a
    failure occurs, or the configured page-count safety bound is reached
            |
    single final DB transaction: reconcile (upsert current state, record
    one immutable observation per accepted row, deactivate whatever is
    missing) + complete run — only ever reached when the traversal ended
    naturally with a fully accepted set of pages; never on a timeout,
    malformed page, or bound breach (12B.6B audit requirement — see
    `AmazonSellerInventoryRepository.reconcile_snapshot`'s own docstring)

On any failure, the run is completed as failed (or rescheduled) in a
fresh, separate transaction — never inside a transaction that also
touched inventory state, and never left stuck at `'started'`.

**Per-row identity rejection, not whole-snapshot rejection** (a
deliberate difference from `listings_ingestion.py`): a row missing
`sellerSku`/`condition` is rejected and counted
(`InventoryNormalizationError`, see `inventory_normalization.py`), but
does not fail the rest of the page/run — the pinned FBA Inventory
contract documents every field on `InventorySummary` as independently
optional, so an unidentifiable row is an expected, not anomalous,
outcome, unlike Listings' `duplicate_sku`/`ambiguous_marketplace_summary`
cases (genuine data-integrity problems worth rejecting the whole
snapshot over).
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
from app.amazon.inventory_client import (
    AmazonSpApiInventoryClient,
    InventoryPageRequest,
)
from app.amazon.inventory_normalization import (
    InventoryNormalizationError,
    NormalizedInventoryObservation,
    normalize_summary,
)
from app.amazon.lwa_token import oauth_application_credentials
from app.amazon.secrets import (
    InvalidSecretReferenceError,
    SecretAccessError,
    SecretNotFoundError,
    SecretProvider,
    get_secret_provider,
)
from app.amazon.sellers import sp_api_base_url
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiErrorEnvelopeError,
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
    AmazonSellerInventoryRepository,
)

# 12B.6B — which traversal failure classes are worth rescheduling for a
# later attempt versus terminal immediately. `pagination_bound_exceeded`
# is deliberately excluded — mirrors Listings' own `result_ceiling_
# exceeded` non-retryable treatment: retrying against the *same*
# configured page bound cannot succeed differently, so it is a signal to
# raise the bound (an operator/config action), not something a retry
# attempt fixes on its own.
RETRYABLE_INVENTORY_FAILURE_CLASSES = frozenset({"throttled", "transient_request_failed", "malformed_page"})

# fix/inventory-empty-response-and-failure-classification — a run that
# exhausts its retry budget while its most recent attempt's own
# `failure_class` was one of these keys is terminalized with the mapped,
# specific reason instead of a blanket "rate_limited" — the live defect
# this closes: a run that retried 5 times against `malformed_page`
# (never an actual 429) was recorded as `rate_limited`, actively
# misleading anyone reading it afterward. `throttled` is deliberately
# absent — exhausting the budget on genuine repeated throttling *is*
# correctly `rate_limited`, the one case the old blanket label was
# actually right for. Mirrors `orders_ingestion.py`'s own
# `_EXHAUSTION_REASON_BY_FAILURE_CLASS` precedent exactly.
_EXHAUSTION_REASON_BY_FAILURE_CLASS: dict[str, str] = {
    "malformed_page": "malformed_page_retry_exhausted",
    "transient_request_failed": "transient_request_retry_exhausted",
}

# Amazon's own documented SP-API error codes this project has direct
# evidence for (SP-API's shared error-code vocabulary, reused across
# operations) — mapped to a stable, sanitized ASI failure_class so an
# operator reading `amazon_ingestion_runs.failure_class` can tell
# "Amazon rejected this for a permission reason" from "Amazon rejected
# this for a validation reason" without ever needing the raw `message`/
# `details` text (never logged or stored — see `SpApiErrorEnvelopeError`).
# An unrecognized code — including any this project has not yet directly
# observed — deliberately falls back to the generic `error_envelope`
# class rather than guessing a more specific one.
_ERROR_ENVELOPE_FAILURE_CLASS_BY_CODE: dict[str, str] = {
    # Reuses this module's own already-established class names exactly
    # (see the `except SpApi...Error:` mappings in `_traverse` below) so
    # an error-envelope-carried failure is indistinguishable, once
    # classified, from the same conceptual failure signaled via a plain
    # HTTP status — never a parallel, near-duplicate vocabulary.
    "Unauthorized": "authentication_failed",
    "AccessDenied": "authentication_failed",
    "Forbidden": "authentication_failed",
    "InvalidInput": "invalid_request",
    "InvalidParameterValue": "invalid_request",
    "QuotaExceeded": "throttled",  # retryable; exhausts to "rate_limited" exactly like a real 429 would.
    "ServiceUnavailable": "transient_request_failed",
    "InternalFailure": "transient_request_failed",
}


def _error_envelope_failure_class(code: str) -> str:
    return _ERROR_ENVELOPE_FAILURE_CLASS_BY_CODE.get(code, "error_envelope")


logger = logging.getLogger(__name__)

# Amazon documents no hard result ceiling for `getInventorySummaries` the
# way Listings' `searchListingsItems` documents 1000 items / 50 pages
# (12B.6B audit finding — explicitly NOT DOCUMENTED). This bound is
# therefore ASI's own configurable safety valve, not a discovered Amazon
# contract fact — see `Settings.inventory_sync_max_pages`. Deliberately
# generous by default so a large catalog is not silently truncated; if a
# real seller's catalog ever needs more pages than the configured bound,
# that is the intended, visible signal to raise the bound, not a ceiling
# this ingestion invents on Amazon's behalf.
DEFAULT_MAX_PAGES = 500

# Pinned contract: 2 requests/second, burst 2 (12B.6B audit,
# getinventorysummaries reference page). This proactive inter-page delay
# is defense-in-depth, never the sole rate-limit mechanism — the actual
# authority is `inventory_client.py`'s reactive handling of a real 429
# response (honors `Retry-After` when Amazon sends one, bounded backoff
# otherwise). Spacing requests below the documented ceiling simply makes
# a 429 less likely to begin with.
DEFAULT_MIN_PAGE_INTERVAL_SECONDS = 0.5

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
class InventoryIngestionOutcome:
    """Sanitized public outcome. Never carries a seller ID, marketplace
    ID, token, lease owner, or raw Amazon payload — only ASI's own
    internal UUIDs and truthful counters."""

    succeeded: bool
    seller_account_id: UUID
    marketplace_participation_id: UUID
    reason: str | None = None
    ingestion_run_id: UUID | None = None
    pages_fetched: int = 0
    records_received: int = 0
    records_accepted: int = 0
    records_rejected: int = 0
    pagination_complete: bool = False


@dataclass(frozen=True)
class _ClaimedRun:
    run_id: UUID
    seller_account_id: UUID
    lease_owner: str
    marketplace_id: str
    region: str
    environment: str
    connection: _ConnectionSnapshot


class _ClaimFailure(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class InventoryClientFactoryProtocol(Protocol):
    def __call__(self, **kwargs: object) -> AmazonSpApiInventoryClient: ...


def _default_lease_owner() -> str:
    return _secrets_module.token_hex(16)


class AmazonInventoryIngestionService:
    """Fetches all `getInventorySummaries` pages for one (seller_account,
    marketplace_participation) scope and reconciles them into
    `amazon_seller_inventory`/`amazon_seller_inventory_observations`."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        secret_provider: SecretProvider | None = None,
        resolver: AmazonConnectionSecretResolver | None = None,
        inventory_client_factory: InventoryClientFactoryProtocol | None = None,
        transport: httpx.BaseTransport | None = None,
        lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        lease_owner_factory: Callable[[], str] = _default_lease_owner,
        max_pages: int | None = None,
        min_page_interval_seconds: float | None = None,
        sleep: Callable[[float], "asyncio.Future|None"] | None = None,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._resolver = resolver
        self._inventory_client_factory = inventory_client_factory
        self._transport = transport
        self._lease_duration_seconds = lease_duration_seconds
        self._lease_owner_factory = lease_owner_factory
        self._max_pages = max_pages
        self._min_page_interval_seconds = min_page_interval_seconds
        self._sleep = sleep or asyncio.sleep

    def __repr__(self) -> str:
        return "AmazonInventoryIngestionService()"

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _resolved_max_pages(self) -> int:
        if self._max_pages is not None:
            return self._max_pages
        return self._cfg().inventory_sync_max_pages

    def _resolved_min_page_interval_seconds(self) -> float:
        if self._min_page_interval_seconds is not None:
            return self._min_page_interval_seconds
        return self._cfg().inventory_worker_min_page_interval_seconds

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _secret_resolver(self) -> AmazonConnectionSecretResolver:
        return self._resolver or AmazonConnectionSecretResolver(secret_provider=self._secrets())

    def _client(self, *, refresh_token: SecretStr, region: str, environment: str) -> AmazonSpApiInventoryClient:
        cfg = self._cfg()
        client_id, client_secret = oauth_application_credentials(cfg)
        factory = self._inventory_client_factory or AmazonSpApiInventoryClient
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

    # --- durable worker entry point ----------------------------------------

    async def process_claimed_job(self, run_id: UUID) -> InventoryIngestionOutcome:
        """Processes an Inventory job the caller has *already* claimed via
        `AmazonIngestionRunRepository.claim_next_inventory_job` (status is
        `started`, a lease is held). Called only by the durable worker
        (`app.amazon.inventory_worker`), never by an HTTP route directly —
        the trigger route only enqueues
        (`AmazonInventorySyncTriggerService` in `app.amazon.inventory_sync`).
        """
        cfg = self._cfg()
        with session_scope() as session:
            run_row = session.get(AmazonIngestionRun, run_id)
            if run_row is None or run_row.run_type != "inventory" or run_row.status != "started":
                return InventoryIngestionOutcome(
                    succeeded=False,
                    seller_account_id=run_row.seller_account_id if run_row else run_id,
                    marketplace_participation_id=(run_row.marketplace_participation_id if run_row else run_id),
                    reason="not_claimed",
                )
            organization_id = run_row.organization_id
            seller_account_id = run_row.seller_account_id
            marketplace_participation_id = run_row.marketplace_participation_id
            lease_owner = run_row.lease_owner
            attempt_number = run_row.retry_count + 1
            first_started_at = run_row.started_at

            try:
                marketplace_id, connection_snapshot, region, environment = self._check_scope(
                    session,
                    organization_id=organization_id,
                    seller_account_id=seller_account_id,
                    marketplace_participation_id=marketplace_participation_id,
                )
            except _ClaimFailure as exc:
                placeholder = _ClaimedRun(
                    run_id=run_id,
                    seller_account_id=seller_account_id,
                    lease_owner=lease_owner,
                    marketplace_id="",
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
                return InventoryIngestionOutcome(
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
                region=region,
                environment=environment,
                connection=connection_snapshot,
            )

        try:
            refresh_token = self._secret_resolver().resolve_refresh_token(
                organization_id=organization_id, connection=claimed.connection
            )
        except (InvalidSecretReferenceError, SecretNotFoundError, SecretAccessError):
            self._fail_claimed_run(organization_id=organization_id, run=claimed, reason="secret_unresolvable")
            return InventoryIngestionOutcome(
                succeeded=False,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason="secret_unresolvable",
                ingestion_run_id=run_id,
            )

        try:
            client = self._client(refresh_token=refresh_token, region=claimed.region, environment=claimed.environment)
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
        base = cfg.inventory_sync_base_backoff_seconds
        cap = cfg.inventory_sync_max_backoff_seconds
        if retry_after_seconds is not None and retry_after_seconds >= 0:
            delay = retry_after_seconds
        else:
            bound = base * (2 ** max(attempt_number - 1, 0))
            delay = random.uniform(0, bound)
        return min(delay, cap)

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
    ) -> InventoryIngestionOutcome:
        if traversal.failure_class not in RETRYABLE_INVENTORY_FAILURE_CLASSES:
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason=traversal.failure_class,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                pagination_complete=traversal.pagination_complete,
            )
            return InventoryIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=traversal.failure_class,
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                records_rejected=traversal.records_received,
                pagination_complete=traversal.pagination_complete,
            )

        elapsed_seconds = (
            (datetime.now(UTC) - ensure_utc(first_started_at)).total_seconds() if first_started_at is not None else 0.0
        )
        max_attempts = cfg.inventory_sync_max_attempts
        max_total_retry_seconds = cfg.inventory_sync_max_total_retry_seconds
        budget_exhausted = attempt_number >= max_attempts or elapsed_seconds >= max_total_retry_seconds
        if budget_exhausted:
            exhaustion_reason = _EXHAUSTION_REASON_BY_FAILURE_CLASS.get(traversal.failure_class, "rate_limited")
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason=exhaustion_reason,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                pagination_complete=traversal.pagination_complete,
            )
            return InventoryIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=exhaustion_reason,
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                records_rejected=traversal.records_received,
                pagination_complete=traversal.pagination_complete,
            )

        delay = self._compute_retry_delay(traversal.retry_after_seconds, attempt_number, cfg)
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        with session_scope() as session:
            rescheduled = AmazonIngestionRunRepository(session).reschedule_inventory_run_for_retry(
                organization_id,
                claimed.run_id,
                lease_owner=claimed.lease_owner,
                next_retry_at=next_retry_at,
                failure_class=traversal.failure_class,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
            )
        reason = "waiting_to_retry" if rescheduled else "lease_lost"
        return InventoryIngestionOutcome(
            succeeded=False,
            seller_account_id=claimed.seller_account_id,
            marketplace_participation_id=marketplace_participation_id,
            reason=reason,
            ingestion_run_id=claimed.run_id,
            pages_fetched=traversal.pages_fetched,
            records_received=traversal.records_received,
            records_rejected=traversal.records_received,
            pagination_complete=traversal.pagination_complete,
        )

    # --- scope validation ----------------------------------------------------

    @staticmethod
    def _check_scope(
        session,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
    ) -> tuple[str, _ConnectionSnapshot, str, str]:
        """Shared ownership/eligibility checks — identical collapsed-reason
        shape to `AmazonListingsIngestionService._check_scope`: missing,
        cross-organization, and mismatched-ownership resources must be
        indistinguishable to the caller."""
        seller_account_repo = AmazonSellerAccountRepository(session)
        participation_repo = AmazonMarketplaceParticipationRepository(session)
        connection_repo = AmazonConnectionRepository(session)

        seller_account = seller_account_repo.get_by_id(organization_id, seller_account_id)
        participation = participation_repo.get_by_id(organization_id, marketplace_participation_id)

        if seller_account is None or participation is None or participation.seller_account_id != seller_account_id:
            raise _ClaimFailure("scope_not_found")

        if seller_account.status != "active" or not participation.is_active:
            raise _ClaimFailure("scope_inactive")

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
        return marketplace_id, connection_snapshot, connection.region, connection.environment

    # --- network traversal ----------------------------------------------------

    async def _traverse(
        self,
        *,
        client: AmazonSpApiInventoryClient,
        organization_id: UUID,
        claimed: _ClaimedRun,
    ) -> "_TraversalResult":
        pages_fetched = 0
        records_received = 0
        records_rejected = 0
        observations: list[NormalizedInventoryObservation] = []
        next_token: str | None = None
        failure_class: str | None = None
        pagination_complete = False
        retry_after_seconds: float | None = None
        max_pages = self._resolved_max_pages()
        min_interval = self._resolved_min_page_interval_seconds()

        while True:
            if pages_fetched > 0 and min_interval > 0:
                # Proactive self-throttle, defense-in-depth only — see
                # module-level `DEFAULT_MIN_PAGE_INTERVAL_SECONDS`'s own
                # docstring for why this is never the sole rate-limit
                # mechanism.
                await self._sleep(min_interval)

            lease_lost_during_request: dict[str, bool] = {"lost": False}
            renewal_task = asyncio.create_task(
                self._renew_lease_while_awaiting(
                    organization_id=organization_id,
                    run_id=claimed.run_id,
                    lease_owner=claimed.lease_owner,
                    pages_fetched=pages_fetched,
                    interval_seconds=self._cfg().sp_api_timeout_seconds,
                    lost=lease_lost_during_request,
                )
            )
            try:
                page = await client.fetch_page(
                    InventoryPageRequest(
                        marketplace_id=claimed.marketplace_id,
                        page_token=next_token,
                        details=True,
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
            except SpApiErrorEnvelopeError as exc:
                failure_class = _error_envelope_failure_class(exc.code)
                break
            except SpApiRequestFailedError:
                failure_class = "transient_request_failed"
                break
            finally:
                renewal_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal_task

            if lease_lost_during_request["lost"]:
                failure_class = "lease_lost"
                break

            pages_fetched += 1
            records_received += len(page.summaries)
            for summary in page.summaries:
                try:
                    observations.append(normalize_summary(summary))
                except InventoryNormalizationError:
                    # Per-row rejection, not whole-page/run rejection — see
                    # module docstring. Counted, never silently dropped.
                    records_rejected += 1

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

            if pages_fetched >= max_pages:
                # Amazon still has more to give (a `nextToken` was
                # returned) but we have reached ASI's own configured
                # safety bound — never a "successful partial" outcome
                # (12B.6B audit requirement: pages are accumulated
                # entirely in memory, so this discards everything
                # gathered so far, exactly like every other failure path
                # here). Terminal, not retryable — see
                # `RETRYABLE_INVENTORY_FAILURE_CLASSES`'s own docstring.
                failure_class = "pagination_bound_exceeded"
                break

        return _TraversalResult(
            pages_fetched=pages_fetched,
            records_received=records_received,
            records_rejected=records_rejected,
            pagination_complete=pagination_complete,
            failure_class=failure_class,
            observations=observations,
            retry_after_seconds=retry_after_seconds,
        )

    def _heartbeat(self, *, organization_id: UUID, run_id: UUID, lease_owner: str, pages_fetched: int) -> bool:
        with session_scope() as session:
            return AmazonIngestionRunRepository(session).heartbeat_inventory_run(
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
        """Identical guarantee to
        `AmazonListingsIngestionService._renew_lease_while_awaiting` — see
        its docstring for the full reasoning. Runs concurrently with
        exactly one in-flight `client.fetch_page()` call."""
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
                    logger.warning("amazon inventory lease renewal raised an unexpected exception run_id=%s", run_id)
                    renewed = False
                if not renewed:
                    lost["lost"] = True
                    return
        except asyncio.CancelledError:
            raise

    # --- reconcile or fail ------------------------------------------------

    def _reconcile(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        claimed: _ClaimedRun,
        traversal: "_TraversalResult",
    ) -> InventoryIngestionOutcome:
        try:
            with session_scope() as session:
                runs = AmazonIngestionRunRepository(session)
                completed = runs.complete_inventory_run(
                    organization_id,
                    claimed.run_id,
                    lease_owner=claimed.lease_owner,
                    status="succeeded",
                    records_received=traversal.records_received,
                    records_accepted=len(traversal.observations),
                    records_rejected=traversal.records_rejected,
                    pages_fetched=traversal.pages_fetched,
                    pagination_complete=True,
                    failure_class=None,
                )
                if not completed:
                    raise _ClaimFailure("lease_lost")

                # One validated, organization-scoped write boundary — see
                # `AmazonSellerInventoryRepository.reconcile_snapshot` for
                # the full atomicity/deactivation-authority contract.
                AmazonSellerInventoryRepository(session).reconcile_snapshot(
                    organization_id=organization_id,
                    marketplace_participation_id=marketplace_participation_id,
                    observations=traversal.observations,
                    ingestion_run_id=claimed.run_id,
                )
        except _ClaimFailure as exc:
            logger.warning("amazon inventory ingestion lost its lease before final reconciliation reason=%s", exc.reason)
            return InventoryIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason=exc.reason,
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                pagination_complete=traversal.pagination_complete,
            )
        except SQLAlchemyError:
            logger.warning("amazon inventory ingestion final reconciliation failed run_id=%s", claimed.run_id)
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason="reconciliation_failed",
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                pagination_complete=True,
            )
            return InventoryIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                reason="reconciliation_failed",
                ingestion_run_id=claimed.run_id,
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                pagination_complete=True,
            )
        except Exception:
            logger.warning(
                "amazon inventory ingestion final reconciliation raised an unexpected exception run_id=%s",
                claimed.run_id,
            )
            self._fail_claimed_run(
                organization_id=organization_id,
                run=claimed,
                reason="unexpected_error",
                pages_fetched=traversal.pages_fetched,
                records_received=traversal.records_received,
                pagination_complete=True,
            )
            raise

        return InventoryIngestionOutcome(
            succeeded=True,
            seller_account_id=claimed.seller_account_id,
            marketplace_participation_id=marketplace_participation_id,
            ingestion_run_id=claimed.run_id,
            pages_fetched=traversal.pages_fetched,
            records_received=traversal.records_received,
            records_accepted=len(traversal.observations),
            records_rejected=traversal.records_rejected,
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
        pagination_complete: bool = False,
    ) -> None:
        try:
            with session_scope() as session:
                AmazonIngestionRunRepository(session).complete_inventory_run(
                    organization_id,
                    run.run_id,
                    lease_owner=run.lease_owner,
                    status="failed",
                    records_received=records_received,
                    records_accepted=0,
                    records_rejected=records_received,
                    pages_fetched=pages_fetched,
                    pagination_complete=pagination_complete,
                    failure_class=reason,
                )
        except Exception:
            logger.warning("amazon inventory ingestion could not record failure run_id=%s reason=%s", run.run_id, reason)


@dataclass(frozen=True)
class _TraversalResult:
    pages_fetched: int
    records_received: int
    records_rejected: int
    pagination_complete: bool
    failure_class: str | None
    observations: list[NormalizedInventoryObservation] = field(default_factory=list)
    retry_after_seconds: float | None = None

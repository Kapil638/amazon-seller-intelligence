"""12B.4D — Orders ingestion: durable, resumable, marketplace-scoped.

Connects the read-only 12B.4C `AmazonSpApiOrdersClient` to PostgreSQL.
Read-only to Amazon, write-to-ASI. No HTTP route. No live Amazon call is
made by anything in this module by itself — it is exercised only through
an injected client/transport in tests, exactly like
`AmazonListingsIngestionService`.

Durable-job-only from the start: unlike Listings (which grew a durable job
model on top of an earlier synchronous `sync()` path retained for
compatibility), Orders has no synchronous immediate-call path at all.
`AmazonOrdersSyncTriggerService.trigger()` (`orders_sync.py`) only
enqueues; `AmazonOrdersWorker` (`orders_worker.py`) claims and calls
`process_claimed_job` here, exactly once per attempt.

Lifecycle per page (mirrors Listings' documented shape, adapted for
per-page persistence rather than accumulate-then-reconcile-once — see
below for why):

    short DB transaction:  re-validate scope (worker path only)
            |
    network page fetch, no DB session open
            |
    short DB transaction:  upsert this page's orders/items + heartbeat/counters
            |
    repeat until pagination.nextToken is absent
            |
    single final DB transaction: finalize_successful_orders_run (checkpoint advance)

**Why per-page persistence, not Listings' accumulate-then-reconcile-once:**
Listings does a full-catalog resync every run (bounded to 1000 items) and
needs a single atomic "this is the complete, authoritative snapshot"
write so deactivation-of-missing-SKUs is correct. Orders is incremental
and current-state (no deactivation concept at all — 12B.4A Phase 4 point
7): there is no such atomicity requirement, and Orders' documented
usage-plan is drastically tighter (0.0056 req/s vs. Listings' 5 req/s), so
a backfill can span many pages over a long wall-clock time. Persisting
each page as it is fetched (transactionally, immediately) is what makes
"leaves previously committed pages available after interruption" (12B.4D
Phase 3 point 17) true by construction, rather than something a crash
recovery path has to reconstruct.

**Durable pagination model (12B.4D remediation, migration `0013`):** the
originally delivered restart-from-watermark design (re-walking every page
of the current window after any interruption) was judged operationally
unacceptable for a large seller given the Orders API's documented
~178.6-second sustained request interval, and has been replaced with true
continuation-token-based resume. Three new `amazon_ingestion_runs` columns
(`orders_window_last_updated_after`, `orders_window_captured_at`,
`orders_pagination_next_token` — see the ORM model's own docstring for the
full design, including the pagination token's threat model) let a
resumed attempt pick up exactly where the last successfully committed page
left off, instead of at page one.

The search window (`lastUpdatedAfter`) is frozen once per run, by
whichever attempt first reaches `AmazonIngestionRunRepository.freeze_
orders_window_if_needed` — never recomputed from the checkpoint on a
resumed attempt, because Amazon's pinned contract requires "all other
parameters" to match the request that originally generated a
`paginationToken` for that token to remain valid
(`docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`'s Pagination
section). The marketplace half of that same frozen request shape needs no
separate storage: it is already immutably fixed by this run's own
membership rows in `amazon_ingestion_run_marketplace_participations`.

Each page's orders/items and the token needed to fetch the *next* page are
committed atomically in the same transaction (`_persist_page` →
`AmazonIngestionRunRepository.heartbeat_orders_run`). This is what makes a
crash safe in both directions: if the crash happens before that
transaction commits, the durable state still names the token that was
used to fetch the page currently in flight, so a resumed attempt safely
refetches that exact page — Orders' idempotent upserts make the replay a
no-op for anything already committed under a prior successful commit of
the same page. If the crash happens after a successful commit, the
resumed attempt correctly moves on to the next page.

Amazon's `paginationToken` has a documented 24-hour lifetime with no
documented distinguishing error code for "this token has expired." When a
resumed request using a saved token is rejected as invalid, this module
cannot tell that apart from an unrelated genuine `invalid_request` by
inspecting Amazon's response alone — see `_traverse`'s handling of
`SpApiInvalidRequestError` for the deliberately conservative heuristic
used (treat it as `pagination_token_rejected`, a classified, truthfully
recorded fallback to a page-one restart *within the still-frozen window*,
never a full window/watermark recompute) and why a false positive there is
safe (one extra harmless restart) while a false negative would
incorrectly terminalize a run that could have safely continued.

Checkpoints never advance until a run's pagination is fully complete
(`_finalize`, gated on `traversal.pagination_complete`) — this was already
true before this remediation and remains unchanged. What did change:
`_finalize` computes each covered participation's final watermark from a
fresh database aggregate over this run's own committed orders
(`AmazonSellerOrderRepository.get_max_last_updated_at_by_participation`),
not from in-memory accumulation across the traversal loop — a resumed
attempt only re-fetches pages after its resume point, so in-memory
accumulation alone would silently miss orders already committed by an
earlier, interrupted attempt of the same run.

**Multi-marketplace attribution:** one Orders run's `searchOrders` call
covers every included participation's marketplace in a single request
(12B.4A Phase 4 point 11's efficiency argument — Amazon's rate budget is
per seller-account, not per participation). Each returned order is
attributed back to the correct `marketplace_participation_id` via
`Order.sales_channel.marketplace_id` — except when the run covers exactly
one participation, in which case attribution is unambiguous regardless of
whether that field is present (`salesChannel.marketplaceId` is documented
optional; fixture `07_missing_optional_fields.json` proves it can be
absent). An order that cannot be safely attributed (multi-participation
run, missing/unmatched marketplace id) is rejected — counted, not
persisted, not treated as a whole-page failure — never guessed.

Ownership-chain validation deliberately reports one of a small set of
generic reasons for anything touching a *different* organization's data
or an internally-inconsistent scope (`scope_not_found`, `scope_inactive`,
`scope_ambiguous`, `identity_missing`, `connection_unresolvable`) — see
`_check_scope` for exactly which checks collapse into which reason.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import secrets as _secrets_module
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, SecretStr
from sqlalchemy import select

from app.amazon.connection_secrets import AmazonConnectionSecretResolver
from app.amazon.lwa_token import oauth_application_credentials
from app.amazon.orders_client import (
    AmazonSpApiOrdersClient,
    SearchOrdersPageRequest,
)
from app.amazon.orders_models import Order
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
from app.persistence.models import AmazonIngestionRun, AmazonIngestionRunMarketplaceParticipation
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonOrdersSyncCheckpointRepository,
    AmazonSellerAccountRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
    OrdersRunFinalizationIncomplete,
)

# 12B.4D: which traversal failure classes are worth rescheduling for a
# later attempt versus terminal immediately. Mirrors
# RETRYABLE_LISTINGS_FAILURE_CLASSES's own reasoning exactly (see
# listings_ingestion.py); Orders has no "record_count_inconsistent"
# concept (no documented total-result-count field exists on
# SearchOrdersResponse the way numberOfResults does for Listings).
RETRYABLE_ORDERS_FAILURE_CLASSES = frozenset(
    {"throttled", "transient_request_failed", "malformed_page", "pagination_token_rejected"}
)

# Sanitized terminal-failure name used once the durable retry budget
# (`orders_sync_max_attempts` / `orders_sync_max_total_retry_seconds`,
# both re-read fresh from the database every attempt) is exhausted for a
# retryable failure class. Classes absent from this mapping fall back to
# the shared `rate_limited` name (throttled/transient/malformed-page:
# Amazon's usage plan genuinely would not let this traversal finish in
# time). `pagination_token_rejected` gets its own truthful name —
# repeated token rejection is not throttling, and reporting it as such
# would make the failure undiagnosable from its own recorded reason.
_EXHAUSTION_REASON_BY_FAILURE_CLASS: dict[str, str] = {
    "pagination_token_rejected": "pagination_token_retry_exhausted",
}

logger = logging.getLogger(__name__)

DEFAULT_LEASE_DURATION_SECONDS = 300
# Defensive absolute backstop only — not a documented Amazon ceiling like
# Listings' 1000-item/50-page limit. Amazon documents no total-page limit
# for searchOrders; this exists purely so a genuinely pathological
# traversal (a bug, not real seller data) cannot hold a lease/attempt
# forever even if never throttled. Real attempts are expected to end via
# a 429 (see module docstring) long before this is ever reached.
MAX_PAGES_PER_ATTEMPT = 500


class _ConnectionSnapshot(BaseModel):
    """Plain, session-independent copy of exactly the fields
    `AmazonConnectionSecretResolver` needs. Never carries a token. Mirrors
    `listings_ingestion._ConnectionSnapshot` exactly — duplicated rather
    than imported to keep the two ingestion modules independent (neither
    should depend on the other's private types)."""

    model_config = ConfigDict(extra="ignore")

    organization_id: UUID
    id: UUID
    provider: str
    environment: str
    token_reference: str | None


@dataclass(frozen=True)
class OrdersIngestionOutcome:
    """Sanitized public outcome. Never carries a seller ID, marketplace
    ID, order ID, token, lease owner, or raw Amazon payload — only ASI's
    own internal UUIDs (already known to any caller that supplied them as
    scope) and truthful counters."""

    succeeded: bool
    seller_account_id: UUID
    region: str
    environment: str
    reason: str | None = None
    ingestion_run_id: UUID | None = None
    pages_fetched: int = 0
    orders_received: int = 0
    orders_accepted: int = 0
    orders_rejected: int = 0
    items_received: int = 0
    items_accepted: int = 0
    items_rejected: int = 0
    pagination_complete: bool = False


@dataclass(frozen=True)
class _ClaimedOrdersRun:
    run_id: UUID
    organization_id: UUID
    seller_account_id: UUID
    lease_owner: str
    region: str
    environment: str
    selling_partner_id: str
    connection: _ConnectionSnapshot
    marketplace_ids: tuple[str, ...]
    participation_by_marketplace_id: dict[str, UUID]
    single_participation_id: UUID | None
    participation_checkpoints: dict[UUID, datetime | None]
    # 12B.4D remediation — frozen once per run (never recomputed on a
    # resumed attempt) by `AmazonIngestionRunRepository.freeze_orders_
    # window_if_needed`. See `AmazonIngestionRun.orders_window_last_
    # updated_after`'s docstring for why this must be frozen rather than
    # re-derived from the checkpoint on every attempt.
    orders_window_last_updated_after: datetime
    orders_window_captured_at: datetime
    # Durable resume point read back from the run row at the start of
    # this attempt — `None`/`0` for a genuinely first attempt, otherwise
    # exactly what the last successfully committed page persisted.
    resume_pagination_token: str | None
    resume_pages_committed: int


class _ClaimFailure(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class OrdersClientFactoryProtocol(Protocol):
    def __call__(self, **kwargs: object) -> AmazonSpApiOrdersClient: ...


def _default_lease_owner() -> str:
    """Random, non-secret — an opaque process/attempt identifier, never a
    credential and never derived from anything Amazon-supplied."""
    return _secrets_module.token_hex(16)


@dataclass
class _TraversalResult:
    pages_fetched: int = 0
    orders_received: int = 0
    orders_accepted: int = 0
    orders_rejected: int = 0
    items_received: int = 0
    items_accepted: int = 0
    items_rejected: int = 0
    pagination_complete: bool = False
    failure_class: str | None = None
    retry_after_seconds: float | None = None
    # 12B.4D remediation: the durable continuation token to persist
    # atomically with the next page's data — seeded from the resume
    # point at attempt start, updated after every successfully persisted
    # page (see `_persist_page`), and read by the periodic in-flight
    # keepalive (`_heartbeat_only`) so that call can safely re-assert the
    # same still-current value. `None` means either "no page persisted
    # yet this run" or "pagination already complete."
    #
    # There is deliberately no per-attempt `candidate_watermarks`
    # tracking here any more (12B.4B/early-12B.4D had one): a resumed
    # attempt only re-fetches pages *after* its resume point, so any
    # in-memory accumulation here would silently miss orders already
    # committed by an earlier, interrupted attempt. `_finalize` instead
    # computes the true final watermark straight from the database
    # (`AmazonSellerOrderRepository.get_max_last_updated_at_by_
    # participation`, scoped to this run's own `last_ingestion_run_id`),
    # which is correct across any number of attempts by construction.
    pagination_token_to_persist: str | None = None


class AmazonOrdersIngestionService:
    """Fetches all safely retrievable Orders pages for one durable
    (seller_account, region, environment) scope, covering every
    participation the claimed run recorded membership for, persisting
    each page transactionally and finalizing checkpoints only after a
    fully successful traversal."""

    def __init__(
        self,
        *,
        settings: Settings | None = None,
        secret_provider: SecretProvider | None = None,
        resolver: AmazonConnectionSecretResolver | None = None,
        orders_client_factory: OrdersClientFactoryProtocol | None = None,
        transport: httpx.BaseTransport | None = None,
        lease_duration_seconds: int = DEFAULT_LEASE_DURATION_SECONDS,
        lease_owner_factory: Callable[[], str] = _default_lease_owner,
        max_pages: int = MAX_PAGES_PER_ATTEMPT,
    ) -> None:
        self._settings = settings
        self._secret_provider = secret_provider
        self._resolver = resolver
        self._orders_client_factory = orders_client_factory
        self._transport = transport
        self._lease_duration_seconds = lease_duration_seconds
        self._lease_owner_factory = lease_owner_factory
        self._max_pages = max_pages

    def __repr__(self) -> str:
        return "AmazonOrdersIngestionService()"

    def _cfg(self) -> Settings:
        return self._settings or get_settings()

    def _secrets(self) -> SecretProvider:
        return self._secret_provider or get_secret_provider(self._cfg())

    def _secret_resolver(self) -> AmazonConnectionSecretResolver:
        return self._resolver or AmazonConnectionSecretResolver(secret_provider=self._secrets())

    def _client(self, *, refresh_token: SecretStr, region: str, environment: str) -> AmazonSpApiOrdersClient:
        cfg = self._cfg()
        client_id, client_secret = oauth_application_credentials(cfg)
        factory = self._orders_client_factory or AmazonSpApiOrdersClient
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

    # --- phase A: scope validation (shared by trigger + worker paths) ------

    @staticmethod
    def _check_scope(
        session,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_ids: list[UUID],
    ) -> tuple[str, _ConnectionSnapshot, str, str, list]:
        """Returns `(selling_partner_id, connection_snapshot, region,
        environment, participations)` or raises `_ClaimFailure`. Never
        claims or mutates anything itself.

        Collapsed on purpose: missing, cross-organization, mismatched-
        ownership, and internally-inconsistent (participations spanning
        more than one connection/region) resources must be
        indistinguishable to the caller wherever the pinned contract
        does not require finer detail — see the module docstring.
        """
        seller_account_repo = AmazonSellerAccountRepository(session)
        participation_repo = AmazonMarketplaceParticipationRepository(session)
        connection_repo = AmazonConnectionRepository(session)

        seller_account = seller_account_repo.get_by_id(organization_id, seller_account_id)
        if seller_account is None:
            raise _ClaimFailure("scope_not_found")
        if seller_account.status != "active":
            raise _ClaimFailure("scope_inactive")
        selling_partner_id = (seller_account.selling_partner_id or "").strip()
        if not selling_partner_id:
            raise _ClaimFailure("identity_missing")

        if not marketplace_participation_ids:
            raise _ClaimFailure("scope_not_found")

        participations = []
        connection_id: UUID | None = None
        region: str | None = None
        for participation_id in marketplace_participation_ids:
            participation = participation_repo.get_by_id(organization_id, participation_id)
            if participation is None or participation.seller_account_id != seller_account_id:
                raise _ClaimFailure("scope_not_found")
            if not participation.is_active:
                raise _ClaimFailure("scope_inactive")
            if connection_id is None:
                connection_id = participation.connection_id
                region = participation.region
            elif participation.connection_id != connection_id or participation.region != region:
                # Every participation in one Orders run must share the same
                # connection and region — the run's own scope is a single
                # (seller_account, region, environment) tuple, not a set.
                raise _ClaimFailure("scope_ambiguous")
            participations.append(participation)

        if connection_id is None:
            raise _ClaimFailure("connection_unresolvable")
        connection = connection_repo.get_by_id(organization_id, connection_id)
        if connection is None:
            raise _ClaimFailure("connection_unresolvable")

        connection_snapshot = _ConnectionSnapshot(
            organization_id=connection.organization_id,
            id=connection.id,
            provider=connection.provider,
            environment=connection.environment,
            token_reference=connection.token_reference,
        )
        return selling_partner_id, connection_snapshot, connection.region, connection.environment, participations

    # --- 12B.4D: durable worker entry point --------------------------------

    async def process_claimed_job(self, run_id: UUID) -> OrdersIngestionOutcome:
        """Processes an Orders job the caller has *already* claimed via
        `AmazonIngestionRunRepository.claim_next_orders_job` (status is
        `started`, a lease is held). Called only by the durable worker
        (`app.amazon.orders_worker`), never by an HTTP route directly —
        the trigger route only enqueues
        (`AmazonOrdersSyncTriggerService.trigger` in `orders_sync.py`).

        Re-validates ownership/eligibility freshly with `_check_scope`
        (conditions may have changed since the job was queued) using the
        run's own recorded participation membership
        (`amazon_ingestion_run_marketplace_participations`) as the
        authoritative source of which participations to cover — never
        re-derived from any caller-supplied value at this point.
        """
        cfg = self._cfg()
        with session_scope() as session:
            run_row = session.get(AmazonIngestionRun, run_id)
            if run_row is None or run_row.run_type != "orders" or run_row.status != "started":
                return OrdersIngestionOutcome(
                    succeeded=False,
                    seller_account_id=run_row.seller_account_id if run_row else run_id,
                    region=run_row.region if run_row else "",
                    environment=run_row.environment if run_row else "",
                    reason="not_claimed",
                )
            organization_id = run_row.organization_id
            seller_account_id = run_row.seller_account_id
            region = run_row.region
            environment = run_row.environment
            lease_owner = run_row.lease_owner
            attempt_number = run_row.retry_count + 1
            first_started_at = run_row.started_at

            participation_ids = list(
                session.scalars(
                    select(AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id).where(
                        AmazonIngestionRunMarketplaceParticipation.ingestion_run_id == run_id
                    )
                )
            )

            try:
                selling_partner_id, connection_snapshot, _region, _environment, participations = (
                    self._check_scope(
                        session,
                        organization_id=organization_id,
                        seller_account_id=seller_account_id,
                        marketplace_participation_ids=participation_ids,
                    )
                )
            except _ClaimFailure as exc:
                self._fail_claimed_run(organization_id, run_id, lease_owner, reason=exc.reason)
                return OrdersIngestionOutcome(
                    succeeded=False,
                    seller_account_id=seller_account_id,
                    region=region,
                    environment=environment,
                    reason=exc.reason,
                    ingestion_run_id=run_id,
                )

            checkpoint_repo = AmazonOrdersSyncCheckpointRepository(session)
            participation_checkpoints: dict[UUID, datetime | None] = {}
            for participation in participations:
                checkpoint = checkpoint_repo.get(organization_id, participation.id)
                participation_checkpoints[participation.id] = (
                    checkpoint.synced_through_at if checkpoint is not None else None
                )

            marketplace_ids = tuple(sorted({p.marketplace_id for p in participations if (p.marketplace_id or "").strip()}))
            participation_by_marketplace_id = {
                p.marketplace_id: p.id for p in participations if (p.marketplace_id or "").strip()
            }
            single_participation_id = participations[0].id if len(participations) == 1 else None

            # 12B.4D remediation: freeze the search window once per run.
            # `freeze_orders_window_if_needed` is idempotent — a resumed
            # attempt gets back the same value a prior attempt already
            # froze, ignoring this attempt's own (redundant but harmless)
            # candidate computation. See `AmazonIngestionRun.orders_
            # window_last_updated_after`'s docstring for why this must be
            # frozen rather than recomputed from the checkpoint every time.
            request_started_at = datetime.now(UTC)
            candidate_last_updated_after = self._compute_window_start(
                participation_checkpoints=participation_checkpoints, cfg=cfg, now=request_started_at
            )
            orders_window_last_updated_after, orders_window_captured_at = AmazonIngestionRunRepository(
                session
            ).freeze_orders_window_if_needed(
                organization_id,
                run_id,
                last_updated_after=candidate_last_updated_after,
                captured_at=request_started_at,
            )
            resume_pagination_token = run_row.orders_pagination_next_token
            resume_pages_committed = run_row.pages_fetched

            claimed = _ClaimedOrdersRun(
                run_id=run_id,
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                lease_owner=lease_owner,
                region=region,
                environment=environment,
                selling_partner_id=selling_partner_id,
                connection=connection_snapshot,
                marketplace_ids=marketplace_ids,
                participation_by_marketplace_id=participation_by_marketplace_id,
                single_participation_id=single_participation_id,
                participation_checkpoints=participation_checkpoints,
                orders_window_last_updated_after=orders_window_last_updated_after,
                orders_window_captured_at=orders_window_captured_at,
                resume_pagination_token=resume_pagination_token,
                resume_pages_committed=resume_pages_committed,
            )

        try:
            refresh_token = self._secret_resolver().resolve_refresh_token(
                organization_id=claimed.organization_id,
                connection=claimed.connection,
            )
        except (InvalidSecretReferenceError, SecretNotFoundError, SecretAccessError):
            self._fail_claimed_run(claimed.organization_id, run_id, claimed.lease_owner, reason="secret_unresolvable")
            return OrdersIngestionOutcome(
                succeeded=False,
                seller_account_id=claimed.seller_account_id,
                region=claimed.region,
                environment=claimed.environment,
                reason="secret_unresolvable",
                ingestion_run_id=run_id,
            )

        try:
            client = self._client(refresh_token=refresh_token, region=claimed.region, environment=claimed.environment)
        finally:
            del refresh_token

        traversal = await self._traverse(
            client=client,
            claimed=claimed,
            last_updated_after=claimed.orders_window_last_updated_after,
            initial_pagination_token=claimed.resume_pagination_token,
            initial_pages_committed=claimed.resume_pages_committed,
        )
        del client

        if traversal.failure_class is not None:
            return self._handle_worker_failure(
                claimed=claimed,
                traversal=traversal,
                attempt_number=attempt_number,
                first_started_at=first_started_at,
                cfg=cfg,
            )

        return self._finalize(claimed=claimed, traversal=traversal)

    def _compute_window_start(
        self, *, participation_checkpoints: dict[UUID, datetime | None], cfg: Settings, now: datetime
    ) -> datetime:
        """The oldest (most conservative) checkpoint-derived start time
        across every covered participation, minus the configured overlap
        window — or the product-default lookback for a participation with
        no prior checkpoint at all (12B.4A Phase 4 point 1: a capacity
        ceiling and a product default are separate concepts). Using the
        oldest across all covered participations, rather than one window
        per participation, is what lets one shared `searchOrders` call
        cover every participation at once (12B.4A Phase 4 point 11) — a
        participation whose own checkpoint is newer than this shared start
        simply sees some already-covered orders again, which is
        idempotent and cheap.

        Only ever called to compute a *candidate* for `freeze_orders_
        window_if_needed` (12B.4D remediation) — the value that actually
        governs a run's traversal is whatever that call froze, which for
        a resumed attempt is a prior attempt's candidate, not a freshly
        recomputed one. Kept as a pure function of its arguments (no
        `_ClaimedOrdersRun` dependency) so it can be called before
        `claimed` exists, at the point in `process_claimed_job` where the
        freeze happens.
        """
        known_checkpoints = [v for v in participation_checkpoints.values() if v is not None]
        if known_checkpoints:
            oldest = min(known_checkpoints)
            return oldest - timedelta(seconds=cfg.orders_sync_checkpoint_overlap_seconds)
        return now - timedelta(days=cfg.orders_sync_default_lookback_days)

    # --- phase B: network traversal + per-page persistence ------------------

    async def _traverse(
        self,
        *,
        client: AmazonSpApiOrdersClient,
        claimed: _ClaimedOrdersRun,
        last_updated_after: datetime,
        initial_pagination_token: str | None,
        initial_pages_committed: int,
    ) -> _TraversalResult:
        result = _TraversalResult(
            pages_fetched=initial_pages_committed, pagination_token_to_persist=initial_pagination_token
        )
        seen_tokens: set[str] = set()
        next_token: str | None = initial_pagination_token
        retry_after_seconds: float | None = None

        while True:
            lease_lost_during_request: dict[str, bool] = {"lost": False}
            renewal_task = asyncio.create_task(
                self._renew_lease_while_awaiting(
                    claimed=claimed,
                    result=result,
                    interval_seconds=self._cfg().orders_sync_heartbeat_time_interval_seconds,
                    lost=lease_lost_during_request,
                )
            )
            try:
                page = await client.search_orders(
                    SearchOrdersPageRequest(
                        marketplace_ids=claimed.marketplace_ids,
                        last_updated_after=last_updated_after,
                        pagination_token=next_token,
                    )
                )
            except SpApiAuthenticationError:
                result.failure_class = "authentication_failed"
                break
            except SpApiRateLimitedError as exc:
                result.failure_class = "throttled"
                retry_after_seconds = exc.retry_after_seconds
                break
            except SpApiInvalidRequestError:
                # 12B.4D remediation: Amazon documents no distinguishing
                # error code for "this paginationToken has expired or is
                # otherwise invalid" versus any other invalid_request —
                # see the module docstring. `next_token is not None` here
                # means this specific request presented a continuation
                # token (either the resumed durable one, or one obtained
                # earlier in this same attempt); reclassify conservatively
                # as a token rejection so the retry path falls back to a
                # page-one restart *within the still-frozen window*
                # instead of terminalizing a run that could have safely
                # continued. A request with no token in play (the first
                # page of a window) cannot be a token-expiry symptom by
                # definition, so it stays a genuine terminal
                # `invalid_request`.
                result.failure_class = "pagination_token_rejected" if next_token is not None else "invalid_request"
                break
            except SpApiConfigurationError:
                result.failure_class = "configuration_error"
                break
            except SpApiParseFailedError:
                result.failure_class = "malformed_page"
                break
            except SpApiRequestFailedError:
                result.failure_class = "transient_request_failed"
                break
            finally:
                renewal_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renewal_task

            if lease_lost_during_request["lost"]:
                result.failure_class = "lease_lost"
                break

            result.pages_fetched += 1
            page_ok = self._persist_page(
                claimed=claimed, orders=page.orders, next_token=page.next_token, result=result
            )
            if not page_ok:
                result.failure_class = "lease_lost"
                break

            next_token = page.next_token
            if next_token is None:
                result.pagination_complete = True
                break
            if next_token in seen_tokens:
                result.failure_class = "cyclic_pagination_token"
                break
            seen_tokens.add(next_token)

            if result.pages_fetched >= self._max_pages:
                result.failure_class = "result_ceiling_exceeded"
                break

        result.retry_after_seconds = retry_after_seconds
        return result

    def _persist_page(
        self, *, claimed: _ClaimedOrdersRun, orders: list[Order], next_token: str | None, result: _TraversalResult
    ) -> bool:
        """Upserts every order/item on one page in a single short
        transaction, then persists the durable continuation state
        (`next_token` — the token needed to fetch the *next* page, or
        `None` once pagination is complete) atomically in that same
        transaction (12B.4D remediation). Returns `False` (never raises
        for an ordinary lease loss) if the heartbeat's compare-and-set
        fails, meaning this worker no longer verifiably holds the lease —
        the caller treats that identically to a lease lost during the
        network await."""
        with session_scope() as session:
            run_repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
            order_repo = AmazonSellerOrderRepository(session)
            item_repo = AmazonSellerOrderItemRepository(session)

            for order in orders:
                result.orders_received += 1
                result.items_received += len(order.order_items)
                participation_id = self._attribute_order(claimed, order)
                if participation_id is None:
                    result.orders_rejected += 1
                    result.items_rejected += len(order.order_items)
                    continue
                if not run_repo.is_participation_in_run(claimed.run_id, participation_id):
                    # Defense in depth: should be structurally impossible
                    # given _check_scope only ever populated
                    # participation_by_marketplace_id/single_participation_id
                    # from this run's own recorded membership — but never
                    # trust an in-memory mapping over the database's own
                    # membership fact for a write this consequential.
                    result.orders_rejected += 1
                    result.items_rejected += len(order.order_items)
                    continue

                is_business_order = "AMAZON_BUSINESS" in (order.programs or [])
                is_prime = "PRIME" in (order.programs or [])
                was_cancelled = order.fulfillment is not None and order.fulfillment.fulfillment_status == "CANCELLED"
                items_shipped_count = None
                items_unshipped_count = None
                fulfilled_totals = [
                    (item.fulfillment.quantity_fulfilled, item.fulfillment.quantity_unfulfilled)
                    for item in order.order_items
                    if item.fulfillment is not None
                ]
                if fulfilled_totals:
                    shipped_values = [v for v, _ in fulfilled_totals if v is not None]
                    unshipped_values = [v for _, v in fulfilled_totals if v is not None]
                    items_shipped_count = sum(shipped_values) if shipped_values else None
                    items_unshipped_count = sum(unshipped_values) if unshipped_values else None

                order_row = order_repo.upsert(
                    organization_id=claimed.organization_id,
                    marketplace_participation_id=participation_id,
                    amazon_order_id=order.order_id,
                    fulfillment_status=order.fulfillment.fulfillment_status if order.fulfillment else None,
                    fulfilled_by=order.fulfillment.fulfilled_by if order.fulfillment else None,
                    sales_channel_name=order.sales_channel.channel_name,
                    sales_channel_marketplace_id=order.sales_channel.marketplace_id,
                    sales_channel_marketplace_name=order.sales_channel.marketplace_name,
                    items_shipped_count=items_shipped_count,
                    items_unshipped_count=items_unshipped_count,
                    order_total_amount=(
                        order.proceeds.grand_total.amount
                        if order.proceeds is not None and order.proceeds.grand_total is not None
                        else None
                    ),
                    order_total_currency=(
                        order.proceeds.grand_total.currency_code
                        if order.proceeds is not None and order.proceeds.grand_total is not None
                        else None
                    ),
                    is_business_order=is_business_order,
                    is_prime=is_prime,
                    was_cancelled=was_cancelled,
                    amazon_created_at=order.created_time,
                    amazon_last_updated_at=order.last_updated_time,
                    last_ingestion_run_id=claimed.run_id,
                )
                result.orders_accepted += 1

                for item in order.order_items:
                    seller_sku = (item.product.seller_sku or "").strip() if item.product.seller_sku else ""
                    if not seller_sku:
                        # ItemProduct.seller_sku is documented optional;
                        # amazon_seller_order_items.seller_sku is NOT NULL.
                        # Reject this one item — never fabricate a SKU,
                        # never fail the whole page/order for it.
                        result.items_rejected += 1
                        continue
                    item_repo.upsert(
                        organization_id=claimed.organization_id,
                        marketplace_participation_id=participation_id,
                        order_id=order_row.id,
                        amazon_order_item_id=item.order_item_id,
                        seller_sku=seller_sku,
                        asin=item.product.asin,
                        item_name=item.product.title,
                        condition_type=(item.product.condition.condition_type if item.product.condition else None),
                        quantity_ordered=item.quantity_ordered,
                        quantity_fulfilled=(item.fulfillment.quantity_fulfilled if item.fulfillment else None),
                        quantity_unfulfilled=(item.fulfillment.quantity_unfulfilled if item.fulfillment else None),
                        unit_price_amount=(
                            item.product.price.unit_price.amount
                            if item.product.price is not None and item.product.price.unit_price is not None
                            else None
                        ),
                        unit_price_currency=(
                            item.product.price.unit_price.currency_code
                            if item.product.price is not None and item.product.price.unit_price is not None
                            else None
                        ),
                        item_proceeds_amount=(
                            item.proceeds.proceeds_total.amount
                            if item.proceeds is not None and item.proceeds.proceeds_total is not None
                            else None
                        ),
                        item_proceeds_currency=(
                            item.proceeds.proceeds_total.currency_code
                            if item.proceeds is not None and item.proceeds.proceeds_total is not None
                            else None
                        ),
                        last_ingestion_run_id=claimed.run_id,
                    )
                    result.items_accepted += 1

            run_repository = AmazonIngestionRunRepository(session)
            persisted = run_repository.heartbeat_orders_run(
                claimed.organization_id,
                claimed.run_id,
                lease_owner=claimed.lease_owner,
                lease_duration_seconds=self._lease_duration_seconds,
                pages_fetched=result.pages_fetched,
                orders_received=result.orders_received,
                orders_accepted=result.orders_accepted,
                orders_rejected=result.orders_rejected,
                items_received=result.items_received,
                items_accepted=result.items_accepted,
                items_rejected=result.items_rejected,
                pagination_next_token=next_token,
            )
        if persisted:
            # Only update once the transaction above has actually
            # committed — this is what `_heartbeat_only`'s periodic
            # in-flight keepalive re-asserts while the *next* page is in
            # flight, so it must reflect the just-committed value, never
            # a value from a persist attempt that turned out to fail.
            result.pagination_token_to_persist = next_token
        return persisted

    def _attribute_order(self, claimed: _ClaimedOrdersRun, order: Order) -> UUID | None:
        """Returns the `marketplace_participation_id` this order belongs
        to, or `None` if it cannot be safely attributed. Single-
        participation runs never need a marketplace-id match (see module
        docstring); multi-participation runs require an exact match
        against the run's own covered marketplaces."""
        if claimed.single_participation_id is not None:
            return claimed.single_participation_id
        marketplace_id = (order.sales_channel.marketplace_id or "").strip() if order.sales_channel.marketplace_id else ""
        if not marketplace_id:
            return None
        return claimed.participation_by_marketplace_id.get(marketplace_id)

    def _heartbeat_only(self, claimed: _ClaimedOrdersRun, result: _TraversalResult) -> bool:
        with session_scope() as session:
            return AmazonIngestionRunRepository(session).heartbeat_orders_run(
                claimed.organization_id,
                claimed.run_id,
                lease_owner=claimed.lease_owner,
                lease_duration_seconds=self._lease_duration_seconds,
                pages_fetched=result.pages_fetched,
                orders_received=result.orders_received,
                orders_accepted=result.orders_accepted,
                orders_rejected=result.orders_rejected,
                items_received=result.items_received,
                items_accepted=result.items_accepted,
                items_rejected=result.items_rejected,
                # Re-asserts whatever value `_persist_page` last actually
                # committed — no new page has been persisted since, so
                # this is an idempotent no-op write for this column.
                pagination_next_token=result.pagination_token_to_persist,
            )

    async def _renew_lease_while_awaiting(
        self,
        *,
        claimed: _ClaimedOrdersRun,
        result: _TraversalResult,
        interval_seconds: float,
        lost: dict[str, bool],
    ) -> None:
        """Runs concurrently with exactly one in-flight
        `client.search_orders()` call — see
        `listings_ingestion._renew_lease_while_awaiting`'s docstring for
        the full reasoning (identical mechanism, identical guarantee).
        More critical here than for Listings: Orders' own bounded retry
        loop inside the client can legitimately take longer given the
        tighter rate-limit budget, and the durable job's own inter-page
        wait is not bounded by this client at all — 12B.4A explicitly
        requires heartbeat renewal on every page transition for exactly
        this reason."""
        try:
            while True:
                await asyncio.sleep(interval_seconds)
                try:
                    renewed = self._heartbeat_only(claimed, result)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("amazon orders lease renewal raised an unexpected exception run_id=%s", claimed.run_id)
                    renewed = False
                if not renewed:
                    lost["lost"] = True
                    return
        except asyncio.CancelledError:
            raise

    # --- phase C: finalize or reschedule/fail --------------------------------

    def _compute_retry_delay(self, retry_after_seconds: float | None, attempt_number: int, cfg: Settings) -> float:
        """Prefer Amazon's own `Retry-After` signal when present;
        otherwise bounded exponential backoff with full jitter, anchored
        near the documented ~178.6s sustained `searchOrders` interval
        (`orders_sync_base_backoff_seconds`), not a short generic default
        — see 12B.4A's rate-limit-implications section. Either way capped
        at `orders_sync_max_backoff_seconds`."""
        if retry_after_seconds is not None and retry_after_seconds >= 0:
            delay = retry_after_seconds
        else:
            bound = cfg.orders_sync_base_backoff_seconds * (2 ** max(attempt_number - 1, 0))
            delay = random.uniform(0, bound)
        return min(delay, cfg.orders_sync_max_backoff_seconds)

    def _handle_worker_failure(
        self,
        *,
        claimed: _ClaimedOrdersRun,
        traversal: _TraversalResult,
        attempt_number: int,
        first_started_at: datetime | None,
        cfg: Settings,
    ) -> OrdersIngestionOutcome:
        """Decides retry vs. terminal for a failed traversal attempt.
        Mirrors `AmazonListingsIngestionService._handle_worker_failure`'s
        retry-budget/elapsed-time gating exactly — both `attempt_number`
        (`run_row.retry_count + 1`) and the elapsed-time base
        (`run_row.started_at`) are re-read fresh from the database on
        every attempt, never held in memory, so the budget survives any
        number of process restarts between attempts.

        Terminal-failure naming on exhaustion is *not* uniformly
        `rate_limited` (an earlier version of this method used one
        shared sanitized name for every retryable class, which
        misrepresented a repeatedly-rejected continuation token as
        throttling): throttled/transient/malformed-page exhaustion keeps
        `rate_limited` — an accurate description of "Amazon's usage plan
        would not let this traversal finish in time" — while repeated
        `pagination_token_rejected` exhaustion is reported as
        `pagination_token_retry_exhausted` (`_EXHAUSTION_REASON_BY_
        FAILURE_CLASS`) — Amazon rejected the continuation token; it did
        not throttle the request, and conflating the two would make this
        failure undiagnosable from its own recorded reason."""
        if traversal.failure_class not in RETRYABLE_ORDERS_FAILURE_CLASSES:
            self._fail_claimed_run(
                claimed.organization_id,
                claimed.run_id,
                claimed.lease_owner,
                reason=traversal.failure_class,
                traversal=traversal,
            )
            return self._outcome(claimed, traversal, succeeded=False, reason=traversal.failure_class)

        elapsed_seconds = (
            (datetime.now(UTC) - self._ensure_utc(first_started_at)).total_seconds()
            if first_started_at is not None
            else 0.0
        )
        budget_exhausted = (
            attempt_number >= cfg.orders_sync_max_attempts
            or elapsed_seconds >= cfg.orders_sync_max_total_retry_seconds
        )
        if budget_exhausted:
            exhaustion_reason = _EXHAUSTION_REASON_BY_FAILURE_CLASS.get(traversal.failure_class, "rate_limited")
            self._fail_claimed_run(
                claimed.organization_id,
                claimed.run_id,
                claimed.lease_owner,
                reason=exhaustion_reason,
                traversal=traversal,
            )
            return self._outcome(claimed, traversal, succeeded=False, reason=exhaustion_reason)

        delay = self._compute_retry_delay(traversal.retry_after_seconds, attempt_number, cfg)
        next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
        # 12B.4D remediation: the ordinary retryable classes preserve the
        # durable continuation state exactly as `_persist_page` last
        # committed it, so the next attempt resumes from that page — not
        # page one. `pagination_token_rejected` is the one deliberate,
        # classified exception: Amazon rejected a resumed token (most
        # plausibly its documented 24-hour expiry), so this attempt
        # cannot trust it any further and explicitly falls back to a
        # page-one restart *within the still-frozen window* — recorded
        # truthfully via `failure_class` on the `waiting_to_retry` row,
        # never silently.
        if traversal.failure_class == "pagination_token_rejected":
            pagination_next_token_for_retry: str | None = None
            pages_fetched_for_retry = 0
        else:
            pagination_next_token_for_retry = traversal.pagination_token_to_persist
            pages_fetched_for_retry = traversal.pages_fetched
        with session_scope() as session:
            rescheduled = AmazonIngestionRunRepository(session).reschedule_orders_run_for_retry(
                claimed.organization_id,
                claimed.run_id,
                lease_owner=claimed.lease_owner,
                next_retry_at=next_retry_at,
                failure_class=traversal.failure_class,
                pages_fetched=pages_fetched_for_retry,
                orders_received=traversal.orders_received,
                orders_accepted=traversal.orders_accepted,
                orders_rejected=traversal.orders_rejected,
                items_received=traversal.items_received,
                items_accepted=traversal.items_accepted,
                items_rejected=traversal.items_rejected,
                pagination_next_token=pagination_next_token_for_retry,
            )
        reason = "waiting_to_retry" if rescheduled else "lease_lost"
        return self._outcome(claimed, traversal, succeeded=False, reason=reason)

    @staticmethod
    def _ensure_utc(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    def _compute_final_watermarks(self, claimed: _ClaimedOrdersRun) -> dict[UUID, datetime]:
        """12B.4D remediation: the checkpoint watermark every covered
        participation may safely advance to, computed fresh from the
        database rather than from in-memory traversal state — see
        `AmazonSellerOrderRepository.get_max_last_updated_at_by_
        participation`'s docstring for why in-memory accumulation alone
        is unsafe once a run can resume mid-traversal. A participation
        that received no orders at all across every attempt of this run
        is still safe to advance to the frozen `orders_window_captured_
        at` (12B.4A Phase 4 point 9: a fully completed sweep proves
        nothing was missed up to when the request was made, not only up
        to the last order actually seen) — same reasoning as before this
        remediation, just anchored to the frozen capture time instead of
        whichever attempt happened to finish last."""
        with session_scope() as session:
            seen_max = AmazonSellerOrderRepository(session).get_max_last_updated_at_by_participation(
                claimed.organization_id, claimed.run_id
            )
        watermarks: dict[UUID, datetime] = {}
        for participation_id in claimed.participation_checkpoints:
            participation_max = seen_max.get(participation_id)
            watermarks[participation_id] = (
                max(participation_max, claimed.orders_window_captured_at)
                if participation_max is not None
                else claimed.orders_window_captured_at
            )
        return watermarks

    def _finalize(self, *, claimed: _ClaimedOrdersRun, traversal: _TraversalResult) -> OrdersIngestionOutcome:
        """Only reached when `traversal.pagination_complete` is True (no
        failure_class). Calls `finalize_successful_orders_run` — the sole
        atomic primitive that marks the run `succeeded` and advances
        every covered participation's checkpoint together. On
        `OrdersRunFinalizationIncomplete`, the transaction this method
        opened is rolled back (never committed), undoing the run's
        `succeeded` flip together with every checkpoint already advanced
        earlier in the same call — the run is then recorded as a
        sanitized terminal failure in a fresh, separate transaction (12B.4D
        Phase 3 point 16)."""
        participation_watermarks = self._compute_final_watermarks(claimed)
        try:
            with session_scope() as session:
                AmazonIngestionRunMarketplaceParticipationRepository(session).finalize_successful_orders_run(
                    organization_id=claimed.organization_id,
                    seller_account_id=claimed.seller_account_id,
                    ingestion_run_id=claimed.run_id,
                    participation_watermarks=participation_watermarks,
                )
        except OrdersRunFinalizationIncomplete:
            logger.warning("amazon orders ingestion finalization incomplete run_id=%s", claimed.run_id)
            self._fail_claimed_run(
                claimed.organization_id,
                claimed.run_id,
                claimed.lease_owner,
                reason="finalization_incomplete",
                traversal=traversal,
            )
            return self._outcome(claimed, traversal, succeeded=False, reason="finalization_incomplete")
        except Exception:
            logger.warning("amazon orders ingestion finalization raised an unexpected exception run_id=%s", claimed.run_id)
            self._fail_claimed_run(
                claimed.organization_id,
                claimed.run_id,
                claimed.lease_owner,
                reason="unexpected_error",
                traversal=traversal,
            )
            raise

        return self._outcome(claimed, traversal, succeeded=True, reason=None)

    def _outcome(
        self, claimed: _ClaimedOrdersRun, traversal: _TraversalResult, *, succeeded: bool, reason: str | None
    ) -> OrdersIngestionOutcome:
        return OrdersIngestionOutcome(
            succeeded=succeeded,
            seller_account_id=claimed.seller_account_id,
            region=claimed.region,
            environment=claimed.environment,
            reason=reason,
            ingestion_run_id=claimed.run_id,
            pages_fetched=traversal.pages_fetched,
            orders_received=traversal.orders_received,
            orders_accepted=traversal.orders_accepted,
            orders_rejected=traversal.orders_rejected,
            items_received=traversal.items_received,
            items_accepted=traversal.items_accepted,
            items_rejected=traversal.items_rejected,
            pagination_complete=traversal.pagination_complete,
        )

    def _fail_claimed_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        lease_owner: str,
        *,
        reason: str,
        traversal: _TraversalResult | None = None,
    ) -> None:
        try:
            with session_scope() as session:
                AmazonIngestionRunRepository(session).complete_orders_run_as_failed(
                    organization_id,
                    run_id,
                    lease_owner=lease_owner,
                    status="failed",
                    failure_class=reason,
                    pages_fetched=traversal.pages_fetched if traversal else 0,
                    orders_received=traversal.orders_received if traversal else 0,
                    orders_accepted=traversal.orders_accepted if traversal else 0,
                    orders_rejected=traversal.orders_rejected if traversal else 0,
                    items_received=traversal.items_received if traversal else 0,
                    items_accepted=traversal.items_accepted if traversal else 0,
                    items_rejected=traversal.items_rejected if traversal else 0,
                    pagination_complete=False,
                )
        except Exception:
            logger.warning("amazon orders ingestion could not record failure run_id=%s reason=%s", run_id, reason)

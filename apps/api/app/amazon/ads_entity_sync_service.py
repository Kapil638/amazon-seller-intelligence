"""Amazon Ads Sponsored Products hierarchy synchronization — PR B2.

Fetches one entity type's full snapshot (campaigns, ad groups, product
ads, keywords, or product targets) via bounded pagination against the
five B1 list endpoints — POST requests carrying only filter/pagination
bodies, read-only in effect despite the HTTP verb (blueprint §10/§11;
"GET-based" was an inaccurate shorthand used in this module's first
pass, corrected during final review) — resolves each item's hierarchy
parent(s) against already-persisted local rows, verifies those parents
are actually coherent with each other (not merely independently
resolvable — see `_resolve_parents`), and persists the accepted
snapshot in a single atomic transaction — never a page-by-page write.
This mirrors `ads_report_service.py`'s own fetch-then-atomically-
persist shape, with one deliberate simplification in lease recovery:
see `AmazonAdsEntitySyncRunRepository`'s own docstring for why a stale
entity-sync lease always terminalizes to `timed_out` rather than
Reporting v3's resumable/terminal split.

A run's terminal status is `'succeeded'` ONLY when every observed item
was schema-valid, in a supported state, and had a fully coherent parent
chain — any schema rejection, unsupported state, missing parent, or
mismatched parent makes the run `'partial'` instead (final review: the
first pass allowed a partial run to be marked `'succeeded'` and to
advance the checkpoint, contradicting the B2 contract). Only a clean
`'succeeded'` run ever advances the checkpoint or runs reconciliation
(reversible active/inactive tracking on the entity tables themselves —
see `_persist_snapshot`); a `'partial'` run persists whatever it safely
can but leaves both alone.

Never logs or persists a pagination token, an access/refresh token, a
raw response body, a campaign/ad-group name, a targeting expression, a
search term, or a seller identifier — `failure_detail` is always
`str(exception)` for one of this module's own typed exceptions or one
of `app.core.exceptions`'s `Ads*` exceptions (both are always
constructed with sanitized, non-secret, non-entity-shaped text), or a
fixed diagnostic string for a broad/unexpected exception (LWA token
refresh) whose own message is not trusted to be safe to persist."""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import and_, func, select, update

from app.amazon.ads_client import AdsRequestContext, AmazonAdsApiClient, EntityParseResult
from app.amazon.ads_lwa_token import refresh_ads_access_token
from app.amazon.ads_models import (
    AdsAdGroupResponse,
    AdsCampaignResponse,
    AdsKeywordResponse,
    AdsProductAdResponse,
    AdsProductTargetResponse,
)
from app.amazon.secrets import SecretNotFoundError, SecretProvider
from app.core.config import Settings
from app.core.exceptions import (
    AdsApiAuthenticationError,
    AdsApiInvalidRequestError,
    AdsApiParseFailedError,
    AdsApiRateLimitedError,
    AdsApiRequestFailedError,
)
from app.persistence.database import session_scope
from app.persistence.repositories import (
    AmazonAdsAdGroupRepository,
    AmazonAdsAdvertisedProductRepository,
    AmazonAdsCampaignRepository,
    AmazonAdsConnectionRepository,
    AmazonAdsEntitySyncCheckpointRepository,
    AmazonAdsEntitySyncRunRepository,
    AmazonAdsKeywordRepository,
    AmazonAdsProductTargetRepository,
    AmazonAdsProfileRepository,
    AmazonAdsSyncErrorRepository,
)

logger = logging.getLogger(__name__)

ENTITY_TYPES = ("campaign", "ad_group", "product_ad", "keyword", "product_target")


class _LeaseLost(Exception):
    """Internal-only concurrency-control signal — identical role to
    `app.amazon.ads_report_service`'s own `_LeaseLost` (see that
    module's docstring for the full CAS rationale). Caught exactly
    once, in `process_one_claimed_run`, and converted to
    `outcome='lease_lost'`; never allowed to propagate further."""


class _CyclicPaginationToken(Exception):
    """Raised internally when a page's `nextToken` was already seen
    earlier in the same run — never includes the token value itself in
    its message, since a pagination token must never be logged or
    persisted (blueprint §13.4 treats it as opaque; this codebase's own
    rule additionally treats it as sensitive)."""


class _ContractMismatch(Exception):
    """Raised internally when a fully-fetched run observed items but
    accepted none of them — the entity-list analogue of
    `ads_report_service.py`'s `report_row_contract_mismatch` guard."""


class _PageLimitExceeded(Exception):
    """Raised internally when pagination did not terminate within
    `ads_entity_sync_max_pages` — a bounded, visible failure rather
    than an unbounded fetch loop or a silent partial snapshot."""


@dataclass(frozen=True)
class _FetchResult:
    pages_processed: int
    items: list
    total_observed: int
    total_schema_rejected: int
    total_unsupported_state: int


@dataclass(frozen=True)
class EntitySyncOutcome:
    run_id: UUID
    outcome: str  # "succeeded" | "partial" | "retrying" | "failed" | "no_job" | "lease_lost"
    pages_processed: int = 0
    items_accepted: int = 0


def _validate_retry_after(retry_after_seconds: float | None, *, max_seconds: float) -> float | None:
    """Identical contract to `ads_report_service._validate_retry_after`
    — see that function's docstring."""
    if retry_after_seconds is None:
        return None
    try:
        value = float(retry_after_seconds)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return min(value, max_seconds)


def compute_backoff_delay(
    attempt_count: int, *, base_seconds: float, max_seconds: float, rng: "random.Random | None" = None
) -> float:
    """Identical contract to `ads_report_service.compute_backoff_delay`."""
    capped = min(max_seconds, base_seconds * (2 ** max(attempt_count, 0)))
    if rng is not None:
        return rng.uniform(0, capped)
    return random.uniform(0, capped)


@dataclass(frozen=True)
class _EntityConfig:
    list_method: str
    gate_attr: str
    repo_cls: type
    allowed_states_attr: str | None
    parent_kind: str | None  # None | "campaign" | "campaign_and_ad_group"
    fields: Any  # callable(dto) -> dict


def _campaign_fields(dto: AdsCampaignResponse) -> dict:
    daily_budget = dto.budget.budget if dto.budget is not None else None
    return {
        "external_campaign_id": dto.campaign_id,
        "name": dto.name,
        "state": dto.state,
        "targeting_type": dto.targeting_type,
        "daily_budget": daily_budget,
        "currency_code": None,
        "start_date": dto.start_date,
        "end_date": dto.end_date,
        "portfolio_id": dto.portfolio_id,
    }


def _ad_group_fields(dto: AdsAdGroupResponse) -> dict:
    return {
        "external_ad_group_id": dto.ad_group_id,
        "name": dto.name,
        "state": dto.state,
        "default_bid": dto.default_bid,
        "currency_code": None,
    }


def _product_ad_fields(dto: AdsProductAdResponse) -> dict:
    return {
        "external_ad_id": dto.ad_id,
        "asin": dto.asin,
        "sku": dto.sku,
        "state": dto.state,
    }


def _keyword_fields(dto: AdsKeywordResponse) -> dict:
    return {
        "external_keyword_id": dto.keyword_id,
        "keyword_text": dto.keyword_text,
        "match_type": dto.match_type,
        "state": dto.state,
        "bid": dto.bid,
        "currency_code": None,
    }


def _product_target_fields(dto: AdsProductTargetResponse) -> dict:
    return {
        "external_target_id": dto.target_id,
        "expression_type": dto.expression_type,
        "expression": dto.expression,
        "state": dto.state,
        "bid": dto.bid,
        "currency_code": None,
    }


_ENTITY_CONFIG: dict[str, _EntityConfig] = {
    "campaign": _EntityConfig(
        list_method="list_campaigns",
        gate_attr="ads_entity_sync_campaigns_enabled",
        repo_cls=AmazonAdsCampaignRepository,
        allowed_states_attr=None,
        parent_kind=None,
        fields=_campaign_fields,
    ),
    "ad_group": _EntityConfig(
        list_method="list_ad_groups",
        gate_attr="ads_entity_sync_ad_groups_enabled",
        repo_cls=AmazonAdsAdGroupRepository,
        allowed_states_attr=None,
        parent_kind="campaign",
        fields=_ad_group_fields,
    ),
    "product_ad": _EntityConfig(
        list_method="list_product_ads",
        gate_attr="ads_entity_sync_product_ads_enabled",
        repo_cls=AmazonAdsAdvertisedProductRepository,
        allowed_states_attr=None,
        parent_kind="campaign_and_ad_group",
        fields=_product_ad_fields,
    ),
    "keyword": _EntityConfig(
        list_method="list_keywords",
        gate_attr="ads_entity_sync_keywords_enabled",
        repo_cls=AmazonAdsKeywordRepository,
        allowed_states_attr=None,
        parent_kind="campaign_and_ad_group",
        fields=_keyword_fields,
    ),
    "product_target": _EntityConfig(
        list_method="list_product_targets",
        gate_attr="ads_entity_sync_product_targets_enabled",
        repo_cls=AmazonAdsProductTargetRepository,
        allowed_states_attr=None,
        parent_kind="campaign_and_ad_group",
        fields=_product_target_fields,
    ),
}


class AmazonAdsEntitySyncService:
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

    def enqueue_sync_request(self, *, organization_id: UUID, ads_profile_id: UUID, entity_type: str) -> UUID:
        """Validates that `ads_profile_id` actually belongs to
        `organization_id` before creating anything — resolves its
        connection here too, so the resulting run's full scope
        `(organization_id, ads_connection_id, ads_profile_id,
        entity_type)` is established at creation time, never inferred
        or trusted from a caller later. Raises `ValueError` for an
        unknown entity type or an unowned/nonexistent profile; creates
        no row in either case."""
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"Unknown Amazon Ads entity type: {entity_type!r}")
        with session_scope() as session:
            profile = AmazonAdsProfileRepository(session).get_owned(organization_id, ads_profile_id)
            if profile is None:
                raise ValueError(
                    f"Amazon Ads profile {ads_profile_id} does not belong to organization {organization_id}."
                )
            run = AmazonAdsEntitySyncRunRepository(session).enqueue(
                organization_id, profile.connection_id, ads_profile_id, entity_type=entity_type
            )
            return run.id

    async def _renew_lease_or_raise(self, run_id: UUID) -> None:
        """Fenced pre-call gate — identical role to
        `ads_report_service.AmazonAdsReportService._renew_lease_or_raise`.
        Called immediately before every external call this service
        makes (LWA refresh, each page fetch), so a stale worker never
        issues an Amazon HTTP call it can no longer persist the result
        of."""
        with session_scope() as session:
            if not AmazonAdsEntitySyncRunRepository(session).heartbeat(
                run_id,
                lease_owner=self._lease_owner,
                lease_duration_seconds=self._cfg.ads_entity_sync_lease_duration_seconds,
            ):
                raise _LeaseLost()

    async def process_one_claimed_run(self) -> EntitySyncOutcome:
        with session_scope() as session:
            run = AmazonAdsEntitySyncRunRepository(session).claim_next_sync_run(
                lease_owner=self._lease_owner,
                lease_duration_seconds=self._cfg.ads_entity_sync_lease_duration_seconds,
                max_global_active=self._cfg.ads_entity_sync_max_global_concurrent_runs,
            )
            if run is None:
                return EntitySyncOutcome(run_id=UUID(int=0), outcome="no_job")
            run_id = run.id
            organization_id = run.organization_id
            ads_connection_id = run.ads_connection_id
            ads_profile_id = run.ads_profile_id
            entity_type = run.entity_type
            attempt_count = run.attempt_count

        try:
            return await self._process_claimed(
                run_id, organization_id, ads_connection_id, ads_profile_id, entity_type, attempt_count
            )
        except _LeaseLost:
            logger.info(
                "ads entity sync run lost its lease to another worker before this attempt finished run_id=%s",
                run_id,
            )
            return EntitySyncOutcome(run_id=run_id, outcome="lease_lost")

    async def _process_claimed(
        self,
        run_id: UUID,
        organization_id: UUID,
        ads_connection_id: UUID,
        ads_profile_id: UUID,
        entity_type: str,
        attempt_count: int,
    ) -> EntitySyncOutcome:
        config = _ENTITY_CONFIG[entity_type]

        # Per-entity-type confidence/activation gate — checked
        # immediately after claim, before any HTTP call. Disabled is a
        # permanent, deterministic condition for this run (retrying
        # would reproduce it identically), so it fails rather than
        # retries.
        if not getattr(self._cfg, config.gate_attr):
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_disabled_for_entity_type",
                detail=f"Synchronization for entity_type={entity_type!r} is disabled by configuration.",
            )

        with session_scope() as session:
            profile = AmazonAdsProfileRepository(session).get_owned(organization_id, ads_profile_id)
            if profile is None:
                if not AmazonAdsEntitySyncRunRepository(session).mark_failed(
                    run_id, lease_owner=self._lease_owner,
                    failure_class="profile_missing", failure_detail="Advertiser profile no longer exists.",
                ):
                    raise _LeaseLost()
                return EntitySyncOutcome(run_id=run_id, outcome="failed")
            connection = AmazonAdsConnectionRepository(session).get_by_id(organization_id, profile.connection_id)
            if connection is None or not connection.token_reference:
                if not AmazonAdsEntitySyncRunRepository(session).mark_failed(
                    run_id, lease_owner=self._lease_owner,
                    failure_class="connection_missing", failure_detail="Amazon Ads connection is not authorized.",
                ):
                    raise _LeaseLost()
                return EntitySyncOutcome(run_id=run_id, outcome="failed")
            token_reference = connection.token_reference
            region = profile.region
            profile_id_str = profile.profile_id

        try:
            refresh_token = self._secrets.get_secret(token_reference)
        except SecretNotFoundError:
            with session_scope() as session:
                if not AmazonAdsEntitySyncRunRepository(session).mark_failed(
                    run_id, lease_owner=self._lease_owner,
                    failure_class="secret_missing", failure_detail="Stored Ads refresh token was not found.",
                ):
                    raise _LeaseLost()
            return EntitySyncOutcome(run_id=run_id, outcome="failed")

        await self._renew_lease_or_raise(run_id)
        try:
            access_token_response = await refresh_ads_access_token(
                client_id=self._cfg.ads_lwa_client_id,
                client_secret=self._cfg.ads_lwa_client_secret,
                refresh_token=refresh_token,
                token_url=self._cfg.ads_lwa_token_url,
                timeout_seconds=self._cfg.ads_api_timeout_seconds,
            )
        except Exception:
            # Deliberately NOT `str(exc)` — an unexpected/broad exception
            # from the underlying HTTP/LWA client is not this codebase's
            # own sanitized exception type, so its message is not trusted
            # to be free of secret- or request-shaped text. A fixed
            # diagnostic string is persisted instead; the real exception
            # is available in-process only, never written to failure_detail,
            # amazon_ads_sync_errors, or any log line.
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="token_refresh_failed",
                detail="Amazon Ads LWA token refresh failed (see run_id for correlation).",
            )

        ctx = AdsRequestContext(
            access_token=access_token_response.access_token,
            client_id=self._cfg.ads_lwa_client_id.get_secret_value() if self._cfg.ads_lwa_client_id else "",
            region=region,
            profile_id=profile_id_str,
            correlation_id=str(run_id),
        )

        try:
            fetch_result = await self._fetch_all_pages(run_id, ctx, config)
        except _PageLimitExceeded:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_page_limit_exceeded",
                detail=f"Exceeded ads_entity_sync_max_pages={self._cfg.ads_entity_sync_max_pages} without pagination completing.",
            )
        except _CyclicPaginationToken:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_cyclic_pagination_token",
                detail="A pagination token repeated within one run — aborting rather than looping forever.",
            )
        except _ContractMismatch as exc:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_contract_mismatch", detail=str(exc),
            )
        except AdsApiAuthenticationError as exc:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_authentication_failed", detail=str(exc),
            )
        except AdsApiInvalidRequestError as exc:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_invalid_request", detail=str(exc),
            )
        except AdsApiParseFailedError as exc:
            return await self._fail_permanently(
                run_id, organization_id, ads_profile_id,
                failure_class="entity_sync_envelope_contract_mismatch", detail=str(exc),
            )
        except AdsApiRateLimitedError as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="entity_sync_rate_limited", detail=str(exc),
                retry_after_seconds=exc.retry_after_seconds,
            )
        except AdsApiRequestFailedError as exc:
            return await self._retry_or_fail(
                run_id, organization_id, ads_profile_id, attempt_count,
                failure_class="entity_sync_request_failed", detail=str(exc),
            )

        return await self._persist_snapshot(
            run_id, organization_id, ads_connection_id, ads_profile_id, entity_type, config, fetch_result
        )

    async def _fetch_all_pages(
        self, run_id: UUID, ctx: AdsRequestContext, config: _EntityConfig
    ) -> _FetchResult:
        """Fetches every page of one entity type's snapshot, accumulating
        accepted items in memory — no database transaction is held open
        across these HTTP calls (mirrors `ads_report_service._poll_
        until_terminal`'s own "never hold a transaction across an
        external wait" rule). Raises `_ContractMismatch` if the fully-
        fetched run observed items but accepted none, `_PageLimitExceeded`
        if pagination did not terminate within the configured bound, and
        `_CyclicPaginationToken` if a token repeats — never logs or
        persists the token value itself in either case."""
        next_token: str | None = None
        seen_tokens: set[str] = set()
        pages_processed = 0
        accumulated: list = []
        total_observed = 0
        total_accepted = 0
        total_schema_rejected = 0
        total_unsupported_state = 0

        list_method = getattr(self._client, config.list_method)
        while True:
            if pages_processed >= self._cfg.ads_entity_sync_max_pages:
                raise _PageLimitExceeded()

            await self._renew_lease_or_raise(run_id)
            result: EntityParseResult = await list_method(
                ctx, next_token=next_token, page_size=self._cfg.ads_entity_list_page_size
            )
            pages_processed += 1
            total_observed += result.total_items
            total_accepted += result.accepted_items
            total_schema_rejected += result.schema_rejected_items
            total_unsupported_state += result.unsupported_state_items
            accumulated.extend(result.items)

            if result.next_token is None:
                break
            if result.next_token in seen_tokens:
                raise _CyclicPaginationToken()
            seen_tokens.add(result.next_token)
            next_token = result.next_token

        if total_observed > 0 and total_accepted == 0:
            raise _ContractMismatch(
                f"Amazon returned total={total_observed} accepted=0 across "
                f"{pages_processed} page(s) — entity-list contract mismatch."
            )
        return _FetchResult(
            pages_processed=pages_processed,
            items=accumulated,
            total_observed=total_observed,
            total_schema_rejected=total_schema_rejected,
            total_unsupported_state=total_unsupported_state,
        )

    def _resolve_parents(
        self, session, organization_id: UUID, ads_profile_id: UUID, config: _EntityConfig, dto
    ) -> str:
        """Returns `"ok"`, `"missing"`, or `"mismatched"` — never persists
        anything itself. `"missing"`: at least one referenced parent does
        not exist locally at all (ordinary and expected before that
        parent entity type has ever been synced). `"mismatched"`: BOTH
        parents were found independently, but the resolved ad group does
        not actually belong to the resolved campaign — final review's
        Blocker 1: the first pass resolved `campaign_row` and
        `ad_group_row` independently and never checked this, so a
        product ad/keyword/target could be persisted under a campaign it
        was never actually part of. Every check here is scoped
        explicitly by `ads_profile_id` (the tenant/profile isolation
        boundary this codebase already establishes) and defensively
        re-asserts `organization_id`, even though the repository lookups
        already imply it, per the review's explicit request."""
        if config.parent_kind is None:
            return "ok"

        campaign_repo = AmazonAdsCampaignRepository(session)
        campaign_row = campaign_repo.get_by_external_id(ads_profile_id, dto.campaign_id)
        if campaign_row is None:
            return "missing"
        if campaign_row.organization_id != organization_id or campaign_row.ads_profile_id != ads_profile_id:
            return "missing"  # defense-in-depth; repository scoping already prevents this
        if campaign_row.external_campaign_id != dto.campaign_id:
            return "missing"  # defense-in-depth; fetched by this exact value

        if config.parent_kind == "campaign":
            return "ok"

        ad_group_repo = AmazonAdsAdGroupRepository(session)
        ad_group_row = ad_group_repo.get_by_external_id(ads_profile_id, dto.ad_group_id)
        if ad_group_row is None:
            return "missing"
        if ad_group_row.organization_id != organization_id or ad_group_row.ads_profile_id != ads_profile_id:
            return "missing"  # defense-in-depth
        if ad_group_row.external_ad_group_id != dto.ad_group_id:
            return "missing"  # defense-in-depth

        # The check the first pass was missing: both parents resolved
        # independently is not proof they belong together.
        if ad_group_row.ads_campaign_id != campaign_row.id:
            return "mismatched"

        return "ok"

    def _upsert_with_parents(
        self, session, organization_id: UUID, ads_profile_id: UUID, config: _EntityConfig, dto
    ):
        """Called only after `_resolve_parents` has returned `"ok"` for
        this exact `dto` — re-resolves the same rows (cheap, indexed
        lookups; keeps this method free of any trust in a caller having
        checked correctly, since it independently reproduces the
        resolution rather than accepting pre-fetched rows as arguments)."""
        data = config.fields(dto)
        if config.parent_kind is None:
            return config.repo_cls(session).upsert(organization_id, ads_profile_id, data)

        campaign_row = AmazonAdsCampaignRepository(session).get_by_external_id(ads_profile_id, dto.campaign_id)
        if config.parent_kind == "campaign":
            return config.repo_cls(session).upsert(organization_id, ads_profile_id, campaign_row.id, data)

        ad_group_row = AmazonAdsAdGroupRepository(session).get_by_external_id(ads_profile_id, dto.ad_group_id)
        return config.repo_cls(session).upsert(
            organization_id, ads_profile_id, campaign_row.id, ad_group_row.id, data
        )

    async def _persist_snapshot(
        self,
        run_id: UUID,
        organization_id: UUID,
        ads_connection_id: UUID,
        ads_profile_id: UUID,
        entity_type: str,
        config: _EntityConfig,
        fetch_result: _FetchResult,
    ) -> EntitySyncOutcome:
        """The one and only database transaction this service performs
        per run: coherent-parent-checked, idempotent upserts for every
        eligible item, a clean/partial terminal-status decision, fenced
        completion, and — ONLY on a clean run — reversible reconciliation
        and checkpoint advance. All atomic: if this worker's lease was
        lost at any point, the fenced `mark_succeeded`/`mark_partial`
        below affects zero rows and `_LeaseLost` rolls back the entire
        transaction, so a stale worker never persists partial hierarchy
        data, never deactivates/reactivates anything, and never advances
        the checkpoint (mirrors `ads_report_service._download_and_ingest`'s
        own guarantee)."""
        items_missing_parent = 0
        items_mismatched_parent = 0
        items_persisted = 0
        touched_ids: list = []

        with session_scope() as session:
            for dto in fetch_result.items:
                resolution = self._resolve_parents(session, organization_id, ads_profile_id, config, dto)
                if resolution == "missing":
                    items_missing_parent += 1
                    continue
                if resolution == "mismatched":
                    items_mismatched_parent += 1
                    continue
                row = self._upsert_with_parents(session, organization_id, ads_profile_id, config, dto)
                touched_ids.append(row.id)
                items_persisted += 1

            if items_missing_parent:
                AmazonAdsSyncErrorRepository(session).record(
                    organization_id, ads_profile_id,
                    error_code="entity_sync_missing_parent",
                    error_message=f"entity_type={entity_type} missing_parent_count={items_missing_parent}",
                )
            if items_mismatched_parent:
                AmazonAdsSyncErrorRepository(session).record(
                    organization_id, ads_profile_id,
                    error_code="entity_sync_mismatched_parent",
                    error_message=f"entity_type={entity_type} mismatched_parent_count={items_mismatched_parent}",
                )

            # Clean success requires EVERY observed item to have been
            # schema-valid, in a supported state, AND coherently parented
            # — final review's Blocker 2: the first pass marked the run
            # 'succeeded' (and advanced the checkpoint) regardless of
            # rejection/missing/mismatched counts. Any of these nonzero
            # makes the run 'partial': persisted (what could safely be
            # persisted was), but never a clean success, and — critically
            # — reconciliation and the checkpoint are both skipped below.
            is_clean = (
                fetch_result.total_schema_rejected == 0
                and fetch_result.total_unsupported_state == 0
                and items_missing_parent == 0
                and items_mismatched_parent == 0
            )

            if not is_clean:
                if not AmazonAdsEntitySyncRunRepository(session).mark_partial(
                    run_id,
                    lease_owner=self._lease_owner,
                    pages_processed=fetch_result.pages_processed,
                    items_observed=fetch_result.total_observed,
                    items_accepted=items_persisted,
                    items_schema_rejected=fetch_result.total_schema_rejected,
                    items_unsupported_state=fetch_result.total_unsupported_state,
                    items_missing_parent=items_missing_parent,
                    items_mismatched_parent=items_mismatched_parent,
                ):
                    raise _LeaseLost()
                logger.info(
                    "ads entity sync partial run_id=%s entity_type=%s pages=%s accepted=%s "
                    "missing_parent=%s mismatched_parent=%s",
                    run_id, entity_type, fetch_result.pages_processed, items_persisted,
                    items_missing_parent, items_mismatched_parent,
                )
                return EntitySyncOutcome(
                    run_id=run_id,
                    outcome="partial",
                    pages_processed=fetch_result.pages_processed,
                    items_accepted=items_persisted,
                )

            # Reversible snapshot-activity reconciliation — final
            # review's Blocker 3: the first pass only counted untouched
            # rows (observability), never actually reactivated or
            # deactivated anything. This never mutates or deletes
            # Amazon's own `state` column; `is_active` is this
            # application's own snapshot-membership signal. Runs ONLY
            # here, inside the clean-success branch — never for a
            # disabled gate, a partial result, a pagination failure, a
            # lease loss, a retry, or a contract mismatch.
            model = config.repo_cls.model
            items_reactivated = 0
            if touched_ids:
                items_reactivated = session.execute(
                    update(model)
                    .where(model.id.in_(touched_ids), model.is_active.is_(False))
                    .values(is_active=True, last_seen_entity_sync_run_id=run_id)
                ).rowcount
                session.execute(
                    update(model)
                    .where(model.id.in_(touched_ids), model.is_active.is_(True))
                    .values(last_seen_entity_sync_run_id=run_id)
                )

            deactivate_conditions = [model.ads_profile_id == ads_profile_id, model.is_active.is_(True)]
            if touched_ids:
                deactivate_conditions.append(model.id.notin_(touched_ids))
            items_deactivated = session.execute(
                update(model).where(and_(*deactivate_conditions)).values(is_active=False)
            ).rowcount

            if not AmazonAdsEntitySyncRunRepository(session).mark_succeeded(
                run_id,
                lease_owner=self._lease_owner,
                pages_processed=fetch_result.pages_processed,
                items_observed=fetch_result.total_observed,
                items_accepted=items_persisted,
                items_schema_rejected=0,
                items_unsupported_state=0,
                items_missing_parent=0,
                items_mismatched_parent=0,
                items_deactivated=int(items_deactivated),
                items_reactivated=int(items_reactivated),
            ):
                raise _LeaseLost()

            AmazonAdsEntitySyncCheckpointRepository(session).advance(
                organization_id, ads_connection_id, ads_profile_id,
                entity_type=entity_type, synced_at=datetime.now(UTC), run_id=run_id,
            )

        logger.info(
            "ads entity sync succeeded run_id=%s entity_type=%s pages=%s accepted=%s "
            "deactivated=%s reactivated=%s",
            run_id, entity_type, fetch_result.pages_processed, items_persisted,
            items_deactivated, items_reactivated,
        )
        return EntitySyncOutcome(
            run_id=run_id,
            outcome="succeeded",
            pages_processed=fetch_result.pages_processed,
            items_accepted=items_persisted,
        )

    async def _fail_permanently(
        self, run_id: UUID, organization_id: UUID, ads_profile_id: UUID, *, failure_class: str, detail: str
    ) -> EntitySyncOutcome:
        with session_scope() as session:
            if not AmazonAdsEntitySyncRunRepository(session).mark_failed(
                run_id, lease_owner=self._lease_owner, failure_class=failure_class, failure_detail=detail
            ):
                raise _LeaseLost()
            AmazonAdsSyncErrorRepository(session).record(
                organization_id, ads_profile_id, error_code=failure_class, error_message=detail
            )
        return EntitySyncOutcome(run_id=run_id, outcome="failed")

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
    ) -> EntitySyncOutcome:
        with session_scope() as session:
            run_repo = AmazonAdsEntitySyncRunRepository(session)
            if attempt_count >= self._cfg.ads_entity_sync_max_attempts:
                if not run_repo.mark_failed(
                    run_id, lease_owner=self._lease_owner, failure_class=failure_class, failure_detail=detail
                ):
                    raise _LeaseLost()
                outcome = "failed"
            else:
                bounded = _validate_retry_after(
                    retry_after_seconds, max_seconds=self._cfg.ads_report_retry_after_max_seconds
                )
                if bounded is not None:
                    delay, delay_source = bounded, "retry_after"
                else:
                    delay, delay_source = (
                        compute_backoff_delay(
                            attempt_count,
                            base_seconds=self._cfg.ads_entity_sync_retry_base_seconds,
                            max_seconds=self._cfg.ads_entity_sync_retry_max_seconds,
                        ),
                        "backoff",
                    )
                next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
                annotated_detail = f"{detail} [retry_delay_source={delay_source} retry_delay_seconds={delay:.2f}]"
                if not run_repo.mark_retry(
                    run_id, lease_owner=self._lease_owner, next_retry_at=next_retry_at,
                    failure_class=failure_class, failure_detail=annotated_detail,
                ):
                    raise _LeaseLost()
                outcome = "retrying"
            AmazonAdsSyncErrorRepository(session).record(
                organization_id, ads_profile_id, error_code=failure_class, error_message=detail
            )
        return EntitySyncOutcome(run_id=run_id, outcome=outcome)

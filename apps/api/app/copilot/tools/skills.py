"""12B.5A — Listings + Orders skill Copilot tools.

Five narrow, read-only tools, each wrapping exactly one deterministic
evidence service from `app.copilot.skills`. Every handler:

- takes `organization_id` from nowhere in its input (there is no such
  field on any of the five input schemas) — it always comes from
  `current_organization_id()`, transitively, inside the evidence
  service's own calls into `AmazonListingsReadService`/
  `AmazonOrdersReadService`;
- re-validates marketplace-participation ownership inside those same
  read services (never trusts the caller's UUID alone);
- never generates SQL — every query is a fixed, parameterized call
  already reviewed as part of the domain read services;
- is registered with `estimated_provider_cost=COST_NONE` (no Amazon or
  OpenAI call happens here), so none of the five ever triggers the
  confirmation gate;
- cannot trigger an Orders/Listings synchronization or call Amazon —
  nothing in this module or the services it calls ever does either.

A foreign or nonexistent `marketplace_participation_id` surfaces as the
exact same sanitized `AmazonListingsParticipationNotFoundError` the read
services already raise for every other caller — translated here into a
`ToolValidationError` so the orchestrator's existing error handling
covers it without a new branch.
"""

from __future__ import annotations

from typing import Callable
from uuid import UUID

from app.amazon.listings_read import AmazonListingsReadService
from app.amazon.orders_read import AmazonOrdersReadService
from app.copilot.budget import COST_NONE
from app.copilot.evidence import EvidenceEnvelope, envelope
from app.copilot.exceptions import ToolValidationError
from app.copilot.registry import ToolDefinition, ToolRegistry
from app.copilot.schemas import (
    AnalyzeOrderTrendsInput,
    DetectCancellationAnomaliesInput,
    InvestigateNonBuyableListingInput,
    PrioritizeListingHealthInput,
    RankListingRiskByOrderExposureInput,
)
from app.copilot.skills.cache import InProcessSkillCache, SingleFlight, cached_evidence_lookup, evidence_cache_key
from app.copilot.skills.cancellations import CancellationAnomalyEvidenceService
from app.copilot.skills.contracts import (
    SKILL_VERSIONS,
    SkillEvidence,
    listings_evidence_version,
    orders_evidence_version,
    skill_evidence_to_claims,
)
from app.copilot.skills.listing_health import ListingHealthEvidenceService
from app.copilot.skills.listing_risk import ListingRiskEvidenceService
from app.copilot.skills.non_buyable import ListingNotFoundForInvestigationError, NonBuyableListingEvidenceService
from app.copilot.skills.order_trends import OrderTrendEvidenceService
from app.core.exceptions import AmazonListingsParticipationNotFoundError
from app.persistence.database import current_organization_id

_FOREIGN_SCOPE_MESSAGE = "This marketplace is not available for this Copilot session."

# 12B.5B — Layer A (evidence cache), process-wide for this API process.
# See `app/copilot/skills/cache.py`'s module docstring for the multi-
# replica limitation and the production shared-cache requirement.
_EVIDENCE_CACHE = InProcessSkillCache()
_EVIDENCE_SINGLE_FLIGHT = SingleFlight()


def _evidence_versions(
    marketplace_participation_id: UUID, *, listings: bool, orders: bool
) -> tuple[str | None, str | None]:
    """Cheap, read-only freshness lookup used only to build/validate a
    cache key — never the expensive full listing/order scan a cache hit
    is meant to avoid. Ownership is re-validated here exactly like every
    other read-service call (never trusts the caller's UUID alone);
    `_guarded` translates a foreign/nonexistent scope the same way every
    other call site in this module already does."""
    listings_version = None
    orders_version = None
    if listings:
        summary = _guarded(lambda: AmazonListingsReadService().get_summary(marketplace_participation_id))
        listings_version = listings_evidence_version(summary.sync)
    if orders:
        try:
            summary = AmazonOrdersReadService().get_summary(marketplace_participation_id)
            orders_version = orders_evidence_version(summary.sync)
        except AmazonListingsParticipationNotFoundError:
            raise
        except Exception:
            # Orders may legitimately be unavailable (never synced) for a
            # participation that only has Listings — matches every
            # skill's own graceful-degradation behavior for this exact
            # case (see e.g. listing_health.py's orders_freshness try/
            # except).
            orders_version = orders_evidence_version(None)
    return listings_version, orders_version


def _cached_evidence(
    *,
    skill_id: str,
    skill_version: str,
    marketplace_participation_id: UUID,
    params: dict,
    needs_listings: bool,
    needs_orders: bool,
    compute: Callable[[], SkillEvidence],
    force_refresh: bool = False,
) -> SkillEvidence:
    listings_version, orders_version = _evidence_versions(
        marketplace_participation_id, listings=needs_listings, orders=needs_orders
    )
    key = evidence_cache_key(
        organization_id=current_organization_id(),
        marketplace_participation_ids=[marketplace_participation_id],
        skill_id=skill_id,
        skill_version=skill_version,
        params=params,
        listings_evidence_version=listings_version,
        orders_evidence_version=orders_version,
    )
    return cached_evidence_lookup(
        _EVIDENCE_CACHE, _EVIDENCE_SINGLE_FLIGHT, key=key, compute=compute, force_refresh=force_refresh
    )


def register(
    registry: ToolRegistry,
    *,
    listing_health: ListingHealthEvidenceService | None = None,
    non_buyable: NonBuyableListingEvidenceService | None = None,
    order_trends: OrderTrendEvidenceService | None = None,
    cancellations: CancellationAnomalyEvidenceService | None = None,
    listing_risk: ListingRiskEvidenceService | None = None,
) -> None:
    health_service = listing_health or ListingHealthEvidenceService()
    non_buyable_service = non_buyable or NonBuyableListingEvidenceService()
    trend_service = order_trends or OrderTrendEvidenceService()
    cancellation_service = cancellations or CancellationAnomalyEvidenceService()
    risk_service = listing_risk or ListingRiskEvidenceService()

    registry.register(
        ToolDefinition(
            name="prioritize_listing_health",
            description=(
                "Rank this marketplace's Listings by how urgently they need attention, using Amazon's "
                "own issue severity, buyability, discoverability, active state, and recent verified order "
                "exposure. Deterministic ranking, not a prediction."
            ),
            input_schema=PrioritizeListingHealthInput,
            handler=lambda payload: _prioritize_listing_health(health_service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="investigate_non_buyable_listing",
            description=(
                "Investigate one Listing (by seller SKU or ASIN) that is not buyable: its active/buyable/"
                "discoverable state, Amazon's own issues by severity, and recent order/unit evidence."
            ),
            input_schema=InvestigateNonBuyableListingInput,
            handler=lambda payload: _investigate_non_buyable_listing(non_buyable_service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="analyze_order_trends",
            description=(
                "Orders, units, order value by currency, and fulfillment-status distribution for this "
                "marketplace over a period, compared with the immediately preceding equal-length period."
            ),
            input_schema=AnalyzeOrderTrendsInput,
            handler=lambda payload: _analyze_order_trends(trend_service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="detect_cancellation_anomalies",
            description=(
                "Cancellation count and rate for this marketplace over a period, compared with the "
                "preceding period, labeled anomalous only when a documented minimum-volume and "
                "threshold rule is met."
            ),
            input_schema=DetectCancellationAnomaliesInput,
            handler=lambda payload: _detect_cancellation_anomalies(cancellation_service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="rank_listing_risk_by_order_exposure",
            description=(
                "Listings with an open ERROR/WARNING issue, joined to their own recent order activity by "
                "seller SKU within this marketplace, with the order value already observed for them."
            ),
            input_schema=RankListingRiskByOrderExposureInput,
            handler=lambda payload: _rank_listing_risk_by_order_exposure(risk_service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )


def _prioritize_listing_health(
    service: ListingHealthEvidenceService, payload: PrioritizeListingHealthInput
) -> EvidenceEnvelope:
    skill_id = "listing_health_prioritizer"
    evidence = _cached_evidence(
        skill_id=skill_id,
        skill_version=SKILL_VERSIONS[skill_id],
        marketplace_participation_id=payload.marketplace_participation_id,
        params={"period_days": payload.period_days, "limit": payload.limit},
        needs_listings=True,
        needs_orders=True,
        force_refresh=payload.force_refresh,
        compute=lambda: _guarded(
            lambda: service.evaluate(
                payload.marketplace_participation_id, period_days=payload.period_days, limit=payload.limit
            )
        ),
    )
    return envelope(
        "prioritize_listing_health", skill_evidence_to_claims(evidence), organization_id=evidence.organization_id
    )


def _investigate_non_buyable_listing(
    service: NonBuyableListingEvidenceService, payload: InvestigateNonBuyableListingInput
) -> EvidenceEnvelope:
    skill_id = "non_buyable_listing_investigator"
    try:
        evidence = _cached_evidence(
            skill_id=skill_id,
            skill_version=SKILL_VERSIONS[skill_id],
            marketplace_participation_id=payload.marketplace_participation_id,
            params={
                "period_days": payload.period_days,
                "seller_sku": payload.seller_sku,
                "asin": payload.asin,
            },
            needs_listings=True,
            needs_orders=True,
            force_refresh=payload.force_refresh,
            compute=lambda: _guarded(
                lambda: service.investigate(
                    payload.marketplace_participation_id,
                    seller_sku=payload.seller_sku,
                    asin=payload.asin,
                    period_days=payload.period_days,
                )
            ),
        )
    except ListingNotFoundForInvestigationError as exc:
        raise ToolValidationError(
            "investigate_non_buyable_listing",
            "No listing in this marketplace matches that SKU or ASIN.",
        ) from exc
    return envelope(
        "investigate_non_buyable_listing",
        skill_evidence_to_claims(evidence),
        organization_id=evidence.organization_id,
    )


def _analyze_order_trends(service: OrderTrendEvidenceService, payload: AnalyzeOrderTrendsInput) -> EvidenceEnvelope:
    skill_id = "order_and_sales_trend_analyst"
    evidence = _cached_evidence(
        skill_id=skill_id,
        skill_version=SKILL_VERSIONS[skill_id],
        marketplace_participation_id=payload.marketplace_participation_id,
        params={"period_days": payload.period_days},
        needs_listings=False,
        needs_orders=True,
        force_refresh=payload.force_refresh,
        compute=lambda: _guarded(
            lambda: service.analyze(payload.marketplace_participation_id, period_days=payload.period_days)
        ),
    )
    return envelope(
        "analyze_order_trends", skill_evidence_to_claims(evidence), organization_id=evidence.organization_id
    )


def _detect_cancellation_anomalies(
    service: CancellationAnomalyEvidenceService, payload: DetectCancellationAnomaliesInput
) -> EvidenceEnvelope:
    skill_id = "cancellation_operational_anomaly_detector"
    evidence = _cached_evidence(
        skill_id=skill_id,
        skill_version=SKILL_VERSIONS[skill_id],
        marketplace_participation_id=payload.marketplace_participation_id,
        params={"period_days": payload.period_days},
        needs_listings=False,
        needs_orders=True,
        force_refresh=payload.force_refresh,
        compute=lambda: _guarded(
            lambda: service.detect(payload.marketplace_participation_id, period_days=payload.period_days)
        ),
    )
    return envelope(
        "detect_cancellation_anomalies", skill_evidence_to_claims(evidence), organization_id=evidence.organization_id
    )


def _rank_listing_risk_by_order_exposure(
    service: ListingRiskEvidenceService, payload: RankListingRiskByOrderExposureInput
) -> EvidenceEnvelope:
    skill_id = "listing_risk_by_order_exposure"
    evidence = _cached_evidence(
        skill_id=skill_id,
        skill_version=SKILL_VERSIONS[skill_id],
        marketplace_participation_id=payload.marketplace_participation_id,
        params={"period_days": payload.period_days, "limit": payload.limit},
        needs_listings=True,
        needs_orders=True,
        force_refresh=payload.force_refresh,
        compute=lambda: _guarded(
            lambda: service.rank(
                payload.marketplace_participation_id, period_days=payload.period_days, limit=payload.limit
            )
        ),
    )
    return envelope(
        "rank_listing_risk_by_order_exposure",
        skill_evidence_to_claims(evidence),
        organization_id=evidence.organization_id,
    )


def _guarded(call):
    """Translates the read services' own sanitized not-found error into a
    `ToolValidationError` with a seller-safe message — never leaking
    whether a participation id was malformed, foreign, or simply
    nonexistent, matching every other tool's error-handling posture."""
    try:
        return call()
    except AmazonListingsParticipationNotFoundError as exc:
        raise ToolValidationError("marketplace_scoped_skill", _FOREIGN_SCOPE_MESSAGE) from exc

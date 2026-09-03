"""12B.5A — shared, ASI-owned evidence contract used by all five Listings
+ Orders Copilot skills.

Every skill's evidence service returns exactly one `SkillEvidence`. This
is the one place that defines what a skill result *is*, independent of
which of the five skills produced it — a deliberate, narrow addition on
top of the existing `EvidenceEnvelope`/`EvidenceClaim` mechanism
(`app/copilot/evidence.py`), not a replacement for it: `skill_evidence_
to_claims()` below converts one `SkillEvidence` into the `EvidenceClaim`
list a tool handler wraps into a normal `EvidenceEnvelope`, so synthesis,
citation-grounding, and the orchestrator's execution path all keep
working unchanged (see `docs/AI_HANDOVER/
12B5A_LISTINGS_ORDERS_COPILOT_SKILLS.md` for the full reasoning).

`extra="forbid"` throughout, matching the Amazon domain read-service
response DTOs (`ListingsSummary`, `OrderCollectionItem`, ...) rather than
Copilot's own untrusted-input schemas (which use `extra="ignore"` for a
different reason — see `app/copilot/schemas.py`). This is
ASI-produced output, not model input; a stray field here would be a bug
worth failing loudly on, not a smuggled instruction to silently drop.

The LLM only ever sees this contract's data by way of `EvidenceClaim`
values already flattened out of it — see `skill_evidence_to_claims`.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.amazon.listings_read import ListingsSyncEvidence
from app.amazon.orders_read import OrdersSyncEvidence
from app.copilot.evidence import EvidenceClaim, claim

SkillId = Literal[
    "listing_health_prioritizer",
    "non_buyable_listing_investigator",
    "order_and_sales_trend_analyst",
    "cancellation_operational_anomaly_detector",
    "listing_risk_by_order_exposure",
]

# Bumped only if a skill's metric/record *shape* changes in a way a
# consumer (frontend card renderer, an eval fixture) would need to know
# about. Independent per skill so one skill's formula change never forces
# every other skill's fixtures to be re-reviewed.
SKILL_VERSIONS: dict[str, str] = {
    "listing_health_prioritizer": "1.0.0",
    "non_buyable_listing_investigator": "1.0.0",
    "order_and_sales_trend_analyst": "1.0.0",
    "cancellation_operational_anomaly_detector": "1.0.0",
    "listing_risk_by_order_exposure": "1.0.0",
}

ConfidenceCategory = Literal["high", "medium", "low", "insufficient_data"]

# Only internal, already-approved Seller pages. A deep link pointing
# anywhere else (an external URL, an API path, a raw Amazon URL) is a
# bug, not a customization — enforced structurally, not by convention.
_SAFE_DEEP_LINK_PREFIXES = ("/seller/listings", "/seller/orders", "/seller")


class PeriodWindow(BaseModel):
    """One analysis or comparison period. `start` is inclusive, `end` is
    exclusive — matching `list_orders`'/`list_order_items_for_window`'s
    own `created_after`/`created_before` convention."""

    model_config = ConfigDict(extra="forbid")

    start: datetime
    end: datetime
    label: str

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, value: datetime, info) -> datetime:
        start = info.data.get("start")
        if start is not None and value <= start:
            raise ValueError("period end must be after start")
        return value


class SkillDeepLink(BaseModel):
    """A safe, internal Seller-page link. Never an external URL, an API
    path, or anything Amazon-hosted."""

    model_config = ConfigDict(extra="forbid")

    label: str
    href: str

    @field_validator("href")
    @classmethod
    def _internal_seller_path_only(cls, value: str) -> str:
        if not any(value == prefix or value.startswith(prefix + "?") or value.startswith(prefix + "/") for prefix in _SAFE_DEEP_LINK_PREFIXES):
            raise ValueError(f"deep link must be an internal /seller path, got: {value!r}")
        return value


def safe_deep_link(path: str, label: str) -> SkillDeepLink:
    return SkillDeepLink(label=label, href=path)


class SkillEvidence(BaseModel):
    """Shared envelope every one of the five launch skills returns.

    `metrics` and `records` are intentionally plain JSON (`dict`/`list`
    of dicts), not a per-skill Pydantic union — each skill's evidence
    service is the only place responsible for populating them, and it
    does so exclusively from fields already sanctioned by
    `AmazonListingsReadService`/`AmazonOrdersReadService`'s own
    `extra="forbid"` response DTOs (`ListingCollectionItem`,
    `ListingDetail`, `OrderCollectionItem`, `OrderItemWindowRow`, ...).
    No skill ever adds a field those services don't already expose.
    """

    model_config = ConfigDict(extra="forbid")

    skill_id: SkillId
    skill_version: str
    organization_id: UUID
    marketplace_participation_ids: list[UUID] = Field(min_length=1)
    analysis_period: PeriodWindow | None = None
    comparison_period: PeriodWindow | None = None
    listings_freshness: ListingsSyncEvidence | None = None
    orders_freshness: OrdersSyncEvidence | None = None
    # True whenever the *latest* relevant run for this scope is anything
    # other than a clean success or "never run" — i.e. queued, running,
    # waiting_to_retry, failed, partial, or timed_out. Derived directly
    # from `listings_freshness.status`/`orders_freshness.status`
    # (whichever this skill actually reads), never a separate query.
    has_newer_incomplete_run: bool = False
    metrics: dict[str, Any] = Field(default_factory=dict)
    records: list[dict[str, Any]] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: ConfidenceCategory
    deep_links: list[SkillDeepLink] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


def incomplete_run(sync_status: str | None) -> bool:
    """True for any status that is neither a clean success nor "never
    synchronized" — queued/running/waiting_to_retry/failed/partial/
    timed_out all count, matching Phase 2's "whether a newer failed/
    partial/in-progress run exists" requirement without a second query:
    the sync-evidence status already IS the latest run's status."""
    return sync_status not in (None, "succeeded", "never_synchronized")


def skill_evidence_to_claims(evidence: SkillEvidence) -> list[EvidenceClaim]:
    """Flattens a `SkillEvidence` into `EvidenceClaim`s for the existing
    `EvidenceEnvelope`/synthesis-citation mechanism.

    One `skill_evidence` claim carries the complete structured payload
    (for the frontend's evidence-card renderer and for a deterministic
    template answer); each top-level `metrics` entry is *also* emitted as
    its own claim (`kind="calculated"`) so synthesis's fact-grounding
    (`build_allowed_facts`/`_FactIndex`) can cite an individual number by
    its own key, exactly like every other tool's claims already do.
    Never includes a field `SkillEvidence` itself doesn't carry — there
    is no separate "extra" data path here.
    """
    source = evidence.skill_id
    claims = [
        claim(
            "skill_evidence",
            evidence.model_dump(mode="json"),
            kind="calculated",
            source=source,
            as_of=evidence.generated_at,
        )
    ]
    for key, value in evidence.metrics.items():
        claims.append(
            claim(key, value, kind="calculated", source=source, as_of=evidence.generated_at)
        )
    claims.append(
        claim(
            "confidence_category",
            evidence.confidence,
            kind="calculated",
            source=source,
            as_of=evidence.generated_at,
        )
    )
    if evidence.limitations:
        claims.append(
            claim(
                "limitations",
                list(evidence.limitations),
                kind="calculated",
                source=source,
                confidence="high",
                as_of=evidence.generated_at,
            )
        )
    return claims

"""Copilot-facing input schemas. These are the only arguments an LLM planner may supply."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.validation import normalize_asin


class CopilotToolInput(BaseModel):
    """Ignore extra keys so a model cannot smuggle `confirmed`, `product`, or handlers."""

    model_config = ConfigDict(extra="ignore")


class GetSavedReportInput(CopilotToolInput):
    report_id: UUID


class ListSavedReportsInput(CopilotToolInput):
    asin: str | None = None
    limit: int = Field(default=20, ge=1, le=100)

    @field_validator("asin")
    @classmethod
    def _normalize_asin(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_asin(value)


class AnalyzeListingV2Input(CopilotToolInput):
    """Load a product through ProductService, then score with Listing Intelligence V2.

    Copilot must not accept a Product blob. Manual/seller-entered listings stay on
    `POST /api/v1/analysis/listing/v2`, not this tool.
    """

    asin: str
    marketplace: str | None = None


class GetProductInput(CopilotToolInput):
    asin: str
    marketplace: str | None = None


class ProfitDomainToolInput(CopilotToolInput):
    """Locate a profit worksheet. organization_id is server-owned and ignored."""

    profit_model_id: UUID | None = None
    asin: str | None = None
    marketplace: str | None = None

    @field_validator("asin")
    @classmethod
    def _normalize_asin(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_asin(value)

    @model_validator(mode="after")
    def _require_locator(self) -> ProfitDomainToolInput:
        if self.profit_model_id is None and not self.asin:
            raise ValueError("Provide profit_model_id or asin.")
        return self


class GetProfitSnapshotInput(ProfitDomainToolInput):
    pass


class AnalyzeProfitabilityInput(ProfitDomainToolInput):
    pass


class AdvertisingDomainToolInput(ProfitDomainToolInput):
    """Locate advertising evidence via the parent profit model or ASIN."""


class GetAdvertisingSnapshotInput(AdvertisingDomainToolInput):
    pass


class AnalyzeAdvertisingImpactInput(AdvertisingDomainToolInput):
    pass


# --- 12B.5A Listings + Orders skill tools -----------------------------------
#
# `marketplace_participation_id` is always required and explicit — never
# inferred, never defaulted server-side. The planner/frontend must supply
# it (see `app/copilot/planner/validator.py`'s `ExtractedSlots.
# marketplace_participation_id` and `PlanTurnRequest.marketplace_
# participation_id`); the handler still re-validates ownership through
# `AmazonListingsReadService`/`AmazonOrdersReadService` on every call, so
# a stale or foreign id from an old turn can never leak another
# organization's or another participation's evidence.
#
# `period_days` mirrors `app.copilot.skills.shared.clamp_period_days` —
# bounded here too so an out-of-range value fails fast at the tool
# boundary with a clear Pydantic error, not a silently clamped surprise
# three layers down.
_MAX_PERIOD_DAYS = 90


class MarketplaceScopedSkillInput(CopilotToolInput):
    marketplace_participation_id: UUID
    period_days: int = Field(default=30, ge=1, le=_MAX_PERIOD_DAYS)


class PrioritizeListingHealthInput(MarketplaceScopedSkillInput):
    limit: int = Field(default=25, ge=1, le=100)


class InvestigateNonBuyableListingInput(MarketplaceScopedSkillInput):
    """`seller_sku`/`asin` are both optional: when neither is given, the
    tool returns a prioritized selection of not-buyable listings instead
    of guessing a target (see `NonBuyableListingEvidenceService.
    _select_candidates`) — this is deliberate, not a missing
    validation."""

    seller_sku: str | None = None
    asin: str | None = None

    @field_validator("asin")
    @classmethod
    def _normalize_asin(cls, value: str | None) -> str | None:
        if value is None or not str(value).strip():
            return None
        return normalize_asin(value)


class AnalyzeOrderTrendsInput(MarketplaceScopedSkillInput):
    pass


class DetectCancellationAnomaliesInput(MarketplaceScopedSkillInput):
    pass


class RankListingRiskByOrderExposureInput(MarketplaceScopedSkillInput):
    limit: int = Field(default=25, ge=1, le=100)

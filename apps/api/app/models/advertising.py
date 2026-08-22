"""Advertising Intelligence request/response models. Client-calculated metrics are ignored."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.profit import Money, ProfitEvidenceView, Rate

ADS_FORMULA_VERSION = "ads-calc-v1"
IMPACT_SOURCE = "advertising_impact"
DEFAULT_CURRENCY = "INR"
DEFAULT_SOURCE = "seller_input"

_CLIENT_CALCULATED_KEYS = frozenset(
    {
        "acos",
        "tacos",
        "roas",
        "net_profit_after_ads",
        "ad_spend_per_unit",
        "break_even_acos",
        "margin_before_ads",
    }
)


class AdvertisingInputs(BaseModel):
    """Canonical ads-calc-v1 inputs. None means unknown, not zero."""

    ad_spend: Money | None = None
    ad_sales: Money | None = None
    total_sales: Money | None = None
    units_in_period: Money | None = None
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("ad_spend", "ad_sales", "total_sales", "units_in_period", mode="before")
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        from app.analytics.profit_rules import parse_money

        try:
            return parse_money(value)
        except InvalidOperation as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def _coerce_date(cls, value: object) -> date | None:
        from app.analytics.advertising_rules import parse_period_date

        try:
            return parse_period_date(value)
        except ValueError as exc:
            raise ValueError("Period dates must be YYYY-MM-DD.") from exc

    @model_validator(mode="after")
    def _period_order(self) -> AdvertisingInputs:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("Period start must be on or before period end.")
        return self


class AdvertisingOutputs(BaseModel):
    acos: Rate | None = None
    tacos: Rate | None = None
    roas: Rate | None = None


class AdvertisingCompleteness(BaseModel):
    unknown: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


class AdvertisingCalculationResult(BaseModel):
    ads_formula_version: str = ADS_FORMULA_VERSION
    status: Literal["complete", "partial", "failed"]
    inputs: AdvertisingInputs
    outputs: AdvertisingOutputs
    completeness: AdvertisingCompleteness


class AdvertisingImpact(BaseModel):
    ad_spend_per_unit: Money | None = None
    net_profit_after_ads: Money | None = None
    break_even_acos: Rate | None = None
    profit_snapshot_id: UUID | None = None
    unknown: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


class AdvertisingUpdate(BaseModel):
    """Seller-owned ads worksheet. Extra keys such as acos are ignored."""

    model_config = ConfigDict(extra="ignore")

    period_start: date | None = None
    period_end: date | None = None
    ad_spend: Money | None = None
    ad_sales: Money | None = None
    total_sales: Money | None = None
    units_in_period: Money | None = None

    @field_validator("ad_spend", "ad_sales", "total_sales", "units_in_period", mode="before")
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        return AdvertisingInputs._coerce_money(value)

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def _coerce_date(cls, value: object) -> date | None:
        return AdvertisingInputs._coerce_date(value)

    @model_validator(mode="after")
    def _period_order(self) -> AdvertisingUpdate:
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("Period start must be on or before period end.")
        return self


class AdvertisingPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ad_spend: Money | None = None
    ad_sales: Money | None = None
    total_sales: Money | None = None
    units_in_period: Money | None = None
    period_start: date | None = None
    period_end: date | None = None
    asin: str | None = None
    marketplace: str | None = None
    currency: str | None = None
    net_profit_before_ads: Money | None = None
    margin_before_ads: Rate | None = None

    @field_validator("ad_spend", "ad_sales", "total_sales", "units_in_period", mode="before")
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        return AdvertisingInputs._coerce_money(value)

    @field_validator("net_profit_before_ads", "margin_before_ads", mode="before")
    @classmethod
    def _coerce_optional_money(cls, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        from app.analytics.profit_rules import parse_money

        try:
            return parse_money(value)
        except InvalidOperation as exc:
            raise ValueError(str(exc)) from exc

    @field_validator("period_start", "period_end", mode="before")
    @classmethod
    def _coerce_date(cls, value: object) -> date | None:
        return AdvertisingInputs._coerce_date(value)

    def to_inputs(self) -> AdvertisingInputs:
        return AdvertisingInputs(
            ad_spend=self.ad_spend,
            ad_sales=self.ad_sales,
            total_sales=self.total_sales,
            units_in_period=self.units_in_period,
            period_start=self.period_start,
            period_end=self.period_end,
        )


class AdvertisingSnapshotSummary(BaseModel):
    id: UUID
    status: str
    period_start: date | None = None
    period_end: date | None = None
    acos: Rate | None = None
    tacos: Rate | None = None
    calculated_at: datetime


class AdvertisingSnapshotListResponse(BaseModel):
    items: list[AdvertisingSnapshotSummary] = Field(default_factory=list)
    total: int = 0


class AdvertisingSnapshotResponse(BaseModel):
    id: UUID
    organization_id: UUID
    advertising_model_id: UUID
    profit_model_id: UUID
    status: str
    ads_formula_version: str
    inputs: AdvertisingInputs
    outputs: AdvertisingOutputs
    completeness: AdvertisingCompleteness
    impact: AdvertisingImpact | None = None
    evidence: ProfitEvidenceView
    calculated_at: datetime


class AdvertisingModelResponse(BaseModel):
    id: UUID
    organization_id: UUID
    profit_model_id: UUID
    asin: str
    marketplace: str
    currency: str
    source: str
    period_start: date | None = None
    period_end: date | None = None
    ad_spend: Money | None = None
    ad_sales: Money | None = None
    total_sales: Money | None = None
    units_in_period: Money | None = None
    latest_snapshot: AdvertisingSnapshotResponse | None = None
    impact: AdvertisingImpact | None = None
    profit_snapshot_stale: bool = False
    created_at: datetime
    updated_at: datetime

"""Profit Intelligence request/response models. Client-calculated money is ignored."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer, field_validator

from app.core.validation import is_valid_asin, normalize_asin
from app.reports.values import MONEY_QUANT

Money = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]
Rate = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]

PROFIT_FORMULA_VERSION = "profit-calc-v1"
DEFAULT_CURRENCY = "INR"
DEFAULT_MARKETPLACE = "amazon.in"
SELLING_PRICE_SOURCES = ("seller", "product_snapshot", "unknown")
SNAPSHOT_STATUSES = ("complete", "partial", "failed")

_CLIENT_CALCULATED_KEYS = frozenset(
    {
        "net_profit",
        "net_profit_before_ads",
        "margin",
        "margin_before_ads",
        "roi",
        "roi_on_cogs",
        "amazon_fees",
        "landed_cost",
        "operating_costs",
    }
)


class ProfitInputs(BaseModel):
    """Canonical calculation inputs. None means unknown, not zero."""

    selling_price: Money | None = None
    cogs: Money | None = None
    referral_fee: Money | None = None
    fba_fee: Money | None = None
    shipping_cost: Money | None = None
    packaging_cost: Money | None = None
    other_cost: Money | None = None

    @field_validator(
        "selling_price",
        "cogs",
        "referral_fee",
        "fba_fee",
        "shipping_cost",
        "packaging_cost",
        "other_cost",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        if value is None or value == "":
            return None
        from app.analytics.profit_rules import parse_money

        try:
            return parse_money(value)
        except InvalidOperation as exc:
            raise ValueError(str(exc)) from exc


class ProfitOutputs(BaseModel):
    amazon_fees: Money | None = None
    operating_costs: Money | None = None
    landed_cost: Money | None = None
    net_profit_before_ads: Money | None = None
    margin_before_ads: Rate | None = None
    roi_on_cogs: Rate | None = None


class ProfitCompleteness(BaseModel):
    unknown: list[str] = Field(default_factory=list)
    messages: list[str] = Field(default_factory=list)


class ProfitCalculationResult(BaseModel):
    profit_formula_version: str = PROFIT_FORMULA_VERSION
    status: Literal["complete", "partial", "failed"]
    inputs: ProfitInputs
    outputs: ProfitOutputs
    completeness: ProfitCompleteness


class ProfitModelCreate(BaseModel):
    """Seller-owned worksheet. Extra keys such as net_profit are ignored."""

    model_config = ConfigDict(extra="ignore")

    asin: str
    marketplace: str | None = None
    currency: str | None = None
    selling_price: Money | None = None
    selling_price_source: Literal["seller", "product_snapshot", "unknown"] | None = None
    cogs: Money | None = None
    shipping_cost: Money | None = None
    packaging_cost: Money | None = None
    other_cost: Money | None = None
    referral_fee_amount: Money | None = None
    fba_fee_amount: Money | None = None
    fee_category_key: str | None = Field(default=None, max_length=64)

    @field_validator("asin")
    @classmethod
    def _asin(cls, value: str) -> str:
        normalized = normalize_asin(value)
        if not is_valid_asin(normalized):
            raise ValueError("ASIN must be 10 letters or numbers.")
        return normalized

    @field_validator(
        "selling_price",
        "cogs",
        "shipping_cost",
        "packaging_cost",
        "other_cost",
        "referral_fee_amount",
        "fba_fee_amount",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        return ProfitInputs._coerce_money(value)


class ProfitModelUpdate(BaseModel):
    model_config = ConfigDict(extra="ignore")

    selling_price: Money | None = None
    selling_price_source: Literal["seller", "product_snapshot", "unknown"] | None = None
    cogs: Money | None = None
    shipping_cost: Money | None = None
    packaging_cost: Money | None = None
    other_cost: Money | None = None
    referral_fee_amount: Money | None = None
    fba_fee_amount: Money | None = None
    fee_category_key: str | None = Field(default=None, max_length=64)

    @field_validator(
        "selling_price",
        "cogs",
        "shipping_cost",
        "packaging_cost",
        "other_cost",
        "referral_fee_amount",
        "fba_fee_amount",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        return ProfitInputs._coerce_money(value)


class ProfitPreviewRequest(BaseModel):
    """Stateless calculate. Client-calculated outputs are ignored."""

    model_config = ConfigDict(extra="ignore")

    selling_price: Money | None = None
    cogs: Money | None = None
    referral_fee: Money | None = None
    fba_fee: Money | None = None
    shipping_cost: Money | None = None
    packaging_cost: Money | None = None
    other_cost: Money | None = None
    asin: str | None = None
    marketplace: str | None = None
    currency: str | None = None

    @field_validator(
        "selling_price",
        "cogs",
        "referral_fee",
        "fba_fee",
        "shipping_cost",
        "packaging_cost",
        "other_cost",
        mode="before",
    )
    @classmethod
    def _coerce_money(cls, value: object) -> Decimal | None:
        return ProfitInputs._coerce_money(value)

    def to_inputs(self) -> ProfitInputs:
        return ProfitInputs(
            selling_price=self.selling_price,
            cogs=self.cogs,
            referral_fee=self.referral_fee,
            fba_fee=self.fba_fee,
            shipping_cost=self.shipping_cost,
            packaging_cost=self.packaging_cost,
            other_cost=self.other_cost,
        )


class ProfitEvidenceClaimView(BaseModel):
    key: str
    value: Any = None
    kind: str
    source: str
    confidence: str = "high"
    notes: str | None = None


class ProfitEvidenceView(BaseModel):
    evidence_id: UUID
    tool_name: str
    organization_id: UUID
    produced_at: datetime
    claims: list[ProfitEvidenceClaimView] = Field(default_factory=list)


class ProfitSnapshotResponse(BaseModel):
    id: UUID
    organization_id: UUID
    profit_model_id: UUID
    status: str
    profit_formula_version: str
    inputs: ProfitInputs
    outputs: ProfitOutputs
    completeness: ProfitCompleteness
    evidence: ProfitEvidenceView
    calculated_at: datetime


class ProfitModelResponse(BaseModel):
    id: UUID
    organization_id: UUID
    asin: str
    marketplace: str
    currency: str
    selling_price: Money | None = None
    selling_price_source: str
    cogs: Money | None = None
    shipping_cost: Money | None = None
    packaging_cost: Money | None = None
    other_cost: Money | None = None
    referral_fee_amount: Money | None = None
    fba_fee_amount: Money | None = None
    fee_category_key: str | None = None
    latest_snapshot: ProfitSnapshotResponse | None = None
    created_at: datetime
    updated_at: datetime


class ProfitModelSummary(BaseModel):
    id: UUID
    asin: str
    marketplace: str
    currency: str
    latest_status: str | None = None
    unknown: list[str] = Field(default_factory=list)
    updated_at: datetime


class ProfitModelListResponse(BaseModel):
    items: list[ProfitModelSummary] = Field(default_factory=list)
    total: int = 0


def money_to_json(value: Decimal | None) -> str | None:
    if value is None:
        return None
    return format(value.quantize(MONEY_QUANT), "f")


def client_calculated_keys() -> frozenset[str]:
    return _CLIENT_CALCULATED_KEYS

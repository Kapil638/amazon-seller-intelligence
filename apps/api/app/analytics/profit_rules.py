"""Deterministic unit-economics formulas (profit-calc-v1).

Python owns all money math. This module does not call a database, AI, or APIs.
Missing inputs stay unknown. Zero denominators stay null. Zeros are never invented.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from app.models.profit import (
    PROFIT_FORMULA_VERSION,
    ProfitCalculationResult,
    ProfitCompleteness,
    ProfitInputs,
    ProfitOutputs,
)
from app.reports.values import MONEY_QUANT, RATE_QUANT

COGS_MISSING_MESSAGE = (
    "The product profitability cannot be calculated because COGS is missing."
)
PRICE_MISSING_MESSAGE = (
    "The product profitability cannot be calculated because selling price is missing."
)
COST_LINES_MISSING_MESSAGE = (
    "Profit cannot be calculated until every cost line is provided. Enter 0 if a cost does not apply."
)

INPUT_KEYS = (
    "selling_price",
    "cogs",
    "referral_fee",
    "fba_fee",
    "shipping_cost",
    "packaging_cost",
    "other_cost",
)
COST_LINE_KEYS = (
    "referral_fee",
    "fba_fee",
    "shipping_cost",
    "packaging_cost",
    "other_cost",
)


def calculate_profit(inputs: ProfitInputs) -> ProfitCalculationResult:
    """Return unit economics for the supplied inputs. Never estimates missing costs."""
    unknown = _unknown_keys(inputs)
    messages = _unknown_messages(unknown)

    amazon_fees: Decimal | None = None
    operating_costs: Decimal | None = None
    landed_cost: Decimal | None = None
    net_profit_before_ads: Decimal | None = None
    margin_before_ads: Decimal | None = None
    roi_on_cogs: Decimal | None = None

    can_sum_fees = inputs.referral_fee is not None and inputs.fba_fee is not None
    can_sum_opex = (
        inputs.shipping_cost is not None
        and inputs.packaging_cost is not None
        and inputs.other_cost is not None
    )
    if can_sum_fees:
        amazon_fees = _money(inputs.referral_fee + inputs.fba_fee)
    if can_sum_opex:
        operating_costs = _money(
            inputs.shipping_cost + inputs.packaging_cost + inputs.other_cost
        )

    can_land = (
        inputs.cogs is not None
        and amazon_fees is not None
        and operating_costs is not None
    )
    if can_land:
        landed_cost = _money(inputs.cogs + amazon_fees + operating_costs)

    can_profit = inputs.selling_price is not None and landed_cost is not None
    if "cogs" in unknown:
        can_profit = False
    if can_profit:
        net_profit_before_ads = _money(inputs.selling_price - landed_cost)
        margin_before_ads = _ratio(net_profit_before_ads, inputs.selling_price)
        roi_on_cogs = _ratio(net_profit_before_ads, inputs.cogs)

    status = "complete" if not unknown else "partial"
    outputs = ProfitOutputs(
        amazon_fees=amazon_fees,
        operating_costs=operating_costs,
        landed_cost=landed_cost,
        net_profit_before_ads=net_profit_before_ads,
        margin_before_ads=margin_before_ads,
        roi_on_cogs=roi_on_cogs,
    )
    return ProfitCalculationResult(
        profit_formula_version=PROFIT_FORMULA_VERSION,
        status=status,
        inputs=inputs,
        outputs=outputs,
        completeness=ProfitCompleteness(unknown=unknown, messages=messages),
    )


def parse_money(value: object) -> Decimal | None:
    """Accept seller JSON (string or number). Never silently turn blanks into 0."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise InvalidOperation("Boolean is not a money amount.")
    if isinstance(value, Decimal):
        money = value
    elif isinstance(value, int):
        money = Decimal(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        money = Decimal(text)
    elif isinstance(value, float):
        money = Decimal(str(value))
    else:
        raise InvalidOperation("Unsupported money type.")
    if money < 0:
        raise InvalidOperation("Money amounts cannot be negative.")
    return _money(money)


def _unknown_keys(inputs: ProfitInputs) -> list[str]:
    unknown: list[str] = []
    for key in INPUT_KEYS:
        if getattr(inputs, key) is None:
            unknown.append(key)
    return unknown


def _unknown_messages(unknown: list[str]) -> list[str]:
    messages: list[str] = []
    if "cogs" in unknown:
        messages.append(COGS_MISSING_MESSAGE)
    if "selling_price" in unknown:
        messages.append(PRICE_MISSING_MESSAGE)
    if any(key in unknown for key in COST_LINE_KEYS) and "cogs" not in unknown:
        messages.append(COST_LINES_MISSING_MESSAGE)
    return messages


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)

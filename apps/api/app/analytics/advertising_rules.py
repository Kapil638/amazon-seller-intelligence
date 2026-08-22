"""Deterministic advertising efficiency formulas (ads-calc-v1).

Python owns ACOS, TACOS, and ROAS. This module does not call a database, AI, or APIs.
Missing inputs stay unknown. Zero denominators stay null. ACOS is never copied into TACOS.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from app.models.advertising import (
    ADS_FORMULA_VERSION,
    AdvertisingCalculationResult,
    AdvertisingCompleteness,
    AdvertisingInputs,
    AdvertisingOutputs,
)
from app.reports.values import RATE_QUANT

AD_SALES_MISSING_MESSAGE = "ACOS unavailable because ad sales are missing."
TOTAL_SALES_MISSING_MESSAGE = "TACOS unavailable because total sales are missing."
AD_SPEND_MISSING_MESSAGE = "Advertising metrics cannot be calculated because ad spend is missing."
PERIOD_MISSING_MESSAGE = "Advertising period start and end are required for a complete snapshot."

PERIOD_KEYS = ("period_start", "period_end")
COMPLETE_REQUIRED = ("period_start", "period_end", "ad_spend", "ad_sales")


def calculate_advertising(inputs: AdvertisingInputs) -> AdvertisingCalculationResult:
    """Return period advertising metrics. Never estimates missing spend or sales."""
    unknown = _unknown_keys(inputs)
    messages = _unknown_messages(unknown)

    acos: Decimal | None = None
    tacos: Decimal | None = None
    roas: Decimal | None = None

    if inputs.ad_spend is not None and inputs.ad_sales is not None and inputs.ad_sales > 0:
        acos = _ratio(inputs.ad_spend, inputs.ad_sales)
    if inputs.ad_spend is not None and inputs.total_sales is not None and inputs.total_sales > 0:
        tacos = _ratio(inputs.ad_spend, inputs.total_sales)
    if inputs.ad_sales is not None and inputs.ad_spend is not None and inputs.ad_spend > 0:
        roas = _ratio(inputs.ad_sales, inputs.ad_spend)

    status = "complete" if not any(key in unknown for key in COMPLETE_REQUIRED) else "partial"
    return AdvertisingCalculationResult(
        ads_formula_version=ADS_FORMULA_VERSION,
        status=status,
        inputs=inputs,
        outputs=AdvertisingOutputs(acos=acos, tacos=tacos, roas=roas),
        completeness=AdvertisingCompleteness(unknown=unknown, messages=messages),
    )


def _unknown_keys(inputs: AdvertisingInputs) -> list[str]:
    unknown: list[str] = []
    for key in ("ad_spend", "ad_sales", "total_sales", "units_in_period", *PERIOD_KEYS):
        if getattr(inputs, key) is None:
            unknown.append(key)
    return unknown


def _unknown_messages(unknown: list[str]) -> list[str]:
    messages: list[str] = []
    if "ad_spend" in unknown:
        messages.append(AD_SPEND_MISSING_MESSAGE)
    if "ad_sales" in unknown:
        messages.append(AD_SALES_MISSING_MESSAGE)
    if "total_sales" in unknown:
        messages.append(TOTAL_SALES_MISSING_MESSAGE)
    if any(key in unknown for key in PERIOD_KEYS):
        messages.append(PERIOD_MISSING_MESSAGE)
    return messages


def _ratio(numerator: Decimal, denominator: Decimal) -> Decimal | None:
    if denominator == 0:
        return None
    return (numerator / denominator).quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def parse_period_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])

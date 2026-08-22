"""Compact Profit Intelligence claims for EvidenceEnvelope.

Does not recalculate profit. Copies engine outputs and labels missing inputs as unknown.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

from app.copilot.evidence import EvidenceClaim, EvidenceEnvelope, claim, envelope
from app.models.profit import PROFIT_FORMULA_VERSION, ProfitCalculationResult

PROFIT_ENGINE = "profit_calculation"
SOURCE_SELLER = "seller_input"
SOURCE_CALCULATED = PROFIT_FORMULA_VERSION

INPUT_CLAIM_KEYS = (
    "selling_price",
    "cogs",
    "referral_fee",
    "fba_fee",
    "shipping_cost",
    "packaging_cost",
    "other_cost",
)
OUTPUT_CLAIM_KEYS = (
    "amazon_fees",
    "operating_costs",
    "landed_cost",
    "net_profit_before_ads",
    "margin_before_ads",
    "roi_on_cogs",
)


def profit_evidence_envelope(
    result: ProfitCalculationResult,
    *,
    asin: str | None = None,
    marketplace: str | None = None,
    currency: str | None = None,
    organization_id: UUID | None = None,
) -> EvidenceEnvelope:
    claims: list[EvidenceClaim] = [
        claim("profit_formula_version", PROFIT_FORMULA_VERSION, kind="calculated", source=SOURCE_CALCULATED),
        claim("status", result.status, kind="calculated", source=SOURCE_CALCULATED),
    ]
    if asin:
        claims.append(claim("asin", asin, kind="seller_provided", source=SOURCE_SELLER))
    if marketplace:
        claims.append(claim("marketplace", marketplace, kind="seller_provided", source=SOURCE_SELLER))
    if currency:
        claims.append(claim("currency", currency, kind="seller_provided", source=SOURCE_SELLER))

    for key in INPUT_CLAIM_KEYS:
        value = getattr(result.inputs, key)
        claims.append(_input_claim(key, value, result.completeness.unknown))

    notes_by_key = _output_notes(result)
    for key in OUTPUT_CLAIM_KEYS:
        value = getattr(result.outputs, key)
        if value is None:
            claims.append(
                claim(
                    key,
                    None,
                    kind="unknown",
                    source=SOURCE_CALCULATED,
                    confidence="none",
                    notes=notes_by_key.get(key),
                )
            )
            continue
        claims.append(
            claim(
                key,
                _json_value(value),
                kind="calculated",
                source=SOURCE_CALCULATED,
            )
        )

    claims.append(
        claim(
            "completeness",
            {
                "unknown": list(result.completeness.unknown),
                "messages": list(result.completeness.messages),
            },
            kind="calculated",
            source=SOURCE_CALCULATED,
        )
    )
    return envelope(PROFIT_ENGINE, claims, organization_id=organization_id)


def evidence_to_dict(item: EvidenceEnvelope) -> dict[str, Any]:
    return item.model_dump(mode="json")


def _input_claim(key: str, value: Decimal | None, unknown: list[str]) -> EvidenceClaim:
    if value is None or key in unknown:
        notes = None
        if key == "cogs":
            notes = "The product profitability cannot be calculated because COGS is missing."
        return claim(key, None, kind="unknown", source=SOURCE_SELLER, confidence="none", notes=notes)
    return claim(key, _json_value(value), kind="seller_provided", source=SOURCE_SELLER)


def _output_notes(result: ProfitCalculationResult) -> dict[str, str]:
    notes: dict[str, str] = {}
    if "cogs" in result.completeness.unknown:
        message = "The product profitability cannot be calculated because COGS is missing."
        notes["net_profit_before_ads"] = message
        notes["margin_before_ads"] = message
        notes["roi_on_cogs"] = message
        notes["landed_cost"] = message
    return notes


def _json_value(value: Decimal) -> str:
    return format(value, "f")

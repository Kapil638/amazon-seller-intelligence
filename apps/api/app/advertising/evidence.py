"""Compact Advertising Intelligence claims for EvidenceEnvelope.

Does not recalculate ACOS or profit. Copies engine and composer outputs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from app.copilot.evidence import EvidenceClaim, EvidenceEnvelope, claim, envelope
from app.models.advertising import (
    ADS_FORMULA_VERSION,
    IMPACT_SOURCE,
    AdvertisingCalculationResult,
    AdvertisingImpact,
    AdvertisingInputs,
)

ADS_ENGINE = "advertising_calculation"
SOURCE_SELLER = "seller_input"
SOURCE_CALCULATED = ADS_FORMULA_VERSION

INPUT_KEYS = (
    "ad_spend",
    "ad_sales",
    "total_sales",
    "units_in_period",
    "period_start",
    "period_end",
)
OUTPUT_KEYS = ("acos", "tacos", "roas")
IMPACT_KEYS = ("ad_spend_per_unit", "net_profit_after_ads", "break_even_acos")


def advertising_evidence_envelope(
    result: AdvertisingCalculationResult,
    *,
    asin: str | None = None,
    marketplace: str | None = None,
    currency: str | None = None,
    impact: AdvertisingImpact | None = None,
    organization_id: UUID | None = None,
    advertising_snapshot_id: UUID | None = None,
) -> EvidenceEnvelope:
    as_of = _period_as_of(result.inputs)
    claims: list[EvidenceClaim] = [
        claim(
            "ads_formula_version",
            ADS_FORMULA_VERSION,
            kind="calculated",
            source=SOURCE_CALCULATED,
            as_of=as_of,
        ),
        claim("status", result.status, kind="calculated", source=SOURCE_CALCULATED, as_of=as_of),
    ]
    if advertising_snapshot_id is not None:
        claims.append(
            claim(
                "advertising_snapshot_id",
                str(advertising_snapshot_id),
                kind="historical",
                source=SOURCE_CALCULATED,
                as_of=as_of,
            )
        )
    if asin:
        claims.append(claim("asin", asin, kind="seller_provided", source=SOURCE_SELLER))
    if marketplace:
        claims.append(claim("marketplace", marketplace, kind="seller_provided", source=SOURCE_SELLER))
    if currency:
        claims.append(claim("currency", currency, kind="seller_provided", source=SOURCE_SELLER))

    for key in INPUT_KEYS:
        value = getattr(result.inputs, key)
        claims.append(_input_claim(key, value, result.completeness.unknown))

    notes = _output_notes(result)
    for key in OUTPUT_KEYS:
        value = getattr(result.outputs, key)
        if value is None:
            claims.append(
                claim(
                    key,
                    None,
                    kind="unknown",
                    source=SOURCE_CALCULATED,
                    confidence="none",
                    notes=notes.get(key),
                    as_of=as_of,
                )
            )
        else:
            claims.append(
                claim(key, _json_value(value), kind="calculated", source=SOURCE_CALCULATED, as_of=as_of)
            )

    if impact is not None:
        claims.extend(_impact_claims(impact))

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
    return envelope(ADS_ENGINE, claims, organization_id=organization_id)


def _impact_claims(impact: AdvertisingImpact) -> list[EvidenceClaim]:
    rows: list[EvidenceClaim] = []
    after_ads_notes = (
        impact.messages[0] if impact.net_profit_after_ads is None and impact.messages else None
    )
    for key in IMPACT_KEYS:
        value = getattr(impact, key)
        if value is None:
            rows.append(
                claim(
                    key,
                    None,
                    kind="unknown",
                    source=IMPACT_SOURCE,
                    confidence="none",
                    notes=after_ads_notes if key == "net_profit_after_ads" else None,
                )
            )
        else:
            rows.append(claim(key, _json_value(value), kind="calculated", source=IMPACT_SOURCE))
    if impact.profit_snapshot_id is not None:
        rows.append(
            claim(
                "profit_snapshot_id",
                str(impact.profit_snapshot_id),
                kind="historical",
                source=IMPACT_SOURCE,
            )
        )
    return rows


def _input_claim(key: str, value: Any, unknown: list[str]) -> EvidenceClaim:
    if value is None or key in unknown:
        notes = None
        if key == "ad_sales":
            notes = "ACOS unavailable because ad sales are missing."
        elif key == "total_sales":
            notes = "TACOS unavailable because total sales are missing."
        elif key == "units_in_period":
            notes = "After-ads profit unavailable because units are missing."
        return claim(key, None, kind="unknown", source=SOURCE_SELLER, confidence="none", notes=notes)
    if hasattr(value, "isoformat"):
        rendered: Any = value.isoformat()
    else:
        rendered = _json_value(value) if isinstance(value, Decimal) else value
    as_of = None
    if hasattr(value, "year"):
        as_of = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return claim(key, rendered, kind="seller_provided", source=SOURCE_SELLER, as_of=as_of)


def _period_as_of(inputs: AdvertisingInputs) -> datetime | None:
    if inputs.period_end is None:
        return None
    return datetime(inputs.period_end.year, inputs.period_end.month, inputs.period_end.day, tzinfo=UTC)


def _output_notes(result: AdvertisingCalculationResult) -> dict[str, str]:
    notes: dict[str, str] = {}
    if "ad_sales" in result.completeness.unknown:
        notes["acos"] = "ACOS unavailable because ad sales are missing."
    if "total_sales" in result.completeness.unknown:
        notes["tacos"] = "TACOS unavailable because total sales are missing."
    if "ad_spend" in result.completeness.unknown:
        notes["roas"] = "Advertising metrics cannot be calculated because ad spend is missing."
    return notes


def _json_value(value: Decimal) -> str:
    return format(value, "f")

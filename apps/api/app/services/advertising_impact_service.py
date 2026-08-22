"""Compose profit snapshot outputs with advertising snapshot inputs.

Does not reimplement profit-calc-v1 or ads-calc-v1.
"""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID

from app.models.advertising import AdvertisingImpact, AdvertisingInputs
from app.models.profit import ProfitOutputs
from app.reports.values import MONEY_QUANT

UNITS_MISSING_MESSAGE = "After-ads profit unavailable because units are missing."
PROFIT_MISSING_MESSAGE = (
    "Profit after ads cannot be calculated because unit profit before ads is unknown."
)
AD_SPEND_MISSING_MESSAGE = (
    "Profit after ads cannot be calculated because ad spend is missing."
)


class AdvertisingImpactService:
    """Deterministic after-ads impact. No database, AI, or formula duplication."""

    def compose(
        self,
        *,
        profit_outputs: ProfitOutputs | None,
        ads_inputs: AdvertisingInputs,
        profit_snapshot_id: UUID | None = None,
    ) -> AdvertisingImpact:
        unknown: list[str] = []
        messages: list[str] = []

        net_before = profit_outputs.net_profit_before_ads if profit_outputs else None
        margin_before = profit_outputs.margin_before_ads if profit_outputs else None
        spend = ads_inputs.ad_spend
        units = ads_inputs.units_in_period

        if net_before is None:
            unknown.append("net_profit_before_ads")
            messages.append(PROFIT_MISSING_MESSAGE)
        if spend is None:
            unknown.append("ad_spend")
            messages.append(AD_SPEND_MISSING_MESSAGE)
        if units is None or units <= 0:
            unknown.append("units_in_period")
            messages.append(UNITS_MISSING_MESSAGE)

        ad_spend_per_unit: Decimal | None = None
        net_after: Decimal | None = None
        if spend is not None and units is not None and units > 0:
            ad_spend_per_unit = (spend / units).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        if net_before is not None and ad_spend_per_unit is not None:
            net_after = (net_before - ad_spend_per_unit).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)

        break_even = margin_before
        if break_even is None:
            unknown.append("break_even_acos")

        return AdvertisingImpact(
            ad_spend_per_unit=ad_spend_per_unit,
            net_profit_after_ads=net_after,
            break_even_acos=break_even,
            profit_snapshot_id=profit_snapshot_id,
            unknown=unknown,
            messages=messages,
        )


def get_advertising_impact_service() -> AdvertisingImpactService:
    return AdvertisingImpactService()

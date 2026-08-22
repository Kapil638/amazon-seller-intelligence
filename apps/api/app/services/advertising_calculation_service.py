"""Pure advertising efficiency façade. No database, AI, or network I/O."""

from app.analytics.advertising_rules import calculate_advertising
from app.models.advertising import AdvertisingCalculationResult, AdvertisingInputs


class AdvertisingCalculationService:
    """Deterministic advertising calculator (ads-calc-v1)."""

    def calculate(self, inputs: AdvertisingInputs) -> AdvertisingCalculationResult:
        return calculate_advertising(inputs)


def get_advertising_calculation_service() -> AdvertisingCalculationService:
    return AdvertisingCalculationService()

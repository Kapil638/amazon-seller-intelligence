"""Pure profit math façade. No database, AI, or network I/O."""

from app.analytics.profit_rules import calculate_profit
from app.models.profit import ProfitCalculationResult, ProfitInputs


class ProfitCalculationService:
    """Deterministic unit-economics calculator (profit-calc-v1)."""

    def calculate(self, inputs: ProfitInputs) -> ProfitCalculationResult:
        return calculate_profit(inputs)


def get_profit_calculation_service() -> ProfitCalculationService:
    return ProfitCalculationService()

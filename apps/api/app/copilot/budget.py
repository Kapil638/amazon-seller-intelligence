"""Execution-budget policy for intelligence tools. Not billing.

The tracker is the per-turn cap a Copilot turn must pass into ToolRegistry.execute.
Handlers must not apply their own spend policy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.copilot.exceptions import BudgetExceededError, ConfirmationRequiredError

COST_NONE = "none"
COST_RAINFOREST_PRODUCT = "rainforest_product"
COST_RAINFOREST_SEARCH = "rainforest_search"
COST_OPENAI = "openai"

MAX_TOOL_ROUNDS = 2
MAX_TOOLS_PER_TURN = 4


@dataclass
class BudgetTracker:
    """Tracks tool use for one seller turn. First Rainforest product call is allowed."""

    max_tool_rounds: int = MAX_TOOL_ROUNDS
    max_tools_per_turn: int = MAX_TOOLS_PER_TURN
    rounds: int = 0
    tools_this_turn: int = 0
    rainforest_product_calls: int = 0
    rainforest_search_calls: int = 0
    openai_calls: int = 0
    _started: bool = field(default=False, repr=False)

    def begin_round(self) -> None:
        """Start a planning/execution round. Caps at max_tool_rounds."""
        if self.rounds >= self.max_tool_rounds:
            raise BudgetExceededError("The maximum number of tool rounds for this turn has been reached.")
        self.rounds += 1
        self._started = True

    def requires_confirmation(self, cost_kind: str) -> bool:
        if cost_kind == COST_NONE:
            return False
        if cost_kind == COST_RAINFOREST_PRODUCT:
            return self.rainforest_product_calls >= 1
        if cost_kind in (COST_RAINFOREST_SEARCH, COST_OPENAI):
            return True
        return True

    def can_execute(self, cost_kind: str, *, confirmed: bool = False) -> bool:
        if self.tools_this_turn >= self.max_tools_per_turn:
            return False
        if self._started and self.rounds > self.max_tool_rounds:
            return False
        if not self._started and self.max_tool_rounds < 1:
            return False
        if self.requires_confirmation(cost_kind) and not confirmed:
            return False
        return True

    def record_execution(self, cost_kind: str) -> None:
        if not self._started:
            self.begin_round()
        if self.tools_this_turn >= self.max_tools_per_turn:
            raise BudgetExceededError("This turn already executed the maximum number of tools.")
        self.tools_this_turn += 1
        if cost_kind == COST_RAINFOREST_PRODUCT:
            self.rainforest_product_calls += 1
        elif cost_kind == COST_RAINFOREST_SEARCH:
            self.rainforest_search_calls += 1
        elif cost_kind == COST_OPENAI:
            self.openai_calls += 1

    def assert_can_execute(self, cost_kind: str, *, confirmed: bool = False) -> None:
        if self.tools_this_turn >= self.max_tools_per_turn:
            raise BudgetExceededError("This turn already executed the maximum number of tools.")
        if self._started and self.rounds > self.max_tool_rounds:
            raise BudgetExceededError("The maximum number of tool rounds for this turn has been reached.")
        if self.requires_confirmation(cost_kind) and not confirmed:
            raise ConfirmationRequiredError(
                _confirmation_message(cost_kind),
                cost_kind=cost_kind,
            )


def _confirmation_message(cost_kind: str) -> str:
    if cost_kind == COST_RAINFOREST_PRODUCT:
        return "This will look up another Amazon product (product credits). Continue?"
    if cost_kind == COST_RAINFOREST_SEARCH:
        return "This will run an Amazon search (search credits). Continue?"
    if cost_kind == COST_OPENAI:
        return "This will call OpenAI. Continue?"
    return "This action requires confirmation."

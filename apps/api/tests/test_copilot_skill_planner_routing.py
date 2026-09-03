"""12B.5A — deterministic (no-LLM) planner routing for the five Listings +
Orders skill intents, plus end-to-end orchestrator isolation checks.
Mirrors `test_copilot_planner.py`'s established pattern exactly."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.copilot import default_registry
from app.copilot.budget import BudgetTracker
from app.copilot.conversation.service import ConversationService
from app.copilot.planner.service import PlannerService


class _GuardRegistry:
    """Same guard used by test_copilot_planner.py — proves planning never
    calls execute()."""

    def __init__(self, inner) -> None:
        self._inner = inner

    def list_tools(self):
        return self._inner.list_tools()

    def get_input_schema(self, name: str):
        return self._inner.get_input_schema(name)

    def get_tool(self, name: str):
        return self._inner.get_tool(name)

    async def execute(self, *args, **kwargs):
        raise AssertionError("Planner must not call ToolRegistry.execute")


def _service() -> PlannerService:
    return PlannerService(registry=_GuardRegistry(default_registry()))


async def _plan(message: str, *, marketplace_participation_id=None):
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = PlannerService(
        conversations=conversations, registry=_GuardRegistry(default_registry())
    )
    return await service.plan_turn(
        created.id, message, marketplace_participation_id=marketplace_participation_id
    )


@pytest.mark.asyncio
async def test_listing_health_routes_with_marketplace_scope() -> None:
    participation_id = uuid4()
    plan = await _plan("Which listings should I fix first?", marketplace_participation_id=participation_id)
    assert plan.intent == "prioritize_listing_health"
    assert len(plan.tool_calls) == 1
    assert plan.tool_calls[0].name == "prioritize_listing_health"
    assert plan.tool_calls[0].arguments["marketplace_participation_id"] == str(participation_id)
    assert plan.needs_confirmation is False


@pytest.mark.asyncio
async def test_order_trends_routes_with_marketplace_scope() -> None:
    participation_id = uuid4()
    plan = await _plan("How are my orders trending?", marketplace_participation_id=participation_id)
    assert plan.intent == "analyze_order_trends"
    assert plan.tool_calls[0].name == "analyze_order_trends"


@pytest.mark.asyncio
async def test_cancellation_anomaly_routes_with_marketplace_scope() -> None:
    participation_id = uuid4()
    plan = await _plan("Are cancellations unusually high?", marketplace_participation_id=participation_id)
    assert plan.intent == "detect_cancellation_anomalies"
    assert plan.tool_calls[0].name == "detect_cancellation_anomalies"


@pytest.mark.asyncio
async def test_listing_risk_routes_with_marketplace_scope() -> None:
    participation_id = uuid4()
    plan = await _plan("Which listing issues affect the most orders?", marketplace_participation_id=participation_id)
    assert plan.intent == "rank_listing_risk_by_order_exposure"
    assert plan.tool_calls[0].name == "rank_listing_risk_by_order_exposure"


@pytest.mark.asyncio
async def test_non_buyable_routes_with_asin_and_marketplace_scope() -> None:
    participation_id = uuid4()
    plan = await _plan(
        "Why is B01MD1SKLL not buyable?", marketplace_participation_id=participation_id
    )
    assert plan.intent == "investigate_non_buyable_listing"
    assert plan.tool_calls[0].name == "investigate_non_buyable_listing"
    assert plan.tool_calls[0].arguments["asin"] == "B01MD1SKLL"


@pytest.mark.asyncio
async def test_non_buyable_without_asin_still_routes_for_a_prioritized_selection() -> None:
    """No specific SKU/ASIN named (the general "why are my listings not
    buyable?" launch-card question) must still route to the tool — it
    returns a prioritized selection of not-buyable listings rather than
    guessing a target (see `NonBuyableListingEvidenceService.
    _select_candidates`), so the turn must not degrade to `clarify`."""
    participation_id = uuid4()
    plan = await _plan("Why are my listings not buyable?", marketplace_participation_id=participation_id)
    assert plan.intent == "investigate_non_buyable_listing"
    assert plan.tool_calls[0].name == "investigate_non_buyable_listing"
    assert plan.tool_calls[0].arguments.get("asin") is None
    assert plan.tool_calls[0].arguments.get("seller_sku") is None
    assert plan.tool_calls[0].arguments["marketplace_participation_id"] == str(participation_id)


@pytest.mark.asyncio
async def test_skill_intent_without_marketplace_scope_clarifies_never_guesses() -> None:
    plan = await _plan("Which listings should I fix first?", marketplace_participation_id=None)
    assert plan.intent == "clarify"
    assert plan.tool_calls == []


@pytest.mark.asyncio
async def test_profit_intent_still_wins_over_skill_keywords() -> None:
    """Existing single-ASIN profit/ads routing must not regress."""
    participation_id = uuid4()
    plan = await _plan(
        "What is my profit for B01MD1SKLL?", marketplace_participation_id=participation_id
    )
    assert plan.intent == "explain_profit"
    assert plan.tool_calls[0].name == "get_profit_snapshot"


@pytest.mark.asyncio
async def test_foreign_marketplace_scope_raises_sanitized_tool_validation_error() -> None:
    """A marketplace this organization does not own must fail with the
    same sanitized `ToolValidationError` every other tool already uses
    for this class of error (caught cleanly by the orchestrator into a
    `status="failed"` `ToolCallResult` — see `test_copilot_orchestrator.py`
    for that layer) — never a raw ORM/db error, and never a hint about
    whether the id was malformed, foreign, or simply nonexistent."""
    from app.copilot.exceptions import ToolValidationError
    from app.copilot.registry import ToolRegistry

    registry: ToolRegistry = default_registry()
    budget = BudgetTracker()
    with pytest.raises(ToolValidationError):
        await registry.execute(
            "prioritize_listing_health",
            {"marketplace_participation_id": str(uuid4())},
            budget=budget,
        )

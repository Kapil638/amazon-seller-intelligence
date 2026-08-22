from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.copilot import default_registry
from app.copilot.conversation.service import ConversationService
from app.copilot.planner.schemas import PlannerProposal, PlannerRequest, ProposedToolCall
from app.copilot.planner.service import PlannerService
from app.copilot.planner.validator import PlanValidator
from app.models.copilot_conversation import CompactContext

CONVERSATIONS_URL = "/api/v1/copilot/conversations"


class _GuardRegistry:
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


class _FakeProposer:
    def __init__(self, proposal: PlannerProposal | None = None, *, error: bool = False) -> None:
        self.proposal = proposal
        self.error = error
        self.calls: list[dict] = []

    async def propose(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise RuntimeError("planner unavailable")
        return self.proposal, "mock-planner"


def _service(proposer=None) -> PlannerService:
    return PlannerService(registry=_GuardRegistry(default_registry()), proposer=proposer)


@pytest.mark.asyncio
async def test_explain_score_uses_history_tools_without_execute() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    conversations.update_slots(created.id, last_asin="B0TEST0001")
    service = PlannerService(
        conversations=conversations,
        registry=_GuardRegistry(default_registry()),
    )
    plan = await service.plan_turn(created.id, "Why is my listing score low?")
    assert plan.intent == "explain_listing_score"
    assert plan.source == "fallback_rules"
    assert plan.needs_confirmation is False
    names = [call.name for call in plan.tool_calls]
    assert names == ["list_saved_reports"]
    assert plan.tool_calls[0].arguments["asin"] == "B0TEST0001"
    assert plan.planner_model is None
    fetched = conversations.get_conversation(created.id)
    assert fetched.previous_intent == "explain_listing_score"
    assert fetched.last_asin == "B0TEST0001"
    assert fetched.messages[-1].role == "user"
    assert fetched.messages[-1].content == "Why is my listing score low?"


@pytest.mark.asyncio
async def test_http_plan_explain_from_message_asin(client: TestClient) -> None:
    created = client.post(CONVERSATIONS_URL, json={}).json()
    response = client.post(
        f"{CONVERSATIONS_URL}/{created['id']}/plan",
        json={"user_message": "Why is my score low for B0TEST0001?", "organization_id": str(uuid4())},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["intent"] == "explain_listing_score"
    assert body["organization_id"] == created["organization_id"]
    assert [call["name"] for call in body["tool_calls"]] == ["list_saved_reports"]
    assert body["needs_confirmation"] is False


@pytest.mark.asyncio
async def test_analyze_requires_confirmation_and_does_not_execute() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = _service()
    service._conversations = conversations
    plan = await service.plan_turn(created.id, "Analyze B0TEST0001")
    assert plan.intent == "analyze_asin"
    assert [call.name for call in plan.tool_calls] == ["analyze_listing_v2"]
    assert plan.needs_confirmation is True
    assert plan.confirm_summary is not None


@pytest.mark.asyncio
async def test_analyze_an_asin_without_asin_asks_and_does_not_insert_test_asin() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    conversations.update_slots(created.id, last_asin="B0TEST0001")
    service = _service()
    service._conversations = conversations
    plan = await service.plan_turn(created.id, "Analyze an ASIN")
    assert plan.intent == "analyze_asin"
    assert plan.tool_calls == []
    assert plan.needs_confirmation is False
    assert "B0TEST0001" not in str(plan.model_dump(mode="json"))


@pytest.mark.asyncio
async def test_analyze_follow_up_asin_with_saved_report_uses_history() -> None:
    from tests.test_listing_analysis import make_product
    from tests.test_report_lifecycle import _persist_report

    _product, _analysis, persist = _persist_report(product=make_product(asin="B01MD1SKLL"))
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = _service()
    service._conversations = conversations
    asked = await service.plan_turn(created.id, "Analyze an ASIN")
    assert asked.tool_calls == []
    plan = await service.plan_turn(created.id, "B01MD1SKLL")
    names = [call.name for call in plan.tool_calls]
    assert "get_saved_report" in names
    assert "analyze_listing_v2" not in names
    assert plan.needs_confirmation is False
    saved = next(call for call in plan.tool_calls if call.name == "get_saved_report")
    assert saved.arguments["report_id"] == str(persist.report_id)


@pytest.mark.asyncio
async def test_analyze_follow_up_new_asin_requires_confirmation() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = _service()
    service._conversations = conversations
    await service.plan_turn(created.id, "Analyze an ASIN")
    plan = await service.plan_turn(created.id, "B0TEST0001")
    assert [call.name for call in plan.tool_calls] == ["analyze_listing_v2"]
    assert plan.needs_confirmation is True
    assert plan.tool_calls[0].arguments["asin"] == "B0TEST0001"


@pytest.mark.asyncio
async def test_analyze_an_asin_ignores_llm_default_asin() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    proposer = _FakeProposer(
        PlannerProposal(
            intent="analyze_asin",
            tool_calls=[ProposedToolCall(name="analyze_listing_v2", arguments={"asin": "B0TEST0001"})],
        )
    )
    service = PlannerService(
        conversations=conversations,
        registry=_GuardRegistry(default_registry()),
        proposer=proposer,
    )
    plan = await service.plan_turn(created.id, "Analyze an ASIN")
    assert plan.intent == "analyze_asin"
    assert plan.tool_calls == []
    assert plan.needs_confirmation is False
    assert any(item.reason == "asin_required" for item in plan.rejected_calls)
    assert "B0TEST0001" not in [call.arguments.get("asin") for call in plan.tool_calls]


@pytest.mark.asyncio
async def test_out_of_scope_has_no_tools() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = PlannerService(conversations=conversations, registry=_GuardRegistry(default_registry()))
    plan = await service.plan_turn(created.id, "Compare my product with competitors")
    assert plan.intent == "out_of_scope"
    assert plan.tool_calls == []


@pytest.mark.asyncio
async def test_clarify_when_no_asin() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = PlannerService(conversations=conversations, registry=_GuardRegistry(default_registry()))
    plan = await service.plan_turn(created.id, "hello there")
    assert plan.intent == "clarify"
    assert plan.tool_calls == []


@pytest.mark.asyncio
async def test_unknown_tool_is_dropped_and_fallback_used() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    conversations.update_slots(created.id, last_asin="B0TEST0001")
    proposer = _FakeProposer(
        PlannerProposal(
            intent="explain_listing_score",
            tool_calls=[ProposedToolCall(name="delete_everything", arguments={})],
        )
    )
    service = PlannerService(
        conversations=conversations,
        registry=_GuardRegistry(default_registry()),
        proposer=proposer,
    )
    plan = await service.plan_turn(created.id, "Why is my listing score low?")
    assert any(item.reason == "unknown_tool" for item in plan.rejected_calls)
    assert [call.name for call in plan.tool_calls] == ["list_saved_reports"]
    assert plan.source == "fallback_rules"


@pytest.mark.asyncio
async def test_product_blob_is_rejected() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    proposer = _FakeProposer(
        PlannerProposal(
            intent="analyze_asin",
            tool_calls=[
                ProposedToolCall(
                    name="analyze_listing_v2",
                    arguments={"asin": "B0TEST0001", "product": {"title": "fake"}, "confirmed": True},
                )
            ],
        )
    )
    service = PlannerService(
        conversations=conversations,
        registry=_GuardRegistry(default_registry()),
        proposer=proposer,
    )
    plan = await service.plan_turn(created.id, "Analyze B0TEST0001")
    assert any(item.reason == "product_blob_forbidden" for item in plan.rejected_calls)
    assert [call.name for call in plan.tool_calls] == ["analyze_listing_v2"]
    assert "product" not in plan.tool_calls[0].arguments
    assert "confirmed" not in plan.tool_calls[0].arguments
    assert plan.needs_confirmation is True


@pytest.mark.asyncio
async def test_history_first_rewrites_fetch_proposal() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    conversations.update_slots(created.id, last_asin="B0TEST0001", last_report_id=uuid4())
    proposer = _FakeProposer(
        PlannerProposal(
            intent="explain_listing_score",
            tool_calls=[ProposedToolCall(name="analyze_listing_v2", arguments={"asin": "B0TEST0001"})],
        )
    )
    service = PlannerService(
        conversations=conversations,
        registry=_GuardRegistry(default_registry()),
        proposer=proposer,
    )
    plan = await service.plan_turn(created.id, "Why is my listing score low?")
    assert plan.source == "rewritten_history_first"
    assert plan.validation_status == "rewritten"
    assert plan.parent_plan_id is not None
    names = [call.name for call in plan.tool_calls]
    assert "analyze_listing_v2" not in names
    assert "list_saved_reports" in names
    assert "get_saved_report" in names
    assert plan.needs_confirmation is False


@pytest.mark.asyncio
async def test_planner_llm_failure_uses_fallback() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    conversations.update_slots(created.id, last_asin="B0TEST0001")
    service = PlannerService(
        conversations=conversations,
        registry=_GuardRegistry(default_registry()),
        proposer=_FakeProposer(error=True),
    )
    plan = await service.plan_turn(created.id, "Why is my score low?")
    assert plan.source == "fallback_rules"
    assert [call.name for call in plan.tool_calls] == ["list_saved_reports"]


def test_validator_does_not_accept_invalid_intent() -> None:
    registry = default_registry()
    validator = PlanValidator(registry)
    plan = validator.validate(
        conversation_id=uuid4(),
        user_message="Why is my score low?",
        compact=CompactContext(last_asin="B0TEST0001"),
        catalog=registry.list_tools(),
        proposal=PlannerProposal(intent="invented_intent", tool_calls=[]),
        used_llm=True,
        planner_model="mock-planner",
        planner_prompt_version="copilot_plan",
    )
    assert plan.intent == "explain_listing_score"


@pytest.mark.asyncio
async def test_analyze_reuses_completed_saved_report_without_confirmation() -> None:
    from tests.test_listing_analysis import make_product
    from tests.test_report_lifecycle import _persist_report

    _product, _analysis, persist = _persist_report(product=make_product(asin="B01MD1SKLL"))
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = _service()
    service._conversations = conversations
    plan = await service.plan_turn(created.id, "Analyze B01MD1SKLL")
    names = [call.name for call in plan.tool_calls]
    assert "get_saved_report" in names
    assert "analyze_listing_v2" not in names
    assert "get_product" not in names
    assert plan.needs_confirmation is False
    saved = next(call for call in plan.tool_calls if call.name == "get_saved_report")
    assert saved.arguments["report_id"] == str(persist.report_id)


@pytest.mark.asyncio
async def test_analyze_mixed_case_asin_reuses_saved_report() -> None:
    from tests.test_listing_analysis import make_product
    from tests.test_report_lifecycle import _persist_report

    _persist_report(product=make_product(asin="B01MD1SKLL"))
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = _service()
    service._conversations = conversations
    plan = await service.plan_turn(created.id, "Analyze b01md1skll")
    names = [call.name for call in plan.tool_calls]
    assert "get_saved_report" in names
    assert plan.needs_confirmation is False


@pytest.mark.asyncio
async def test_analyze_without_saved_report_still_requires_confirmation() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = _service()
    service._conversations = conversations
    plan = await service.plan_turn(created.id, "Analyze B01MD1SKLL")
    assert [call.name for call in plan.tool_calls] == ["analyze_listing_v2"]
    assert plan.needs_confirmation is True


def test_planner_modules_do_not_import_forbidden_layers() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "copilot" / "planner"
    forbidden = {
        "openai",
        "app.services.product_service",
        "app.services.listing_analysis_v2_service",
        "app.providers.rainforest",
    }
    imported: set[str] = set()
    source = ""
    for path in root.glob("*.py"):
        text = path.read_text()
        source += text
        tree = ast.parse(text)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    for name in forbidden:
        assert name not in imported, name
    assert ".execute(" not in source
    assert "from app.services.product_service" not in source
    assert "from app.services.listing_analysis_v2_service" not in source

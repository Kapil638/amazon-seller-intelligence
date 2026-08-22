from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.copilot import default_registry
from app.copilot.conversation.service import ConversationService
from app.copilot.orchestrator.schemas import ConfirmRequest, ExecutionRequest
from app.copilot.orchestrator.service import OrchestratorService
from app.copilot.planner.schemas import ApprovedToolCall, Plan
from app.copilot.planner.service import PlannerService
from app.core.exceptions import (
    ConfirmationNonceConsumedError,
    ConfirmationNonceExpiredError,
    ConfirmationNonceInvalidError,
    PlanHashMismatchError,
    PlanNotFoundError,
)
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import CopilotConversationRepository
from tests.test_listing_analysis import make_product
from tests.test_report_lifecycle import _persist_report

CONVERSATIONS_URL = "/api/v1/copilot/conversations"


class _RecordingRegistry:
    def __init__(self, inner) -> None:
        self._inner = inner
        self.calls: list[dict] = []

    def list_tools(self):
        return self._inner.list_tools()

    def get_tool(self, name: str):
        return self._inner.get_tool(name)

    def get_input_schema(self, name: str):
        return self._inner.get_input_schema(name)

    async def execute(self, name, arguments, *, budget, confirmed=False):
        self.calls.append({"name": name, "arguments": arguments, "confirmed": confirmed})
        return await self._inner.execute(name, arguments, budget=budget, confirmed=confirmed)


class _GuardPlannerRegistry:
    def __init__(self, inner) -> None:
        self._inner = inner

    def list_tools(self):
        return self._inner.list_tools()

    def get_tool(self, name: str):
        return self._inner.get_tool(name)

    def get_input_schema(self, name: str):
        return self._inner.get_input_schema(name)

    async def execute(self, *args, **kwargs):
        raise AssertionError("Planner must not call ToolRegistry.execute")


def _conversations() -> ConversationService:
    return ConversationService()


async def _planned(message: str, *, last_asin: str | None = None, last_report_id=None) -> tuple[ConversationService, Plan]:
    conversations = _conversations()
    created = conversations.create_conversation()
    if last_asin is not None or last_report_id is not None:
        conversations.update_slots(created.id, last_asin=last_asin, last_report_id=last_report_id)
    planner = PlannerService(conversations=conversations, registry=_GuardPlannerRegistry(default_registry()))
    plan = await planner.plan_turn(created.id, message)
    return conversations, plan


def _orchestrator(conversations: ConversationService, registry=None) -> tuple[OrchestratorService, _RecordingRegistry]:
    recorded = _RecordingRegistry(registry or default_registry())
    return OrchestratorService(conversations=conversations, registry=recorded), recorded


def _store_plan(conversations: ConversationService, conversation_id, plan: Plan) -> None:
    conversations.add_message(
        conversation_id,
        role="system",
        content="Validated Copilot plan (not executed).",
        structured_payload={"type": "copilot_plan", "plan": plan.model_dump(mode="json")},
    )


@pytest.mark.asyncio
async def test_executes_free_history_tool_successfully() -> None:
    _persist_report(product=make_product(asin="B0TEST0001"))
    conversations, plan = await _planned("Why is my listing score low for B0TEST0001?")
    service, recorded = _orchestrator(conversations)
    result = await service.execute(
        ExecutionRequest(
            plan_id=plan.plan_id,
            conversation_id=plan.conversation_id,
            plan_hash=plan.plan_hash,
            confirmed=True,
        )
    )
    assert result.status == "succeeded"
    assert result.confirmation_required is False
    names = [item.tool_name for item in result.evidence]
    assert "list_saved_reports" in names
    assert "get_saved_report" in names
    assert all(call["confirmed"] is False for call in recorded.calls)
    reports = next(item for item in result.evidence if item.tool_name == "list_saved_reports").value("reports")
    assert any(row["asin"] == "B0TEST0001" for row in reports)


@pytest.mark.asyncio
async def test_blocks_paid_tool_without_confirmation_even_if_client_sets_confirmed() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")
    assert plan.needs_confirmation is True
    service, recorded = _orchestrator(conversations)
    result = await service.execute(
        ExecutionRequest(
            plan_id=plan.plan_id,
            conversation_id=plan.conversation_id,
            plan_hash=plan.plan_hash,
            confirmed=True,
        )
    )
    assert result.status == "blocked_confirmation"
    assert result.confirmation_required is True
    assert result.confirmation_nonce
    assert recorded.calls == []
    fetched = conversations.get_conversation(plan.conversation_id)
    assert fetched.pending_confirmation is not None
    assert fetched.pending_confirmation.nonce_present is True
    assert result.confirmation_nonce not in str(fetched.compact_context.model_dump())


@pytest.mark.asyncio
async def test_executes_paid_tool_after_valid_confirmation() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")
    service, recorded = _orchestrator(conversations)
    blocked = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    result = await service.confirm(plan.conversation_id, ConfirmRequest(nonce=blocked.confirmation_nonce or ""))
    assert result.status == "succeeded"
    assert [item.tool_name for item in result.evidence] == ["analyze_listing_v2"]
    assert recorded.calls[-1]["name"] == "analyze_listing_v2"
    assert recorded.calls[-1]["confirmed"] is True
    fetched = conversations.get_conversation(plan.conversation_id)
    assert fetched.pending_confirmation is None
    assert fetched.compact_context.evidence_refs


@pytest.mark.asyncio
async def test_invalid_nonce_is_rejected() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")
    service, recorded = _orchestrator(conversations)
    await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    with pytest.raises(ConfirmationNonceInvalidError):
        await service.confirm(plan.conversation_id, ConfirmRequest(nonce="not-a-real-nonce"))
    assert recorded.calls == []


@pytest.mark.asyncio
async def test_expired_nonce_is_rejected() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")
    service, recorded = _orchestrator(conversations)
    blocked = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    with session_scope() as session:
        repo = CopilotConversationRepository(session)
        pending = repo.get_active_pending(current_organization_id(), plan.conversation_id)
        assert pending is not None
        pending.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    with pytest.raises(ConfirmationNonceExpiredError):
        await service.confirm(plan.conversation_id, ConfirmRequest(nonce=blocked.confirmation_nonce or ""))
    assert recorded.calls == []


@pytest.mark.asyncio
async def test_consumed_nonce_is_rejected() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")
    service, recorded = _orchestrator(conversations)
    blocked = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    nonce = blocked.confirmation_nonce or ""
    first = await service.confirm(plan.conversation_id, ConfirmRequest(nonce=nonce))
    assert first.status == "succeeded"
    with pytest.raises(ConfirmationNonceConsumedError):
        await service.confirm(plan.conversation_id, ConfirmRequest(nonce=nonce))
    assert len([call for call in recorded.calls if call["name"] == "analyze_listing_v2"]) == 1


@pytest.mark.asyncio
async def test_modified_plan_hash_is_rejected() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")
    service, recorded = _orchestrator(conversations)
    blocked = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    with pytest.raises(PlanHashMismatchError):
        await service.execute(
            ExecutionRequest(
                plan_id=plan.plan_id,
                conversation_id=plan.conversation_id,
                plan_hash="0" * 64,
                confirmation_nonce=blocked.confirmation_nonce,
            )
        )
    assert recorded.calls == []


@pytest.mark.asyncio
async def test_unknown_tool_fails_through_registry_only() -> None:
    conversations, plan = await _planned("Why is my listing score low for B0TEST0001?")
    evil = plan.model_copy(
        update={
            "plan_id": uuid4(),
            "tool_calls": [ApprovedToolCall(name="not_a_real_tool", arguments={})],
            "needs_confirmation": False,
        }
    )
    _store_plan(conversations, plan.conversation_id, evil)
    service, recorded = _orchestrator(conversations)
    result = await service.execute(
        ExecutionRequest(plan_id=evil.plan_id, conversation_id=plan.conversation_id, plan_hash=evil.plan_hash)
    )
    assert result.status == "failed"
    assert result.tool_results[0].error_code == "unknown_tool"
    assert recorded.calls == [{"name": "not_a_real_tool", "arguments": {}, "confirmed": False}]


@pytest.mark.asyncio
async def test_invalid_plan_is_rejected() -> None:
    conversations = _conversations()
    created = conversations.create_conversation()
    service, recorded = _orchestrator(conversations)
    with pytest.raises(PlanNotFoundError):
        await service.execute(
            ExecutionRequest(plan_id=uuid4(), conversation_id=created.id, plan_hash="abc123")
        )
    assert recorded.calls == []


@pytest.mark.asyncio
async def test_budget_failure_stops_execution() -> None:
    conversations, plan = await _planned("Show my saved history")
    extra = [
        ApprovedToolCall(name="list_saved_reports", arguments={"limit": 1})
        for _ in range(5)
    ]
    bloated = plan.model_copy(update={"plan_id": uuid4(), "tool_calls": extra, "needs_confirmation": False})
    _store_plan(conversations, plan.conversation_id, bloated)
    service, recorded = _orchestrator(conversations)
    result = await service.execute(
        ExecutionRequest(
            plan_id=bloated.plan_id,
            conversation_id=plan.conversation_id,
            plan_hash=bloated.plan_hash,
        )
    )
    assert result.status == "failed"
    assert [item.status for item in result.tool_results[:4]] == ["succeeded"] * 4
    assert result.tool_results[4].error_code == "budget_exceeded"
    assert result.tool_results[4].status == "failed"


@pytest.mark.asyncio
async def test_tool_execution_failure_does_not_fabricate_evidence() -> None:
    conversations, plan = await _planned("Analyze B0TEST0001")

    class _BoomRegistry(_RecordingRegistry):
        async def execute(self, name, arguments, *, budget, confirmed=False):
            self.calls.append({"name": name, "arguments": arguments, "confirmed": confirmed})
            raise RuntimeError("provider exploded")

    recorded = _BoomRegistry(default_registry())
    service = OrchestratorService(conversations=conversations, registry=recorded)
    blocked = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    result = await service.confirm(plan.conversation_id, ConfirmRequest(nonce=blocked.confirmation_nonce or ""))
    assert result.status == "failed"
    assert result.evidence == []
    assert result.tool_results[0].error_code == "tool_execution_failed"


@pytest.mark.asyncio
async def test_history_first_plan_does_not_call_product_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product, analysis, persist = _persist_report(product=make_product(asin="B0TEST0001"))
    conversations, plan = await _planned(
        "Why is my listing score low?",
        last_asin="B0TEST0001",
        last_report_id=persist.report_id,
    )
    assert "analyze_listing_v2" not in [call.name for call in plan.tool_calls]
    assert "get_saved_report" in [call.name for call in plan.tool_calls]

    async def _forbidden(*args, **kwargs):
        raise AssertionError("ProductService must not run for History-first plans")

    monkeypatch.setattr("app.services.product_service.ProductService.fetch_product", _forbidden)
    service, recorded = _orchestrator(conversations)
    result = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    assert result.status == "succeeded"
    names = [call["name"] for call in recorded.calls]
    assert "analyze_listing_v2" not in names
    assert "get_product" not in names
    assert "get_saved_report" in names
    saved = next(item for item in result.evidence if item.tool_name == "get_saved_report")
    assert saved.value("listing_quality_score") == analysis.listing_quality_score
    assert saved.value("asin") == product.asin


@pytest.mark.asyncio
async def test_asin_analysis_runs_only_after_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    original = None
    from app.services.product_service import ProductService

    original = ProductService.fetch_product

    async def _counted(self, *args, **kwargs):
        calls["n"] += 1
        return await original(self, *args, **kwargs)

    monkeypatch.setattr(ProductService, "fetch_product", _counted)
    conversations, plan = await _planned("Analyze B0TEST0001")
    service, _recorded = _orchestrator(conversations)
    blocked = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    assert calls["n"] == 0
    result = await service.confirm(plan.conversation_id, ConfirmRequest(nonce=blocked.confirmation_nonce or ""))
    assert result.status == "succeeded"
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_analyze_existing_saved_report_skips_amazon_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _product, analysis, persist = _persist_report(product=make_product(asin="B01MD1SKLL"))
    conversations, plan = await _planned("Analyze B01MD1SKLL")
    assert "get_saved_report" in [call.name for call in plan.tool_calls]
    assert plan.needs_confirmation is False

    async def _forbidden(*args, **kwargs):
        raise AssertionError("Amazon lookup must not run when a saved report exists")

    monkeypatch.setattr("app.services.product_service.ProductService.fetch_product", _forbidden)
    service, recorded = _orchestrator(conversations)
    result = await service.execute(
        ExecutionRequest(plan_id=plan.plan_id, conversation_id=plan.conversation_id, plan_hash=plan.plan_hash)
    )
    assert result.status == "succeeded"
    names = [call["name"] for call in recorded.calls]
    assert "get_saved_report" in names
    assert "analyze_listing_v2" not in names
    assert "get_product" not in names
    assert all(call["confirmed"] is False for call in recorded.calls)
    saved = next(item for item in result.evidence if item.tool_name == "get_saved_report")
    assert saved.value("asin") == "B01MD1SKLL"
    assert saved.value("listing_quality_score") == analysis.listing_quality_score
    assert saved.value("report_id") == str(persist.report_id)


@pytest.mark.asyncio
async def test_http_execute_and_confirm(client: TestClient) -> None:
    created = client.post(CONVERSATIONS_URL, json={}).json()
    planned = client.post(
        f"{CONVERSATIONS_URL}/{created['id']}/plan",
        json={"user_message": "Analyze B0TEST0001", "organization_id": str(uuid4()), "confirmed": True},
    )
    assert planned.status_code == 200, planned.text
    plan = planned.json()
    blocked = client.post(
        f"{CONVERSATIONS_URL}/{created['id']}/execute",
        json={
            "plan_id": plan["plan_id"],
            "plan_hash": plan["plan_hash"],
            "confirmed": True,
            "organization_id": str(uuid4()),
        },
    )
    assert blocked.status_code == 200, blocked.text
    body = blocked.json()
    assert body["status"] == "blocked_confirmation"
    assert body["confirmation_nonce"]
    fetched = client.get(f"{CONVERSATIONS_URL}/{created['id']}").json()
    assert fetched["pending_confirmation"]["nonce_present"] is True
    assert body["confirmation_nonce"] not in str(fetched)

    confirmed = client.post(
        f"{CONVERSATIONS_URL}/{created['id']}/confirm",
        json={"nonce": body["confirmation_nonce"], "confirmed": True},
    )
    assert confirmed.status_code == 200, confirmed.text
    payload = confirmed.json()
    assert payload["status"] == "succeeded"
    assert payload["evidence"][0]["tool_name"] == "analyze_listing_v2"
    replay = client.post(
        f"{CONVERSATIONS_URL}/{created['id']}/confirm",
        json={"nonce": body["confirmation_nonce"]},
    )
    assert replay.status_code == 409


def test_orchestrator_modules_do_not_call_services_directly() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "copilot" / "orchestrator"
    forbidden = {
        "openai",
        "app.services.product_service",
        "app.services.listing_analysis_v2_service",
        "app.services.analysis_history_service",
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
    assert "self._registry.execute(" in source
    assert "ProductService" not in source
    assert "ListingAnalysisV2Service" not in source

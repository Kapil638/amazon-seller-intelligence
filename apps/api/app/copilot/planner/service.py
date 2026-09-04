"""Hybrid planner: LLM may propose, application validates. Does not execute tools."""

from __future__ import annotations

from typing import Any, Protocol
from uuid import UUID

from pydantic import ValidationError

from app.copilot import default_registry
from app.copilot.conversation.service import ConversationService
from app.copilot.planner.prompts import PROMPT_VERSION, REPAIR_PROMPT, SYSTEM_PROMPT, build_user_prompt
from app.copilot.planner.schemas import Plan, PlannerProposal, PlannerRequest
from app.copilot.planner.validator import PlanValidator, extract_slots
from app.copilot.registry import ToolRegistry
from app.models.copilot_conversation import CompactContext
from app.persistence.database import sqlalchemy_database_url


class PlannerProposer(Protocol):
    """Optional language model. Failures must be treated as missing proposals."""

    async def propose(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> tuple[PlannerProposal | None, str | None]: ...


class AIProviderPlannerProposer:
    """One structured generate_structured call. Never executes Copilot tools."""

    def __init__(self, provider: Any) -> None:
        self._provider = provider

    async def propose(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        repair_prompt: str,
        prompt_version: str,
    ) -> tuple[PlannerProposal | None, str | None]:
        result = await self._provider.generate_structured(
            schema=PlannerProposal,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            repair_prompt=repair_prompt,
            prompt_version=prompt_version,
        )
        payload = result.payload
        if isinstance(payload, PlannerProposal):
            return payload, getattr(self._provider, "model", None)
        try:
            return PlannerProposal.model_validate(payload), getattr(self._provider, "model", None)
        except ValidationError:
            return None, getattr(self._provider, "model", None)


class PlannerService:
    """Conversation Manager → Context → propose → PlanValidator → Plan.

    Stops at a validated Plan. Does not run tools or write seller answers.
    """

    def __init__(
        self,
        *,
        conversations: ConversationService | None = None,
        registry: ToolRegistry | None = None,
        validator: PlanValidator | None = None,
        proposer: PlannerProposer | None = None,
    ) -> None:
        self._conversations = conversations or ConversationService()
        self._registry = registry or default_registry()
        self._validator = validator or PlanValidator(self._registry)
        self._proposer = proposer

    async def create_plan(self, request: PlannerRequest) -> Plan:
        catalog = request.available_tools or self._registry.list_tools()
        compact = request.compact_context
        proposal, model_id, used_llm = await self._propose(
            request.user_message, compact, catalog, request.marketplace_participation_id
        )
        plan = self._validator.validate(
            conversation_id=request.conversation_id,
            user_message=request.user_message,
            compact=compact,
            catalog=catalog,
            proposal=proposal,
            used_llm=used_llm,
            planner_model=model_id,
            planner_prompt_version=PROMPT_VERSION if used_llm else None,
            marketplace_participation_id=request.marketplace_participation_id,
            period_days=request.period_days,
            force_refresh=request.force_refresh,
        )
        await self._persist(request, plan)
        return plan

    async def plan_turn(
        self,
        conversation_id: UUID,
        user_message: str,
        *,
        marketplace_participation_id: UUID | None = None,
        period_days: int | None = None,
        force_refresh: bool = False,
    ) -> Plan:
        detail = self._conversations.get_conversation(conversation_id)
        request = PlannerRequest(
            user_message=user_message,
            conversation_id=conversation_id,
            compact_context=detail.compact_context,
            available_tools=self._registry.list_tools(),
            marketplace_participation_id=marketplace_participation_id,
            period_days=period_days,
            force_refresh=force_refresh,
        )
        return await self.create_plan(request)

    async def _propose(
        self,
        user_message: str,
        compact: CompactContext,
        catalog: list,
        marketplace_participation_id: UUID | None = None,
    ) -> tuple[PlannerProposal | None, str | None, bool]:
        if self._proposer is None:
            return None, None, False
        planner_context = {
            "last_asin": compact.last_asin,
            "last_report_id": str(compact.last_report_id) if compact.last_report_id else None,
            "previous_intent": compact.previous_intent,
            "pending_confirmation": (
                compact.pending_confirmation.model_dump() if compact.pending_confirmation else None
            ),
            "recent_user_snippets": list(compact.recent_user_snippets),
            "marketplace_participation_id": (
                str(marketplace_participation_id) if marketplace_participation_id else None
            ),
        }
        tools = [item.model_dump() for item in catalog]
        try:
            proposal, model_id = await self._proposer.propose(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(
                    user_message=user_message,
                    compact_context=planner_context,
                    available_tools=tools,
                ),
                repair_prompt=REPAIR_PROMPT,
                prompt_version=PROMPT_VERSION,
            )
        except Exception:
            return None, None, False
        if proposal is None:
            return None, model_id, False
        return proposal, model_id, True

    async def _persist(self, request: PlannerRequest, plan: Plan) -> None:
        slots = extract_slots(request.user_message, request.compact_context)
        self._conversations.add_message(request.conversation_id, role="user", content=request.user_message)
        self._conversations.update_slots(
            request.conversation_id,
            last_asin=slots.asin,
            last_report_id=slots.report_id,
            previous_intent=plan.intent,
        )
        self._conversations.add_message(
            request.conversation_id,
            role="system",
            content="Validated Copilot plan (not executed).",
            structured_payload={"type": "copilot_plan", "plan": plan.model_dump(mode="json")},
        )


def _sqlite_test_database() -> bool:
    url = sqlalchemy_database_url()
    return url.startswith("sqlite")


def get_planner_service() -> PlannerService:
    """Production may attach an LLM proposer; SQLite/tests stay on fallback rules."""
    proposer: PlannerProposer | None = None
    if not _sqlite_test_database():
        try:
            from app.ai.factory import get_ai_provider

            proposer = AIProviderPlannerProposer(get_ai_provider())
        except Exception:
            proposer = None
    return PlannerService(proposer=proposer)

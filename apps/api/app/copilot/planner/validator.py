"""Application plan validator. Does not execute tools or call OpenAI."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import ValidationError

from app.copilot.budget import (
    COST_NONE,
    COST_OPENAI,
    COST_RAINFOREST_PRODUCT,
    COST_RAINFOREST_SEARCH,
    MAX_TOOL_ROUNDS,
    MAX_TOOLS_PER_TURN,
)
from app.copilot.planner.schemas import (
    INTENT_VALUES,
    PLAN_SCHEMA_VERSION,
    ApprovedToolCall,
    BudgetSnapshot,
    Intent,
    Plan,
    PlannerProposal,
    ProposedToolCall,
    RejectedToolCall,
)
from app.copilot.registry import ToolCatalogEntry, ToolRegistry
from app.core.exceptions import PersistenceNotConfiguredError
from app.core.validation import is_valid_asin, normalize_asin
from app.models.copilot_conversation import CompactContext
from app.persistence.database import current_organization_id
from app.services.analysis_history_service import AnalysisHistoryService

_PERMISSION_KEYS = frozenset({"confirmed", "budget", "handler"})
_ASIN_IN_TEXT = re.compile(r"\b([A-Z0-9]{10})\b")
_UUID_IN_TEXT = re.compile(
    r"\b([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})\b"
)
_OUT_OF_SCOPE = (
    "competitor",
    "competitors",
    "ppc",
    "acos",
    "advertis",
    "sponsored",
    "profit",
    "p&l",
    "launch this",
    "should i launch",
)
_CHANGE_WORDS = ("changed", "vs last", "versus last", "last month", "compared to last", "what changed")
_ANALYZE_WORDS = ("analyze", "analyse", "look up")
_REFRESH_WORDS = ("refresh", "re-analyze", "reanalyze", "new analysis")
_EXPLAIN_WORDS = ("why", "score", "low", "explain", "findings", "fix first")
_HISTORY_WORDS = ("history", "saved report", "saved analysis", "past report")
_SUMMARIZE_WORDS = ("summarize", "summarise", "what did we conclude", "last time")
_FETCH_TOOLS = frozenset({"analyze_listing_v2", "get_product"})


class ExtractedSlots:
    def __init__(self, asin: str | None = None, report_id: UUID | None = None) -> None:
        self.asin = asin
        self.report_id = report_id


def asin_from_message(user_message: str) -> str | None:
    text = user_message or ""
    for match in _ASIN_IN_TEXT.findall(text.upper()):
        if is_valid_asin(match):
            return normalize_asin(match)
    return None


def extract_slots(user_message: str, compact: CompactContext) -> ExtractedSlots:
    text = user_message or ""
    asin: str | None = None
    for match in _ASIN_IN_TEXT.findall(text.upper()):
        if is_valid_asin(match):
            asin = normalize_asin(match)
            break
    if asin is None and compact.last_asin and is_valid_asin(compact.last_asin.upper()):
        asin = normalize_asin(compact.last_asin)

    report_id: UUID | None = None
    found = _UUID_IN_TEXT.search(text)
    if found:
        report_id = UUID(found.group(1))
    elif compact.last_report_id is not None:
        report_id = compact.last_report_id
    return ExtractedSlots(asin=asin, report_id=report_id)


def infer_fallback_intent(
    user_message: str,
    slots: ExtractedSlots,
    previous_intent: str | None = None,
) -> Intent:
    text = (user_message or "").lower()
    if any(token in text for token in _OUT_OF_SCOPE):
        return "out_of_scope"
    if any(token in text for token in _CHANGE_WORDS):
        return "what_changed"
    if any(token in text for token in _REFRESH_WORDS):
        return "analyze_asin"
    if any(token in text for token in _ANALYZE_WORDS) and "why" not in text:
        return "analyze_asin"
    if any(token in text for token in _SUMMARIZE_WORDS):
        return "summarize_report"
    if any(token in text for token in _HISTORY_WORDS) and "why" not in text:
        return "list_history"
    if any(token in text for token in _EXPLAIN_WORDS):
        return "explain_listing_score"
    if previous_intent == "analyze_asin" and (slots.asin is not None or slots.report_id is not None):
        return "analyze_asin"
    if slots.report_id is not None or slots.asin is not None:
        return "explain_listing_score"
    return "clarify"


def wants_refresh(user_message: str) -> bool:
    text = (user_message or "").lower()
    return any(token in text for token in _REFRESH_WORDS)


def fallback_tool_calls(intent: Intent, slots: ExtractedSlots) -> list[ProposedToolCall]:
    if intent in {"out_of_scope", "clarify"}:
        return []
    if intent == "list_history":
        arguments: dict[str, Any] = {"limit": 10}
        if slots.asin:
            arguments["asin"] = slots.asin
        return [ProposedToolCall(name="list_saved_reports", arguments=arguments)]
    if intent == "what_changed":
        if not slots.asin:
            return []
        return [ProposedToolCall(name="list_saved_reports", arguments={"asin": slots.asin, "limit": 10})]
    if intent in {"explain_listing_score", "summarize_report"}:
        return history_first_calls(slots)
    if intent == "analyze_asin":
        if slots.report_id is not None:
            return history_first_calls(slots)
        if slots.asin:
            return [ProposedToolCall(name="analyze_listing_v2", arguments={"asin": slots.asin})]
        return []
    return []


def history_first_calls(slots: ExtractedSlots) -> list[ProposedToolCall]:
    calls: list[ProposedToolCall] = []
    if slots.asin:
        calls.append(ProposedToolCall(name="list_saved_reports", arguments={"asin": slots.asin, "limit": 10}))
    if slots.report_id is not None:
        calls.append(ProposedToolCall(name="get_saved_report", arguments={"report_id": str(slots.report_id)}))
    return calls


def catalog_hash(tools: list[ToolCatalogEntry]) -> str:
    payload = [
        {
            "name": item.name,
            "cost": item.cost,
            "confirmation_required": item.confirmation_required,
            "input_schema": item.input_schema,
        }
        for item in sorted(tools, key=lambda row: row.name)
    ]
    return _hash_json(payload)


def compute_plan_hash(intent: str, tool_calls: list[ApprovedToolCall]) -> str:
    payload = {
        "plan_schema_version": PLAN_SCHEMA_VERSION,
        "intent": intent,
        "tool_calls": [{"name": call.name, "arguments": call.arguments} for call in tool_calls],
    }
    return _hash_json(payload)


def _hash_json(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class PlanValidator:
    """Accept, rewrite (History-first), or reject a proposal. Never calls execute()."""

    def __init__(self, registry: ToolRegistry, history: AnalysisHistoryService | None = None) -> None:
        self._registry = registry
        self._history = history or AnalysisHistoryService()

    def validate(
        self,
        *,
        conversation_id: UUID,
        user_message: str,
        compact: CompactContext,
        catalog: list[ToolCatalogEntry],
        proposal: PlannerProposal | None,
        used_llm: bool,
        planner_model: str | None,
        planner_prompt_version: str | None,
    ) -> Plan:
        slots = extract_slots(user_message, compact)
        typed_asin = asin_from_message(user_message)
        fallback_intent = infer_fallback_intent(user_message, slots, compact.previous_intent)
        if fallback_intent == "analyze_asin" and typed_asin is None:
            slots.asin = None
            if not _UUID_IN_TEXT.search(user_message or ""):
                slots.report_id = None
        elif not wants_refresh(user_message):
            if typed_asin:
                slots.asin = typed_asin
            slots = self._bind_saved_report(slots)
        registered = {item.name for item in catalog}
        rejected: list[RejectedToolCall] = []
        parent_plan_id: UUID | None = None
        plan_version = 1
        source = "fallback_rules"
        status = "accepted"
        planner_model_out = None
        prompt_version_out = None

        if proposal is None:
            intent = fallback_intent
            approved, extra = self._sanitize_calls(fallback_tool_calls(intent, slots), registered)
            rejected.extend(extra)
        else:
            intent = self._coerce_intent(proposal.intent, fallback_intent)
            approved, extra = self._sanitize_calls(proposal.tool_calls, registered)
            rejected.extend(extra)
            source = "planner_llm" if used_llm else "fallback_rules"
            planner_model_out = planner_model if used_llm else None
            prompt_version_out = planner_prompt_version if used_llm else None

            if intent == "out_of_scope" and approved:
                rejected.extend(
                    RejectedToolCall(name=call.name, arguments=call.arguments, reason="out_of_scope")
                    for call in approved
                )
                approved = []
                status = "rewritten"
                parent_plan_id = uuid4()
                plan_version = 2

            if len(approved) > MAX_TOOLS_PER_TURN:
                rejected.extend(
                    RejectedToolCall(name=call.name, arguments=call.arguments, reason="budget")
                    for call in approved
                )
                approved = []
                intent = fallback_intent
                recovered, extra = self._sanitize_calls(fallback_tool_calls(intent, slots), registered)
                rejected.extend(extra)
                approved = recovered
                source = "fallback_rules"
                status = "accepted"
                planner_model_out = None
                prompt_version_out = None

            approved, hist_rejected, rewritten = self._history_first(
                intent=intent,
                approved=approved,
                slots=slots,
                user_message=user_message,
                registered=registered,
            )
            rejected.extend(hist_rejected)
            if rewritten:
                status = "rewritten"
                source = "rewritten_history_first"
                parent_plan_id = parent_plan_id or uuid4()
                plan_version = 2

            if not approved and intent not in {"out_of_scope", "clarify"}:
                recovered, extra = self._sanitize_calls(fallback_tool_calls(fallback_intent, slots), registered)
                rejected.extend(extra)
                if recovered:
                    approved = recovered
                    intent = fallback_intent
                    source = "fallback_rules"
                    status = "accepted"
                    planner_model_out = None
                    prompt_version_out = None
                else:
                    intent = (
                        fallback_intent
                        if fallback_intent in {"clarify", "out_of_scope", "analyze_asin"}
                        else "clarify"
                    )
                    source = "fallback_rules"
                    status = "accepted"

        if intent == "analyze_asin" and typed_asin is None:
            rejected.extend(
                RejectedToolCall(name=call.name, arguments=call.arguments, reason="asin_required")
                for call in approved
            )
            approved = []
            intent = "analyze_asin"

        if intent in {"out_of_scope", "clarify"}:
            approved = []
        if intent == "clarify" and not slots.asin and not slots.report_id:
            approved = []

        return self._to_plan(
            conversation_id=conversation_id,
            intent=intent,
            approved=approved,
            rejected=rejected,
            source=source,
            status=status,
            parent_plan_id=parent_plan_id,
            plan_version=plan_version,
            planner_model=planner_model_out,
            planner_prompt_version=prompt_version_out,
            catalog=catalog,
            slots=slots,
        )

    def _to_plan(
        self,
        *,
        conversation_id: UUID,
        intent: Intent,
        approved: list[ApprovedToolCall],
        rejected: list[RejectedToolCall],
        source: str,
        status: str,
        parent_plan_id: UUID | None,
        plan_version: int,
        planner_model: str | None,
        planner_prompt_version: str | None,
        catalog: list[ToolCatalogEntry],
        slots: ExtractedSlots,
    ) -> Plan:
        costs = [self._cost_for(call) for call in approved]
        paid = {COST_RAINFOREST_PRODUCT, COST_RAINFOREST_SEARCH, COST_OPENAI}
        needs_confirmation = any(cost in paid for cost in costs)
        confirm_summary = None
        if needs_confirmation:
            confirm_summary = (
                "You don’t have a saved analysis for this ASIN. "
                "Looking it up on Amazon uses product credits."
                if slots.asin
                else "Continuing will use Amazon or OpenAI credits."
            )

        return Plan(
            plan_id=uuid4(),
            plan_version=plan_version,
            plan_schema_version=PLAN_SCHEMA_VERSION,
            conversation_id=conversation_id,
            turn_id=uuid4(),
            organization_id=current_organization_id(),
            intent=intent,
            planner_model=planner_model,
            planner_prompt_version=planner_prompt_version,
            created_at=datetime.now(UTC),
            tool_calls=approved,
            rejected_calls=rejected,
            validation_status=status,  # type: ignore[arg-type]
            rejection_reason=None,
            parent_plan_id=parent_plan_id,
            source=source,  # type: ignore[arg-type]
            catalog_hash=catalog_hash(catalog),
            budget_snapshot=BudgetSnapshot(
                max_tool_rounds=MAX_TOOL_ROUNDS,
                max_tools_per_turn=MAX_TOOLS_PER_TURN,
            ),
            needs_confirmation=needs_confirmation,
            confirm_summary=confirm_summary,
            plan_hash=compute_plan_hash(intent, approved),
        )

    def _sanitize_calls(
        self,
        calls: list[ProposedToolCall] | list[ApprovedToolCall],
        registered: set[str],
    ) -> tuple[list[ApprovedToolCall], list[RejectedToolCall]]:
        approved: list[ApprovedToolCall] = []
        rejected: list[RejectedToolCall] = []
        for call in calls:
            name = call.name
            raw = dict(call.arguments or {})
            if name not in registered:
                rejected.append(RejectedToolCall(name=name, arguments=raw, reason="unknown_tool"))
                continue
            if "product" in raw and name == "analyze_listing_v2":
                rejected.append(RejectedToolCall(name=name, arguments=raw, reason="product_blob_forbidden"))
                continue
            for key in _PERMISSION_KEYS:
                raw.pop(key, None)
            raw.pop("product", None)
            try:
                schema = self._registry.get_input_schema(name)
                payload = schema.model_validate(raw)
            except (ValidationError, Exception):
                rejected.append(RejectedToolCall(name=name, arguments=raw, reason="schema_invalid"))
                continue
            approved.append(ApprovedToolCall(name=name, arguments=payload.model_dump(mode="json")))
        return approved, rejected

    def _history_first(
        self,
        *,
        intent: Intent,
        approved: list[ApprovedToolCall],
        slots: ExtractedSlots,
        user_message: str,
        registered: set[str],
    ) -> tuple[list[ApprovedToolCall], list[RejectedToolCall], bool]:
        if wants_refresh(user_message):
            return approved, [], False
        if intent == "analyze_asin":
            if slots.report_id is None:
                return approved, [], False
        elif intent not in {"explain_listing_score", "summarize_report", "what_changed"}:
            return approved, [], False
        has_fetch = any(call.name in _FETCH_TOOLS for call in approved)
        if not has_fetch:
            return approved, [], False
        if not slots.asin and slots.report_id is None:
            return approved, [], False
        rejected = [
            RejectedToolCall(name=call.name, arguments=call.arguments, reason="rewritten_history_first")
            for call in approved
            if call.name in _FETCH_TOOLS
        ]
        rewritten, extra = self._sanitize_calls(history_first_calls(slots), registered)
        kept = [call for call in approved if call.name not in _FETCH_TOOLS]
        merged: list[ApprovedToolCall] = []
        seen: set[tuple[str, str]] = set()
        for call in rewritten + kept:
            key = (call.name, json.dumps(call.arguments, sort_keys=True, default=str))
            if key in seen:
                continue
            seen.add(key)
            merged.append(call)
        return merged, rejected + extra, True

    def _bind_saved_report(self, slots: ExtractedSlots) -> ExtractedSlots:
        if slots.report_id is not None or not slots.asin:
            return slots
        try:
            report_id = self._history.latest_complete_report_id(slots.asin)
        except PersistenceNotConfiguredError:
            return slots
        except Exception:
            return slots
        if report_id is not None:
            slots.report_id = report_id
        return slots

    def _coerce_intent(self, value: str, fallback: Intent) -> Intent:
        if value in INTENT_VALUES:
            return value  # type: ignore[return-value]
        return fallback

    def _cost_for(self, call: ApprovedToolCall) -> str:
        try:
            return self._registry.get_tool(call.name).cost
        except Exception:
            return COST_NONE

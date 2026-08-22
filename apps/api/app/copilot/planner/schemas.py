"""Planner request, untrusted proposal, and versioned Plan. Not an execution record."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.copilot.registry import ToolCatalogEntry
from app.models.copilot_conversation import CompactContext

PLAN_SCHEMA_VERSION = "copilot-plan-v1"

Intent = Literal[
    "explain_listing_score",
    "summarize_report",
    "list_history",
    "analyze_asin",
    "what_changed",
    "explain_profit",
    "explain_advertising_impact",
    "out_of_scope",
    "clarify",
]

INTENT_VALUES: tuple[str, ...] = (
    "explain_listing_score",
    "summarize_report",
    "list_history",
    "analyze_asin",
    "what_changed",
    "explain_profit",
    "explain_advertising_impact",
    "out_of_scope",
    "clarify",
)

PlanSource = Literal["planner_llm", "fallback_rules", "rewritten_history_first"]
ValidationStatus = Literal["accepted", "rejected", "rewritten"]


class CopilotIgnoreExtra(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ProposedToolCall(CopilotIgnoreExtra):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class PlannerProposal(CopilotIgnoreExtra):
    """Untrusted LLM output. Must be validated before anything runs."""

    intent: str
    slots: dict[str, Any] = Field(default_factory=dict)
    tool_calls: list[ProposedToolCall] = Field(default_factory=list)


class PlannerRequest(CopilotIgnoreExtra):
    user_message: str = Field(min_length=1, max_length=8000)
    conversation_id: UUID
    compact_context: CompactContext = Field(default_factory=CompactContext)
    available_tools: list[ToolCatalogEntry] = Field(default_factory=list)


class PlanTurnRequest(CopilotIgnoreExtra):
    """HTTP body for a plan-only turn. organization_id is ignored if sent."""

    user_message: str = Field(min_length=1, max_length=8000)


class ApprovedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class RejectedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str


class BudgetSnapshot(BaseModel):
    max_tool_rounds: int
    max_tools_per_turn: int
    tools_this_turn: int = 0
    rainforest_product_calls: int = 0
    rainforest_search_calls: int = 0
    openai_calls: int = 0


class Plan(BaseModel):
    """Validated plan. Orchestrators consume this object, never raw LLM JSON."""

    plan_id: UUID
    plan_version: int = 1
    plan_schema_version: str = PLAN_SCHEMA_VERSION
    conversation_id: UUID
    turn_id: UUID
    organization_id: UUID
    intent: Intent
    planner_model: str | None = None
    planner_prompt_version: str | None = None
    created_at: datetime
    tool_calls: list[ApprovedToolCall] = Field(default_factory=list)
    rejected_calls: list[RejectedToolCall] = Field(default_factory=list)
    validation_status: ValidationStatus
    rejection_reason: str | None = None
    parent_plan_id: UUID | None = None
    source: PlanSource
    catalog_hash: str
    budget_snapshot: BudgetSnapshot
    needs_confirmation: bool = False
    confirm_summary: str | None = None
    plan_hash: str

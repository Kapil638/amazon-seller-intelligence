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
    # 12B.5A — Listings + Orders launch skills.
    "prioritize_listing_health",
    "investigate_non_buyable_listing",
    "analyze_order_trends",
    "detect_cancellation_anomalies",
    "rank_listing_risk_by_order_exposure",
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
    "prioritize_listing_health",
    "investigate_non_buyable_listing",
    "analyze_order_trends",
    "detect_cancellation_anomalies",
    "rank_listing_risk_by_order_exposure",
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
    marketplace_participation_id: UUID | None = None
    period_days: int | None = Field(default=None, ge=1, le=90)


class PlanTurnRequest(CopilotIgnoreExtra):
    """HTTP body for a plan-only turn. organization_id is ignored if sent.

    `marketplace_participation_id` (12B.5A) is the seller's currently
    selected marketplace in the Copilot UI — required to route any of
    the five Listings/Orders skill intents. Deliberately per-turn, never
    persisted into `CompactContext`: the frontend always knows which
    marketplace is selected right now, so re-sending it here is simpler
    and safer than trying to remember a "last selected marketplace"
    across turns that could go stale. Ownership is still re-validated
    inside the skill's own evidence service on every call — this field
    is never trusted as proof of ownership by itself.

    `period_days` is the seller's currently selected analysis window in
    the Copilot UI — same per-turn, never-persisted treatment as
    `marketplace_participation_id`. `None` lets each tool apply its own
    default (30 days); the tool's own schema still bounds it to [1, 90]
    regardless of what this field allows through.
    """

    user_message: str = Field(min_length=1, max_length=8000)
    marketplace_participation_id: UUID | None = None
    period_days: int | None = Field(default=None, ge=1, le=90)


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

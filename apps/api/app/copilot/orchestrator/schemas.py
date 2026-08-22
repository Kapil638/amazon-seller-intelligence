"""Orchestrator request/result schemas. Not seller-facing synthesis."""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.copilot.evidence import EvidenceEnvelope

PAID_TOOLS = frozenset({"get_product", "analyze_listing_v2"})
FREE_TOOLS = frozenset({"list_saved_reports", "get_saved_report"})
ALLOWED_TOOLS = PAID_TOOLS | FREE_TOOLS

ToolCallStatus = Literal["planned", "blocked_confirmation", "succeeded", "failed", "skipped"]
ExecutionStatus = Literal["succeeded", "blocked_confirmation", "failed"]


class CopilotIgnoreExtra(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ExecutionRequest(CopilotIgnoreExtra):
    """Untrusted client request. organization_id and confirmed are ignored for grants."""

    plan_id: UUID
    conversation_id: UUID
    plan_hash: str = Field(min_length=1, max_length=64)
    confirmation_nonce: str | None = None
    confirmed: bool = False


class ExecuteTurnRequest(CopilotIgnoreExtra):
    """HTTP body for plan execution. Path supplies conversation_id."""

    plan_id: UUID
    plan_hash: str = Field(min_length=1, max_length=64)
    confirmation_nonce: str | None = None
    confirmed: bool = False


class ConfirmRequest(CopilotIgnoreExtra):
    """HTTP body for seller confirmation. Server grant only."""

    nonce: str = Field(min_length=1, max_length=64)


class ConfirmationGrant(BaseModel):
    """Server-owned permission. Never constructed from model JSON."""

    nonce: str
    plan_id: UUID
    plan_hash: str
    conversation_id: UUID


class ToolCallResult(BaseModel):
    name: str
    status: ToolCallStatus
    evidence: EvidenceEnvelope | None = None
    error_code: str | None = None
    error_message: str | None = None


class ExecutionResult(BaseModel):
    plan_id: UUID
    conversation_id: UUID
    organization_id: UUID
    plan_hash: str
    status: ExecutionStatus
    confirmation_required: bool = False
    confirmation_nonce: str | None = None
    confirm_summary: str | None = None
    evidence: list[EvidenceEnvelope] = Field(default_factory=list)
    tool_results: list[ToolCallResult] = Field(default_factory=list)

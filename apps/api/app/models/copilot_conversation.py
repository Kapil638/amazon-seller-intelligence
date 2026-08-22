from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CopilotConversationCreate(BaseModel):
    """Client may send extra keys such as organization_id; they are ignored."""

    model_config = ConfigDict(extra="ignore")

    title: str | None = Field(default=None, max_length=200)


class ConversationMessage(BaseModel):
    id: UUID
    role: str
    content: str
    created_at: datetime


class PendingConfirmationPublic(BaseModel):
    """Safe view of a pending confirm. Never includes the nonce."""

    plan_id: UUID | None = None
    nonce_present: bool = False
    summary: str | None = None


class EvidenceRef(BaseModel):
    evidence_id: UUID
    claim_keys: list[str] = Field(default_factory=list)


class CompactContext(BaseModel):
    last_asin: str | None = None
    last_report_id: UUID | None = None
    previous_intent: str | None = None
    pending_confirmation: PendingConfirmationPublic | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    recent_user_snippets: list[str] = Field(default_factory=list)


class PlannerSafeContext(BaseModel):
    last_asin: str | None = None
    last_report_id: UUID | None = None
    previous_intent: str | None = None
    pending_confirmation: PendingConfirmationPublic | None = None
    recent_user_snippets: list[str] = Field(default_factory=list)


class SynthesisSafeContext(BaseModel):
    last_asin: str | None = None
    last_report_id: UUID | None = None
    previous_intent: str | None = None
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    recent_user_snippets: list[str] = Field(default_factory=list)


class ConversationSummary(BaseModel):
    id: UUID
    status: str
    title: str | None = None
    last_asin: str | None = None
    created_at: datetime
    updated_at: datetime


class ConversationListResponse(BaseModel):
    items: list[ConversationSummary] = Field(default_factory=list)
    total: int = 0
    offset: int = 0
    limit: int = 20


class ConversationDetail(BaseModel):
    id: UUID
    organization_id: UUID
    status: str
    title: str | None = None
    last_asin: str | None = None
    last_report_id: UUID | None = None
    previous_intent: str | None = None
    messages: list[ConversationMessage] = Field(default_factory=list)
    compact_context: CompactContext
    pending_confirmation: PendingConfirmationPublic | None = None
    created_at: datetime
    updated_at: datetime

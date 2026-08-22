"""Synthesis request, untrusted draft, and seller-facing grounded response."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.copilot.evidence import EvidenceEnvelope

PROMPT_VERSION = "copilot_synthesize"

Confidence = Literal["high", "medium", "low", "none"]
SynthesisSource = Literal["synthesis_llm", "template_fallback", "rewritten_citations"]


class CopilotIgnoreExtra(BaseModel):
    model_config = ConfigDict(extra="ignore")


class AllowedFact(BaseModel):
    """Claim index sent to the synthesizer. Not a database row."""

    evidence_id: UUID
    tool_name: str
    claim_key: str
    value: Any = None
    kind: str
    source: str


class ProposedFinding(CopilotIgnoreExtra):
    text: str = ""
    claim_key: str = ""
    evidence_id: UUID | None = None


class ProposedRecommendation(CopilotIgnoreExtra):
    text: str = ""
    claim_key: str | None = None
    evidence_id: UUID | None = None


class SynthesisProposal(CopilotIgnoreExtra):
    """Untrusted LLM output. Must pass the citation validator."""

    summary: str = ""
    findings: list[ProposedFinding] = Field(default_factory=list)
    recommendations: list[ProposedRecommendation] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    confidence: str = "medium"


class EvidenceCitation(BaseModel):
    evidence_id: UUID
    claim_key: str
    tool_name: str
    label: str


class SynthesisRequest(CopilotIgnoreExtra):
    """Facts-only request. organization_id and raw history are ignored if sent."""

    user_message: str = Field(min_length=1, max_length=8000)
    intent: str = "explain_listing_score"
    evidence: list[EvidenceEnvelope] = Field(default_factory=list)
    compact_context: dict[str, Any] = Field(default_factory=dict)


class SynthesizedResponse(BaseModel):
    summary: str
    findings: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    citations: list[EvidenceCitation] = Field(default_factory=list)
    confidence: Confidence = "medium"
    unknowns: list[str] = Field(default_factory=list)
    source: SynthesisSource
    prompt_version: str | None = None
    synthesis_model: str | None = None
    message: str

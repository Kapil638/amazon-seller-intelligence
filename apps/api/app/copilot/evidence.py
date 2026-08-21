"""Evidence envelopes for intelligence tools. Presentation and Copilot must not invent claims."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.persistence.database import current_organization_id

ClaimKind = Literal[
    "observed",
    "calculated",
    "historical",
    "seller_provided",
    "ai_inference",
    "unknown",
]
ClaimConfidence = Literal["high", "medium", "low", "none"]


class EvidenceClaim(BaseModel):
    """One evidence-backed fact. `value` is JSON-serializable."""

    key: str
    value: Any = None
    kind: ClaimKind
    source: str
    confidence: ClaimConfidence = "high"
    as_of: datetime | None = None
    notes: str | None = None


class EvidenceEnvelope(BaseModel):
    """Tool result wrapper. Deterministic services remain the source of truth."""

    evidence_id: UUID = Field(default_factory=uuid4)
    tool_name: str
    organization_id: UUID
    produced_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    claims: list[EvidenceClaim] = Field(default_factory=list)

    def claim_map(self) -> dict[str, EvidenceClaim]:
        return {item.key: item for item in self.claims}

    def value(self, key: str) -> Any:
        return self.claim_map()[key].value


def claim(
    key: str,
    value: Any,
    *,
    kind: ClaimKind,
    source: str,
    confidence: ClaimConfidence = "high",
    as_of: datetime | None = None,
    notes: str | None = None,
) -> EvidenceClaim:
    return EvidenceClaim(
        key=key,
        value=value,
        kind=kind,
        source=source,
        confidence=confidence,
        as_of=as_of,
        notes=notes,
    )


def envelope(
    tool_name: str,
    claims: list[EvidenceClaim],
    *,
    organization_id: UUID | None = None,
) -> EvidenceEnvelope:
    return EvidenceEnvelope(
        tool_name=tool_name,
        organization_id=organization_id or current_organization_id(),
        claims=claims,
    )

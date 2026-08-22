"""Deterministic compact context. Never calls an LLM or dumps raw history."""

from __future__ import annotations

from app.models.copilot_conversation import (
    CompactContext,
    EvidenceRef,
    PlannerSafeContext,
    PendingConfirmationPublic,
    SynthesisSafeContext,
)
from app.persistence.models import CopilotConversation, CopilotMessage, CopilotPendingConfirmation

SNIPPET_MAX_CHARS = 500
MAX_USER_SNIPPETS = 2


def truncate_snippet(text: str, limit: int = SNIPPET_MAX_CHARS) -> str:
    stripped = text.strip()
    if len(stripped) <= limit:
        return stripped
    return stripped[:limit]


class ContextBuilder:
    """Selects slots and truncated snippets for future planner/synthesis layers."""

    def build(
        self,
        conversation: CopilotConversation,
        messages: list[CopilotMessage],
        pending: CopilotPendingConfirmation | None = None,
    ) -> CompactContext:
        user_texts = [row.content for row in messages if row.role == "user"]
        snippets = [truncate_snippet(text) for text in user_texts[-MAX_USER_SNIPPETS:]]
        pending_public = self._public_pending(pending)
        return CompactContext(
            last_asin=conversation.last_asin,
            last_report_id=conversation.last_report_id,
            previous_intent=conversation.previous_intent,
            pending_confirmation=pending_public,
            evidence_refs=self._evidence_refs(messages),
            recent_user_snippets=snippets,
        )

    def for_planner(self, compact: CompactContext) -> PlannerSafeContext:
        return PlannerSafeContext(
            last_asin=compact.last_asin,
            last_report_id=compact.last_report_id,
            previous_intent=compact.previous_intent,
            pending_confirmation=compact.pending_confirmation,
            recent_user_snippets=list(compact.recent_user_snippets),
        )

    def for_synthesis(self, compact: CompactContext) -> SynthesisSafeContext:
        return SynthesisSafeContext(
            last_asin=compact.last_asin,
            last_report_id=compact.last_report_id,
            previous_intent=compact.previous_intent,
            evidence_refs=list(compact.evidence_refs),
            recent_user_snippets=list(compact.recent_user_snippets),
        )

    def _public_pending(
        self, pending: CopilotPendingConfirmation | None
    ) -> PendingConfirmationPublic | None:
        if pending is None or pending.consumed_at is not None:
            return None
        return PendingConfirmationPublic(
            plan_id=pending.plan_id,
            nonce_present=bool(pending.nonce),
            summary=pending.summary,
        )

    def _evidence_refs(self, messages: list[CopilotMessage]) -> list[EvidenceRef]:
        for message in reversed(messages):
            payload = message.structured_payload or {}
            if payload.get("type") != "copilot_execution":
                continue
            refs: list[EvidenceRef] = []
            for item in payload.get("evidence_refs") or []:
                try:
                    refs.append(EvidenceRef.model_validate(item))
                except Exception:
                    continue
            return refs
        return []

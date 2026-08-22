from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from secrets import token_urlsafe

from app.copilot.conversation.context import ContextBuilder
from app.core.exceptions import (
    ConfirmationNonceInvalidError,
    ConversationNotFoundError,
    PersistenceNotConfiguredError,
)
from app.models.copilot_conversation import (
    CompactContext,
    ConversationDetail,
    ConversationListResponse,
    ConversationMessage,
    ConversationSummary,
    CopilotConversationCreate,
    PlannerSafeContext,
    SynthesisSafeContext,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.repositories import CopilotConversationRepository

ALLOWED_ROLES = frozenset({"user", "assistant", "system"})
SELLER_VISIBLE_ROLES = frozenset({"user", "assistant"})
TITLE_MAX_CHARS = 200
CONFIRMATION_TTL = timedelta(minutes=15)


@dataclass(frozen=True)
class PendingConfirmationRecord:
    """Internal nonce record. Compact context never includes `nonce`."""

    nonce: str
    conversation_id: UUID
    plan_id: UUID | None
    plan_schema_version: str | None
    plan_hash: str | None
    summary: str | None
    expires_at: datetime | None
    consumed_at: datetime | None


class ConversationService:
    """Persist conversations and compact context. Does not plan, execute, or synthesize."""

    def __init__(self, context_builder: ContextBuilder | None = None) -> None:
        self._context = context_builder or ContextBuilder()

    def create_conversation(self, payload: CopilotConversationCreate | None = None) -> ConversationDetail:
        self._require_persistence()
        title = _truncate_title(payload.title if payload is not None else None)
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            row = repo.create(organization_id=current_organization_id(), title=title)
            return self._detail(repo, row)

    def list_conversations(self, *, offset: int = 0, limit: int = 20) -> ConversationListResponse:
        self._require_persistence()
        limit = min(max(limit, 1), 100)
        offset = max(offset, 0)
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            rows, total = repo.list_page(current_organization_id(), offset=offset, limit=limit)
            items = [
                ConversationSummary(
                    id=row.id,
                    status=row.status,
                    title=row.title,
                    last_asin=row.last_asin,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ]
        return ConversationListResponse(items=items, total=total, offset=offset, limit=limit)

    def get_conversation(self, conversation_id: UUID) -> ConversationDetail:
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            row = repo.get(current_organization_id(), conversation_id)
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            return self._detail(repo, row)

    def add_message(
        self,
        conversation_id: UUID,
        *,
        role: str,
        content: str,
        structured_payload: dict | None = None,
    ) -> ConversationDetail:
        self._require_persistence()
        if role not in ALLOWED_ROLES:
            raise ValueError(f"Unsupported message role: {role}")
        text = content if content is not None else ""
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, conversation_id)
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            repo.add_message(
                organization_id=org_id,
                conversation=row,
                role=role,
                content=text,
                structured_payload=structured_payload,
            )
            if role == "user" and not (row.title or "").strip():
                row.title = _truncate_title(text)
            return self._detail(repo, row)

    def get_compact_context(self, conversation_id: UUID) -> CompactContext:
        return self.get_conversation(conversation_id).compact_context

    def planner_context(self, conversation_id: UUID) -> PlannerSafeContext:
        return self._context.for_planner(self.get_compact_context(conversation_id))

    def synthesis_context(self, conversation_id: UUID) -> SynthesisSafeContext:
        return self._context.for_synthesis(self.get_compact_context(conversation_id))

    def update_slots(
        self,
        conversation_id: UUID,
        *,
        last_asin: str | None = None,
        last_report_id: UUID | None = None,
        previous_intent: str | None = None,
    ) -> ConversationDetail:
        """Internal slot memory for later planner phases. Not an HTTP endpoint."""
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            row = repo.get(current_organization_id(), conversation_id)
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            if last_asin is not None:
                row.last_asin = last_asin
            if last_report_id is not None:
                row.last_report_id = last_report_id
            if previous_intent is not None:
                row.previous_intent = previous_intent
            row.updated_at = datetime.now(UTC)
            return self._detail(repo, row)

    def get_plan_payload(self, conversation_id: UUID, plan_id: UUID) -> dict[str, Any] | None:
        """Return stored validated-plan JSON. Does not execute."""
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, conversation_id)
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            return repo.get_plan_payload(org_id, conversation_id, plan_id)

    def issue_pending_confirmation(
        self,
        conversation_id: UUID,
        *,
        plan_id: UUID,
        plan_schema_version: str | None,
        plan_hash: str,
        summary: str | None,
    ) -> PendingConfirmationRecord:
        """Create a seller-owned nonce bound to plan_id + plan_hash. Internal only."""
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, conversation_id)
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            existing = repo.get_active_pending(org_id, conversation_id)
            if (
                existing is not None
                and existing.plan_id == plan_id
                and existing.plan_hash == plan_hash
                and not _pending_expired(existing.expires_at)
            ):
                return _pending_record(existing)
            repo.cancel_active_pendings(org_id, conversation_id)
            created = repo.create_pending(
                organization_id=org_id,
                conversation=row,
                nonce=token_urlsafe(32),
                plan_id=plan_id,
                plan_schema_version=plan_schema_version,
                plan_hash=plan_hash,
                summary=summary,
                expires_at=datetime.now(UTC) + CONFIRMATION_TTL,
            )
            return _pending_record(created)

    def get_pending_by_nonce(self, nonce: str) -> PendingConfirmationRecord | None:
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            pending = repo.get_pending_by_nonce(current_organization_id(), nonce)
            return _pending_record(pending) if pending is not None else None

    def consume_pending(self, nonce: str) -> PendingConfirmationRecord:
        """Mark a valid nonce consumed. Caller must have already validated it."""
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            pending = repo.get_pending_by_nonce(current_organization_id(), nonce)
            if pending is None:
                raise ConfirmationNonceInvalidError()
            repo.consume_pending(pending)
            return _pending_record(pending)

    def cancel_active_pendings(self, conversation_id: UUID) -> None:
        self._require_persistence()
        with session_scope() as session:
            repo = CopilotConversationRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, conversation_id)
            if row is None:
                raise ConversationNotFoundError(str(conversation_id))
            repo.cancel_active_pendings(org_id, conversation_id)
            if row.status == "awaiting_confirmation":
                row.status = "active"
                row.updated_at = datetime.now(UTC)

    def _detail(self, repo: CopilotConversationRepository, row) -> ConversationDetail:
        org_id = current_organization_id()
        messages = repo.list_messages(org_id, row.id)
        pending = repo.get_active_pending(org_id, row.id)
        compact = self._context.build(row, messages, pending)
        visible = [
            ConversationMessage(id=item.id, role=item.role, content=item.content, created_at=item.created_at)
            for item in messages
            if item.role in SELLER_VISIBLE_ROLES
        ]
        return ConversationDetail(
            id=row.id,
            organization_id=row.organization_id,
            status=row.status,
            title=row.title,
            last_asin=row.last_asin,
            last_report_id=row.last_report_id,
            previous_intent=row.previous_intent,
            messages=visible,
            compact_context=compact,
            pending_confirmation=compact.pending_confirmation,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Copilot conversations are not configured.")


def get_conversation_service() -> ConversationService:
    return ConversationService()


def _truncate_title(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = " ".join(value.split())
    if not stripped:
        return None
    if len(stripped) <= TITLE_MAX_CHARS:
        return stripped
    return stripped[:TITLE_MAX_CHARS]


def _pending_expired(expires_at: datetime | None) -> bool:
    if expires_at is None:
        return False
    now = datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    return expires_at <= now


def _pending_record(row) -> PendingConfirmationRecord:
    return PendingConfirmationRecord(
        nonce=row.nonce,
        conversation_id=row.conversation_id,
        plan_id=row.plan_id,
        plan_schema_version=row.plan_schema_version,
        plan_hash=row.plan_hash,
        summary=row.summary,
        expires_at=row.expires_at,
        consumed_at=row.consumed_at,
    )

"""Copilot conversation foundation.

Revision ID: 0004_copilot_conversations
Revises: 0003_report_lifecycle
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0004_copilot_conversations"
down_revision = "0003_report_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "copilot_conversations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("title", sa.String(200), nullable=True),
        sa.Column("last_asin", sa.String(10), nullable=True),
        sa.Column("last_report_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("previous_intent", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_copilot_conversations_org_updated",
        "copilot_conversations",
        ["organization_id", "updated_at"],
    )
    op.create_table(
        "copilot_messages",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("copilot_conversations.id"),
            nullable=False,
        ),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("structured_payload", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_copilot_messages_org_conversation_created",
        "copilot_messages",
        ["organization_id", "conversation_id", "created_at"],
    )
    op.create_table(
        "copilot_pending_confirmations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("copilot_conversations.id"),
            nullable=False,
        ),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("nonce", sa.String(64), nullable=False),
        sa.Column("plan_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("plan_schema_version", sa.String(64), nullable=True),
        sa.Column("plan_hash", sa.String(64), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("nonce", name="uq_copilot_pending_confirmations_nonce"),
    )
    op.create_index(
        "ix_copilot_pending_confirmations_org_conversation",
        "copilot_pending_confirmations",
        ["organization_id", "conversation_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_copilot_pending_confirmations_org_conversation",
        table_name="copilot_pending_confirmations",
    )
    op.drop_table("copilot_pending_confirmations")
    op.drop_index("ix_copilot_messages_org_conversation_created", table_name="copilot_messages")
    op.drop_table("copilot_messages")
    op.drop_index("ix_copilot_conversations_org_updated", table_name="copilot_conversations")
    op.drop_table("copilot_conversations")

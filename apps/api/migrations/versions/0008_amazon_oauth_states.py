"""Temporary hashed Amazon OAuth state. No tokens or authorization codes.

Revision ID: 0008_amazon_oauth_states
Revises: 0007_amazon_connections
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0008_amazon_oauth_states"
down_revision = "0007_amazon_connections"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_oauth_states",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column(
            "connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("amazon_state", sa.String(256), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("state_hash", name="uq_amazon_oauth_states_state_hash"),
        sa.CheckConstraint("provider IN ('SP_API')", name="ck_amazon_oauth_states_provider"),
        sa.CheckConstraint(
            "environment IN ('SANDBOX', 'PRODUCTION')",
            name="ck_amazon_oauth_states_environment",
        ),
    )
    op.create_index("ix_amazon_oauth_states_org", "amazon_oauth_states", ["organization_id"])
    op.create_index(
        "ix_amazon_oauth_states_connection_id",
        "amazon_oauth_states",
        ["connection_id"],
    )
    op.create_index("ix_amazon_oauth_states_expires_at", "amazon_oauth_states", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_amazon_oauth_states_expires_at", table_name="amazon_oauth_states")
    op.drop_index("ix_amazon_oauth_states_connection_id", table_name="amazon_oauth_states")
    op.drop_index("ix_amazon_oauth_states_org", table_name="amazon_oauth_states")
    op.drop_table("amazon_oauth_states")

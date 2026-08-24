"""Amazon connection metadata. Authorization state only. No secrets.

Revision ID: 0007_amazon_connections
Revises: 0006_advertising_models
Create Date: 2026-08-23
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0007_amazon_connections"
down_revision = "0006_advertising_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_connections",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="not_connected"),
        sa.Column("selling_partner_id", sa.String(64), nullable=True),
        sa.Column("application_id", sa.String(128), nullable=True),
        sa.Column("token_reference", sa.String(128), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_validation_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "provider",
            "environment",
            name="uq_amazon_connections_org_provider_env",
        ),
        sa.CheckConstraint(
            "status IN ("
            "'not_connected', 'pending_authorization', 'pending_validation', "
            "'connected', 'degraded', 'revoked', 'error'"
            ")",
            name="ck_amazon_connections_status",
        ),
    )
    op.create_index(
        "ix_amazon_connections_org",
        "amazon_connections",
        ["organization_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_amazon_connections_org", table_name="amazon_connections")
    op.drop_table("amazon_connections")

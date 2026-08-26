"""Canonical Amazon seller identity schema foundation. No ingest, no backfill.

Revision ID: 0009_amazon_seller_identity
Revises: 0008_amazon_oauth_states
Create Date: 2026-08-26
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0009_amazon_seller_identity"
down_revision = "0008_amazon_oauth_states"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "amazon_seller_accounts",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("selling_partner_id", sa.String(64), nullable=False),
        sa.Column("display_store_name", sa.String(256), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "selling_partner_id",
            name="uq_amazon_seller_accounts_selling_partner_id",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'identity_incomplete', 'disconnected')",
            name="ck_amazon_seller_accounts_status",
        ),
    )
    op.create_index(
        "ix_amazon_seller_accounts_org",
        "amazon_seller_accounts",
        ["organization_id"],
    )

    op.create_table(
        "amazon_marketplace_participations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "seller_account_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_seller_accounts.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("marketplace_id", sa.String(32), nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("country_code", sa.String(8), nullable=True),
        sa.Column("default_currency_code", sa.String(8), nullable=True),
        sa.Column("default_language_code", sa.String(16), nullable=True),
        sa.Column("domain_name", sa.String(128), nullable=True),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("is_participating", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("has_suspended_listings", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("store_name", sa.String(256), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "seller_account_id",
            "marketplace_id",
            name="uq_amazon_marketplace_participations_seller_marketplace",
        ),
    )
    op.create_index(
        "ix_amazon_marketplace_participations_org",
        "amazon_marketplace_participations",
        ["organization_id"],
    )
    op.create_index(
        "ix_amazon_marketplace_participations_seller_active",
        "amazon_marketplace_participations",
        ["seller_account_id", "is_active"],
    )

    op.create_table(
        "amazon_ingestion_runs",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_connections.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "seller_account_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_seller_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("domain", sa.String(64), nullable=False),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("environment", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="started"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("request_correlation_id", sa.String(64), nullable=True),
        sa.Column("records_received", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("records_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("retry_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("failure_class", sa.String(64), nullable=True),
        sa.Column("pagination_complete", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('started', 'succeeded', 'partial', 'failed', 'timed_out')",
            name="ck_amazon_ingestion_runs_status",
        ),
    )
    op.create_index(
        "ix_amazon_ingestion_runs_org",
        "amazon_ingestion_runs",
        ["organization_id"],
    )
    op.create_index(
        "ix_amazon_ingestion_runs_seller_account",
        "amazon_ingestion_runs",
        ["seller_account_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_amazon_ingestion_runs_seller_account", table_name="amazon_ingestion_runs")
    op.drop_index("ix_amazon_ingestion_runs_org", table_name="amazon_ingestion_runs")
    op.drop_table("amazon_ingestion_runs")

    op.drop_index(
        "ix_amazon_marketplace_participations_seller_active",
        table_name="amazon_marketplace_participations",
    )
    op.drop_index(
        "ix_amazon_marketplace_participations_org",
        table_name="amazon_marketplace_participations",
    )
    op.drop_table("amazon_marketplace_participations")

    op.drop_index("ix_amazon_seller_accounts_org", table_name="amazon_seller_accounts")
    op.drop_table("amazon_seller_accounts")

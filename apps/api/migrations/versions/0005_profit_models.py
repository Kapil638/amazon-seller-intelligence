"""Profit Intelligence worksheets and immutable calculation snapshots.

Revision ID: 0005_profit_models
Revises: 0004_copilot_conversations
Create Date: 2026-08-21
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0005_profit_models"
down_revision = "0004_copilot_conversations"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profit_models",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("marketplace", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("selling_price", sa.Numeric(14, 2), nullable=True),
        sa.Column("selling_price_source", sa.String(32), nullable=False, server_default="seller"),
        sa.Column("cogs", sa.Numeric(14, 2), nullable=True),
        sa.Column("shipping_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("packaging_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("other_cost", sa.Numeric(14, 2), nullable=True),
        sa.Column("referral_fee_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("fba_fee_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("fee_category_key", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "organization_id",
            "asin",
            "marketplace",
            name="uq_profit_models_org_asin_marketplace",
        ),
    )
    op.create_index(
        "ix_profit_models_org_updated",
        "profit_models",
        ["organization_id", "updated_at"],
    )
    op.create_index(
        "ix_profit_models_org_asin",
        "profit_models",
        ["organization_id", "asin"],
    )
    op.create_table(
        "profit_snapshots",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "profit_model_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("profit_models.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("profit_formula_version", sa.String(32), nullable=False, server_default="profit-calc-v1"),
        sa.Column("inputs_json", JSONB(), nullable=False),
        sa.Column("outputs_json", JSONB(), nullable=False),
        sa.Column("completeness", JSONB(), nullable=False),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_profit_snapshots_org_model_calculated",
        "profit_snapshots",
        ["organization_id", "profit_model_id", "calculated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_profit_snapshots_org_model_calculated", table_name="profit_snapshots")
    op.drop_table("profit_snapshots")
    op.drop_index("ix_profit_models_org_asin", table_name="profit_models")
    op.drop_index("ix_profit_models_org_updated", table_name="profit_models")
    op.drop_table("profit_models")

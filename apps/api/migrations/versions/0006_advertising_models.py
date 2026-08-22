"""Advertising Intelligence worksheets and immutable calculation snapshots.

Revision ID: 0006_advertising_models
Revises: 0005_profit_models
Create Date: 2026-08-22
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0006_advertising_models"
down_revision = "0005_profit_models"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "advertising_models",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("profit_model_id", PGUUID(as_uuid=True), sa.ForeignKey("profit_models.id"), nullable=False),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("marketplace", sa.String(32), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False, server_default="INR"),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("ad_spend", sa.Numeric(14, 2), nullable=True),
        sa.Column("ad_sales", sa.Numeric(14, 2), nullable=True),
        sa.Column("total_sales", sa.Numeric(14, 2), nullable=True),
        sa.Column("units_in_period", sa.Numeric(14, 2), nullable=True),
        sa.Column("source", sa.String(32), nullable=False, server_default="seller_input"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("profit_model_id", name="uq_advertising_models_profit_model_id"),
    )
    op.create_index(
        "ix_advertising_models_org_profit",
        "advertising_models",
        ["organization_id", "profit_model_id"],
    )
    op.create_table(
        "advertising_snapshots",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "advertising_model_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("advertising_models.id"),
            nullable=False,
        ),
        sa.Column("profit_model_id", PGUUID(as_uuid=True), sa.ForeignKey("profit_models.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("ads_formula_version", sa.String(32), nullable=False, server_default="ads-calc-v1"),
        sa.Column("inputs_json", JSONB(), nullable=False),
        sa.Column("outputs_json", JSONB(), nullable=False),
        sa.Column("completeness_json", JSONB(), nullable=False),
        sa.Column("impact_json", JSONB(), nullable=True),
        sa.Column("profit_snapshot_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("calculated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_advertising_snapshots_org_model_calculated",
        "advertising_snapshots",
        ["organization_id", "advertising_model_id", "calculated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_advertising_snapshots_org_model_calculated", table_name="advertising_snapshots")
    op.drop_table("advertising_snapshots")
    op.drop_index("ix_advertising_models_org_profit", table_name="advertising_models")
    op.drop_table("advertising_models")

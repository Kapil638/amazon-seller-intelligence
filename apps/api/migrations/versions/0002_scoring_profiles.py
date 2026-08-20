"""Custom scoring profiles for Listing Intelligence V2.

Revision ID: 0002_scoring_profiles
Revises: 0001_m10_persistence
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0002_scoring_profiles"
down_revision = "0001_m10_persistence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "scoring_profiles",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("title_weight", sa.Numeric(6, 2), nullable=False),
        sa.Column("bullets_weight", sa.Numeric(6, 2), nullable=False),
        sa.Column("description_a_plus_weight", sa.Numeric(6, 2), nullable=False),
        sa.Column("media_weight", sa.Numeric(6, 2), nullable=False),
        sa.Column("content_structure_weight", sa.Numeric(6, 2), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_scoring_profiles_organization_id", "scoring_profiles", ["organization_id"])
    op.add_column(
        "listing_analysis_results",
        sa.Column("custom_listing_quality_score", sa.Integer(), nullable=True),
    )
    op.add_column(
        "listing_analysis_results",
        sa.Column("scoring_profile_snapshot", JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("listing_analysis_results", "scoring_profile_snapshot")
    op.drop_column("listing_analysis_results", "custom_listing_quality_score")
    op.drop_index("ix_scoring_profiles_organization_id", table_name="scoring_profiles")
    op.drop_table("scoring_profiles")

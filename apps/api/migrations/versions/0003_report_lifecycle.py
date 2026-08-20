"""Report lifecycle and client PDF artifacts.

Revision ID: 0003_report_lifecycle
Revises: 0002_scoring_profiles
Create Date: 2026-08-20
"""

from alembic import op
import sqlalchemy as sa

revision = "0003_report_lifecycle"
down_revision = "0002_scoring_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_analysis_runs_org_deleted", "analysis_runs", ["organization_id", "deleted_at"])
    op.add_column(
        "generated_reports",
        sa.Column("template_version", sa.String(64), nullable=True),
    )
    op.create_index(
        "ix_generated_reports_run_type_template",
        "generated_reports",
        ["analysis_run_id", "report_type", "template_version"],
    )


def downgrade() -> None:
    op.drop_index("ix_generated_reports_run_type_template", table_name="generated_reports")
    op.drop_column("generated_reports", "template_version")
    op.drop_index("ix_analysis_runs_org_deleted", table_name="analysis_runs")
    op.drop_column("analysis_runs", "deleted_at")

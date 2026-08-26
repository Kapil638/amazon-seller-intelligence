"""Milestone 10 persistence schema.

Revision ID: 0001_m10_persistence
Revises:
Create Date: 2026-08-20

Reconstructed 2026-08-26 (12B.2A.1) from the models as they existed in the
commit that introduced this migration (`c0706cb`, "feat: persist history,
custom scoring, and client PDF reports"), which also introduced
`0002_scoring_profiles` in the same commit. The original `upgrade()` called
`Base.metadata.create_all(bind)` against the *live, ever-growing* ORM
metadata rather than a fixed table set — which, even in that first commit,
already included `scoring_profiles` and two columns on
`listing_analysis_results` that `0002` also creates, and `analysis_runs
.deleted_at` / `generated_reports.template_version` that `0003` also adds.
A genuine `alembic upgrade head` from an empty database has therefore never
succeeded in this project's history (see
`docs/AI_HANDOVER/19_DATABASE_DEPLOYMENT_HARDENING_ARCHITECTURE_REVIEW.md`
and `docs/AI_HANDOVER/04_DATABASE_AND_MIGRATIONS.md` for the full
investigation). This migration is rewritten to create, deterministically,
only the tables and columns this revision actually owned historically.
`0002`–`0009` are unchanged and remain the sole owners of everything they
already added.

Safe to apply to a database already stamped past `0001`: Alembic never
re-executes an already-applied revision's `upgrade()`, so this rewrite has
no effect on any database at `0001` or later (every real environment, per
the investigation above, was bootstrapped some other way and stamped, never
via a genuine run of the old `create_all()`-based `upgrade()`).
"""

from alembic import context, op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

from app.core.config import get_settings

revision = "0001_m10_persistence"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "organizations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
    )

    op.create_table(
        "product_snapshots",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("marketplace", sa.String(32), nullable=False),
        sa.Column("source", sa.String(32), nullable=True),
        sa.Column("normalized_product", JSONB(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_product_snapshots_org_asin_fetched",
        "product_snapshots",
        ["organization_id", "asin", "fetched_at"],
    )

    op.create_table(
        "report_uploads",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("original_filename", sa.String(300), nullable=False),
        sa.Column("storage_bucket", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("file_hash", sa.String(64), nullable=False),
        sa.Column("parser_version", sa.String(64), nullable=True),
        sa.Column("row_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("duplicate_of_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("analysis_payload", JSONB(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_report_uploads_org_hash", "report_uploads", ["organization_id", "file_hash"])

    op.create_table(
        "analysis_runs",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "product_snapshot_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("product_snapshots.id"),
            nullable=False,
        ),
        sa.Column("asin", sa.String(10), nullable=False),
        sa.Column("marketplace", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("listing_score_version", sa.String(32), nullable=True),
        sa.Column("ai_prompt_version", sa.String(64), nullable=True),
        sa.Column("image_prompt_version", sa.String(64), nullable=True),
        sa.Column("product_source", sa.String(32), nullable=True),
        sa.Column("display_name", sa.String(300), nullable=True),
        # Python attribute is `extra_metadata`; the DB column has always been "metadata".
        sa.Column("metadata", JSONB(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        # `deleted_at` is NOT part of this revision — it is added by
        # `0003_report_lifecycle`, which owns the soft-delete feature.
    )
    op.create_index(
        "ix_analysis_runs_org_asin_created",
        "analysis_runs",
        ["organization_id", "asin", "created_at"],
    )
    op.create_index("ix_analysis_runs_org_created", "analysis_runs", ["organization_id", "created_at"])

    op.create_table(
        "listing_analysis_results",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "analysis_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("score_version", sa.String(32), nullable=False),
        sa.Column("listing_quality_score", sa.Integer(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("analysis_run_id", name="uq_listing_analysis_results_analysis_run_id"),
        # `custom_listing_quality_score` and `scoring_profile_snapshot` are NOT
        # part of this revision — both are added by `0002_scoring_profiles`.
    )

    op.create_table(
        "ai_listing_results",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "analysis_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("analysis_run_id", name="uq_ai_listing_results_analysis_run_id"),
    )

    op.create_table(
        "image_intelligence_results",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "analysis_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("analysis_runs.id"),
            nullable=False,
        ),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("prompt_version", sa.String(64), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("images_available", sa.Integer(), nullable=False),
        sa.Column("images_selected", sa.Integer(), nullable=False),
        sa.Column("images_skipped", sa.Integer(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("analysis_run_id", name="uq_image_intelligence_results_analysis_run_id"),
    )

    op.create_table(
        "bulk_jobs",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "input_file_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("report_uploads.id"),
            nullable=True,
        ),
        sa.Column("external_job_id", sa.String(64), nullable=False),
        sa.Column("total_items", sa.Integer(), nullable=False),
        sa.Column("processed_items", sa.Integer(), nullable=False),
        sa.Column("successful_items", sa.Integer(), nullable=False),
        sa.Column("failed_items", sa.Integer(), nullable=False),
        sa.Column("settings", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("external_job_id", name="uq_bulk_jobs_external_job_id"),
    )

    op.create_table(
        "bulk_job_items",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("bulk_job_id", PGUUID(as_uuid=True), sa.ForeignKey("bulk_jobs.id"), nullable=False),
        sa.Column("asin", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column(
            "product_snapshot_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("product_snapshots.id"),
            nullable=True,
        ),
        sa.Column("listing_analysis", JSONB(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "generated_reports",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column(
            "analysis_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("analysis_runs.id"),
            nullable=True,
        ),
        sa.Column("bulk_job_id", PGUUID(as_uuid=True), sa.ForeignKey("bulk_jobs.id"), nullable=True),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("storage_bucket", sa.String(100), nullable=False),
        sa.Column("storage_path", sa.String(500), nullable=False),
        sa.Column("filename", sa.String(300), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # `template_version` is NOT part of this revision — added by
        # `0003_report_lifecycle`.
    )

    op.create_table(
        "usage_events",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), sa.ForeignKey("organizations.id"), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("workflow", sa.String(64), nullable=False),
        sa.Column("event_type", sa.String(32), nullable=False),
        sa.Column("model", sa.String(128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float(), nullable=True),
        sa.Column("cache_hit", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_usage_events_org_created", "usage_events", ["organization_id", "created_at"])

    # Baseline default-organization bootstrap, preserved from the original
    # migration. Uses a lightweight table proxy and a plain existence check
    # instead of importing the live ORM `Organization` model / `Session`, so
    # this revision does not depend on live application metadata to run, and
    # remains portable (no dialect-specific upsert syntax). Skipped in
    # offline (`alembic upgrade --sql`) mode: that mode only emits DDL/DML
    # text for later review and has no live connection to query against, so
    # a conditional existence check cannot run there. An operator reviewing
    # `--sql` output should insert the default organization manually if
    # bootstrapping a database that way.
    if not context.is_offline_mode():
        organizations = sa.table("organizations", sa.column("id"), sa.column("name"))
        settings = get_settings()
        bind = op.get_bind()
        existing = bind.execute(
            sa.select(organizations.c.id).where(organizations.c.id == settings.default_organization_id)
        ).first()
        if existing is None:
            bind.execute(
                organizations.insert().values(
                    id=settings.default_organization_id,
                    name=settings.default_organization_name,
                )
            )


def downgrade() -> None:
    op.drop_index("ix_usage_events_org_created", table_name="usage_events")
    op.drop_table("usage_events")

    op.drop_table("generated_reports")

    op.drop_table("bulk_job_items")

    op.drop_table("bulk_jobs")

    op.drop_table("image_intelligence_results")

    op.drop_table("ai_listing_results")

    op.drop_table("listing_analysis_results")

    op.drop_index("ix_analysis_runs_org_created", table_name="analysis_runs")
    op.drop_index("ix_analysis_runs_org_asin_created", table_name="analysis_runs")
    op.drop_table("analysis_runs")

    op.drop_index("ix_report_uploads_org_hash", table_name="report_uploads")
    op.drop_table("report_uploads")

    op.drop_index("ix_product_snapshots_org_asin_fetched", table_name="product_snapshots")
    op.drop_table("product_snapshots")

    op.drop_table("organizations")

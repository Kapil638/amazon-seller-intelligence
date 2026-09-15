"""Amazon Ads Sponsored Products entity-hierarchy synchronization
ledger and checkpoint tables (PR B2).

Revision ID: 0021_ads_entity_sync_runs
Revises: 0020_ads_campaign_state_enum
Create Date: 2026-09-15

Two new tables, deliberately separate from `amazon_ads_report_runs` /
`amazon_ads_sync_checkpoints` (the Reporting v3 ledger — PR #36) rather
than overloaded onto it: entity-list synchronization is a different run
model (bounded-page snapshot fetch of a GET-based, idempotent list
endpoint) from Reporting v3's async create/poll/download lifecycle, and
the columns that make sense for one make little sense for the other
(there is no Amazon-side `amazon_report_id` to protect against
duplication for a paginated GET; there is no `start_date`/`end_date`
request window for a full-snapshot entity list).

`amazon_ads_entity_sync_runs`: one row per (organization, profile,
entity_type) claim attempt. `status` vocabulary and lease columns
mirror `amazon_ads_report_runs` / `AmazonIngestionRun` exactly so the
claim/heartbeat/fenced-completion query shapes in
`app.persistence.repositories` can reuse the same proven pattern.
Deliberately simpler resumption than Reporting v3: a stale lease always
terminalizes to `timed_out` (never auto-resumed) because restarting a
paginated entity-list fetch from page 1 is always safe and idempotent —
unlike Reporting v3, there is no already-in-flight Amazon-side report
whose duplication must be avoided.

`amazon_ads_entity_sync_checkpoints`: one row per (ads_profile_id,
entity_type), recording only the timestamp of the last COMPLETE,
successful full-snapshot sync. No new column is added to the five
existing entity tables for reconciliation — `first_seen_at`/
`last_seen_at` (already present since `0019_amazon_ads_foundation`)
remain each row's own freshness signal for manual/observability
queries, and are updated by every upsert exactly as before. The
service's own per-run reconciliation COUNT (see
`app.amazon.ads_entity_sync_service.AmazonAdsEntitySyncService.
_persist_snapshot`) does not compare against them, though — it checks
row-id membership against the exact set this run's own upserts
touched, which needs no shared "before this run" clock reference
between the Python process and the database server (or between
dialects in tests vs. production) and is exact by construction.
Snapshot reconciliation never deletes or mutates a `state`; it only
counts which existing rows this run did NOT touch, recording that
count for observability on the completed run row.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0021_ads_entity_sync_runs"
down_revision = "0020_ads_campaign_state_enum"
branch_labels = None
depends_on = None

_ENTITY_TYPE_CHECK = (
    "entity_type IN ('campaign', 'ad_group', 'product_ad', 'keyword', 'product_target')"
)
_STATUS_CHECK = "status IN ('queued', 'started', 'waiting_to_retry', 'succeeded', 'failed', 'timed_out')"


def upgrade() -> None:
    op.create_table(
        "amazon_ads_entity_sync_runs",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_class", sa.String(64), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("pages_processed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_observed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_schema_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_unsupported_state", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_missing_parent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("reconciliation_stale_count", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_ENTITY_TYPE_CHECK, name="ck_amazon_ads_entity_sync_runs_entity_type"),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_amazon_ads_entity_sync_runs_status"),
    )
    op.create_index("ix_amazon_ads_entity_sync_runs_org", "amazon_ads_entity_sync_runs", ["organization_id"])
    op.create_index("ix_amazon_ads_entity_sync_runs_profile", "amazon_ads_entity_sync_runs", ["ads_profile_id"])
    op.create_index(
        "ix_amazon_ads_entity_sync_runs_claimable", "amazon_ads_entity_sync_runs", ["status", "next_retry_at"]
    )

    op.create_table(
        "amazon_ads_entity_sync_checkpoints",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_successful_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_entity_sync_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "ads_profile_id", "entity_type", name="uq_amazon_ads_entity_sync_checkpoints_profile_entity_type"
        ),
        sa.CheckConstraint(_ENTITY_TYPE_CHECK, name="ck_amazon_ads_entity_sync_checkpoints_entity_type"),
    )
    op.create_index(
        "ix_amazon_ads_entity_sync_checkpoints_org", "amazon_ads_entity_sync_checkpoints", ["organization_id"]
    )


def downgrade() -> None:
    conn = op.get_bind()

    def _count(table: str) -> int:
        return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()

    populated = {
        table: n
        for table in ("amazon_ads_entity_sync_checkpoints", "amazon_ads_entity_sync_runs")
        if (n := _count(table))
    }
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0021: the pre-B2 schema has no way to represent entity "
            "sync run/checkpoint history, and downgrading now would silently discard it. "
            f"Non-empty: {populated}. Remove or migrate this data out-of-band before downgrading."
        )

    op.drop_table("amazon_ads_entity_sync_checkpoints")
    op.drop_table("amazon_ads_entity_sync_runs")

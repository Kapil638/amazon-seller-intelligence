"""Amazon Ads Sponsored Products entity-hierarchy synchronization
ledger, checkpoint tables, and reversible snapshot-activity tracking
(PR B2).

Revision ID: 0021_ads_entity_sync_runs
Revises: 0020_ads_campaign_state_enum
Create Date: 2026-09-15

Two new tables, deliberately separate from `amazon_ads_report_runs` /
`amazon_ads_sync_checkpoints` (the Reporting v3 ledger — PR #36) rather
than overloaded onto it: entity-list synchronization is a different run
model (bounded-page snapshot fetch of a POST-based, read-only,
idempotent list endpoint — see docs/AI_HANDOVER/23's §10/§11 evidence
tables; these are POST requests carrying only filter/pagination bodies,
never a write, "GET-based" was an inaccurate shorthand corrected during
final review) from Reporting v3's async create/poll/download lifecycle.

`amazon_ads_entity_sync_runs`: one row per (organization, connection,
profile, entity_type) claim attempt. `status` vocabulary and lease
columns mirror `amazon_ads_report_runs` / `AmazonIngestionRun` exactly.
Deliberately simpler resumption than Reporting v3: a stale lease always
terminalizes to `timed_out` (never auto-resumed) because restarting a
paginated entity-list fetch from page 1 is always safe and idempotent.
`status` additionally carries a `'partial'` terminal value — final
review found the first pass of this migration allowed a run with
schema-rejected, unsupported-state, missing-parent, or mismatched-
parent items to be marked `'succeeded'` and advance the checkpoint,
which contradicts the B2 contract. `'partial'` is now a distinct
terminal outcome: persisted (if transactionally safe) but never
advances the checkpoint and never triggers reconciliation.

`amazon_ads_entity_sync_checkpoints`: one row per (ads_profile_id,
entity_type), recording the timestamp of the last COMPLETE,
clean-success (never partial) full-snapshot sync, plus the exact scope
(`organization_id`, `ads_connection_id`) that produced it — final
review found the original checkpoint identity omitted the Ads
connection, and required checkpoint advancement to be guarded by a
SQL-verified match against a run that is itself in the clean
`'succeeded'` terminal state, in the same organization/connection/
profile/entity_type scope, rather than trusting an unguarded caller
(see `app.persistence.repositories.AmazonAdsEntitySyncCheckpointRepository
.advance`, revised in the same review pass).

Reversible snapshot-activity tracking (final review, replacing the
original `reconciliation_stale_count`-only "observability, not
reconciliation" design): `is_active` and `last_seen_entity_sync_run_id`
are added to all five existing Sponsored Products entity tables
(`amazon_ads_campaigns`, `amazon_ads_ad_groups`,
`amazon_ads_advertised_products`, `amazon_ads_keywords`,
`amazon_ads_product_targets`). A row touched by a run's upserts is
marked active and stamped with that run's id; only after a complete,
rejection-free ('succeeded', never 'partial') snapshot may untouched
rows in the exact same profile/entity-table scope be marked inactive —
never hard-deleted, never touched after a disabled gate, partial
result, pagination failure, lease loss, retry, contract mismatch, or
database error. A later clean snapshot that observes a currently-
inactive row reactivates it. Amazon's own entity `state` column is
left completely untouched by this mechanism — `is_active` is this
application's own snapshot-membership signal, deliberately independent
of Amazon's reported lifecycle state.
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
_STATUS_CHECK = (
    "status IN ('queued', 'started', 'waiting_to_retry', 'succeeded', 'partial', 'failed', 'timed_out')"
)

_ENTITY_TABLES = (
    "amazon_ads_campaigns",
    "amazon_ads_ad_groups",
    "amazon_ads_advertised_products",
    "amazon_ads_keywords",
    "amazon_ads_product_targets",
)


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
            "ads_connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_connections.id", ondelete="RESTRICT"),
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
        sa.Column("items_mismatched_parent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_deactivated", sa.Integer(), nullable=True),
        sa.Column("items_reactivated", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(_ENTITY_TYPE_CHECK, name="ck_amazon_ads_entity_sync_runs_entity_type"),
        sa.CheckConstraint(_STATUS_CHECK, name="ck_amazon_ads_entity_sync_runs_status"),
    )
    op.create_index("ix_amazon_ads_entity_sync_runs_org", "amazon_ads_entity_sync_runs", ["organization_id"])
    op.create_index(
        "ix_amazon_ads_entity_sync_runs_connection", "amazon_ads_entity_sync_runs", ["ads_connection_id"]
    )
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
            "ads_connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_connections.id", ondelete="RESTRICT"),
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
    op.create_index(
        "ix_amazon_ads_entity_sync_checkpoints_connection",
        "amazon_ads_entity_sync_checkpoints",
        ["ads_connection_id"],
    )

    # Reversible snapshot-activity tracking on the five existing entity
    # tables (all created by 0019). `last_seen_entity_sync_run_id` must
    # follow `amazon_ads_entity_sync_runs`, hence added only now, after
    # that table exists.
    for table in _ENTITY_TABLES:
        op.add_column(table, sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()))
        op.add_column(
            table,
            sa.Column(
                "last_seen_entity_sync_run_id",
                PGUUID(as_uuid=True),
                sa.ForeignKey("amazon_ads_entity_sync_runs.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index(f"ix_{table}_is_active", table, ["ads_profile_id", "is_active"])


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

    for table in _ENTITY_TABLES:
        op.drop_index(f"ix_{table}_is_active", table_name=table)
        op.drop_column(table, "last_seen_entity_sync_run_id")
        op.drop_column(table, "is_active")

    op.drop_table("amazon_ads_entity_sync_checkpoints")
    op.drop_table("amazon_ads_entity_sync_runs")

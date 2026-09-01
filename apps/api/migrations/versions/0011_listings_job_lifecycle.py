"""Durable Listings synchronization job lifecycle on amazon_ingestion_runs.

Revision ID: 0011_listings_job_lifecycle
Revises: 0010_amazon_seller_listings
Create Date: 2026-08-29

12B.3G — schema only, additive and widening. No data is destroyed or
rewritten for any existing row:

- Widens `ck_amazon_ingestion_runs_status` to add `queued` and
  `waiting_to_retry` (existing values `started`/`succeeded`/`partial`/
  `failed`/`timed_out` remain valid and untouched).
- Relaxes `started_at` from NOT NULL to nullable, and drops its
  `server_default`: a `queued` row has not been claimed by any worker yet
  and so has no start time. Every existing row already has a real,
  non-null `started_at` value, so this is a pure widening — no existing
  row's data changes.
- Adds `next_retry_at` and `last_heartbeat_at` (both nullable, no
  server_default — existing rows get NULL, which is truthful: nothing
  before this migration ever tracked either concept).
- Drops and recreates `uq_amazon_ingestion_runs_active_listings_scope` so
  its partial predicate covers every nonterminal status
  (`queued`, `started`, `waiting_to_retry`) instead of only `started`.
  This is the single-writer guarantee's scope-of-protection changing, not
  a data change — no row is added, removed, or modified by this index
  change itself.
- Adds `ix_amazon_ingestion_runs_listings_claimable` to support the
  worker's claim query.

`retry_count` (already existed, unused by any listings-run code path
before this milestone) is reused as the job's attempt counter — no schema
change needed for it.

No table is created or dropped. No column is removed. No existing value
is rewritten. `downgrade()` refuses (raises) if any row is in a status the
pre-12B.3G schema cannot represent (`queued` or `waiting_to_retry`),
rather than silently corrupting or discarding it — see `downgrade()`.
"""

from alembic import op
import sqlalchemy as sa

revision = "0011_listings_job_lifecycle"
down_revision = "0010_amazon_seller_listings"
branch_labels = None
depends_on = None

_OLD_STATUS_CHECK = "status IN ('started', 'succeeded', 'partial', 'failed', 'timed_out')"
_NEW_STATUS_CHECK = (
    "status IN ("
    "'queued', 'started', 'waiting_to_retry', "
    "'succeeded', 'partial', 'failed', 'timed_out'"
    ")"
)
_OLD_ACTIVE_SCOPE_PREDICATE = "run_type = 'listings' AND status = 'started'"
_NEW_ACTIVE_SCOPE_PREDICATE = "run_type = 'listings' AND status IN ('queued', 'started', 'waiting_to_retry')"


def upgrade() -> None:
    op.drop_constraint("ck_amazon_ingestion_runs_status", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_status", "amazon_ingestion_runs", _NEW_STATUS_CHECK)

    op.alter_column(
        "amazon_ingestion_runs",
        "started_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=True,
        server_default=None,
    )

    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.drop_index("uq_amazon_ingestion_runs_active_listings_scope", table_name="amazon_ingestion_runs")
    op.create_index(
        "uq_amazon_ingestion_runs_active_listings_scope",
        "amazon_ingestion_runs",
        ["seller_account_id", "marketplace_participation_id"],
        unique=True,
        postgresql_where=sa.text(_NEW_ACTIVE_SCOPE_PREDICATE),
    )

    op.create_index(
        "ix_amazon_ingestion_runs_listings_claimable",
        "amazon_ingestion_runs",
        ["run_type", "status", "next_retry_at"],
    )


def downgrade() -> None:
    conn = op.get_bind()
    unrepresentable = conn.execute(
        sa.text(
            "SELECT count(*) FROM amazon_ingestion_runs "
            "WHERE status IN ('queued', 'waiting_to_retry')"
        )
    ).scalar_one()
    if unrepresentable:
        raise RuntimeError(
            f"Refusing to downgrade 0011: {unrepresentable} row(s) have a status "
            "('queued' or 'waiting_to_retry') the pre-12B.3G schema cannot represent. "
            "Downgrading would either violate the restored CHECK constraint or silently "
            "discard truthful ingestion-run evidence. Resolve those rows to a status "
            "valid under the old schema (e.g. let them reach a terminal state) before "
            "downgrading, or accept that this migration cannot be safely reversed while "
            "they exist."
        )

    op.drop_index("ix_amazon_ingestion_runs_listings_claimable", table_name="amazon_ingestion_runs")

    op.drop_index("uq_amazon_ingestion_runs_active_listings_scope", table_name="amazon_ingestion_runs")
    op.create_index(
        "uq_amazon_ingestion_runs_active_listings_scope",
        "amazon_ingestion_runs",
        ["seller_account_id", "marketplace_participation_id"],
        unique=True,
        postgresql_where=sa.text(_OLD_ACTIVE_SCOPE_PREDICATE),
    )

    op.drop_column("amazon_ingestion_runs", "last_heartbeat_at")
    op.drop_column("amazon_ingestion_runs", "next_retry_at")

    op.alter_column(
        "amazon_ingestion_runs",
        "started_at",
        existing_type=sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )

    op.drop_constraint("ck_amazon_ingestion_runs_status", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_status", "amazon_ingestion_runs", _OLD_STATUS_CHECK)

"""Seller listings schema foundation + ingestion-run ledger reuse. No ingest.

Revision ID: 0010_amazon_seller_listings
Revises: 0009_amazon_seller_identity
Create Date: 2026-08-27

12B.3B — schema only. Extends `amazon_ingestion_runs` so a future listings
sync can reuse the existing ledger (`run_type`, `marketplace_participation_id`,
`pages_fetched`, `reported_total_results`, `lease_owner`, `lease_expires_at`)
and adds `amazon_seller_listings`. Backfills existing rows deterministically
as `run_type='marketplace_participations'` via server_default (no separate
UPDATE required — Postgres applies a scalar server_default to existing rows
when a NOT NULL column is added this way). No SP-API client, reconciliation
service, read API, or UI code is authorized by this migration.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

revision = "0010_amazon_seller_listings"
down_revision = "0009_amazon_seller_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Extend amazon_ingestion_runs ---------------------------------
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column(
            "run_type",
            sa.String(32),
            nullable=False,
            server_default="marketplace_participations",
        ),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("marketplace_participation_id", PGUUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("pages_fetched", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("reported_total_results", sa.Integer(), nullable=True),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("lease_owner", sa.String(128), nullable=True),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    # RESTRICT, not SET NULL: a run_type='listings' row's CHECK constraint
    # (below) requires marketplace_participation_id to stay non-null for as
    # long as run_type='listings' holds, so SET NULL could never actually
    # succeed for such a row anyway — it would only surface as a confusing
    # CHECK-constraint failure instead of a direct, legible FK violation.
    op.create_foreign_key(
        "fk_amazon_ingestion_runs_marketplace_participation_id",
        "amazon_ingestion_runs",
        "amazon_marketplace_participations",
        ["marketplace_participation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_run_type",
        "amazon_ingestion_runs",
        "run_type IN ('marketplace_participations', 'listings')",
    )
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_listings_scope_required",
        "amazon_ingestion_runs",
        "run_type <> 'listings' OR "
        "(marketplace_participation_id IS NOT NULL AND seller_account_id IS NOT NULL)",
    )
    # Single-writer guarantee: at most one 'started' listings run per
    # (seller_account_id, marketplace_participation_id). This partial
    # unique index *is* the claim mechanism (a concurrent second INSERT
    # into the same scope fails on this constraint); no separate
    # claim/lease service is implemented by this migration. Any transition
    # out of 'started' (succeeded/partial/failed/timed_out) falls outside
    # the predicate, so the scope is never permanently locked.
    op.create_index(
        "uq_amazon_ingestion_runs_active_listings_scope",
        "amazon_ingestion_runs",
        ["seller_account_id", "marketplace_participation_id"],
        unique=True,
        postgresql_where=sa.text("run_type = 'listings' AND status = 'started'"),
    )
    # Widens the PK into a composite unique key so amazon_seller_listings can
    # hold a composite FK guaranteeing a listing's last ingestion run
    # belongs to the same marketplace participation as the listing itself.
    op.create_unique_constraint(
        "uq_amazon_ingestion_runs_id_marketplace_participation",
        "amazon_ingestion_runs",
        ["id", "marketplace_participation_id"],
    )

    # --- amazon_seller_listings ---------------------------------------
    op.create_table(
        "amazon_seller_listings",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seller_sku", sa.String(180), nullable=False),
        sa.Column("asin", sa.String(10), nullable=True),
        sa.Column("product_type", sa.String(64), nullable=True),
        sa.Column("condition_type", sa.String(32), nullable=True),
        sa.Column("item_name", sa.String(500), nullable=True),
        sa.Column("main_image_url", sa.String(2048), nullable=True),
        sa.Column("amazon_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amazon_last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", JSONB(), nullable=False),
        sa.Column("is_buyable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_discoverable", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("offers", JSONB(), nullable=False),
        sa.Column("price_amount", sa.Numeric(14, 2), nullable=True),
        sa.Column("price_currency", sa.String(8), nullable=True),
        sa.Column("fulfillment_availability", JSONB(), nullable=False),
        sa.Column("issues", JSONB(), nullable=False),
        sa.Column("issue_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("highest_issue_severity", sa.String(16), nullable=True),
        sa.Column("product_types", JSONB(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        # No column-level ForeignKey here: the actual constraint is the
        # composite ForeignKeyConstraint below, which pins the referenced
        # run's marketplace_participation_id to this row's own.
        sa.Column("last_ingestion_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "marketplace_participation_id",
            "seller_sku",
            name="uq_amazon_seller_listings_participation_sku",
        ),
        sa.CheckConstraint(
            "highest_issue_severity IS NULL OR highest_issue_severity IN ('ERROR', 'WARNING', 'INFO')",
            name="ck_amazon_seller_listings_highest_issue_severity",
        ),
        # Provenance integrity: a listing's last ingestion run must belong
        # to the SAME marketplace participation as the listing itself.
        # ON DELETE RESTRICT (not SET NULL): nulling only last_ingestion_run_id
        # while leaving marketplace_participation_id alone would violate this
        # composite FK's own semantics, and nulling both is impossible since
        # marketplace_participation_id is NOT NULL. Deliberately does NOT
        # also enforce that the referenced run has run_type='listings' — a
        # composite FK cannot pin the referenced side to a literal constant,
        # and a trigger would be disproportionate schema complexity for this
        # foundation slice; that check is deferred to repository/service
        # validation in 12B.3D.
        sa.ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_seller_listings_last_ingestion_run_same_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_seller_listings_participation_asin",
        "amazon_seller_listings",
        ["marketplace_participation_id", "asin"],
    )
    op.create_index(
        "ix_amazon_seller_listings_participation_active",
        "amazon_seller_listings",
        ["marketplace_participation_id", "is_active"],
    )
    op.create_index(
        "ix_amazon_seller_listings_participation_buyable",
        "amazon_seller_listings",
        ["marketplace_participation_id", "is_buyable"],
    )
    op.create_index(
        "ix_amazon_seller_listings_participation_discoverable",
        "amazon_seller_listings",
        ["marketplace_participation_id", "is_discoverable"],
    )
    op.create_index(
        "ix_amazon_seller_listings_participation_issue_count",
        "amazon_seller_listings",
        ["marketplace_participation_id", "issue_count"],
    )
    op.create_index(
        "ix_amazon_seller_listings_participation_updated",
        "amazon_seller_listings",
        ["marketplace_participation_id", "amazon_last_updated_at"],
    )
    op.create_index(
        "ix_amazon_seller_listings_last_ingestion_run",
        "amazon_seller_listings",
        ["last_ingestion_run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_amazon_seller_listings_last_ingestion_run", table_name="amazon_seller_listings")
    op.drop_index("ix_amazon_seller_listings_participation_updated", table_name="amazon_seller_listings")
    op.drop_index("ix_amazon_seller_listings_participation_issue_count", table_name="amazon_seller_listings")
    op.drop_index("ix_amazon_seller_listings_participation_discoverable", table_name="amazon_seller_listings")
    op.drop_index("ix_amazon_seller_listings_participation_buyable", table_name="amazon_seller_listings")
    op.drop_index("ix_amazon_seller_listings_participation_active", table_name="amazon_seller_listings")
    op.drop_index("ix_amazon_seller_listings_participation_asin", table_name="amazon_seller_listings")
    op.drop_table("amazon_seller_listings")

    # amazon_seller_listings (and its composite FK) is already gone above,
    # so this unique constraint's only dependent is dropped before we get here.
    op.drop_constraint(
        "uq_amazon_ingestion_runs_id_marketplace_participation", "amazon_ingestion_runs", type_="unique"
    )
    op.drop_index("uq_amazon_ingestion_runs_active_listings_scope", table_name="amazon_ingestion_runs")
    op.drop_constraint(
        "ck_amazon_ingestion_runs_listings_scope_required", "amazon_ingestion_runs", type_="check"
    )
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.drop_constraint(
        "fk_amazon_ingestion_runs_marketplace_participation_id", "amazon_ingestion_runs", type_="foreignkey"
    )
    op.drop_column("amazon_ingestion_runs", "lease_expires_at")
    op.drop_column("amazon_ingestion_runs", "lease_owner")
    op.drop_column("amazon_ingestion_runs", "reported_total_results")
    op.drop_column("amazon_ingestion_runs", "pages_fetched")
    op.drop_column("amazon_ingestion_runs", "marketplace_participation_id")
    op.drop_column("amazon_ingestion_runs", "run_type")

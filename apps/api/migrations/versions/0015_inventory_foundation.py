"""FBA Inventory schema foundation: durable inventory-run lifecycle wiring
on the shared ingestion ledger, canonical current-state inventory, and
immutable per-run inventory observations. No ingest.

Revision ID: 0015_inventory_foundation
Revises: 0014_sales_traffic_foundation
Create Date: 2026-09-07

12B.6B — schema only, additive. No SP-API FBA Inventory client, ingestion
service, read API, worker, or UI code is authorized by this migration. No
live Amazon call, no Supabase mutation, no data backfill, no seed rows.

See `docs/AI_HANDOVER/12B6B_FBA_INVENTORY_INGESTION.md` for the full
contract pin (Amazon model file + SHA-256 checksum), scope decision, and
the corrected observation-grain design this migration implements.

Extends `amazon_ingestion_runs` exactly as 12B.3D/12B.6A did:

- Widens `ck_amazon_ingestion_runs_run_type` to add `'inventory'`.
- Adds `ck_amazon_ingestion_runs_inventory_scope_required`: an
  `'inventory'` row is scoped **like Listings/Sales-Traffic**, never
  like Orders — `marketplace_participation_id` and `seller_account_id`
  both `NOT NULL` — because `getInventorySummaries` is called per
  marketplace (its `marketplaceIds` parameter has a pinned `maxItems: 1`),
  never coarser.
- Adds `uq_amazon_ingestion_runs_active_inventory_scope`: the Inventory
  equivalent of the existing Listings/Sales-Traffic single-writer partial
  unique index, scoped to `(seller_account_id,
  marketplace_participation_id)`, covering `queued`, `started`, and
  `waiting_to_retry` together.

**No new columns are added to `amazon_ingestion_runs`.** Inventory reuses
the fully generic `pages_fetched`/`reported_total_results`/
`pagination_complete`/`records_received`/`records_accepted`/
`records_rejected` columns that already exist — this ingestion is
Listings-shaped (in-memory accumulate-then-reconcile, no durable
cross-attempt pagination token), not Orders-shaped or Sales-Traffic-
report-shaped, so none of those domains' own dedicated columns apply.
`nextToken` for this operation expires 30 seconds after being created
(pinned contract) — a durable, cross-attempt pagination token would be
architecturally unsound here regardless, so none is persisted.

New tables, in dependency order:

1. `amazon_seller_inventory` — canonical current state, one row per
   `(marketplace_participation_id, seller_sku, condition)`. No
   `organization_id`/`seller_account_id` column, matching
   `amazon_seller_listings`'s own documented design — ownership is
   derived solely through `marketplace_participation_id`. `seller_sku`/
   `condition` are `NOT NULL` even though Amazon's own pinned schema
   does not require either on every response row: the application layer
   (`inventory_normalization.py`) rejects and counts any row missing
   either *before* persistence, so this NOT NULL only proves an
   invariant already enforced above the database, never rejects
   Amazon-valid data this table would otherwise accept. Every quantity
   column is nullable with a non-negative CHECK (defense-in-depth; see
   the same normalization-layer reasoning). `is_active` is the
   within-FBA absence flag — see the model's own docstring for why
   absence is never treated as zero stock. No currency column exists on
   this table (inventory is unit counts, not money).
2. `amazon_seller_inventory_observations` — immutable per-run history.
   **Corrected design** (12B.6B audit approval): natural key includes
   `ingestion_run_id`, not a calendar date — every successfully
   reconciled run records its own observations; no later successful
   run's observations are ever discarded or overwritten by an earlier
   one the same day. Retention/rollups are deliberately out of scope for
   this migration.

`downgrade()` refuses (raises) if any row exists in either new table, or
any `amazon_ingestion_runs` row has `run_type='inventory'` — `0014`'s
schema has no way to represent inventory data, and downgrading in that
state would either violate a restored constraint or silently discard
real ingestion evidence.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0015_inventory_foundation"
down_revision = "0014_sales_traffic_foundation"
branch_labels = None
depends_on = None

_OLD_RUN_TYPE_CHECK = (
    "run_type IN ('marketplace_participations', 'listings', 'orders', 'sales_and_traffic_report')"
)
_NEW_RUN_TYPE_CHECK = (
    "run_type IN ('marketplace_participations', 'listings', 'orders', 'sales_and_traffic_report', 'inventory')"
)
_INVENTORY_SCOPE_CHECK = (
    "run_type <> 'inventory' OR (marketplace_participation_id IS NOT NULL AND seller_account_id IS NOT NULL)"
)
_INVENTORY_ACTIVE_SCOPE_PREDICATE = "run_type = 'inventory' AND status IN ('queued', 'started', 'waiting_to_retry')"

# Shared verbatim between amazon_seller_inventory and
# amazon_seller_inventory_observations — see models.py's
# `_INVENTORY_QUANTITY_COLUMNS`, which this list must stay in sync with.
_QUANTITY_COLUMNS = (
    "total_quantity",
    "fulfillable_quantity",
    "inbound_working_quantity",
    "inbound_shipped_quantity",
    "inbound_receiving_quantity",
    "reserved_total_quantity",
    "reserved_pending_customer_order_quantity",
    "reserved_pending_transshipment_quantity",
    "reserved_fc_processing_quantity",
    "unfulfillable_total_quantity",
    "unfulfillable_customer_damaged_quantity",
    "unfulfillable_warehouse_damaged_quantity",
    "unfulfillable_distributor_damaged_quantity",
    "unfulfillable_carrier_damaged_quantity",
    "unfulfillable_defective_quantity",
    "unfulfillable_expired_quantity",
    "researching_total_quantity",
    "researching_quantity_short_term",
    "researching_quantity_mid_term",
    "researching_quantity_long_term",
)


def _quantity_sa_columns() -> list[sa.Column]:
    return [sa.Column(name, sa.Integer(), nullable=True) for name in _QUANTITY_COLUMNS]


def _non_negative_quantities_check(*, constraint_name: str) -> sa.CheckConstraint:
    """One combined CHECK covering every quantity column — see
    `models.py`'s identical helper for why a per-column constraint name
    is unworkable under PostgreSQL's 63-character identifier limit."""
    condition = " AND ".join(f"({name} IS NULL OR {name} >= 0)" for name in _QUANTITY_COLUMNS)
    return sa.CheckConstraint(condition, name=constraint_name)


def upgrade() -> None:
    # --- Extend amazon_ingestion_runs ---------------------------------
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", _NEW_RUN_TYPE_CHECK)
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_inventory_scope_required", "amazon_ingestion_runs", _INVENTORY_SCOPE_CHECK
    )
    op.create_index(
        "uq_amazon_ingestion_runs_active_inventory_scope",
        "amazon_ingestion_runs",
        ["seller_account_id", "marketplace_participation_id"],
        unique=True,
        postgresql_where=sa.text(_INVENTORY_ACTIVE_SCOPE_PREDICATE),
    )

    # --- amazon_seller_inventory ---------------------------------------
    op.create_table(
        "amazon_seller_inventory",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("seller_sku", sa.String(180), nullable=False),
        sa.Column("condition", sa.String(64), nullable=False),
        sa.Column("asin", sa.String(10), nullable=True),
        sa.Column("fnsku", sa.String(16), nullable=True),
        sa.Column("product_name", sa.String(500), nullable=True),
        *_quantity_sa_columns(),
        sa.Column("amazon_last_updated_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_ingestion_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "marketplace_participation_id",
            "seller_sku",
            "condition",
            name="uq_amazon_seller_inventory_participation_sku_condition",
        ),
        _non_negative_quantities_check(constraint_name="ck_amazon_seller_inventory_quantities_nonneg"),
        sa.ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_seller_inventory_last_run_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_seller_inventory_participation_asin",
        "amazon_seller_inventory",
        ["marketplace_participation_id", "asin"],
    )
    op.create_index(
        "ix_amazon_seller_inventory_participation_fnsku",
        "amazon_seller_inventory",
        ["marketplace_participation_id", "fnsku"],
    )
    op.create_index(
        "ix_amazon_seller_inventory_participation_active",
        "amazon_seller_inventory",
        ["marketplace_participation_id", "is_active"],
    )
    op.create_index(
        "ix_amazon_seller_inventory_last_ingestion_run", "amazon_seller_inventory", ["last_ingestion_run_id"]
    )

    # --- amazon_seller_inventory_observations ---------------------------
    op.create_table(
        "amazon_seller_inventory_observations",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("ingestion_run_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("seller_sku", sa.String(180), nullable=False),
        sa.Column("condition", sa.String(64), nullable=False),
        sa.Column("asin", sa.String(10), nullable=True),
        sa.Column("fnsku", sa.String(16), nullable=True),
        sa.Column("product_name", sa.String(500), nullable=True),
        *_quantity_sa_columns(),
        sa.Column("amazon_last_updated_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "ingestion_run_id",
            "marketplace_participation_id",
            "seller_sku",
            "condition",
            name="uq_amazon_seller_inventory_observations_run_identity",
        ),
        _non_negative_quantities_check(constraint_name="ck_amazon_seller_inventory_obs_quantities_nonneg"),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_seller_inventory_obs_run_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_seller_inventory_observations_participation_sku",
        "amazon_seller_inventory_observations",
        ["marketplace_participation_id", "seller_sku", "condition"],
    )
    op.create_index(
        "ix_amazon_seller_inventory_observations_run", "amazon_seller_inventory_observations", ["ingestion_run_id"]
    )
    op.create_index(
        "ix_amazon_seller_inventory_observations_observed_at",
        "amazon_seller_inventory_observations",
        ["marketplace_participation_id", "observed_at"],
    )


def downgrade() -> None:
    conn = op.get_bind()

    def _count(sql: str) -> int:
        return conn.execute(sa.text(sql)).scalar_one()

    unsafe = {
        "amazon_ingestion_runs (run_type='inventory')": _count(
            "SELECT count(*) FROM amazon_ingestion_runs WHERE run_type = 'inventory'"
        ),
        "amazon_seller_inventory": _count("SELECT count(*) FROM amazon_seller_inventory"),
        "amazon_seller_inventory_observations": _count(
            "SELECT count(*) FROM amazon_seller_inventory_observations"
        ),
    }
    populated = {name: n for name, n in unsafe.items() if n}
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0015: the pre-12B.6B schema (0014) has no way to "
            "represent FBA inventory data, and downgrading now would either violate a "
            f"restored constraint or silently discard it. Non-empty: {populated}. "
            "Remove or migrate this data out-of-band before downgrading, or accept that "
            "this migration cannot be safely reversed while it exists."
        )

    op.drop_index(
        "ix_amazon_seller_inventory_observations_observed_at", table_name="amazon_seller_inventory_observations"
    )
    op.drop_index("ix_amazon_seller_inventory_observations_run", table_name="amazon_seller_inventory_observations")
    op.drop_index(
        "ix_amazon_seller_inventory_observations_participation_sku",
        table_name="amazon_seller_inventory_observations",
    )
    op.drop_table("amazon_seller_inventory_observations")

    op.drop_index("ix_amazon_seller_inventory_last_ingestion_run", table_name="amazon_seller_inventory")
    op.drop_index("ix_amazon_seller_inventory_participation_active", table_name="amazon_seller_inventory")
    op.drop_index("ix_amazon_seller_inventory_participation_fnsku", table_name="amazon_seller_inventory")
    op.drop_index("ix_amazon_seller_inventory_participation_asin", table_name="amazon_seller_inventory")
    op.drop_table("amazon_seller_inventory")

    op.drop_index("uq_amazon_ingestion_runs_active_inventory_scope", table_name="amazon_ingestion_runs")
    op.drop_constraint("ck_amazon_ingestion_runs_inventory_scope_required", "amazon_ingestion_runs", type_="check")
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", _OLD_RUN_TYPE_CHECK)

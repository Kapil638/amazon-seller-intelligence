"""Orders schema foundation: current-state orders/items, run-participation
association, and per-participation sync checkpoints. No ingest.

Revision ID: 0012_orders_foundation
Revises: 0011_listings_job_lifecycle
Create Date: 2026-09-02

12B.4B — schema only, additive. No SP-API client, ingestion service, read
API, worker, or UI code is authorized by this migration. No live Amazon
call, no Supabase mutation, no data backfill, no seed rows.

Remediated (same revision, not amended as a second migration — this has
not been applied to any real database) after a schema review found four
blocking gaps in the original draft. See
`docs/AI_HANDOVER/12B4B_ORDERS_SCHEMA.md` for the full remediation report;
summarized here:

1. **Checkpoint advancement is now success-gated at the SQL level.** There
   is no longer a permissive `advance()` path — checkpoints only move via
   `AmazonIngestionRunMarketplaceParticipationRepository.
   finalize_successful_orders_run`, which atomically marks a `started`
   run `succeeded` and advances every included participation's checkpoint
   in one transaction, gated by a SQL predicate requiring
   `run_type='orders' AND status='succeeded' AND completed_at IS NOT NULL`.
   No new DDL was needed for this — it is a repository-layer change against
   the existing schema.
2. **Environment/connection consistency is now structural, not just
   documented.** `amazon_connections` gains
   `uq_amazon_connections_id_org_region_environment`; `amazon_ingestion_
   runs` gains a composite FK
   (`fk_amazon_ingestion_runs_connection_org_region_env`) proving its own
   `region`/`environment` match its own `connection_id`'s authoritative
   values, and its orders-scope CHECK now also requires `connection_id IS
   NOT NULL`; `amazon_marketplace_participations` gains `connection_id` in
   its composite anchor constraint
   (`uq_amazon_marketplace_participations_id_org_seller_region_conn`); and
   `amazon_ingestion_run_marketplace_participations` gains a `connection_id`
   column, pinned by both its composite FKs, forcing a run and every
   participation it covers to share the *same* connection row — making a
   PRODUCTION run pairing with a SANDBOX-backed participation, or an `na`
   run pairing with an `eu`/`fe` participation, structurally impossible.
3. **The durable lifecycle is queued-then-claimed, mirroring Listings.**
   No new DDL beyond what already existed — `uq_amazon_ingestion_runs_
   active_orders_scope` already covered `queued`/`started`/
   `waiting_to_retry` together. The repository layer no longer exposes a
   method that creates a run directly `started`; `enqueue_orders_run`
   creates `queued`, `claim_orders_run` is the only transition to
   `started`.
4. **Monetary columns use `Numeric(19,4)`, not `Numeric(14,2)`.** The
   pinned contract's `Decimal` type is documented as "a decimal number
   with no loss of precision," transmitted as a string specifically to
   avoid float rounding — direct evidence against assuming two fractional
   digits always suffice. Scoped to the three new Orders amount columns
   only; no other table in this repository is touched.

Extends `amazon_ingestion_runs` (rather than creating an unrelated job
ledger) so a future Orders ingestion slice can reuse the same durable
job/lease/retry lifecycle already proven for Listings:

- Widens `ck_amazon_ingestion_runs_run_type` to add `'orders'`.
- Adds `ck_amazon_ingestion_runs_orders_scope_required`: an `'orders'` row
  must have `marketplace_participation_id IS NULL` and `seller_account_id`/
  `region`/`environment`/`connection_id` all `NOT NULL`.
- Adds `uq_amazon_ingestion_runs_active_orders_scope`: the Orders
  equivalent of the existing Listings single-writer partial unique index,
  scoped to `(seller_account_id, region, environment)`, covering `queued`,
  `started`, and `waiting_to_retry` together — the single concurrency
  control for the whole enqueue-then-claim lifecycle.
- Adds `uq_amazon_ingestion_runs_id_org_seller_region_conn` and
  `fk_amazon_ingestion_runs_connection_org_region_env` (see point 2 above).
- Adds six new counters (`orders_received/accepted/rejected`,
  `items_received/accepted/rejected`), all `NOT NULL DEFAULT 0`, additive
  and deterministic for every existing row. The pre-existing
  `records_received/accepted/rejected` were sized for Listings (one record
  == one listing); an Orders page truthfully contains two distinct
  countable entities (orders and their items), which those columns cannot
  represent without conflating the two.

Adds `uq_amazon_connections_id_org_region_environment` to
`amazon_connections` and `uq_amazon_marketplace_participations_id_org_
seller_region_conn` to `amazon_marketplace_participations` (see point 2).

New tables, in dependency order:

1. `amazon_ingestion_run_marketplace_participations` — one row per
   (Orders run, participation) actually covered by that run. Composite PK
   `(ingestion_run_id, marketplace_participation_id)` gives the exact
   no-duplicate-pair uniqueness, and is the FK target every Orders/items/
   checkpoint row's `last_ingestion_run_id` references. Both its own FKs
   pin the full `(organization, seller_account, region, connection_id)`
   tuple, so a cross-organization, cross-seller, cross-region, or
   cross-connection (hence cross-environment) association row cannot be
   inserted.
2. `amazon_seller_orders` — current-state canonical order per marketplace
   participation. No customer PII column exists (no buyer/recipient/
   payment/tax data, no gift message, no cancellation free-text reason, no
   raw payload) — see `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`
   Phase 3/7 and `tests/test_amazon_seller_orders_schema.py`. Natural key
   `(marketplace_participation_id, amazon_order_id)`. `order_total_amount`
   is `Numeric(19,4)`.
3. `amazon_seller_order_items` — one product line per order. Natural key
   `(order_id, amazon_order_item_id)`, evidenced by the pinned contract
   ("a unique identifier for this specific item within the order"), not
   invented from `seller_sku`/`asin` alone. `unit_price_amount`/
   `item_proceeds_amount` are `Numeric(19,4)`.
4. `amazon_orders_sync_checkpoints` — one row per participation, storing
   only a raw UTC high-water mark (`synced_through_at`) and provenance to
   the association table. No overlap-window policy is baked into the
   stored value; no `paginationToken`/`nextToken` column exists.

`downgrade()` refuses (raises) if any row exists in any of the four new
tables, or any `amazon_ingestion_runs` row has `run_type='orders'` —
`0011`'s schema has no way to represent Orders data, so downgrading in
that state would either violate a restored constraint or silently discard
real ingestion evidence. Orders rows are never reinterpreted as Listings
rows.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0012_orders_foundation"
down_revision = "0011_listings_job_lifecycle"
branch_labels = None
depends_on = None

_OLD_RUN_TYPE_CHECK = "run_type IN ('marketplace_participations', 'listings')"
_NEW_RUN_TYPE_CHECK = "run_type IN ('marketplace_participations', 'listings', 'orders')"
_ORDERS_SCOPE_CHECK = (
    "run_type <> 'orders' OR "
    "(marketplace_participation_id IS NULL AND seller_account_id IS NOT NULL "
    "AND region IS NOT NULL AND environment IS NOT NULL AND connection_id IS NOT NULL)"
)
_ORDERS_ACTIVE_SCOPE_PREDICATE = "run_type = 'orders' AND status IN ('queued', 'started', 'waiting_to_retry')"

_ORDERS_COUNTER_COLUMNS = (
    "orders_received",
    "orders_accepted",
    "orders_rejected",
    "items_received",
    "items_accepted",
    "items_rejected",
)


def upgrade() -> None:
    # --- Extend amazon_connections ---------------------------------------
    op.create_unique_constraint(
        "uq_amazon_connections_id_org_region_environment",
        "amazon_connections",
        ["id", "organization_id", "region", "environment"],
    )

    # --- Extend amazon_ingestion_runs ---------------------------------
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", _NEW_RUN_TYPE_CHECK)
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_orders_scope_required", "amazon_ingestion_runs", _ORDERS_SCOPE_CHECK
    )

    for column_name in _ORDERS_COUNTER_COLUMNS:
        op.add_column(
            "amazon_ingestion_runs",
            sa.Column(column_name, sa.Integer(), nullable=False, server_default="0"),
        )

    op.create_unique_constraint(
        "uq_amazon_ingestion_runs_id_org_seller_region_conn",
        "amazon_ingestion_runs",
        ["id", "organization_id", "seller_account_id", "region", "connection_id"],
    )
    # RESTRICT, not SET NULL: organization_id/region/environment are all
    # NOT NULL on this table, so SET NULL cannot be satisfied for a
    # composite FK including them. No code anywhere in this repository
    # deletes an amazon_connections row today, so this changes no
    # currently-exercised behavior.
    op.create_foreign_key(
        "fk_amazon_ingestion_runs_connection_org_region_env",
        "amazon_ingestion_runs",
        "amazon_connections",
        ["connection_id", "organization_id", "region", "environment"],
        ["id", "organization_id", "region", "environment"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_amazon_ingestion_runs_active_orders_scope",
        "amazon_ingestion_runs",
        ["seller_account_id", "region", "environment"],
        unique=True,
        postgresql_where=sa.text(_ORDERS_ACTIVE_SCOPE_PREDICATE),
    )

    # --- Extend amazon_marketplace_participations ----------------------
    op.create_unique_constraint(
        "uq_amazon_marketplace_participations_id_org_seller_region_conn",
        "amazon_marketplace_participations",
        ["id", "organization_id", "seller_account_id", "region", "connection_id"],
    )

    # --- amazon_ingestion_run_marketplace_participations ---------------
    op.create_table(
        "amazon_ingestion_run_marketplace_participations",
        sa.Column("ingestion_run_id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("marketplace_participation_id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("organization_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("seller_account_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("connection_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["ingestion_run_id", "organization_id", "seller_account_id", "region", "connection_id"],
            [
                "amazon_ingestion_runs.id",
                "amazon_ingestion_runs.organization_id",
                "amazon_ingestion_runs.seller_account_id",
                "amazon_ingestion_runs.region",
                "amazon_ingestion_runs.connection_id",
            ],
            name="fk_amazon_ingestion_run_parts_run_scope",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["marketplace_participation_id", "organization_id", "seller_account_id", "region", "connection_id"],
            [
                "amazon_marketplace_participations.id",
                "amazon_marketplace_participations.organization_id",
                "amazon_marketplace_participations.seller_account_id",
                "amazon_marketplace_participations.region",
                "amazon_marketplace_participations.connection_id",
            ],
            name="fk_amazon_ingestion_run_parts_participation_scope",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_ingestion_run_participations_participation",
        "amazon_ingestion_run_marketplace_participations",
        ["marketplace_participation_id"],
    )

    # --- amazon_seller_orders -------------------------------------------
    op.create_table(
        "amazon_seller_orders",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("amazon_order_id", sa.String(64), nullable=False),
        sa.Column("fulfillment_status", sa.String(32), nullable=True),
        sa.Column("fulfilled_by", sa.String(16), nullable=True),
        sa.Column("sales_channel_name", sa.String(32), nullable=True),
        sa.Column("sales_channel_marketplace_id", sa.String(32), nullable=True),
        sa.Column("sales_channel_marketplace_name", sa.String(128), nullable=True),
        sa.Column("items_shipped_count", sa.Integer(), nullable=True),
        sa.Column("items_unshipped_count", sa.Integer(), nullable=True),
        sa.Column("order_total_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("order_total_currency", sa.String(8), nullable=True),
        sa.Column("is_business_order", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_prime", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("was_cancelled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("amazon_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("amazon_last_updated_at", sa.DateTime(timezone=True), nullable=True),
        # No column-level ForeignKey here: the actual constraint is the
        # composite ForeignKeyConstraint below, against the association
        # table (not amazon_ingestion_runs directly — see module docstring).
        sa.Column("last_ingestion_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "marketplace_participation_id",
            "amazon_order_id",
            name="uq_amazon_seller_orders_participation_order_id",
        ),
        sa.UniqueConstraint(
            "id",
            "marketplace_participation_id",
            name="uq_amazon_seller_orders_id_marketplace_participation",
        ),
        sa.CheckConstraint(
            "fulfillment_status IS NULL OR fulfillment_status IN ("
            "'PENDING_AVAILABILITY', 'PENDING', 'UNSHIPPED', 'PARTIALLY_SHIPPED', "
            "'SHIPPED', 'CANCELLED', 'UNFULFILLABLE'"
            ")",
            name="ck_amazon_seller_orders_fulfillment_status",
        ),
        sa.CheckConstraint(
            "fulfilled_by IS NULL OR fulfilled_by IN ('MERCHANT', 'AMAZON')",
            name="ck_amazon_seller_orders_fulfilled_by",
        ),
        sa.ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            [
                "amazon_ingestion_run_marketplace_participations.ingestion_run_id",
                "amazon_ingestion_run_marketplace_participations.marketplace_participation_id",
            ],
            name="fk_amazon_seller_orders_last_run_same_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_seller_orders_participation_updated",
        "amazon_seller_orders",
        ["marketplace_participation_id", "amazon_last_updated_at"],
    )
    op.create_index(
        "ix_amazon_seller_orders_participation_status",
        "amazon_seller_orders",
        ["marketplace_participation_id", "fulfillment_status"],
    )
    op.create_index(
        "ix_amazon_seller_orders_last_ingestion_run", "amazon_seller_orders", ["last_ingestion_run_id"]
    )

    # --- amazon_seller_order_items ---------------------------------------
    op.create_table(
        "amazon_seller_order_items",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("order_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("marketplace_participation_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("amazon_order_item_id", sa.String(64), nullable=False),
        sa.Column("seller_sku", sa.String(180), nullable=False),
        sa.Column("asin", sa.String(10), nullable=True),
        sa.Column("item_name", sa.String(500), nullable=True),
        sa.Column("condition_type", sa.String(32), nullable=True),
        sa.Column("quantity_ordered", sa.Integer(), nullable=False),
        sa.Column("quantity_fulfilled", sa.Integer(), nullable=True),
        sa.Column("quantity_unfulfilled", sa.Integer(), nullable=True),
        sa.Column("unit_price_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("unit_price_currency", sa.String(8), nullable=True),
        sa.Column("item_proceeds_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("item_proceeds_currency", sa.String(8), nullable=True),
        sa.Column("last_ingestion_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "order_id",
            "amazon_order_item_id",
            name="uq_amazon_seller_order_items_order_item_id",
        ),
        sa.ForeignKeyConstraint(
            ["order_id", "marketplace_participation_id"],
            ["amazon_seller_orders.id", "amazon_seller_orders.marketplace_participation_id"],
            name="fk_amazon_seller_order_items_order_same_participation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            [
                "amazon_ingestion_run_marketplace_participations.ingestion_run_id",
                "amazon_ingestion_run_marketplace_participations.marketplace_participation_id",
            ],
            name="fk_amazon_seller_order_items_last_run_same_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_seller_order_items_participation_asin",
        "amazon_seller_order_items",
        ["marketplace_participation_id", "asin"],
    )
    op.create_index(
        "ix_amazon_seller_order_items_participation_sku",
        "amazon_seller_order_items",
        ["marketplace_participation_id", "seller_sku"],
    )
    op.create_index(
        "ix_amazon_seller_order_items_last_ingestion_run",
        "amazon_seller_order_items",
        ["last_ingestion_run_id"],
    )

    # --- amazon_orders_sync_checkpoints -----------------------------------
    op.create_table(
        "amazon_orders_sync_checkpoints",
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("organization_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("seller_account_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("synced_through_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_successful_run_id", "marketplace_participation_id"],
            [
                "amazon_ingestion_run_marketplace_participations.ingestion_run_id",
                "amazon_ingestion_run_marketplace_participations.marketplace_participation_id",
            ],
            name="fk_amazon_orders_sync_checkpoints_run_same_participation",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()

    def _count(sql: str) -> int:
        return conn.execute(sa.text(sql)).scalar_one()

    unsafe = {
        "amazon_ingestion_runs (run_type='orders')": _count(
            "SELECT count(*) FROM amazon_ingestion_runs WHERE run_type = 'orders'"
        ),
        "amazon_ingestion_run_marketplace_participations": _count(
            "SELECT count(*) FROM amazon_ingestion_run_marketplace_participations"
        ),
        "amazon_seller_orders": _count("SELECT count(*) FROM amazon_seller_orders"),
        "amazon_seller_order_items": _count("SELECT count(*) FROM amazon_seller_order_items"),
        "amazon_orders_sync_checkpoints": _count("SELECT count(*) FROM amazon_orders_sync_checkpoints"),
    }
    populated = {name: n for name, n in unsafe.items() if n}
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0012: the pre-12B.4B schema (0011) has no way to "
            "represent Orders data, and downgrading now would either violate a "
            f"restored constraint or silently discard it. Non-empty: {populated}. "
            "Orders rows are never reinterpreted as Listings rows. Remove or migrate "
            "this data out-of-band before downgrading, or accept that this migration "
            "cannot be safely reversed while it exists."
        )

    op.drop_table("amazon_orders_sync_checkpoints")

    op.drop_index("ix_amazon_seller_order_items_last_ingestion_run", table_name="amazon_seller_order_items")
    op.drop_index("ix_amazon_seller_order_items_participation_sku", table_name="amazon_seller_order_items")
    op.drop_index("ix_amazon_seller_order_items_participation_asin", table_name="amazon_seller_order_items")
    op.drop_table("amazon_seller_order_items")

    op.drop_index("ix_amazon_seller_orders_last_ingestion_run", table_name="amazon_seller_orders")
    op.drop_index("ix_amazon_seller_orders_participation_status", table_name="amazon_seller_orders")
    op.drop_index("ix_amazon_seller_orders_participation_updated", table_name="amazon_seller_orders")
    op.drop_table("amazon_seller_orders")

    op.drop_index(
        "ix_amazon_ingestion_run_participations_participation",
        table_name="amazon_ingestion_run_marketplace_participations",
    )
    op.drop_table("amazon_ingestion_run_marketplace_participations")

    op.drop_constraint(
        "uq_amazon_marketplace_participations_id_org_seller_region_conn",
        "amazon_marketplace_participations",
        type_="unique",
    )

    op.drop_index("uq_amazon_ingestion_runs_active_orders_scope", table_name="amazon_ingestion_runs")
    op.drop_constraint(
        "fk_amazon_ingestion_runs_connection_org_region_env", "amazon_ingestion_runs", type_="foreignkey"
    )
    op.drop_constraint(
        "uq_amazon_ingestion_runs_id_org_seller_region_conn", "amazon_ingestion_runs", type_="unique"
    )
    for column_name in reversed(_ORDERS_COUNTER_COLUMNS):
        op.drop_column("amazon_ingestion_runs", column_name)
    op.drop_constraint(
        "ck_amazon_ingestion_runs_orders_scope_required", "amazon_ingestion_runs", type_="check"
    )
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", _OLD_RUN_TYPE_CHECK)

    op.drop_constraint(
        "uq_amazon_connections_id_org_region_environment", "amazon_connections", type_="unique"
    )

"""Sales and Traffic Business Report schema foundation: durable report-run
lifecycle columns on the shared ingestion ledger, catalog-wide dated facts,
product-level request-window facts, and a per-participation sync
checkpoint. No ingest.

Revision ID: 0014_sales_traffic_foundation
Revises: 0013_orders_durable_pagination
Create Date: 2026-09-04

12B.6A — schema only, additive. No SP-API Reports client, ingestion
service, read API, worker, or UI code is authorized by this migration. No
live Amazon call, no Supabase mutation, no data backfill, no seed rows.

See `docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md` for the full
contract pin, grain conclusion, and privacy/metric review this migration
implements.

Extends `amazon_ingestion_runs` (rather than creating an unrelated job
ledger) exactly as 12B.4B did for Orders:

- Widens `ck_amazon_ingestion_runs_run_type` to add
  `'sales_and_traffic_report'`.
- Adds `ck_amazon_ingestion_runs_sales_traffic_scope_required`: a
  `'sales_and_traffic_report'` row is scoped **like Listings**, never
  like Orders — `marketplace_participation_id` and `seller_account_id`
  both `NOT NULL` — because the pinned Reports API contract requires
  exactly one `marketplaceId` per report request (Phase 1 of the
  handover doc); there is no multi-participation request shape for this
  report type to represent, so this reuses the single-participation
  scope shape rather than Orders' association-table indirection.
- Adds `ck_amazon_ingestion_runs_sales_traffic_fields_scope_required`: the
  seven new report-lifecycle columns below must all be `NULL` for every
  other `run_type`.
- Adds `ck_amazon_ingestion_runs_report_processing_status`,
  `..._report_date_granularity`, `..._report_asin_granularity`: enum
  CHECKs matching the pinned contract's own enums exactly (`IN_QUEUE,
  IN_PROGRESS, DONE, CANCELLED, FATAL`; `DAY, WEEK, MONTH`; `PARENT,
  CHILD, SKU`).
- Adds `uq_amazon_ingestion_runs_active_sales_traffic_scope`: the
  Sales-and-Traffic equivalent of the existing Listings single-writer
  partial unique index, scoped to `(seller_account_id,
  marketplace_participation_id)`, covering `queued`, `started`, and
  `waiting_to_retry` together.
- Adds seven new nullable columns: `report_id`, `report_document_id`,
  `report_processing_status`, `report_data_start_time`,
  `report_data_end_time`, `report_date_granularity`,
  `report_asin_granularity`. `report_id`/`report_document_id` are
  durably stored (narrowly reviewed — handover doc §2) so a worker
  restarted after `createReport` succeeded never creates a duplicate
  report against this report type's scarce rate-limit budget (three
  `createReport` calls per five minutes). The pre-signed document URL
  itself is never a column here or anywhere — it expires within 5
  minutes of being issued (pinned contract), independent of the privacy
  reasoning that also forbids storing it.

New tables, in dependency order:

1. `amazon_sales_traffic_daily_facts` — catalog-wide, dated facts
   (`salesAndTrafficByDate`). Natural key
   `(marketplace_participation_id, report_date, date_granularity)` —
   `date_granularity` is part of the key because a `WEEK`/`MONTH`
   period-start date can collide with an unrelated `DAY`'s own date
   without it. Every money column is `Numeric(19, 4)` (matches the
   precision already established for Orders amounts); every percentage
   column is `Numeric(7, 4)` with an explicit `CHECK (... BETWEEN 0 AND
   100)` **except** `unit_session_percentage`/`unit_session_percentage_
   b2b`, which the pinned contract's own schema leaves unbounded above
   (a session can yield more than one unit purchased; the contract's own
   worked example shows `300.00`) — see handover doc §3.
2. `amazon_sales_traffic_product_facts` — product-level,
   **never-dated** facts (`salesAndTrafficByAsin`) at the exact
   `(request_window_start, request_window_end)` one report request
   covered — the pinned contract's `SalesAndTrafficByAsin` definition has
   no `date` field at all (handover doc §1a's grain conclusion), so no
   date is ever invented for a row here. `child_asin`/`seller_sku` are
   `NOT NULL` with an empty-string default (never nullable) specifically
   so the natural-key `UNIQUE` constraint actually enforces uniqueness —
   SQL treats every `NULL` as distinct from every other `NULL`, which
   would silently let two idempotent-retry upserts of the same
   `PARENT`-granularity row (where Amazon's own response genuinely omits
   `childAsin`/`sku`) both insert as "different" rows. A
   `CHECK` constraint proves the granularity column and the identifier
   columns always agree.
3. `amazon_sales_traffic_sync_checkpoints` — one row per
   participation, storing only a raw calendar-date high-water mark
   (`synced_through_date`) and provenance — mirrors
   `amazon_orders_sync_checkpoints` exactly, for the product-level daily
   ingestion path only (the expensive, one-report-per-day path).

`downgrade()` refuses (raises) if any row exists in any of the three new
tables, or any `amazon_ingestion_runs` row has
`run_type='sales_and_traffic_report'` — `0013`'s schema has no way to
represent Sales and Traffic report data, and downgrading in that state
would either violate a restored constraint or silently discard real
ingestion evidence.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0014_sales_traffic_foundation"
down_revision = "0013_orders_durable_pagination"
branch_labels = None
depends_on = None

_OLD_RUN_TYPE_CHECK = "run_type IN ('marketplace_participations', 'listings', 'orders')"
_NEW_RUN_TYPE_CHECK = "run_type IN ('marketplace_participations', 'listings', 'orders', 'sales_and_traffic_report')"
_SALES_TRAFFIC_SCOPE_CHECK = (
    "run_type <> 'sales_and_traffic_report' OR "
    "(marketplace_participation_id IS NOT NULL AND seller_account_id IS NOT NULL)"
)
_SALES_TRAFFIC_FIELDS_SCOPE_CHECK = (
    "run_type = 'sales_and_traffic_report' OR "
    "(report_id IS NULL AND report_document_id IS NULL AND report_processing_status IS NULL "
    "AND report_data_start_time IS NULL AND report_data_end_time IS NULL "
    "AND report_date_granularity IS NULL AND report_asin_granularity IS NULL)"
)
_REPORT_PROCESSING_STATUS_CHECK = (
    "report_processing_status IS NULL OR report_processing_status IN "
    "('IN_QUEUE', 'IN_PROGRESS', 'DONE', 'CANCELLED', 'FATAL')"
)
_REPORT_DATE_GRANULARITY_CHECK = "report_date_granularity IS NULL OR report_date_granularity IN ('DAY', 'WEEK', 'MONTH')"
_REPORT_ASIN_GRANULARITY_CHECK = "report_asin_granularity IS NULL OR report_asin_granularity IN ('PARENT', 'CHILD', 'SKU')"
_SALES_TRAFFIC_ACTIVE_SCOPE_PREDICATE = (
    "run_type = 'sales_and_traffic_report' AND status IN ('queued', 'started', 'waiting_to_retry')"
)

_REPORT_LIFECYCLE_COLUMNS = (
    ("report_id", sa.String(64)),
    ("report_document_id", sa.String(64)),
    ("report_processing_status", sa.String(16)),
    ("report_data_start_time", sa.Date()),
    ("report_data_end_time", sa.Date()),
    ("report_date_granularity", sa.String(8)),
    ("report_asin_granularity", sa.String(8)),
)


def upgrade() -> None:
    # --- Extend amazon_ingestion_runs ---------------------------------
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", _NEW_RUN_TYPE_CHECK)
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_sales_traffic_scope_required", "amazon_ingestion_runs", _SALES_TRAFFIC_SCOPE_CHECK
    )

    for column_name, column_type in _REPORT_LIFECYCLE_COLUMNS:
        op.add_column("amazon_ingestion_runs", sa.Column(column_name, column_type, nullable=True))

    op.create_check_constraint(
        "ck_amazon_ingestion_runs_sales_traffic_fields_scope_required",
        "amazon_ingestion_runs",
        _SALES_TRAFFIC_FIELDS_SCOPE_CHECK,
    )
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_report_processing_status", "amazon_ingestion_runs", _REPORT_PROCESSING_STATUS_CHECK
    )
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_report_date_granularity", "amazon_ingestion_runs", _REPORT_DATE_GRANULARITY_CHECK
    )
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_report_asin_granularity", "amazon_ingestion_runs", _REPORT_ASIN_GRANULARITY_CHECK
    )
    op.create_index(
        "uq_amazon_ingestion_runs_active_sales_traffic_scope",
        "amazon_ingestion_runs",
        ["seller_account_id", "marketplace_participation_id"],
        unique=True,
        postgresql_where=sa.text(_SALES_TRAFFIC_ACTIVE_SCOPE_PREDICATE),
    )

    # --- amazon_sales_traffic_daily_facts -----------------------------
    op.create_table(
        "amazon_sales_traffic_daily_facts",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("report_date", sa.Date(), nullable=False),
        sa.Column("date_granularity", sa.String(8), nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("ordered_product_sales_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("ordered_product_sales_amount_b2b", sa.Numeric(19, 4), nullable=True),
        sa.Column("units_ordered", sa.Integer(), nullable=True),
        sa.Column("units_ordered_b2b", sa.Integer(), nullable=True),
        sa.Column("total_order_items", sa.Integer(), nullable=True),
        sa.Column("total_order_items_b2b", sa.Integer(), nullable=True),
        sa.Column("average_sales_per_order_item_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("average_sales_per_order_item_amount_b2b", sa.Numeric(19, 4), nullable=True),
        sa.Column("average_units_per_order_item", sa.Numeric(10, 4), nullable=True),
        sa.Column("average_units_per_order_item_b2b", sa.Numeric(10, 4), nullable=True),
        sa.Column("average_selling_price_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("average_selling_price_amount_b2b", sa.Numeric(19, 4), nullable=True),
        sa.Column("units_refunded", sa.Integer(), nullable=True),
        sa.Column("refund_rate", sa.Numeric(7, 4), nullable=True),
        sa.Column("claims_granted", sa.Integer(), nullable=True),
        sa.Column("claims_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("shipped_product_sales_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("units_shipped", sa.Integer(), nullable=True),
        sa.Column("orders_shipped", sa.Integer(), nullable=True),
        sa.Column("browser_page_views", sa.Integer(), nullable=True),
        sa.Column("browser_page_views_b2b", sa.Integer(), nullable=True),
        sa.Column("mobile_app_page_views", sa.Integer(), nullable=True),
        sa.Column("mobile_app_page_views_b2b", sa.Integer(), nullable=True),
        sa.Column("page_views", sa.Integer(), nullable=True),
        sa.Column("page_views_b2b", sa.Integer(), nullable=True),
        sa.Column("browser_sessions", sa.Integer(), nullable=True),
        sa.Column("browser_sessions_b2b", sa.Integer(), nullable=True),
        sa.Column("mobile_app_sessions", sa.Integer(), nullable=True),
        sa.Column("mobile_app_sessions_b2b", sa.Integer(), nullable=True),
        sa.Column("sessions", sa.Integer(), nullable=True),
        sa.Column("sessions_b2b", sa.Integer(), nullable=True),
        sa.Column("buy_box_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("buy_box_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("order_item_session_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("order_item_session_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("unit_session_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("unit_session_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("average_offer_count", sa.Integer(), nullable=True),
        sa.Column("average_parent_items", sa.Integer(), nullable=True),
        sa.Column("feedback_received", sa.Integer(), nullable=True),
        sa.Column("negative_feedback_received", sa.Integer(), nullable=True),
        sa.Column("received_negative_feedback_rate", sa.Numeric(7, 4), nullable=True),
        sa.Column("last_ingestion_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "marketplace_participation_id",
            "report_date",
            "date_granularity",
            name="uq_amazon_sales_traffic_daily_facts_natural_key",
        ),
        sa.CheckConstraint(
            "date_granularity IN ('DAY', 'WEEK', 'MONTH')", name="ck_amazon_sales_traffic_daily_facts_date_granularity"
        ),
        sa.CheckConstraint(
            "refund_rate IS NULL OR refund_rate BETWEEN 0 AND 100", name="ck_amazon_sales_traffic_daily_facts_refund_rate"
        ),
        sa.CheckConstraint(
            "buy_box_percentage IS NULL OR buy_box_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_buy_box_pct",
        ),
        sa.CheckConstraint(
            "buy_box_percentage_b2b IS NULL OR buy_box_percentage_b2b BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_buy_box_pct_b2b",
        ),
        sa.CheckConstraint(
            "order_item_session_percentage IS NULL OR order_item_session_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_item_session_pct",
        ),
        sa.CheckConstraint(
            "order_item_session_percentage_b2b IS NULL OR order_item_session_percentage_b2b BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_item_session_pct_b2b",
        ),
        sa.CheckConstraint(
            "received_negative_feedback_rate IS NULL OR received_negative_feedback_rate BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_neg_feedback_rate",
        ),
        sa.ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_sales_traffic_daily_facts_last_run_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_sales_traffic_daily_facts_participation_date",
        "amazon_sales_traffic_daily_facts",
        ["marketplace_participation_id", "report_date"],
    )
    op.create_index(
        "ix_amazon_sales_traffic_daily_facts_last_ingestion_run",
        "amazon_sales_traffic_daily_facts",
        ["last_ingestion_run_id"],
    )

    # --- amazon_sales_traffic_product_facts ---------------------------
    op.create_table(
        "amazon_sales_traffic_product_facts",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("request_window_start", sa.Date(), nullable=False),
        sa.Column("request_window_end", sa.Date(), nullable=False),
        sa.Column("asin_granularity", sa.String(8), nullable=False),
        sa.Column("parent_asin", sa.String(10), nullable=False),
        sa.Column("child_asin", sa.String(10), nullable=False, server_default=""),
        sa.Column("seller_sku", sa.String(180), nullable=False, server_default=""),
        sa.Column("item_name", sa.String(500), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("units_ordered", sa.Integer(), nullable=True),
        sa.Column("units_ordered_b2b", sa.Integer(), nullable=True),
        sa.Column("ordered_product_sales_amount", sa.Numeric(19, 4), nullable=True),
        sa.Column("ordered_product_sales_amount_b2b", sa.Numeric(19, 4), nullable=True),
        sa.Column("total_order_items", sa.Integer(), nullable=True),
        sa.Column("total_order_items_b2b", sa.Integer(), nullable=True),
        sa.Column("browser_sessions", sa.Integer(), nullable=True),
        sa.Column("browser_sessions_b2b", sa.Integer(), nullable=True),
        sa.Column("mobile_app_sessions", sa.Integer(), nullable=True),
        sa.Column("mobile_app_sessions_b2b", sa.Integer(), nullable=True),
        sa.Column("sessions", sa.Integer(), nullable=True),
        sa.Column("sessions_b2b", sa.Integer(), nullable=True),
        sa.Column("browser_session_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("browser_session_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("mobile_app_session_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("mobile_app_session_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("session_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("session_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("browser_page_views", sa.Integer(), nullable=True),
        sa.Column("browser_page_views_b2b", sa.Integer(), nullable=True),
        sa.Column("mobile_app_page_views", sa.Integer(), nullable=True),
        sa.Column("mobile_app_page_views_b2b", sa.Integer(), nullable=True),
        sa.Column("page_views", sa.Integer(), nullable=True),
        sa.Column("page_views_b2b", sa.Integer(), nullable=True),
        sa.Column("browser_page_views_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("browser_page_views_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("mobile_app_page_views_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("mobile_app_page_views_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("page_views_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("page_views_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("buy_box_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("buy_box_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("unit_session_percentage", sa.Numeric(7, 4), nullable=True),
        sa.Column("unit_session_percentage_b2b", sa.Numeric(7, 4), nullable=True),
        sa.Column("last_ingestion_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "marketplace_participation_id",
            "request_window_start",
            "request_window_end",
            "asin_granularity",
            "parent_asin",
            "child_asin",
            "seller_sku",
            name="uq_amazon_sales_traffic_product_facts_natural_key",
        ),
        sa.CheckConstraint(
            "asin_granularity IN ('PARENT', 'CHILD', 'SKU')",
            name="ck_amazon_sales_traffic_product_facts_asin_granularity",
        ),
        sa.CheckConstraint(
            "request_window_start <= request_window_end", name="ck_amazon_sales_traffic_product_facts_window_order"
        ),
        sa.CheckConstraint(
            "(asin_granularity = 'PARENT' AND child_asin = '' AND seller_sku = '') OR "
            "(asin_granularity = 'CHILD' AND child_asin <> '' AND seller_sku = '') OR "
            "(asin_granularity = 'SKU' AND child_asin <> '' AND seller_sku <> '')",
            name="ck_amazon_sales_traffic_product_facts_granularity_ids",
        ),
        sa.CheckConstraint(
            "browser_session_percentage IS NULL OR browser_session_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_browser_session_pct",
        ),
        sa.CheckConstraint(
            "session_percentage IS NULL OR session_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_session_pct",
        ),
        sa.CheckConstraint(
            "page_views_percentage IS NULL OR page_views_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_page_views_pct",
        ),
        sa.CheckConstraint(
            "buy_box_percentage IS NULL OR buy_box_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_buy_box_pct",
        ),
        sa.ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_sales_traffic_product_facts_last_run_participation",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_amazon_sales_traffic_product_facts_participation_window",
        "amazon_sales_traffic_product_facts",
        ["marketplace_participation_id", "request_window_start", "request_window_end"],
    )
    op.create_index(
        "ix_amazon_sales_traffic_product_facts_parent_asin",
        "amazon_sales_traffic_product_facts",
        ["marketplace_participation_id", "parent_asin"],
    )
    op.create_index(
        "ix_amazon_sales_traffic_product_facts_participation_sku",
        "amazon_sales_traffic_product_facts",
        ["marketplace_participation_id", "seller_sku"],
    )
    op.create_index(
        "ix_amazon_sales_traffic_product_facts_last_ingestion_run",
        "amazon_sales_traffic_product_facts",
        ["last_ingestion_run_id"],
    )

    # --- amazon_sales_traffic_sync_checkpoints -------------------------
    op.create_table(
        "amazon_sales_traffic_sync_checkpoints",
        sa.Column(
            "marketplace_participation_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("organization_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("seller_account_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("synced_through_date", sa.Date(), nullable=True),
        sa.Column("last_successful_run_id", PGUUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["last_successful_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_sales_traffic_sync_checkpoints_run_participation",
            ondelete="RESTRICT",
        ),
    )


def downgrade() -> None:
    conn = op.get_bind()

    def _count(sql: str) -> int:
        return conn.execute(sa.text(sql)).scalar_one()

    unsafe = {
        "amazon_ingestion_runs (run_type='sales_and_traffic_report')": _count(
            "SELECT count(*) FROM amazon_ingestion_runs WHERE run_type = 'sales_and_traffic_report'"
        ),
        "amazon_sales_traffic_daily_facts": _count("SELECT count(*) FROM amazon_sales_traffic_daily_facts"),
        "amazon_sales_traffic_product_facts": _count(
            "SELECT count(*) FROM amazon_sales_traffic_product_facts"
        ),
        "amazon_sales_traffic_sync_checkpoints": _count(
            "SELECT count(*) FROM amazon_sales_traffic_sync_checkpoints"
        ),
    }
    populated = {name: n for name, n in unsafe.items() if n}
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0014: the pre-12B.6A schema (0013) has no way to "
            "represent Sales and Traffic report data, and downgrading now would either "
            f"violate a restored constraint or silently discard it. Non-empty: {populated}. "
            "Remove or migrate this data out-of-band before downgrading, or accept that "
            "this migration cannot be safely reversed while it exists."
        )

    op.drop_table("amazon_sales_traffic_sync_checkpoints")

    op.drop_index(
        "ix_amazon_sales_traffic_product_facts_last_ingestion_run",
        table_name="amazon_sales_traffic_product_facts",
    )
    op.drop_index(
        "ix_amazon_sales_traffic_product_facts_participation_sku", table_name="amazon_sales_traffic_product_facts"
    )
    op.drop_index(
        "ix_amazon_sales_traffic_product_facts_parent_asin",
        table_name="amazon_sales_traffic_product_facts",
    )
    op.drop_index(
        "ix_amazon_sales_traffic_product_facts_participation_window",
        table_name="amazon_sales_traffic_product_facts",
    )
    op.drop_table("amazon_sales_traffic_product_facts")

    op.drop_index(
        "ix_amazon_sales_traffic_daily_facts_last_ingestion_run", table_name="amazon_sales_traffic_daily_facts"
    )
    op.drop_index(
        "ix_amazon_sales_traffic_daily_facts_participation_date", table_name="amazon_sales_traffic_daily_facts"
    )
    op.drop_table("amazon_sales_traffic_daily_facts")

    op.drop_index("uq_amazon_ingestion_runs_active_sales_traffic_scope", table_name="amazon_ingestion_runs")
    op.drop_constraint("ck_amazon_ingestion_runs_report_asin_granularity", "amazon_ingestion_runs", type_="check")
    op.drop_constraint("ck_amazon_ingestion_runs_report_date_granularity", "amazon_ingestion_runs", type_="check")
    op.drop_constraint("ck_amazon_ingestion_runs_report_processing_status", "amazon_ingestion_runs", type_="check")
    op.drop_constraint(
        "ck_amazon_ingestion_runs_sales_traffic_fields_scope_required", "amazon_ingestion_runs", type_="check"
    )
    for column_name, _column_type in reversed(_REPORT_LIFECYCLE_COLUMNS):
        op.drop_column("amazon_ingestion_runs", column_name)
    op.drop_constraint("ck_amazon_ingestion_runs_sales_traffic_scope_required", "amazon_ingestion_runs", type_="check")
    op.drop_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", type_="check")
    op.create_check_constraint("ck_amazon_ingestion_runs_run_type", "amazon_ingestion_runs", _OLD_RUN_TYPE_CHECK)

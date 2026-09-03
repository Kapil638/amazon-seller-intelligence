"""Durable Orders pagination continuation. No ingest, no live Amazon call,
no Supabase mutation, no backfill — schema only, additive.

Revision ID: 0013_orders_durable_pagination
Revises: 0012_orders_foundation
Create Date: 2026-09-03

12B.4D remediation. The originally delivered Orders ingestion design
(restart-from-watermark) was safe and idempotent but re-walked every page
from the start of the current window after any interruption. Given the
Orders API's documented ~178.6-second sustained request interval
(`docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`'s rate-limit
section), restarting a large seller's backfill at page one after a late
interruption was judged operationally unacceptable, so this migration adds
true continuation-token-based resume.

Three new columns on `amazon_ingestion_runs`, meaningful only for
`run_type='orders'` (structurally enforced by
`ck_amazon_ingestion_runs_orders_pagination_scope_required` — see that
constraint's and the ORM model's own docstrings for the full design and
threat-model reasoning, not repeated here):

- `orders_window_last_updated_after` (nullable) — the frozen `lastUpdated
  After` search-window start for this run's entire traversal, written
  once by whichever attempt first reaches it and read back unchanged by
  every later attempt of the same run. Freezing this (rather than
  recomputing it from the checkpoint on every attempt, as 12B.4B
  originally did) is what makes reusing a still-valid pagination token
  across attempts safe — Amazon's pinned contract requires the same
  filter parameters for the life of a paginated traversal.
- `orders_window_captured_at` (nullable) — the frozen "as of" timestamp a
  fully completed sweep may safely advance a zero-order participation's
  checkpoint to. Frozen alongside the window start for the same reason.
- `orders_pagination_next_token` (nullable, `Text`) — Amazon's opaque
  `paginationToken` needed to fetch the next page. A tightly scoped
  private column, never selected by any read-service projection or
  response model, cleared to `NULL` the instant the run reaches any
  terminal status. Not routed through `SecretProvider`: it is not a
  credential (worthless without the actual refresh token, which stays in
  `SecretProvider`), and `SecretProvider`'s one-value-per-connection
  reference format does not fit a per-run cursor. `Text` rather than a
  bounded `String`: this value is opaque and never parsed, so no
  arbitrary length bound is justified.

No new column is needed to freeze the *marketplace* half of the search
window — that is already immutably fixed by this run's own membership
rows in `amazon_ingestion_run_marketplace_participations`, which nothing
ever adds to or removes from after a run is created.

`downgrade()` refuses (raises) if any row has a non-null
`orders_pagination_next_token` — an in-flight paginated traversal's resume
point has no representation in `0012`'s schema, and silently dropping it
would force that specific run back to a page-one restart the next time it
is claimed, which is exactly the operational cost this migration exists to
remove. A run with no pagination in flight (token already cleared, whether
because it finished, failed, or was never claimed) downgrades cleanly.
"""

from alembic import op
import sqlalchemy as sa

revision = "0013_orders_durable_pagination"
down_revision = "0012_orders_foundation"
branch_labels = None
depends_on = None

_ORDERS_PAGINATION_SCOPE_CHECK = (
    "run_type = 'orders' OR "
    "(orders_window_last_updated_after IS NULL AND orders_window_captured_at IS NULL "
    "AND orders_pagination_next_token IS NULL)"
)


def upgrade() -> None:
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("orders_window_last_updated_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("orders_window_captured_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "amazon_ingestion_runs",
        sa.Column("orders_pagination_next_token", sa.Text(), nullable=True),
    )
    op.create_check_constraint(
        "ck_amazon_ingestion_runs_orders_pagination_scope_required",
        "amazon_ingestion_runs",
        _ORDERS_PAGINATION_SCOPE_CHECK,
    )


def downgrade() -> None:
    conn = op.get_bind()
    in_flight = conn.execute(
        sa.text(
            "SELECT count(*) FROM amazon_ingestion_runs WHERE orders_pagination_next_token IS NOT NULL"
        )
    ).scalar_one()
    if in_flight:
        raise RuntimeError(
            "Refusing to downgrade 0013: "
            f"{in_flight} run(s) hold an in-flight orders_pagination_next_token with no "
            "representation in 0012's schema. Let those runs reach a terminal state "
            "(which clears the token) or fail them administratively before downgrading."
        )
    op.drop_constraint(
        "ck_amazon_ingestion_runs_orders_pagination_scope_required", "amazon_ingestion_runs", type_="check"
    )
    op.drop_column("amazon_ingestion_runs", "orders_pagination_next_token")
    op.drop_column("amazon_ingestion_runs", "orders_window_captured_at")
    op.drop_column("amazon_ingestion_runs", "orders_window_last_updated_after")

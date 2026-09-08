"""Worker liveness heartbeats: a database-backed availability signal for
the durable-job workers (Listings, Orders, Sales & Traffic), used by each
domain's sync-trigger service to refuse enqueueing a new job when no
matching worker process is actually running, instead of accepting the
job and leaving it queued forever.

Revision ID: 0015_worker_heartbeats
Revises: 0014_sales_traffic_foundation
Create Date: 2026-09-08

fix/ingestion-worker-runtime-availability — schema only, additive.
Branched independently from `origin/main` at `0014_sales_traffic_
foundation`, the same base the unmerged 12B.6B Inventory branch's own
`0015_inventory_foundation` migration also branches from — this is a
genuinely separate, unrelated feature and is expected to require a
trivial revision-id rebase (or an Alembic merge migration) at whichever
PR merges second; it is not a defect in either migration.

See `app/persistence/models.py`'s `AmazonWorkerHeartbeat` docstring and
`app/amazon/worker_heartbeat.py` for the full design. One row per
`worker_type`, upserted by that worker's own process on a fixed cadence,
independent of its claim/poll loop. Deliberately not a lease: this table
has no interaction whatsoever with `amazon_ingestion_runs`' own
lease/claim columns — it only ever answers "has some process of this
worker type reported itself alive recently."

`downgrade()` simply drops the table — it carries no durable business
data, only ephemeral liveness state that is meaningless once the table
is gone (a worker will simply write a fresh row on its next heartbeat
after a re-upgrade).
"""

from alembic import op
import sqlalchemy as sa

revision = "0015_worker_heartbeats"
down_revision = "0014_sales_traffic_foundation"
branch_labels = None
depends_on = None

_WORKER_TYPE_CHECK = "worker_type IN ('listings', 'orders', 'sales_and_traffic_report')"


def upgrade() -> None:
    op.create_table(
        "amazon_worker_heartbeats",
        sa.Column("worker_type", sa.String(64), primary_key=True),
        sa.Column("instance_id", sa.String(64), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(_WORKER_TYPE_CHECK, name="ck_amazon_worker_heartbeats_worker_type"),
    )


def downgrade() -> None:
    op.drop_table("amazon_worker_heartbeats")

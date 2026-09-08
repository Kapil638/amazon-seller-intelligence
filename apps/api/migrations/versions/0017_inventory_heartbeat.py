"""Widen amazon_worker_heartbeats' worker_type check constraint to allow
'inventory'.

Revision ID: 0017_inventory_heartbeat
Revises: 0016_inventory_foundation
Create Date: 2026-09-08

fix/inventory-heartbeat-check-constraint — schema only, additive (widens
an existing CHECK, drops no data, no backfill).

Root cause this closes: `0016_inventory_foundation` widened
`ck_amazon_ingestion_runs_run_type` to add `'inventory'` (see that
migration's own docstring), and `app/persistence/models.py`'s
`AmazonWorkerHeartbeat.__table_args__` was updated to declare its own
`ck_amazon_worker_heartbeats_worker_type` CHECK with `'inventory'`
already included — but the *migration* for that second constraint
(originally created by `0015_worker_heartbeats`, before Inventory
existed) was never given a matching follow-up. The ORM model and the
live schema silently diverged: `app.amazon.worker_heartbeat.
KNOWN_WORKER_TYPES` (Python) already listed `'inventory'` as a known
worker type, but the *database* rejected every heartbeat write for it —
discovered live when the Inventory worker crashed on every single
startup attempt with `psycopg.errors.CheckViolation`, exhausting its
restart ceiling before ever publishing a heartbeat. Not caught by
`test_migration_chain_matches_orm_metadata.py`, which only compares
table and column sets, not constraint definitions — see
`tests/postgres/test_disposable_postgres_worker_heartbeats.py`'s two
new tests (reject-before-0017 / accept-after-0017, and downgrade-
refuses-while-in-use), added alongside this migration, for the
targeted regression coverage this gap was missing.

`downgrade()` narrows the constraint back to the original three-value
set — this will fail loudly (not silently corrupt data) if an
`'inventory'` heartbeat row still exists at downgrade time, which is
the correct, safe behavior for a CHECK narrowing.
"""

from alembic import op

revision = "0017_inventory_heartbeat"
down_revision = "0016_inventory_foundation"
branch_labels = None
depends_on = None

_CONSTRAINT_NAME = "ck_amazon_worker_heartbeats_worker_type"
_OLD_CHECK = "worker_type IN ('listings', 'orders', 'sales_and_traffic_report')"
_NEW_CHECK = "worker_type IN ('listings', 'orders', 'sales_and_traffic_report', 'inventory')"


def upgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "amazon_worker_heartbeats", type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, "amazon_worker_heartbeats", _NEW_CHECK)


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT_NAME, "amazon_worker_heartbeats", type_="check")
    op.create_check_constraint(_CONSTRAINT_NAME, "amazon_worker_heartbeats", _OLD_CHECK)

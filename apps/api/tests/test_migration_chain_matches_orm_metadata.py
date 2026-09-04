"""12B.2A.1 — Static drift check: cumulative migration output vs live ORM metadata.

Runs the full Alembic chain in `--sql` (offline) mode, which compiles every
migration's DDL for the PostgreSQL dialect without needing a real database
connection or a real DATABASE_URL, then parses the resulting `CREATE TABLE`
statements and compares table/column names against `Base.metadata`.

This is not a substitute for the disposable-PostgreSQL upgrade test in
`tests/postgres/` — it proves the migration files compile and are internally
consistent with the ORM models; it does not prove PostgreSQL actually
accepts and enforces the DDL at runtime (constraints, defaults, FK behavior).
See `docs/AI_HANDOVER/19_DATABASE_DEPLOYMENT_HARDENING_ARCHITECTURE_REVIEW.md`
for why both checks exist and are not interchangeable.

This test is fast, dependency-free (no Docker, no network, no real database),
and runs everywhere — it exists specifically to catch the class of error this
repository has had before: a migration and the ORM models silently
describing different schemas.
"""

from __future__ import annotations

import io
import os
import re
from contextlib import redirect_stdout
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.core.config import get_settings
from app.persistence.models import Base

API_ROOT = Path(__file__).resolve().parents[1]

# A syntactically valid PostgreSQL URL that alembic never actually connects
# to in offline (`--sql`) mode: only used to select the PostgreSQL dialect
# for DDL compilation.
_OFFLINE_ONLY_POSTGRES_URL = "postgresql://offline-check:offline-check@localhost/offline_check_only"

_CREATE_TABLE_RE = re.compile(r"CREATE TABLE (\w+) \((.*?)\n\);", re.DOTALL)
_ADD_COLUMN_RE = re.compile(r"ALTER TABLE (\w+) ADD COLUMN (\w+)\b")
_SKIP_LINE_PREFIXES = ("PRIMARY KEY", "FOREIGN KEY", "UNIQUE", "CONSTRAINT", "CHECK")

# Tables that exist in the database but are intentionally not part of
# `Base.metadata` (currently none), or vice versa (also currently none).
# Kept explicit so a future, real difference must be a conscious edit here,
# not a silently-passing test.
_TABLES_ONLY_IN_DATABASE: frozenset[str] = frozenset({"alembic_version"})
_TABLES_ONLY_IN_ORM: frozenset[str] = frozenset()


def _generate_offline_sql() -> str:
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = _OFFLINE_ONLY_POSTGRES_URL
    get_settings.cache_clear()
    try:
        cfg = Config(str(API_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
        cfg.set_main_option("sqlalchemy.url", _OFFLINE_ONLY_POSTGRES_URL)
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            command.upgrade(cfg, "head", sql=True)
        return buffer.getvalue()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()


def _parse_tables(sql_text: str) -> dict[str, set[str]]:
    tables: dict[str, set[str]] = {}
    for table_name, body in _CREATE_TABLE_RE.findall(sql_text):
        columns: set[str] = set()
        for raw_line in body.split("\n"):
            line = raw_line.strip().rstrip(",")
            if not line or line.startswith(_SKIP_LINE_PREFIXES):
                continue
            column_name = line.split()[0]
            columns.add(column_name)
        tables[table_name] = columns
    # `op.add_column` (used by later migrations to extend an earlier
    # migration's table, e.g. 0002/0003 on tables 0001 owns) emits
    # `ALTER TABLE ... ADD COLUMN ...`, not another `CREATE TABLE` — these
    # must be merged in, or every such column looks like drift.
    for table_name, column_name in _ADD_COLUMN_RE.findall(sql_text):
        tables.setdefault(table_name, set()).add(column_name)
    return tables


def test_full_migration_chain_compiles_for_postgresql_with_no_collisions() -> None:
    """The whole point of the 12B.2A.1 repair: zero-to-head must compile
    cleanly for PostgreSQL with no duplicate-table/column errors."""
    sql_text = _generate_offline_sql()
    tables = _parse_tables(sql_text)
    assert "scoring_profiles" in tables
    assert "amazon_seller_accounts" in tables
    assert "amazon_marketplace_participations" in tables
    assert "amazon_ingestion_runs" in tables
    assert "amazon_seller_listings" in tables
    assert "amazon_ingestion_run_marketplace_participations" in tables
    assert "amazon_seller_orders" in tables
    assert "amazon_seller_order_items" in tables
    assert "amazon_orders_sync_checkpoints" in tables
    assert "amazon_sales_traffic_daily_facts" in tables
    assert "amazon_sales_traffic_product_facts" in tables
    assert "amazon_sales_traffic_sync_checkpoints" in tables
    # 32 application tables (12B.4B added amazon_ingestion_run_marketplace_
    # participations, amazon_seller_orders, amazon_seller_order_items,
    # amazon_orders_sync_checkpoints; 12B.6A added amazon_sales_traffic_
    # daily_facts, amazon_sales_traffic_product_facts, amazon_sales_
    # traffic_sync_checkpoints) + alembic's own bookkeeping table.
    assert len(tables) == 33, sorted(tables)


def test_migration_chain_table_set_matches_orm_metadata() -> None:
    sql_text = _generate_offline_sql()
    migrated_tables = _parse_tables(sql_text)

    migrated_names = set(migrated_tables) - _TABLES_ONLY_IN_DATABASE
    orm_names = set(Base.metadata.tables) - _TABLES_ONLY_IN_ORM

    missing_from_migrations = orm_names - migrated_names
    extra_in_migrations = migrated_names - orm_names
    assert not missing_from_migrations, (
        f"ORM model(s) with no corresponding migration: {sorted(missing_from_migrations)}"
    )
    assert not extra_in_migrations, (
        f"Migration-created table(s) with no corresponding ORM model: {sorted(extra_in_migrations)}"
    )


def test_migration_chain_columns_match_orm_metadata_per_table() -> None:
    sql_text = _generate_offline_sql()
    migrated_tables = _parse_tables(sql_text)

    mismatches: dict[str, dict[str, set[str]]] = {}
    for table_name, orm_table in Base.metadata.tables.items():
        if table_name in _TABLES_ONLY_IN_ORM or table_name not in migrated_tables:
            continue
        orm_columns = set(orm_table.columns.keys())
        migrated_columns = migrated_tables[table_name]
        if orm_columns != migrated_columns:
            mismatches[table_name] = {
                "missing_from_migrations": orm_columns - migrated_columns,
                "extra_in_migrations": migrated_columns - orm_columns,
            }
    assert not mismatches, mismatches

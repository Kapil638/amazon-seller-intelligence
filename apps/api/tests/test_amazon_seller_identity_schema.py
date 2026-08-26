"""12B.2A — Alembic migration 0009: canonical Amazon seller identity schema.

This file contains two different, non-interchangeable kinds of coverage —
read both docstrings below before touching either:

1. `test_migration_0009_upgrades_and_downgrades_in_isolation_from_0008` —
   proves 0009's own upgrade()/downgrade() are correct, starting from a
   database already stamped at 0008. This is real coverage of 0009, but it
   is NOT a test that the full migration chain applies cleanly from zero.

2. `test_full_migration_chain_from_empty_database_currently_fails_at_0002` —
   a diagnostic that pins a genuine, PRE-EXISTING defect (not introduced by
   0009): `alembic upgrade head` from a truly empty database fails partway
   through, at 0002, because `migrations/versions/0001_m10_persistence.py`
   calls `Base.metadata.create_all(bind)`, which creates every table
   *currently* defined on the live `Base` metadata object — including tables
   added by migrations written years after 0001 (and this file's own new
   12B.2A tables). `0002_scoring_profiles.py` then tries to
   `op.create_table` a table 0001 already created and fails with
   "table already exists". This has apparently never been exercised in
   practice: real databases were bootstrapped some other way (see
   `docs/AI_HANDOVER/04_DATABASE_AND_MIGRATIONS.md` for the proposed
   remediation), then migrated incrementally one revision at a time.

   This repository has no established convention for tracking known-failing
   tests (no `xfail`/`skip` usage anywhere in the suite), so this is
   deliberately NOT marked `xfail` — introducing that convention here would
   be an unrelated process change. Instead it is a normal, passing
   regression test that asserts the CURRENT failure mode precisely (via
   `pytest.raises`), so it pins the bug rather than hiding it. When `0001`
   is eventually remediated, this test must be rewritten to assert success,
   not deleted.

Neither test ever touches the configured `DATABASE_URL`: it is overridden
for the duration of each test and restored in a `finally` block.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import OperationalError

from app.core.config import get_settings
from app.persistence.models import Base

API_ROOT = Path(__file__).resolve().parents[1]
_NEW_TABLES = {
    "amazon_seller_accounts",
    "amazon_marketplace_participations",
    "amazon_ingestion_runs",
}


def _alembic_config(sqlite_url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", sqlite_url)
    return cfg


def test_alembic_has_a_single_head() -> None:
    cfg = _alembic_config("sqlite://")
    script = ScriptDirectory.from_config(cfg)
    heads = script.get_heads()
    assert len(heads) == 1
    assert heads[0] == "0009_amazon_seller_identity"
    revision = script.get_revision("0009_amazon_seller_identity")
    assert revision is not None
    assert revision.down_revision == "0008_amazon_oauth_states"


def test_migration_0009_upgrades_and_downgrades_in_isolation_from_0008(tmp_path) -> None:
    """Proves 0009 itself is correct. Does NOT prove the full chain applies from
    zero — see `test_full_migration_chain_from_empty_database_currently_fails_at_0002`
    for that, and the module docstring for why the two are not interchangeable.
    """
    db_path = tmp_path / "migration_0009.sqlite3"
    sqlite_url = f"sqlite:///{db_path}"
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = sqlite_url
    get_settings.cache_clear()
    try:
        cfg = _alembic_config(sqlite_url)
        bootstrap_engine = create_engine(sqlite_url)
        try:
            pre_0009_tables = [
                table for name, table in Base.metadata.tables.items() if name not in _NEW_TABLES
            ]
            Base.metadata.create_all(bootstrap_engine, tables=pre_0009_tables)
        finally:
            bootstrap_engine.dispose()
        command.stamp(cfg, "0008_amazon_oauth_states")

        command.upgrade(cfg, "0009_amazon_seller_identity")

        engine = create_engine(sqlite_url)
        try:
            inspector = inspect(engine)
            tables = set(inspector.get_table_names())
            assert "amazon_seller_accounts" in tables
            assert "amazon_marketplace_participations" in tables
            assert "amazon_ingestion_runs" in tables

            seller_account_uniques = {
                uq["name"] for uq in inspector.get_unique_constraints("amazon_seller_accounts")
            }
            assert "uq_amazon_seller_accounts_selling_partner_id" in seller_account_uniques
            seller_account_indexes = {
                ix["name"] for ix in inspector.get_indexes("amazon_seller_accounts")
            }
            assert "ix_amazon_seller_accounts_org" in seller_account_indexes
            seller_account_columns = {
                col["name"] for col in inspector.get_columns("amazon_seller_accounts")
            }
            forbidden = {"refresh_token", "access_token", "client_secret", "token_reference"}
            assert not forbidden.intersection(seller_account_columns)

            participation_uniques = {
                uq["name"]
                for uq in inspector.get_unique_constraints("amazon_marketplace_participations")
            }
            assert (
                "uq_amazon_marketplace_participations_seller_marketplace" in participation_uniques
            )
            participation_columns = {
                col["name"] for col in inspector.get_columns("amazon_marketplace_participations")
            }
            assert not forbidden.intersection(participation_columns)

            run_columns = {col["name"] for col in inspector.get_columns("amazon_ingestion_runs")}
            assert {
                "organization_id",
                "seller_account_id",
                "status",
                "records_received",
                "records_accepted",
                "records_rejected",
                "retry_count",
                "failure_class",
            }.issubset(run_columns)
            assert not forbidden.intersection(run_columns)
        finally:
            engine.dispose()

        command.downgrade(cfg, "0008_amazon_oauth_states")
        engine = create_engine(sqlite_url)
        try:
            inspector = inspect(engine)
            tables_after_downgrade = set(inspector.get_table_names())
            assert "amazon_seller_accounts" not in tables_after_downgrade
            assert "amazon_marketplace_participations" not in tables_after_downgrade
            assert "amazon_ingestion_runs" not in tables_after_downgrade
            # Downgrade must not touch unrelated tables.
            assert "amazon_connections" in tables_after_downgrade
            assert "amazon_oauth_states" in tables_after_downgrade
        finally:
            engine.dispose()
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()


def test_full_migration_chain_from_empty_database_currently_fails_at_0002(tmp_path) -> None:
    """Diagnostic, not a 0009 defect: `alembic upgrade head` from a genuinely
    empty database currently fails at 0002 for a pre-existing reason unrelated
    to this migration. See the module docstring for the full explanation and
    `docs/AI_HANDOVER/04_DATABASE_AND_MIGRATIONS.md` for the proposed fix.

    This test intentionally asserts the CURRENT failing behavior so a future
    fix to `0001_m10_persistence.py` is forced to touch this test (and can
    then flip it to assert success) rather than the regression going unnoticed.
    """
    db_path = tmp_path / "migration_from_zero.sqlite3"
    sqlite_url = f"sqlite:///{db_path}"
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = sqlite_url
    get_settings.cache_clear()
    try:
        cfg = _alembic_config(sqlite_url)
        with pytest.raises(OperationalError) as excinfo:
            command.upgrade(cfg, "head")
        message = str(excinfo.value)
        assert "scoring_profiles" in message
        assert "already exists" in message
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()

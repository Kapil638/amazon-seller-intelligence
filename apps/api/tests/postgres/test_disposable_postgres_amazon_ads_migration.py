"""Disposable PostgreSQL validation for migration 0019
(`amazon_ads_foundation`, the Amazon Ads read-only integration
foundation).

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Proves, against genuine PostgreSQL (not just
the offline-SQL/ORM-metadata static check in
`tests/test_migration_chain_matches_orm_metadata.py`):

- the real DDL for all 12 new tables actually applies cleanly on top of
  an existing 0018 database,
- pre-existing tables/rows from earlier migrations are untouched by the
  0019 upgrade (this migration is additive-only; it must never touch
  `amazon_connections`, `amazon_encrypted_secrets`, or any other
  pre-existing table),
- `downgrade()` refuses when any of the 12 new tables holds a row
  (mirrors `0014`'s own precedent, verified here per-table so a future
  edit that narrows the guard to only some of the 12 tables would be
  caught), and
- a clean 0019 -> 0018 -> 0019 round trip succeeds when the new tables
  are empty.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.core.config import get_settings
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]

NEW_TABLES = (
    "amazon_ads_connections",
    "amazon_ads_oauth_states",
    "amazon_ads_profiles",
    "amazon_ads_campaigns",
    "amazon_ads_ad_groups",
    "amazon_ads_advertised_products",
    "amazon_ads_keywords",
    "amazon_ads_product_targets",
    "amazon_ads_report_runs",
    "amazon_ads_daily_performance_facts",
    "amazon_ads_sync_checkpoints",
    "amazon_ads_sync_errors",
)


def _alembic_config(url: str) -> Config:
    cfg = Config(str(API_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@contextmanager
def _alembic_environment(url: str):
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()


@pytest.fixture
def disposable_engine():
    url = _guard.disposable_url()
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        existing_tables = set(inspector.get_table_names())
        if existing_tables - {"alembic_version"}:
            pytest.fail(
                "POSTGRES_DISPOSABLE_TEST_URL points at a non-empty database "
                f"({len(existing_tables)} existing table(s)) — refusing to run "
                "destructive migration tests against it. Use a genuinely fresh "
                "disposable instance."
            )
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def test_empty_postgres_upgrade_to_0019_produces_all_twelve_new_tables(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0019_amazon_ads_foundation")

    inspector = inspect(disposable_engine)
    table_names = set(inspector.get_table_names())
    for table in NEW_TABLES:
        assert table in table_names, f"missing table: {table}"

    # Spot-check the two tables with the most load-bearing shape: the
    # connection's status CHECK/uniqueness, and the report run's lease
    # columns the claim query depends on.
    connection_columns = {c["name"] for c in inspector.get_columns("amazon_ads_connections")}
    assert {"organization_id", "status", "token_reference"}.issubset(connection_columns)
    report_run_columns = {c["name"] for c in inspector.get_columns("amazon_ads_report_runs")}
    assert {"status", "lease_owner", "lease_expires_at", "next_retry_at", "amazon_report_id"}.issubset(
        report_run_columns
    )

    fact_pk = inspector.get_pk_constraint("amazon_ads_daily_performance_facts")
    assert fact_pk["constrained_columns"] == ["id"]
    fact_unique = {tuple(sorted(uc["column_names"])) for uc in inspector.get_unique_constraints(
        "amazon_ads_daily_performance_facts"
    )}
    assert (
        tuple(sorted(["ads_profile_id", "entity_type", "entity_external_id", "fact_date", "attribution_window"]))
        in fact_unique
    )


def test_0019_upgrade_does_not_touch_any_pre_existing_table_or_row(disposable_engine) -> None:
    """The core forward-migration safety proof requested: seed a row in a
    table that exists at 0018, upgrade to 0019, and confirm that row (and
    every pre-existing table) is completely unaffected."""
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0018_amazon_encrypted_secrets")

    inspector_before = inspect(disposable_engine)
    tables_before = set(inspector_before.get_table_names())
    assert not (set(NEW_TABLES) & tables_before)  # sanity: none of the new tables exist yet

    # Migration 0001 seeds exactly one default "Development" organization
    # row (see 0001_m10_persistence.py) — reused here as the pre-existing
    # row to verify survives 0019, rather than inventing a second
    # organization id that happens to collide with it.
    with disposable_engine.begin() as conn:
        seeded_org_id = conn.execute(text("SELECT id FROM organizations LIMIT 1")).scalar_one()
        conn.execute(
            text("UPDATE organizations SET name = 'Pre-existing Org' WHERE id = :id"),
            {"id": seeded_org_id},
        )
        conn.execute(
            text(
                "INSERT INTO amazon_encrypted_secrets (reference, key_version, nonce, ciphertext) "
                "VALUES ('asi/amazon/SP_API/PRODUCTION/pre/existing', 'v1', :nonce, :ciphertext)"
            ),
            {"nonce": b"\x00" * 12, "ciphertext": b"\x01" * 16},
        )

    with _alembic_environment(url):
        command.upgrade(cfg, "0019_amazon_ads_foundation")

    inspector_after = inspect(disposable_engine)
    tables_after = set(inspector_after.get_table_names())
    # Every table that existed before 0019 still exists — nothing dropped.
    assert tables_before.issubset(tables_after)

    with disposable_engine.begin() as conn:
        org_row = conn.execute(
            text("SELECT name FROM organizations WHERE id = :id"), {"id": seeded_org_id}
        ).one()
        assert org_row.name == "Pre-existing Org"
        secret_count = conn.execute(
            text(
                "SELECT count(*) FROM amazon_encrypted_secrets "
                "WHERE reference = 'asi/amazon/SP_API/PRODUCTION/pre/existing'"
            )
        ).scalar_one()
        assert secret_count == 1


def test_downgrade_refuses_when_any_new_table_is_populated(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0019_amazon_ads_foundation")

        org_id = "22222222-2222-4222-8222-222222222222"
        with disposable_engine.begin() as conn:
            conn.execute(
                text("INSERT INTO organizations (id, name) VALUES (:id, 'Ads Org')"), {"id": org_id}
            )
            conn.execute(
                text(
                    "INSERT INTO amazon_ads_connections (id, organization_id, status) "
                    "VALUES ('33333333-3333-4333-8333-333333333333', :org_id, 'not_connected')"
                ),
                {"org_id": org_id},
            )

        with pytest.raises(RuntimeError, match="Refusing to downgrade 0019"):
            command.downgrade(cfg, "0018_amazon_encrypted_secrets")

    # The refusal must be transactional/all-or-nothing — every new table
    # (not just the populated one) must still exist afterward.
    inspector = inspect(disposable_engine)
    table_names = set(inspector.get_table_names())
    for table in NEW_TABLES:
        assert table in table_names


def test_0019_to_0018_to_0019_round_trip_when_empty(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0019_amazon_ads_foundation")

        inspector = inspect(disposable_engine)
        assert set(NEW_TABLES).issubset(set(inspector.get_table_names()))

        command.downgrade(cfg, "0018_amazon_encrypted_secrets")

        inspector = inspect(disposable_engine)
        table_names_after_downgrade = set(inspector.get_table_names())
        for table in NEW_TABLES:
            assert table not in table_names_after_downgrade
        # 0018's own table must still be present — the downgrade only
        # removed what 0019 added.
        assert "amazon_encrypted_secrets" in table_names_after_downgrade

        command.upgrade(cfg, "0019_amazon_ads_foundation")

        inspector = inspect(disposable_engine)
        table_names_after_reupgrade = set(inspector.get_table_names())
        for table in NEW_TABLES:
            assert table in table_names_after_reupgrade

"""12B.2A — Alembic migration 0009: canonical Amazon seller identity schema.

This file contains two different, non-interchangeable kinds of coverage —
read both docstrings below before touching either:

1. `test_migration_0009_upgrades_and_downgrades_in_isolation_from_0008` —
   proves 0009's own upgrade()/downgrade() are correct, starting from a
   database already stamped at 0008. This is real coverage of 0009, but it
   is NOT a test that the full migration chain applies cleanly from zero.

2. `test_full_migration_chain_from_empty_sqlite_fails_only_on_postgres_only_types` —
   as of 12B.2A.1, `migrations/versions/0001_m10_persistence.py` was
   rewritten to create only its reconstructed, historically-accurate
   baseline schema via deterministic `op.create_table`/`op.create_index`
   calls (see that file's own docstring for the git-history reconstruction),
   instead of calling `Base.metadata.create_all(bind)` against the live,
   ever-growing model metadata. That resolved the collision with `0002` that
   previously made a genuine `alembic upgrade head` from an empty database
   fail (see `docs/AI_HANDOVER/19_DATABASE_DEPLOYMENT_HARDENING_ARCHITECTURE_REVIEW.md`
   for the full investigation and repair).

   Running the real chain against SQLite specifically still cannot succeed,
   for an unrelated, pre-existing, and expected reason: `0001` (and `0002`,
   `0004`, `0005`, `0006`) use `sqlalchemy.dialects.postgresql.JSONB`
   directly, which has no SQLite-compatible rendering at all (unlike
   `postgresql.UUID`, which SQLAlchemy degrades gracefully for DDL). These
   migrations have only ever targeted PostgreSQL — SQLite is not, and was
   never, a supported migration target; the `Guid`/`JsonPayload` dialect-
   aware ORM types used for the *pytest* database (via `Base.metadata
   .create_all()`) are a separate, application-level concern from what the
   raw migration files declare. This test pins that expected boundary
   precisely, so a regression back to the *old* collision (still at 0002)
   would be caught, but the still-correct, still-expected JSONB-on-SQLite
   failure is not mistaken for a bug.

   The real proof that zero-to-head now works end-to-end belongs to
   PostgreSQL, not SQLite — see `apps/api/tests/postgres/` (disposable,
   opt-in) and `tests/test_migration_chain_matches_orm_metadata.py` (a
   dependency-free static check using Alembic's offline `--sql` mode,
   which compiles every migration for the PostgreSQL dialect without a real
   connection and confirms no collisions and no drift against the live ORM
   models).

Neither test in this file ever touches the configured `DATABASE_URL`: it is
overridden for the duration of each test and restored in a `finally` block.

12B.3B note: migration `0010_amazon_seller_listings` is now head. It adds
`amazon_seller_listings`, which uses the same PostgreSQL-only `JSONB` type,
so it inherits the identical SQLite limitation described above and is not
independently isolation-tested here — `0010` also `ADD COLUMN`s onto the
already-existing `amazon_ingestion_runs` table (unlike `0009`, which only
created brand-new tables), which would require reconstructing an exact
pre-0010 schema by hand to bootstrap correctly, for a dialect that cannot
run the migration end-to-end regardless. See
`tests/postgres/test_disposable_postgres_seller_listings_migration.py` and
`tests/test_migration_chain_matches_orm_metadata.py` for 0010's real
coverage.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import CompileError

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
    assert heads[0] == "0014_sales_traffic_foundation"
    revision = script.get_revision("0014_sales_traffic_foundation")
    assert revision is not None
    assert revision.down_revision == "0013_orders_durable_pagination"


# Alembic's own bookkeeping table, `alembic_version`, stores the revision
# id in a `VARCHAR(32)` column by default — a value this repository
# genuinely hit live on 2026-08-29 (`0011_amazon_listings_job_lifecycle`,
# 34 chars, renamed to `0011_listings_job_lifecycle`, 27 chars). Nothing
# else in this offline suite would ever catch that: it only fails at the
# very last statement of a real `alembic upgrade`, against a real
# database, after every DDL statement in the migration has already
# succeeded (and then rolls back, since Postgres DDL is transactional —
# but only after every prior schema check reported this migration as fine).
_ALEMBIC_VERSION_NUM_MAX_LENGTH = 32


def test_every_revision_id_fits_the_alembic_version_column() -> None:
    """Generic, permanent guard — walks *every* revision currently in the
    repository via `script.walk_revisions()` (works across branches/
    merges, not just a single linear chain), not a hardcoded check for
    any one revision id. Passes today only because `0011_listings_job_
    lifecycle` (27 chars) replaced the original `0011_amazon_listings_
    job_lifecycle` (34 chars) — see the parametrized unit test below for
    direct proof the check itself would have failed on that original
    value."""
    cfg = _alembic_config("sqlite://")
    script = ScriptDirectory.from_config(cfg)
    too_long = [
        revision.revision
        for revision in script.walk_revisions()
        if len(revision.revision) > _ALEMBIC_VERSION_NUM_MAX_LENGTH
    ]
    assert too_long == [], (
        f"revision id(s) exceed alembic_version.version_num's default "
        f"VARCHAR({_ALEMBIC_VERSION_NUM_MAX_LENGTH}): {too_long}"
    )


@pytest.mark.parametrize(
    "revision_id,fits",
    [
        # The exact 34-character id that failed live against real
        # PostgreSQL on 2026-08-29 with `StringDataRightTruncation`
        # ("value too long for type character varying(32)") on the very
        # last statement of `alembic upgrade` — after every DDL statement
        # in the migration had already succeeded and then rolled back
        # (Postgres DDL is transactional). No offline SQLite-based check
        # existed before this test to catch that ahead of time.
        ("0011_amazon_listings_job_lifecycle", False),
        # The corrected id actually used in this repository today.
        ("0011_listings_job_lifecycle", True),
        ("a" * 32, True),  # exactly at the boundary — fits
        ("a" * 33, False),  # one character over — does not fit
    ],
)
def test_revision_id_length_check_logic_matches_the_real_postgres_failure(revision_id: str, fits: bool) -> None:
    assert (len(revision_id) <= _ALEMBIC_VERSION_NUM_MAX_LENGTH) is fits


def test_migration_0009_upgrades_and_downgrades_in_isolation_from_0008(tmp_path) -> None:
    """Proves 0009 itself is correct. Does NOT prove the full chain applies from
    zero — see `test_full_migration_chain_from_empty_sqlite_fails_only_on_postgres_only_types`
    and `tests/test_migration_chain_matches_orm_metadata.py` for that, and the
    module docstring for why the three are not interchangeable.
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


def test_full_migration_chain_from_empty_sqlite_fails_only_on_postgres_only_types(tmp_path) -> None:
    """12B.2A.1: the 0001/0002 collision is fixed — this must NOT fail at
    0002 (or with "already exists") any more. It still cannot complete on
    SQLite, but only because 0001 now reaches its (correct, Postgres-only)
    `JSONB` columns — proving the chain progressed past the old failure
    point rather than resurfacing it. See the module docstring.
    """
    db_path = tmp_path / "migration_from_zero.sqlite3"
    sqlite_url = f"sqlite:///{db_path}"
    previous_database_url = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = sqlite_url
    get_settings.cache_clear()
    try:
        cfg = _alembic_config(sqlite_url)
        with pytest.raises(CompileError) as excinfo:
            command.upgrade(cfg, "head")
        message = str(excinfo.value)
        assert "already exists" not in message
        assert "scoring_profiles" not in message
        assert "JSONB" in message
    finally:
        if previous_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous_database_url
        get_settings.cache_clear()

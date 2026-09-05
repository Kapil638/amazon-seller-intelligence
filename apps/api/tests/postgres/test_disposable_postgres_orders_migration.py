"""12B.4B — Disposable PostgreSQL validation for migration 0012 (remediated).
Extended (12B.4D remediation) with tests for migration 0013's own
upgrade-preservation and downgrade-refusal boundaries (durable Orders
pagination continuation).

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Written and statically reasoned through
carefully (mirroring `test_disposable_postgres_seller_listings_migration.py`),
but could not be executed end-to-end in the authoring environment (no
Docker, no local PostgreSQL binary available). Whoever runs this with a
real disposable Postgres instance should treat a first run as the actual
proof, not this file's existence.

Covers the four remediated blockers specifically: success-gated checkpoint
advancement, structural environment/connection consistency (composite FKs
involving `connection_id`), the queued-then-claimed durable lifecycle, and
`Numeric(19,4)` precision/magnitude enforcement — the last of which is
*only* provable against real PostgreSQL (SQLite's `Numeric` type silently
rounds rather than rejecting; see
`tests/test_amazon_seller_orders_schema.py`'s note on this).

No SP-API client, ingestion service, read API, worker, or UI code is
exercised here — schema-level proof only.

**Migration-boundary rule, found and fixed after a real CI failure:**
whenever a test intentionally pins the database below `head` (e.g.
upgrading only to `0011_listings_job_lifecycle` to seed pre-0012 rows),
never instantiate or query the *current* ORM model for a table that
migration `0012` changed — `AmazonIngestionRun`'s six new counter columns
(`orders_received`/`orders_accepted`/`orders_rejected`/`items_received`/
`items_accepted`/`items_rejected`) each carry a Python-side `default=0`,
so SQLAlchemy includes them in any ORM-generated `INSERT` regardless of
whether the caller mentioned them — and PostgreSQL rejects that `INSERT`
with `UndefinedColumn` against a table that doesn't have those columns
yet. Seed and read back such rows with raw SQL restricted to the columns
that genuinely existed at the pinned revision instead; only use the
current ORM once the test has actually upgraded to `head` (or whichever
revision first introduces every column that model maps).

**Recurrence, found and fixed again for migration 0013:** the exact same
bug reappeared one migration later — `test_existing_0011_database_
upgrades_to_0012_preserving_data` was pinned correctly to exactly `0012_
orders_foundation`, but still called `session.get(AmazonIngestionRun,
run_id)` after 0013 (12B.4D remediation) added three more columns to
that same model. The fix is identical in kind: replace the post-upgrade
ORM read with raw SQL restricted to the columns that exist at the pinned
revision.

**Recurrence, a third time, for migration 0014 (12B.6A):** the "every
`AmazonIngestionRun` reference in this file was re-audited" claim this
docstring used to make above was itself falsified by a real CI failure —
`test_existing_0012_database_upgrades_to_0013_preserving_data` (this
file's own dedicated 0012 -> 0013 boundary proof, pinned correctly to
`0013_orders_durable_pagination`, never upgraded to `0014`) still ended
with `session.get(AmazonIngestionRun, run_id)`. Once `0014_sales_
traffic_foundation` added seven more nullable report-lifecycle columns
(`report_id`, ...) to that same model, that final `session.get()`
generated a `SELECT` naming all seven, and PostgreSQL correctly rejected
it with `UndefinedColumn: column amazon_ingestion_runs.report_id does
not exist` — a database genuinely still pinned at 0013 has no such
column. Fixed identically again: the trailing ORM read was replaced with
raw SQL restricted to the columns that exist at 0013, preserving the
same two assertions (`row is not None` and the pagination-token value).
**The claim "every reference was re-audited" is retired** — it has now
been wrong twice. The durable, disprovable claim instead: any test in
this file (or any Postgres-guarded migration test file in this
repository) that pins the database below `head` must never call
`session.get(<current ORM class>, ...)` or otherwise let the ORM build a
`SELECT`/`INSERT` against that model, full stop, regardless of how
recently it was last checked — a future migration will add more columns
again, and only a raw-SQL read scoped to the pinned revision's own
column set stays correct as the model keeps growing after it.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import (
    AmazonConnection,
    AmazonIngestionRun,
    AmazonIngestionRunMarketplaceParticipation,
    AmazonMarketplaceParticipation,
    AmazonOrdersSyncCheckpoint,
    AmazonSellerAccount,
    AmazonSellerOrder,
    Base,
    Organization,
)
from app.persistence.repositories import (
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonSellerOrderRepository,
    OrdersRunFinalizationIncomplete,
)
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]


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


def _seed_org_seller_connection_and_participation(engine, *, environment="PRODUCTION", region="na"):
    org_id = uuid4()
    seller_account_id = uuid4()
    connection_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="12B.4B Postgres Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id, organization_id=org_id, selling_partner_id="A12B4BPOSTGRES1", status="active"
            )
        )
        session.add(
            AmazonConnection(
                id=connection_id, organization_id=org_id, provider="SP_API", environment=environment,
                region=region, status="connected",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id, organization_id=org_id, seller_account_id=seller_account_id,
                connection_id=connection_id, marketplace_id="ATVPDKIKX0DER", region=region,
            )
        )
        session.commit()
    return org_id, seller_account_id, connection_id, participation_id


# 1: historical boundary — a fresh empty PostgreSQL upgrades cleanly to 0011
# first (pre-12B.4B state), matching the existing repository head at the
# time 0012 was authored.
def test_empty_postgres_upgrades_cleanly_to_0011(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0011_listings_job_lifecycle")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == "0011_listings_job_lifecycle"


# 2: expected new tables/columns/constraints/FKs exist after 0012.
#
# Pinned to the exact "0012_orders_foundation" revision, not "head" —
# this test's name and every assertion below are specifically about what
# migration 0012 delivers (reflection-only: `inspect()`, never the current
# ORM/`Base.metadata`), so it must keep proving exactly that fact
# regardless of what a future 0013+ migration later adds. Upgrading to
# "head" instead would silently let this test start validating "whatever
# schema head currently has" — the same latent boundary defect already
# found and fixed once in this file (see `test_existing_0011_database_
# upgrades_to_0012_preserving_data`), just with reflection instead of the
# ORM as the vector this time.
def test_empty_postgres_upgrade_produces_expected_orders_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0012_orders_foundation")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    for table in (
        "amazon_seller_orders",
        "amazon_seller_order_items",
        "amazon_ingestion_run_marketplace_participations",
        "amazon_orders_sync_checkpoints",
    ):
        assert table in tables

    order_columns = {c["name"] for c in inspector.get_columns("amazon_seller_orders")}
    assert "organization_id" not in order_columns
    assert "seller_account_id" not in order_columns
    for forbidden_substring in ("buyer", "recipient", "address", "gift", "payment", "tax_registration"):
        assert not any(forbidden_substring in c for c in order_columns)

    amount_column = next(c for c in inspector.get_columns("amazon_seller_orders") if c["name"] == "order_total_amount")
    assert amount_column["type"].precision == 19
    assert amount_column["type"].scale == 4

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ingestion_runs")}
    assert {
        "orders_received", "orders_accepted", "orders_rejected",
        "items_received", "items_accepted", "items_rejected",
    }.issubset(run_columns)

    run_checks = {c["name"] for c in inspector.get_check_constraints("amazon_ingestion_runs")}
    assert "ck_amazon_ingestion_runs_orders_scope_required" in run_checks

    run_indexes = {ix["name"] for ix in inspector.get_indexes("amazon_ingestion_runs")}
    assert "uq_amazon_ingestion_runs_active_orders_scope" in run_indexes

    run_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("amazon_ingestion_runs")}
    connection_fk = run_fks["fk_amazon_ingestion_runs_connection_org_region_env"]
    assert connection_fk["referred_table"] == "amazon_connections"
    assert set(connection_fk["constrained_columns"]) == {"connection_id", "organization_id", "region", "environment"}

    order_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("amazon_seller_orders")}
    provenance_fk = order_fks["fk_amazon_seller_orders_last_run_same_participation"]
    assert provenance_fk["referred_table"] == "amazon_ingestion_run_marketplace_participations"
    assert set(provenance_fk["constrained_columns"]) == {"last_ingestion_run_id", "marketplace_participation_id"}
    assert provenance_fk["options"].get("ondelete") == "RESTRICT"

    checkpoint_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("amazon_orders_sync_checkpoints")}
    checkpoint_provenance_fk = checkpoint_fks["fk_amazon_orders_sync_checkpoints_run_same_participation"]
    assert checkpoint_provenance_fk["referred_table"] == "amazon_ingestion_run_marketplace_participations"

    assoc_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("amazon_ingestion_run_marketplace_participations")}
    run_scope_fk = assoc_fks["fk_amazon_ingestion_run_parts_run_scope"]
    assert set(run_scope_fk["constrained_columns"]) == {
        "ingestion_run_id", "organization_id", "seller_account_id", "region", "connection_id"
    }
    participation_scope_fk = assoc_fks["fk_amazon_ingestion_run_parts_participation_scope"]
    assert set(participation_scope_fk["constrained_columns"]) == {
        "marketplace_participation_id", "organization_id", "seller_account_id", "region", "connection_id"
    }


# 3: full drift parity — deliberately kept on "head", not pinned to
# "0012_orders_foundation": this test's own purpose is comparing the
# reflected schema against `Base.metadata` (the current ORM), so it must
# always track whatever head currently is to remain a meaningful drift
# check for every future migration, not just 0012. Its name says
# "_after_0012" only because 0012 was head at authoring time.
def test_empty_postgres_upgrade_matches_orm_metadata_exactly_after_0012(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    inspector = inspect(disposable_engine)
    reflected_tables = set(inspector.get_table_names()) - {"alembic_version"}
    orm_tables = set(Base.metadata.tables)
    assert reflected_tables == orm_tables, (
        f"missing from migrations: {orm_tables - reflected_tables}; "
        f"extra in migrations: {reflected_tables - orm_tables}"
    )
    for table_name, orm_table in Base.metadata.tables.items():
        reflected_columns = {c["name"] for c in inspector.get_columns(table_name)}
        orm_columns = set(orm_table.columns.keys())
        assert reflected_columns == orm_columns, table_name


# 4-5: an existing 0011 database upgrades to 0012 preserving existing rows,
# with the six new counters deterministically defaulted to 0.
def test_existing_0011_database_upgrades_to_0012_preserving_data(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0011_listings_job_lifecycle")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    run_id = uuid4()
    # Raw SQL restricted to columns that genuinely exist at 0011 — NOT the
    # current `AmazonIngestionRun` ORM. The database is still pinned at
    # 0011 here (upgraded to "head" only below); the current ORM class
    # maps six additional 0012 columns (`orders_received`/`orders_accepted`/
    # `orders_rejected`/`items_received`/`items_accepted`/`items_rejected`),
    # each with a Python-side `default=0` — SQLAlchemy therefore includes
    # them explicitly in any ORM-generated INSERT, which PostgreSQL would
    # reject with `UndefinedColumn` against a table that doesn't have them
    # yet. This is exactly the class of bug this file's own module
    # docstring warns about: never instantiate a current model against a
    # database intentionally pinned below the revision that model reflects.
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs ("
                "id, organization_id, seller_account_id, marketplace_participation_id, connection_id, "
                "run_type, domain, region, environment, status"
                ") VALUES ("
                ":id, :organization_id, :seller_account_id, :marketplace_participation_id, :connection_id, "
                "'listings', 'listings_items', 'na', 'PRODUCTION', 'succeeded'"
                ")"
            ),
            {
                "id": run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
                "marketplace_participation_id": participation_id, "connection_id": connection_id,
            },
        )

    with _alembic_environment(url):
        command.upgrade(cfg, "0012_orders_foundation")

    # Pinned to the exact "0012_orders_foundation" revision, not "head" —
    # this test's own name and purpose are specifically the 0011 -> 0012
    # boundary. Using "head" here would let this test silently start
    # exercising whatever a future 0013+ migration adds instead, on top of
    # the ORM this test uses below (which will itself grow past 0012 once
    # such a migration ships) — recreating the exact class of latent
    # boundary defect already found and fixed once in this file, just one
    # revision later.
    #
    # `Organization`/`AmazonSellerAccount`/`AmazonMarketplaceParticipation`
    # are unchanged by every migration after 0012 (including 0013), so
    # `session.get(...)` against those three remains safe here. The
    # current `AmazonIngestionRun` ORM is NOT safe to use at this boundary
    # any more: migration 0013 (12B.4D remediation) added three columns to
    # it (`orders_window_last_updated_after`, `orders_window_captured_at`,
    # `orders_pagination_next_token`), so `session.get(AmazonIngestionRun,
    # ...)` would generate a SELECT naming all three — columns that do not
    # exist on a database still pinned at exactly 0012 — and PostgreSQL
    # would correctly reject it with `UndefinedColumn`. This is exactly
    # the class of bug this file's own module docstring warns about,
    # recurring one migration later: the fix is the same technique already
    # used for the INSERT above (raw SQL restricted to columns that
    # genuinely exist at the pinned revision), not upgrading this test to
    # "head".
    with Session(disposable_engine) as session:
        assert session.get(Organization, org_id) is not None
        assert session.get(AmazonSellerAccount, seller_account_id) is not None
        assert session.get(AmazonMarketplaceParticipation, participation_id) is not None

    with disposable_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT run_type, status, orders_received, orders_accepted, orders_rejected, "
                "items_received, items_accepted, items_rejected "
                "FROM amazon_ingestion_runs WHERE id = :id"
            ),
            {"id": run_id},
        ).one()
    assert row.run_type == "listings"
    assert row.status == "succeeded"
    assert row.orders_received == 0
    assert row.orders_accepted == 0
    assert row.orders_rejected == 0
    assert row.items_received == 0
    assert row.items_accepted == 0
    assert row.items_rejected == 0


# 6: downgrade ordering — 0012 -> 0011 removes only what 0012 added, when
# no Orders data exists (the safe case).
#
# Upgrades to exactly "0012_orders_foundation" first, not "head" — this
# test's name is specifically the 0012 -> 0011 boundary; starting from
# "head" would route the downgrade through every migration after 0012 too
# once one exists, testing "does head fully unwind to 0011" rather than
# the precise fact this test claims to prove. Safe regardless either way
# (only `inspect()` is used below, never the current ORM), but pinning
# keeps this test's proof exact as new migrations are added.
def test_downgrade_0012_to_0011_is_clean_when_no_orders_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0012_orders_foundation")
        command.downgrade(cfg, "0011_listings_job_lifecycle")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_seller_orders" not in tables
    assert "amazon_seller_order_items" not in tables
    assert "amazon_ingestion_run_marketplace_participations" not in tables
    assert "amazon_orders_sync_checkpoints" not in tables

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ingestion_runs")}
    for column in (
        "orders_received", "orders_accepted", "orders_rejected",
        "items_received", "items_accepted", "items_rejected",
    ):
        assert column not in run_columns

    connection_uniques = {uq["name"] for uq in inspector.get_unique_constraints("amazon_connections")}
    assert "uq_amazon_connections_id_org_region_environment" not in connection_uniques

    run_check_definitions = " ".join(
        (c.get("sqltext") or "") for c in inspector.get_check_constraints("amazon_ingestion_runs")
    )
    assert "orders" not in run_check_definitions


# 7: downgrade refuses when Orders data would be discarded — never
# silently reinterpreted as Listings data.
#
# Same reasoning as the previous test: pinned to exactly
# "0012_orders_foundation" rather than "head", since this test's name and
# purpose are specifically the 0012 -> 0011 boundary. Only raw SQL/
# `inspect()` are used for `amazon_ingestion_runs` throughout (never the
# current ORM), so this is safe either way — pinning simply keeps the
# proof exact regardless of what a future 0013+ migration later adds.
def test_downgrade_0012_to_0011_refuses_when_orders_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0012_orders_foundation")

    org_id, seller_account_id, connection_id, _ = _seed_org_seller_connection_and_participation(disposable_engine)
    run_id = uuid4()
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs "
                "(id, organization_id, seller_account_id, marketplace_participation_id, connection_id, run_type, "
                "domain, region, environment, status) "
                "VALUES (:id, :org_id, :seller_account_id, NULL, :connection_id, 'orders', 'orders', 'na', "
                "'PRODUCTION', 'succeeded')"
            ),
            {"id": run_id, "org_id": org_id, "seller_account_id": seller_account_id, "connection_id": connection_id},
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0011_listings_job_lifecycle")

    inspector = inspect(disposable_engine)
    assert "amazon_seller_orders" in set(inspector.get_table_names())
    with disposable_engine.connect() as conn:
        still_there = conn.execute(
            text("SELECT count(*) FROM amazon_ingestion_runs WHERE id = :id AND run_type = 'orders'"),
            {"id": run_id},
        ).scalar()
    assert still_there == 1


# 7a (12B.4D CI remediation): the explicit 0012 -> 0013 upgrade proof —
# existing pre-0013 Orders/run data survives, the three new columns exist
# and start NULL, the orders-only pagination-scope check constraint
# behaves correctly in both directions, and the database reports exactly
# "0013_orders_durable_pagination" afterward.
#
# Pinned to exactly "0012_orders_foundation" before seeding, then upgraded
# exactly once to "0013_orders_durable_pagination" — never "head" — since
# this test's own name and purpose are specifically the 0012 -> 0013
# boundary. Every read/write in this test, including after the upgrade,
# is raw SQL restricted to the columns that genuinely exist at whichever
# revision the database is pinned to at that point (0012 before the
# upgrade, 0013 after it) — never the current `AmazonIngestionRun` ORM,
# which by now also maps 0014_sales_traffic_foundation's own later
# columns (e.g. `report_id`) that do not exist yet at either revision
# this test ever reaches. See this file's own module docstring for the
# full history of this exact bug recurring across 0012, 0013, and 0014.
def test_existing_0012_database_upgrades_to_0013_preserving_data(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0012_orders_foundation")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    run_id = uuid4()
    other_run_id = uuid4()
    with disposable_engine.begin() as conn:
        # A representative pre-0013 Orders run — exactly the 0012-era
        # amazon_ingestion_runs column set for run_type='orders'.
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs ("
                "id, organization_id, seller_account_id, marketplace_participation_id, connection_id, "
                "run_type, domain, region, environment, status"
                ") VALUES ("
                ":id, :organization_id, :seller_account_id, NULL, :connection_id, "
                "'orders', 'orders', 'na', 'PRODUCTION', 'succeeded'"
                ")"
            ),
            {
                "id": run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
                "connection_id": connection_id,
            },
        )
        # A second, non-orders run — used below to prove the pagination
        # columns stay structurally forbidden for any run_type other than
        # 'orders', both before and after this upgrade.
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs ("
                "id, organization_id, seller_account_id, marketplace_participation_id, connection_id, "
                "run_type, domain, region, environment, status"
                ") VALUES ("
                ":id, :organization_id, :seller_account_id, :participation_id, :connection_id, "
                "'listings', 'listings_items', 'na', 'PRODUCTION', 'succeeded'"
                ")"
            ),
            {
                "id": other_run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
                "participation_id": participation_id, "connection_id": connection_id,
            },
        )

    with _alembic_environment(url):
        command.upgrade(cfg, "0013_orders_durable_pagination")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == "0013_orders_durable_pagination"

    # Existing data survived the upgrade untouched, and the three new
    # columns exist and begin NULL for a pre-existing row — an ADD COLUMN
    # migration must never invent a value for rows that predate it.
    with disposable_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT run_type, status, orders_window_last_updated_after, "
                "orders_window_captured_at, orders_pagination_next_token "
                "FROM amazon_ingestion_runs WHERE id = :id"
            ),
            {"id": run_id},
        ).mappings().first()
    assert row is not None
    assert row["run_type"] == "orders"
    assert row["status"] == "succeeded"
    assert row["orders_window_last_updated_after"] is None
    assert row["orders_window_captured_at"] is None
    assert row["orders_pagination_next_token"] is None

    # The orders-only pagination-scope check constraint
    # (`ck_amazon_ingestion_runs_orders_pagination_scope_required`)
    # rejects a non-'orders' row that has any pagination column set...
    with disposable_engine.connect() as conn, pytest.raises(IntegrityError):
        with conn.begin():
            conn.execute(
                text(
                    "UPDATE amazon_ingestion_runs SET orders_pagination_next_token = 'SHOULD-BE-REJECTED' "
                    "WHERE id = :id"
                ),
                {"id": other_run_id},
            )

    # ...but permits it for a genuine 'orders' row — proving the
    # constraint is scoped correctly, not merely always-failing.
    with disposable_engine.begin() as conn:
        conn.execute(
            text("UPDATE amazon_ingestion_runs SET orders_pagination_next_token = 'ALLOWED' WHERE id = :id"),
            {"id": run_id},
        )
    with disposable_engine.connect() as conn:
        allowed_value = conn.execute(
            text("SELECT orders_pagination_next_token FROM amazon_ingestion_runs WHERE id = :id"),
            {"id": run_id},
        ).scalar()
    assert allowed_value == "ALLOWED"

    # NOT the current `AmazonIngestionRun` ORM here — this test stays
    # pinned at exactly 0013 for its entire duration and never upgrades
    # to 0014, so the ORM (which also maps 0014_sales_traffic_
    # foundation's seven report-lifecycle columns, e.g. `report_id`)
    # would generate a SELECT naming columns that genuinely do not exist
    # yet, which PostgreSQL correctly rejects with `UndefinedColumn` —
    # this is exactly the recurring bug this file's own module docstring
    # now documents happening a third time. Raw SQL restricted to the
    # columns that exist at 0013 instead; the 0013 -> 0014 boundary has
    # its own dedicated proof in `test_disposable_postgres_sales_
    # traffic_migration.py`, not here.
    with disposable_engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, orders_pagination_next_token FROM amazon_ingestion_runs WHERE id = :id"),
            {"id": run_id},
        ).mappings().first()
    assert row is not None
    assert row["orders_pagination_next_token"] == "ALLOWED"


# 7b (12B.4D remediation): migration 0013's own downgrade refusal — an
# in-flight `orders_pagination_next_token` has no representation in
# 0012's schema, so downgrading while one exists must be refused, never
# silently dropped (which would strand a resumable run at a page-one
# restart the next time it's claimed, with no record that anything was
# lost). Pinned to exactly "0013_orders_durable_pagination" as the
# upgrade target (== head at authoring time) and "0012_orders_
# foundation" as the downgrade target, since this test's purpose is
# specifically the 0013 -> 0012 boundary.
def test_downgrade_0013_to_0012_refuses_when_pagination_token_in_flight(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0013_orders_durable_pagination")

    org_id, seller_account_id, connection_id, _ = _seed_org_seller_connection_and_participation(disposable_engine)
    run_id = uuid4()
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs "
                "(id, organization_id, seller_account_id, marketplace_participation_id, connection_id, run_type, "
                "domain, region, environment, status, orders_pagination_next_token) "
                "VALUES (:id, :org_id, :seller_account_id, NULL, :connection_id, 'orders', 'orders', 'na', "
                "'PRODUCTION', 'started', 'IN-FLIGHT-TOKEN')"
            ),
            {"id": run_id, "org_id": org_id, "seller_account_id": seller_account_id, "connection_id": connection_id},
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0012_orders_foundation")

    inspector = inspect(disposable_engine)
    assert "orders_pagination_next_token" in {
        col["name"] for col in inspector.get_columns("amazon_ingestion_runs")
    }
    with disposable_engine.connect() as conn:
        still_there = conn.execute(
            text(
                "SELECT count(*) FROM amazon_ingestion_runs "
                "WHERE id = :id AND orders_pagination_next_token = 'IN-FLIGHT-TOKEN'"
            ),
            {"id": run_id},
        ).scalar()
    assert still_there == 1

    # Clearing the token (the same effect any terminal completion has)
    # unblocks the downgrade cleanly.
    with disposable_engine.begin() as conn:
        conn.execute(
            text("UPDATE amazon_ingestion_runs SET orders_pagination_next_token = NULL WHERE id = :id"),
            {"id": run_id},
        )
    with _alembic_environment(url):
        command.downgrade(cfg, "0012_orders_foundation")
    inspector = inspect(disposable_engine)
    assert "orders_pagination_next_token" not in {
        col["name"] for col in inspector.get_columns("amazon_ingestion_runs")
    }


# 8: active Orders run uniqueness under REAL PostgreSQL — the coarser
# (seller_account, region, environment) partial unique index, covering
# queued/started/waiting_to_retry together.
def test_active_orders_run_uniqueness_enforced_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with Session(disposable_engine) as session:
        outcome = AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            marketplace_participation_ids=[participation_id], region="na", environment="PRODUCTION",
        )
        session.commit()
    assert outcome.claimed is True

    with Session(disposable_engine) as session:
        second = AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            marketplace_participation_ids=[participation_id], region="na", environment="PRODUCTION",
        )
        session.commit()
    assert second.claimed is False


# 9: cross-participation provenance rejected by the real composite FK
# targeting the association table (not amazon_ingestion_runs directly).
def test_order_provenance_must_reference_a_run_that_covered_its_participation_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_a = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    participation_b = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b, organization_id=org_id, seller_account_id=seller_account_id,
                connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2", region="na",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        outcome = AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            marketplace_participation_ids=[participation_a], region="na", environment="PRODUCTION",
        )
        session.commit()
        run_id = outcome.run_id

    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerOrder(
                marketplace_participation_id=participation_b, amazon_order_id="PG-CROSS-PARTICIPATION",
                last_ingestion_run_id=run_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerOrder(
                marketplace_participation_id=participation_a, amazon_order_id="PG-SAME-PARTICIPATION",
                last_ingestion_run_id=run_id,
            )
        )
        session.commit()  # must not raise


# 10: cross-organization association row rejected by real Postgres.
def test_association_row_cannot_mix_organizations_on_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_a, seller_a, connection_a, participation_a = _seed_org_seller_connection_and_participation(disposable_engine)
    org_b_id = uuid4()
    with Session(disposable_engine) as session:
        session.add(Organization(id=org_b_id, name="Org B"))
        session.commit()

    run_id = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_id, organization_id=org_a, seller_account_id=seller_a, connection_id=connection_a,
                marketplace_participation_id=None, run_type="orders", domain="orders", region="na",
                environment="PRODUCTION", status="queued",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRunMarketplaceParticipation(
                ingestion_run_id=run_id, marketplace_participation_id=participation_a,
                organization_id=org_b_id, seller_account_id=seller_a, region="na", connection_id=connection_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# 11: checkpoint cannot reference a run outside its participation
# membership, enforced by real Postgres.
def test_checkpoint_provenance_enforced_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_a = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    participation_b = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b, organization_id=org_id, seller_account_id=seller_account_id,
                connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2", region="na",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        outcome = AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            marketplace_participation_ids=[participation_a], region="na", environment="PRODUCTION",
        )
        session.commit()
        run_id = outcome.run_id

    with Session(disposable_engine) as session:
        session.add(
            AmazonOrdersSyncCheckpoint(
                marketplace_participation_id=participation_b, organization_id=org_id,
                seller_account_id=seller_account_id, last_successful_run_id=run_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# 12 (Blocker 1): success-gated checkpoint advancement, end to end, using
# the real repository lifecycle against real PostgreSQL.
def test_finalize_successful_orders_run_advances_checkpoint_on_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with Session(disposable_engine) as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        enqueued = repo.enqueue_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            marketplace_participation_ids=[participation_id], region="na", environment="PRODUCTION",
        )
        session.commit()
        claimed = repo.claim_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, region="na", environment="PRODUCTION",
            lease_owner="pg-test-worker", lease_duration_seconds=300,
        )
        session.commit()
        assert claimed.claimed is True
        from datetime import UTC, datetime

        watermark = datetime.now(UTC)
        outcome = repo.finalize_successful_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, ingestion_run_id=claimed.run_id,
            participation_watermarks={participation_id: watermark},
        )
        session.commit()
    assert outcome.finalized is True
    with Session(disposable_engine) as session:
        run = session.get(AmazonIngestionRun, claimed.run_id)
        assert run.status == "succeeded"
        checkpoint = session.get(AmazonOrdersSyncCheckpoint, participation_id)
        assert checkpoint is not None


# 13 (Blocker 4, corrected): PostgreSQL's real NUMERIC(19,4) does NOT
# reject excess *scale* (more than 4 fractional digits) — it silently
# ROUNDS the value at type-coercion time. This test exists specifically
# to document that real, verified behavior (a schema review initially and
# incorrectly assumed PostgreSQL would raise `DataError` here; it does
# not). Excess-scale *rejection* is enforced at the application layer
# instead — see `app.persistence.repositories._validate_orders_money_
# amount` and `tests/test_amazon_seller_orders_schema.py`'s Python-level
# rejection tests, which run identically on SQLite because the rejection
# happens before any SQL is constructed. This raw-SQL insert deliberately
# bypasses the repository (and therefore that validation) to isolate and
# prove the database's own behavior in isolation.
def test_excess_scale_is_rounded_not_rejected_by_raw_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_seller_orders (id, marketplace_participation_id, amazon_order_id, "
                "order_total_amount) VALUES (gen_random_uuid(), :pid, 'PG-EXCESS-SCALE-RAW', :amount)"
            ),
            {"pid": participation_id, "amount": Decimal("12.34567")},
        )
    with disposable_engine.connect() as conn:
        stored = conn.execute(
            text("SELECT order_total_amount FROM amazon_seller_orders WHERE amazon_order_id = 'PG-EXCESS-SCALE-RAW'")
        ).scalar()
    assert stored == Decimal("12.3457")  # rounded (half-up), not rejected, not truncated to 12.3456


# 14 (Blocker 4): excessive magnitude (more than 19 total digits) IS
# rejected by real PostgreSQL's NUMERIC(19,4) with `numeric_field_
# overflow` — this is the one boundary PostgreSQL's column type genuinely
# enforces on its own, unlike scale.
def test_excessive_magnitude_rejected_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        with pytest.raises(DataError):
            conn.execute(
                text(
                    "INSERT INTO amazon_seller_orders (id, marketplace_participation_id, amazon_order_id, "
                    "order_total_amount) VALUES (gen_random_uuid(), :pid, 'PG-EXCESS-MAGNITUDE', :amount)"
                ),
                {"pid": participation_id, "amount": Decimal("9999999999999999.9999")},
            )


# 15 (Blocker 4): exact round-trip at the true magnitude boundary
# (15 integer digits + 4 fractional = 19 total) through the real
# repository (validation must accept it, and PostgreSQL must store it
# exactly) — SQLite cannot prove this specific value (its float-based
# `NUMERIC` storage rounds it; see the note in
# tests/test_amazon_seller_orders_schema.py).
def test_boundary_magnitude_round_trips_exactly_on_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    boundary = Decimal("999999999999999.9999")
    with Session(disposable_engine) as session:

        AmazonSellerOrderRepository(session).upsert(
            organization_id=org_id, marketplace_participation_id=participation_id,
            amazon_order_id="PG-BOUNDARY-MAGNITUDE", fulfillment_status=None, fulfilled_by=None,
            sales_channel_name=None, sales_channel_marketplace_id=None, sales_channel_marketplace_name=None,
            items_shipped_count=None, items_unshipped_count=None, order_total_amount=boundary,
            order_total_currency="USD", is_business_order=False, is_prime=False, was_cancelled=False,
            amazon_created_at=None, amazon_last_updated_at=None, last_ingestion_run_id=None,
        )
        session.commit()
    with Session(disposable_engine) as session:
        row = session.query(AmazonSellerOrder).filter_by(amazon_order_id="PG-BOUNDARY-MAGNITUDE").first()
        assert row.order_total_amount == boundary


# 16 (checkpoint atomicity, mid-batch failure): confirmed against real
# PostgreSQL, not only SQLite — see
# tests/test_amazon_seller_orders_schema.py::test_finalize_mid_batch_failure_rolls_back_run_and_all_earlier_checkpoint_writes
# for the fuller commentary. A valid participation's checkpoint write
# genuinely happens before the ineligible one raises; the rollback must
# undo both the run's status flip and the valid participation's already-
# written checkpoint.
def test_finalize_mid_batch_failure_rolls_back_on_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_valid = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    participation_foreign = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_foreign, organization_id=org_id, seller_account_id=seller_account_id,
                connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2", region="na",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        enqueued = repo.enqueue_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            marketplace_participation_ids=[participation_valid], region="na", environment="PRODUCTION",
        )
        session.commit()
        claimed = repo.claim_orders_run(
            organization_id=org_id, seller_account_id=seller_account_id, region="na", environment="PRODUCTION",
            lease_owner="pg-test-worker", lease_duration_seconds=300,
        )
        session.commit()

    from datetime import UTC, datetime


    with Session(disposable_engine) as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        with pytest.raises(OrdersRunFinalizationIncomplete):
            repo.finalize_successful_orders_run(
                organization_id=org_id, seller_account_id=seller_account_id, ingestion_run_id=claimed.run_id,
                participation_watermarks={
                    participation_valid: datetime.now(UTC),
                    participation_foreign: datetime.now(UTC),
                },
            )
        session.rollback()

    with Session(disposable_engine) as session:
        run = session.get(AmazonIngestionRun, claimed.run_id)
        assert run.status == "started"
        assert session.get(AmazonOrdersSyncCheckpoint, participation_valid) is None
        assert session.get(AmazonOrdersSyncCheckpoint, participation_foreign) is None

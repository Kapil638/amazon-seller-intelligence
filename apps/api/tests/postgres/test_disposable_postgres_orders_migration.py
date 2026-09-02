"""12B.4B — Disposable PostgreSQL validation for migration 0012 (remediated).

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
def test_empty_postgres_upgrade_produces_expected_orders_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

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


# 3: full drift parity after 0012.
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
    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_id, organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, connection_id=connection_id,
                run_type="listings", domain="listings_items", region="na", environment="PRODUCTION",
                status="succeeded",
            )
        )
        session.commit()

    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    with Session(disposable_engine) as session:
        assert session.get(Organization, org_id) is not None
        assert session.get(AmazonSellerAccount, seller_account_id) is not None
        assert session.get(AmazonMarketplaceParticipation, participation_id) is not None
        run = session.get(AmazonIngestionRun, run_id)
        assert run is not None
        assert run.run_type == "listings"
        assert run.status == "succeeded"
        assert run.orders_received == 0
        assert run.orders_accepted == 0
        assert run.orders_rejected == 0
        assert run.items_received == 0
        assert run.items_accepted == 0
        assert run.items_rejected == 0


# 6: downgrade ordering — 0012 -> 0011 removes only what 0012 added, when
# no Orders data exists (the safe case).
def test_downgrade_0012_to_0011_is_clean_when_no_orders_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")
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
def test_downgrade_0012_to_0011_refuses_when_orders_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

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

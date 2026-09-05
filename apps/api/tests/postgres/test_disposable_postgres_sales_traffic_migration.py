"""12B.6A — Disposable PostgreSQL validation for migration 0014 (Sales and
Traffic report foundation).

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Mirrors `test_disposable_postgres_orders_
migration.py`'s own conventions exactly, including its migration-boundary
rule: a test intentionally pinned below `head` must never instantiate or
query the *current* ORM model for a table 0014 changed
(`AmazonIngestionRun` gained seven new nullable report-lifecycle columns)
— use raw SQL restricted to the columns that genuinely existed at the
pinned revision instead, and only use the current ORM once the database
has actually upgraded past 0014.

No SP-API client, ingestion service, read API, worker, or UI code is
exercised here — schema-level proof only. Full ORM/reflection drift
parity at `head` (which, once this migration lands, includes every table
this file introduces) is already proven generically by `test_disposable_
postgres_orders_migration.py::test_empty_postgres_upgrade_matches_orm_
metadata_exactly_after_0012` — not duplicated here.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
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
    AmazonMarketplaceParticipation,
    AmazonSalesAndTrafficDailyFact,
    AmazonSellerAccount,
    Base,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository
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
        session.add(Organization(id=org_id, name="12B.6A Postgres Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id, organization_id=org_id, selling_partner_id="A12B6APOSTGRES1", status="active"
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


# 1: existing pre-0014 database upgrades to 0014 preserving data, with the
# seven new report-lifecycle columns starting NULL for a pre-existing row.
def test_existing_0013_database_upgrades_to_0014_preserving_data(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0013_orders_durable_pagination")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    run_id = uuid4()
    # Raw SQL restricted to columns that genuinely exist at 0013 — NOT the
    # current AmazonIngestionRun ORM, which maps seven additional 0014
    # columns. See this file's own module docstring.
    with disposable_engine.begin() as conn:
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
                "id": run_id, "organization_id": org_id, "seller_account_id": seller_account_id,
                "participation_id": participation_id, "connection_id": connection_id,
            },
        )

    with _alembic_environment(url):
        command.upgrade(cfg, "0014_sales_traffic_foundation")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == "0014_sales_traffic_foundation"

    with disposable_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT run_type, status, report_id, report_document_id, report_processing_status, "
                "report_data_start_time, report_data_end_time, report_date_granularity, report_asin_granularity "
                "FROM amazon_ingestion_runs WHERE id = :id"
            ),
            {"id": run_id},
        ).mappings().first()
    assert row is not None
    assert row["run_type"] == "listings"
    assert row["status"] == "succeeded"
    for column in (
        "report_id", "report_document_id", "report_processing_status",
        "report_data_start_time", "report_data_end_time", "report_date_granularity", "report_asin_granularity",
    ):
        assert row[column] is None

    # Now — and only now, after the upgrade to 0014 — the current
    # AmazonIngestionRun ORM is safe to use.
    with Session(disposable_engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        assert run is not None
        assert run.report_id is None


# 2: expected new tables/columns/constraints/FKs/indexes exist after 0014.
def test_empty_postgres_upgrade_produces_expected_sales_traffic_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0014_sales_traffic_foundation")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    for table in (
        "amazon_sales_traffic_daily_facts",
        "amazon_sales_traffic_product_facts",
        "amazon_sales_traffic_sync_checkpoints",
    ):
        assert table in tables

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ingestion_runs")}
    assert {
        "report_id", "report_document_id", "report_processing_status",
        "report_data_start_time", "report_data_end_time", "report_date_granularity", "report_asin_granularity",
    }.issubset(run_columns)

    run_checks = {c["name"] for c in inspector.get_check_constraints("amazon_ingestion_runs")}
    assert "ck_amazon_ingestion_runs_sales_traffic_scope_required" in run_checks
    assert "ck_amazon_ingestion_runs_sales_traffic_fields_scope_required" in run_checks

    run_indexes = {ix["name"] for ix in inspector.get_indexes("amazon_ingestion_runs")}
    assert "uq_amazon_ingestion_runs_active_sales_traffic_scope" in run_indexes

    daily_amount_column = next(
        c for c in inspector.get_columns("amazon_sales_traffic_daily_facts") if c["name"] == "ordered_product_sales_amount"
    )
    assert daily_amount_column["type"].precision == 19
    assert daily_amount_column["type"].scale == 4

    daily_uniques = {uq["name"] for uq in inspector.get_unique_constraints("amazon_sales_traffic_daily_facts")}
    assert "uq_amazon_sales_traffic_daily_facts_natural_key" in daily_uniques

    product_uniques = {uq["name"] for uq in inspector.get_unique_constraints("amazon_sales_traffic_product_facts")}
    assert "uq_amazon_sales_traffic_product_facts_natural_key" in product_uniques

    product_checks = {c["name"] for c in inspector.get_check_constraints("amazon_sales_traffic_product_facts")}
    assert "ck_amazon_sales_traffic_product_facts_granularity_ids" in product_checks

    checkpoint_columns = {c["name"] for c in inspector.get_columns("amazon_sales_traffic_sync_checkpoints")}
    assert {"marketplace_participation_id", "synced_through_date", "last_successful_run_id"}.issubset(
        checkpoint_columns
    )


# 3: downgrade is clean when no Sales and Traffic data exists at all.
def test_downgrade_0014_to_0013_is_clean_when_no_sales_traffic_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0014_sales_traffic_foundation")
        command.downgrade(cfg, "0013_orders_durable_pagination")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    for table in (
        "amazon_sales_traffic_daily_facts",
        "amazon_sales_traffic_product_facts",
        "amazon_sales_traffic_sync_checkpoints",
    ):
        assert table not in tables

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ingestion_runs")}
    for column in (
        "report_id", "report_document_id", "report_processing_status",
        "report_data_start_time", "report_data_end_time", "report_date_granularity", "report_asin_granularity",
    ):
        assert column not in run_columns


# 4: downgrade refuses when a sales_and_traffic_report run exists — 0013's
# schema has no way to represent it, so downgrading would silently discard
# real ingestion evidence.
def test_downgrade_0014_to_0013_refuses_when_sales_traffic_run_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0014_sales_traffic_foundation")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    run_id = uuid4()
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs "
                "(id, organization_id, seller_account_id, marketplace_participation_id, connection_id, run_type, "
                "domain, region, environment, status) "
                "VALUES (:id, :org_id, :seller_account_id, :participation_id, :connection_id, "
                "'sales_and_traffic_report', 'sales_and_traffic_report', 'na', 'PRODUCTION', 'succeeded')"
            ),
            {
                "id": run_id, "org_id": org_id, "seller_account_id": seller_account_id,
                "participation_id": participation_id, "connection_id": connection_id,
            },
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0013_orders_durable_pagination")

    inspector = inspect(disposable_engine)
    assert "amazon_sales_traffic_daily_facts" in set(inspector.get_table_names())
    with disposable_engine.connect() as conn:
        still_there = conn.execute(
            text(
                "SELECT count(*) FROM amazon_ingestion_runs "
                "WHERE id = :id AND run_type = 'sales_and_traffic_report'"
            ),
            {"id": run_id},
        ).scalar()
    assert still_there == 1


# 5: downgrade also refuses when only fact-table rows exist (no covering
# run row survives, e.g. a pruned/archived run) — the fact rows themselves
# are what 0013 truly cannot represent.
def test_downgrade_0014_to_0013_refuses_when_daily_fact_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0014_sales_traffic_foundation")

    _org_id, _seller_account_id, _connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_sales_traffic_daily_facts "
                "(id, marketplace_participation_id, report_date, date_granularity) "
                "VALUES (gen_random_uuid(), :pid, '2026-08-01', 'DAY')"
            ),
            {"pid": participation_id},
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0013_orders_durable_pagination")

    inspector = inspect(disposable_engine)
    assert "amazon_sales_traffic_daily_facts" in set(inspector.get_table_names())


# 6: active Sales and Traffic run uniqueness under REAL PostgreSQL — the
# single-participation partial unique index, covering queued/started/
# waiting_to_retry together.
def test_active_sales_traffic_run_uniqueness_enforced_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    from datetime import date

    with Session(disposable_engine) as session:
        first = AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=connection_id, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1),
            date_granularity="DAY", asin_granularity="SKU",
        )
        session.commit()
    assert first.claimed is True

    with Session(disposable_engine) as session:
        second = AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=connection_id, data_start_time=date(2026, 8, 2), data_end_time=date(2026, 8, 2),
            date_granularity="DAY", asin_granularity="SKU",
        )
        session.commit()
    assert second.claimed is False
    assert second.reason == "already_running"


# 7 (money precision, real-Postgres-only proof): excessive magnitude (more
# than 19 total digits) is rejected by real PostgreSQL's NUMERIC(19,4) with
# `numeric_field_overflow` — SQLite cannot prove this (see the identical
# reasoning in test_disposable_postgres_orders_migration.py).
def test_excessive_magnitude_rejected_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    _org_id, _seller_account_id, _connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        with pytest.raises(DataError):
            conn.execute(
                text(
                    "INSERT INTO amazon_sales_traffic_daily_facts "
                    "(id, marketplace_participation_id, report_date, date_granularity, ordered_product_sales_amount) "
                    "VALUES (gen_random_uuid(), :pid, '2026-08-01', 'DAY', :amount)"
                ),
                {"pid": participation_id, "amount": Decimal("9999999999999999.9999")},
            )


# 8: the pinned contract's own unbounded-above field
# (unit_session_percentage) is genuinely accepted past 100 by the real
# database, not just by SQLite's identical CHECK-constraint evaluation.
def test_unit_session_percentage_over_100_accepted_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    _org_id, _seller_account_id, _connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_sales_traffic_daily_facts "
                "(id, marketplace_participation_id, report_date, date_granularity, unit_session_percentage) "
                "VALUES (gen_random_uuid(), :pid, '2026-08-01', 'DAY', 300.00)"
            ),
            {"pid": participation_id},
        )
    with disposable_engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT unit_session_percentage FROM amazon_sales_traffic_daily_facts "
                "WHERE marketplace_participation_id = :pid"
            ),
            {"pid": participation_id},
        ).scalar()
    assert stored == Decimal("300.0000")


# 9: foreign-participation provenance rejected by the real composite FK
# targeting `(amazon_ingestion_runs.id, amazon_ingestion_runs.
# marketplace_participation_id)` — a fact row's `last_ingestion_run_id`
# must belong to a run scoped to the *same* participation as the fact
# row itself, exactly mirroring `test_disposable_postgres_orders_
# migration.py`'s own `test_order_provenance_must_reference_a_run_that_
# covered_its_participation_on_real_postgres`.
def test_daily_fact_provenance_must_reference_a_run_scoped_to_its_own_participation_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_a = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with Session(disposable_engine) as session:
        participation_b = AmazonMarketplaceParticipation(
            id=uuid4(), organization_id=org_id, seller_account_id=seller_account_id,
            connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2", region="na",
        )
        session.add(participation_b)
        session.commit()
        participation_b_id = participation_b.id

    with Session(disposable_engine) as session:
        outcome = AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=org_id, seller_account_id=seller_account_id, marketplace_participation_id=participation_a,
            region="na", environment="PRODUCTION", connection_id=connection_id,
            data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1),
            date_granularity="DAY", asin_granularity="SKU",
        )
        session.commit()
        run_id = outcome.run_id

    # A fact row claiming to belong to participation_b, but whose
    # last_ingestion_run_id actually covered participation_a — the
    # composite FK must reject this combination even though `run_id`
    # alone genuinely exists.
    with Session(disposable_engine) as session:
        session.add(
            AmazonSalesAndTrafficDailyFact(
                marketplace_participation_id=participation_b_id, report_date=date(2026, 8, 1),
                date_granularity="DAY", last_ingestion_run_id=run_id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    # The same run_id against its own, genuinely-covered participation
    # must succeed — proving the constraint is scoped correctly, not
    # merely always-failing.
    with Session(disposable_engine) as session:
        session.add(
            AmazonSalesAndTrafficDailyFact(
                marketplace_participation_id=participation_a, report_date=date(2026, 8, 1),
                date_granularity="DAY", last_ingestion_run_id=run_id,
            )
        )
        session.commit()


# 10: concurrent enqueue for the same participation under REAL
# PostgreSQL has exactly one winner — the partial unique index
# (`uq_amazon_ingestion_runs_active_sales_traffic_scope`) is what
# actually enforces the single-writer guarantee under genuine
# concurrency, not merely the sequential proof in test 6 above.
@dataclass
class _EnqueueAttemptOutcome:
    label: str
    claimed: bool
    reason: str | None


def _enqueue_attempt(
    *, engine, org_id, seller_account_id, participation_id, connection_id, label, barrier, outcomes, errors, lock
):
    try:
        barrier.wait(timeout=10)
        with Session(engine) as session:
            outcome = AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
                organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
                connection_id=connection_id, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1),
                date_granularity="DAY", asin_granularity="SKU",
            )
            session.commit()
        with lock:
            outcomes.append(_EnqueueAttemptOutcome(label=label, claimed=outcome.claimed, reason=outcome.reason))
    except Exception as exc:  # noqa: BLE001 - see the listings concurrency suite for why.
        with lock:
            errors.append(exc)


def test_concurrent_enqueue_for_the_same_participation_has_exactly_one_winner_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    barrier = threading.Barrier(2)
    outcomes: list[_EnqueueAttemptOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_enqueue_attempt,
            kwargs=dict(
                engine=disposable_engine, org_id=org_id, seller_account_id=seller_account_id,
                participation_id=participation_id, connection_id=connection_id, label=label,
                barrier=barrier, outcomes=outcomes, errors=errors, lock=lock,
            ),
        )
        for label in ("attempt-a", "attempt-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == [], errors
    assert len(outcomes) == 2
    winners = [o for o in outcomes if o.claimed]
    losers = [o for o in outcomes if not o.claimed]
    assert len(winners) == 1, outcomes
    assert len(losers) == 1
    assert losers[0].reason == "already_running"

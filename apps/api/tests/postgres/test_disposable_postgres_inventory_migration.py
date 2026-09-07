"""12B.6B — Disposable PostgreSQL validation for migration 0015 (FBA
Inventory schema foundation).

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Mirrors `test_disposable_postgres_sales_traffic_
migration.py`'s own conventions exactly, including its migration-boundary
rule: a test intentionally pinned below `head` must never instantiate or
query the *current* ORM model for a table 0015 changed (`AmazonIngestionRun`
gained the `'inventory'` run_type plus two new constraints) — use raw SQL
restricted to the columns that genuinely existed at the pinned revision
instead, and only use the current ORM once the database has actually
upgraded past 0015.

No SP-API client, worker, read API, or UI code is exercised here — schema-
level proof only, plus the repository write paths that only real PostgreSQL
can genuinely prove (partial unique index enforcement, CHECK boundary,
composite FK provenance, advisory-lock concurrency). Full ORM/reflection
drift parity at `head` is already proven generically by
`test_disposable_postgres_orders_migration.py::test_empty_postgres_upgrade_
matches_orm_metadata_exactly_after_0012` — not duplicated here.

Never prints the disposable URL, table contents, or any credential.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import Session

from app.amazon.inventory_normalization import NormalizedInventoryObservation
from app.core.config import get_settings
from app.persistence.models import (
    AmazonConnection,
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    AmazonSellerInventoryObservation,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository, AmazonSellerInventoryRepository
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
        session.add(Organization(id=org_id, name="12B.6B Postgres Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id, organization_id=org_id, selling_partner_id="A12B6BPOSTGRES1", status="active"
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


def _observation(*, seller_sku="SYN-SKU-1", condition="NewItem", total_quantity=10, fulfillable_quantity=8):
    return NormalizedInventoryObservation(
        seller_sku=seller_sku, condition=condition, asin=None, fnsku=None, product_name=None,
        total_quantity=total_quantity, fulfillable_quantity=fulfillable_quantity,
        inbound_working_quantity=None, inbound_shipped_quantity=None, inbound_receiving_quantity=None,
        reserved_total_quantity=None, reserved_pending_customer_order_quantity=None,
        reserved_pending_transshipment_quantity=None, reserved_fc_processing_quantity=None,
        unfulfillable_total_quantity=None, unfulfillable_customer_damaged_quantity=None,
        unfulfillable_warehouse_damaged_quantity=None, unfulfillable_distributor_damaged_quantity=None,
        unfulfillable_carrier_damaged_quantity=None, unfulfillable_defective_quantity=None,
        unfulfillable_expired_quantity=None, researching_total_quantity=None,
        researching_quantity_short_term=None, researching_quantity_mid_term=None,
        researching_quantity_long_term=None, amazon_last_updated_time=None,
    )


# 1: existing pre-0015 database upgrades to 0015 preserving data, with the
# widened run_type CHECK now accepting 'inventory' for new rows while a
# pre-existing non-inventory row is untouched.
def test_existing_0014_database_upgrades_to_0015_preserving_data(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0014_sales_traffic_foundation")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    run_id = uuid4()
    # Raw SQL restricted to columns that genuinely exist at 0014 — NOT the
    # current AmazonIngestionRun ORM. See this file's own module docstring.
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
        command.upgrade(cfg, "0015_inventory_foundation")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == "0015_inventory_foundation"

    with disposable_engine.connect() as conn:
        row = conn.execute(
            text("SELECT run_type, status FROM amazon_ingestion_runs WHERE id = :id"), {"id": run_id}
        ).mappings().first()
    assert row is not None
    assert row["run_type"] == "listings"
    assert row["status"] == "succeeded"

    # A brand-new 'inventory' row is now accepted by the widened CHECK.
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs ("
                "id, organization_id, seller_account_id, marketplace_participation_id, connection_id, "
                "run_type, domain, region, environment, status"
                ") VALUES ("
                "gen_random_uuid(), :organization_id, :seller_account_id, :participation_id, :connection_id, "
                "'inventory', 'inventory', 'na', 'PRODUCTION', 'queued'"
                ")"
            ),
            {
                "organization_id": org_id, "seller_account_id": seller_account_id,
                "participation_id": participation_id, "connection_id": connection_id,
            },
        )

    # Now — and only now, after the upgrade to 0015 — the current ORM is
    # safe to use.
    with Session(disposable_engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        assert run is not None
        assert run.run_type == "listings"


# 2: expected new tables/columns/constraints/indexes exist after 0015.
def test_empty_postgres_upgrade_produces_expected_inventory_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_inventory_foundation")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_seller_inventory" in tables
    assert "amazon_seller_inventory_observations" in tables

    run_checks = {c["name"] for c in inspector.get_check_constraints("amazon_ingestion_runs")}
    assert "ck_amazon_ingestion_runs_inventory_scope_required" in run_checks

    run_indexes = {ix["name"] for ix in inspector.get_indexes("amazon_ingestion_runs")}
    assert "uq_amazon_ingestion_runs_active_inventory_scope" in run_indexes

    inventory_uniques = {uq["name"] for uq in inspector.get_unique_constraints("amazon_seller_inventory")}
    assert "uq_amazon_seller_inventory_participation_sku_condition" in inventory_uniques

    inventory_checks = {c["name"] for c in inspector.get_check_constraints("amazon_seller_inventory")}
    assert "ck_amazon_seller_inventory_quantities_nonneg" in inventory_checks

    observation_uniques = {
        uq["name"] for uq in inspector.get_unique_constraints("amazon_seller_inventory_observations")
    }
    assert "uq_amazon_seller_inventory_observations_run_identity" in observation_uniques

    observation_checks = {
        c["name"] for c in inspector.get_check_constraints("amazon_seller_inventory_observations")
    }
    assert "ck_amazon_seller_inventory_obs_quantities_nonneg" in observation_checks


# 3: downgrade is clean when no Inventory data exists at all.
def test_downgrade_0015_to_0014_is_clean_when_no_inventory_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_inventory_foundation")
        command.downgrade(cfg, "0014_sales_traffic_foundation")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_seller_inventory" not in tables
    assert "amazon_seller_inventory_observations" not in tables

    run_checks = {c["name"] for c in inspector.get_check_constraints("amazon_ingestion_runs")}
    assert "ck_amazon_ingestion_runs_inventory_scope_required" not in run_checks


# 4: downgrade refuses when an inventory run exists — 0014's schema has no
# way to represent it.
def test_downgrade_0015_to_0014_refuses_when_inventory_run_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_inventory_foundation")

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
                "'inventory', 'inventory', 'na', 'PRODUCTION', 'succeeded')"
            ),
            {
                "id": run_id, "org_id": org_id, "seller_account_id": seller_account_id,
                "participation_id": participation_id, "connection_id": connection_id,
            },
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0014_sales_traffic_foundation")

    inspector = inspect(disposable_engine)
    assert "amazon_seller_inventory" in set(inspector.get_table_names())
    with disposable_engine.connect() as conn:
        still_there = conn.execute(
            text("SELECT count(*) FROM amazon_ingestion_runs WHERE id = :id AND run_type = 'inventory'"),
            {"id": run_id},
        ).scalar()
    assert still_there == 1


# 5: downgrade also refuses when only current-state inventory rows exist
# (no covering run row survives, e.g. a pruned/archived run).
def test_downgrade_0015_to_0014_refuses_when_current_state_inventory_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_inventory_foundation")

    _org_id, _seller_account_id, _connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO amazon_seller_inventory "
                "(id, marketplace_participation_id, seller_sku, condition) "
                "VALUES (gen_random_uuid(), :pid, 'SYN-SKU-1', 'NewItem')"
            ),
            {"pid": participation_id},
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0014_sales_traffic_foundation")

    inspector = inspect(disposable_engine)
    assert "amazon_seller_inventory" in set(inspector.get_table_names())


# 6: downgrade also refuses when only immutable observation rows exist.
def test_downgrade_0015_to_0014_refuses_when_observation_data_exists(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0015_inventory_foundation")

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
                "'inventory', 'inventory', 'na', 'PRODUCTION', 'succeeded')"
            ),
            {
                "id": run_id, "org_id": org_id, "seller_account_id": seller_account_id,
                "participation_id": participation_id, "connection_id": connection_id,
            },
        )
        conn.execute(
            text(
                "INSERT INTO amazon_seller_inventory_observations "
                "(id, marketplace_participation_id, ingestion_run_id, seller_sku, condition) "
                "VALUES (gen_random_uuid(), :pid, :run_id, 'SYN-SKU-1', 'NewItem')"
            ),
            {"pid": participation_id, "run_id": run_id},
        )

    with _alembic_environment(url):
        with pytest.raises(Exception):
            command.downgrade(cfg, "0014_sales_traffic_foundation")

    inspector = inspect(disposable_engine)
    assert "amazon_seller_inventory_observations" in set(inspector.get_table_names())


# 7: active Inventory run uniqueness under REAL PostgreSQL — the
# single-scope partial unique index, covering queued/started/
# waiting_to_retry together.
def test_active_inventory_run_uniqueness_enforced_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with Session(disposable_engine) as session:
        first = AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=connection_id,
        )
        session.commit()
    assert first.claimed is True

    with Session(disposable_engine) as session:
        second = AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=connection_id,
        )
        session.commit()
    assert second.claimed is False
    assert second.reason == "already_running"


# 8 (real-Postgres-only proof): a negative quantity bypassing the
# application-layer rejection (e.g. a hand-crafted row) is still rejected
# by the combined non-negative CHECK — SQLite evaluates the identical
# CHECK expression, but this proves it under the real engine, not just an
# equivalent one.
def test_negative_quantity_rejected_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    _org_id, _seller_account_id, _connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with disposable_engine.begin() as conn:
        with pytest.raises((DataError, IntegrityError)):
            conn.execute(
                text(
                    "INSERT INTO amazon_seller_inventory "
                    "(id, marketplace_participation_id, seller_sku, condition, total_quantity) "
                    "VALUES (gen_random_uuid(), :pid, 'SYN-SKU-1', 'NewItem', -1)"
                ),
                {"pid": participation_id},
            )


# 9: foreign-participation provenance rejected by the real composite FK
# targeting `(amazon_ingestion_runs.id, amazon_ingestion_runs.
# marketplace_participation_id)` — an observation row's `ingestion_run_id`
# must belong to a run scoped to the *same* participation as the
# observation row itself.
def test_observation_provenance_must_reference_a_run_scoped_to_its_own_participation_on_real_postgres(
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
        outcome = AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=org_id, seller_account_id=seller_account_id, marketplace_participation_id=participation_a,
            region="na", environment="PRODUCTION", connection_id=connection_id,
        )
        session.commit()
        run_id = outcome.run_id

    # A row claiming to belong to participation_b, but whose
    # ingestion_run_id actually covered participation_a — the composite
    # FK must reject this combination even though `run_id` alone
    # genuinely exists.
    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerInventoryObservation(
                marketplace_participation_id=participation_b_id, ingestion_run_id=run_id,
                seller_sku="SYN-SKU-1", condition="NewItem",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    # The same run_id against its own, genuinely-covered participation
    # must succeed — proving the constraint is scoped correctly, not
    # merely always-failing.
    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerInventoryObservation(
                marketplace_participation_id=participation_a, ingestion_run_id=run_id,
                seller_sku="SYN-SKU-1", condition="NewItem",
            )
        )
        session.commit()


# 10: the corrected snapshot-grain design under REAL PostgreSQL — two
# distinct successful runs the same calendar day each produce their own
# observation row for the identical (participation, SKU, condition); a
# retried reconcile attempt for the exact SAME run is a silent no-op
# rather than a duplicate row, enforced by the real unique constraint
# (`uq_amazon_seller_inventory_observations_run_identity`), not merely
# application logic.
def test_same_day_multiple_successful_observations_are_never_collapsed_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )

    # Inserted directly as terminal ('succeeded') rows via raw SQL rather
    # than `enqueue_inventory_run` — two *simultaneously active* runs for
    # the same scope would collide with the partial unique index proven
    # in test 7 above; two independently-completed runs the same day is
    # exactly the scenario this test is proving, so each is inserted
    # already-terminal. `reconcile_snapshot` only needs a valid `(id,
    # marketplace_participation_id)` row to satisfy the observation
    # table's composite FK; it never inspects run status itself (that
    # decision belongs to the ingestion service, per this repository
    # method's own docstring).
    def _run_id() -> UUID:
        run_id = uuid4()
        with disposable_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO amazon_ingestion_runs ("
                    "id, organization_id, seller_account_id, marketplace_participation_id, connection_id, "
                    "run_type, domain, region, environment, status"
                    ") VALUES ("
                    ":id, :org_id, :seller_account_id, :participation_id, :connection_id, "
                    "'inventory', 'inventory', 'na', 'PRODUCTION', 'succeeded'"
                    ")"
                ),
                {
                    "id": run_id, "org_id": org_id, "seller_account_id": seller_account_id,
                    "participation_id": participation_id, "connection_id": connection_id,
                },
            )
        return run_id

    run_a = _run_id()
    run_b = _run_id()

    with Session(disposable_engine) as session:
        repo = AmazonSellerInventoryRepository(session)
        repo.reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            observations=[_observation(total_quantity=10)], ingestion_run_id=run_a,
        )
        session.commit()
    with Session(disposable_engine) as session:
        repo = AmazonSellerInventoryRepository(session)
        repo.reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            observations=[_observation(total_quantity=12)], ingestion_run_id=run_b,
        )
        session.commit()

    with disposable_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT ingestion_run_id, total_quantity FROM amazon_seller_inventory_observations "
                "WHERE marketplace_participation_id = :pid ORDER BY total_quantity"
            ),
            {"pid": participation_id},
        ).all()
    assert len(rows) == 2
    assert {r.ingestion_run_id for r in rows} == {run_a, run_b}
    assert [r.total_quantity for r in rows] == [10, 12]

    # A retried reconcile for run_a itself never inserts a duplicate row.
    with Session(disposable_engine) as session:
        repo = AmazonSellerInventoryRepository(session)
        repo.reconcile_snapshot(
            organization_id=org_id, marketplace_participation_id=participation_id,
            observations=[_observation(total_quantity=10)], ingestion_run_id=run_a,
        )
        session.commit()
    with disposable_engine.connect() as conn:
        count = conn.execute(
            text(
                "SELECT count(*) FROM amazon_seller_inventory_observations "
                "WHERE marketplace_participation_id = :pid AND ingestion_run_id = :run_id"
            ),
            {"pid": participation_id, "run_id": run_a},
        ).scalar()
    assert count == 1

    # Current-state row reflects the most recent reconcile only.
    with disposable_engine.connect() as conn:
        stored = conn.execute(
            text(
                "SELECT total_quantity FROM amazon_seller_inventory "
                "WHERE marketplace_participation_id = :pid AND seller_sku = 'SYN-SKU-1'"
            ),
            {"pid": participation_id},
        ).scalar()
    assert stored == 10


# 11: concurrent enqueue for the same scope under REAL PostgreSQL has
# exactly one winner — the partial unique index
# (`uq_amazon_ingestion_runs_active_inventory_scope`) is what actually
# enforces the single-writer guarantee under genuine concurrency, not
# merely the sequential proof in test 7 above.
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
            outcome = AmazonIngestionRunRepository(session).enqueue_inventory_run(
                organization_id=org_id, seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
                connection_id=connection_id,
            )
            session.commit()
        with lock:
            outcomes.append(_EnqueueAttemptOutcome(label=label, claimed=outcome.claimed, reason=outcome.reason))
    except Exception as exc:  # noqa: BLE001 - see the listings concurrency suite for why.
        with lock:
            errors.append(exc)


def test_concurrent_enqueue_for_the_same_scope_has_exactly_one_winner_on_real_postgres(disposable_engine) -> None:
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


# 12: concurrent claims of a single queued job have exactly one winner —
# proves `claim_next_inventory_job`'s `SELECT ... FOR UPDATE SKIP LOCKED`
# plus its dedicated advisory-lock key under genuine PostgreSQL
# concurrency, not merely SQLite's single-connection semantics.
@dataclass
class _ClaimAttemptOutcome:
    label: str
    claimed: bool


def _claim_attempt(*, engine, label, barrier, outcomes, errors, lock):
    try:
        barrier.wait(timeout=10)
        with Session(engine) as session:
            claimed_run = AmazonIngestionRunRepository(session).claim_next_inventory_job(
                lease_owner=label, lease_duration_seconds=300, max_global_active=4,
                max_active_per_organization=1,
            )
            session.commit()
        with lock:
            outcomes.append(_ClaimAttemptOutcome(label=label, claimed=claimed_run is not None))
    except Exception as exc:  # noqa: BLE001 - see the listings concurrency suite for why.
        with lock:
            errors.append(exc)


def test_concurrent_claims_of_a_single_queued_inventory_job_have_exactly_one_winner_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, connection_id, participation_id = _seed_org_seller_connection_and_participation(
        disposable_engine
    )
    with Session(disposable_engine) as session:
        AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=connection_id,
        )
        session.commit()

    barrier = threading.Barrier(2)
    outcomes: list[_ClaimAttemptOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_claim_attempt,
            kwargs=dict(engine=disposable_engine, label=label, barrier=barrier, outcomes=outcomes, errors=errors, lock=lock),
        )
        for label in ("worker-a", "worker-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == [], errors
    assert len(outcomes) == 2
    winners = [o for o in outcomes if o.claimed]
    assert len(winners) == 1, outcomes

    with Session(disposable_engine) as session:
        rows = (
            session.query(AmazonIngestionRun)
            .filter_by(seller_account_id=seller_account_id, marketplace_participation_id=participation_id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "started"

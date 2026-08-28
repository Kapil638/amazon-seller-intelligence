"""12B.3B — Disposable PostgreSQL validation for migration 0010.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Written and statically reasoned through
carefully (mirroring `test_disposable_postgres_deployment.py`), but could
not be executed end-to-end in the authoring environment (no Docker, no
local PostgreSQL binary available). Whoever runs this with a real
disposable Postgres instance should treat a first run as the actual proof,
not this file's existence.

No SP-API client, reconciliation service, read API, or UI code is exercised
here — schema-level proof only.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    AmazonSellerListing,
    Base,
    Organization,
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
    """See the identical helper in `test_disposable_postgres_deployment.py`
    for why this is required: `migrations/env.py` always re-resolves the URL
    from `DATABASE_URL`, which `tests/conftest.py` pins to `sqlite://` for
    the whole tree."""
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


def _seed_org_seller_account_and_participation(engine):
    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="12B.3B Postgres Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id,
                organization_id=org_id,
                selling_partner_id="A12B3BPOSTGRES1",
                status="active",
            )
        )
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_id="ATVPDKIKX0DER",
                region="na",
            )
        )
        session.commit()
    return org_id, seller_account_id, participation_id


# 1: fresh empty PostgreSQL upgrades cleanly to 0010, single head.
def test_empty_postgres_upgrades_cleanly_to_0010(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == "0010_amazon_seller_listings"


# 2: expected new table/columns/constraints/FKs exist.
def test_empty_postgres_upgrade_produces_expected_seller_listings_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_seller_listings" in tables

    listing_columns = {c["name"] for c in inspector.get_columns("amazon_seller_listings")}
    assert {
        "id",
        "marketplace_participation_id",
        "seller_sku",
        "asin",
        "status",
        "is_buyable",
        "is_discoverable",
        "offers",
        "price_amount",
        "price_currency",
        "fulfillment_availability",
        "issues",
        "issue_count",
        "highest_issue_severity",
        "product_types",
        "is_active",
        "last_ingestion_run_id",
    }.issubset(listing_columns)
    # No organization_id/seller_account_id columns — ownership is derived
    # solely through marketplace_participation_id (12B.3B design decision).
    assert "organization_id" not in listing_columns
    assert "seller_account_id" not in listing_columns

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ingestion_runs")}
    assert {
        "run_type",
        "marketplace_participation_id",
        "pages_fetched",
        "reported_total_results",
        "lease_owner",
        "lease_expires_at",
    }.issubset(run_columns)

    listing_uniques = {uq["name"] for uq in inspector.get_unique_constraints("amazon_seller_listings")}
    assert "uq_amazon_seller_listings_participation_sku" in listing_uniques

    listing_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("amazon_seller_listings")}
    participation_fk = next(
        fk for fk in listing_fks.values() if fk["referred_table"] == "amazon_marketplace_participations"
    )
    assert participation_fk["options"].get("ondelete") == "RESTRICT"

    # The provenance-integrity composite FK: last_ingestion_run_id and
    # marketplace_participation_id together must reference the SAME pair
    # on amazon_ingestion_runs — this is what makes a cross-participation
    # last_ingestion_run_id structurally impossible.
    provenance_fk = listing_fks["fk_amazon_seller_listings_last_ingestion_run_same_participation"]
    assert provenance_fk["referred_table"] == "amazon_ingestion_runs"
    assert set(provenance_fk["constrained_columns"]) == {"last_ingestion_run_id", "marketplace_participation_id"}
    assert set(provenance_fk["referred_columns"]) == {"id", "marketplace_participation_id"}
    assert provenance_fk["options"].get("ondelete") == "RESTRICT"

    run_fks = {fk["name"]: fk for fk in inspector.get_foreign_keys("amazon_ingestion_runs")}
    run_participation_fk = run_fks["fk_amazon_ingestion_runs_marketplace_participation_id"]
    assert run_participation_fk["referred_table"] == "amazon_marketplace_participations"
    assert run_participation_fk["options"].get("ondelete") == "RESTRICT"

    run_uniques = {uq["name"] for uq in inspector.get_unique_constraints("amazon_ingestion_runs")}
    assert "uq_amazon_ingestion_runs_id_marketplace_participation" in run_uniques

    run_indexes = {ix["name"] for ix in inspector.get_indexes("amazon_ingestion_runs")}
    assert "uq_amazon_ingestion_runs_active_listings_scope" in run_indexes


# 3: full drift parity after 0010, the same class of check already proven
# for 0009 in test_disposable_postgres_deployment.py.
def test_empty_postgres_upgrade_matches_orm_metadata_exactly_after_0010(disposable_engine) -> None:
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


# 4: downgrade 0010 -> 0009 removes only what 0010 added.
def test_downgrade_0010_to_0009_is_clean(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0009_amazon_seller_identity")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_seller_listings" not in tables
    assert "amazon_marketplace_participations" in tables
    assert "amazon_seller_accounts" in tables

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ingestion_runs")}
    assert "run_type" not in run_columns
    assert "marketplace_participation_id" not in run_columns
    assert "pages_fetched" not in run_columns
    assert "reported_total_results" not in run_columns
    assert "lease_owner" not in run_columns
    assert "lease_expires_at" not in run_columns


# 5-6: an existing 0009 database upgrades to 0010 preserving existing rows
# and deterministically backfilling run_type via server_default (no
# separate UPDATE statement — the whole point of the design).
def test_existing_0009_database_upgrades_to_0010_preserving_data_and_backfilling(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0009_amazon_seller_identity")

    org_id = uuid4()
    run_id = uuid4()
    with disposable_engine.begin() as conn:
        conn.execute(
            text("INSERT INTO organizations (id, name) VALUES (:id, :name)"),
            {"id": org_id, "name": "Pre-0010 Ingestion Run Org"},
        )
        # Exactly the 0009-era amazon_ingestion_runs column set — proves the
        # backfill applies to a row shaped before run_type ever existed.
        conn.execute(
            text(
                "INSERT INTO amazon_ingestion_runs "
                "(id, organization_id, domain, region, environment, status) "
                "VALUES (:id, :org_id, :domain, :region, :environment, :status)"
            ),
            {
                "id": run_id,
                "org_id": org_id,
                "domain": "amazon.com",
                "region": "na",
                "environment": "PRODUCTION",
                "status": "succeeded",
            },
        )

    with _alembic_environment(url):
        command.upgrade(cfg, "0010_amazon_seller_listings")

    with disposable_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT run_type, pages_fetched, reported_total_results, "
                "lease_owner, lease_expires_at, marketplace_participation_id, domain, status "
                "FROM amazon_ingestion_runs WHERE id = :id"
            ),
            {"id": run_id},
        ).mappings().first()

    assert row is not None
    assert row["run_type"] == "marketplace_participations"
    assert row["pages_fetched"] == 0
    assert row["reported_total_results"] is None
    assert row["lease_owner"] is None
    assert row["lease_expires_at"] is None
    assert row["marketplace_participation_id"] is None
    # Untouched original data.
    assert row["domain"] == "amazon.com"
    assert row["status"] == "succeeded"


# 7-8: the single-writer guarantee under REAL PostgreSQL — the partial
# unique index is the actual claim mechanism, proven here against real
# constraint enforcement, not just SQLite's (also proven, separately, in
# tests/test_amazon_seller_listings_schema.py).
def test_active_listings_run_uniqueness_enforced_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(
        disposable_engine
    )

    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    # Completing the first run frees the scope — never permanently locked.
    with Session(disposable_engine) as session:
        first = session.query(AmazonIngestionRun).filter_by(
            seller_account_id=seller_account_id, marketplace_participation_id=participation_id
        ).one()
        first.status = "succeeded"
        session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()  # must not raise


# 9: the seller-listings natural key is enforced by real Postgres.
def test_seller_listing_natural_key_uniqueness_enforced_by_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    _, _, participation_id = _seed_org_seller_account_and_participation(disposable_engine)

    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="PG-TEST-SKU-1",
                status=["BUYABLE"],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_id,
                seller_sku="PG-TEST-SKU-1",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


# 10: real-Postgres referential-action integrity review finding — deleting a
# marketplace participation referenced by a 'listings' ingestion run must be
# RESTRICTed, not silently SET NULL (which would only surface later as a
# confusing CHECK-constraint violation on ck_amazon_ingestion_runs_listings_
# scope_required). Unlike SQLite, Postgres enforces FKs unconditionally, so
# this is real proof beyond the pragma-enabled SQLite equivalent in
# tests/test_amazon_seller_listings_schema.py.
def test_deleting_a_referenced_marketplace_participation_is_restricted_by_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, participation_id = _seed_org_seller_account_and_participation(
        disposable_engine
    )

    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="started",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        participation = session.get(AmazonMarketplaceParticipation, participation_id)
        session.delete(participation)
        with pytest.raises(IntegrityError):
            session.commit()


# 11: real-Postgres provenance-integrity review finding — a listing's
# last_ingestion_run_id cannot reference a run scoped to a DIFFERENT
# marketplace participation. Enforced by the composite FK
# (last_ingestion_run_id, marketplace_participation_id) ->
# amazon_ingestion_runs(id, marketplace_participation_id).
def test_last_ingestion_run_must_share_marketplace_participation_on_real_postgres(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, seller_account_id, participation_a = _seed_org_seller_account_and_participation(
        disposable_engine
    )
    participation_b = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_id="A1F83G8C2ARO7P",
                region="eu",
            )
        )
        session.commit()

    run_for_a = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                id=run_for_a,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_a,
                run_type="listings",
                domain="amazon.com",
                region="na",
                environment="PRODUCTION",
                status="succeeded",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_b,
                seller_sku="PG-CROSS-PARTICIPATION",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
                last_ingestion_run_id=run_for_a,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()

    with Session(disposable_engine) as session:
        session.add(
            AmazonSellerListing(
                marketplace_participation_id=participation_a,
                seller_sku="PG-SAME-PARTICIPATION",
                status=[],
                offers=[],
                fulfillment_availability=[],
                issues=[],
                product_types=[],
                last_ingestion_run_id=run_for_a,
            )
        )
        session.commit()  # must not raise

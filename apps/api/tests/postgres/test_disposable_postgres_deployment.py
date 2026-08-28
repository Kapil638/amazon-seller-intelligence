"""12B.2A.1 — Disposable PostgreSQL deployment and concurrency validation.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs (`ASI_ALLOW_DISPOSABLE_POSTGRES=1` and a
`POSTGRES_DISPOSABLE_TEST_URL` that does not resemble the configured
application database). In the environment this suite was authored in,
neither Docker nor a local PostgreSQL binary was available, so these tests
were written and statically reasoned through carefully (reusing the same
patterns already proven correct in `tests/test_amazon_connection_claim_
concurrency.py` and `tests/test_amazon_seller_identity_schema.py`), but
could not themselves be executed end-to-end here. Whoever runs this with a
real disposable Postgres instance should treat a first run as the actual
proof, not this file's existence.

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
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import AmazonConnection, Base, Organization
from app.persistence.repositories import AmazonConnectionRepository
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
    """`migrations/env.py` always overrides `Config.sqlalchemy.url` with
    whatever `DATABASE_URL` currently resolves to via
    `app.persistence.database.sqlalchemy_database_url()` — including the
    `DATABASE_URL = "sqlite://"` that `apps/api/tests/conftest.py` sets at
    collection time for the *entire* `tests/` tree, this file included.
    Without this, every `alembic.command.*` call below would silently run
    against SQLite instead of the disposable PostgreSQL instance, regardless
    of what is set directly on the `Config` object — exactly the gotcha
    already solved this same way in `tests/test_amazon_seller_identity_
    schema.py`.
    """
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
        # Refuse to proceed if the target is not genuinely empty — this
        # suite must never run against a database anyone depends on.
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
        # Always drop everything this suite created, regardless of outcome —
        # leave the disposable instance clean for the next run.
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


# 1-2-3: empty PostgreSQL -> alembic upgrade to the pinned 0009 revision ->
# revision is exactly 0009. Deliberately pinned, not "head": this test's
# stated purpose is the 0009 boundary specifically (12B.2A), which must stay
# provable in isolation regardless of how many later migrations (0010+)
# become head. A fresh database's upgrade to the CURRENT head is proven
# separately and is expected to keep advancing — see
# test_disposable_postgres_seller_listings_migration.py::
# test_empty_postgres_upgrades_cleanly_to_0010 (12B.3B) — and the repo-wide
# single-head invariant is proven by
# test_amazon_seller_identity_schema.py::test_alembic_has_a_single_head.
def test_empty_postgres_upgrades_cleanly_to_0009(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "0009_amazon_seller_identity")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    assert current == "0009_amazon_seller_identity"


# 4: expected tables, indexes, constraints, and foreign keys exist.
def test_empty_postgres_upgrade_produces_expected_schema(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    # Spot-check across the whole chain, not just 12B.2A's own tables — this
    # is real evidence the repaired 0001 and every later revision coexist.
    for expected in (
        "organizations",
        "product_snapshots",
        "analysis_runs",
        "listing_analysis_results",
        "scoring_profiles",
        "copilot_conversations",
        "profit_models",
        "advertising_models",
        "amazon_connections",
        "amazon_oauth_states",
        "amazon_seller_accounts",
        "amazon_marketplace_participations",
        "amazon_ingestion_runs",
    ):
        assert expected in tables, f"missing table: {expected}"

    # Columns 0002/0003 add to 0001-owned tables must be present exactly once.
    analysis_runs_columns = {c["name"] for c in inspector.get_columns("analysis_runs")}
    assert "deleted_at" in analysis_runs_columns
    listing_columns = {c["name"] for c in inspector.get_columns("listing_analysis_results")}
    assert {"custom_listing_quality_score", "scoring_profile_snapshot"}.issubset(listing_columns)
    generated_reports_columns = {c["name"] for c in inspector.get_columns("generated_reports")}
    assert "template_version" in generated_reports_columns

    seller_account_uniques = {
        uq["name"] for uq in inspector.get_unique_constraints("amazon_seller_accounts")
    }
    assert "uq_amazon_seller_accounts_selling_partner_id" in seller_account_uniques

    participation_uniques = {
        uq["name"] for uq in inspector.get_unique_constraints("amazon_marketplace_participations")
    }
    assert "uq_amazon_marketplace_participations_seller_marketplace" in participation_uniques

    fk_tables_with_organizations_fk = [
        "product_snapshots",
        "analysis_runs",
        "amazon_connections",
        "amazon_seller_accounts",
    ]
    for table_name in fk_tables_with_organizations_fk:
        fks = inspector.get_foreign_keys(table_name)
        assert any(fk["referred_table"] == "organizations" for fk in fks), table_name


# 5: Alembic/model drift check against the REAL, reflected PostgreSQL schema
# (a live counterpart to the static offline check in
# tests/test_migration_chain_matches_orm_metadata.py).
def test_empty_postgres_upgrade_matches_orm_metadata_exactly(disposable_engine) -> None:
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


# 6: downgrade behavior matches the approved (forward-only-below-0001,
# ordinary single-step above it) policy.
def test_downgrade_0009_to_0008_is_clean(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0008_amazon_oauth_states")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_seller_accounts" not in tables
    assert "amazon_marketplace_participations" not in tables
    assert "amazon_ingestion_runs" not in tables
    assert "amazon_connections" in tables
    assert "amazon_oauth_states" in tables


# 7-8: a database representing 0008 upgrades to 0009; existing rows and data
# are unchanged by that step (0009 only adds new, empty tables).
def test_existing_0008_database_upgrades_to_0009_preserving_data(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        # Starting from empty, this runs the full 0001-0008 chain. 0001's
        # upgrade() deterministically bootstraps one default-organization row
        # (see migrations/versions/0001_m10_persistence.py) whenever it runs
        # online against a database that doesn't already have it — which is
        # exactly this case. The pre-upgrade organization count below is
        # therefore that bootstrap row plus this test's own fixture row, not
        # the fixture row alone.
        command.upgrade(cfg, "0008_amazon_oauth_states")

    org_id = uuid4()
    org_name = "Disposable Postgres Test Org"
    with Session(disposable_engine) as session:
        session.add(Organization(id=org_id, name=org_name))
        session.add(
            AmazonConnection(
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                region="eu",
                status="pending_authorization",
            )
        )
        session.commit()

    with disposable_engine.connect() as conn:
        connections_before = conn.execute(text("SELECT COUNT(*) FROM amazon_connections")).scalar()
        organizations_before = conn.execute(text("SELECT COUNT(*) FROM organizations")).scalar()
        # Expect the 0001 default-organization bootstrap row plus this test's
        # own fixture row — asserted explicitly so a future change to either
        # fails loudly here instead of silently invalidating the assumption
        # this test's invariant is built on.
        assert organizations_before == 2, (
            "expected 0001's default-organization bootstrap row plus this "
            f"test's own fixture row; got {organizations_before}"
        )
        preserved_name_before = conn.execute(
            text("SELECT name FROM organizations WHERE id = :id"), {"id": org_id}
        ).scalar()
        assert preserved_name_before == org_name

    with _alembic_environment(url):
        command.upgrade(cfg, "0009_amazon_seller_identity")

    with disposable_engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        connections_after = conn.execute(text("SELECT COUNT(*) FROM amazon_connections")).scalar()
        organizations_after = conn.execute(text("SELECT COUNT(*) FROM organizations")).scalar()
        seller_accounts_after = conn.execute(text("SELECT COUNT(*) FROM amazon_seller_accounts")).scalar()
        preserved_name_after = conn.execute(
            text("SELECT name FROM organizations WHERE id = :id"), {"id": org_id}
        ).scalar()

    assert current == "0009_amazon_seller_identity"
    assert connections_after == connections_before == 1
    # The real invariant: the upgrade must not create, delete, or duplicate
    # any row — not that the count equals a specific hard-coded number.
    assert organizations_after == organizations_before
    # Identity/content, not just aggregate count: the exact fixture row must
    # still exist, unmodified, by its own id.
    assert preserved_name_after == preserved_name_before == org_name
    assert seller_accounts_after == 0


@dataclass
class _ClaimOutcome:
    selling_partner_id: str
    claimed: bool


def _seed_connection(engine, *, selling_partner_id: str | None = None) -> tuple[UUID, UUID]:
    org_id = uuid4()
    connection_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="Concurrency Test Org"))
        session.add(
            AmazonConnection(
                id=connection_id,
                organization_id=org_id,
                provider="SP_API",
                environment="SANDBOX",
                region="eu",
                status="pending_authorization",
                selling_partner_id=selling_partner_id,
            )
        )
        session.commit()
    return org_id, connection_id


def _claim(
    *,
    engine,
    organization_id: UUID,
    connection_id: UUID,
    selling_partner_id: str,
    barrier: threading.Barrier,
    outcomes: list[_ClaimOutcome],
    lock: threading.Lock,
) -> None:
    barrier.wait()
    with Session(engine) as session:
        claimed = AmazonConnectionRepository(session).claim_identity_for_authorization(
            organization_id, connection_id, selling_partner_id=selling_partner_id
        )
        session.commit()
    with lock:
        outcomes.append(_ClaimOutcome(selling_partner_id=selling_partner_id, claimed=claimed))


# 9-10: the atomic seller-identity claim under REAL PostgreSQL concurrency —
# the exact invariant tests/test_amazon_connection_claim_concurrency.py
# already proves on SQLite, run here against real Postgres row-level write
# serialization instead of SQLite's coarser file locking.
def test_concurrent_claims_with_different_identifiers_use_real_postgres_serialization(
    disposable_engine,
) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    for _ in range(10):
        with disposable_engine.begin() as conn:
            conn.execute(text("TRUNCATE amazon_connections, organizations CASCADE"))
        org_id, connection_id = _seed_connection(disposable_engine)
        barrier = threading.Barrier(2)
        outcomes: list[_ClaimOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_claim,
                kwargs=dict(
                    engine=disposable_engine,
                    organization_id=org_id,
                    connection_id=connection_id,
                    selling_partner_id=spid,
                    barrier=barrier,
                    outcomes=outcomes,
                    lock=lock,
                ),
            )
            for spid in ("PGRACESELLERA01", "PGRACESELLERB02")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2
        winners = [outcome for outcome in outcomes if outcome.claimed]
        losers = [outcome for outcome in outcomes if not outcome.claimed]
        assert len(winners) == 1, outcomes
        assert len(losers) == 1

        with Session(disposable_engine) as session:
            row = AmazonConnectionRepository(session).get_by_id(org_id, connection_id)
            assert row is not None
            assert row.selling_partner_id == winners[0].selling_partner_id


# 11: missing/invalid identities still fail closed against real PostgreSQL —
# the repository-level guarantee added in 12B.2A's fail-closed remediation.
def test_claim_rejects_missing_identity_against_real_postgres(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, connection_id = _seed_connection(disposable_engine)
    with Session(disposable_engine) as session:
        with pytest.raises(TypeError):
            AmazonConnectionRepository(session).claim_identity_for_authorization(
                org_id, connection_id, selling_partner_id=None
            )
        with pytest.raises(TypeError):
            AmazonConnectionRepository(session).claim_identity_for_authorization(
                org_id, connection_id, selling_partner_id="   "
            )


# 12: no token or seller identifier is logged during the concurrency scenario.
def test_concurrent_claim_logs_contain_no_secret_material(disposable_engine, caplog) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    org_id, connection_id = _seed_connection(disposable_engine)
    barrier = threading.Barrier(2)
    outcomes: list[_ClaimOutcome] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_claim,
            kwargs=dict(
                engine=disposable_engine,
                organization_id=org_id,
                connection_id=connection_id,
                selling_partner_id=spid,
                barrier=barrier,
                outcomes=outcomes,
                lock=lock,
            ),
        )
        for spid in ("LOGCHECKSELLERA", "LOGCHECKSELLERB")
    ]
    with caplog.at_level("DEBUG"):
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    assert "LOGCHECKSELLERA" not in combined_log
    assert "LOGCHECKSELLERB" not in combined_log
    assert "Atzr|" not in combined_log
    assert "Atza|" not in combined_log
    assert os.environ.get("POSTGRES_DISPOSABLE_TEST_URL", "") not in combined_log

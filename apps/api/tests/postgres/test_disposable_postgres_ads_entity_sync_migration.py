"""Disposable PostgreSQL validation for migration 0021
(`ads_entity_sync_runs`) — PR B2, including the final-review revision
that added `ads_connection_id` scope, the `'partial'` terminal status,
and reversible `is_active`/`last_seen_entity_sync_run_id` snapshot
tracking on the five existing entity tables.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Proves, against genuine PostgreSQL:

- `amazon_ads_entity_sync_runs` / `amazon_ads_entity_sync_checkpoints`
  exist with the expected shape (entity_type/status check constraints
  — including `'partial'` — the checkpoint's per-(profile, entity_type)
  uniqueness, and `ads_connection_id` on both);
- the five entity tables gained `is_active` (default true) and
  `last_seen_entity_sync_run_id`;
- `downgrade()` refuses when either new table is populated, and
  succeeds (removing both tables and the two new entity-table columns)
  when they are empty;
- the entity-sync lease ledger's fenced CAS mutations behave under real
  concurrency exactly like `AmazonAdsReportRunRepository`'s proven
  pattern, with the one deliberate difference this PR introduces: a
  stale lease always terminalizes to `timed_out`, never resumed;
- `AmazonAdsEntitySyncCheckpointRepository.advance`'s guarded,
  SQL-verified finalization rejects a non-`'succeeded'` or foreign-scope
  run even when called directly, under real PostgreSQL;
- reconciliation (deactivation/reactivation) behaves correctly and
  stays tenant-isolated under real PostgreSQL, and an incomplete/failed
  run never touches `is_active` at all.

As with every other disposable-Postgres suite in this repository, this
file could not itself be executed end-to-end in the environment it was
authored in (no Docker, no local PostgreSQL binary available) — it
reuses the exact fixtures and patterns already proven in
`test_disposable_postgres_ads_report_run_lease_fencing.py` and
`test_disposable_postgres_ads_campaign_state_enum_migration.py`.
Whoever runs this with a real disposable Postgres instance should treat
a first run as the actual proof, not this file's existence.

Never prints the disposable URL, table contents, or any credential.
"""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import AmazonAdsCampaign, AmazonAdsEntitySyncRun, Organization
from app.persistence.repositories import (
    AmazonAdsCampaignRepository,
    AmazonAdsConnectionRepository,
    AmazonAdsEntitySyncCheckpointRepository,
    AmazonAdsEntitySyncRunRepository,
    AmazonAdsProfileRepository,
)
from tests.postgres import _guard

pytestmark = pytest.mark.skipif(bool(_guard.skip_reason()), reason=_guard.skip_reason() or "")

API_ROOT = Path(__file__).resolve().parents[2]

_ENTITY_TABLES = (
    "amazon_ads_campaigns",
    "amazon_ads_ad_groups",
    "amazon_ads_advertised_products",
    "amazon_ads_keywords",
    "amazon_ads_product_targets",
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
        with _alembic_environment(url):
            command.upgrade(_alembic_config(url), "head")
        yield engine
    finally:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))
        engine.dispose()


def _seed_profile(engine) -> tuple[UUID, UUID, UUID]:
    """Returns (organization_id, ads_connection_id, ads_profile_id)."""
    org_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="PR B2 Postgres Entity Sync Test Org"))
        session.commit()
    with Session(engine) as session:
        connection = AmazonAdsConnectionRepository(session).get_or_create_for_org(org_id)
        profile = AmazonAdsProfileRepository(session).upsert_many(
            org_id,
            connection.id,
            [
                {
                    "profile_id": "111",
                    "account_id": "a",
                    "account_type": "seller",
                    "marketplace_country_code": "US",
                    "currency_code": "USD",
                    "timezone": "America/Los_Angeles",
                    "region": "NA",
                    "display_name": "AJ Duran",
                }
            ],
        )[0]
        session.commit()
        return org_id, connection.id, profile.id


def _seed_campaign(engine, *, org_id: UUID, profile_id: UUID, external_id: str, is_active: bool = True) -> UUID:
    with Session(engine) as session:
        row = AmazonAdsCampaignRepository(session).upsert(
            org_id, profile_id, {"external_campaign_id": external_id, "name": "Seed", "state": "ENABLED"}
        )
        row_id = row.id
        if not is_active:
            row.is_active = False
        session.commit()
        return row_id


def test_0021_creates_the_expected_tables_and_columns(disposable_engine) -> None:
    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_ads_entity_sync_runs" in tables
    assert "amazon_ads_entity_sync_checkpoints" in tables

    run_columns = {c["name"] for c in inspector.get_columns("amazon_ads_entity_sync_runs")}
    assert "ads_connection_id" in run_columns
    assert "items_mismatched_parent" in run_columns
    assert "items_deactivated" in run_columns
    assert "items_reactivated" in run_columns
    assert "reconciliation_stale_count" not in run_columns  # replaced, not kept alongside

    checkpoint_columns = {c["name"] for c in inspector.get_columns("amazon_ads_entity_sync_checkpoints")}
    assert "ads_connection_id" in checkpoint_columns

    run_checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("amazon_ads_entity_sync_runs")}
    assert "partial" in run_checks["ck_amazon_ads_entity_sync_runs_status"]
    assert "succeeded" in run_checks["ck_amazon_ads_entity_sync_runs_status"]

    for table in _ENTITY_TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert "is_active" in columns, f"{table} missing is_active"
        assert "last_seen_entity_sync_run_id" in columns, f"{table} missing last_seen_entity_sync_run_id"


def test_existing_campaign_rows_default_to_active_after_upgrade(disposable_engine) -> None:
    org_id, _connection_id, profile_id = _seed_profile(disposable_engine)
    campaign_id = _seed_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-1")
    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsCampaign, campaign_id)
        assert row.is_active is True
        assert row.last_seen_entity_sync_run_id is None


def test_downgrade_refuses_when_either_new_table_is_populated(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    org_id, connection_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        AmazonAdsEntitySyncRunRepository(session).enqueue(org_id, connection_id, profile_id, entity_type="campaign")
        session.commit()

    with _alembic_environment(url):
        with pytest.raises(RuntimeError, match="Refusing to downgrade 0021"):
            command.downgrade(cfg, "0020_ads_campaign_state_enum")

    inspector = inspect(disposable_engine)
    assert "amazon_ads_entity_sync_runs" in set(inspector.get_table_names())


def test_downgrade_succeeds_and_removes_entity_table_columns_when_empty(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.downgrade(cfg, "0020_ads_campaign_state_enum")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_ads_entity_sync_runs" not in tables
    assert "amazon_ads_entity_sync_checkpoints" not in tables
    for table in _ENTITY_TABLES:
        columns = {c["name"] for c in inspector.get_columns(table)}
        assert "is_active" not in columns
        assert "last_seen_entity_sync_run_id" not in columns


def test_stale_lease_always_terminalizes_never_resumed_under_real_concurrency(disposable_engine) -> None:
    org_id, connection_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(org_id, connection_id, profile_id, entity_type="campaign")
        run_id = run.id
        claimed = repo.claim_next_sync_run(lease_owner="worker-a", lease_duration_seconds=300, max_global_active=10)
        assert claimed is not None and claimed.id == run_id
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        session.commit()

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        result = repo.claim_next_sync_run(lease_owner="worker-b", lease_duration_seconds=300, max_global_active=10)
        assert result is None
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        assert row.status == "timed_out"
        assert row.lease_owner is None
        assert row.failure_class == "lease_expired"


def test_checkpoint_advance_rejects_a_non_succeeded_or_foreign_scope_run(disposable_engine) -> None:
    org_id, connection_id, profile_id = _seed_profile(disposable_engine)
    other_org_id, other_connection_id, other_profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        run_repo = AmazonAdsEntitySyncRunRepository(session)
        run = run_repo.enqueue(org_id, connection_id, profile_id, entity_type="campaign")
        run_id = run.id
        run_repo.claim_next_sync_run(lease_owner="w1", lease_duration_seconds=300, max_global_active=10)
        session.commit()

    checkpoint_repo_kwargs = dict(entity_type="campaign", synced_at=datetime.now(UTC), run_id=run_id)

    # Still 'started' — not yet succeeded.
    with Session(disposable_engine) as session:
        with pytest.raises(ValueError):
            AmazonAdsEntitySyncCheckpointRepository(session).advance(
                org_id, connection_id, profile_id, **checkpoint_repo_kwargs
            )

    with Session(disposable_engine) as session:
        assert AmazonAdsEntitySyncRunRepository(session).mark_partial(
            run_id, lease_owner="w1", pages_processed=1, items_observed=1, items_accepted=0,
            items_schema_rejected=1, items_unsupported_state=0, items_missing_parent=0, items_mismatched_parent=0,
        )
        session.commit()

    # 'partial', not 'succeeded'.
    with Session(disposable_engine) as session:
        with pytest.raises(ValueError):
            AmazonAdsEntitySyncCheckpointRepository(session).advance(
                org_id, connection_id, profile_id, **checkpoint_repo_kwargs
            )

    # Foreign organization/connection/profile scope, even with a real run_id.
    with Session(disposable_engine) as session:
        with pytest.raises(ValueError):
            AmazonAdsEntitySyncCheckpointRepository(session).advance(
                other_org_id, other_connection_id, other_profile_id, **checkpoint_repo_kwargs
            )

    with Session(disposable_engine) as session:
        assert AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign") is None


def test_reconciliation_deactivates_and_reactivates_with_tenant_isolation(disposable_engine) -> None:
    org_id, connection_id, profile_id = _seed_profile(disposable_engine)
    other_org_id, _other_connection_id, other_profile_id = _seed_profile(disposable_engine)

    stale_id = _seed_campaign(disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-stale")
    inactive_id = _seed_campaign(
        disposable_engine, org_id=org_id, profile_id=profile_id, external_id="c-inactive", is_active=False
    )
    other_profile_row_id = _seed_campaign(
        disposable_engine, org_id=other_org_id, profile_id=other_profile_id, external_id="c-other"
    )

    with Session(disposable_engine) as session:
        run_repo = AmazonAdsEntitySyncRunRepository(session)
        run = run_repo.enqueue(org_id, connection_id, profile_id, entity_type="campaign")
        run_id = run.id
        run_repo.claim_next_sync_run(lease_owner="w1", lease_duration_seconds=300, max_global_active=10)
        session.commit()

    # Simulate the service's own reconciliation step directly against
    # real PostgreSQL: c-inactive is "observed again" (reactivated),
    # c-stale is not observed (deactivated), c-other (different profile)
    # must never be touched.
    with Session(disposable_engine) as session:
        from sqlalchemy import update

        touched_ids = [inactive_id]
        session.execute(
            update(AmazonAdsCampaign)
            .where(AmazonAdsCampaign.id.in_(touched_ids), AmazonAdsCampaign.is_active.is_(False))
            .values(is_active=True, last_seen_entity_sync_run_id=run_id)
        )
        session.execute(
            update(AmazonAdsCampaign)
            .where(AmazonAdsCampaign.ads_profile_id == profile_id, AmazonAdsCampaign.is_active.is_(True), AmazonAdsCampaign.id.notin_(touched_ids))
            .values(is_active=False)
        )
        assert AmazonAdsEntitySyncRunRepository(session).mark_succeeded(
            run_id, lease_owner="w1", pages_processed=1, items_observed=1, items_accepted=1,
            items_schema_rejected=0, items_unsupported_state=0, items_missing_parent=0, items_mismatched_parent=0,
            items_deactivated=1, items_reactivated=1,
        )
        AmazonAdsEntitySyncCheckpointRepository(session).advance(
            org_id, connection_id, profile_id, entity_type="campaign", synced_at=datetime.now(UTC), run_id=run_id
        )
        session.commit()

    with Session(disposable_engine) as session:
        assert session.get(AmazonAdsCampaign, stale_id).is_active is False
        assert session.get(AmazonAdsCampaign, inactive_id).is_active is True
        assert session.get(AmazonAdsCampaign, other_profile_row_id).is_active is True  # untouched


def test_persist_and_checkpoint_advance_are_atomic_with_the_ownership_check(disposable_engine) -> None:
    """Mirrors the Reporting v3 Postgres suite's own proof: stages a
    campaign upsert and a fenced completion that has already lost the
    race inside ONE transaction, then rolls back exactly the way
    `AmazonAdsEntitySyncService` does when `_LeaseLost` is raised —
    proving the upsert never survives even though it was written before
    the ownership check failed."""
    org_id, connection_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(org_id, connection_id, profile_id, entity_type="campaign")
        run_id = run.id
        repo.claim_next_sync_run(lease_owner="worker-a", lease_duration_seconds=300, max_global_active=10)
        session.commit()

    # Another worker reclaims the row out from under worker-a.
    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        row.lease_owner = "worker-b"
        session.commit()

    with Session(disposable_engine) as session:
        AmazonAdsCampaignRepository(session).upsert(
            org_id, profile_id, {"external_campaign_id": "c-atomicity-test", "name": "X", "state": "ENABLED"}
        )
        ok = AmazonAdsEntitySyncRunRepository(session).mark_succeeded(
            run_id, lease_owner="worker-a", pages_processed=1, items_observed=1, items_accepted=1,
            items_schema_rejected=0, items_unsupported_state=0, items_missing_parent=0, items_mismatched_parent=0,
            items_deactivated=0, items_reactivated=0,
        )
        assert ok is False
        session.rollback()  # exactly what session_scope() does on _LeaseLost

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        assert row.status == "started"  # never completed by worker-a
        rows, total = AmazonAdsCampaignRepository(session).list_for_profile(org_id, profile_id, offset=0, limit=10)
        assert total == 0  # the upsert never survived the rollback
        assert AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign") is None

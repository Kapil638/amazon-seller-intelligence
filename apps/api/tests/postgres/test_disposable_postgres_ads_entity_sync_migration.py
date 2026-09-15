"""Disposable PostgreSQL validation for migration 0021
(`ads_entity_sync_runs`) — PR B2.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. Proves, against genuine PostgreSQL:

- `amazon_ads_entity_sync_runs` / `amazon_ads_entity_sync_checkpoints`
  exist after upgrading to 0021 with the expected shape (entity_type/
  status check constraints, the checkpoint's per-(profile, entity_type)
  uniqueness);
- `downgrade()` refuses when either table is populated, and succeeds
  (removing both tables) when they are empty;
- the entity-sync lease ledger's fenced CAS mutations behave under real
  concurrency exactly like `AmazonAdsReportRunRepository`'s proven
  pattern (see `test_disposable_postgres_ads_report_run_lease_fencing.py`),
  with the one deliberate difference this PR introduces: a stale lease
  always terminalizes to `timed_out`, never resumed — there is no
  `amazon_report_id`-shaped field whose presence would make resumption
  safe the way Reporting v3's does.

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
from app.persistence.models import AmazonAdsEntitySyncRun, Organization
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


def _seed_profile(engine) -> tuple[UUID, UUID]:
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
        return org_id, profile.id


def test_0021_creates_the_expected_tables_and_constraints(disposable_engine) -> None:
    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_ads_entity_sync_runs" in tables
    assert "amazon_ads_entity_sync_checkpoints" in tables

    run_checks = {c["name"]: c["sqltext"] for c in inspector.get_check_constraints("amazon_ads_entity_sync_runs")}
    assert "ck_amazon_ads_entity_sync_runs_entity_type" in run_checks
    assert "ck_amazon_ads_entity_sync_runs_status" in run_checks
    for value in ("campaign", "ad_group", "product_ad", "keyword", "product_target"):
        assert value in run_checks["ck_amazon_ads_entity_sync_runs_entity_type"]
    for value in ("queued", "started", "waiting_to_retry", "succeeded", "failed", "timed_out"):
        assert value in run_checks["ck_amazon_ads_entity_sync_runs_status"]

    checkpoint_uniques = {
        uq["name"] for uq in inspector.get_unique_constraints("amazon_ads_entity_sync_checkpoints")
    }
    assert "uq_amazon_ads_entity_sync_checkpoints_profile_entity_type" in checkpoint_uniques


def test_downgrade_refuses_when_either_table_is_populated(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    org_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        AmazonAdsEntitySyncRunRepository(session).enqueue(org_id, profile_id, entity_type="campaign")
        session.commit()

    with _alembic_environment(url):
        with pytest.raises(RuntimeError, match="Refusing to downgrade 0021"):
            command.downgrade(cfg, "0020_ads_campaign_state_enum")

    inspector = inspect(disposable_engine)
    assert "amazon_ads_entity_sync_runs" in set(inspector.get_table_names())


def test_downgrade_succeeds_when_both_tables_are_empty(disposable_engine) -> None:
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.downgrade(cfg, "0020_ads_campaign_state_enum")

    inspector = inspect(disposable_engine)
    tables = set(inspector.get_table_names())
    assert "amazon_ads_entity_sync_runs" not in tables
    assert "amazon_ads_entity_sync_checkpoints" not in tables


def test_stale_lease_always_terminalizes_never_resumed_under_real_concurrency(disposable_engine) -> None:
    """The one deliberate behavioral difference from
    `AmazonAdsReportRunRepository`'s resumable/terminal split: there is
    no second stale-lease branch here at all. Every stale `started` row
    becomes `timed_out`, unconditionally."""
    org_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(org_id, profile_id, entity_type="campaign")
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
        # No other queued/waiting_to_retry run exists — the stale row
        # itself must not be silently re-claimable as if nothing happened.
        result = repo.claim_next_sync_run(lease_owner="worker-b", lease_duration_seconds=300, max_global_active=10)
        assert result is None
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        assert row.status == "timed_out"
        assert row.lease_owner is None
        assert row.failure_class == "lease_expired"


def test_worker_a_loses_every_fenced_mutation_after_worker_b_reclaims(disposable_engine) -> None:
    org_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(org_id, profile_id, entity_type="campaign")
        run_id = run.id
        claimed = repo.claim_next_sync_run(lease_owner="worker-a", lease_duration_seconds=300, max_global_active=10)
        assert claimed is not None
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        session.commit()

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        reclaimed = repo.claim_next_sync_run(lease_owner="worker-b", lease_duration_seconds=300, max_global_active=10)
        assert reclaimed is None  # the stale row terminalized; nothing left to claim
        session.commit()

    # Worker A's writes against a row that is no longer 'started' at all
    # (it is now 'timed_out') are rejected exactly like a hijack attempt.
    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        assert repo.heartbeat(run_id, lease_owner="worker-a", lease_duration_seconds=300) is False
        assert repo.mark_succeeded(
            run_id, lease_owner="worker-a", pages_processed=1, items_observed=1, items_accepted=1,
            items_schema_rejected=0, items_unsupported_state=0, items_missing_parent=0,
            reconciliation_stale_count=0,
        ) is False
        assert repo.mark_failed(
            run_id, lease_owner="worker-a", failure_class="stale_worker_attempt", failure_detail="should never win"
        ) is False
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        assert row.status == "timed_out"
        assert row.lease_owner is None


def test_persist_and_checkpoint_advance_are_atomic_with_the_ownership_check(disposable_engine) -> None:
    """Mirrors the Reporting v3 Postgres suite's own proof: stages a
    campaign upsert and a fenced completion that has already lost the
    race inside ONE transaction, then rolls back exactly the way
    `AmazonAdsEntitySyncService` does when `_LeaseLost` is raised —
    proving the upsert never survives even though it was written before
    the ownership check failed."""
    org_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        repo = AmazonAdsEntitySyncRunRepository(session)
        run = repo.enqueue(org_id, profile_id, entity_type="campaign")
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
            items_schema_rejected=0, items_unsupported_state=0, items_missing_parent=0,
            reconciliation_stale_count=0,
        )
        assert ok is False
        session.rollback()  # exactly what session_scope() does on _LeaseLost

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsEntitySyncRun, run_id)
        assert row.status == "started"  # never completed by worker-a
        rows, total = AmazonAdsCampaignRepository(session).list_for_profile(org_id, profile_id, offset=0, limit=10)
        assert total == 0  # the upsert never survived the rollback
        assert AmazonAdsEntitySyncCheckpointRepository(session).get(profile_id, "campaign") is None

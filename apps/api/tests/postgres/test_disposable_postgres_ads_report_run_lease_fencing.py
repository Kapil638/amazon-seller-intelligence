"""PR A2 (second review) — Amazon Ads Reporting v3 report-run lease
fencing under real PostgreSQL concurrency.

Proves the scenario the review specifically asked for:

1. Worker A claims a report run.
2. A's lease expires (simulated by forcing lease_expires_at into the past).
3. Worker B reclaims it — the stale-lease recovery split resumes the same
   `amazon_report_id`, never re-creating.
4. Worker A attempts status, retry, failure, success, and report-id
   mutations against the row it no longer owns.
5. Every one of those stale mutations is rejected (returns False, writes
   nothing).
6. Only Worker B can complete the run and advance its checkpoint.

Also proves that fact persistence and checkpoint advancement are atomic
with the ownership check: a fenced completion that loses the race, when
rolled back the way `AmazonAdsReportService` rolls back on `_LeaseLost`,
leaves behind no facts and no checkpoint — even though the fact rows were
already written earlier in that same, still-uncommitted transaction.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. As with every other disposable-Postgres suite
in this repository, this file could not itself be executed end-to-end in
the environment it was authored in (no Docker, no local PostgreSQL binary
available) — it reuses the exact fixtures and patterns already proven in
`test_disposable_postgres_listings_run_claim_concurrency.py`. Whoever runs
this with a real disposable Postgres instance should treat a first run as
the actual proof, not this file's existence.

Never prints the disposable URL, table contents, or any credential.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.persistence.models import AmazonAdsReportRun, Organization
from app.persistence.repositories import (
    AmazonAdsConnectionRepository,
    AmazonAdsDailyPerformanceFactRepository,
    AmazonAdsProfileRepository,
    AmazonAdsReportRunRepository,
    AmazonAdsSyncCheckpointRepository,
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
    """Returns (organization_id, ads_profile_id)."""
    org_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="PR A2 Postgres Lease Fencing Test Org"))
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


def test_worker_a_loses_every_fenced_mutation_after_worker_b_reclaims(disposable_engine) -> None:
    org_id, profile_id = _seed_profile(disposable_engine)

    # 1. Worker A claims the report run.
    with Session(disposable_engine) as session:
        repo = AmazonAdsReportRunRepository(session)
        run = repo.create(
            org_id, profile_id, report_type="sponsored_products_daily",
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 1),
        )
        run_id = run.id  # captured before commit expires the ORM instance
        claimed = repo.claim_next_report_job(
            lease_owner="worker-a", lease_duration_seconds=300, max_global_active=10, max_active_per_profile=10
        )
        assert claimed is not None and claimed.id == run_id
        # Amazon already accepted the report before A crashed — the
        # scenario the resumable stale-lease split exists for.
        assert repo.set_amazon_report(
            run_id, lease_owner="worker-a", amazon_report_id="r-pg-fencing-1", amazon_report_status="PENDING"
        )
        session.commit()

    # 2. A's lease expires.
    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsReportRun, run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=5)
        session.commit()

    # 3. Worker B reclaims it — resumes the SAME amazon_report_id, never a
    # second create (this repository layer never calls create_report;
    # that guarantee lives in AmazonAdsReportService's own `if not
    # amazon_report_id` guard, proven at the SQLite/unit level — this
    # proves the precondition that guard depends on: the id survives the
    # reclaim intact).
    with Session(disposable_engine) as session:
        repo = AmazonAdsReportRunRepository(session)
        reclaimed = repo.claim_next_report_job(
            lease_owner="worker-b", lease_duration_seconds=300, max_global_active=10, max_active_per_profile=10
        )
        assert reclaimed is not None
        assert reclaimed.id == run_id
        assert reclaimed.amazon_report_id == "r-pg-fencing-1"
        assert reclaimed.status == "started"
        assert reclaimed.lease_owner == "worker-b"
        session.commit()

    # 4/5. Worker A attempts every fenced mutation — every one is rejected.
    with Session(disposable_engine) as session:
        repo = AmazonAdsReportRunRepository(session)
        assert repo.heartbeat(run_id, lease_owner="worker-a", lease_duration_seconds=300) is False
        assert repo.set_amazon_report(
            run_id, lease_owner="worker-a", amazon_report_id="r-hijack-attempt", amazon_report_status="PENDING"
        ) is False
        assert repo.update_amazon_status(run_id, lease_owner="worker-a", amazon_report_status="COMPLETED") is False
        assert repo.mark_retry(
            run_id, lease_owner="worker-a", next_retry_at=datetime.now(UTC) + timedelta(seconds=30),
            failure_class="stale_worker_attempt", failure_detail="worker-a should never win this",
        ) is False
        assert repo.mark_failed(
            run_id, lease_owner="worker-a", failure_class="stale_worker_attempt", failure_detail="worker-a should never win this"
        ) is False
        assert repo.mark_succeeded(run_id, lease_owner="worker-a", records_ingested=999) is False
        session.commit()

    # None of worker A's rejected mutations altered the row worker B owns.
    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsReportRun, run_id)
        assert row.lease_owner == "worker-b"
        assert row.status == "started"
        assert row.amazon_report_id == "r-pg-fencing-1"  # untouched by the "r-hijack-attempt" write
        assert row.records_ingested == 0
        assert row.failure_class is None

    # 6. Only Worker B can complete the run and advance its checkpoint.
    with Session(disposable_engine) as session:
        repo = AmazonAdsReportRunRepository(session)
        assert repo.mark_succeeded(run_id, lease_owner="worker-b", records_ingested=1) is True
        AmazonAdsSyncCheckpointRepository(session).advance(
            org_id, profile_id, synced_through_date=date(2026, 9, 1), report_run_id=run_id
        )
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsReportRun, run_id)
        assert row.status == "succeeded"
        assert row.records_ingested == 1
        assert row.lease_owner is None
        checkpoint = AmazonAdsSyncCheckpointRepository(session).get(profile_id)
        assert checkpoint is not None
        assert checkpoint.synced_through_date == date(2026, 9, 1)
        assert checkpoint.last_successful_report_run_id == run_id


def test_fact_persistence_and_completion_are_atomic_with_the_ownership_check(disposable_engine) -> None:
    """Mirrors the Listings suite's own 'a failed final transaction rolls
    back everything' proof: stages a fact upsert and a fenced completion
    that has already lost the race (another worker reclaimed the row)
    inside ONE transaction, then rolls back exactly the way
    AmazonAdsReportService does when `_LeaseLost` is raised — proving the
    fact never survives even though it was written before the ownership
    check failed."""
    org_id, profile_id = _seed_profile(disposable_engine)

    with Session(disposable_engine) as session:
        repo = AmazonAdsReportRunRepository(session)
        run = repo.create(
            org_id, profile_id, report_type="sponsored_products_daily",
            start_date=date(2026, 9, 1), end_date=date(2026, 9, 1),
        )
        run_id = run.id  # captured before commit expires the ORM instance
        repo.claim_next_report_job(
            lease_owner="worker-a", lease_duration_seconds=300, max_global_active=10, max_active_per_profile=10
        )
        session.commit()

    # Another worker reclaims the row out from under worker-a.
    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsReportRun, run_id)
        row.lease_owner = "worker-b"
        session.commit()

    # Worker A, unaware, proceeds to "finish" its ingest: writes a fact,
    # then attempts the fenced completion check — all in one transaction.
    with Session(disposable_engine) as session:
        AmazonAdsDailyPerformanceFactRepository(session).upsert(
            org_id, profile_id,
            {
                "entity_type": "campaign",
                "entity_external_id": "c-atomicity-test",
                "fact_date": date(2026, 9, 1),
                "attribution_window": "14d",
                "currency_code": "USD",
                "impressions": 100,
                "clicks": 10,
                "cost": Decimal("1.00"),
                "attributed_sales": Decimal("2.00"),
                "attributed_conversions": 1,
            },
            report_run_id=run_id,
        )
        ok = AmazonAdsReportRunRepository(session).mark_succeeded(run_id, lease_owner="worker-a", records_ingested=1)
        assert ok is False
        # This is exactly what session_scope()'s rollback-on-exception
        # does when AmazonAdsReportService raises _LeaseLost here.
        session.rollback()

    with Session(disposable_engine) as session:
        row = session.get(AmazonAdsReportRun, run_id)
        assert row.status == "started"  # never completed by worker-a
        assert row.records_ingested == 0
        facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
            org_id, profile_id, start=date(2026, 9, 1), end=date(2026, 9, 1)
        )
        assert facts == []  # the fact write never survived the rollback
        assert AmazonAdsSyncCheckpointRepository(session).get(profile_id) is None

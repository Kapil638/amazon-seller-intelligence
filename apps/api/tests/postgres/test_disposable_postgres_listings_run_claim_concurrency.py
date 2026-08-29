"""12B.3D — Listings-run atomic claim, stale recovery, and final
transactional reconciliation under real PostgreSQL concurrency.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. As with every other disposable-Postgres suite in
this repository, this file could not itself be executed end-to-end in the
environment it was authored in (no Docker, no local PostgreSQL binary
available) — it reuses the exact fixtures and patterns already proven in
`test_disposable_postgres_marketplace_reconciliation_concurrency.py` and
`test_disposable_postgres_seller_listings_migration.py`. Whoever runs this
with a real disposable Postgres instance should treat a first run as the
actual proof, not this file's existence.

Never prints the disposable URL, table contents, or any credential.
"""

from __future__ import annotations

import os
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.amazon.listings_normalization import NormalizedListing
from app.core.config import get_settings
from app.persistence.database import get_engine, reset_persistence
from app.persistence.models import (
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonSellerAccount,
    AmazonSellerListing,
    Organization,
)
from app.persistence.repositories import AmazonIngestionRunRepository, AmazonSellerListingRepository
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


def _seed_scope(engine) -> tuple[UUID, UUID, UUID]:
    org_id = uuid4()
    seller_account_id = uuid4()
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="12B.3D Postgres Claim Test Org"))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id,
                organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}",
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


@dataclass
class _ClaimAttemptOutcome:
    lease_owner: str
    claimed: bool
    reason: str | None


def _claim_attempt(
    *,
    engine,
    organization_id: UUID,
    seller_account_id: UUID,
    marketplace_participation_id: UUID,
    lease_owner: str,
    barrier: threading.Barrier,
    outcomes: list[_ClaimAttemptOutcome],
    errors: list[BaseException],
    lock: threading.Lock,
) -> None:
    try:
        barrier.wait()
        with Session(engine) as session:
            claim = AmazonIngestionRunRepository(session).claim_listings_run(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                region="na",
                environment="PRODUCTION",
                connection_id=None,
                lease_owner=lease_owner,
                lease_duration_seconds=300,
            )
            session.commit()
        with lock:
            outcomes.append(_ClaimAttemptOutcome(lease_owner=lease_owner, claimed=claim.claimed, reason=claim.reason))
    except Exception as exc:  # noqa: BLE001 - see the marketplace-reconciliation guarded suite for why.
        with lock:
            errors.append(exc)


# 1: concurrent claims for a fresh scope have exactly one winner.
def test_concurrent_claims_for_a_fresh_scope_have_exactly_one_winner(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    barrier = threading.Barrier(2)
    outcomes: list[_ClaimAttemptOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_claim_attempt,
            kwargs=dict(
                engine=disposable_engine,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                lease_owner=owner,
                barrier=barrier,
                outcomes=outcomes,
                errors=errors,
                lock=lock,
            ),
        )
        for owner in ("owner-a", "owner-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    assert len(outcomes) == 2
    winners = [o for o in outcomes if o.claimed]
    losers = [o for o in outcomes if not o.claimed]
    assert len(winners) == 1, outcomes
    assert len(losers) == 1
    assert losers[0].reason == "already_running"

    with Session(disposable_engine) as session:
        rows = (
            session.query(AmazonIngestionRun)
            .filter_by(seller_account_id=seller_account_id, marketplace_participation_id=participation_id)
            .all()
        )
        assert len(rows) == 1
        assert rows[0].status == "started"


# 2: concurrent claims against an EXPIRED stale run have exactly one winner
# that successfully reclaims the scope.
def test_concurrent_claims_against_an_expired_stale_run_have_exactly_one_winner(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    stale_run_id = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonIngestionRun(
                id=stale_run_id,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                run_type="listings",
                domain="listings_items",
                region="na",
                environment="PRODUCTION",
                status="started",
                lease_owner="crashed-worker",
                lease_expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()

    barrier = threading.Barrier(2)
    outcomes: list[_ClaimAttemptOutcome] = []
    errors: list[BaseException] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_claim_attempt,
            kwargs=dict(
                engine=disposable_engine,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                lease_owner=owner,
                barrier=barrier,
                outcomes=outcomes,
                errors=errors,
                lock=lock,
            ),
        )
        for owner in ("recovery-a", "recovery-b")
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert errors == []
    winners = [o for o in outcomes if o.claimed]
    assert len(winners) == 1, outcomes

    with Session(disposable_engine) as session:
        stale_row = session.get(AmazonIngestionRun, stale_run_id)
        assert stale_row.status == "timed_out"
        assert stale_row.lease_owner is None
        active_rows = (
            session.query(AmazonIngestionRun)
            .filter_by(
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                status="started",
            )
            .all()
        )
        assert len(active_rows) == 1
        assert active_rows[0].lease_owner == winners[0].lease_owner


# 3: a failure during the final reconciliation transaction rolls back
# EVERYTHING in that transaction under real Postgres, including the run's
# own status change — proven here with a genuine composite-FK violation
# (a listing whose last_ingestion_run_id belongs to a DIFFERENT
# marketplace participation), which only real Postgres enforces
# unconditionally (SQLite requires an explicit PRAGMA the application does
# not set).
def test_failed_final_transaction_rolls_back_run_completion_and_listing_writes(disposable_engine) -> None:
    org_id, seller_account_id, participation_a = _seed_scope(disposable_engine)
    participation_b = uuid4()
    with Session(disposable_engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_b,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                marketplace_id="A2EUQ1WTGCTBG2",
                region="eu",
            )
        )
        session.commit()

    with Session(disposable_engine) as session:
        run_for_b = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_b,
            region="eu",
            environment="PRODUCTION",
            connection_id=None,
            lease_owner="owner-b",
            lease_duration_seconds=300,
        )
        session.commit()
    assert run_for_b.claimed

    with pytest.raises(IntegrityError):
        with Session(disposable_engine) as session:
            runs = AmazonIngestionRunRepository(session)
            completed = runs.complete_listings_run(
                org_id, run_for_b.run_id, lease_owner="owner-b", status="succeeded",
                records_received=1, records_accepted=1, pages_fetched=1,
                reported_total_results=1, pagination_complete=True,
            )
            assert completed is True
            # Deliberately cross-scoped: this listing belongs to
            # participation_a, but claims provenance from a run scoped to
            # participation_b — violates the composite FK.
            AmazonSellerListingRepository(session).reconcile_snapshot(
                organization_id=org_id,
                marketplace_participation_id=participation_a,
                listings=[
                    NormalizedListing(
                        seller_sku="SKU-CROSS-SCOPE",
                        asin=None, product_type=None, condition_type=None, item_name=None,
                        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
                        status=[], is_buyable=False, is_discoverable=False, offers=[],
                        price_amount=None, price_currency=None, fulfillment_availability=[],
                        issues=[], issue_count=0, highest_issue_severity=None, product_types=[],
                    )
                ],
                last_ingestion_run_id=run_for_b.run_id,
            )
            session.commit()

    # The whole transaction rolled back: the run's completion (status flip
    # to 'succeeded') never took effect either, and no listing exists.
    with Session(disposable_engine) as session:
        run_row = session.get(AmazonIngestionRun, run_for_b.run_id)
        assert run_row.status == "started"  # unchanged — the completion was rolled back too
        assert (
            session.query(AmazonSellerListing).filter_by(seller_sku="SKU-CROSS-SCOPE").first() is None
        )


# 4: 12B.3D remediation — real lease expiry against real PostgreSQL: an
# expired lease fails closed even with no replacement worker, using the
# database's own clock rather than the test process's.
def test_heartbeat_and_completion_fail_once_lease_expires_with_no_replacement_worker(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(disposable_engine) as session:
        heartbeat_ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", lease_duration_seconds=300, pages_fetched=5,
        )
        session.commit()
    assert heartbeat_ok is False

    with Session(disposable_engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", status="succeeded",
        )
        session.commit()
    assert completed is False

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        assert row.status == "started"  # untouched by either failed compare-and-set


# 5: after a genuine concurrent reclaim, the original worker remains unable
# to heartbeat or complete against its now-terminalized run.
def test_original_worker_cannot_heartbeat_or_complete_after_concurrent_reclaim(disposable_engine) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        original = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="original-owner", lease_duration_seconds=300,
        )
        session.commit()

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, original.run_id)
        row.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

    with Session(disposable_engine) as session:
        reclaim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="new-owner", lease_duration_seconds=300,
        )
        session.commit()
    assert reclaim.claimed is True

    with Session(disposable_engine) as session:
        original_row = session.get(AmazonIngestionRun, original.run_id)
        assert original_row.status == "timed_out"
        assert original_row.failure_class == "lease_expired"
        assert original_row.pagination_complete is False

    with Session(disposable_engine) as session:
        heartbeat_ok = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, original.run_id, lease_owner="original-owner", lease_duration_seconds=300, pages_fetched=1,
        )
        session.commit()
    assert heartbeat_ok is False

    with Session(disposable_engine) as session:
        completed = AmazonIngestionRunRepository(session).complete_listings_run(
            org_id, original.run_id, lease_owner="original-owner", status="succeeded",
        )
        session.commit()
    assert completed is False


# 6/7: 12B.3D remediation — proving the *written* `lease_expires_at` is
# genuinely database-time-authoritative on real PostgreSQL, not merely the
# expiry *comparison*. A wildly skewed application clock is injected via
# monkeypatch; `_lease_expiry_value`'s PostgreSQL branch never reads
# `datetime.now(...)` at all (it is pure `func.now(type_=DateTime) +
# timedelta(...)`, compiled to `now() + make_interval(...)`), so the skew
# must have zero effect on what actually lands in the row. If either test
# ever recorded a lease anywhere near the skewed clock's value, that would
# prove the write path had silently regressed to depending on the
# application's own clock again.
def test_new_lease_expiry_on_claim_ignores_a_skewed_application_clock(disposable_engine, monkeypatch) -> None:
    import app.persistence.repositories as repositories_module

    class _SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(1999, 1, 1, tzinfo=tz)

    monkeypatch.setattr(repositories_module, "datetime", _SkewedDatetime)

    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    real_now_before = datetime.now(UTC)
    with Session(disposable_engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()
    real_now_after = datetime.now(UTC)
    assert claim.claimed is True

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        lease_expires_at = row.lease_expires_at

    # Nowhere near the skewed 1999 clock's `+300s` value.
    assert lease_expires_at.year > 2020
    # Within a generous window of the *real* wall clock's `+300s`, proving
    # the value came from the database's own clock, unaffected by the
    # monkeypatched application clock.
    assert real_now_before + timedelta(seconds=300) - timedelta(minutes=1) <= lease_expires_at
    assert lease_expires_at <= real_now_after + timedelta(seconds=300) + timedelta(minutes=1)


def test_heartbeat_renewal_ignores_a_skewed_application_clock(disposable_engine, monkeypatch) -> None:
    org_id, seller_account_id, participation_id = _seed_scope(disposable_engine)
    with Session(disposable_engine) as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=org_id, seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id, region="na", environment="PRODUCTION",
            connection_id=None, lease_owner="owner-1", lease_duration_seconds=300,
        )
        session.commit()

    import app.persistence.repositories as repositories_module

    class _SkewedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(1999, 1, 1, tzinfo=tz)

    monkeypatch.setattr(repositories_module, "datetime", _SkewedDatetime)

    real_now_before = datetime.now(UTC)
    with Session(disposable_engine) as session:
        renewed = AmazonIngestionRunRepository(session).heartbeat_listings_run(
            org_id, claim.run_id, lease_owner="owner-1", lease_duration_seconds=600, pages_fetched=1,
        )
        session.commit()
    real_now_after = datetime.now(UTC)
    assert renewed is True

    with Session(disposable_engine) as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        lease_expires_at = row.lease_expires_at

    assert lease_expires_at.year > 2020
    assert real_now_before + timedelta(seconds=600) - timedelta(minutes=1) <= lease_expires_at
    assert lease_expires_at <= real_now_after + timedelta(seconds=600) + timedelta(minutes=1)

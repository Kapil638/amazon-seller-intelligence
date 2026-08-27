"""12B.2B — Marketplace-participation reconciliation under real PostgreSQL concurrency.

Opt-in only. See `_guard.py` for the two conditions that must both hold
before anything here runs. As with the 12B.2A.1 disposable-Postgres suite,
this file could not itself be executed end-to-end in the environment it was
authored in (no Docker, no local PostgreSQL binary available) — it was
written and reasoned through carefully, reusing the same fixtures and
patterns already proven in `tests/postgres/test_disposable_postgres_
deployment.py`. Whoever runs this with a real disposable Postgres instance
should treat a first run as the actual proof, not this file's existence.

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

from app.amazon.marketplace_reconciliation import AmazonMarketplaceReconciliationService
from app.amazon.seller_validation import NormalizedMarketplaceParticipation
from app.core.config import get_settings
from app.persistence.database import get_engine, reset_persistence
from app.persistence.models import AmazonConnection, Organization
from app.persistence.repositories import AmazonSellerAccountRepository
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
    """See the identical helper in `test_disposable_postgres_deployment.py`:
    `migrations/env.py` always re-reads `DATABASE_URL`, so it must be
    overridden here even though the URL is also set on the `Config` object.
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


@contextmanager
def _global_engine_pointed_at(url: str):
    """Point the application's process-global session factory at `url`.

    `get_engine()` is a *zero-argument* `lru_cache` — setting `DATABASE_URL`
    and calling `get_settings.cache_clear()` alone (as `_alembic_environment`
    above does, correctly, for driving Alembic directly) has no effect on it.
    `AmazonMarketplaceReconciliationService.reconcile()` always goes through
    `session_scope()` → `get_engine()`, so without also clearing *that*
    cache it keeps resolving to whatever engine this test process warmed up
    first — `apps/api/tests/conftest.py`'s autouse fixture already calls
    `get_engine()` against `DATABASE_URL=sqlite://` before every test. Left
    unfixed, every reconciliation call in this file silently queries an
    empty, unrelated SQLite database instead of the disposable Postgres
    instance, and `AmazonConnection`/`AmazonSellerAccount` lookups return
    `None` — indistinguishable, from the caller's side, from a genuine
    cross-organization binding rejection.

    `reset_persistence()` clears both caches together (the same helper the
    conftest autouse fixture itself uses), so this is done once, from the
    main thread, before any worker thread starts — never from inside a
    worker, which would race the same process-global caches across threads
    for no benefit, since every thread wants the identical URL anyway. The
    resulting `Engine`/connection pool is safe to share across threads
    afterward, exactly as it is in the real running application.
    """
    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = url
    get_settings.cache_clear()
    reset_persistence()
    get_engine()
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        get_settings.cache_clear()
        reset_persistence()


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


def _seed_connection(engine, *, organization_id: UUID | None = None) -> tuple[UUID, UUID]:
    org_id = organization_id or uuid4()
    connection_id = uuid4()
    with Session(engine) as session:
        if session.get(Organization, org_id) is None:
            session.add(Organization(id=org_id, name="Reconciliation Concurrency Test Org"))
        session.add(
            AmazonConnection(
                id=connection_id,
                organization_id=org_id,
                provider="SP_API",
                environment="PRODUCTION",
                region="na",
                status="connected",
            )
        )
        session.commit()
    return org_id, connection_id


def _participation(marketplace_id: str) -> NormalizedMarketplaceParticipation:
    return NormalizedMarketplaceParticipation(
        marketplace_id=marketplace_id,
        name="Amazon.com",
        country_code="US",
        default_currency_code="USD",
        default_language_code="en_US",
        domain_name="www.amazon.com",
        store_name="ConcurrencyTestStore",
        is_participating=True,
        has_suspended_listings=False,
    )


@dataclass
class _ReconcileOutcome:
    organization_id: UUID
    succeeded: bool
    reason: str | None


def _reconcile(
    *,
    organization_id: UUID,
    connection_id: UUID,
    selling_partner_id: str,
    barrier: threading.Barrier,
    outcomes: list[_ReconcileOutcome],
    errors: list[BaseException],
    lock: threading.Lock,
) -> None:
    # A worker thread's exception otherwise vanishes silently — the thread
    # simply dies and `outcomes` stays short, which read as "the race never
    # happened" rather than surfacing the real failure. Capturing it here
    # means a future regression shows its actual exception, not just a
    # length mismatch.
    try:
        barrier.wait()
        service = AmazonMarketplaceReconciliationService()
        outcome = service.reconcile(
            organization_id=organization_id,
            connection_id=connection_id,
            region="na",
            environment="PRODUCTION",
            selling_partner_id=selling_partner_id,
            participations=[_participation("ATVPDKIKX0DER")],
        )
        with lock:
            outcomes.append(
                _ReconcileOutcome(
                    organization_id=organization_id, succeeded=outcome.succeeded, reason=outcome.reason
                )
            )
    except Exception as exc:  # noqa: BLE001 - deliberately broad, see docstring above.
        with lock:
            errors.append(exc)


def test_concurrent_same_seller_reconciliation_produces_no_duplicate_rows(disposable_engine) -> None:
    """Two overlapping reconcile() calls for the same seller must never
    produce two seller-account rows or two participation rows for the same
    marketplace — the read-then-upsert repository methods can race, and any
    loser must fail cleanly into `database_failure` rather than raising an
    unhandled exception or corrupting state.
    """
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    with _global_engine_pointed_at(url):
        org_id, connection_id = _seed_connection(disposable_engine)
        barrier = threading.Barrier(2)
        outcomes: list[_ReconcileOutcome] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_reconcile,
                kwargs=dict(
                    organization_id=org_id,
                    connection_id=connection_id,
                    selling_partner_id="PGRECONCILESELLER01",
                    barrier=barrier,
                    outcomes=outcomes,
                    errors=errors,
                    lock=lock,
                ),
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert not any(thread.is_alive() for thread in threads), "a worker thread did not finish in time"
        assert errors == [], f"worker thread(s) raised: {errors!r}"
        assert len(outcomes) == 2
        # Whichever thread(s) failed must have failed cleanly, never crashed the process.
        for outcome in outcomes:
            assert outcome.succeeded or outcome.reason == "database_failure"

        with Session(disposable_engine) as session:
            accounts = AmazonSellerAccountRepository(session).list_for_org(org_id)
            assert len(accounts) == 1
            rows = session.execute(
                text(
                    "SELECT marketplace_id FROM amazon_marketplace_participations "
                    "WHERE seller_account_id = :seller_account_id"
                ),
                {"seller_account_id": str(accounts[0].id)},
            ).fetchall()
            assert len(rows) == 1


def test_concurrent_cross_org_ownership_conflict_has_exactly_one_winner(disposable_engine) -> None:
    """Two different organizations racing to reconcile the same
    selling_partner_id must never both succeed — PostgreSQL's own unique
    constraint on `amazon_seller_accounts.selling_partner_id` serializes the
    two writers, and the loser must land on `ownership_conflict`, never
    disclosing the winner's organization id.
    """
    url = _guard.disposable_url()
    cfg = _alembic_config(url)
    with _alembic_environment(url):
        command.upgrade(cfg, "head")

    with _global_engine_pointed_at(url):
        org_a, connection_a = _seed_connection(disposable_engine)
        org_b, connection_b = _seed_connection(disposable_engine)
        barrier = threading.Barrier(2)
        outcomes: list[_ReconcileOutcome] = []
        errors: list[BaseException] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_reconcile,
                kwargs=dict(
                    organization_id=org,
                    connection_id=connection,
                    selling_partner_id="PGSHAREDSELLER01",
                    barrier=barrier,
                    outcomes=outcomes,
                    errors=errors,
                    lock=lock,
                ),
            )
            for org, connection in ((org_a, connection_a), (org_b, connection_b))
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert not any(thread.is_alive() for thread in threads), "a worker thread did not finish in time"
        assert errors == [], f"worker thread(s) raised: {errors!r}"
        assert len(outcomes) == 2
        winners = [outcome for outcome in outcomes if outcome.succeeded]
        losers = [outcome for outcome in outcomes if not outcome.succeeded]
        assert len(winners) == 1, outcomes
        assert len(losers) == 1
        assert losers[0].reason == "ownership_conflict"
        assert str(winners[0].organization_id) not in repr(losers[0])
        assert str(winners[0].organization_id) not in str(losers[0].reason)

        with Session(disposable_engine) as session:
            winner_accounts = AmazonSellerAccountRepository(session).list_for_org(winners[0].organization_id)
            loser_accounts = AmazonSellerAccountRepository(session).list_for_org(losers[0].organization_id)
            assert len(winner_accounts) == 1
            assert loser_accounts == []

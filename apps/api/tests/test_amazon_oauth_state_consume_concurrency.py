"""Final review gate for PR #28 — AmazonOAuthStateRepository.consume race
fix. The original implementation was read-then-write (`get_by_id`, check
`consumed_at is None`, set it, flush) with no atomic guard: two concurrent
callers racing the SAME `state` value (two browser tabs, a client retry, or
a captured/replayed callback URL while the state is still unexpired) could
both observe `consumed_at is None` and both "win," each then proceeding to
exchange the same authorization code with Amazon. `consume()` now uses a
single `UPDATE ... WHERE consumed_at IS NULL` (the same atomic-conditional-
UPDATE technique `AmazonConnectionRepository.claim_identity_for_authorization`
already uses, and the same style test_amazon_connection_claim_concurrency.py
already established for proving it).

Deterministic concurrency tests only — a `threading.Barrier` synchronizes
both threads to the same instant, then the database itself decides the
winner. No sleeps, no probabilistic retry loops. Each test builds its own
dedicated, file-based SQLite engine (not the shared in-memory StaticPool
the app fixture uses, which binds every "session" to one physical
connection and would not exercise genuine write-write contention) so the
two threads' UPDATEs genuinely contend for the same on-disk row.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.persistence.models import AmazonConnection, AmazonOAuthState, Base, Organization
from app.persistence.repositories import AmazonOAuthStateRepository

ITERATIONS = 20


@dataclass
class _ConsumeOutcome:
    label: str
    won: bool


def _dedicated_engine(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 15, "check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


def _seed_state(engine, *, expires_at: datetime | None = None) -> tuple[UUID, UUID]:
    org_id = uuid4()
    connection_id = uuid4()
    state_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name="Concurrency Test Org"))
        session.add(
            AmazonConnection(
                id=connection_id,
                organization_id=org_id,
                provider="SP_API",
                environment="PRODUCTION",
                region="na",
                status="pending_authorization",
            )
        )
        session.add(
            AmazonOAuthState(
                id=state_id,
                organization_id=org_id,
                provider="SP_API",
                environment="PRODUCTION",
                connection_id=connection_id,
                state_hash="a" * 64,
                expires_at=expires_at or (datetime.now(UTC) + timedelta(seconds=600)),
            )
        )
        session.commit()
    return org_id, state_id


def _consume(
    *,
    engine,
    organization_id: UUID,
    state_id: UUID,
    label: str,
    barrier: threading.Barrier,
    outcomes: list[_ConsumeOutcome],
    lock: threading.Lock,
) -> None:
    barrier.wait()
    with Session(engine) as session:
        result = AmazonOAuthStateRepository(session).consume(organization_id, state_id)
        session.commit()
    with lock:
        outcomes.append(_ConsumeOutcome(label=label, won=result is not None))


def _get_state(engine, organization_id: UUID, state_id: UUID) -> AmazonOAuthState | None:
    with Session(engine) as session:
        return AmazonOAuthStateRepository(session).get_by_id(organization_id, state_id)


def test_concurrent_consume_of_the_same_state_exactly_one_winner(tmp_path) -> None:
    """The core fix this test proves: racing the SAME state value never lets
    both callers proceed — exactly one wins, on every iteration,
    deterministically, under real cross-connection write contention."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"race_{i}")
        org_id, state_id = _seed_state(engine)
        barrier = threading.Barrier(2)
        outcomes: list[_ConsumeOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_consume,
                kwargs=dict(
                    engine=engine,
                    organization_id=org_id,
                    state_id=state_id,
                    label=label,
                    barrier=barrier,
                    outcomes=outcomes,
                    lock=lock,
                ),
            )
            for label in ("thread-a", "thread-b")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2, f"iteration {i}: a thread failed to complete: {outcomes}"
        winners = [outcome for outcome in outcomes if outcome.won]
        losers = [outcome for outcome in outcomes if not outcome.won]
        assert len(winners) == 1, f"iteration {i}: expected exactly one winner, got {outcomes}"
        assert len(losers) == 1

        row = _get_state(engine, org_id, state_id)
        assert row is not None
        assert row.consumed_at is not None
        engine.dispose()


def test_sequential_second_consume_of_an_already_consumed_state_loses(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "sequential")
    org_id, state_id = _seed_state(engine)
    with Session(engine) as session:
        repo = AmazonOAuthStateRepository(session)
        first = repo.consume(org_id, state_id)
        session.commit()
    with Session(engine) as session:
        repo = AmazonOAuthStateRepository(session)
        second = repo.consume(org_id, state_id)
        session.commit()
    assert first is not None
    assert second is None
    engine.dispose()


def test_consume_returns_none_for_an_unknown_state_id(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "unknown")
    org_id, _state_id = _seed_state(engine)
    with Session(engine) as session:
        result = AmazonOAuthStateRepository(session).consume(org_id, uuid4())
    assert result is None
    engine.dispose()


def test_consume_is_organization_scoped(tmp_path) -> None:
    """A state row belonging to organization A must never be consumable by
    passing organization B's id, even with the correct state_id."""
    engine = _dedicated_engine(tmp_path, "org_scope")
    org_a, state_id = _seed_state(engine)
    other_org = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=other_org, name="Other Org"))
        session.commit()
    with Session(engine) as session:
        result = AmazonOAuthStateRepository(session).consume(other_org, state_id)
    assert result is None
    row = _get_state(engine, org_a, state_id)
    assert row is not None
    assert row.consumed_at is None
    engine.dispose()

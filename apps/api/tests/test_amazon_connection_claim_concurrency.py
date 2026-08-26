"""12B.2A concurrency remediation — AmazonConnectionRepository.claim_identity_for_authorization.

Deterministic concurrency tests only. Two real threads are synchronized with
a `threading.Barrier` so both reach the claim call at the same instant, then
the database itself (not a Python-level lock) decides which one wins. No
sleeps, no probabilistic retry loops — every iteration is expected to satisfy
the invariant "exactly one caller may claim an incompatible identifier."

These tests deliberately do NOT use the shared app fixture (`session_scope`/
`get_engine`), which binds every session to one `StaticPool`-held in-memory
SQLite connection for the whole test process — i.e. every "session" in that
setup shares one physical connection, so two threads issuing writes through
it are not exercising genuine write-write serialization at all. Each test
here builds its own dedicated, file-based SQLite engine (default pooling, one
real connection per thread) so the two threads' UPDATEs genuinely contend for
the same on-disk row, the way two independent database connections would in
any deployment. See the 12B.2A concurrency report for why this still does not
constitute PostgreSQL-specific validation, and why the chosen design does not
need one.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.persistence.models import AmazonConnection, Base, Organization
from app.persistence.repositories import AmazonConnectionRepository

ITERATIONS = 20


@dataclass
class _ClaimOutcome:
    selling_partner_id: str
    claimed: bool


def _dedicated_engine(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 15, "check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


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


def _get_by_id(engine, organization_id: UUID, connection_id: UUID) -> AmazonConnection | None:
    with Session(engine) as session:
        return AmazonConnectionRepository(session).get_by_id(organization_id, connection_id)


def test_concurrent_claims_with_different_identifiers_on_empty_connection(tmp_path) -> None:
    """Two threads race to claim an identity-empty connection with different
    sellers. Exactly one must win, on every iteration, deterministically."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"empty_{i}")
        org_id, connection_id = _seed_connection(engine)
        barrier = threading.Barrier(2)
        outcomes: list[_ClaimOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_claim,
                kwargs=dict(
                    engine=engine,
                    organization_id=org_id,
                    connection_id=connection_id,
                    selling_partner_id=spid,
                    barrier=barrier,
                    outcomes=outcomes,
                    lock=lock,
                ),
            )
            for spid in ("RACESELLERA001", "RACESELLERB002")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2, f"iteration {i}: a thread failed to complete: {outcomes}"
        winners = [outcome for outcome in outcomes if outcome.claimed]
        losers = [outcome for outcome in outcomes if not outcome.claimed]
        assert len(winners) == 1, f"iteration {i}: expected exactly one winner, got {outcomes}"
        assert len(losers) == 1

        row = _get_by_id(engine, org_id, connection_id)
        assert row is not None
        assert row.selling_partner_id == winners[0].selling_partner_id
        assert row.selling_partner_id != losers[0].selling_partner_id
        engine.dispose()


def test_concurrent_claims_with_different_identifiers_on_claimed_connection(tmp_path) -> None:
    """Two threads race to claim an already-owned connection with two DIFFERENT
    (and both incompatible) identifiers. Both must lose — the existing identity
    is never disturbed by either."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"claimed_{i}")
        org_id, connection_id = _seed_connection(engine, selling_partner_id="ORIGINALOWNER1")
        barrier = threading.Barrier(2)
        outcomes: list[_ClaimOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_claim,
                kwargs=dict(
                    engine=engine,
                    organization_id=org_id,
                    connection_id=connection_id,
                    selling_partner_id=spid,
                    barrier=barrier,
                    outcomes=outcomes,
                    lock=lock,
                ),
            )
            for spid in ("INTRUDERONE001", "INTRUDERTWO002")
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2
        assert all(not outcome.claimed for outcome in outcomes), f"iteration {i}: {outcomes}"
        row = _get_by_id(engine, org_id, connection_id)
        assert row is not None
        assert row.selling_partner_id == "ORIGINALOWNER1"
        engine.dispose()


def test_concurrent_same_seller_claims_both_succeed(tmp_path) -> None:
    """Two threads race to (re)claim with the SAME identifier. Neither is a
    conflict; both may legitimately succeed, and the identity never changes."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"same_{i}")
        org_id, connection_id = _seed_connection(engine)
        barrier = threading.Barrier(2)
        outcomes: list[_ClaimOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_claim,
                kwargs=dict(
                    engine=engine,
                    organization_id=org_id,
                    connection_id=connection_id,
                    selling_partner_id="SAMESELLERRACE",
                    barrier=barrier,
                    outcomes=outcomes,
                    lock=lock,
                ),
            )
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2
        assert all(outcome.claimed for outcome in outcomes), f"iteration {i}: {outcomes}"
        row = _get_by_id(engine, org_id, connection_id)
        assert row is not None
        assert row.selling_partner_id == "SAMESELLERRACE"
        engine.dispose()


def test_cross_organization_claims_cannot_influence_each_other(tmp_path) -> None:
    """A claim scoped to organization B must never affect organization A's
    connection, even when raced concurrently against A's own claim."""
    engine = _dedicated_engine(tmp_path, "cross_org")
    org_a, connection_a = _seed_connection(engine)
    org_b, connection_b = _seed_connection(engine)

    barrier = threading.Barrier(2)
    outcomes: list[_ClaimOutcome] = []
    lock = threading.Lock()
    threads = [
        threading.Thread(
            target=_claim,
            kwargs=dict(
                engine=engine,
                organization_id=org_a,
                connection_id=connection_a,
                selling_partner_id="ORGASELLER0001",
                barrier=barrier,
                outcomes=outcomes,
                lock=lock,
            ),
        ),
        threading.Thread(
            target=_claim,
            kwargs=dict(
                engine=engine,
                organization_id=org_b,
                connection_id=connection_b,
                selling_partner_id="ORGBSELLER0002",
                barrier=barrier,
                outcomes=outcomes,
                lock=lock,
            ),
        ),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    assert len(outcomes) == 2
    assert all(outcome.claimed for outcome in outcomes)
    row_a = _get_by_id(engine, org_a, connection_a)
    row_b = _get_by_id(engine, org_b, connection_b)
    assert row_a is not None
    assert row_b is not None
    assert row_a.selling_partner_id == "ORGASELLER0001"
    assert row_b.selling_partner_id == "ORGBSELLER0002"
    engine.dispose()

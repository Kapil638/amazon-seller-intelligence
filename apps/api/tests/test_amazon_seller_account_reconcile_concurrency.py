"""12B.2B remediation — AmazonSellerAccountRepository.create_or_reconcile concurrency.

Deterministic concurrency tests only, following the same dedicated
file-based SQLite engine pattern as `test_amazon_connection_claim_
concurrency.py` — the shared in-memory `StaticPool` engine used by
`session_scope()`/`get_engine()` binds every session to one physical
connection, so it cannot exercise genuine write-write contention. Each test
here builds its own dedicated, file-based SQLite engine so the two threads'
inserts genuinely contend for the same on-disk unique constraint.

This regression pins the exact defect a real disposable-PostgreSQL CI run
surfaced: two concurrent `create_or_reconcile()` calls for the SAME
organization and SAME `selling_partner_id` must both succeed (one wins the
unique-constraint race, the other reconciles into the winner's row) —
neither may be misreported as `SellerAccountOwnershipConflict`, which must
remain reserved for a genuine, different-organization conflict.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.persistence.models import Base, Organization
from app.persistence.repositories import (
    AmazonSellerAccountRepository,
    SellerAccountOwnershipConflict,
)

ITERATIONS = 20


def _dedicated_engine(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"timeout": 15, "check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return engine


@dataclass
class _ReconcileOutcome:
    organization_id: UUID
    succeeded: bool
    conflict: bool


def _reconcile(
    *,
    engine,
    organization_id: UUID,
    selling_partner_id: str,
    barrier: threading.Barrier,
    outcomes: list[_ReconcileOutcome],
    lock: threading.Lock,
) -> None:
    barrier.wait()
    succeeded = False
    conflict = False
    with Session(engine) as session:
        try:
            AmazonSellerAccountRepository(session).create_or_reconcile(
                organization_id=organization_id, selling_partner_id=selling_partner_id
            )
            session.commit()
            succeeded = True
        except SellerAccountOwnershipConflict:
            conflict = True
    with lock:
        outcomes.append(
            _ReconcileOutcome(organization_id=organization_id, succeeded=succeeded, conflict=conflict)
        )


def test_same_organization_concurrent_reconciliation_never_reports_ownership_conflict(tmp_path) -> None:
    """Two threads reconciling the SAME org + SAME selling_partner_id must
    both succeed — the loser of the unique-constraint race is this
    organization's own concurrent attempt, not another organization's."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"same_org_{i}")
        org_id = uuid4()
        with Session(engine) as session:
            session.add(Organization(id=org_id, name="Same-Org Race Test"))
            session.commit()
        barrier = threading.Barrier(2)
        outcomes: list[_ReconcileOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_reconcile,
                kwargs=dict(
                    engine=engine,
                    organization_id=org_id,
                    selling_partner_id="RACESAMEORGSELLER01",
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

        assert len(outcomes) == 2, f"iteration {i}: a thread failed to complete: {outcomes}"
        assert all(outcome.succeeded for outcome in outcomes), f"iteration {i}: {outcomes}"
        assert not any(outcome.conflict for outcome in outcomes), f"iteration {i}: {outcomes}"

        with Session(engine) as session:
            accounts = AmazonSellerAccountRepository(session).list_for_org(org_id)
            assert len(accounts) == 1
        engine.dispose()


def test_cross_organization_concurrent_reconciliation_still_reports_ownership_conflict(tmp_path) -> None:
    """Two threads racing to reconcile the SAME selling_partner_id under
    DIFFERENT organizations must still land exactly one winner and one
    genuine ownership conflict — this fix must not paper over a real
    cross-organization mismatch."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"cross_org_{i}")
        org_a = uuid4()
        org_b = uuid4()
        with Session(engine) as session:
            session.add(Organization(id=org_a, name="Org A"))
            session.add(Organization(id=org_b, name="Org B"))
            session.commit()
        barrier = threading.Barrier(2)
        outcomes: list[_ReconcileOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_reconcile,
                kwargs=dict(
                    engine=engine,
                    organization_id=org,
                    selling_partner_id="RACECROSSORGSELLER01",
                    barrier=barrier,
                    outcomes=outcomes,
                    lock=lock,
                ),
            )
            for org in (org_a, org_b)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        assert len(outcomes) == 2, f"iteration {i}: a thread failed to complete: {outcomes}"
        winners = [outcome for outcome in outcomes if outcome.succeeded]
        losers = [outcome for outcome in outcomes if outcome.conflict]
        assert len(winners) == 1, f"iteration {i}: {outcomes}"
        assert len(losers) == 1, f"iteration {i}: {outcomes}"

        with Session(engine) as session:
            winner_accounts = AmazonSellerAccountRepository(session).list_for_org(winners[0].organization_id)
            loser_accounts = AmazonSellerAccountRepository(session).list_for_org(losers[0].organization_id)
            assert len(winner_accounts) == 1
            assert loser_accounts == []
        engine.dispose()

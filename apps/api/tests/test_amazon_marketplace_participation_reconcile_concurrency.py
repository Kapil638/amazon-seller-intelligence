"""12B.2B remediation — AmazonMarketplaceParticipationRepository.create_or_reconcile concurrency.

Deterministic concurrency tests only, following the same dedicated
file-based SQLite engine pattern as `test_amazon_connection_claim_
concurrency.py` and `test_amazon_seller_account_reconcile_concurrency.py` —
the shared in-memory `StaticPool` engine used by `session_scope()`/
`get_engine()` binds every session to one physical connection, so it cannot
exercise genuine write-write contention.

This pins the marketplace-participation half of the same-org convergence
defect: two concurrent `create_or_reconcile()` calls for the SAME seller
account and SAME `marketplace_id` must both succeed and converge on exactly
one participation row, and the session used by the loser of the
unique-constraint race must remain usable afterward (proving the fix uses a
SAVEPOINT to isolate the failed insert, not a full transaction rollback).
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
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
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
class _ParticipationOutcome:
    succeeded: bool
    session_reusable_after: bool


def _reconcile_participation(
    *,
    engine,
    organization_id: UUID,
    seller_account_id: UUID,
    marketplace_id: str,
    barrier: threading.Barrier,
    outcomes: list[_ParticipationOutcome],
    lock: threading.Lock,
) -> None:
    barrier.wait()
    succeeded = False
    session_reusable_after = False
    with Session(engine) as session:
        AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=organization_id,
            seller_account_id=seller_account_id,
            marketplace_id=marketplace_id,
            region="na",
        )
        session.commit()
        succeeded = True
        # Proves the SAVEPOINT recovery (if this thread lost the race)
        # isolated the failure to just the insert: this session must still
        # be able to do further, unrelated work rather than raising on an
        # aborted transaction.
        rows = AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
            organization_id, seller_account_id
        )
        session_reusable_after = len(rows) >= 1
    with lock:
        outcomes.append(
            _ParticipationOutcome(succeeded=succeeded, session_reusable_after=session_reusable_after)
        )


def test_same_marketplace_concurrent_reconciliation_never_fails(tmp_path) -> None:
    """Two threads reconciling the SAME seller account + SAME marketplace_id
    must both succeed and converge on exactly one participation row — the
    loser of the unique-constraint race is this seller account's own
    concurrent attempt, never treated as a failure."""
    for i in range(ITERATIONS):
        engine = _dedicated_engine(tmp_path, f"participation_race_{i}")
        org_id = uuid4()
        with Session(engine) as session:
            session.add(Organization(id=org_id, name="Participation Race Test"))
            seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
                organization_id=org_id, selling_partner_id=f"RACESELLER{i:03d}"
            )
            session.commit()
            seller_account_id = seller_account.id

        barrier = threading.Barrier(2)
        outcomes: list[_ParticipationOutcome] = []
        lock = threading.Lock()
        threads = [
            threading.Thread(
                target=_reconcile_participation,
                kwargs=dict(
                    engine=engine,
                    organization_id=org_id,
                    seller_account_id=seller_account_id,
                    marketplace_id="ATVPDKIKX0DER",
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
        assert all(outcome.session_reusable_after for outcome in outcomes), f"iteration {i}: {outcomes}"

        with Session(engine) as session:
            rows = AmazonMarketplaceParticipationRepository(session).list_for_seller_account(
                org_id, seller_account_id
            )
            assert len(rows) == 1
        engine.dispose()

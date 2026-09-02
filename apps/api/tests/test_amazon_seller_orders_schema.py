"""12B.4B — schema foundation for Orders: `amazon_seller_orders`,
`amazon_seller_order_items`, `amazon_ingestion_run_marketplace_participations`,
`amazon_orders_sync_checkpoints`, and the extended `amazon_ingestion_runs`/
`amazon_connections`/`amazon_marketplace_participations` ledgers.
Schema-level and repository-primitive proof only: no SP-API client,
ingestion service, read API, worker, or UI code exists yet.

Remediated after a schema review found four blocking gaps (see
`docs/AI_HANDOVER/12B4B_ORDERS_SCHEMA.md`): success-gated checkpoint
advancement, structural environment/connection consistency, a queued-then-
claimed durable lifecycle mirroring Listings, and `Numeric(19,4)` monetary
precision. This file's tests are organized by which of the four blockers
they prove, plus the pre-existing ownership/uniqueness/privacy coverage.

Uses a dedicated file-based SQLite engine (`Base.metadata.create_all()`),
the same pattern as `tests/test_amazon_seller_listings_schema.py`. Real
PostgreSQL proof of the migration itself lives in
`tests/postgres/test_disposable_postgres_orders_migration.py` (guarded,
not executed in this environment) and the dependency-free offline compile
check in `tests/test_migration_chain_matches_orm_metadata.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.persistence.models import (
    AmazonConnection,
    AmazonIngestionRun,
    AmazonIngestionRunMarketplaceParticipation,
    AmazonMarketplaceParticipation,
    AmazonOrdersSyncCheckpoint,
    AmazonSellerAccount,
    AmazonSellerOrder,
    AmazonSellerOrderItem,
    Base,
    Organization,
)
from app.persistence.repositories import (
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonOrdersSyncCheckpointRepository,
    AmazonSellerOrderItemRepository,
    AmazonSellerOrderRepository,
    OrdersRunFinalizationIncomplete,
    _validate_orders_money_amount,
)


def _as_utc(value: datetime) -> datetime:
    """SQLite does not round-trip `tzinfo` on `DateTime(timezone=True)` — a
    value read back is naive but still genuinely UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _dedicated_engine(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    return engine


def _dedicated_engine_with_fk_enforcement(tmp_path: Path, name: str):
    db_path = tmp_path / f"{name}.sqlite3"
    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _connection_record) -> None:
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def _seed_org_seller_account(engine, *, name: str = "12B.4B Schema Test Org"):
    org_id = uuid4()
    seller_account_id = uuid4()
    with Session(engine) as session:
        session.add(Organization(id=org_id, name=name))
        session.add(
            AmazonSellerAccount(
                id=seller_account_id,
                organization_id=org_id,
                selling_partner_id=f"A{uuid4().hex[:14].upper()}",
                status="active",
            )
        )
        session.commit()
    return org_id, seller_account_id


def _seed_connection(engine, *, organization_id, region="na", environment="PRODUCTION"):
    connection_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonConnection(
                id=connection_id,
                organization_id=organization_id,
                provider="SP_API",
                environment=environment,
                region=region,
                status="connected",
            )
        )
        session.commit()
    return connection_id


def _seed_participation(
    engine, *, organization_id, seller_account_id, connection_id, marketplace_id="ATVPDKIKX0DER", region="na"
):
    participation_id = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=participation_id,
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                connection_id=connection_id,
                marketplace_id=marketplace_id,
                region=region,
            )
        )
        session.commit()
    return participation_id


def _enqueue(
    engine,
    *,
    organization_id,
    seller_account_id,
    connection_id,
    participation_ids,
    region="na",
    environment="PRODUCTION",
):
    with Session(engine) as session:
        claim = AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
            organization_id=organization_id,
            seller_account_id=seller_account_id,
            connection_id=connection_id,
            marketplace_participation_ids=list(participation_ids),
            region=region,
            environment=environment,
        )
        session.commit()
        return claim


def _claim(engine, *, organization_id, seller_account_id, region="na", environment="PRODUCTION", lease_owner="worker-1"):
    with Session(engine) as session:
        claim = AmazonIngestionRunMarketplaceParticipationRepository(session).claim_orders_run(
            organization_id=organization_id,
            seller_account_id=seller_account_id,
            region=region,
            environment=environment,
            lease_owner=lease_owner,
            lease_duration_seconds=300,
        )
        session.commit()
        return claim


def _enqueue_and_claim(engine, *, organization_id, seller_account_id, connection_id, participation_ids, region="na", environment="PRODUCTION"):
    enqueued = _enqueue(
        engine,
        organization_id=organization_id,
        seller_account_id=seller_account_id,
        connection_id=connection_id,
        participation_ids=participation_ids,
        region=region,
        environment=environment,
    )
    assert enqueued.claimed is True
    claimed = _claim(engine, organization_id=organization_id, seller_account_id=seller_account_id, region=region, environment=environment)
    assert claimed.claimed is True
    assert claimed.run_id == enqueued.run_id
    return enqueued.run_id


def _finalize(engine, *, organization_id, seller_account_id, run_id, participation_watermarks):
    with Session(engine) as session:
        outcome = AmazonIngestionRunMarketplaceParticipationRepository(session).finalize_successful_orders_run(
            organization_id=organization_id,
            seller_account_id=seller_account_id,
            ingestion_run_id=run_id,
            participation_watermarks=participation_watermarks,
        )
        session.commit()
        return outcome


def _full_scope(engine, *, region="na", environment="PRODUCTION", marketplace_id="ATVPDKIKX0DER"):
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id, region=region, environment=environment)
    participation_id = _seed_participation(
        engine,
        organization_id=org_id,
        seller_account_id=seller_account_id,
        connection_id=connection_id,
        marketplace_id=marketplace_id,
        region=region,
    )
    return org_id, seller_account_id, connection_id, participation_id


# =====================================================================
# Blocker 3 — durable queued-then-claimed lifecycle
# =====================================================================


def test_enqueue_produces_queued_unleased_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_enqueue_queued")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    claim = _enqueue(
        engine,
        organization_id=org_id,
        seller_account_id=seller_account_id,
        connection_id=connection_id,
        participation_ids=[participation_id],
    )
    assert claim.claimed is True
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, claim.run_id)
        assert run.status == "queued"
        assert run.started_at is None
        assert run.lease_owner is None
        assert run.lease_expires_at is None


def test_direct_start_method_does_not_exist() -> None:
    """The old `start_orders_run` (created a run directly `started`, no
    worker claim) was removed entirely, not merely deprecated."""
    assert not hasattr(AmazonIngestionRunMarketplaceParticipationRepository, "start_orders_run")


def test_exactly_one_active_orders_run_per_selected_scope(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_active_scope")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    first = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    second = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    assert first.claimed is True
    assert second.claimed is False
    assert second.reason == "already_running"


def test_concurrent_enqueue_converges_to_one_active_job(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_concurrent_enqueue")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    outcomes = [
        _enqueue(
            engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
            participation_ids=[participation_id],
        )
        for _ in range(5)
    ]
    assert sum(1 for o in outcomes if o.claimed) == 1


def test_worker_claim_is_the_only_transition_to_started(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_claim_transition")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    enqueued = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    with Session(engine) as session:
        assert session.get(AmazonIngestionRun, enqueued.run_id).status == "queued"

    claimed = _claim(engine, organization_id=org_id, seller_account_id=seller_account_id)
    assert claimed.claimed is True
    with Session(engine) as session:
        assert session.get(AmazonIngestionRun, enqueued.run_id).status == "started"


def test_claim_sets_started_at_lease_owner_expiry_and_heartbeat(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_claim_fields")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    claimed = _claim(engine, organization_id=org_id, seller_account_id=seller_account_id, lease_owner="worker-xyz")
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, claimed.run_id)
        assert run.started_at is not None
        assert run.lease_owner == "worker-xyz"
        assert run.lease_expires_at is not None
        assert run.last_heartbeat_at is not None


def test_claim_with_no_eligible_job_reports_reason(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_claim_no_job")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    claim = _claim(engine, organization_id=org_id, seller_account_id=seller_account_id)
    assert claim.claimed is False
    assert claim.reason == "no_eligible_job"


def test_queued_run_cannot_complete_directly(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b3_queued_no_finalize")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    enqueued = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    outcome = _finalize(
        engine,
        organization_id=org_id,
        seller_account_id=seller_account_id,
        run_id=enqueued.run_id,
        participation_watermarks={participation_id: datetime.now(UTC)},
    )
    assert outcome.finalized is False
    with Session(engine) as session:
        assert session.get(AmazonIngestionRun, enqueued.run_id).status == "queued"


# =====================================================================
# Blocker 1 — success-gated checkpoint advancement
# =====================================================================


def test_succeeded_orders_run_advances_checkpoint(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_success_advances")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    watermark = datetime.now(UTC)
    outcome = _finalize(
        engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_id,
        participation_watermarks={participation_id: watermark},
    )
    assert outcome.finalized is True
    assert outcome.advanced_participation_ids == (participation_id,)
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        assert run.status == "succeeded"
        assert run.completed_at is not None
        assert run.lease_owner is None
        assert run.lease_expires_at is None
        checkpoint = AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_id)
        assert _as_utc(checkpoint.synced_through_at) == watermark
        assert checkpoint.last_successful_run_id == run_id


@pytest.mark.parametrize(
    "status,failure_class",
    [
        ("queued", None),
        ("waiting_to_retry", None),
        ("failed", None),
        ("partial", None),
        ("timed_out", None),
        ("failed", "cancelled_before_start"),
    ],
)
def test_checkpoint_advance_rejected_for_non_started_status(tmp_path, status, failure_class) -> None:
    engine = _dedicated_engine(tmp_path, f"b1_reject_{status}_{failure_class}")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    enqueued = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, enqueued.run_id)
        run.status = status
        if status in ("failed", "partial", "timed_out"):
            run.completed_at = datetime.now(UTC)
        if failure_class:
            run.failure_class = failure_class
        session.commit()

    outcome = _finalize(
        engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=enqueued.run_id,
        participation_watermarks={participation_id: datetime.now(UTC)},
    )
    assert outcome.finalized is False
    with Session(engine) as session:
        assert AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_id) is None


def test_checkpoint_advance_rejected_for_started_status_called_directly(tmp_path) -> None:
    """The private gate, probed directly (bypassing `finalize_successful_
    orders_run`): a merely `started` run — never marked `succeeded` — must
    not be able to advance a checkpoint."""
    engine = _dedicated_engine(tmp_path, "b1_reject_started_direct")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    with Session(engine) as session:
        result = AmazonOrdersSyncCheckpointRepository(session)._advance_if_run_succeeded(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            ingestion_run_id=run_id,
            synced_through_at=datetime.now(UTC),
        )
        session.commit()
    assert result is False
    with Session(engine) as session:
        assert AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_id) is None


def test_checkpoint_advance_rejected_for_listings_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_reject_listings")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    with Session(engine) as session:
        run = AmazonIngestionRun(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            connection_id=connection_id,
            run_type="listings",
            domain="listings_items",
            region="na",
            environment="PRODUCTION",
            status="succeeded",
            completed_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        result = AmazonOrdersSyncCheckpointRepository(session)._advance_if_run_succeeded(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            ingestion_run_id=run_id,
            synced_through_at=datetime.now(UTC),
        )
    assert result is False


def test_checkpoint_advance_rejected_for_marketplace_participations_run(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_reject_mp_run")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    with Session(engine) as session:
        run = AmazonIngestionRun(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            connection_id=connection_id,
            run_type="marketplace_participations",
            domain="amazon.com",
            region="na",
            environment="PRODUCTION",
            status="succeeded",
            completed_at=datetime.now(UTC),
        )
        session.add(run)
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        result = AmazonOrdersSyncCheckpointRepository(session)._advance_if_run_succeeded(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=participation_id,
            ingestion_run_id=run_id,
            synced_through_at=datetime.now(UTC),
        )
    assert result is False


def test_checkpoint_advance_rejected_for_participation_outside_run_membership(tmp_path) -> None:
    """Also proves the all-or-nothing atomicity: `finalize_successful_
    orders_run` must raise, not silently skip the ineligible participation
    and report success — see
    `test_finalize_mid_batch_failure_rolls_back_run_and_all_earlier_checkpoint_writes`
    for the case where a *valid* participation is processed first."""
    engine = _dedicated_engine(tmp_path, "b1_reject_outside_membership")
    org_id, seller_account_id, connection_id, participation_a = _full_scope(engine)
    participation_b = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        marketplace_id="A2EUQ1WTGCTBG2",
    )
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_a],
    )
    with Session(engine) as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        with pytest.raises(OrdersRunFinalizationIncomplete):
            repo.finalize_successful_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                ingestion_run_id=run_id,
                participation_watermarks={participation_b: datetime.now(UTC)},
            )
        session.rollback()
    with Session(engine) as session:
        assert session.get(AmazonIngestionRun, run_id).status == "started"
        assert AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_b) is None


def test_checkpoint_advance_rejected_for_cross_organization(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_reject_cross_org")
    org_a, seller_a, connection_a, participation_a = _full_scope(engine)
    run_id = _enqueue_and_claim(
        engine, organization_id=org_a, seller_account_id=seller_a, connection_id=connection_a,
        participation_ids=[participation_a],
    )
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        run.status = "succeeded"
        run.completed_at = datetime.now(UTC)
        session.commit()

    org_b, seller_b = _seed_org_seller_account(engine, name="Org B")
    with Session(engine) as session:
        result = AmazonOrdersSyncCheckpointRepository(session)._advance_if_run_succeeded(
            organization_id=org_b,
            seller_account_id=seller_b,
            marketplace_participation_id=participation_a,
            ingestion_run_id=run_id,
            synced_through_at=datetime.now(UTC),
        )
    assert result is False


def test_checkpoint_advance_rejected_for_cross_seller_same_organization(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_reject_cross_seller")
    org_id, seller_a, connection_id, participation_a = _full_scope(engine)
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_a, connection_id=connection_id,
        participation_ids=[participation_a],
    )
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        run.status = "succeeded"
        run.completed_at = datetime.now(UTC)
        session.commit()

    seller_b = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonSellerAccount(
                id=seller_b, organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active"
            )
        )
        session.commit()

    with Session(engine) as session:
        result = AmazonOrdersSyncCheckpointRepository(session)._advance_if_run_succeeded(
            organization_id=org_id,
            seller_account_id=seller_b,
            marketplace_participation_id=participation_a,
            ingestion_run_id=run_id,
            synced_through_at=datetime.now(UTC),
        )
    assert result is False


def test_older_watermark_cannot_move_checkpoint_backward(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_older_watermark")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)

    run_1 = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    _finalize(engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_1, participation_watermarks={participation_id: t2})

    run_2 = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    outcome = _finalize(engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_2, participation_watermarks={participation_id: t1})
    assert outcome.finalized is True  # the run itself still succeeds

    with Session(engine) as session:
        checkpoint = AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_id)
        assert _as_utc(checkpoint.synced_through_at) == t2
        assert checkpoint.last_successful_run_id == run_1


def test_same_watermark_is_idempotent(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_idempotent_watermark")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    watermark = datetime(2026, 1, 1, tzinfo=UTC)

    run_1 = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    _finalize(engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_1, participation_watermarks={participation_id: watermark})

    run_2 = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    outcome = _finalize(engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_2, participation_watermarks={participation_id: watermark})
    assert outcome.finalized is True
    assert outcome.advanced_participation_ids == (participation_id,)

    with Session(engine) as session:
        checkpoint = AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_id)
        assert _as_utc(checkpoint.synced_through_at) == watermark
        assert checkpoint.last_successful_run_id == run_2  # provenance still refreshes


def test_finalize_rollback_leaves_run_and_checkpoint_unchanged_on_rejection(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_rollback")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    enqueued = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    # Never claimed — still queued. finalize must reject and touch nothing.
    with Session(engine) as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        outcome = repo.finalize_successful_orders_run(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            ingestion_run_id=enqueued.run_id,
            participation_watermarks={participation_id: datetime.now(UTC)},
        )
        session.rollback()
    assert outcome.finalized is False
    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, enqueued.run_id)
        assert run.status == "queued"
        assert AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_id) is None


def test_finalize_mid_batch_failure_rolls_back_run_and_all_earlier_checkpoint_writes(tmp_path) -> None:
    """Deterministic mid-batch failure, not merely rejection-before-updates-
    begin: the run genuinely transitions to `succeeded` and a real
    checkpoint write genuinely happens for `participation_valid` (first in
    dict iteration order) *before* `participation_foreign` (not part of
    this run's membership) causes `finalize_successful_orders_run` to
    raise. All-or-nothing means the raise must force a rollback that
    undoes the already-applied `succeeded` status flip AND the already-
    written checkpoint for `participation_valid` — never a state where the
    run is left `succeeded` with only one of the two checkpoints advanced.
    """
    engine = _dedicated_engine(tmp_path, "b1_mid_batch_failure")
    org_id, seller_account_id, connection_id, participation_valid = _full_scope(engine)
    participation_foreign = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        marketplace_id="A2EUQ1WTGCTBG2",
    )
    # participation_foreign is deliberately NEVER included in the run's
    # own enqueue — it exists, and belongs to the same org/seller, but
    # this specific run never covered it.
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_valid],
    )

    with Session(engine) as session:
        repo = AmazonIngestionRunMarketplaceParticipationRepository(session)
        with pytest.raises(OrdersRunFinalizationIncomplete):
            repo.finalize_successful_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                ingestion_run_id=run_id,
                # dict insertion order: participation_valid is processed
                # (and its checkpoint successfully written) BEFORE
                # participation_foreign fails eligibility and raises.
                participation_watermarks={
                    participation_valid: datetime.now(UTC),
                    participation_foreign: datetime.now(UTC),
                },
            )
        # The caller's rollback — never a swallow-and-commit.
        session.rollback()

    with Session(engine) as session:
        run = session.get(AmazonIngestionRun, run_id)
        assert run.status == "started"  # NOT left "succeeded"
        assert run.completed_at is None
        checkpoint_repo = AmazonOrdersSyncCheckpointRepository(session)
        assert checkpoint_repo.get(org_id, participation_valid) is None  # rolled back too
        assert checkpoint_repo.get(org_id, participation_foreign) is None


def test_multi_participation_success_advances_exactly_the_runs_included_participations(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b1_multi_participation")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id)
    participation_us = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        marketplace_id="ATVPDKIKX0DER",
    )
    participation_ca = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        marketplace_id="A2EUQ1WTGCTBG2",
    )
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_us, participation_ca],
    )
    watermark = datetime.now(UTC)
    outcome = _finalize(
        engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_id,
        participation_watermarks={participation_us: watermark, participation_ca: watermark},
    )
    assert outcome.finalized is True
    assert set(outcome.advanced_participation_ids) == {participation_us, participation_ca}
    with Session(engine) as session:
        checkpoint_repo = AmazonOrdersSyncCheckpointRepository(session)
        assert checkpoint_repo.get(org_id, participation_us) is not None
        assert checkpoint_repo.get(org_id, participation_ca) is not None


# =====================================================================
# Blocker 2 — structural environment/connection consistency
# =====================================================================


def test_production_run_with_production_participation_succeeds(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_prod_prod")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine, environment="PRODUCTION")
    claim = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id], environment="PRODUCTION",
    )
    assert claim.claimed is True


def test_sandbox_run_with_sandbox_participation_succeeds(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_sandbox_sandbox")
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine, environment="SANDBOX")
    claim = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id], environment="SANDBOX",
    )
    assert claim.claimed is True


def test_production_run_with_sandbox_backed_participation_rejected(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_prod_run_sandbox_participation")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    sandbox_connection = _seed_connection(engine, organization_id=org_id, environment="SANDBOX")
    production_connection = _seed_connection(engine, organization_id=org_id, environment="PRODUCTION")
    sandbox_participation = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=sandbox_connection,
    )
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=production_connection,
                marketplace_participation_ids=[sandbox_participation],
                region="na",
                environment="PRODUCTION",
            )


def test_sandbox_run_with_production_backed_participation_rejected(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_sandbox_run_prod_participation")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    sandbox_connection = _seed_connection(engine, organization_id=org_id, environment="SANDBOX")
    production_connection = _seed_connection(engine, organization_id=org_id, environment="PRODUCTION")
    production_participation = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=production_connection,
    )
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=sandbox_connection,
                marketplace_participation_ids=[production_participation],
                region="na",
                environment="SANDBOX",
            )


def test_region_mismatch_rejected(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_region_mismatch")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id, region="na")
    eu_participation = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, region="eu",
    )
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=connection_id,
                marketplace_participation_ids=[eu_participation],
                region="na",
                environment="PRODUCTION",
            )


def test_connection_region_environment_mismatch_rejected_at_enqueue(tmp_path) -> None:
    """The caller asserts a region/environment that disagrees with the
    named connection's own authoritative values."""
    engine = _dedicated_engine(tmp_path, "b2_connection_mismatch")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id, region="na", environment="PRODUCTION")
    participation_id = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
    )
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=connection_id,
                marketplace_participation_ids=[participation_id],
                region="na",
                environment="SANDBOX",  # disagrees with the connection's own PRODUCTION
            )


def test_cross_organization_connection_rejected(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_cross_org_connection")
    org_a, seller_a = _seed_org_seller_account(engine, name="Org A")
    org_b, seller_b = _seed_org_seller_account(engine, name="Org B")
    connection_b = _seed_connection(engine, organization_id=org_b)
    participation_a = _seed_participation(
        engine, organization_id=org_a, seller_account_id=seller_a, connection_id=_seed_connection(engine, organization_id=org_a),
    )
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_a,
                seller_account_id=seller_a,
                connection_id=connection_b,
                marketplace_participation_ids=[participation_a],
                region="na",
                environment="PRODUCTION",
            )


def test_database_rejects_association_row_spanning_two_connections(tmp_path) -> None:
    """The structural (composite-FK) guarantee, not just the repository
    check: even a direct ORM write that pairs a run with a participation
    on a *different* connection is rejected at the database level."""
    engine = _dedicated_engine_with_fk_enforcement(tmp_path, "b2_db_level_connection_guard")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_a = _seed_connection(engine, organization_id=org_id, environment="PRODUCTION")
    connection_b = _seed_connection(engine, organization_id=org_id, environment="SANDBOX")
    participation_b = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_b,
    )
    with Session(engine) as session:
        run = AmazonIngestionRun(
            organization_id=org_id,
            seller_account_id=seller_account_id,
            connection_id=connection_a,
            marketplace_participation_id=None,
            run_type="orders",
            domain="orders",
            region="na",
            environment="PRODUCTION",
            status="queued",
        )
        session.add(run)
        session.commit()
        run_id = run.id

    with Session(engine) as session:
        session.add(
            AmazonIngestionRunMarketplaceParticipation(
                ingestion_run_id=run_id,
                marketplace_participation_id=participation_b,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                region="na",
                connection_id=connection_b,  # disagrees with the run's own connection_a
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_connection_own_region_environment_must_match_itself(tmp_path) -> None:
    """`amazon_ingestion_runs`' own composite FK to `amazon_connections`
    rejects a run whose region/environment disagree with its own
    connection_id's authoritative row, even bypassing the repository."""
    engine = _dedicated_engine_with_fk_enforcement(tmp_path, "b2_run_connection_self_consistency")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id, region="na", environment="PRODUCTION")
    with Session(engine) as session:
        session.add(
            AmazonIngestionRun(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=connection_id,
                marketplace_participation_id=None,
                run_type="orders",
                domain="orders",
                region="eu",  # disagrees with the connection's own "na"
                environment="PRODUCTION",
                status="queued",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_valid_multi_marketplace_same_seller_region_environment_succeeds(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_valid_multi_marketplace")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id)
    participation_us = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        marketplace_id="ATVPDKIKX0DER",
    )
    participation_ca = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        marketplace_id="A2EUQ1WTGCTBG2",
    )
    claim = _enqueue(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_us, participation_ca],
    )
    assert claim.claimed is True
    with Session(engine) as session:
        memberships = (
            session.query(AmazonIngestionRunMarketplaceParticipation).filter_by(ingestion_run_id=claim.run_id).all()
        )
        assert {m.marketplace_participation_id for m in memberships} == {participation_us, participation_ca}
        assert all(m.connection_id == connection_id for m in memberships)


def test_participation_with_no_connection_cannot_be_enqueued(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "b2_no_connection_participation")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id)
    orphan_participation = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonMarketplaceParticipation(
                id=orphan_participation,
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=None,
                marketplace_id="ATVPDKIKX0DER",
                region="na",
            )
        )
        session.commit()
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_id,
                seller_account_id=seller_account_id,
                connection_id=connection_id,
                marketplace_participation_ids=[orphan_participation],
                region="na",
                environment="PRODUCTION",
            )


# =====================================================================
# Blocker 4 — monetary precision (Numeric(19,4))
# =====================================================================


def test_monetary_columns_are_numeric_19_4() -> None:
    for table_name, column_name in (
        ("amazon_seller_orders", "order_total_amount"),
        ("amazon_seller_order_items", "unit_price_amount"),
        ("amazon_seller_order_items", "item_proceeds_amount"),
    ):
        column = Base.metadata.tables[table_name].columns[column_name]
        assert column.type.precision == 19
        assert column.type.scale == 4


def test_zero_amount_round_trips(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_zero")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, order_total_amount=Decimal("0.0000"))
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        assert session.get(AmazonSellerOrder, order_id).order_total_amount == Decimal("0.0000")


def test_negative_amount_round_trips_for_adjustments(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_negative")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, order_total_amount=Decimal("-15.5000"))
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        assert session.get(AmazonSellerOrder, order_id).order_total_amount == Decimal("-15.5000")


def test_moderately_large_amount_round_trips(tmp_path) -> None:
    """A large-but-not-boundary amount, safely representable by SQLite's
    own float-based `NUMERIC` storage. The *exact* 19-digit boundary case
    is deliberately not asserted here — see the module-level note above
    `test_excessive_precision_rejected_rather_than_silently_rounded` for
    why that specific proof requires real PostgreSQL."""
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_large")
    large = Decimal("123456789.1234")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, order_total_amount=large)
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        assert session.get(AmazonSellerOrder, order_id).order_total_amount == large


def test_three_decimal_currency_value_round_trips_exactly(tmp_path) -> None:
    """E.g. BHD/KWD/OMR use three fractional digits."""
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_three_decimal")
    value = Decimal("12.345")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, order_total_amount=value, order_total_currency="BHD")
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        row = session.get(AmazonSellerOrder, order_id)
        assert row.order_total_amount == Decimal("12.3450")
        assert row.order_total_currency == "BHD"


def test_four_decimal_value_round_trips_exactly(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_four_decimal")
    value = Decimal("12.3456")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, order_total_amount=value)
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        assert session.get(AmazonSellerOrder, order_id).order_total_amount == value


# NOTE — real PostgreSQL's NUMERIC(19,4) does NOT reject a value with more
# than 4 fractional digits; it silently ROUNDS it at type-coercion time
# (confirmed: PostgreSQL's numeric type only raises `numeric_field_
# overflow` for excess *magnitude* — an integer part needing more digits
# than precision-scale allows — never for excess *scale*). Relying on the
# database to reject excess precision would be relying on behavior it does
# not have. The actual, only enforcement point is therefore the
# repository/DTO write boundary — `_validate_orders_money_amount`, called
# before any value is bound into SQL — proven by the tests below, which
# run identically on SQLite because the rejection happens in pure Python,
# before any SQL is even constructed. Excess *magnitude* rejection at the
# real database level (`numeric_field_overflow`) is separately proven in
# `tests/postgres/test_disposable_postgres_orders_migration.py`, since
# SQLite's `Numeric` type has no native arbitrary-precision backing and
# cannot prove that specific guarantee.


def test_excess_scale_rejected_before_sql_execution_order_total(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_scale_order")
    with Session(engine) as session:
        with pytest.raises(ValueError):
            AmazonSellerOrderRepository(session).upsert(
                **_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-EXCESS-SCALE", order_total_amount=Decimal("12.34567"))
            )
        session.rollback()
    with Session(engine) as session:
        assert session.query(AmazonSellerOrder).filter_by(amazon_order_id="902-EXCESS-SCALE").first() is None


def test_excess_magnitude_rejected_before_sql_execution_order_total(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_magnitude_order")
    with Session(engine) as session:
        with pytest.raises(ValueError):
            AmazonSellerOrderRepository(session).upsert(
                **_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-EXCESS-MAGNITUDE", order_total_amount=Decimal("9999999999999999.9999"))
            )
        session.rollback()
    with Session(engine) as session:
        assert session.query(AmazonSellerOrder).filter_by(amazon_order_id="902-EXCESS-MAGNITUDE").first() is None


def test_float_amount_rejected_rather_than_implicitly_converted(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_float_rejected")
    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonSellerOrderRepository(session).upsert(
                **_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-FLOAT", order_total_amount=19.99)
            )


def test_excess_scale_rejected_before_sql_execution_item_fields(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_scale_item")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(**_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-ITEM-SCALE"))
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        with pytest.raises(ValueError):
            AmazonSellerOrderItemRepository(session).upsert(
                organization_id=org_id, marketplace_participation_id=participation_id, order_id=order_id,
                amazon_order_item_id="10000000000201", seller_sku="SKU-SCALE", asin=None, item_name=None,
                condition_type=None, quantity_ordered=1, quantity_fulfilled=None, quantity_unfulfilled=None,
                unit_price_amount=Decimal("1.23456"), unit_price_currency="USD",
                item_proceeds_amount=None, item_proceeds_currency=None, last_ingestion_run_id=run_id,
            )
        session.rollback()
    with Session(engine) as session:
        assert session.query(AmazonSellerOrderItem).filter_by(amazon_order_item_id="10000000000201").first() is None


def test_excess_magnitude_rejected_before_sql_execution_item_fields(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_magnitude_item")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(**_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-ITEM-MAGNITUDE"))
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        with pytest.raises(ValueError):
            AmazonSellerOrderItemRepository(session).upsert(
                organization_id=org_id, marketplace_participation_id=participation_id, order_id=order_id,
                amazon_order_item_id="10000000000202", seller_sku="SKU-MAGNITUDE", asin=None, item_name=None,
                condition_type=None, quantity_ordered=1, quantity_fulfilled=None, quantity_unfulfilled=None,
                unit_price_amount=None, unit_price_currency=None,
                item_proceeds_amount=Decimal("9999999999999999.9999"), item_proceeds_currency="USD",
                last_ingestion_run_id=run_id,
            )
        session.rollback()
    with Session(engine) as session:
        assert session.query(AmazonSellerOrderItem).filter_by(amazon_order_item_id="10000000000202").first() is None


def test_boundary_valid_scale_and_magnitude_accepted(tmp_path) -> None:
    """The validation is exact at the boundary, not overly conservative:
    exactly 4 fractional digits passes, and a large-but-not-extreme
    magnitude passes. The *exact* 15-integer-digit magnitude boundary
    (`999999999999999.9999`) is validated at the Python layer here too
    (it must not raise), but its exact round-trip is only asserted against
    real PostgreSQL (`test_boundary_magnitude_round_trips_exactly_on_real_
    postgres`) — SQLite's float-based `NUMERIC` storage cannot represent
    that specific extreme value exactly (confirmed empirically: it rounds
    to `1000000000000000.0000` on read-back), which is a SQLite storage
    limitation, not a validation-logic bug."""
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_boundary_valid")

    # Validation itself must accept the exact boundary value without
    # raising, even though SQLite's own storage can't round-trip it later.
    _validate_orders_money_amount(Decimal("999999999999999.9999"), field_name="order_total_amount")

    moderately_large_boundary = Decimal("123456789012.3456")  # 4 fractional digits, safely SQLite-representable
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-BOUNDARY-VALID", order_total_amount=moderately_large_boundary)
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        assert session.get(AmazonSellerOrder, order_id).order_total_amount == moderately_large_boundary


def test_missing_amount_remains_null(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "b4_missing_amount")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, order_total_amount=None, order_total_currency=None)
        )
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        row = session.get(AmazonSellerOrder, order_id)
        assert row.order_total_amount is None
        assert row.order_total_currency is None
        # Absence distinct from zero.
        assert row.order_total_amount != Decimal("0")


def test_no_float_type_anywhere_in_new_orders_columns() -> None:
    for table_name, column_name in (
        ("amazon_seller_orders", "order_total_amount"),
        ("amazon_seller_order_items", "unit_price_amount"),
        ("amazon_seller_order_items", "item_proceeds_amount"),
    ):
        column = Base.metadata.tables[table_name].columns[column_name]
        assert "FLOAT" not in str(column.type).upper()


def test_no_float_conversion_in_repository_upsert_source() -> None:
    import inspect as _inspect

    source = _inspect.getsource(AmazonSellerOrderRepository.upsert) + _inspect.getsource(
        AmazonSellerOrderItemRepository.upsert
    )
    assert "float(" not in source


def _order_repo_context(tmp_path, name):
    engine = _dedicated_engine(tmp_path, name)
    org_id, seller_account_id, connection_id, participation_id = _full_scope(engine)
    run_id = _enqueue_and_claim(
        engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id,
        participation_ids=[participation_id],
    )
    return engine, org_id, participation_id, participation_id, run_id


def _order_kwargs(org_id, participation_id, run_id, **overrides):
    base = dict(
        organization_id=org_id,
        marketplace_participation_id=participation_id,
        amazon_order_id=f"902-{uuid4().hex[:10]}",
        fulfillment_status=None,
        fulfilled_by=None,
        sales_channel_name=None,
        sales_channel_marketplace_id=None,
        sales_channel_marketplace_name=None,
        items_shipped_count=None,
        items_unshipped_count=None,
        order_total_amount=None,
        order_total_currency="USD",
        is_business_order=False,
        is_prime=False,
        was_cancelled=False,
        amazon_created_at=None,
        amazon_last_updated_at=None,
        last_ingestion_run_id=run_id,
    )
    base.update(overrides)
    return base


# =====================================================================
# Ownership / uniqueness / provenance (carried over, updated for the new
# connection-scoped enqueue/claim API)
# =====================================================================


def test_orders_run_cannot_bind_participation_from_another_organization(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "own_cross_org")
    org_a, seller_a = _seed_org_seller_account(engine, name="Org A")
    org_b, seller_b = _seed_org_seller_account(engine, name="Org B")
    connection_a = _seed_connection(engine, organization_id=org_a)
    connection_b = _seed_connection(engine, organization_id=org_b)
    participation_b = _seed_participation(engine, organization_id=org_b, seller_account_id=seller_b, connection_id=connection_b)

    with Session(engine) as session:
        with pytest.raises(TypeError):
            AmazonIngestionRunMarketplaceParticipationRepository(session).enqueue_orders_run(
                organization_id=org_a,
                seller_account_id=seller_a,
                connection_id=connection_a,
                marketplace_participation_ids=[participation_b],
                region="na",
                environment="PRODUCTION",
            )


def test_no_one_seller_per_organization_assumption(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "own_multi_seller_org")
    org_id, seller_a = _seed_org_seller_account(engine)
    seller_b = uuid4()
    with Session(engine) as session:
        session.add(
            AmazonSellerAccount(id=seller_b, organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}", status="active")
        )
        session.commit()
    connection_a = _seed_connection(engine, organization_id=org_id)
    participation_a = _seed_participation(engine, organization_id=org_id, seller_account_id=seller_a, connection_id=connection_a)
    participation_b = _seed_participation(
        engine, organization_id=org_id, seller_account_id=seller_b, connection_id=connection_a, marketplace_id="A2EUQ1WTGCTBG2",
    )
    claim_a = _enqueue(engine, organization_id=org_id, seller_account_id=seller_a, connection_id=connection_a, participation_ids=[participation_a])
    claim_b = _enqueue(engine, organization_id=org_id, seller_account_id=seller_b, connection_id=connection_a, participation_ids=[participation_b])
    assert claim_a.claimed is True
    assert claim_b.claimed is True


def test_duplicate_order_in_same_participation_converges(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "own_order_converge")
    with Session(engine) as session:
        AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-CONVERGE", fulfillment_status="UNSHIPPED")
        )
        session.commit()
    with Session(engine) as session:
        AmazonSellerOrderRepository(session).upsert(
            **_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-CONVERGE", fulfillment_status="SHIPPED")
        )
        session.commit()
    with Session(engine) as session:
        rows = session.query(AmazonSellerOrder).filter_by(amazon_order_id="902-CONVERGE").all()
        assert len(rows) == 1
        assert rows[0].fulfillment_status == "SHIPPED"


def test_same_amazon_order_id_in_different_participation_is_allowed(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "own_order_id_cross_participation")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id)
    participation_a = _seed_participation(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, marketplace_id="ATVPDKIKX0DER")
    participation_b = _seed_participation(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2")
    run_id = _enqueue_and_claim(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, participation_ids=[participation_a, participation_b])

    with Session(engine) as session:
        AmazonSellerOrderRepository(session).upsert(**_order_kwargs(org_id, participation_a, run_id, amazon_order_id="902-SAME-ID"))
        AmazonSellerOrderRepository(session).upsert(**_order_kwargs(org_id, participation_b, run_id, amazon_order_id="902-SAME-ID"))
        session.commit()
    with Session(engine) as session:
        assert len(session.query(AmazonSellerOrder).filter_by(amazon_order_id="902-SAME-ID").all()) == 2


def test_order_cannot_bind_a_run_that_did_not_cover_its_participation(tmp_path) -> None:
    engine = _dedicated_engine_with_fk_enforcement(tmp_path, "own_cross_run_provenance")
    org_id, seller_account_id = _seed_org_seller_account(engine)
    connection_id = _seed_connection(engine, organization_id=org_id)
    participation_a = _seed_participation(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, marketplace_id="ATVPDKIKX0DER")
    participation_b = _seed_participation(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2")
    run_for_a_only = _enqueue_and_claim(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, participation_ids=[participation_a])

    with Session(engine) as session:
        session.add(AmazonSellerOrder(marketplace_participation_id=participation_b, amazon_order_id="902-CROSS-RUN", last_ingestion_run_id=run_for_a_only))
        with pytest.raises(IntegrityError):
            session.commit()


def test_order_item_natural_key_is_order_and_amazon_order_item_id(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "own_item_natural_key")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(**_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-ITEM-TEST"))
        session.commit()
        order_id = order.id
    with Session(engine) as session:
        AmazonSellerOrderItemRepository(session).upsert(
            organization_id=org_id, marketplace_participation_id=participation_id, order_id=order_id,
            amazon_order_item_id="10000000000001", seller_sku="SKU-A", asin="B0TEST0001", item_name=None,
            condition_type=None, quantity_ordered=1, quantity_fulfilled=None, quantity_unfulfilled=None,
            unit_price_amount=None, unit_price_currency=None, item_proceeds_amount=None, item_proceeds_currency=None,
            last_ingestion_run_id=run_id,
        )
        session.commit()
    with Session(engine) as session:
        AmazonSellerOrderItemRepository(session).upsert(
            organization_id=org_id, marketplace_participation_id=participation_id, order_id=order_id,
            amazon_order_item_id="10000000000002", seller_sku="SKU-A", asin="B0TEST0001", item_name=None,
            condition_type=None, quantity_ordered=1, quantity_fulfilled=None, quantity_unfulfilled=None,
            unit_price_amount=None, unit_price_currency=None, item_proceeds_amount=None, item_proceeds_currency=None,
            last_ingestion_run_id=run_id,
        )
        session.commit()  # must not raise — same SKU/ASIN, different item id
    with Session(engine) as session:
        assert len(session.query(AmazonSellerOrderItem).filter_by(order_id=order_id).all()) == 2


def test_duplicate_order_item_converges(tmp_path) -> None:
    engine, org_id, _, participation_id, run_id = _order_repo_context(tmp_path, "own_item_converge")
    with Session(engine) as session:
        order = AmazonSellerOrderRepository(session).upsert(**_order_kwargs(org_id, participation_id, run_id, amazon_order_id="902-ITEM-CONVERGE"))
        session.commit()
        order_id = order.id
    for quantity_fulfilled in (0, 1):
        with Session(engine) as session:
            AmazonSellerOrderItemRepository(session).upsert(
                organization_id=org_id, marketplace_participation_id=participation_id, order_id=order_id,
                amazon_order_item_id="10000000000099", seller_sku="SKU-CONVERGE", asin=None, item_name=None,
                condition_type=None, quantity_ordered=1, quantity_fulfilled=quantity_fulfilled,
                quantity_unfulfilled=1 - quantity_fulfilled, unit_price_amount=None, unit_price_currency=None,
                item_proceeds_amount=None, item_proceeds_currency=None, last_ingestion_run_id=run_id,
            )
            session.commit()
    with Session(engine) as session:
        items = session.query(AmazonSellerOrderItem).filter_by(order_id=order_id).all()
        assert len(items) == 1
        assert items[0].quantity_fulfilled == 1


def test_newly_added_participation_has_no_checkpoint_and_does_not_inherit_history(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "own_new_participation_no_checkpoint")
    org_id, seller_account_id, connection_id, participation_existing = _full_scope(engine)
    run_id = _enqueue_and_claim(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, participation_ids=[participation_existing])
    _finalize(engine, organization_id=org_id, seller_account_id=seller_account_id, run_id=run_id, participation_watermarks={participation_existing: datetime.now(UTC)})

    participation_new = _seed_participation(engine, organization_id=org_id, seller_account_id=seller_account_id, connection_id=connection_id, marketplace_id="A2EUQ1WTGCTBG2")
    with Session(engine) as session:
        assert AmazonOrdersSyncCheckpointRepository(session).get(org_id, participation_new) is None


# --- privacy: forbidden columns absent ---------------------------------------

_FORBIDDEN_SUBSTRINGS = (
    "buyer", "recipient", "address", "email", "phone", "gift", "payment",
    "tax_registration", "customiz", "cancel_reason", "raw_payload", "raw_order", "raw_item",
)


def _assert_no_forbidden_columns(table_name: str) -> None:
    columns = {c.name.lower() for c in Base.metadata.tables[table_name].columns}
    for column in columns:
        for forbidden in _FORBIDDEN_SUBSTRINGS:
            assert forbidden not in column, f"{table_name}.{column} looks PII/raw-payload-shaped ({forbidden!r})"


def test_amazon_seller_orders_has_no_pii_or_raw_payload_columns() -> None:
    _assert_no_forbidden_columns("amazon_seller_orders")


def test_amazon_seller_order_items_has_no_pii_or_raw_payload_columns() -> None:
    _assert_no_forbidden_columns("amazon_seller_order_items")


def test_amazon_orders_sync_checkpoints_has_no_pii_or_raw_payload_columns() -> None:
    _assert_no_forbidden_columns("amazon_orders_sync_checkpoints")


def test_amazon_ingestion_run_marketplace_participations_has_no_pii_columns() -> None:
    _assert_no_forbidden_columns("amazon_ingestion_run_marketplace_participations")


def test_no_generic_json_column_exists_on_any_new_orders_table() -> None:
    from sqlalchemy.types import JSON

    for table_name in (
        "amazon_seller_orders", "amazon_seller_order_items",
        "amazon_orders_sync_checkpoints", "amazon_ingestion_run_marketplace_participations",
    ):
        for column in Base.metadata.tables[table_name].columns:
            assert not isinstance(column.type, JSON), f"{table_name}.{column.name} is a JSON column"


def test_repository_upsert_signatures_have_no_gift_message_or_cancel_reason_parameter() -> None:
    import inspect as _inspect

    order_params = set(_inspect.signature(AmazonSellerOrderRepository.upsert).parameters)
    item_params = set(_inspect.signature(AmazonSellerOrderItemRepository.upsert).parameters)
    for params in (order_params, item_params):
        for forbidden in ("gift_message", "cancel_reason", "cancellation_reason", "raw_payload", "raw_response"):
            assert forbidden not in params


def test_new_orders_tables_match_orm_metadata_exactly(tmp_path) -> None:
    engine = _dedicated_engine(tmp_path, "orm_parity")
    inspector = inspect(engine)
    for table_name in (
        "amazon_seller_orders", "amazon_seller_order_items",
        "amazon_ingestion_run_marketplace_participations", "amazon_orders_sync_checkpoints",
    ):
        reflected = {c["name"] for c in inspector.get_columns(table_name)}
        orm_columns = set(Base.metadata.tables[table_name].columns.keys())
        assert reflected == orm_columns, table_name

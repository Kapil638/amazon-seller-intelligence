"""12B.4D — AmazonOrdersSyncTriggerService. Durable-job version: `trigger()`
never calls Amazon and never blocks — it only enqueues or reports the
existing durable `run_type='orders'` job row. No live Amazon call anywhere
in this file. The ingestion service's own pagination/attribution/
finalization behavior is covered exhaustively in
`test_amazon_orders_ingestion_service.py` and is not re-tested here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.amazon.orders_sync import AmazonOrdersSyncTriggerService
from app.core.config import Settings
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunMarketplaceParticipationRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"
MARKETPLACE_2 = "A2EUQ1WTGCTBG2"


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        orders_sync_trigger_cooldown_seconds=0,
        orders_sync_max_concurrent_jobs_per_organization=10,
        orders_sync_max_global_concurrent_jobs=10,
    )
    base.update(overrides)
    return Settings(**base)


def _seed_scope(*, participation_active: bool = True, seller_status: str = "active") -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1B2C3D4E5F6G7"
        )
        seller_account.status = seller_status
        session.flush()
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller_account.id,
            marketplace_id=MARKETPLACE,
            region="na",
            connection_id=connection.id,
        )
        participation.is_active = participation_active
        session.flush()
        return {
            "organization_id": org_id,
            "seller_account_id": seller_account.id,
            "marketplace_participation_id": participation.id,
            "connection_id": connection.id,
        }


def _get_run(run_id):
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


# --- ownership / tenancy --------------------------------------------------


def test_nonexistent_participation_returns_scope_not_found() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[uuid4()])
    assert outcome.reason == "scope_not_found"
    assert outcome.job is None


def test_nonexistent_seller_account_returns_scope_not_found() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=uuid4(), marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert outcome.reason == "scope_not_found"


def test_inactive_participation_returns_scope_inactive() -> None:
    scope = _seed_scope(participation_active=False)
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert outcome.reason == "scope_inactive"


def test_inactive_seller_account_returns_scope_inactive() -> None:
    scope = _seed_scope(seller_status="disconnected")
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert outcome.reason == "scope_inactive"


# --- enqueue / already-running / duplicate-trigger prevention ------------


def test_new_trigger_enqueues_a_queued_job() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert outcome.reason == "queued"
    assert outcome.job is not None
    assert outcome.job.status == "queued"
    assert outcome.job.marketplace_participation_ids == (scope["marketplace_participation_id"],)
    run = _get_run(outcome.job.run_id)
    assert run.run_type == "orders"


def test_second_trigger_while_queued_returns_already_running_with_same_run_id() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    first = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    second = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert second.reason == "already_running"
    assert second.job.run_id == first.job.run_id


def test_trigger_while_started_returns_already_running() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    first = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    with session_scope() as session:
        AmazonIngestionRunMarketplaceParticipationRepository(session).claim_orders_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            region="na",
            environment="PRODUCTION",
            lease_owner="test-lease",
            lease_duration_seconds=300,
        )
    second = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert second.reason == "already_running"
    assert second.job.run_id == first.job.run_id
    assert second.job.status == "started"


def test_repeated_request_after_a_run_succeeds_can_enqueue_again() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    first = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    with session_scope() as session:
        row = session.get(AmazonIngestionRun, first.job.run_id)
        row.status = "succeeded"
        row.completed_at = datetime.now(UTC) - timedelta(hours=1)
        row.lease_owner = None
        row.lease_expires_at = None
        session.commit()

    second = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert second.reason == "queued"
    assert second.job.run_id != first.job.run_id


# --- cooldown --------------------------------------------------------------


def test_cooldown_blocks_a_trigger_immediately_after_a_recent_completed_run() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings(orders_sync_trigger_cooldown_seconds=300))
    first = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    with session_scope() as session:
        row = session.get(AmazonIngestionRun, first.job.run_id)
        row.status = "succeeded"
        row.completed_at = datetime.now(UTC)
        row.lease_owner = None
        row.lease_expires_at = None
        session.commit()

    second = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert second.reason == "cooldown"
    assert second.retry_allowed_at is not None


def test_no_cooldown_configured_allows_immediate_re_trigger() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings(orders_sync_trigger_cooldown_seconds=0))
    first = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    with session_scope() as session:
        row = session.get(AmazonIngestionRun, first.job.run_id)
        row.status = "succeeded"
        row.completed_at = datetime.now(UTC)
        row.lease_owner = None
        row.lease_expires_at = None
        session.commit()

    second = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    assert second.reason == "queued"


# --- queue backlog safety valve ---------------------------------------------


def test_queue_backlog_limit_blocks_a_new_job_once_the_organizations_queue_itself_is_full() -> None:
    scope1 = _seed_scope()
    org_id = scope1["organization_id"]
    service = AmazonOrdersSyncTriggerService(settings=_settings(orders_sync_max_queued_per_organization=1))
    outcome1 = service.trigger(
        seller_account_id=scope1["seller_account_id"], marketplace_participation_ids=[scope1["marketplace_participation_id"]]
    )
    assert outcome1.reason == "queued"

    with session_scope() as session:
        participation2 = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=scope1["seller_account_id"],
            marketplace_id=MARKETPLACE_2,
            region="na",
            connection_id=scope1["connection_id"],
        )
        session.flush()
        seller2 = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A_SECOND_SELLER_ACCT"
        )
        session.flush()
        participation3 = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id,
            seller_account_id=seller2.id,
            marketplace_id=MARKETPLACE,
            region="na",
            connection_id=scope1["connection_id"],
        )
        session.flush()
        seller2_id = seller2.id
        participation3_id = participation3.id

    outcome2 = service.trigger(seller_account_id=seller2_id, marketplace_participation_ids=[participation3_id])
    assert outcome2.reason == "queue_backlog_limit_reached"


# --- get_status --------------------------------------------------------------


def test_get_status_returns_none_for_nonexistent_run() -> None:
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    assert service.get_status(uuid4()) is None


def test_get_status_returns_the_job_for_a_valid_run() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    status = service.get_status(outcome.job.run_id)
    assert status is not None
    assert status.run_id == outcome.job.run_id
    assert status.run_type == "orders"


def test_get_status_returns_none_for_a_run_belonging_to_a_different_organization() -> None:
    """`current_organization_id()` is fixed per test-process configuration
    (this codebase has no dynamic "switch organization" mechanism) — the
    foreign-row case is exercised the same way as the analogous Listings
    test suite's cross-organization tests: directly re-pointing an
    already-created row's `organization_id` at a different, real
    organization, then proving `get_status` (which always checks against
    the caller's own, unchanged `current_organization_id()`) can no
    longer see it."""
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    run_id = outcome.job.run_id
    with session_scope() as session:
        from app.persistence.models import Organization

        other_org_id = uuid4()
        session.add(Organization(id=other_org_id, name="Other Org"))
        session.flush()
        row = session.get(AmazonIngestionRun, run_id)
        row.organization_id = other_org_id
        session.commit()

    assert service.get_status(run_id) is None


def test_get_status_never_exposes_forbidden_fields() -> None:
    scope = _seed_scope()
    service = AmazonOrdersSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_ids=[scope["marketplace_participation_id"]]
    )
    dumped = outcome.job.model_dump()
    forbidden = {"organization_id", "seller_account_id", "connection_id", "lease_owner", "token_reference", "pagination_token"}
    assert forbidden.isdisjoint(dumped.keys())

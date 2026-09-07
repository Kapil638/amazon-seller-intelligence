"""12B.6B — `AmazonInventorySyncTriggerService`. No live Amazon call: this
service never resolves secrets or calls Amazon itself — it only validates
ownership/eligibility and enqueues a durable job row."""

from __future__ import annotations

from uuid import uuid4

from app.amazon.inventory_sync import AmazonInventorySyncTriggerService
from app.core.config import Settings
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _test_settings(**overrides) -> Settings:
    fields = dict(
        sp_api_lwa_client_id="x", sp_api_lwa_client_secret="x",
        sp_api_production_lwa_client_id="x", sp_api_production_lwa_client_secret="x",
        inventory_sync_trigger_cooldown_seconds=300,
        inventory_sync_max_queued_per_organization=25,
    )
    fields.update(overrides)
    return Settings(_env_file=None, **fields)


def _seed_scope(*, active_participation: bool = True, active_seller: bool = True) -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        if not active_seller:
            seller_account.status = "disconnected"
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
            connection_id=connection.id,
        )
        if not active_participation:
            participation.is_active = False
        session.flush()
        return {
            "org_id": org_id,
            "seller_account_id": seller_account.id,
            "participation_id": participation.id,
            "connection_id": connection.id,
        }


def _get_run(run_id) -> AmazonIngestionRun:
    with session_scope() as session:
        return session.get(AmazonIngestionRun, run_id)


def test_trigger_enqueues_a_new_job() -> None:
    scope = _seed_scope()
    service = AmazonInventorySyncTriggerService(settings=_test_settings())

    outcome = service.trigger(scope["participation_id"])

    assert outcome.reason == "queued"
    assert outcome.job is not None
    run = _get_run(outcome.job.run_id)
    assert run.run_type == "inventory"
    assert run.status == "queued"


def test_trigger_returns_scope_not_found_for_foreign_participation() -> None:
    service = AmazonInventorySyncTriggerService(settings=_test_settings())
    outcome = service.trigger(uuid4())
    assert outcome.reason == "scope_not_found"
    assert outcome.job is None


def test_trigger_returns_scope_inactive_for_inactive_participation() -> None:
    scope = _seed_scope(active_participation=False)
    service = AmazonInventorySyncTriggerService(settings=_test_settings())
    outcome = service.trigger(scope["participation_id"])
    assert outcome.reason == "scope_inactive"


def test_trigger_returns_scope_inactive_for_inactive_seller_account() -> None:
    scope = _seed_scope(active_seller=False)
    service = AmazonInventorySyncTriggerService(settings=_test_settings())
    outcome = service.trigger(scope["participation_id"])
    assert outcome.reason == "scope_inactive"


def test_trigger_returns_already_running_for_a_second_call() -> None:
    scope = _seed_scope()
    service = AmazonInventorySyncTriggerService(settings=_test_settings())

    first = service.trigger(scope["participation_id"])
    second = service.trigger(scope["participation_id"])

    assert first.reason == "queued"
    assert second.reason == "already_running"
    assert second.job.run_id == first.job.run_id


def test_trigger_respects_cooldown_after_a_completed_run() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"],
        )
        claimed_row = AmazonIngestionRunRepository(session).claim_next_inventory_job(
            lease_owner="test", lease_duration_seconds=300, max_global_active=10, max_active_per_organization=10
        )
        AmazonIngestionRunRepository(session).complete_inventory_run(
            scope["org_id"], claimed_row.id, lease_owner=claimed_row.lease_owner, status="succeeded",
            pagination_complete=True,
        )

    service = AmazonInventorySyncTriggerService(settings=_test_settings(inventory_sync_trigger_cooldown_seconds=300))
    outcome = service.trigger(scope["participation_id"])

    assert outcome.reason == "cooldown"
    assert outcome.retry_allowed_at is not None


def test_trigger_respects_queue_backlog_limit() -> None:
    scope = _seed_scope()
    service = AmazonInventorySyncTriggerService(settings=_test_settings(inventory_sync_max_queued_per_organization=1))
    # First trigger succeeds and occupies the only queue slot for this
    # participation's single-writer scope; simulate backlog via a second
    # unrelated participation under the same organization.
    first = service.trigger(scope["participation_id"])
    assert first.reason == "queued"

    with session_scope() as session:
        participation_2 = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A1PA6795UKMFR9", region="na", connection_id=scope["connection_id"],
        )
        session.flush()
        participation_2_id = participation_2.id

    second = service.trigger(participation_2_id)
    assert second.reason == "queue_backlog_limit_reached"


def test_get_status_returns_none_for_foreign_run() -> None:
    scope = _seed_scope()
    service = AmazonInventorySyncTriggerService(settings=_test_settings())
    status = service.get_status(scope["participation_id"], uuid4())
    assert status is None


def test_get_status_returns_job_for_own_run() -> None:
    scope = _seed_scope()
    service = AmazonInventorySyncTriggerService(settings=_test_settings())
    outcome = service.trigger(scope["participation_id"])

    status = service.get_status(scope["participation_id"], outcome.job.run_id)

    assert status is not None
    assert status.run_id == outcome.job.run_id
    assert status.status == "queued"

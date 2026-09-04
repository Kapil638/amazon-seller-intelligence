"""12B.6A — AmazonSalesTrafficSyncTriggerService. Durable-job version:
`trigger()` never calls Amazon and never blocks — it only enqueues or
reports the existing durable `run_type='sales_and_traffic_report'` job
row. No live Amazon call anywhere in this file. The ingestion service's
own report-lifecycle/persistence behavior is covered exhaustively in
`test_amazon_sales_traffic_ingestion.py` and is not re-tested here.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from app.amazon.sales_traffic_sync import AmazonSalesTrafficSyncTriggerService
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
DAY = date(2026, 8, 1)


def _settings(**overrides) -> Settings:
    base = dict(_env_file=None, sales_traffic_sync_trigger_cooldown_seconds=0)
    base.update(overrides)
    return Settings(**base)


def _seed_scope(*, connection_status: str = "connected") -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        connection = AmazonConnectionRepository(session).create(
            organization_id=org_id, provider="SP_API", environment="PRODUCTION", region="na"
        )
        connection.token_reference = f"asi-amazon-secret:{uuid4().hex}"
        connection.status = connection_status
        session.flush()
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="A1B2C3D4E5F6G7"
        )
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
            connection_id=connection.id,
        )
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


# --- ownership / tenancy ----------------------------------------------------


def test_nonexistent_participation_returns_scope_not_found() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"], marketplace_participation_id=uuid4(),
        data_start_time=DAY, data_end_time=DAY,
    )
    assert outcome.reason == "scope_not_found"
    assert outcome.job is None


def test_nonexistent_seller_account_returns_scope_not_found() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=uuid4(), marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    assert outcome.reason == "scope_not_found"


def test_participation_belonging_to_different_seller_account_returns_scope_not_found() -> None:
    scope = _seed_scope()
    org_id = scope["organization_id"]
    with session_scope() as session:
        other_seller = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id="AOTHERSELLER01"
        )
        session.flush()
        other_seller_id = other_seller.id
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=other_seller_id, marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    assert outcome.reason == "scope_not_found"


def test_inactive_connection_returns_scope_inactive() -> None:
    scope = _seed_scope(connection_status="degraded")
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    assert outcome.reason == "scope_inactive"


# --- request validation ------------------------------------------------------


def test_start_after_end_returns_invalid_request() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=date(2026, 8, 2), data_end_time=date(2026, 8, 1),
    )
    assert outcome.reason == "invalid_request"


def test_unsupported_granularity_returns_invalid_request() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY, date_granularity="YEAR",
    )
    assert outcome.reason == "invalid_request"


# --- happy path / duplicate / cooldown ---------------------------------------


def test_first_trigger_enqueues_a_queued_job() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    assert outcome.reason == "queued"
    assert outcome.job is not None
    assert outcome.job.status == "queued"
    assert outcome.job.data_start_time == DAY
    run = _get_run(outcome.job.run_id)
    assert run.run_type == "sales_and_traffic_report"


def test_second_trigger_while_first_is_active_returns_already_running() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    first = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    assert first.reason == "queued"

    second = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=date(2026, 8, 2), data_end_time=date(2026, 8, 2),
    )
    assert second.reason == "already_running"
    assert second.job.run_id == first.job.run_id


def test_cooldown_blocks_a_trigger_immediately_after_a_completed_run() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings(sales_traffic_sync_trigger_cooldown_seconds=300))
    first = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, first.job.run_id)
        run.status = "succeeded"
        run.lease_owner = None
        run.completed_at = datetime.now(UTC)
        session.flush()

    second = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=date(2026, 8, 2), data_end_time=date(2026, 8, 2),
    )
    assert second.reason == "cooldown"
    assert second.retry_allowed_at is not None


def test_get_status_returns_none_for_a_foreign_run() -> None:
    scope = _seed_scope()
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    outcome = service.trigger(
        seller_account_id=scope["seller_account_id"],
        marketplace_participation_id=scope["marketplace_participation_id"],
        data_start_time=DAY, data_end_time=DAY,
    )
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, outcome.job.run_id)
        run.organization_id = uuid4()
        session.flush()

    assert service.get_status(outcome.job.run_id) is None


def test_get_status_rejects_a_non_sales_traffic_run_type() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        run = AmazonIngestionRunRepository(session).start(
            organization_id=scope["organization_id"], domain="listings_items", region="na", environment="PRODUCTION",
        )
        run_id = run.id
    service = AmazonSalesTrafficSyncTriggerService(settings=_settings())
    assert service.get_status(run_id) is None

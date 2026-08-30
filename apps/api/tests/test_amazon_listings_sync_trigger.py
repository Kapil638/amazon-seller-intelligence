"""12B.3G — AmazonListingsSyncTriggerService. Durable-job version: `trigger()`
never calls Amazon and never blocks — it only enqueues or reports the
existing durable `run_type='listings'` job row. No live Amazon call
anywhere in this file (there is no Amazon client in the trigger's own
code path at all). The ingestion service's own pagination/normalization/
reconciliation behavior is covered exhaustively in
`test_amazon_listings_ingestion_service.py` and is not re-tested here;
the durable worker's own claim/retry behavior is covered in
`test_amazon_listings_job_lifecycle.py` and `test_amazon_listings_worker.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from app.amazon.listings_sync import AmazonListingsSyncTriggerService
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


def _settings(**overrides) -> Settings:
    base = dict(
        _env_file=None,
        listings_sync_trigger_cooldown_seconds=0,
        listings_sync_max_concurrent_jobs_per_organization=10,
        listings_sync_max_global_concurrent_jobs=10,
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


# --- ownership / tenancy ----------------------------------------------


def test_nonexistent_participation_returns_scope_not_found() -> None:
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(uuid4())
    assert outcome.reason == "scope_not_found"
    assert outcome.job is None


def test_foreign_and_nonexistent_participation_are_indistinguishable() -> None:
    from app.persistence.models import Organization

    _seed_scope()
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other Org"))
    with session_scope() as session:
        foreign_seller = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=other_org, selling_partner_id="A9Z9Z9Z9Z9Z9Z9"
        )
        foreign_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=other_org,
            seller_account_id=foreign_seller.id,
            marketplace_id=MARKETPLACE,
            region="na",
        ).id

    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    foreign_outcome = trigger.trigger(foreign_participation)
    nonexistent_outcome = trigger.trigger(uuid4())

    assert foreign_outcome.reason == "scope_not_found"
    assert nonexistent_outcome.reason == "scope_not_found"
    assert foreign_outcome.job is None
    assert nonexistent_outcome.job is None


def test_inactive_participation_returns_scope_inactive_without_creating_a_run() -> None:
    scope = _seed_scope(participation_active=False)
    trigger = AmazonListingsSyncTriggerService(settings=_settings())

    outcome = trigger.trigger(scope["marketplace_participation_id"])

    assert outcome.reason == "scope_inactive"
    assert outcome.job is None
    with session_scope() as session:
        runs = AmazonIngestionRunRepository(session).get_latest_listings_run(
            scope["organization_id"], scope["marketplace_participation_id"]
        )
        assert runs is None


def test_inactive_seller_account_returns_scope_inactive() -> None:
    scope = _seed_scope(seller_status="disconnected")
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "scope_inactive"


# --- successful enqueue -----------------------------------------------


def test_new_trigger_enqueues_a_queued_job() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())

    outcome = trigger.trigger(scope["marketplace_participation_id"])

    assert outcome.reason == "queued"
    assert outcome.job is not None
    assert outcome.job.status == "queued"
    assert outcome.job.run_type == "listings"
    assert outcome.job.marketplace_participation_id == scope["marketplace_participation_id"]
    assert outcome.job.started_at is None
    assert outcome.job.attempt_count == 0
    assert outcome.job.pages_fetched == 0


def test_enqueued_job_is_bound_to_the_connection_and_region() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])

    with session_scope() as session:
        row = session.get(AmazonIngestionRun, outcome.job.run_id)
        assert row.region == "na"
        assert row.environment == "PRODUCTION"
        assert row.connection_id is not None


# --- already running / races --------------------------------------------


def test_second_trigger_while_queued_returns_already_running_with_same_run_id() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    first = trigger.trigger(scope["marketplace_participation_id"])
    second = trigger.trigger(scope["marketplace_participation_id"])

    assert first.reason == "queued"
    assert second.reason == "already_running"
    assert second.job.run_id == first.job.run_id


def test_trigger_while_started_returns_already_running() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )

    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])

    assert outcome.reason == "already_running"
    assert outcome.job.run_id == claim.run_id
    assert outcome.job.status == "started"


def test_trigger_while_waiting_to_retry_returns_already_running() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        AmazonIngestionRunRepository(session).reschedule_listings_run_for_retry(
            scope["organization_id"], claim.run_id, lease_owner="worker-1",
            next_retry_at=datetime.now(UTC) + timedelta(minutes=5), failure_class="throttled",
        )

    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])

    assert outcome.reason == "already_running"
    assert outcome.job.status == "waiting_to_retry"


def test_repeated_request_after_a_run_succeeds_can_enqueue_again() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["organization_id"], claim.run_id, lease_owner="worker-1", status="succeeded",
        )

    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "queued"


# --- cooldown -------------------------------------------------------------


def test_cooldown_blocks_a_trigger_immediately_after_a_recent_completed_run() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["organization_id"], claim.run_id, lease_owner="worker-1", status="succeeded",
        )

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=3600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "cooldown"


def test_no_cooldown_configured_allows_immediate_re_trigger() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["organization_id"], claim.run_id, lease_owner="worker-1", status="succeeded",
        )

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=0))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "queued"


# --- admission control: per-organization / global limits ------------------


def test_a_busy_worker_execution_capacity_never_blocks_a_legitimate_new_enqueue() -> None:
    """Regression: an earlier version of this admission check used
    `listings_sync_max_concurrent_jobs_per_organization`/
    `listings_sync_max_global_concurrent_jobs` (worker EXECUTION capacity)
    to gate the TRIGGER itself — meaning a legitimate new job could be
    rejected outright just because another job happened to be `started`,
    even though nothing about the queue itself was actually full. Both
    settings are pinned to 1 here (their tightest legal value) and an
    existing `started` job already occupies that capacity — the new job
    for a *different* participation must still be accepted as `queued`."""
    scope = _seed_scope()
    with session_scope() as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )

    # A second, distinct participation for the same organization.
    with session_scope() as session:
        other_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2",
            region="eu",
            connection_id=scope["connection_id"],
        ).id

    trigger = AmazonListingsSyncTriggerService(
        settings=_settings(
            listings_sync_max_concurrent_jobs_per_organization=1,
            listings_sync_max_global_concurrent_jobs=1,
        )
    )
    outcome = trigger.trigger(other_participation)
    assert outcome.reason == "queued"
    assert outcome.job.status == "queued"


def test_queue_backlog_limit_blocks_a_new_job_once_the_organizations_queue_itself_is_full() -> None:
    """The ONLY admission-time capacity check: a genuine backlog of
    already-`queued` (never `started`) jobs for this organization. Two
    distinct participations are pre-queued (limit=2); a third must be
    rejected with the sanitized `queue_backlog_limit_reached` reason —
    never `organization_limit_reached`/`capacity_exhausted`, which no
    longer exist as trigger outcomes at all."""
    scope = _seed_scope()
    settings = _settings(listings_sync_max_queued_per_organization=2)
    trigger = AmazonListingsSyncTriggerService(settings=settings)
    first = trigger.trigger(scope["marketplace_participation_id"])
    assert first.reason == "queued"

    with session_scope() as session:
        participation_b = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["organization_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2", region="eu", connection_id=scope["connection_id"],
        ).id
        participation_c = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["organization_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A1PA6795UKMFR9", region="de", connection_id=scope["connection_id"],
        ).id

    second = trigger.trigger(participation_b)
    assert second.reason == "queued"  # backlog now at the configured limit (2)

    third = trigger.trigger(participation_c)
    assert third.reason == "queue_backlog_limit_reached"
    assert third.job is None


# --- get_status -------------------------------------------------------


def test_get_status_returns_none_for_nonexistent_run() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    assert trigger.get_status(scope["marketplace_participation_id"], uuid4()) is None


def test_get_status_returns_none_for_a_run_belonging_to_a_different_participation() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        other_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2",
            region="eu",
            connection_id=scope["connection_id"],
        ).id

    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])

    assert trigger.get_status(other_participation, outcome.job.run_id) is None


def test_get_status_returns_none_for_nonexistent_participation() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert trigger.get_status(uuid4(), outcome.job.run_id) is None


def test_get_status_returns_the_job_for_a_valid_run() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])

    status = trigger.get_status(scope["marketplace_participation_id"], outcome.job.run_id)
    assert status is not None
    assert status.run_id == outcome.job.run_id
    assert status.status == "queued"


def test_get_status_never_exposes_forbidden_fields() -> None:
    scope = _seed_scope()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    status = trigger.get_status(scope["marketplace_participation_id"], outcome.job.run_id)

    dumped = status.model_dump()
    forbidden = {
        "organization_id", "seller_account_id", "connection_id", "lease_owner",
        "token_reference", "refresh_token", "access_token", "client_secret", "page_token",
    }
    assert forbidden.isdisjoint(dumped.keys())


def test_get_status_never_mutates_a_queued_run_no_matter_how_stale() -> None:
    """12B.3G follow-up: `GET .../listings/sync/{run_id}` must be a pure
    read, even repeatedly, against an old, never-claimed `queued` run."""
    scope = _seed_scope()
    with session_scope() as session:
        old_run = AmazonIngestionRun(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            run_type="listings",
            domain="listings_items",
            region="na",
            environment="PRODUCTION",
            status="queued",
            created_at=datetime.now(UTC) - timedelta(days=30),
        )
        session.add(old_run)
        session.flush()
        run_id = old_run.id

    def snapshot():
        with session_scope() as session:
            row = session.get(AmazonIngestionRun, run_id)
            return (row.status, row.lease_owner, row.lease_expires_at, row.started_at, row.retry_count)

    before = snapshot()
    trigger = AmazonListingsSyncTriggerService(settings=_settings())
    for _ in range(3):
        status = trigger.get_status(scope["marketplace_participation_id"], run_id)
        assert status is not None
        assert status.status == "queued"
    assert snapshot() == before

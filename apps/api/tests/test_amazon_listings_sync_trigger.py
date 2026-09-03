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


def test_cooldown_response_includes_retry_allowed_at() -> None:
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
        created_at = session.get(AmazonIngestionRun, claim.run_id).created_at
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["organization_id"], claim.run_id, lease_owner="worker-1", status="succeeded",
        )
        completed_at = session.get(AmazonIngestionRun, claim.run_id).completed_at

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "cooldown"
    assert outcome.retry_allowed_at is not None
    # Anchored to completed_at, not created_at — asserted against both so
    # a regression back to created_at would be caught even though the two
    # happen to be milliseconds apart in this fast test (see the dedicated
    # anchoring test below for a version with a real gap between them).
    expected = completed_at.replace(tzinfo=completed_at.tzinfo or UTC) + timedelta(seconds=600)
    assert abs((outcome.retry_allowed_at - expected).total_seconds()) < 2


def test_cooldown_retry_allowed_at_is_anchored_to_completed_at_not_created_at() -> None:
    """The cooldown must pace against when the last real Amazon attempt
    *finished*, not when it was merely queued — a job that sits queued
    for a while before a worker claims it would otherwise let its
    cooldown clock run out before the real call it's meant to pace
    against has even happened. `created_at` and `completed_at` are
    forced deliberately far apart here so any regression back to
    anchoring on `created_at` fails by minutes, not milliseconds."""
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
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.created_at = datetime.now(UTC) - timedelta(minutes=20)
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["organization_id"], claim.run_id, lease_owner="worker-1", status="succeeded",
        )
        completed_at = session.get(AmazonIngestionRun, claim.run_id).completed_at

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "cooldown"
    expected_from_completed_at = completed_at.replace(tzinfo=completed_at.tzinfo or UTC) + timedelta(seconds=600)
    assert abs((outcome.retry_allowed_at - expected_from_completed_at).total_seconds()) < 2
    # If this were still anchored to created_at (20 minutes earlier plus a
    # 600s/10-minute cooldown), retry_allowed_at would already be roughly
    # 10 minutes in the *past* relative to now — nowhere near the
    # completed_at-anchored value asserted above.
    assert outcome.retry_allowed_at > datetime.now(UTC)


def test_cooldown_falls_back_to_created_at_when_completed_at_is_missing() -> None:
    """Documented legacy-data fallback: every real terminal-transition
    path in this codebase (`complete_listings_run`, the stale-lease
    `timed_out` reclaim, `terminalize_unclaimed_listings_run`) always
    sets `completed_at`, so this scenario cannot occur through any
    current write path — it is simulated directly to prove the fallback
    itself, not to claim it happens in practice."""
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
    with session_scope() as session:
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.completed_at = None  # simulates legacy data missing this column's value
        created_at = row.created_at

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "cooldown"
    expected_from_created_at = created_at.replace(tzinfo=created_at.tzinfo or UTC) + timedelta(seconds=600)
    assert abs((outcome.retry_allowed_at - expected_from_created_at).total_seconds()) < 2


# --- regression: a `cancelled_before_start` row must never defeat the -----
# --- cooldown for a genuinely newer, real run (production incident) -------


def test_an_old_cancelled_before_start_row_does_not_hide_a_newer_real_run_from_cooldown() -> None:
    """Motivated by the production incident, but this specific assertion
    is dialect-independent by design: `get_latest_cooldown_relevant_
    listings_run` excludes `cancelled_before_start` rows via a `WHERE`
    filter, not merely by out-ranking them in `ORDER BY` — so this passes
    regardless of a database's null-ordering default. The production
    defect itself (an old `cancelled_before_start` row's `NULL started_at`
    silently outranking a newer real row under PostgreSQL's `NULLS FIRST`
    for `DESC`) can only be reproduced against real PostgreSQL — see
    `test_get_latest_listings_run_is_not_fooled_by_a_null_started_at_row`
    in `tests/postgres/test_disposable_postgres_listings_sync_trigger_
    concurrency.py`. This test instead proves the resulting business
    behavior: a days-old cancelled row must never suppress the cooldown
    that a real, recent run should impose."""
    scope = _seed_scope()
    with session_scope() as session:
        cancelled_claim = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
        )
        row = session.get(AmazonIngestionRun, cancelled_claim.run_id)
        row.created_at = datetime.now(UTC) - timedelta(days=3)
    with session_scope() as session:
        AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            scope["organization_id"], cancelled_claim.run_id
        )

    with session_scope() as session:
        real_claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        AmazonIngestionRunRepository(session).complete_listings_run(
            scope["organization_id"], real_claim.run_id, lease_owner="worker-1", status="succeeded",
        )

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=3600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "cooldown"
    assert outcome.job is not None
    assert outcome.job.run_id == real_claim.run_id  # the real run, not the 3-day-old cancelled one


# --- regression: a non-terminal sibling row must never be cooldown-relevant
# (real-PostgreSQL concurrency incident: tests/postgres/test_disposable_
# postgres_listings_sync_trigger_concurrency.py::
# test_ten_concurrent_triggers_create_at_most_one_job) ----------------------


def test_a_queued_sibling_run_is_never_cooldown_relevant() -> None:
    """`get_latest_cooldown_relevant_listings_run` must only ever consider
    terminal runs. A `queued` row — including one a concurrent trigger
    call just created, an instant before this call's own transaction
    reads it under real PostgreSQL's row-visibility timing — has no
    `completed_at` yet, so before this fix its `created_at` fallback
    computed a fresh cooldown window against a job that made no real
    Amazon call at all, causing a losing concurrent caller to resolve to
    `reason="cooldown"` instead of truthfully reporting the winner's job
    as `already_running`. Proven directly at the repository layer
    (dialect-independent — no NULLS-ordering dependency, unlike the
    `cancelled_before_start` ordering defect) rather than by trying to
    force the actual thread interleaving, which real concurrency alone
    can produce; the real-PostgreSQL concurrency test proves the full
    race is closed."""
    scope = _seed_scope()
    with session_scope() as session:
        AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
        )
    with session_scope() as session:
        latest = AmazonIngestionRunRepository(session).get_latest_cooldown_relevant_listings_run(
            scope["organization_id"], scope["marketplace_participation_id"]
        )
        assert latest is None

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=3600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    # The pre-existing active-run check catches this queued row first in
    # a normal (non-raced) call — this end-to-end assertion documents
    # that the caller-visible behavior for a plain repeat trigger was
    # already correct; the bug was specific to the race window where the
    # active-run check runs before the sibling row exists but the
    # cooldown check runs after.
    assert outcome.reason == "already_running"


def test_a_started_sibling_run_is_never_cooldown_relevant() -> None:
    """Same defect, `started` status: a claimed-but-not-yet-completed run
    has no `completed_at` either, and is just as non-terminal as
    `queued` — must never be read as a cooldown anchor."""
    scope = _seed_scope()
    with session_scope() as session:
        AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        latest = AmazonIngestionRunRepository(session).get_latest_cooldown_relevant_listings_run(
            scope["organization_id"], scope["marketplace_participation_id"]
        )
        assert latest is None


def test_a_waiting_to_retry_sibling_run_is_never_cooldown_relevant() -> None:
    """Same defect, `waiting_to_retry` status — simulated directly since
    reaching it through the real retry-reschedule path requires a failed
    claimed attempt first; the status value itself is what this query's
    `WHERE` clause must exclude, regardless of how a row got there."""
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
        row = session.get(AmazonIngestionRun, claim.run_id)
        row.status = "waiting_to_retry"
        row.next_retry_at = datetime.now(UTC) + timedelta(minutes=5)
    with session_scope() as session:
        latest = AmazonIngestionRunRepository(session).get_latest_cooldown_relevant_listings_run(
            scope["organization_id"], scope["marketplace_participation_id"]
        )
        assert latest is None


def test_cancelled_before_start_itself_never_imposes_a_cooldown() -> None:
    """The whole point of `terminalize-queued` is to unblock a stuck scope
    immediately — an operator should never have to additionally wait out
    a cooldown caused by the very job they just cancelled, since it made
    zero Amazon calls."""
    scope = _seed_scope()
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
        )
    with session_scope() as session:
        terminalized = AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            scope["organization_id"], claim.run_id
        )
    assert terminalized is True

    trigger = AmazonListingsSyncTriggerService(settings=_settings(listings_sync_trigger_cooldown_seconds=3600))
    outcome = trigger.trigger(scope["marketplace_participation_id"])
    assert outcome.reason == "queued"


def test_get_latest_listings_run_orders_by_created_at_not_started_at() -> None:
    """Confirms current, fixed behavior on SQLite — but SQLite defaults to
    `NULLS LAST` for `DESC`, the opposite of PostgreSQL's `NULLS FIRST`,
    so this scenario would also have passed against the *pre-fix* query
    (`ORDER BY started_at DESC`) on this dialect alone. This test is not
    proof the regression is fixed; it only proves the current query still
    behaves correctly here. The actual regression proof — which requires
    PostgreSQL's real null-ordering semantics to even be capable of
    failing — is `test_get_latest_listings_run_is_not_fooled_by_a_null_
    started_at_row` in `tests/postgres/test_disposable_postgres_listings_
    sync_trigger_concurrency.py`."""
    scope = _seed_scope()
    with session_scope() as session:
        never_started = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
        )
        row = session.get(AmazonIngestionRun, never_started.run_id)
        row.created_at = datetime.now(UTC) - timedelta(days=3)
    with session_scope() as session:
        AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            scope["organization_id"], never_started.run_id
        )

    with session_scope() as session:
        real_claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )

    with session_scope() as session:
        latest = AmazonIngestionRunRepository(session).get_latest_listings_run(
            scope["organization_id"], scope["marketplace_participation_id"]
        )
        assert latest.id == real_claim.run_id


def test_get_latest_listings_run_tiebreak_prefers_a_started_row_on_an_exact_created_at_tie() -> None:
    """Dialect-independent (unlike the days-old-vs-new scenario above,
    which depends on PostgreSQL's real `NULLS FIRST` semantics to even be
    capable of failing): forces an *exact* `created_at` tie between a
    never-started cancelled row and a real started row. `created_at`
    alone cannot break this tie in either row's favor — the
    `started_at IS NOT NULL` tiebreak must, structurally and
    deterministically (an explicit `ORDER BY` term, not a coincidence of
    row content), never leaving this to the luck of a random UUID
    comparison."""
    scope = _seed_scope()
    tie_point = datetime.now(UTC)
    with session_scope() as session:
        cancelled_claim = AmazonIngestionRunRepository(session).enqueue_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
        )
        session.get(AmazonIngestionRun, cancelled_claim.run_id).created_at = tie_point
    with session_scope() as session:
        AmazonIngestionRunRepository(session).terminalize_unclaimed_listings_run(
            scope["organization_id"], cancelled_claim.run_id
        )

    with session_scope() as session:
        real_claim = AmazonIngestionRunRepository(session).claim_listings_run(
            organization_id=scope["organization_id"],
            seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["marketplace_participation_id"],
            region="na", environment="PRODUCTION", connection_id=None,
            lease_owner="worker-1", lease_duration_seconds=300,
        )
    with session_scope() as session:
        session.get(AmazonIngestionRun, real_claim.run_id).created_at = tie_point  # the exact same instant

    with session_scope() as session:
        latest = AmazonIngestionRunRepository(session).get_latest_listings_run(
            scope["organization_id"], scope["marketplace_participation_id"]
        )
        assert latest.id == real_claim.run_id, "a same-created_at cancelled row outranked the real one"


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

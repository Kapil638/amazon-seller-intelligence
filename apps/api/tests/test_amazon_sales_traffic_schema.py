"""12B.6A — schema/repository tests for Sales and Traffic report ingestion:
the durable run lifecycle (enqueue/claim/finalize), the two fact-table
idempotent upserts, and the sync checkpoint. No SP-API client, worker, or
live Amazon call anywhere in this file — pure persistence-layer proof,
mirroring `test_amazon_seller_orders_schema.py`'s own conventions but
using this repository's `session_scope()`-based seeding pattern (as
`test_copilot_skills_evidence.py` already does) rather than a raw
per-test SQLAlchemy engine.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import AmazonIngestionRun, AmazonSalesAndTrafficDailyFact, AmazonSalesAndTrafficProductFact
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSalesTrafficDailyFactRepository,
    AmazonSalesTrafficProductFactRepository,
    AmazonSalesTrafficSyncCheckpointRepository,
    AmazonSellerAccountRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _seed_scope() -> dict:
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
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
            connection_id=connection.id,
        )
        session.flush()
        return {
            "org_id": org_id,
            "seller_account_id": seller_account.id,
            "participation_id": participation.id,
            "connection_id": connection.id,
        }


def _enqueue(scope: dict, *, day: date | None = None):
    day = day or date(2026, 8, 1)
    with session_scope() as session:
        return AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"], data_start_time=day, data_end_time=day,
            date_granularity="DAY", asin_granularity="SKU",
        )


def _claim(*, lease_owner: str = "test-lease"):
    with session_scope() as session:
        return AmazonIngestionRunRepository(session).claim_next_sales_traffic_job(
            lease_owner=lease_owner, lease_duration_seconds=300, max_global_active=10, max_active_per_organization=10
        )


# --- Run lifecycle -----------------------------------------------------


def test_enqueue_produces_a_queued_unleased_run() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    assert claim.claimed, claim.reason
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, claim.run_id)
        assert run.status == "queued"
        assert run.lease_owner is None
        assert run.report_data_start_time == date(2026, 8, 1)
        assert run.report_date_granularity == "DAY"
        assert run.report_asin_granularity == "SKU"


def test_only_one_active_run_per_participation_at_a_time() -> None:
    scope = _seed_scope()
    first = _enqueue(scope)
    assert first.claimed
    second = _enqueue(scope, day=date(2026, 8, 2))
    assert second.claimed is False
    assert second.reason == "already_running"


def test_claim_transitions_to_started_with_lease() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    claimed_run = _claim()
    assert claimed_run is not None
    assert claimed_run.id == claim.run_id
    assert claimed_run.status == "started"
    assert claimed_run.lease_owner == "test-lease"
    assert claimed_run.lease_expires_at is not None


def test_claim_with_no_eligible_job_returns_none() -> None:
    assert _claim() is None


def test_heartbeat_records_report_id_without_overwriting_with_none() -> None:
    scope = _seed_scope()
    _enqueue(scope)
    claimed_run = _claim()
    with session_scope() as session:
        repo = AmazonIngestionRunRepository(session)
        ok = repo.heartbeat_sales_traffic_run(
            scope["org_id"], claimed_run.id, lease_owner="test-lease", lease_duration_seconds=300,
            report_id="AMZN-REPORT-1", report_processing_status="IN_QUEUE",
        )
        assert ok is True
        ok2 = repo.heartbeat_sales_traffic_run(
            scope["org_id"], claimed_run.id, lease_owner="test-lease", lease_duration_seconds=300,
            report_processing_status="IN_PROGRESS",
        )
        assert ok2 is True
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, claimed_run.id)
        assert run.report_id == "AMZN-REPORT-1"  # untouched by the second heartbeat
        assert run.report_processing_status == "IN_PROGRESS"


def test_finalize_success_advances_checkpoint() -> None:
    scope = _seed_scope()
    _enqueue(scope)
    claimed_run = _claim()
    with session_scope() as session:
        ok = AmazonIngestionRunRepository(session).finalize_successful_sales_traffic_run(
            scope["org_id"], claimed_run.id, lease_owner="test-lease",
            marketplace_participation_id=scope["participation_id"], seller_account_id=scope["seller_account_id"],
            synced_through_date=date(2026, 8, 1),
        )
        assert ok is True
    with session_scope() as session:
        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(scope["org_id"], scope["participation_id"])
        assert checkpoint is not None
        assert checkpoint.synced_through_date == date(2026, 8, 1)


def test_finalize_never_moves_checkpoint_backward() -> None:
    scope = _seed_scope()
    _enqueue(scope, day=date(2026, 8, 5))
    run_a = _claim()
    with session_scope() as session:
        AmazonIngestionRunRepository(session).finalize_successful_sales_traffic_run(
            scope["org_id"], run_a.id, lease_owner="test-lease",
            marketplace_participation_id=scope["participation_id"], seller_account_id=scope["seller_account_id"],
            synced_through_date=date(2026, 8, 5),
        )
    _enqueue(scope, day=date(2026, 8, 1))
    run_b = _claim()
    with session_scope() as session:
        AmazonIngestionRunRepository(session).finalize_successful_sales_traffic_run(
            scope["org_id"], run_b.id, lease_owner="test-lease",
            marketplace_participation_id=scope["participation_id"], seller_account_id=scope["seller_account_id"],
            synced_through_date=date(2026, 8, 1),
        )
    with session_scope() as session:
        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(scope["org_id"], scope["participation_id"])
        assert checkpoint.synced_through_date == date(2026, 8, 5)  # unchanged — never moved backward


def test_checkpoint_advance_rejected_for_a_non_succeeded_run_called_directly() -> None:
    scope = _seed_scope()
    _enqueue(scope)
    claimed_run = _claim()
    with session_scope() as session:
        # Still `started` — never finalized. Calling the private advance
        # method directly must still refuse (SQL-gated, not caller-trusted).
        advanced = AmazonSalesTrafficSyncCheckpointRepository(session)._advance_if_run_succeeded(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], run_id=claimed_run.id,
            synced_through_date=date(2026, 8, 1),
        )
        assert advanced is False


def test_complete_as_failed_rejects_succeeded_status() -> None:
    scope = _seed_scope()
    _enqueue(scope)
    claimed_run = _claim()
    with session_scope() as session:
        with pytest.raises(ValueError):
            AmazonIngestionRunRepository(session).complete_sales_traffic_run_as_failed(
                scope["org_id"], claimed_run.id, lease_owner="test-lease", status="succeeded", failure_class=None
            )


def test_reschedule_for_retry_releases_lease() -> None:
    scope = _seed_scope()
    _enqueue(scope)
    claimed_run = _claim()
    with session_scope() as session:
        ok = AmazonIngestionRunRepository(session).reschedule_sales_traffic_run_for_retry(
            scope["org_id"], claimed_run.id, lease_owner="test-lease",
            next_retry_at=datetime.now(UTC) + timedelta(minutes=1), failure_class="throttled",
        )
        assert ok is True
    with session_scope() as session:
        run = session.get(AmazonIngestionRun, claimed_run.id)
        assert run.status == "waiting_to_retry"
        assert run.lease_owner is None
        assert run.failure_class == "throttled"


def test_finalize_rejects_the_wrong_lease_owner_and_changes_nothing() -> None:
    """Direct proof, at the repository CAS layer itself, that only the
    caller holding the exact current `lease_owner` may finalize a run —
    a stale worker (one whose lease was reassigned to a replacement after
    an expiry or a mistaken crash) presenting its own now-stale lease
    value must be rejected, never partially applied."""
    scope = _seed_scope()
    claim = _enqueue(scope)
    _claim()  # transitions the row to 'started' under lease_owner="test-lease"

    with session_scope() as session:
        ok = AmazonIngestionRunRepository(session).finalize_successful_sales_traffic_run(
            scope["org_id"], claim.run_id, lease_owner="an-impostor-lease-owner",
            marketplace_participation_id=scope["participation_id"], seller_account_id=scope["seller_account_id"],
            synced_through_date=date(2026, 8, 1),
        )
        assert ok is False

    with session_scope() as session:
        run = session.get(AmazonIngestionRun, claim.run_id)
        assert run.status == "started"  # never flipped to succeeded
        assert run.lease_owner == "test-lease"  # untouched — still the genuine owner's lease
        checkpoint = AmazonSalesTrafficSyncCheckpointRepository(session).get(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"]
        )
        assert checkpoint is None  # never created by the rejected call


def test_waiting_to_retry_job_is_not_reclaimed_before_its_next_retry_at() -> None:
    """`claim_next_sales_traffic_job`'s own candidate-selection predicate
    (`next_retry_at <= now()`) must genuinely gate reclaiming — a
    `waiting_to_retry` row with a *future* `next_retry_at` must remain
    invisible to every claim attempt until that time actually arrives."""
    scope = _seed_scope()
    claim = _enqueue(scope)
    _claim()
    with session_scope() as session:
        ok = AmazonIngestionRunRepository(session).reschedule_sales_traffic_run_for_retry(
            scope["org_id"], claim.run_id, lease_owner="test-lease",
            next_retry_at=datetime.now(UTC) + timedelta(hours=1), failure_class="polling",
        )
        assert ok is True

    reclaimed = _claim(lease_owner="a-different-worker")
    assert reclaimed is None

    with session_scope() as session:
        run = session.get(AmazonIngestionRun, claim.run_id)
        assert run.status == "waiting_to_retry"
        assert run.lease_owner is None


# --- Fact table idempotent upsert + natural keys ------------------------


def test_daily_fact_upsert_is_idempotent() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    run_id = claim.run_id
    fields = {"currency_code": "USD", "ordered_product_sales_amount": Decimal("100.00"), "units_ordered": 5}
    with session_scope() as session:
        AmazonSalesTrafficDailyFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            report_date=date(2026, 8, 1), date_granularity="DAY", last_ingestion_run_id=run_id, fields=fields,
        )
        AmazonSalesTrafficDailyFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            report_date=date(2026, 8, 1), date_granularity="DAY", last_ingestion_run_id=run_id,
            fields={**fields, "units_ordered": 7},
        )
    with session_scope() as session:

        rows = session.query(AmazonSalesAndTrafficDailyFact).filter_by(
            marketplace_participation_id=scope["participation_id"], report_date=date(2026, 8, 1)
        ).all()
        assert len(rows) == 1  # second upsert replaced, never duplicated
        assert rows[0].units_ordered == 7


def test_daily_fact_day_and_week_with_same_date_do_not_collide() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    with session_scope() as session:
        AmazonSalesTrafficDailyFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            report_date=date(2026, 8, 1), date_granularity="DAY", last_ingestion_run_id=claim.run_id,
            fields={"units_ordered": 1},
        )
        AmazonSalesTrafficDailyFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            report_date=date(2026, 8, 1), date_granularity="WEEK", last_ingestion_run_id=claim.run_id,
            fields={"units_ordered": 40},
        )
    with session_scope() as session:

        rows = session.query(AmazonSalesAndTrafficDailyFact).filter_by(
            marketplace_participation_id=scope["participation_id"], report_date=date(2026, 8, 1)
        ).all()
        assert len(rows) == 2  # never conflated — date_granularity is part of the natural key


def test_product_fact_upsert_is_idempotent_for_parent_granularity_no_child_or_sku() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    with session_scope() as session:
        repo = AmazonSalesTrafficProductFactRepository(session)
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 1), asin_granularity="PARENT",
            parent_asin="B0PARENT001", last_ingestion_run_id=claim.run_id, fields={"units_ordered": 1},
        )
        repo.upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 1), asin_granularity="PARENT",
            parent_asin="B0PARENT001", last_ingestion_run_id=claim.run_id, fields={"units_ordered": 3},
        )
    with session_scope() as session:

        rows = session.query(AmazonSalesAndTrafficProductFact).filter_by(
            marketplace_participation_id=scope["participation_id"], parent_asin="B0PARENT001"
        ).all()
        assert len(rows) == 1
        assert rows[0].units_ordered == 3
        assert rows[0].child_asin == ""
        assert rows[0].seller_sku == ""


def test_product_fact_never_dated_stores_the_exact_request_window() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)
    with session_scope() as session:
        AmazonSalesTrafficProductFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 30), asin_granularity="SKU",
            parent_asin="B0PARENT001", child_asin="B0CHILD001", seller_sku="SKU-A",
            last_ingestion_run_id=claim.run_id, fields={"units_ordered": 30},
        )
    with session_scope() as session:

        row = session.query(AmazonSalesAndTrafficProductFact).filter_by(seller_sku="SKU-A").one()
        assert row.request_window_start == date(2026, 8, 1)
        assert row.request_window_end == date(2026, 8, 30)


def test_product_fact_granularity_identifier_check_constraint_rejects_mismatch() -> None:
    """A PARENT-granularity row must never carry a child/sku identifier —
    proven at the database level, not just by the repository's own
    defaults, by attempting to violate it directly via the ORM."""
    scope = _seed_scope()
    claim = _enqueue(scope)

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(
                AmazonSalesAndTrafficProductFact(
                    marketplace_participation_id=scope["participation_id"],
                    request_window_start=date(2026, 8, 1),
                    request_window_end=date(2026, 8, 1),
                    asin_granularity="PARENT",
                    parent_asin="B0BAD0001",
                    child_asin="B0BADCHILD",  # invalid: PARENT rows must have child_asin == ""
                    seller_sku="",
                    last_ingestion_run_id=claim.run_id,
                )
            )
            session.flush()


def test_percentage_over_100_rejected_by_check_constraint_except_unit_session() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)

    with pytest.raises(IntegrityError):
        with session_scope() as session:
            session.add(
                AmazonSalesAndTrafficDailyFact(
                    marketplace_participation_id=scope["participation_id"],
                    report_date=date(2026, 8, 1),
                    date_granularity="DAY",
                    last_ingestion_run_id=claim.run_id,
                    buy_box_percentage=Decimal("150.00"),  # invalid: capped at 100
                )
            )
            session.flush()


def test_unit_session_percentage_allows_values_over_100() -> None:
    scope = _seed_scope()
    claim = _enqueue(scope)

    with session_scope() as session:
        session.add(
            AmazonSalesAndTrafficDailyFact(
                marketplace_participation_id=scope["participation_id"],
                report_date=date(2026, 8, 1),
                date_granularity="DAY",
                last_ingestion_run_id=claim.run_id,
                unit_session_percentage=Decimal("300.00"),  # valid — matches the pinned contract's own example
            )
        )
        session.flush()


def test_facts_never_leak_across_marketplace_participations() -> None:
    scope_a = _seed_scope()
    with session_scope() as session:
        participation_b = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope_a["org_id"], seller_account_id=scope_a["seller_account_id"],
            marketplace_id="A1PA6795UKMFR9", region="na", connection_id=scope_a["connection_id"],
        )
        session.flush()
        participation_b_id = participation_b.id
    claim_a = _enqueue(scope_a)
    with session_scope() as session:
        AmazonSalesTrafficProductFactRepository(session).upsert(
            organization_id=scope_a["org_id"], marketplace_participation_id=scope_a["participation_id"],
            request_window_start=date(2026, 8, 1), request_window_end=date(2026, 8, 1), asin_granularity="SKU",
            parent_asin="B0P1", child_asin="B0C1", seller_sku="SKU-ONLY-IN-A",
            last_ingestion_run_id=claim_a.run_id, fields={"units_ordered": 1},
        )
    with session_scope() as session:

        rows_for_b = session.query(AmazonSalesAndTrafficProductFact).filter_by(
            marketplace_participation_id=participation_b_id
        ).all()
        assert rows_for_b == []

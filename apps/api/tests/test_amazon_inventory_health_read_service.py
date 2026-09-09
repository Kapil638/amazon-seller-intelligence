"""12B.6C — AmazonInventoryHealthReadService. Strictly read-only: no
Amazon call, no AI call, no write beyond test seeding. Seeds Inventory
rows and Sales & Traffic product facts directly through their own
repositories (never through the SP-API client or a worker), mirroring
`test_amazon_inventory_read_service.py`'s and
`test_amazon_sales_traffic_read_service.py`'s established harness.
"""

from __future__ import annotations

from datetime import date, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event

from app.amazon.inventory_health_formulas import FORMULA_VERSION
from app.amazon.inventory_health_read import AmazonInventoryHealthReadService
from app.amazon.inventory_normalization import NormalizedInventoryObservation
from app.core.exceptions import AmazonListingsParticipationNotFoundError, AmazonSellerInventoryNotFoundError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
    AmazonSalesTrafficProductFactRepository,
    AmazonSellerAccountRepository,
    AmazonSellerInventoryRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _observation(seller_sku: str, condition: str = "NewItem", **overrides) -> NormalizedInventoryObservation:
    fields = dict(
        seller_sku=seller_sku, condition=condition, asin="B000000001", fnsku="FN1", product_name="Widget",
        total_quantity=10, fulfillable_quantity=8, inbound_working_quantity=None, inbound_shipped_quantity=None,
        inbound_receiving_quantity=None, reserved_total_quantity=2, reserved_pending_customer_order_quantity=2,
        reserved_pending_transshipment_quantity=None, reserved_fc_processing_quantity=None,
        unfulfillable_total_quantity=0, unfulfillable_customer_damaged_quantity=None,
        unfulfillable_warehouse_damaged_quantity=None, unfulfillable_distributor_damaged_quantity=None,
        unfulfillable_carrier_damaged_quantity=None, unfulfillable_defective_quantity=None,
        unfulfillable_expired_quantity=None, researching_total_quantity=None,
        researching_quantity_short_term=None, researching_quantity_mid_term=None,
        researching_quantity_long_term=None, amazon_last_updated_time=None,
    )
    fields.update(overrides)
    return NormalizedInventoryObservation(**fields)


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
            "org_id": org_id, "seller_account_id": seller_account.id, "participation_id": participation.id,
            "connection_id": connection.id,
        }


def _seed_inventory(scope: dict, observations: list[NormalizedInventoryObservation]) -> "UUID":  # noqa: F821
    with session_scope() as session:
        AmazonIngestionRunRepository(session).enqueue_inventory_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"],
        )
        claimed = AmazonIngestionRunRepository(session).claim_next_inventory_job(
            lease_owner="test", lease_duration_seconds=300, max_global_active=10, max_active_per_organization=10
        )
        AmazonSellerInventoryRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            observations=observations, ingestion_run_id=claimed.id,
        )
        AmazonIngestionRunRepository(session).complete_inventory_run(
            scope["org_id"], claimed.id, lease_owner=claimed.lease_owner, status="succeeded",
            records_received=len(observations), records_accepted=len(observations), pagination_complete=True,
        )
        return claimed.id


def _seed_succeeded_sales_traffic_run(scope: dict, *, day: date) -> "UUID":  # noqa: F821
    """Enqueue -> claim -> mark succeeded, the same three-step lifecycle
    a real worker drives — required because
    `AmazonSalesTrafficProductFactRepository.get_eligible_sku_facts`
    only ever considers a fact from a run whose `status == 'succeeded'`
    (a queued/started run's own facts are not reliable demand
    evidence)."""
    with session_scope() as session:
        AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"], data_start_time=day, data_end_time=day,
            date_granularity="DAY", asin_granularity="SKU",
        )
        claimed = AmazonIngestionRunRepository(session).claim_next_sales_traffic_job(
            lease_owner="test", lease_duration_seconds=300, max_global_active=10, max_active_per_organization=10
        )
        AmazonIngestionRunRepository(session).complete_sales_traffic_run_terminal(
            scope["org_id"], claimed.id, lease_owner=claimed.lease_owner, status="succeeded"
        )
        return claimed.id


def _seed_product_fact(
    scope: dict, run_id, *, sku: str, units: int | None, start: date, end: date, asin_granularity: str = "SKU"
) -> None:
    with session_scope() as session:
        AmazonSalesTrafficProductFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=start, request_window_end=end, asin_granularity=asin_granularity,
            parent_asin="B0PARENT001", child_asin=("B0CHILD001" if asin_granularity != "PARENT" else ""),
            seller_sku=(sku if asin_granularity == "SKU" else ""), last_ingestion_run_id=run_id,
            fields={"units_ordered": units, "currency_code": "USD"},
        )


class _QueryCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, *_args, **_kwargs) -> None:
        self.count += 1


# --- ownership / isolation ---------------------------------------------


def test_get_summary_raises_for_foreign_participation() -> None:
    service = AmazonInventoryHealthReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.get_summary(uuid4())


def test_list_inventory_health_raises_for_foreign_participation() -> None:
    service = AmazonInventoryHealthReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.list_inventory_health(uuid4())


def test_get_evidence_raises_for_foreign_inventory_row() -> None:
    scope = _seed_scope()
    service = AmazonInventoryHealthReadService()
    with pytest.raises(AmazonSellerInventoryNotFoundError):
        service.get_evidence(scope["participation_id"], uuid4())


def test_never_synchronized_participation_has_zero_total_and_no_eligible_facts() -> None:
    scope = _seed_scope()
    service = AmazonInventoryHealthReadService()
    summary = service.get_summary(scope["participation_id"])
    assert summary.total == 0
    assert summary.inventory_sync.status == "never_synchronized"
    assert summary.sales_traffic_sync.status == "never_synchronized"


# --- join correctness: identity risks from the reviewed proposal -------


def test_sku_granularity_fact_attaches_to_matching_inventory_row() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])

    row = result.items[0]
    assert row.demand_eligibility == "eligible"
    assert row.units_per_covered_day == pytest.approx(10.0)
    assert row.fulfillable_days_of_cover == pytest.approx(10.0)


def test_parent_granularity_fact_never_attaches_to_any_sku() -> None:
    """A PARENT-granularity row carries no seller_sku identity at all
    (`seller_sku=''`) — it must never be mistaken for evidence about a
    specific SKU."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(
        scope, run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30), asin_granularity="PARENT"
    )
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])

    assert result.items[0].demand_eligibility == "no_eligible_sales_traffic_fact"


def test_fact_from_a_non_succeeded_run_never_attaches() -> None:
    """A queued/started (never-completed) run's product facts are not
    reliable demand evidence — proven by seeding a fact against a run
    that is deliberately left at status='queued'."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    with session_scope() as session:
        claim = AmazonIngestionRunRepository(session).enqueue_sales_traffic_run(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_participation_id=scope["participation_id"], region="na", environment="PRODUCTION",
            connection_id=scope["connection_id"], data_start_time=date(2026, 8, 30), data_end_time=date(2026, 8, 30),
            date_granularity="DAY", asin_granularity="SKU",
        )
    _seed_product_fact(scope, claim.run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])

    assert result.items[0].demand_eligibility == "no_eligible_sales_traffic_fact"


def test_fact_referencing_a_run_from_a_different_participation_never_attaches() -> None:
    """Defense-in-depth proof for `get_eligible_sku_facts`'s explicit
    join condition (repositories.py): even when a fact's
    last_ingestion_run_id is made to point at a *different*
    participation's succeeded run — a pairing PostgreSQL's own
    composite foreign key would reject, but this test suite's SQLite
    engine never enforces (`PRAGMA foreign_keys` is not turned on
    here) — the read service still correctly excludes it. Constructed
    via a raw table UPDATE specifically because the repository's own
    `upsert()` would never produce this state through its normal API."""
    scope_a = _seed_scope()
    # A second, independent participation sharing scope_a's own
    # connection (AmazonConnection is unique per (org, provider,
    # environment) — a second full _seed_scope() would collide on
    # that).
    with session_scope() as session:
        seller_account_b = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=scope_a["org_id"], selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation_b = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope_a["org_id"], seller_account_id=seller_account_b.id,
            marketplace_id="A1PA6795UKMFR9", region="eu", connection_id=scope_a["connection_id"],
        )
        session.flush()
        scope_b = {
            "org_id": scope_a["org_id"], "seller_account_id": seller_account_b.id,
            "participation_id": participation_b.id, "connection_id": scope_a["connection_id"],
        }
    _seed_inventory(scope_a, [_observation("SKU-1", fulfillable_quantity=100)])
    other_run_id = _seed_succeeded_sales_traffic_run(scope_b, day=date(2026, 8, 30))
    # Seed the fact honestly under scope_a's own succeeded run first...
    real_run_id = _seed_succeeded_sales_traffic_run(scope_a, day=date(2026, 8, 30))
    _seed_product_fact(scope_a, real_run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    # ...then corrupt its last_ingestion_run_id to point at scope_b's
    # run instead, bypassing the repository entirely.
    with session_scope() as session:
        from app.persistence.models import AmazonSalesAndTrafficProductFact

        session.execute(
            AmazonSalesAndTrafficProductFact.__table__.update()
            .where(
                AmazonSalesAndTrafficProductFact.marketplace_participation_id == scope_a["participation_id"],
                AmazonSalesAndTrafficProductFact.seller_sku == "SKU-1",
            )
            .values(last_ingestion_run_id=other_run_id)
        )
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope_a["participation_id"]).items[0]
    assert row.demand_eligibility == "no_eligible_sales_traffic_fact"


def test_read_service_enforces_the_minimum_eligible_window_end_to_end() -> None:
    """A real, succeeded fact whose own inclusive window is shorter
    than the configured minimum (7 days) must not drive velocity —
    proven through the full read-service stack, not just the pure
    formula function."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=30, start=date(2026, 8, 28), end=date(2026, 8, 30))  # 3 days
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.demand_eligibility == "insufficient_window"
    assert row.sales_covered_days == 3
    assert row.units_per_covered_day is None


def test_multiple_skus_for_one_asin_do_not_collide() -> None:
    scope = _seed_scope()
    _seed_inventory(
        scope,
        [
            _observation("SKU-1", asin="B0SAME00001", fulfillable_quantity=100),
            _observation("SKU-2", asin="B0SAME00001", fulfillable_quantity=50),
        ],
    )
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    # SKU-2 has no fact of its own.
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])
    by_sku = {item.seller_sku: item for item in result.items}

    assert by_sku["SKU-1"].demand_eligibility == "eligible"
    assert by_sku["SKU-2"].demand_eligibility == "no_eligible_sales_traffic_fact"


def test_multiple_conditions_for_one_sku_never_share_the_same_demand_fact() -> None:
    """The condition risk the review specifically flagged: a
    Sales & Traffic product fact has no condition dimension, so two
    condition-rows for the same SKU could otherwise both claim the same
    demand evidence. Only the supported condition (NewItem) is
    eligible; a UsedGood row for the identical SKU must never become
    eligible off the same fact."""
    scope = _seed_scope()
    _seed_inventory(
        scope,
        [
            _observation("SKU-1", condition="NewItem", fulfillable_quantity=100),
            _observation("SKU-1", condition="UsedGood", fulfillable_quantity=20),
        ],
    )
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])
    by_condition = {item.condition: item for item in result.items}

    assert by_condition["NewItem"].demand_eligibility == "eligible"
    assert by_condition["UsedGood"].demand_eligibility == "unsupported_condition"
    assert by_condition["UsedGood"].units_per_covered_day is None


def test_listing_absent_for_an_inventory_row_does_not_break_evidence() -> None:
    """No AmazonSellerListing join is required at all — Inventory Health
    must compute correctly even with zero rows in amazon_seller_listings."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-NO-LISTING", fulfillable_quantity=10)])
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])
    assert result.total == 1


# --- coverage/freshness corrections from the review ---------------------


def test_null_amazon_last_updated_time_does_not_make_a_fresh_row_stale() -> None:
    """The core freshness correction: amazon_last_updated_time may be
    legitimately null even on a just-synced row — freshness must come
    from ASI's own provenance (the successful run's completed_at /
    the row's last_seen_at), never from Amazon's optional field."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", amazon_last_updated_time=None)])
    service = AmazonInventoryHealthReadService()

    result = service.list_inventory_health(scope["participation_id"])
    row = result.items[0]

    assert row.amazon_last_updated_time is None
    assert row.inventory_observed_at is not None
    assert row.freshness_state in ("stale_sales", "fresh")  # never stale on the inventory side
    assert "stale_inventory" not in row.freshness_state


def test_inventory_and_sales_freshness_are_reported_independently() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1")])
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    # No sales run at all yet -> sales side must be independently stale
    # even though inventory was just synced.
    assert row.inventory_observed_at is not None
    assert row.sales_traffic_ingestion_completed_at is None
    assert row.freshness_state == "stale_sales"


def test_prior_data_remains_visible_after_no_successful_sales_sync_yet() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=42)])
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.fulfillable_quantity == 42
    assert row.demand_eligibility == "no_eligible_sales_traffic_fact"


# --- zero vs missing demand ------------------------------------------------


def test_eligible_fact_with_explicit_zero_units_is_no_recent_demand_not_missing() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=50)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=0, start=date(2026, 8, 1), end=date(2026, 8, 30))
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.demand_eligibility == "eligible"
    assert row.units_per_covered_day == 0.0
    assert row.fulfillable_days_of_cover is None
    assert "no_recent_demand" in row.overlays


def test_absent_product_fact_never_reported_as_zero_demand() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=50)])
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.demand_eligibility == "no_eligible_sales_traffic_fact"
    assert row.units_per_covered_day is None


# --- overlapping windows never summed at the service layer --------------


def test_service_never_sums_overlapping_7_30_90_day_facts() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=300)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    end = date(2026, 8, 30)
    _seed_product_fact(scope, run_id, sku="SKU-1", units=70, start=end - timedelta(days=6), end=end)
    _seed_product_fact(scope, run_id, sku="SKU-1", units=300, start=end - timedelta(days=29), end=end)
    _seed_product_fact(scope, run_id, sku="SKU-1", units=900, start=end - timedelta(days=89), end=end)
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.units_per_covered_day == pytest.approx(10.0)
    assert row.sales_covered_days == 30


# --- PR corrections: strict potential_units null policy --------------------


def test_potential_units_null_when_an_inbound_quantity_is_unreported() -> None:
    """The default observation fixture already leaves every inbound
    quantity null (matching what a real, non-`details=true` or
    partially-populated Amazon response looks like) — proves the
    service-layer wiring surfaces the formula's strict null policy
    end-to-end, not just at the pure-function level."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.potential_units is None
    assert row.potential_days_of_cover is None
    assert row.potential_units_incomplete_inputs is True


def test_potential_units_real_number_when_every_component_is_known() -> None:
    scope = _seed_scope()
    _seed_inventory(
        scope,
        [
            _observation(
                "SKU-1",
                fulfillable_quantity=100,
                inbound_working_quantity=10,
                inbound_shipped_quantity=20,
                inbound_receiving_quantity=5,
            )
        ],
    )
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.potential_units == 135
    assert row.potential_units_incomplete_inputs is False


def test_potential_units_all_zero_known_inputs_is_a_real_zero_not_null() -> None:
    scope = _seed_scope()
    _seed_inventory(
        scope,
        [
            _observation(
                "SKU-1",
                fulfillable_quantity=0,
                inbound_working_quantity=0,
                inbound_shipped_quantity=0,
                inbound_receiving_quantity=0,
            )
        ],
    )
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.potential_units == 0
    assert row.potential_units_incomplete_inputs is False


# --- PR corrections: inventory freshness provenance -------------------------


def test_freshness_unknown_when_last_ingestion_run_id_is_missing() -> None:
    """A row with no run provenance at all must never be treated as
    fresh from any other timestamp on the row."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1")])
    with session_scope() as session:
        from app.persistence.models import AmazonSellerInventory

        row_obj = session.execute(
            AmazonSellerInventory.__table__.select().where(AmazonSellerInventory.seller_sku == "SKU-1")
        ).first()
        session.execute(
            AmazonSellerInventory.__table__.update()
            .where(AmazonSellerInventory.id == row_obj.id)
            .values(last_ingestion_run_id=None)
        )
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.inventory_observed_at is None
    assert row.freshness_state in ("stale_inventory", "stale_both")


def test_freshness_unknown_when_referenced_run_is_not_succeeded() -> None:
    """A row whose last_ingestion_run_id points at a run that is not
    (or no longer) recorded 'succeeded' must not be treated as fresh —
    proves the read service checks run.status, not merely the run's
    existence."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1")])
    with session_scope() as session:
        from app.persistence.models import AmazonIngestionRun, AmazonSellerInventory

        row_obj = session.execute(
            AmazonSellerInventory.__table__.select().where(AmazonSellerInventory.seller_sku == "SKU-1")
        ).first()
        session.execute(
            AmazonIngestionRun.__table__.update()
            .where(AmazonIngestionRun.id == row_obj.last_ingestion_run_id)
            .values(status="failed", failure_class="unexpected_error")
        )
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.inventory_observed_at is None
    assert row.freshness_state in ("stale_inventory", "stale_both")


def test_freshness_unknown_when_referenced_run_has_no_completed_at() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1")])
    with session_scope() as session:
        from app.persistence.models import AmazonIngestionRun, AmazonSellerInventory

        row_obj = session.execute(
            AmazonSellerInventory.__table__.select().where(AmazonSellerInventory.seller_sku == "SKU-1")
        ).first()
        session.execute(
            AmazonIngestionRun.__table__.update()
            .where(AmazonIngestionRun.id == row_obj.last_ingestion_run_id)
            .values(completed_at=None)
        )
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.inventory_observed_at is None


def test_freshness_uses_the_provably_succeeded_run_completed_at() -> None:
    """The positive case: a genuinely successful reconcile's own
    run.completed_at is used, matching the invariant this correction
    relies on rather than assumes."""
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1")])
    service = AmazonInventoryHealthReadService()

    row = service.list_inventory_health(scope["participation_id"]).items[0]
    assert row.inventory_observed_at is not None


# --- summary counts -------------------------------------------------------


def test_summary_counts_by_state_and_eligibility() -> None:
    scope = _seed_scope()
    _seed_inventory(
        scope,
        [
            _observation("SKU-OOS", fulfillable_quantity=0),
            _observation("SKU-HEALTHY", fulfillable_quantity=140),
        ],
    )
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-HEALTHY", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    service = AmazonInventoryHealthReadService()

    summary = service.get_summary(scope["participation_id"])

    assert summary.total == 2
    assert summary.counts_by_inventory_state["out_of_stock"] == 1
    assert summary.counts_by_inventory_state["healthy_coverage"] == 1
    assert summary.formula_version == FORMULA_VERSION
    assert summary.thresholds.low_coverage_days_threshold == 14.0


# --- evidence endpoint -----------------------------------------------------


def test_evidence_exposes_exact_run_ids_and_formula_version() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    inventory_run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, inventory_run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))
    service = AmazonInventoryHealthReadService()

    listing = service.list_inventory_health(scope["participation_id"])
    inventory_id = listing.items[0].inventory_id

    evidence = service.get_evidence(scope["participation_id"], inventory_id)

    assert evidence.formula_version == FORMULA_VERSION
    assert evidence.sales_traffic_ingestion_run_id == inventory_run_id
    assert evidence.inventory_ingestion_run_id is not None
    assert evidence.thresholds.preferred_window_days == 30


# --- no Amazon or AI call during calculation -------------------------------


def test_no_network_call_is_made_during_calculation(monkeypatch) -> None:
    """Every formula and query in this service is local — patch the
    stdlib socket connect to raise if anything ever tries to reach the
    network, proving the calculation path is fully offline."""
    import socket

    def _forbidden_connect(*_args, **_kwargs):
        raise AssertionError("Inventory Health calculation must never open a network connection")

    monkeypatch.setattr(socket.socket, "connect", _forbidden_connect)

    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", fulfillable_quantity=100)])
    service = AmazonInventoryHealthReadService()
    service.get_summary(scope["participation_id"])
    service.list_inventory_health(scope["participation_id"])


# --- query-count / N+1 regression guard -------------------------------------


def _count_queries(fn):
    counter = _QueryCounter()
    with session_scope() as session:
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", counter)
        try:
            result = fn()
        finally:
            event.remove(engine, "before_cursor_execute", counter)
    return result, counter.count


def test_list_inventory_health_query_count_bounded_across_materially_different_page_sizes() -> None:
    """Seeds 100 rows once, then compares a 1-row page against a
    100-row page against the *same* dataset — proves query count is
    bounded by page size (pagination applied before evidence assembly
    fetches facts/runs), not by total row count, and does not scale
    materially between the two, not merely that one arbitrary page
    size (25) stays under one arbitrary ceiling."""
    scope = _seed_scope()
    observations = [_observation(f"SKU-{i:03d}", fulfillable_quantity=10 + i) for i in range(100)]
    _seed_inventory(scope, observations)
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    for i in range(100):
        _seed_product_fact(scope, run_id, sku=f"SKU-{i:03d}", units=30 * (i + 1), start=date(2026, 8, 1), end=date(2026, 8, 30))

    service = AmazonInventoryHealthReadService()

    result_1, count_1 = _count_queries(lambda: service.list_inventory_health(scope["participation_id"], limit=1))
    result_100, count_100 = _count_queries(lambda: service.list_inventory_health(scope["participation_id"], limit=100))

    assert result_1.total == 100
    assert len(result_1.items) == 1, "pagination must be applied before evidence assembly, not after"
    assert result_100.total == 100
    assert len(result_100.items) == 100

    assert count_1 < 15, f"expected a bounded query count for limit=1, got {count_1}"
    assert count_100 < 15, f"expected a bounded query count for limit=100, got {count_100}"
    assert count_100 <= count_1 + 2, (
        f"query count grew materially between limit=1 ({count_1}) and limit=100 ({count_100}) — "
        "pagination does not appear to be applied before evidence assembly"
    )


def test_summary_query_count_is_bounded_not_one_per_row() -> None:
    """Summary aggregation intentionally materializes every row for
    this participation (classification requires the Python-side
    formula layer — join/tie-break selection cannot be pushed into SQL
    without reimplementing the whole formula layer there, which is out
    of this milestone's approved scope). What must still hold, and is
    proven here, is that this costs a small constant number of
    *queries* regardless of row count — never one query per row for
    facts/runs, which is the actual, unbounded-scaling failure mode
    this test guards against."""
    scope = _seed_scope()
    observations = [_observation(f"SKU-{i:03d}", fulfillable_quantity=10 + i) for i in range(100)]
    _seed_inventory(scope, observations)
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    for i in range(100):
        _seed_product_fact(scope, run_id, sku=f"SKU-{i:03d}", units=30 * (i + 1), start=date(2026, 8, 1), end=date(2026, 8, 30))

    service = AmazonInventoryHealthReadService()
    result, count = _count_queries(lambda: service.get_summary(scope["participation_id"]))

    assert result.total == 100
    assert count < 15, f"expected a bounded query count for summary, got {count}"


def test_pagination_ordering_is_deterministic_when_sort_values_tie() -> None:
    """Every row shares the same fulfillable_quantity (the default
    sort field) — proves the secondary `id` tie-breaker
    (`AmazonSellerInventoryRepository.list_page`) makes pagination
    stable: two consecutive pages together cover every row exactly
    once, with zero overlap and zero gap, never a row silently
    reshuffled between them."""
    scope = _seed_scope()
    observations = [_observation(f"SKU-{i:03d}", fulfillable_quantity=50) for i in range(10)]
    _seed_inventory(scope, observations)
    service = AmazonInventoryHealthReadService()

    page_1 = service.list_inventory_health(scope["participation_id"], offset=0, limit=5)
    page_2 = service.list_inventory_health(scope["participation_id"], offset=5, limit=5)

    skus_1 = {item.seller_sku for item in page_1.items}
    skus_2 = {item.seller_sku for item in page_2.items}
    assert len(skus_1) == 5
    assert len(skus_2) == 5
    assert skus_1.isdisjoint(skus_2)
    assert skus_1 | skus_2 == {f"SKU-{i:03d}" for i in range(10)}

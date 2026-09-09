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


def test_list_inventory_health_query_count_does_not_scale_with_row_count() -> None:
    """A bounded, small number of queries regardless of how many
    Inventory rows are on the page — the read service must never issue
    one query per row for facts/runs."""
    scope = _seed_scope()
    observations = [_observation(f"SKU-{i}", fulfillable_quantity=10 + i) for i in range(25)]
    _seed_inventory(scope, observations)
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    for i in range(25):
        _seed_product_fact(scope, run_id, sku=f"SKU-{i}", units=30 * (i + 1), start=date(2026, 8, 1), end=date(2026, 8, 30))

    service = AmazonInventoryHealthReadService()
    counter = _QueryCounter()

    with session_scope() as session:
        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", counter)
        try:
            result = service.list_inventory_health(scope["participation_id"], limit=25)
        finally:
            event.remove(engine, "before_cursor_execute", counter)

    assert result.total == 25
    # A small constant bound, generous but proving no per-row scaling
    # (25 rows must not produce anywhere near 25x the query count).
    assert counter.count < 15, f"expected a bounded query count, got {counter.count}"

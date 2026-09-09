"""12B.6C — Amazon Inventory Health Read API (HTTP layer). No Amazon
call, no AI call, no write. Uses the `client` fixture from conftest
(shared, per-test-isolated SQLite database), matching
`test_amazon_inventory_api.py`'s established pattern. Business-logic
correctness (join/eligibility/freshness) is already covered exhaustively
at the service layer (`test_amazon_inventory_health_read_service.py`) —
this file covers the HTTP transport contract only: status codes,
sanitization, pagination wiring, and response-shape completeness.
"""

from __future__ import annotations

from datetime import date
from uuid import uuid4

from app.amazon.inventory_normalization import NormalizedInventoryObservation
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


def _seed_participation() -> dict:
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


def _reconcile(scope: dict, observations: list[NormalizedInventoryObservation]) -> None:
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


def _seed_succeeded_sales_traffic_run(scope: dict, *, day: date):
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


def _seed_product_fact(scope: dict, run_id, *, sku: str, units: int, start: date, end: date) -> None:
    with session_scope() as session:
        AmazonSalesTrafficProductFactRepository(session).upsert(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            request_window_start=start, request_window_end=end, asin_granularity="SKU",
            parent_asin="B0PARENT001", child_asin="B0CHILD001", seller_sku=sku, last_ingestion_run_id=run_id,
            fields={"units_ordered": units, "currency_code": "USD"},
        )


def _url(participation_id, suffix: str = "") -> str:
    return f"/api/v1/amazon/marketplace-participations/{participation_id}/inventory-health{suffix}"


# --- summary endpoint --------------------------------------------------


def test_summary_endpoint_returns_expected_shape(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1", fulfillable_quantity=0), _observation("SKU-2", fulfillable_quantity=140)])
    response = client.get(_url(scope["participation_id"], "/summary"))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["marketplace_participation_id"] == str(scope["participation_id"])
    assert body["formula_version"]
    assert body["thresholds"]["low_coverage_days_threshold"] == 14.0
    assert body["thresholds"]["high_coverage_days_threshold"] == 90.0
    assert body["counts_by_inventory_state"]["out_of_stock"] == 1
    assert body["inventory_sync"]["status"] == "succeeded"
    assert body["sales_traffic_sync"]["status"] == "never_synchronized"


def test_summary_endpoint_malformed_uuid_rejected(client) -> None:
    response = client.get(_url("not-a-uuid", "/summary"))
    assert response.status_code == 400


def test_summary_endpoint_nonexistent_participation_returns_sanitized_404(client) -> None:
    response = client.get(_url(uuid4(), "/summary"))
    assert response.status_code == 404


# --- collection endpoint --------------------------------------------------


def test_list_endpoint_returns_items_with_full_evidence_fields(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1", fulfillable_quantity=140)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))

    response = client.get(_url(scope["participation_id"]))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["seller_sku"] == "SKU-1"
    assert item["demand_eligibility"] == "eligible"
    assert item["units_per_covered_day"] == 10.0
    assert item["fulfillable_days_of_cover"] == 14.0
    assert item["inventory_state"] == "healthy_coverage"
    assert item["sales_covered_days"] == 30
    assert "overlays" in item
    assert body["thresholds"]["preferred_window_days"] == 30


def test_list_endpoint_zero_velocity_serializes_as_json_zero_not_null(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-ZERO", fulfillable_quantity=50)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-ZERO", units=0, start=date(2026, 8, 1), end=date(2026, 8, 30))

    response = client.get(_url(scope["participation_id"]))
    item = response.json()["items"][0]
    assert item["units_per_covered_day"] == 0.0
    assert item["fulfillable_days_of_cover"] is None
    assert "no_recent_demand" in item["overlays"]


def test_list_endpoint_missing_evidence_serializes_as_null_not_zero(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-NO-FACT", fulfillable_quantity=50)])

    response = client.get(_url(scope["participation_id"]))
    item = response.json()["items"][0]
    assert item["units_per_covered_day"] is None
    assert item["fulfillable_days_of_cover"] is None
    assert item["demand_eligibility"] == "no_eligible_sales_traffic_fact"


def test_list_endpoint_search_filters_by_sku(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-ALPHA"), _observation("SKU-BETA")])
    response = client.get(_url(scope["participation_id"]), params={"q": "ALPHA"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["seller_sku"] == "SKU-ALPHA"


def test_list_endpoint_pagination_offset_and_limit(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation(f"SKU-{i}") for i in range(5)])
    response = client.get(_url(scope["participation_id"]), params={"offset": 2, "limit": 2})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 5
    assert body["offset"] == 2
    assert body["limit"] == 2
    assert len(body["items"]) == 2


def test_list_endpoint_rejects_limit_over_the_max_page_size(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"]), params={"limit": 1000})
    # This app's validation-error handler normalizes FastAPI's default
    # 422 to 400 — matching every other malformed-input case in this
    # route file (e.g. a non-UUID participation id).
    assert response.status_code == 400


def test_list_endpoint_never_exposes_forbidden_fields(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1")])
    response = client.get(_url(scope["participation_id"]))
    body = response.json()["items"][0]
    forbidden = {"organization_id", "seller_account_id", "connection_id", "token_reference", "lease_owner"}
    assert forbidden.isdisjoint(body.keys())


def test_list_endpoint_nonexistent_participation_returns_404(client) -> None:
    response = client.get(_url(uuid4()))
    assert response.status_code == 404


# --- evidence endpoint -------------------------------------------------------


def test_evidence_endpoint_returns_full_provenance(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1", fulfillable_quantity=140)])
    run_id = _seed_succeeded_sales_traffic_run(scope, day=date(2026, 8, 30))
    _seed_product_fact(scope, run_id, sku="SKU-1", units=300, start=date(2026, 8, 1), end=date(2026, 8, 30))

    listing = client.get(_url(scope["participation_id"])).json()
    inventory_id = listing["items"][0]["inventory_id"]

    response = client.get(_url(scope["participation_id"], f"/{inventory_id}/evidence"))
    assert response.status_code == 200
    body = response.json()
    assert body["inventory_id"] == inventory_id
    assert body["sales_traffic_ingestion_run_id"] == str(run_id)
    assert body["inventory_ingestion_run_id"] is not None
    assert body["formula_version"]
    assert body["thresholds"]["low_coverage_days_threshold"] == 14.0
    assert body["sales_window_start"] == "2026-08-01"
    assert body["sales_window_end"] == "2026-08-30"


def test_evidence_endpoint_never_accepts_a_raw_sku_in_the_path(client) -> None:
    """The evidence route takes the Inventory row's own UUID, never a
    raw seller SKU — a SKU-shaped string in that path segment must fail
    UUID validation, not silently 200 or 404 through a different code
    path."""
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1")])
    response = client.get(_url(scope["participation_id"], "/SKU-1/evidence"))
    assert response.status_code == 400


def test_evidence_endpoint_nonexistent_inventory_row_returns_sanitized_404(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"], f"/{uuid4()}/evidence"))
    assert response.status_code == 404


def test_evidence_endpoint_malformed_participation_uuid_rejected(client) -> None:
    response = client.get(_url("not-a-uuid", f"/{uuid4()}/evidence"))
    assert response.status_code == 400

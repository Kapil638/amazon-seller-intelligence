"""12B.6B — Amazon FBA Inventory Read API (HTTP layer). No Amazon call, no
ingestion trigger, no write. Uses the `client` fixture from conftest
(shared, per-test-isolated SQLite database) and seeds real rows through
the repository, matching `test_amazon_listings_api.py`'s established
pattern.
"""

from __future__ import annotations

from uuid import uuid4

from app.amazon.inventory_normalization import NormalizedInventoryObservation
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonConnectionRepository,
    AmazonIngestionRunRepository,
    AmazonMarketplaceParticipationRepository,
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
        claim = AmazonIngestionRunRepository(session).enqueue_inventory_run(
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


def _url(participation_id, suffix: str = "") -> str:
    return f"/api/v1/amazon/marketplace-participations/{participation_id}/inventory{suffix}"


# --- summary endpoint --------------------------------------------------


def test_summary_endpoint_returns_counts(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1"), _observation("SKU-2", asin=None, fnsku=None)])
    response = client.get(_url(scope["participation_id"], "/summary"))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["with_asin_count"] == 1
    assert body["marketplace_participation_id"] == str(scope["participation_id"])
    assert body["sync"]["status"] == "succeeded"


def test_summary_endpoint_never_synchronized_for_new_participation(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"], "/summary"))
    assert response.status_code == 200
    assert response.json()["sync"]["status"] == "never_synchronized"


def test_summary_endpoint_malformed_uuid_rejected(client) -> None:
    response = client.get(_url("not-a-uuid", "/summary"))
    assert response.status_code == 400


def test_summary_endpoint_nonexistent_participation_returns_sanitized_404(client) -> None:
    response = client.get(_url(uuid4(), "/summary"))
    assert response.status_code == 404


# --- list endpoint -------------------------------------------------------


def test_list_endpoint_returns_items(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-ALPHA"), _observation("SKU-BETA")])
    response = client.get(_url(scope["participation_id"]))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    skus = {item["seller_sku"] for item in body["items"]}
    assert skus == {"SKU-ALPHA", "SKU-BETA"}


def test_list_endpoint_search_filters_by_sku(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-ALPHA"), _observation("SKU-BETA")])
    response = client.get(_url(scope["participation_id"]), params={"q": "ALPHA"})
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["seller_sku"] == "SKU-ALPHA"


def test_list_endpoint_never_exposes_forbidden_fields(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1")])
    response = client.get(_url(scope["participation_id"]))
    body = response.json()["items"][0]
    forbidden = {"organization_id", "seller_account_id", "connection_id", "token_reference"}
    assert forbidden.isdisjoint(body.keys())


def test_list_endpoint_nonexistent_participation_returns_404(client) -> None:
    response = client.get(_url(uuid4()))
    assert response.status_code == 404


# --- detail endpoint -------------------------------------------------------


def test_detail_endpoint_returns_full_row(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_observation("SKU-1", reserved_total_quantity=3)])
    listing = client.get(_url(scope["participation_id"])).json()
    item_id = listing["items"][0]["id"]

    response = client.get(_url(scope["participation_id"], f"/{item_id}"))
    assert response.status_code == 200
    body = response.json()
    assert body["seller_sku"] == "SKU-1"
    assert body["reserved_total_quantity"] == 3


def test_detail_endpoint_nonexistent_item_returns_404(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"], f"/{uuid4()}"))
    assert response.status_code == 404


def test_detail_endpoint_malformed_uuid_rejected(client) -> None:
    response = client.get(_url(uuid4(), "/not-a-uuid"))
    assert response.status_code == 400

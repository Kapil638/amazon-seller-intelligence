"""12B.6B — `AmazonInventoryReadService`. Strictly read-only: no Amazon
call, no write. Seeds `amazon_seller_inventory` rows directly via the
repository (not through ingestion) — the ingestion path is already
covered by `test_amazon_inventory_ingestion.py`."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.amazon.inventory_normalization import NormalizedInventoryObservation
from app.amazon.inventory_read import AmazonInventoryReadService
from app.core.exceptions import AmazonListingsParticipationNotFoundError, AmazonSellerInventoryNotFoundError
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


def _seed_inventory(scope: dict, observations: list[NormalizedInventoryObservation]):
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
        return claimed.id


def test_get_summary_returns_never_synchronized_for_a_never_synced_participation() -> None:
    scope = _seed_scope()
    service = AmazonInventoryReadService()

    summary = service.get_summary(scope["participation_id"])

    assert summary.total == 0
    assert summary.sync.status == "never_synchronized"


def test_get_summary_returns_counts_and_sync_evidence_after_a_successful_sync() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1"), _observation("SKU-2", asin=None, fnsku=None)])
    service = AmazonInventoryReadService()

    summary = service.get_summary(scope["participation_id"])

    assert summary.total == 2
    assert summary.active_count == 2
    assert summary.inactive_count == 0
    assert summary.with_asin_count == 1
    assert summary.sync.status == "succeeded"
    assert summary.sync.last_successful_synchronized_at is not None


def test_get_summary_raises_for_foreign_participation() -> None:
    service = AmazonInventoryReadService()
    with pytest.raises(AmazonListingsParticipationNotFoundError):
        service.get_summary(uuid4())


def test_list_inventory_returns_rows_with_search() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-ALPHA"), _observation("SKU-BETA")])
    service = AmazonInventoryReadService()

    result = service.list_inventory(scope["participation_id"], search="ALPHA")

    assert result.total == 1
    assert result.items[0].seller_sku == "SKU-ALPHA"


def test_list_inventory_filters_by_is_active() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1")])
    _seed_inventory(scope, [])  # second full sweep with nothing -> SKU-1 deactivated
    service = AmazonInventoryReadService()

    active_only = service.list_inventory(scope["participation_id"], is_active=True)
    inactive_only = service.list_inventory(scope["participation_id"], is_active=False)

    assert active_only.total == 0
    assert inactive_only.total == 1
    assert inactive_only.items[0].total_quantity == 10  # preserved, never zeroed


def test_get_inventory_item_returns_full_detail() -> None:
    scope = _seed_scope()
    _seed_inventory(scope, [_observation("SKU-1", reserved_total_quantity=3)])
    service = AmazonInventoryReadService()

    listing = service.list_inventory(scope["participation_id"])
    item_id = listing.items[0].id

    detail = service.get_inventory_item(scope["participation_id"], item_id)

    assert detail.seller_sku == "SKU-1"
    assert detail.reserved_total_quantity == 3


def test_get_inventory_item_raises_for_foreign_item() -> None:
    scope = _seed_scope()
    service = AmazonInventoryReadService()
    with pytest.raises(AmazonSellerInventoryNotFoundError):
        service.get_inventory_item(scope["participation_id"], uuid4())

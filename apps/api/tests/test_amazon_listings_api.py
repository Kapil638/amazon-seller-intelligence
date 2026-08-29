"""12B.3E — Amazon Seller Listings Read API (HTTP layer). No Amazon call,
no ingestion trigger, no write. Uses the `client` fixture from conftest
(shared, per-test-isolated SQLite database).
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from app.amazon.listings_normalization import NormalizedListing
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonMarketplaceParticipationRepository,
    AmazonSellerAccountRepository,
    AmazonSellerListingRepository,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _listing(sku: str, **overrides) -> NormalizedListing:
    base = dict(
        seller_sku=sku, asin=f"B0{sku}TEST1"[:10], product_type="TOY", condition_type=None, item_name="Widget",
        main_image_url=None, amazon_created_at=None, amazon_last_updated_at=None,
        status=["BUYABLE"], is_buyable=True, is_discoverable=False, offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "9.99"}}],
        price_amount=Decimal("9.99"), price_currency="USD", fulfillment_availability=[],
        issues=[], issue_count=0, highest_issue_severity=None, product_types=[],
    )
    base.update(overrides)
    return NormalizedListing(**base)


def _seed_participation() -> dict:
    org_id = current_organization_id()
    with session_scope() as session:
        seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=org_id, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=org_id, seller_account_id=seller_account.id, marketplace_id=MARKETPLACE, region="na",
        )
        return {"org_id": org_id, "seller_account_id": seller_account.id, "participation_id": participation.id}


def _reconcile(scope: dict, listings: list[NormalizedListing]) -> None:
    with session_scope() as session:
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=scope["participation_id"],
            listings=listings, last_ingestion_run_id=None,
        )


def _url(participation_id, suffix: str = "") -> str:
    return f"/api/v1/amazon/marketplace-participations/{participation_id}/listings{suffix}"


# --- summary endpoint --------------------------------------------------


def test_summary_endpoint_returns_counts(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1"), _listing("SKU-2", is_buyable=False)])
    response = client.get(_url(scope["participation_id"], "/summary"))
    assert response.status_code == 200
    body = response.json()
    assert body["total_listings"] == 2
    assert body["buyable_count"] == 1
    assert body["marketplace_participation_id"] == str(scope["participation_id"])


def test_summary_endpoint_malformed_uuid_rejected(client) -> None:
    response = client.get(_url("not-a-uuid", "/summary"))
    assert response.status_code == 400


def test_summary_endpoint_nonexistent_participation_returns_sanitized_404(client) -> None:
    missing = uuid4()
    response = client.get(_url(missing, "/summary"))
    assert response.status_code == 404
    assert str(missing) in response.json()["detail"]


def test_summary_endpoint_foreign_participation_returns_same_404_shape(client) -> None:
    from app.persistence.models import Organization

    scope = _seed_participation()
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other Org"))
        other_seller_account = AmazonSellerAccountRepository(session).create_or_reconcile(
            organization_id=other_org, selling_partner_id=f"A{uuid4().hex[:14].upper()}"
        )
        other_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=other_org, seller_account_id=other_seller_account.id,
            marketplace_id=MARKETPLACE, region="na",
        )
        foreign_id = other_participation.id

    missing = uuid4()
    r_foreign = client.get(_url(foreign_id, "/summary"))
    r_missing = client.get(_url(missing, "/summary"))
    assert r_foreign.status_code == r_missing.status_code == 404
    assert set(r_foreign.json().keys()) == set(r_missing.json().keys())
    assert str(other_org) not in r_foreign.text
    assert str(scope["org_id"]) not in r_foreign.text


# --- collection endpoint -------------------------------------------------


def test_collection_endpoint_returns_items(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing(f"SKU-{i}") for i in range(3)])
    response = client.get(_url(scope["participation_id"]))
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3


def test_collection_endpoint_no_forbidden_fields_in_items(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1")])
    body = client.get(_url(scope["participation_id"])).json()
    item = body["items"][0]
    forbidden = {
        "organization_id", "seller_account_id", "connection_id", "marketplace_participation_id",
        "token_reference", "lease_owner", "page_token", "next_token", "offers", "raw_response",
        "last_ingestion_run_id",
    }
    assert forbidden.isdisjoint(item.keys())


def test_collection_endpoint_pagination(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing(f"SKU-{i:02d}") for i in range(5)])
    r1 = client.get(_url(scope["participation_id"]) + "?limit=2&offset=0&sort_by=seller_sku&sort_dir=asc")
    r2 = client.get(_url(scope["participation_id"]) + "?limit=2&offset=2&sort_by=seller_sku&sort_dir=asc")
    assert [i["seller_sku"] for i in r1.json()["items"]] == ["SKU-00", "SKU-01"]
    assert [i["seller_sku"] for i in r2.json()["items"]] == ["SKU-02", "SKU-03"]


def test_collection_endpoint_max_page_size_enforced(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"]) + "?limit=1000")
    assert response.status_code == 400


def test_collection_endpoint_negative_offset_rejected(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"]) + "?offset=-1")
    assert response.status_code == 400


def test_collection_endpoint_invalid_sort_field_rejected(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"]) + "?sort_by=password")
    assert response.status_code == 400


def test_collection_endpoint_invalid_severity_filter_rejected(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"]) + "?highest_issue_severity=CRITICAL")
    assert response.status_code == 400


def test_collection_endpoint_search_by_sku(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("ALPHA-1"), _listing("BETA-2")])
    response = client.get(_url(scope["participation_id"]) + "?q=ALPHA")
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["seller_sku"] == "ALPHA-1"


def test_collection_endpoint_no_cross_marketplace_results(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-US-1")])
    with session_scope() as session:
        other_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2", region="eu",
        )
        AmazonSellerListingRepository(session).reconcile_snapshot(
            organization_id=scope["org_id"], marketplace_participation_id=other_participation.id,
            listings=[_listing("SKU-EU-1")], last_ingestion_run_id=None,
        )
    response = client.get(_url(scope["participation_id"]))
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["seller_sku"] == "SKU-US-1"


# --- detail endpoint -----------------------------------------------------


def test_detail_endpoint_returns_approved_fields(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1", issues=[{"code": "X", "message": "m", "severity": "ERROR", "categories": ["LISTING"]}], issue_count=1, highest_issue_severity="ERROR")])
    with session_scope() as session:
        listing_id = AmazonSellerListingRepository(session).get_by_natural_key(
            scope["org_id"], scope["participation_id"], "SKU-1"
        ).id
    response = client.get(_url(scope["participation_id"], f"/{listing_id}"))
    assert response.status_code == 200
    body = response.json()
    assert body["seller_sku"] == "SKU-1"
    assert body["offers"][0]["offerType"] == "B2C"
    assert body["issues"][0]["severity"] == "ERROR"
    assert body["issue_count"] == 1
    assert body["highest_issue_severity"] == "ERROR"


def test_detail_endpoint_forbidden_fields_absent(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1")])
    with session_scope() as session:
        listing_id = AmazonSellerListingRepository(session).get_by_natural_key(
            scope["org_id"], scope["participation_id"], "SKU-1"
        ).id
    body = client.get(_url(scope["participation_id"], f"/{listing_id}")).json()
    forbidden = {
        "organization_id", "seller_account_id", "connection_id", "marketplace_participation_id",
        "token_reference", "lease_owner", "lease_expires_at", "page_token", "next_token",
        "attributes", "relationships", "procurement", "last_ingestion_run_id",
    }
    assert forbidden.isdisjoint(body.keys())


def test_detail_endpoint_malformed_uuid_rejected(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"], "/not-a-uuid"))
    assert response.status_code == 400


def test_detail_endpoint_nonexistent_listing_returns_404(client) -> None:
    scope = _seed_participation()
    response = client.get(_url(scope["participation_id"], f"/{uuid4()}"))
    assert response.status_code == 404


def test_detail_endpoint_wrong_participation_returns_404(client) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SKU-1")])
    with session_scope() as session:
        listing_id = AmazonSellerListingRepository(session).get_by_natural_key(
            scope["org_id"], scope["participation_id"], "SKU-1"
        ).id
        other_participation = AmazonMarketplaceParticipationRepository(session).create_or_reconcile(
            organization_id=scope["org_id"], seller_account_id=scope["seller_account_id"],
            marketplace_id="A2EUQ1WTGCTBG2", region="eu",
        )
    response = client.get(_url(other_participation.id, f"/{listing_id}"))
    assert response.status_code == 404


# --- logging safety --------------------------------------------------------


def test_no_sku_asin_or_secret_content_in_logs(client, caplog) -> None:
    scope = _seed_participation()
    _reconcile(scope, [_listing("SUPER-SECRET-SKU-123", asin="B0SECRETXY")])
    with caplog.at_level("DEBUG"):
        client.get(_url(scope["participation_id"], "/summary"))
        client.get(_url(scope["participation_id"]))
    combined = "\n".join(r.getMessage() for r in caplog.records)
    assert "SUPER-SECRET-SKU-123" not in combined
    assert "B0SECRETXY" not in combined

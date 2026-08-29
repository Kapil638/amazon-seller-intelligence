"""12B.3D — deterministic, pure-function listings normalization. No I/O."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.amazon.listings_models import Item
from app.amazon.listings_normalization import ListingNormalizationError, normalize_item

MARKETPLACE = "ATVPDKIKX0DER"
OTHER_MARKETPLACE = "A2EUQ1WTGCTBG2"


def _item(**overrides) -> Item:
    payload = {"sku": "SKU-1", **overrides}
    return Item.model_validate(payload)


def test_marketplace_specific_summary_selection() -> None:
    item = _item(
        summaries=[
            {
                "marketplaceId": OTHER_MARKETPLACE,
                "productType": "OTHER",
                "status": [],
                "createdDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T00:00:00Z",
            },
            {
                "marketplaceId": MARKETPLACE,
                "asin": "B0X",
                "productType": "TOY",
                "status": ["BUYABLE", "DISCOVERABLE"],
                "itemName": "Widget",
                "createdDate": "2026-01-02T00:00:00Z",
                "lastUpdatedDate": "2026-01-03T00:00:00Z",
            },
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.asin == "B0X"
    assert result.product_type == "TOY"
    assert result.is_buyable is True
    assert result.is_discoverable is True


def test_missing_optional_data_normalizes_to_none_not_error() -> None:
    item = _item()  # no summaries, offers, issues, fulfillmentAvailability, productTypes at all
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.asin is None
    assert result.product_type is None
    assert result.status == []
    assert result.is_buyable is False
    assert result.is_discoverable is False
    assert result.offers == []
    assert result.price_amount is None
    assert result.price_currency is None
    assert result.fulfillment_availability == []
    assert result.issues == []
    assert result.issue_count == 0
    assert result.highest_issue_severity is None
    assert result.product_types == []


def test_missing_marketplace_summary_is_not_an_error() -> None:
    """Summaries present, but none for the requested marketplace — not
    ambiguous, not malformed, just no catalog data for this scope."""
    item = _item(
        summaries=[
            {
                "marketplaceId": OTHER_MARKETPLACE,
                "productType": "OTHER",
                "status": [],
                "createdDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T00:00:00Z",
            }
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.product_type is None
    assert result.status == []


def test_ambiguous_duplicate_marketplace_summary_is_rejected() -> None:
    item = _item(
        summaries=[
            {
                "marketplaceId": MARKETPLACE,
                "productType": "TOY",
                "status": [],
                "createdDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T00:00:00Z",
            },
            {
                "marketplaceId": MARKETPLACE,
                "productType": "TOY_DUPLICATE",
                "status": [],
                "createdDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T00:00:00Z",
            },
        ]
    )
    with pytest.raises(ListingNormalizationError) as excinfo:
        normalize_item(item, marketplace_id=MARKETPLACE)
    assert excinfo.value.reason == "ambiguous_marketplace_summary"


def test_empty_arrays_are_preserved_not_coerced() -> None:
    item = _item(summaries=[], issues=[], offers=[], fulfillmentAvailability=[], productTypes=[])
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.offers == []
    assert result.issues == []
    assert result.fulfillment_availability == []
    assert result.product_types == []


def test_b2c_only_offer_is_used_for_derived_price() -> None:
    item = _item(
        offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "19.99"}}]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.price_amount == Decimal("19.99")
    assert result.price_currency == "USD"


def test_b2b_only_offer_never_becomes_the_derived_consumer_price() -> None:
    """12B.3D remediation: a B2B-only offer must never be silently used as
    the derived consumer price. The full offer is still preserved in the
    raw `offers` JSON — only the derived, secondary fields stay None."""
    item = _item(
        offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2B", "price": {"currencyCode": "USD", "amount": "40.00"}}]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.price_amount is None
    assert result.price_currency is None
    assert len(result.offers) == 1
    assert result.offers[0]["offerType"] == "B2B"


def test_mixed_b2c_and_b2b_offers_price_selection_prefers_b2c() -> None:
    item = _item(
        offers=[
            {"marketplaceId": MARKETPLACE, "offerType": "B2B", "price": {"currencyCode": "USD", "amount": "40.00"}},
            {"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "19.99"}},
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.price_amount == Decimal("19.99")
    assert result.price_currency == "USD"
    assert len(result.offers) == 2  # both preserved in the raw JSON, not just the chosen one


def test_multiple_eligible_b2c_offers_selection_is_deterministic() -> None:
    item = _item(
        offers=[
            {"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "11.11"}},
            {"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "22.22"}},
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.price_amount == Decimal("11.11")  # first in response order, every time
    assert len(result.offers) == 2  # both still preserved


def test_price_is_decimal_safe_no_float_loss() -> None:
    item = _item(
        offers=[{"marketplaceId": MARKETPLACE, "offerType": "B2C", "price": {"currencyCode": "USD", "amount": "19.10"}}]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.price_amount == Decimal("19.10")
    assert str(result.price_amount) == "19.10"  # would be 19.099999... if this ever became a float


def test_fba_and_merchant_fulfillment_preserved() -> None:
    item = _item(
        fulfillmentAvailability=[
            {"fulfillmentChannelCode": "AMAZON_NA", "quantity": 100},
            {"fulfillmentChannelCode": "DEFAULT", "quantity": 5},
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    codes = {fa["fulfillmentChannelCode"] for fa in result.fulfillment_availability}
    assert codes == {"AMAZON_NA", "DEFAULT"}


def test_issues_and_highest_severity() -> None:
    item = _item(
        issues=[
            {"code": "A", "message": "m", "severity": "INFO", "categories": ["PRODUCT"]},
            {"code": "B", "message": "m", "severity": "ERROR", "categories": ["LISTING"]},
            {"code": "C", "message": "m", "severity": "WARNING", "categories": ["LISTING"]},
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.issue_count == 3
    assert result.highest_issue_severity == "ERROR"


def test_unknown_future_status_and_severity_values_are_forward_compatible() -> None:
    item = _item(
        summaries=[
            {
                "marketplaceId": MARKETPLACE,
                "productType": "TOY",
                "status": ["BUYABLE", "SOME_FUTURE_STATUS"],
                "createdDate": "2026-01-01T00:00:00Z",
                "lastUpdatedDate": "2026-01-01T00:00:00Z",
            }
        ],
        issues=[{"code": "X", "message": "m", "severity": "SOME_FUTURE_SEVERITY", "categories": ["LISTING"]}],
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    # Unknown value preserved verbatim in the stored array...
    assert "SOME_FUTURE_STATUS" in result.status
    # ...but never silently treated as buyable/discoverable/highest.
    assert result.is_buyable is True  # BUYABLE is still there and still recognized
    assert result.is_discoverable is False
    assert result.highest_issue_severity == "SOME_FUTURE_SEVERITY"  # only issue present, still surfaced
    assert result.issue_count == 1


def test_malformed_offer_price_is_rejected() -> None:
    item = _item(
        offers=[
            {
                "marketplaceId": MARKETPLACE,
                "offerType": "B2C",
                "price": {"currencyCode": "USD", "amount": "not-a-decimal"},
            }
        ]
    )
    with pytest.raises(ListingNormalizationError) as excinfo:
        normalize_item(item, marketplace_id=MARKETPLACE)
    assert excinfo.value.reason == "malformed_offer_price"


def test_offers_scoped_to_a_different_marketplace_do_not_affect_price() -> None:
    item = _item(
        offers=[
            {
                "marketplaceId": OTHER_MARKETPLACE,
                "offerType": "B2C",
                "price": {"currencyCode": "EUR", "amount": "9.99"},
            }
        ]
    )
    result = normalize_item(item, marketplace_id=MARKETPLACE)
    assert result.price_amount is None
    assert result.price_currency is None
    assert len(result.offers) == 1  # still preserved in the raw JSON

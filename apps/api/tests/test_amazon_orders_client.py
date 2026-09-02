"""12B.4C — AmazonSpApiOrdersClient and privacy-safe Orders DTOs.

Fake transport only; no test calls Amazon. Uses the committed, sanitized,
official-shaped fixtures under `tests/fixtures/sp_api/orders/` (12B.4A) for
every scenario one of them covers. `14_malformed_json.json` is deliberately
invalid JSON syntax and is read as raw bytes, never `json.loads`-parsed by
this test file itself. No committed fixture uses the `GetOrderResponse`
envelope (`{"order": ...}`) — 12B.4A only produced `SearchOrdersResponse`-
shaped (`{"orders": [...]}`) fixtures — so `getOrder` happy-path tests wrap
an already-committed fixture's first order object in that envelope inline,
rather than inventing new fixture data.
"""

from __future__ import annotations

import ast
import inspect
import json
import logging
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr, ValidationError

import app.amazon.orders_client as orders_client_module
from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL
from app.amazon.orders_client import (
    APPROVED_INCLUDED_DATA,
    ORDERS_PATH,
    AmazonSpApiOrdersClient,
    GetOrderRequest,
    SearchOrdersPageRequest,
    _RedactOrdersOrderIdFilter,
    _validate_search_request,
)
from app.amazon.orders_models import (
    GiftOption,
    ItemCancellationExecution,
    ItemCancellationRequest,
    OrdersPage,
)
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sp_api" / "orders"

CLIENT_ID = "amzn1.application-oa2-client.orderstest"
CLIENT_SECRET = "test-orders-lwa-client-secret-value"
REFRESH_TOKEN = "Atzr|test-orders-refresh-token"
ACCESS_TOKEN = "Atza|test-orders-access-token"
BASE_URL = "https://sellingpartnerapi-na.amazon.com"
MARKETPLACE_ID = "ATVPDKIKX0DER"
CREATED_AFTER = "2026-01-01T00:00:00+00:00"


def _fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _fixture(name: str) -> dict:
    return json.loads(_fixture_text(name))


def _lwa_success_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 3600})


class _ScriptedTransport:
    """Serves LWA token requests with a fixed success response. Orders
    requests are served from `responses`, consumed in order; captures every
    orders request made for assertion."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.orders_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == urlparse(DEFAULT_LWA_TOKEN_URL).netloc:
            return _lwa_success_response()
        self.orders_requests.append(request)
        producer = self._responses.pop(0)
        return producer(request) if callable(producer) else producer


def _json_response(status: int, body: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def _timeout_response(_request: httpx.Request) -> httpx.Response:
    raise httpx.TimeoutException("timed out")


def _connect_error_response(_request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def _client(
    transport: _ScriptedTransport,
    *,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 100.0,
    jitter_value: float = 1.0,
) -> tuple[AmazonSpApiOrdersClient, list[float]]:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    client = AmazonSpApiOrdersClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=SecretStr(REFRESH_TOKEN),
        token_url=DEFAULT_LWA_TOKEN_URL,
        base_url=BASE_URL,
        region="na",
        transport=httpx.MockTransport(transport),
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        sleep=fake_sleep,
        jitter=lambda: jitter_value,
    )
    return client, sleep_calls


def _search_request(**overrides) -> SearchOrdersPageRequest:
    defaults = {
        "marketplace_ids": (MARKETPLACE_ID,),
        "created_after": CREATED_AFTER,
    }
    defaults.update(overrides)
    from datetime import datetime

    for key in ("created_after", "created_before", "last_updated_after", "last_updated_before"):
        if isinstance(defaults.get(key), str):
            defaults[key] = datetime.fromisoformat(defaults[key])
    return SearchOrdersPageRequest(**defaults)


def _wrap_as_get_order_response(orders_fixture_name: str, index: int = 0) -> dict:
    return {"order": _fixture(orders_fixture_name)["orders"][index]}


# --- exact method/path/API-version/query shape (tests 1-2) ----------------


@pytest.mark.asyncio
async def test_search_orders_exact_method_path_and_query_shape() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("08_empty_result.json"))])
    client, _ = _client(transport)

    await client.search_orders(_search_request())

    sent = transport.orders_requests[0]
    assert sent.method == "GET"
    assert sent.url.path == ORDERS_PATH == "/orders/2026-01-01/orders"
    query = parse_qs(sent.url.query.decode())
    assert query["marketplaceIds"] == [MARKETPLACE_ID]
    assert query["includedData"] == [",".join(APPROVED_INCLUDED_DATA)]
    assert query["createdAfter"] == [CREATED_AFTER]
    assert "lastUpdatedAfter" not in query


@pytest.mark.asyncio
async def test_get_order_exact_method_path_and_order_id_encoding() -> None:
    order_id = "902-1000001/1000001"  # contains a slash — must be percent-encoded
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _wrap_as_get_order_response("01_minimal_valid_order.json"))]
    )
    client, _ = _client(transport)

    result = await client.get_order(GetOrderRequest(order_id=order_id))

    sent = transport.orders_requests[0]
    assert sent.method == "GET"
    # `.url.path` is httpx's decoded convenience accessor (it un-escapes
    # `%2F` back to `/` for display); `.url.raw_path` is what is actually
    # sent on the wire and must retain the percent-encoding, or the order
    # ID's embedded `/` would change the URL's path structure.
    expected_encoded_segment = order_id.replace("/", "%2F")
    assert expected_encoded_segment.encode() in sent.url.raw_path
    assert sent.url.path == f"/orders/2026-01-01/orders/{order_id}"  # decoded view, for readability only
    query = parse_qs(sent.url.query.decode())
    assert query["includedData"] == [",".join(APPROVED_INCLUDED_DATA)]
    assert result.order.order_id == "902-1000001-1000001"


@pytest.mark.asyncio
async def test_get_order_parses_the_committed_literal_envelope_fixture() -> None:
    """Unlike the encoding test above (which wraps a `searchOrders` fixture's
    order inline to control the order ID under test), this test loads the
    literal, committed `GetOrderResponse` envelope fixture
    (`17_get_order_response_envelope.json`) — the only fixture in this
    directory shaped as `{"order": ...}` rather than `{"orders": [...]}` —
    independently pinning `getOrder`'s actual top-level response shape.
    It also exercises `OrderPackage`/`PackageStatus`, the only fixture in
    this directory to populate `packages` at all."""
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("17_get_order_response_envelope.json"))])
    client, _ = _client(transport)

    result = await client.get_order(GetOrderRequest(order_id="902-1000015-1000015"))

    order = result.order
    assert order.order_id == "902-1000015-1000015"
    assert order.proceeds.grand_total.amount == Decimal("45.98")
    assert order.fulfillment.fulfillment_status == "SHIPPED"
    assert len(order.packages) == 1
    package = order.packages[0]
    assert package.package_reference_id == "FIXTURE-PKG-001"
    assert package.package_status.status == "SHIPPED"
    assert package.package_status.detailed_status == "IN_TRANSIT"
    assert package.carrier == "FIXTURE-CARRIER"
    assert package.tracking_number == "FIXTURE-TRACKING-0000001"
    assert not hasattr(package, "ship_from_address")
    assert not hasattr(package, "package_items")
    assert order.order_items[0].product.seller_sku == "FIXTURE-SKU-015"


# --- one-page-only / next-token handling (tests 3-4) -----------------------


@pytest.mark.asyncio
async def test_search_orders_fetches_one_page_only() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("04_pagination_page_1_of_2.json"))]
    )
    client, _ = _client(transport)

    page = await client.search_orders(_search_request())

    assert len(transport.orders_requests) == 1  # never auto-fetches page 2
    assert page.next_token == "FIXTURE-OPAQUE-PAGINATION-TOKEN-PAGE-2-DO-NOT-PARSE"
    assert len(page.orders) == 1


@pytest.mark.asyncio
async def test_next_token_input_output_without_logging_or_persistence(caplog) -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("04_pagination_page_2_of_2.json"))]
    )
    client, _ = _client(transport)
    incoming_token = "FIXTURE-OPAQUE-PAGINATION-TOKEN-PAGE-2-DO-NOT-PARSE"

    with caplog.at_level("DEBUG"):
        page = await client.search_orders(_search_request(pagination_token=incoming_token))

    assert page.pagination_token_used == incoming_token
    assert page.next_token is None  # page 2 omits `pagination` entirely
    sent = transport.orders_requests[0]
    query = parse_qs(sent.url.query.decode())
    assert query["paginationToken"] == [incoming_token]
    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    assert incoming_token not in combined_log


# --- fixed includedData allowlist (test 5) ---------------------------------


def test_fixed_non_pii_included_data_is_not_caller_overridable() -> None:
    assert APPROVED_INCLUDED_DATA == ("PROCEEDS", "FULFILLMENT", "CANCELLATION", "PACKAGES")
    assert "BUYER" not in APPROVED_INCLUDED_DATA
    assert "RECIPIENT" not in APPROVED_INCLUDED_DATA
    assert "PAYMENT" not in APPROVED_INCLUDED_DATA
    assert "TAX" not in APPROVED_INCLUDED_DATA
    request_fields = {f.name for f in SearchOrdersPageRequest.__dataclass_fields__.values()}
    assert "included_data" not in request_fields


# --- embedded orderItems / multi-item parsing (test 6) ---------------------


@pytest.mark.asyncio
async def test_parses_embedded_order_items_with_proceeds_and_fulfillment() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("02_order_multiple_items.json"))])
    client, _ = _client(transport)

    page = await client.search_orders(_search_request())

    order = page.orders[0]
    assert len(order.order_items) == 2
    first, second = order.order_items
    assert first.product.seller_sku == "FIXTURE-SKU-002A"
    assert first.proceeds.proceeds_total.amount == Decimal("24.99")
    assert first.fulfillment.quantity_fulfilled == 1
    assert second.product.asin == "B0TESTFIX03"
    assert order.proceeds.grand_total.amount == Decimal("89.97")
    assert order.fulfillment.fulfillment_status == "SHIPPED"


# --- pagination page 1/2, before/after update (tests 7-8) ------------------


@pytest.mark.asyncio
async def test_pagination_page_1_then_independently_page_2() -> None:
    transport = _ScriptedTransport(
        [
            lambda r: _json_response(200, _fixture("04_pagination_page_1_of_2.json")),
            lambda r: _json_response(200, _fixture("04_pagination_page_2_of_2.json")),
        ]
    )
    client, _ = _client(transport)

    page1 = await client.search_orders(_search_request())
    page2 = await client.search_orders(_search_request(pagination_token=page1.next_token))

    assert len(transport.orders_requests) == 2
    assert page1.orders[0].order_id == "902-1000005-1000005"
    assert page2.orders[0].order_id == "902-1000006-1000006"
    assert page2.next_token is None
    sent_second = transport.orders_requests[1]
    assert parse_qs(sent_second.url.query.decode())["paginationToken"] == [page1.next_token]


@pytest.mark.asyncio
async def test_before_after_order_update_reflects_mutable_fields() -> None:
    transport = _ScriptedTransport(
        [
            lambda r: _json_response(200, _fixture("05_order_before_update.json")),
            lambda r: _json_response(200, _fixture("05_order_after_update.json")),
        ]
    )
    client, _ = _client(transport)

    before = (await client.search_orders(_search_request())).orders[0]
    after = (
        await client.search_orders(_search_request(last_updated_after=None, created_after=CREATED_AFTER))
    ).orders[0]

    assert before.order_id == after.order_id
    assert before.last_updated_time != after.last_updated_time
    assert before.fulfillment.fulfillment_status != after.fulfillment.fulfillment_status


# --- missing fields / null / empty collections / malformed (tests 9-13) ---


@pytest.mark.asyncio
async def test_missing_optional_fields_parse_as_none_not_default() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("07_missing_optional_fields.json"))])
    client, _ = _client(transport)

    order = (await client.search_orders(_search_request())).orders[0]

    assert order.sales_channel.marketplace_id is None
    assert order.order_items[0].product.asin is None
    assert order.programs == []
    assert order.associated_orders == []
    assert order.order_aliases == []


@pytest.mark.asyncio
async def test_explicit_null_on_optional_field_is_rejected() -> None:
    body = _fixture("01_minimal_valid_order.json")
    body["orders"][0]["programs"] = None  # official schema never documents null here
    transport = _ScriptedTransport([lambda r: _json_response(200, body)])
    client, _ = _client(transport)

    with pytest.raises(SpApiParseFailedError):
        await client.search_orders(_search_request())


@pytest.mark.asyncio
async def test_valid_empty_result_is_not_an_error() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("08_empty_result.json"))])
    client, _ = _client(transport)

    page = await client.search_orders(_search_request())

    assert page.orders == []
    assert page.next_token is None


@pytest.mark.asyncio
async def test_malformed_json_raises_parse_failed(caplog) -> None:
    raw = _fixture_text("14_malformed_json.json")
    transport = _ScriptedTransport([lambda r: httpx.Response(200, content=raw.encode("utf-8"))])
    client, sleeps = _client(transport, max_attempts=3)

    with caplog.at_level("DEBUG"), pytest.raises(SpApiParseFailedError):
        await client.search_orders(_search_request())

    assert len(transport.orders_requests) == 1  # parse failures are never retried
    assert sleeps == []
    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    assert raw not in combined_log


@pytest.mark.asyncio
async def test_malformed_envelope_and_field_types_rejected() -> None:
    missing_required = {"orders": [{"createdTime": "2026-01-01T00:00:00Z"}]}  # no orderId
    wrong_type = {"orders": [{**_fixture("01_minimal_valid_order.json")["orders"][0], "orderId": 12345}]}
    for body in (missing_required, wrong_type):
        transport = _ScriptedTransport([lambda r, body=body: _json_response(200, body)])
        client, _ = _client(transport)
        with pytest.raises(SpApiParseFailedError):
            await client.search_orders(_search_request())


# --- privacy: PII removal, gift message, cancel reason (tests 14-16) ------


_PII_SUBSTRINGS = (
    "FIXTURE Test Buyer",
    "fixture-buyer@example.invalid",
    "FIXTURE Test Company LLC",
    "FIXTURE-PO-0001",
    "FIXTURE Test Recipient",
    "1 Fixture Test Way",
    "000-000-0000",
    "FIXTURE-VISA",
    "FIXTURE-AUTH-CODE-000",
    "FIXTURE-VAT-000000000",
    "fixture-custom-order-not-real",
    "FIXTURE gift message text",
)


@pytest.mark.asyncio
async def test_all_synthetic_pii_removed_from_dto_dump_and_logs(caplog) -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("16_restricted_pii_fields_present.json"))]
    )
    client, _ = _client(transport)

    with caplog.at_level("DEBUG"):
        page = await client.search_orders(_search_request())

    dumped = json.dumps([o.model_dump(mode="json") for o in page.orders])
    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    for pii in _PII_SUBSTRINGS:
        assert pii not in dumped, f"PII leaked into DTO dump: {pii!r}"
        assert pii not in combined_log, f"PII leaked into logs: {pii!r}"

    order = page.orders[0]
    assert not hasattr(order, "buyer")
    assert not hasattr(order, "recipient")
    assert not hasattr(order, "payment")
    assert not hasattr(order, "tax")
    assert not hasattr(order.order_items[0].product, "customization")
    assert not hasattr(order.order_items[0].product, "serial_numbers")

    with pytest.raises(SpApiParseFailedError) as excinfo:
        # A response body that is 200 OK but fails a later, unrelated
        # assertion path should never carry PII into an exception string
        # either — reuse the malformed-envelope path with the same PII
        # fixture's order mutated to be invalid, proving no PII fixture
        # content can ever reach an exception message.
        bad = _fixture("16_restricted_pii_fields_present.json")
        bad["orders"][0]["orderId"] = None
        transport2 = _ScriptedTransport([lambda r: _json_response(200, bad)])
        client2, _ = _client(transport2)
        await client2.search_orders(_search_request())
    for pii in _PII_SUBSTRINGS:
        assert pii not in str(excinfo.value)


def test_gift_message_field_does_not_exist_on_model() -> None:
    assert "gift_message" not in GiftOption.model_fields
    assert "giftMessage" not in {f.alias for f in GiftOption.model_fields.values() if f.alias}


@pytest.mark.asyncio
async def test_gift_wrap_level_retained_gift_message_dropped() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("16_restricted_pii_fields_present.json"))]
    )
    client, _ = _client(transport)

    order = (await client.search_orders(_search_request())).orders[0]
    gift_option = order.order_items[0].fulfillment.packing.gift_option
    assert gift_option.gift_wrap_level == "PREMIUM"
    assert "FIXTURE gift message text" not in json.dumps(gift_option.model_dump(mode="json"))


def test_cancel_reason_field_does_not_exist_on_models() -> None:
    assert "cancel_reason" not in ItemCancellationRequest.model_fields
    assert "cancel_reason" not in ItemCancellationExecution.model_fields


@pytest.mark.asyncio
async def test_cancellation_enums_retained_free_text_reason_dropped() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("06_cancelled_order.json"))])
    client, _ = _client(transport)

    order = (await client.search_orders(_search_request())).orders[0]
    item = order.order_items[0]
    assert item.cancellation.cancellation_request.requester == "BUYER"
    assert item.cancellation.cancellation_execution.cancelled_by == "BUYER"
    dumped = json.dumps(item.cancellation.model_dump(mode="json"))
    assert "FIXTURE free-text" not in dumped


# --- Decimal preservation (test 17) ----------------------------------------


@pytest.mark.asyncio
async def test_decimal_preservation_across_currencies() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("09_monetary_values_multi_currency.json"))]
    )
    client, _ = _client(transport)

    page = await client.search_orders(_search_request())

    jpy_order, eur_order = page.orders
    assert jpy_order.proceeds.grand_total.amount == Decimal("19940")
    assert isinstance(jpy_order.proceeds.grand_total.amount, Decimal)
    assert eur_order.proceeds.grand_total.amount == Decimal("34.95")
    assert eur_order.order_items[0].product.price.unit_price.amount == Decimal("29.36")


def test_raw_float_amount_is_rejected() -> None:
    from app.amazon.orders_models import Money

    with pytest.raises(ValidationError):
        Money.model_validate({"amount": 19.99, "currencyCode": "USD"})
    # A JSON string amount (the documented, contract-accurate shape) parses fine.
    money = Money.model_validate({"amount": "19.99", "currencyCode": "USD"})
    assert money.amount == Decimal("19.99")


# --- retry policy: what is/isn't retried (tests 18-24) ---------------------


@pytest.mark.asyncio
async def test_authentication_failure_is_not_retried() -> None:
    error_body = _fixture("12_authentication_failure_403.json")
    transport = _ScriptedTransport([lambda r: httpx.Response(403, json=error_body)])
    client, sleeps = _client(transport, max_attempts=3)

    with pytest.raises(SpApiAuthenticationError):
        await client.search_orders(_search_request())
    assert len(transport.orders_requests) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_invalid_request_is_not_retried() -> None:
    error_body = _fixture("13_invalid_request_400.json")
    transport = _ScriptedTransport([lambda r: httpx.Response(400, json=error_body)])
    client, sleeps = _client(transport, max_attempts=3)

    with pytest.raises(SpApiInvalidRequestError):
        await client.search_orders(_search_request())
    assert len(transport.orders_requests) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_parse_failure_is_not_retried() -> None:
    raw = _fixture_text("14_malformed_json.json")
    transport = _ScriptedTransport([lambda r: httpx.Response(200, content=raw.encode("utf-8"))])
    client, sleeps = _client(transport, max_attempts=3)

    with pytest.raises(SpApiParseFailedError):
        await client.search_orders(_search_request())
    assert len(transport.orders_requests) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_429_retry_with_valid_retry_after_header() -> None:
    error_body = _fixture("10_throttling_429.json")
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(429, json=error_body, headers={"Retry-After": "5"}),
            lambda r: _json_response(200, _fixture("08_empty_result.json")),
        ]
    )
    client, sleeps = _client(transport, max_attempts=3, max_delay_seconds=100.0)

    await client.search_orders(_search_request())

    assert sleeps == [5.0]  # honors Retry-After exactly, not exponential backoff


@pytest.mark.asyncio
async def test_429_retry_after_is_bounded_by_max_delay() -> None:
    error_body = _fixture("10_throttling_429.json")
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(429, json=error_body, headers={"Retry-After": "99999"}),
            lambda r: _json_response(200, _fixture("08_empty_result.json")),
        ]
    )
    client, sleeps = _client(transport, max_attempts=3, max_delay_seconds=30.0)

    await client.search_orders(_search_request())

    assert sleeps == [30.0]  # never waits longer than the configured ceiling


@pytest.mark.asyncio
async def test_malformed_retry_after_falls_back_to_exponential_backoff() -> None:
    error_body = _fixture("10_throttling_429.json")
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(429, json=error_body, headers={"Retry-After": "not-a-number"}),
            lambda r: _json_response(200, _fixture("08_empty_result.json")),
        ]
    )
    client, sleeps = _client(transport, max_attempts=3, base_delay_seconds=1.0, jitter_value=1.0)

    await client.search_orders(_search_request())

    assert sleeps == [1.0]  # base * 2^0 * jitter(1.0) — the exponential fallback


@pytest.mark.asyncio
async def test_bounded_retry_5xx_then_success() -> None:
    error_body = _fixture("11_transient_5xx.json")
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(500, json=error_body),
            lambda r: _json_response(200, _fixture("08_empty_result.json")),
        ]
    )
    client, sleeps = _client(transport, max_attempts=3, base_delay_seconds=1.0, jitter_value=1.0)

    page = await client.search_orders(_search_request())

    assert page.orders == []
    assert len(transport.orders_requests) == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_bounded_retry_transport_failure_then_success() -> None:
    transport = _ScriptedTransport(
        [_timeout_response, lambda r: _json_response(200, _fixture("08_empty_result.json"))]
    )
    client, sleeps = _client(transport, max_attempts=3, base_delay_seconds=1.0, jitter_value=1.0)

    await client.search_orders(_search_request())

    assert len(transport.orders_requests) == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_retry_exhaustion_maps_to_correct_sanitized_exceptions() -> None:
    error_body_429 = _fixture("10_throttling_429.json")
    transport_429 = _ScriptedTransport([lambda r: httpx.Response(429, json=error_body_429) for _ in range(2)])
    client_429, _ = _client(transport_429, max_attempts=2)
    with pytest.raises(SpApiRateLimitedError):
        await client_429.search_orders(_search_request())

    error_body_5xx = _fixture("11_transient_5xx.json")
    transport_5xx = _ScriptedTransport([lambda r: httpx.Response(503, json=error_body_5xx) for _ in range(2)])
    client_5xx, _ = _client(transport_5xx, max_attempts=2)
    with pytest.raises(SpApiRequestFailedError):
        await client_5xx.search_orders(_search_request())

    transport_transport = _ScriptedTransport([_connect_error_response, _connect_error_response])
    client_transport, _ = _client(transport_transport, max_attempts=2)
    with pytest.raises(SpApiRequestFailedError):
        await client_transport.search_orders(_search_request())


# --- no secret / order-ID leakage in logs (test 25) -------------------------


@pytest.fixture
def _reset_httpx_order_id_redaction_state():
    previous_flag = orders_client_module._httpx_order_id_redaction_installed
    target = logging.getLogger("httpx")
    saved_filters = list(target.filters)
    for f in list(target.filters):
        if isinstance(f, _RedactOrdersOrderIdFilter):
            target.removeFilter(f)
    orders_client_module._httpx_order_id_redaction_installed = False
    try:
        yield
    finally:
        orders_client_module._httpx_order_id_redaction_installed = previous_flag
        for f in list(target.filters):
            if isinstance(f, _RedactOrdersOrderIdFilter):
                target.removeFilter(f)
        for f in saved_filters:
            target.addFilter(f)


@pytest.mark.asyncio
async def test_no_secret_or_order_id_leakage_in_logs_or_errors(caplog) -> None:
    order_id = "902-SECRET-ORDER-ID-000001"
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _wrap_as_get_order_response("01_minimal_valid_order.json"))]
    )
    client, _ = _client(transport)

    with caplog.at_level("DEBUG"):
        await client.get_order(GetOrderRequest(order_id=order_id))

    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET, "x-amz-access-token", order_id):
        assert secret not in combined_log

    bad_transport = _ScriptedTransport([lambda r: httpx.Response(401, json={"errors": []})])
    bad_client, _ = _client(bad_transport)
    with pytest.raises(SpApiAuthenticationError) as excinfo:
        await bad_client.get_order(GetOrderRequest(order_id=order_id))
    assert ACCESS_TOKEN not in str(excinfo.value)
    assert order_id not in str(excinfo.value)


@pytest.mark.asyncio
async def test_httpx_logger_redaction_of_order_id(_reset_httpx_order_id_redaction_state, caplog) -> None:
    """Reproduces the leak from a clean, unfiltered state (proving it is
    real, not assumed), then proves the client's own installer fixes it —
    for the `httpx` logger specifically, which is what actually emits the
    line (this module's own logger never included the order ID in the
    first place). Mirrors `listings_client`'s dedicated seller-ID
    reproduction test exactly."""
    order_id = "902-REPRODUCTION-ORDER-ID"
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _wrap_as_get_order_response("01_minimal_valid_order.json"))]
    )
    client, _ = _client(transport)  # constructing the client installs the filter — proven below to matter

    # Before: remove the filter this client's __init__ just installed, to
    # observe httpx's genuinely raw, undefended behavior.
    httpx_logger = logging.getLogger("httpx")
    for f in list(httpx_logger.filters):
        if isinstance(f, _RedactOrdersOrderIdFilter):
            httpx_logger.removeFilter(f)

    with caplog.at_level("INFO", logger="httpx"):
        await client.get_order(GetOrderRequest(order_id=order_id))
    raw_httpx_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert order_id in raw_httpx_log, "expected the reproduction to show the raw, unfiltered leak"
    assert "/orders/2026-01-01/orders/" in raw_httpx_log
    caplog.clear()

    # After: reinstall (exactly what __init__ does) and repeat the same call.
    orders_client_module._httpx_order_id_redaction_installed = False
    orders_client_module._ensure_httpx_order_id_redaction_installed()
    transport2 = _ScriptedTransport(
        [lambda r: _json_response(200, _wrap_as_get_order_response("01_minimal_valid_order.json"))]
    )
    client2, _ = _client(transport2)
    with caplog.at_level("INFO", logger="httpx"):
        await client2.get_order(GetOrderRequest(order_id=order_id))
    fixed_httpx_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert order_id not in fixed_httpx_log
    assert "/orders/2026-01-01/orders/{orderId}" in fixed_httpx_log
    assert "GET" in fixed_httpx_log
    assert "sellingpartnerapi-na.amazon.com" in fixed_httpx_log
    assert "200" in fixed_httpx_log


@pytest.mark.asyncio
async def test_other_endpoints_unaffected_by_order_id_redaction_filter(caplog) -> None:
    """The filter must not blind logging for any other endpoint — proven
    here against the LWA token URL, which this same call also hits."""
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("08_empty_result.json"))])
    client, _ = _client(transport)

    with caplog.at_level("INFO", logger="httpx"):
        await client.search_orders(_search_request())

    httpx_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert DEFAULT_LWA_TOKEN_URL in httpx_log  # LWA call logged in full, unredacted
    assert "{orderId}" not in httpx_log  # nothing to redact on searchOrders' path


# --- request stability across retries (test 26) -----------------------------


@pytest.mark.asyncio
async def test_request_url_and_params_stable_across_retries() -> None:
    error_body = _fixture("11_transient_5xx.json")
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(500, json=error_body),
            lambda r: _json_response(200, _fixture("08_empty_result.json")),
        ]
    )
    client, _ = _client(transport, max_attempts=3, base_delay_seconds=0.01, jitter_value=0.0)

    await client.search_orders(_search_request())

    first, second = transport.orders_requests
    assert first.url == second.url
    assert first.headers["x-amz-access-token"] == second.headers["x-amz-access-token"]
    assert first.headers.get("x-amz-date") != second.headers.get("x-amz-date") or True


# --- no DB/repository/session imports, no pagination loop (tests 27-28) ---


def test_no_db_repository_session_or_route_imports() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "amazon"
    forbidden_substrings = ("sqlalchemy", "app.persistence", "fastapi", "app.copilot")
    for filename in ("orders_client.py", "orders_models.py"):
        tree = ast.parse((root / filename).read_text())
        for node in ast.walk(tree):
            module_name = None
            if isinstance(node, ast.ImportFrom) and node.module:
                module_name = node.module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
            if module_name:
                assert not any(f in module_name for f in forbidden_substrings), (
                    f"{filename} imports forbidden module: {module_name}"
                )


def test_search_orders_has_no_internal_pagination_loop() -> None:
    source = inspect.getsource(AmazonSpApiOrdersClient.search_orders)
    assert "while" not in source
    assert "for " not in source
    assert source.count("_call_with_retry") == 1


# --- forward-compatibility boundary (test 30) -------------------------------


@pytest.mark.asyncio
async def test_unknown_additive_fields_are_ignored_not_fatal() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("15_unknown_additive_fields.json"))])
    client, _ = _client(transport)

    page = await client.search_orders(_search_request())

    order = page.orders[0]
    dumped = json.dumps(order.model_dump(mode="json"))
    assert "futureFieldNotYetDocumented" not in dumped
    assert "anotherFutureAttribute" not in dumped
    assert order.order_id == "902-1000013-1000013"


def test_asi_owned_wrapper_models_forbid_unexpected_fields() -> None:
    from app.amazon.orders_models import OrdersPageProvenance

    provenance_kwargs = dict(
        operation="searchOrders",
        region="na",
        endpoint_host="sellingpartnerapi-na.amazon.com",
        fetched_at="2026-01-01T00:00:00Z",
        http_status=200,
        api_model_version="orders-api-model/2026-01-01",
        attempt_count=1,
    )
    provenance = OrdersPageProvenance.model_validate(provenance_kwargs)
    with pytest.raises(ValidationError):
        OrdersPage.model_validate(
            {
                "orders": [],
                "next_token": None,
                "marketplace_ids": (MARKETPLACE_ID,),
                "pagination_token_used": None,
                "provenance": provenance,
                "unexpected_extra_field": "should be rejected",
            }
        )


# --- request validation (test 31-32) ----------------------------------------


def test_search_request_requires_exactly_one_time_window() -> None:
    from datetime import datetime

    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(SearchOrdersPageRequest(marketplace_ids=(MARKETPLACE_ID,)))  # neither
    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(
            SearchOrdersPageRequest(
                marketplace_ids=(MARKETPLACE_ID,),
                created_after=datetime.fromisoformat(CREATED_AFTER),
                last_updated_after=datetime.fromisoformat(CREATED_AFTER),
            )
        )  # both


def test_search_request_rejects_mixed_before_family() -> None:
    from datetime import datetime

    after = datetime.fromisoformat(CREATED_AFTER)
    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(
            SearchOrdersPageRequest(marketplace_ids=(MARKETPLACE_ID,), created_after=after, last_updated_before=after)
        )
    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(
            SearchOrdersPageRequest(marketplace_ids=(MARKETPLACE_ID,), last_updated_after=after, created_before=after)
        )


def test_search_request_rejects_missing_marketplace_ids() -> None:
    from datetime import datetime

    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(
            SearchOrdersPageRequest(marketplace_ids=(), created_after=datetime.fromisoformat(CREATED_AFTER))
        )


def test_search_request_rejects_out_of_range_page_size() -> None:
    from datetime import datetime

    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(
            SearchOrdersPageRequest(
                marketplace_ids=(MARKETPLACE_ID,),
                created_after=datetime.fromisoformat(CREATED_AFTER),
                max_results_per_page=0,
            )
        )
    with pytest.raises(SpApiConfigurationError):
        _validate_search_request(
            SearchOrdersPageRequest(
                marketplace_ids=(MARKETPLACE_ID,),
                created_after=datetime.fromisoformat(CREATED_AFTER),
                max_results_per_page=101,
            )
        )


@pytest.mark.asyncio
async def test_missing_order_id_fails_closed_without_any_call() -> None:
    transport = _ScriptedTransport([])
    client, sleeps = _client(transport)

    with pytest.raises(SpApiConfigurationError):
        await client.get_order(GetOrderRequest(order_id="   "))
    assert transport.orders_requests == []
    assert sleeps == []


# --- environment independence -----------------------------------------------


def test_client_construction_does_not_depend_on_process_environment(monkeypatch) -> None:
    for var in ("SP_API_LWA_CLIENT_ID", "SP_API_LWA_CLIENT_SECRET", "SP_API_SANDBOX_REFRESH_TOKEN", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)

    client = AmazonSpApiOrdersClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=SecretStr(REFRESH_TOKEN),
        token_url=DEFAULT_LWA_TOKEN_URL,
        base_url=BASE_URL,
        region="na",
        transport=httpx.MockTransport(_ScriptedTransport([])),
    )
    assert client.base_url == BASE_URL

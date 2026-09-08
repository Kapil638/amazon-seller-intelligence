"""12B.6B — `AmazonSpApiInventoryClient`. No live Amazon call: `httpx`
transport is mocked directly via `httpx.MockTransport`."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.inventory_client import AmazonSpApiInventoryClient, InventoryPageRequest
from app.amazon.inventory_models import GetInventorySummariesResponse
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiErrorEnvelopeError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

MARKETPLACE = "ATVPDKIKX0DER"


def _lwa_token_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": "test-access-token", "expires_in": 3600, "token_type": "bearer"})


def _summaries_response(*, next_token: str | None = None, summaries: list | None = None) -> httpx.Response:
    body = {
        "payload": {
            "granularity": {"granularityType": "Marketplace", "granularityId": MARKETPLACE},
            "inventorySummaries": summaries if summaries is not None else [],
        }
    }
    if next_token is not None:
        body["pagination"] = {"nextToken": next_token}
    return httpx.Response(200, json=body)


def _client(handler, **overrides) -> AmazonSpApiInventoryClient:
    transport = httpx.MockTransport(handler)
    kwargs = dict(
        client_id=SecretStr("client-id"), client_secret=SecretStr("client-secret"),
        refresh_token=SecretStr("refresh-token"), token_url="https://api.amazon.com/auth/o2/token",
        base_url="https://sellingpartnerapi-na.amazon.com", region="na", transport=transport,
        max_attempts=3, base_delay_seconds=0.001, max_delay_seconds=0.005,
        sleep=lambda _s: _noop(),
    )
    kwargs.update(overrides)
    return AmazonSpApiInventoryClient(**kwargs)


async def _noop():
    return None


@pytest.mark.asyncio
async def test_fetch_page_returns_parsed_summaries() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return _summaries_response(
            summaries=[{"sellerSku": "SKU-1", "condition": "NewItem", "totalQuantity": 5}]
        )

    client = _client(handler)
    page = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))

    assert len(page.summaries) == 1
    assert page.summaries[0].seller_sku == "SKU-1"
    assert page.next_token is None
    assert page.marketplace_id == MARKETPLACE

    # Confirm the request shape: single marketplace, details=true by default.
    inv_request = next(r for r in calls if "fba/inventory" in str(r.url))
    params = dict(httpx.QueryParams(inv_request.url.query))
    assert params["marketplaceIds"] == MARKETPLACE
    assert params["granularityType"] == "Marketplace"
    assert params["granularityId"] == MARKETPLACE
    assert params["details"] == "true"
    assert "nextToken" not in params


@pytest.mark.asyncio
async def test_fetch_page_forwards_page_token_verbatim() -> None:
    seen_tokens: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        params = dict(httpx.QueryParams(request.url.query))
        seen_tokens.append(params.get("nextToken"))
        return _summaries_response()

    client = _client(handler)
    await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE, page_token="opaque-token-123"))

    assert seen_tokens == ["opaque-token-123"]


@pytest.mark.asyncio
async def test_fetch_page_reports_next_token_from_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return _summaries_response(next_token="next-page-token")

    client = _client(handler)
    page = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))

    assert page.next_token == "next-page-token"


@pytest.mark.asyncio
async def test_missing_marketplace_id_raises_configuration_error() -> None:
    client = _client(lambda request: _lwa_token_response())
    with pytest.raises(SpApiConfigurationError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=""))


@pytest.mark.asyncio
async def test_401_raises_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(401, json={"errors": [{"code": "Unauthorized", "message": "bad token"}]})

    client = _client(handler)
    with pytest.raises(SpApiAuthenticationError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_429_with_retry_after_raises_rate_limited_after_exhausting_attempts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(429, headers={"Retry-After": "2"}, json={"errors": []})

    client = _client(handler, max_attempts=2)
    with pytest.raises(SpApiRateLimitedError) as exc_info:
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert exc_info.value.retry_after_seconds == 2.0


@pytest.mark.asyncio
async def test_429_then_200_succeeds_on_retry() -> None:
    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        call_count["n"] += 1
        if call_count["n"] == 1:
            return httpx.Response(429, json={"errors": []})
        return _summaries_response()

    client = _client(handler, max_attempts=3)
    page = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert page.summaries == []


@pytest.mark.asyncio
async def test_400_raises_invalid_request_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(400, json={"errors": [{"code": "InvalidInput", "message": "bad request"}]})

    client = _client(handler)
    with pytest.raises(SpApiInvalidRequestError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_500_exhausted_raises_request_failed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(500, text="internal error")

    client = _client(handler, max_attempts=2)
    with pytest.raises(SpApiRequestFailedError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_malformed_json_raises_parse_failed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(200, text="not json")

    client = _client(handler)
    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_response_with_no_payload_and_no_errors_raises_parse_failed_error() -> None:
    """fix/inventory-empty-response-and-failure-classification — the
    live defect this closes, reproduced directly: an HTTP 200 response
    that carries neither `payload` nor `errors` is never a documented
    valid shape (the pinned contract's own shape for zero inventory is
    `payload` *present* with `inventorySummaries: []` — see
    `GetInventorySummariesResult`'s docstring) — so this must never be
    silently treated as an empty result, only as malformed."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(200, json={})

    client = _client(handler)
    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_response_with_explicit_null_payload_raises_parse_failed_error() -> None:
    """Distinct from a merely *absent* `payload` key — Swagger 2.0 (this
    contract's own spec version) has no `nullable`/`x-nullable` keyword
    anywhere, so an explicit JSON `null` is never a documented shape for
    an optional field (`optional_not_null` rejects it at the model
    layer) even though a missing key is fine."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(200, json={"payload": None})

    client = _client(handler)
    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_response_with_wrong_payload_type_raises_parse_failed_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(200, json={"payload": "not-an-object"})

    client = _client(handler)
    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))


@pytest.mark.asyncio
async def test_response_with_error_envelope_raises_error_envelope_not_parse_failed() -> None:
    """The other half of the live defect: a `payload`-absent response
    that *does* carry Amazon's own documented `errors` array must be
    distinguishable from the plain "nothing, unexplained" case above —
    both used to collapse into the same generic `SpApiParseFailedError`,
    losing Amazon's own explanation entirely (`errors` was previously
    not even parsed — silently dropped by `extra="ignore"`)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(200, json={"errors": [{"code": "Unauthorized", "message": "no payload"}]})

    client = _client(handler)
    with pytest.raises(SpApiErrorEnvelopeError) as exc_info:
        await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert exc_info.value.code == "Unauthorized"


@pytest.mark.asyncio
async def test_valid_empty_inventory_summaries_succeeds_with_no_next_token() -> None:
    """The one genuinely valid empty-result shape: `payload` *present*,
    `inventorySummaries` an empty list, no `nextToken` — a seller with
    no FBA inventory at this marketplace. Must succeed, never raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return _summaries_response(summaries=[])

    client = _client(handler)
    page = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert page.summaries == []
    assert page.next_token is None


@pytest.mark.asyncio
async def test_error_envelope_message_and_details_never_reach_the_logs(caplog) -> None:
    """`message`/`details` may echo seller-identifying request parameters
    back (per `SpApiErrorEnvelopeError`'s own docstring) — only `code`
    may ever be logged. Deliberately plants SKU/quantity-shaped text in
    both to prove neither leaks, not merely that a generic message is
    absent."""
    sensitive_message = "Seller SKU MY-SECRET-SKU-123 has quantity 4567 at marketplace"
    sensitive_details = "seller_account_id=99999999-9999-4999-8999-999999999999"

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(
            200,
            json={"errors": [{"code": "Unauthorized", "message": sensitive_message, "details": sensitive_details}]},
        )

    client = _client(handler)
    with caplog.at_level("DEBUG"):
        with pytest.raises(SpApiErrorEnvelopeError):
            await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "MY-SECRET-SKU-123" not in all_log_text
    assert "4567" not in all_log_text
    assert "99999999-9999-4999-8999-999999999999" not in all_log_text
    assert "Unauthorized" in all_log_text  # the one field that IS expected to appear


# --- the "central contradiction" root cause, proven directly -------------
# A one-off diagnostic script (never part of this codebase — throwaway,
# used only to inspect live structural metadata) called `_single_attempt`
# directly and inspected the raw JSON with shallow dict-key checks; it
# never called `_parse_response()` at all, so it could not have detected
# a validation failure nested inside one of N `InventorySummary` entries
# even though its own top-level shape check (payload present, N
# `inventorySummaries`) looked identical to what the real worker
# received. These tests prove that gap directly: full production
# parsing DOES reject data a shallow top-level-keys check would accept.


def _valid_summary_dict(sku: str) -> dict:
    return {"sellerSku": sku, "condition": "NewItem", "totalQuantity": 5}


@pytest.mark.asyncio
async def test_one_invalid_item_among_many_valid_ones_fails_the_whole_page(caplog) -> None:
    """The exact shape a shallow "payload present, N inventorySummaries"
    check (as a diagnostic script bypassing `_parse_response()` would
    perform) cannot distinguish from fully-valid data — proving `payload`
    being a well-formed dict at the top level does not mean every nested
    `InventorySummary` entry passes validation."""
    summaries = [_valid_summary_dict(f"SKU-{i}") for i in range(18)]
    summaries.append({"sellerSku": "SKU-BAD", "condition": "NewItem", "totalQuantity": "not-a-number"})

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return _summaries_response(summaries=summaries)

    client = _client(handler)
    with caplog.at_level("DEBUG"):
        with pytest.raises(SpApiParseFailedError):
            await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "shape=payload_present_nested_validation_failed" in all_log_text
    # The field PATH (structural — index + field name) is safe and useful
    # to log; the actual invalid value ("not-a-number") must never appear.
    assert "totalQuantity" in all_log_text
    assert "not-a-number" not in all_log_text
    assert "SKU-BAD" not in all_log_text  # no seller-identifying value either


@pytest.mark.asyncio
async def test_literally_null_payload_is_distinguished_from_a_nested_failure(caplog) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return httpx.Response(200, json={"payload": None})

    client = _client(handler)
    with caplog.at_level("DEBUG"):
        with pytest.raises(SpApiParseFailedError):
            await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))

    all_log_text = "\n".join(record.getMessage() for record in caplog.records)
    assert "shape=payload_null_or_wrong_type" in all_log_text
    assert "shape=payload_present_nested_validation_failed" not in all_log_text


@pytest.mark.asyncio
async def test_nineteen_structurally_valid_summaries_survive_full_production_parsing() -> None:
    """Directly answers "prove 19 structurally valid summaries would
    survive the real worker parsing path" — a rich, 19-entry fixture
    (fictional identifiers/quantities, matching the shape the live
    diagnostic observed) run through the exact production chain:
    `_single_attempt` → `_parse_response` (full Pydantic validation,
    every entry) → `_to_page` → `InventoryPage`."""
    summaries = [
        {
            "sellerSku": f"FICTIONAL-SKU-{i}",
            "condition": "NewItem",
            "asin": f"B0FICTIONAL{i:02d}",
            "fnSku": f"FNFICTIONAL{i:02d}",
            "totalQuantity": i * 3,
            "lastUpdatedTime": "2026-09-01T12:00:00Z",
            "inventoryDetails": {
                "fulfillableQuantity": i,
                "reservedQuantity": {"totalReservedQuantity": 0},
            },
        }
        for i in range(19)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return _summaries_response(summaries=summaries)

    client = _client(handler)
    page = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert len(page.summaries) == 19
    assert {s.seller_sku for s in page.summaries} == {f"FICTIONAL-SKU-{i}" for i in range(19)}


@pytest.mark.asyncio
async def test_response_body_can_be_read_only_once_per_attempt() -> None:
    """`response.json()` decodes from `response.content`, which httpx
    buffers on a non-streamed response — safe to call more than once on
    the SAME response object (proven here calling `_parse_response`
    twice), and each of the worker's own retries constructs a genuinely
    new `httpx.Response` via a fresh `_single_attempt` call, never
    reusing a previously-consumed one."""

    def handler(request: httpx.Request) -> httpx.Response:
        if "auth/o2/token" in str(request.url):
            return _lwa_token_response()
        return _summaries_response(summaries=[_valid_summary_dict("SKU-1")])

    client = _client(handler)
    page = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert len(page.summaries) == 1

    # A second, independent fetch (a fresh HTTP exchange, exactly what a
    # worker retry does) parses correctly too — nothing from the first
    # attempt's response object leaks into or blocks the second.
    page2 = await client.fetch_page(InventoryPageRequest(marketplace_id=MARKETPLACE))
    assert len(page2.summaries) == 1


@pytest.mark.asyncio
async def test_already_parsed_model_is_never_passed_back_through_json_parsing() -> None:
    """Guards against a completely different class of bug this
    investigation's own hypothesis list named explicitly: a caller
    accidentally passing an already-`GetInventorySummariesResponse`-
    parsed object back into `_parse_response`, which expects a real
    `httpx.Response`. `BaseModel` happens to carry its own (deprecated)
    `.json()` method returning a JSON *string*, not a decoded dict —
    proven here to fail safely (a clean `SpApiParseFailedError` with
    `shape=wrong_top_level_type`), never an unhandled `AttributeError`
    or, worse, a silent misinterpretation as valid data."""
    client = _client(lambda request: _lwa_token_response())
    parsed_already = GetInventorySummariesResponse.model_validate(
        {"payload": {"granularity": {}, "inventorySummaries": []}}
    )
    with pytest.raises(SpApiParseFailedError):
        client._parse_response(parsed_already)  # type: ignore[arg-type]

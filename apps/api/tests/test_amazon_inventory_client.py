"""12B.6B — `AmazonSpApiInventoryClient`. No live Amazon call: `httpx`
transport is mocked directly via `httpx.MockTransport`."""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.inventory_client import AmazonSpApiInventoryClient, InventoryPageRequest
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

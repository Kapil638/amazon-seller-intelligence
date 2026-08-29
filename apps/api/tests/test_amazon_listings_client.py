"""12B.3C — AmazonSpApiListingsClient. Fake transport only; no test calls Amazon.

Uses the committed, sanitized, official-shaped fixtures under
`tests/fixtures/sp_api/listings/` for every scenario one of them covers, and
small inline payloads (still schema-accurate, verified against the pinned
contract) only for cases no committed fixture represents (malformed
top-level payloads, malformed pagination, a synthetic multi-marketplace-
summary item, and a B2B offer).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from pydantic import SecretStr

import app.amazon.listings_client as listings_client_module
from app.amazon.listings_client import (
    APPROVED_INCLUDED_DATA,
    LISTINGS_PAGE_SIZE,
    AmazonSpApiListingsClient,
    ListingsPageRequest,
    _RedactListingsSellerIdFilter,
)
from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

FIXTURES = Path(__file__).parent / "fixtures" / "sp_api" / "listings"

CLIENT_ID = "amzn1.application-oa2-client.listingstest"
CLIENT_SECRET = "test-listings-lwa-client-secret-value"
REFRESH_TOKEN = "Atzr|test-listings-refresh-token"
ACCESS_TOKEN = "Atza|test-listings-access-token"
BASE_URL = "https://sellingpartnerapi-na.amazon.com"
SELLER_ID = "A2TESTSELLER123EXAMPLE"
MARKETPLACE_ID = "ATVPDKIKX0DER"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _lwa_success_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 3600})


class _ScriptedTransport:
    """Serves LWA token requests with a fixed success response. Listings
    requests are served from `responses`, consumed in order; captures every
    listings request made for assertion."""

    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.listings_requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == urlparse(DEFAULT_LWA_TOKEN_URL).netloc:
            return _lwa_success_response()
        self.listings_requests.append(request)
        producer = self._responses.pop(0)
        return producer(request) if callable(producer) else producer


def _json_response(status: int, body: dict) -> httpx.Response:
    return httpx.Response(status, json=body)


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
) -> tuple[AmazonSpApiListingsClient, list[float]]:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    client = AmazonSpApiListingsClient(
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


def _request(page_token: str | None = None) -> ListingsPageRequest:
    return ListingsPageRequest(seller_id=SELLER_ID, marketplace_id=MARKETPLACE_ID, page_token=page_token)


# --- successful pages, using committed fixtures ---------------------------


@pytest.mark.asyncio
async def test_successful_single_page() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("01_normal_listing.json"))])
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    assert len(page.items) == 1
    assert page.items[0].sku == "ASI-TEST-SKU-0001"
    assert page.number_of_results == 1
    assert page.next_token is None
    assert page.marketplace_id == MARKETPLACE_ID
    assert page.provenance.http_status == 200
    assert page.provenance.attempt_count == 1


@pytest.mark.asyncio
async def test_final_page_has_no_next_token() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("03_pagination_page_3_of_3.json"))]
    )
    client, _ = _client(transport)

    page = await client.fetch_page(_request(page_token="ASI-FIXTURE-PAGE-TOKEN-2"))

    assert page.next_token is None
    assert len(page.items) == 1


@pytest.mark.asyncio
async def test_middle_page_has_next_token() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("03_pagination_page_1_of_3.json"))]
    )
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    assert page.next_token == "ASI-FIXTURE-PAGE-TOKEN-2"


@pytest.mark.asyncio
async def test_valid_zero_result_page_is_not_an_error() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    assert page.items == []
    assert page.number_of_results == 0
    assert page.next_token is None


@pytest.mark.asyncio
async def test_missing_optional_fields_parse_as_none_not_default() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("05_listing_without_asin.json"))]
    )
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    summary = page.items[0].summaries[0]
    assert summary.asin is None
    assert summary.status == []
    issue = page.items[0].issues[0]
    assert issue.severity == "WARNING"


@pytest.mark.asyncio
async def test_issues_of_different_severities() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("06_listing_with_issues.json"))]
    )
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    severities = {issue.severity for issue in page.items[0].issues}
    assert severities == {"ERROR", "INFO"}
    enforced = next(issue for issue in page.items[0].issues if issue.enforcements is not None)
    assert enforced.enforcements.exemption.status == "EXEMPT_UNTIL_EXPIRY_DATE"


@pytest.mark.asyncio
async def test_fba_and_merchant_fulfilled_availability() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("07_fba_and_merchant_fulfilled.json"))]
    )
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    channels = {fa.fulfillment_channel_code for fa in page.items[0].fulfillment_availability}
    assert channels == {"AMAZON_NA", "DEFAULT"}


@pytest.mark.asyncio
async def test_b2c_offer_from_fixture_and_synthetic_b2b_offer() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("01_normal_listing.json"))])
    client, _ = _client(transport)
    page = await client.fetch_page(_request())
    assert page.items[0].offers[0].offer_type == "B2C"

    # No committed fixture includes a B2B offer; this inline payload matches
    # the official ItemOfferByMarketplace shape (marketplaceId, offerType,
    # price all required) exactly.
    b2b_payload = {
        "numberOfResults": 1,
        "pagination": {},
        "items": [
            {
                "sku": "ASI-TEST-SKU-B2B",
                "offers": [
                    {
                        "marketplaceId": MARKETPLACE_ID,
                        "offerType": "B2B",
                        "price": {"currencyCode": "USD", "amount": "49.99"},
                    }
                ],
            }
        ],
    }
    transport2 = _ScriptedTransport([lambda r: _json_response(200, b2b_payload)])
    client2, _ = _client(transport2)
    page2 = await client2.fetch_page(_request())
    assert page2.items[0].offers[0].offer_type == "B2B"


@pytest.mark.asyncio
async def test_multiple_marketplace_summaries_while_request_stays_scoped_to_one_marketplace() -> None:
    """The official schema allows `summaries` to contain more than one
    marketplace-scoped entry (the response is not required to match the
    request's marketplace scope 1:1). The client must parse that correctly
    without assuming exactly one entry, while the *request* it sent still
    only ever specified one `marketplaceIds` value."""
    payload = {
        "numberOfResults": 1,
        "pagination": {},
        "items": [
            {
                "sku": "ASI-TEST-SKU-MULTI-SUMMARY",
                "summaries": [
                    {
                        "marketplaceId": "ATVPDKIKX0DER",
                        "asin": "B0TESTASINMS",
                        "productType": "LUGGAGE",
                        "status": ["BUYABLE"],
                        "itemName": "Multi-marketplace example",
                        "createdDate": "2026-01-01T00:00:00Z",
                        "lastUpdatedDate": "2026-01-02T00:00:00Z",
                    },
                    {
                        "marketplaceId": "A2EUQ1WTGCTBG2",
                        "asin": "B0TESTASINMS",
                        "productType": "LUGGAGE",
                        "status": [],
                        "itemName": "Multi-marketplace example",
                        "createdDate": "2026-01-01T00:00:00Z",
                        "lastUpdatedDate": "2026-01-02T00:00:00Z",
                    },
                ],
            }
        ],
    }
    transport = _ScriptedTransport([lambda r: _json_response(200, payload)])
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    assert len(page.items[0].summaries) == 2
    assert {s.marketplace_id for s in page.items[0].summaries} == {"ATVPDKIKX0DER", "A2EUQ1WTGCTBG2"}
    sent_marketplace_ids = parse_qs(transport.listings_requests[0].url.query.decode())["marketplaceIds"]
    assert sent_marketplace_ids == [MARKETPLACE_ID]


# --- malformed payloads: never silently reinterpreted as empty ------------


@pytest.mark.asyncio
async def test_malformed_top_level_payload_missing_items_key() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, {"numberOfResults": 0})]  # no "items" key at all
    )
    client, _ = _client(transport)

    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1  # never retried


@pytest.mark.asyncio
async def test_malformed_top_level_payload_null_items() -> None:
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, {"numberOfResults": 0, "items": None})]
    )
    client, _ = _client(transport)

    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1


@pytest.mark.asyncio
async def test_malformed_top_level_payload_not_json() -> None:
    transport = _ScriptedTransport([lambda r: httpx.Response(200, text="not json at all")])
    client, _ = _client(transport)

    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1


@pytest.mark.asyncio
async def test_malformed_listing_entry_rejects_whole_page() -> None:
    """`09_malformed_item.json` has one valid item and one item missing the
    only required `Item` field, `sku`. The whole page must fail to parse —
    the valid item must not be silently returned as a partial success."""
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("09_malformed_item.json"))])
    client, _ = _client(transport)

    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1


@pytest.mark.asyncio
async def test_malformed_pagination_wrong_type() -> None:
    payload = {"numberOfResults": 1, "pagination": {"nextToken": 12345}, "items": [{"sku": "X"}]}
    transport = _ScriptedTransport([lambda r: _json_response(200, payload)])
    client, _ = _client(transport)

    with pytest.raises(SpApiParseFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1


# --- non-retryable failures -------------------------------------------


@pytest.mark.asyncio
async def test_authorization_failure_is_not_retried() -> None:
    transport = _ScriptedTransport([lambda r: httpx.Response(401, json={"errors": [{"code": "Unauthorized", "message": "x"}]})])
    client, sleeps = _client(transport, max_attempts=3)

    with pytest.raises(SpApiAuthenticationError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1
    assert sleeps == []


@pytest.mark.asyncio
async def test_non_retryable_4xx_is_not_retried() -> None:
    transport = _ScriptedTransport(
        [lambda r: httpx.Response(404, json={"errors": [{"code": "NotFound", "message": "x"}]})]
    )
    client, sleeps = _client(transport, max_attempts=3)

    with pytest.raises(SpApiInvalidRequestError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 1
    assert sleeps == []


# --- retryable failures: throttling, transient server, transport ---------


@pytest.mark.asyncio
async def test_throttling_bounded_retry_success() -> None:
    error_body = _fixture("10_rate_limited_error.json")["response_body"]
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(429, json=error_body),
            lambda r: httpx.Response(429, json=error_body),
            lambda r: _json_response(200, _fixture("01_normal_listing.json")),
        ]
    )
    client, sleeps = _client(transport, max_attempts=3)

    page = await client.fetch_page(_request())

    assert len(page.items) == 1
    assert len(transport.listings_requests) == 3
    assert page.provenance.attempt_count == 3
    assert sleeps == [1.0, 2.0]  # base=1.0, jitter fixed at 1.0: 1*2^0, 1*2^1


@pytest.mark.asyncio
async def test_throttling_bounded_retry_exhaustion() -> None:
    error_body = _fixture("10_rate_limited_error.json")["response_body"]
    transport = _ScriptedTransport([lambda r: httpx.Response(429, json=error_body) for _ in range(3)])
    client, sleeps = _client(transport, max_attempts=3)

    with pytest.raises(SpApiRateLimitedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 3
    assert sleeps == [1.0, 2.0]


@pytest.mark.asyncio
async def test_transient_server_failure_bounded_retry_success() -> None:
    error_body = _fixture("11_mid_pagination_failure.json")["response_body"]
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(500, json=error_body),
            lambda r: _json_response(200, _fixture("01_normal_listing.json")),
        ]
    )
    client, sleeps = _client(transport, max_attempts=3)

    page = await client.fetch_page(_request())

    assert len(page.items) == 1
    assert len(transport.listings_requests) == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_transient_server_failure_bounded_retry_exhaustion() -> None:
    error_body = _fixture("11_mid_pagination_failure.json")["response_body"]
    transport = _ScriptedTransport([lambda r: httpx.Response(503, json=error_body) for _ in range(2)])
    client, sleeps = _client(transport, max_attempts=2)

    with pytest.raises(SpApiRequestFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_timeout_transport_failure_bounded_retry_success() -> None:
    transport = _ScriptedTransport(
        [_timeout_response, lambda r: _json_response(200, _fixture("01_normal_listing.json"))]
    )
    client, sleeps = _client(transport, max_attempts=3)

    page = await client.fetch_page(_request())

    assert len(page.items) == 1
    assert len(transport.listings_requests) == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
async def test_connect_error_transport_failure_bounded_retry_exhaustion() -> None:
    transport = _ScriptedTransport([_connect_error_response, _connect_error_response])
    client, sleeps = _client(transport, max_attempts=2)

    with pytest.raises(SpApiRequestFailedError):
        await client.fetch_page(_request())
    assert len(transport.listings_requests) == 2
    assert sleeps == [1.0]


# --- request construction -------------------------------------------------


@pytest.mark.asyncio
async def test_seller_id_and_marketplace_request_construction() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)

    await client.fetch_page(_request(page_token="a-page-token"))

    sent = transport.listings_requests[0]
    assert sent.url.path == f"/listings/2021-08-01/items/{SELLER_ID}"
    query = parse_qs(sent.url.query.decode())
    assert query["marketplaceIds"] == [MARKETPLACE_ID]
    assert query["pageToken"] == ["a-page-token"]


@pytest.mark.asyncio
async def test_exact_approved_included_data_and_page_size() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)

    await client.fetch_page(_request())

    query = parse_qs(transport.listings_requests[0].url.query.decode())
    assert query["includedData"] == [",".join(APPROVED_INCLUDED_DATA)]
    assert query["pageSize"] == [str(LISTINGS_PAGE_SIZE)]
    assert LISTINGS_PAGE_SIZE == 20


@pytest.mark.asyncio
async def test_no_accidental_attributes_relationships_procurement_requested() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)

    await client.fetch_page(_request())

    included = parse_qs(transport.listings_requests[0].url.query.decode())["includedData"][0]
    for forbidden in ("attributes", "relationships", "procurement"):
        assert forbidden not in included.split(",")


@pytest.mark.asyncio
async def test_missing_seller_id_or_marketplace_id_fails_closed_without_any_call() -> None:
    transport = _ScriptedTransport([])
    client, _ = _client(transport)

    with pytest.raises(SpApiConfigurationError):
        await client.fetch_page(ListingsPageRequest(seller_id="", marketplace_id=MARKETPLACE_ID))
    with pytest.raises(SpApiConfigurationError):
        await client.fetch_page(ListingsPageRequest(seller_id=SELLER_ID, marketplace_id=""))
    assert transport.listings_requests == []


# --- no secret or raw-payload leakage --------------------------------------


@pytest.mark.asyncio
async def test_no_secret_or_raw_payload_leakage_in_logs_or_errors(caplog) -> None:
    """True secrets (access token, refresh token, client secret) must never
    appear in ANY log line, from any logger — they live only in headers
    (`x-amz-access-token`, LWA form body), never in a URL or query string.
    The seller ID must also not appear anywhere in the combined log: the
    centralized `httpx`-logger redaction filter (installed once, at client
    construction — see `_ensure_httpx_seller_id_redaction_installed`)
    rewrites it out of `httpx`'s own request logging, not just this
    module's own log statements. See
    `test_httpx_logger_reproduction_and_redaction_of_seller_id` for the
    dedicated before/after proof that this filter is what makes the
    difference, not an accident of what happened to get logged. This test
    uses `httpx.MockTransport` (like every test in this file), which
    replaces `httpx`'s real transport entirely — `httpcore` code never
    executes here, so nothing in this suite tests or claims anything about
    `httpcore`'s own logging.
    """
    error_body = _fixture("10_rate_limited_error.json")["response_body"]
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(429, json=error_body),
            lambda r: _json_response(200, _fixture("06_listing_with_issues.json")),
        ]
    )
    client, _ = _client(transport, max_attempts=3)

    with caplog.at_level("DEBUG"):
        page = await client.fetch_page(_request())

    combined_log = "\n".join(record.getMessage() for record in caplog.records)
    for secret in (ACCESS_TOKEN, REFRESH_TOKEN, CLIENT_SECRET, "x-amz-access-token"):
        assert secret not in combined_log
    assert "QuotaExceeded" not in combined_log  # raw error-response body text
    assert page.items[0].sku not in combined_log  # raw listing content

    # The centralized httpx-logger redaction filter (installed once,
    # process-lifetime, by client construction) now covers this too — the
    # seller ID must not appear anywhere in the combined log, not just this
    # module's own log lines. See test_httpx_logger_reproduction_and_
    # redaction_of_seller_id for the dedicated before/after proof.
    assert SELLER_ID not in combined_log

    bad_transport = _ScriptedTransport([lambda r: httpx.Response(401, json={"errors": []})])
    bad_client, _ = _client(bad_transport)
    with pytest.raises(SpApiAuthenticationError) as excinfo:
        await bad_client.fetch_page(_request())
    assert ACCESS_TOKEN not in str(excinfo.value)
    assert REFRESH_TOKEN not in str(excinfo.value)
    assert SELLER_ID not in str(excinfo.value)


# --- 12B.3C review: httpx seller-ID log redaction ---------------------------
#
# Scoped to the `httpx` logger only — the one proven leak. `httpcore`'s own
# tracing logs through per-component sub-logger names (`httpcore.
# connection`, `httpcore.http11`, ...), never the bare `httpcore` name, and
# a filter attached only to the parent does not propagate to those child
# loggers' own filter checks (verified directly; Python's logger filters
# are checked once, on the originating logger only). This test suite also
# uses `httpx.MockTransport` everywhere, which replaces `httpx`'s real
# transport entirely, so `httpcore` code never executes here regardless —
# nothing in this file could test `httpcore`'s behavior even if the claim
# were made. See the module docstring in `listings_client.py` for the full
# reasoning and the documented residual risk (do not enable verbose
# transport/connection DEBUG logging in production).


@pytest.fixture
def _reset_httpx_redaction_state():
    """Saves and restores the module-level installed-flag and the actual
    filters on the `httpx` logger, so this test can prove the *raw*,
    unfiltered behavior first and does not leak logger mutations into any
    other test in this file (tests run in the same process, and the
    flag/filter are otherwise process-lifetime by design)."""
    previous_flag = listings_client_module._httpx_redaction_installed
    target = logging.getLogger("httpx")
    saved_filters = list(target.filters)
    for f in list(target.filters):
        if isinstance(f, _RedactListingsSellerIdFilter):
            target.removeFilter(f)
    listings_client_module._httpx_redaction_installed = False
    try:
        yield
    finally:
        listings_client_module._httpx_redaction_installed = previous_flag
        for f in list(target.filters):
            if isinstance(f, _RedactListingsSellerIdFilter):
                target.removeFilter(f)
        for f in saved_filters:
            target.addFilter(f)


@pytest.mark.asyncio
async def test_httpx_logger_reproduction_and_redaction_of_seller_id(_reset_httpx_redaction_state, caplog) -> None:
    """Reproduces the reported leak from a clean, unfiltered state (proving
    it is real, not assumed), then proves the client's own installer fixes
    it — for the `httpx` logger specifically, which is what actually emits
    the line (this module's own logger never included the seller ID in the
    first place)."""
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)  # constructing the client installs the filter — proven below to matter

    # Before: remove the filter this client's __init__ just installed, to
    # observe httpx's genuinely raw, undefended behavior.
    httpx_logger = logging.getLogger("httpx")
    for f in list(httpx_logger.filters):
        if isinstance(f, _RedactListingsSellerIdFilter):
            httpx_logger.removeFilter(f)

    with caplog.at_level("INFO", logger="httpx"):
        await client.fetch_page(_request())
    raw_httpx_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert SELLER_ID in raw_httpx_log, "expected the reproduction to show the raw, unfiltered leak"
    assert "/listings/2021-08-01/items/" in raw_httpx_log
    caplog.clear()

    # After: reinstall (exactly what __init__ does) and repeat the same call.
    # The module-level idempotency flag was already set True by the first
    # `_client(transport)` call above; reset it so removing the filter
    # object (done manually, above, only to observe the "before" state)
    # doesn't leave the installer believing there is nothing to do.
    listings_client_module._httpx_redaction_installed = False
    listings_client_module._ensure_httpx_seller_id_redaction_installed()
    transport2 = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client2, _ = _client(transport2)
    with caplog.at_level("INFO", logger="httpx"):
        await client2.fetch_page(_request())
    fixed_httpx_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert SELLER_ID not in fixed_httpx_log
    assert "/listings/2021-08-01/items/{sellerId}" in fixed_httpx_log
    # Diagnostic value preserved: method, host, and status code still present.
    assert "GET" in fixed_httpx_log
    assert "sellingpartnerapi-na.amazon.com" in fixed_httpx_log
    assert "200" in fixed_httpx_log


@pytest.mark.asyncio
async def test_other_endpoints_unaffected_by_seller_id_redaction_filter(caplog) -> None:
    """The filter must not blind logging for any other endpoint — proven
    here against the LWA token URL, which this same test run also hits."""
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)

    with caplog.at_level("INFO", logger="httpx"):
        await client.fetch_page(_request())

    httpx_log = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert DEFAULT_LWA_TOKEN_URL in httpx_log  # LWA call logged in full, unredacted


# --- 12B.3C review: null vs absent, per the official schema ---------------


@pytest.mark.asyncio
async def test_optional_field_absent_parses_as_none() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)
    page = await client.fetch_page(_request())
    assert page.items == []  # numberOfResults/items both present; nothing optional to check here

    payload = {"numberOfResults": 1, "items": [{"sku": "X"}]}  # no "pagination" key at all
    transport2 = _ScriptedTransport([lambda r: _json_response(200, payload)])
    client2, _ = _client(transport2)
    page2 = await client2.fetch_page(_request())
    assert page2.next_token is None
    assert page2.items[0].summaries is None
    assert page2.items[0].issues is None
    assert page2.items[0].offers is None


@pytest.mark.asyncio
async def test_optional_field_explicit_null_is_rejected_not_treated_as_absent() -> None:
    """The official Swagger 2.0 schema documents zero `nullable`/`x-nullable`
    markers anywhere (verified directly against the pinned spec) — every
    optional field means "may be absent," never "may be present as null."
    An explicit null must fail the whole page, not be silently treated the
    same as the key being missing."""
    for payload in (
        {"numberOfResults": 1, "items": [{"sku": "X", "summaries": None}]},
        {"numberOfResults": 1, "items": [{"sku": "X", "issues": None}]},
        {"numberOfResults": 1, "items": [{"sku": "X", "offers": None}]},
        {"numberOfResults": 1, "items": [{"sku": "X", "fulfillmentAvailability": None}]},
        {"numberOfResults": 1, "items": [{"sku": "X", "productTypes": None}]},
    ):
        transport = _ScriptedTransport([lambda r, payload=payload: _json_response(200, payload)])
        client, _ = _client(transport)
        with pytest.raises(SpApiParseFailedError):
            await client.fetch_page(_request())


@pytest.mark.asyncio
async def test_optional_empty_array_is_preserved_distinct_from_absent() -> None:
    payload = {"numberOfResults": 1, "items": [{"sku": "X", "summaries": [], "issues": []}]}
    transport = _ScriptedTransport([lambda r: _json_response(200, payload)])
    client, _ = _client(transport)
    page = await client.fetch_page(_request())
    assert page.items[0].summaries == []
    assert page.items[0].issues == []
    assert page.items[0].summaries is not None


@pytest.mark.asyncio
async def test_pagination_absent_vs_explicit_null() -> None:
    absent_payload = {"numberOfResults": 1, "items": [{"sku": "X"}]}
    transport = _ScriptedTransport([lambda r: _json_response(200, absent_payload)])
    client, _ = _client(transport)
    page = await client.fetch_page(_request())
    assert page.next_token is None  # pagination absent -> no next page, not an error

    null_payload = {"numberOfResults": 1, "items": [{"sku": "X"}], "pagination": None}
    transport2 = _ScriptedTransport([lambda r: _json_response(200, null_payload)])
    client2, _ = _client(transport2)
    with pytest.raises(SpApiParseFailedError):
        await client2.fetch_page(_request())


@pytest.mark.asyncio
async def test_next_token_absent_vs_explicit_null() -> None:
    absent_payload = {"numberOfResults": 1, "items": [{"sku": "X"}], "pagination": {}}
    transport = _ScriptedTransport([lambda r: _json_response(200, absent_payload)])
    client, _ = _client(transport)
    page = await client.fetch_page(_request())
    assert page.next_token is None

    null_payload = {"numberOfResults": 1, "items": [{"sku": "X"}], "pagination": {"nextToken": None}}
    transport2 = _ScriptedTransport([lambda r: _json_response(200, null_payload)])
    client2, _ = _client(transport2)
    with pytest.raises(SpApiParseFailedError):
        await client2.fetch_page(_request())


@pytest.mark.asyncio
async def test_required_field_missing_vs_null() -> None:
    for payload in (
        {"items": [{"sku": "X"}]},  # numberOfResults missing entirely
        {"numberOfResults": None, "items": [{"sku": "X"}]},  # numberOfResults explicit null
        {"numberOfResults": 1},  # items missing entirely
        {"numberOfResults": 1, "items": None},  # items explicit null
    ):
        transport = _ScriptedTransport([lambda r, payload=payload: _json_response(200, payload)])
        client, _ = _client(transport)
        with pytest.raises(SpApiParseFailedError):
            await client.fetch_page(_request())


# --- 12B.3C review: request/path safety and identity ------------------------


@pytest.mark.asyncio
async def test_seller_id_is_percent_encoded_as_a_path_component() -> None:
    """Amazon's real selling_partner_id values are plain alphanumeric, but
    this client must not rely on that: an unexpected character must not be
    able to change the URL's structure."""
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)
    odd_seller_id = "A1B2/C3?D4#E5"

    await client.fetch_page(ListingsPageRequest(seller_id=odd_seller_id, marketplace_id=MARKETPLACE_ID))

    # `.path` decodes percent-escapes back to logical characters (correct,
    # standard httpx.URL behavior) — the actual wire-encoded form actually
    # sent is `.raw_path` / `str(url)`, which is what must be checked here.
    from urllib.parse import quote as _quote

    raw_path = transport.listings_requests[0].url.raw_path.decode()
    expected_encoded_segment = _quote(odd_seller_id, safe="")
    assert raw_path.startswith(f"/listings/2021-08-01/items/{expected_encoded_segment}")
    # The encoded seller ID must not introduce a new "/" path segment before
    # the query string starts, nor a "?" or "#" that would change the URL's
    # structure.
    path_only = raw_path.split("?", 1)[0]
    assert path_only.count("/") == 4  # the four fixed path segments only
    assert "#" not in raw_path
    # And the *logical* (decoded) path must round-trip back to the literal,
    # un-mangled seller ID — proving the encoding is reversible, not lossy.
    assert transport.listings_requests[0].url.path == f"/listings/2021-08-01/items/{odd_seller_id}"


@pytest.mark.asyncio
async def test_surrounding_whitespace_is_normalized_once_at_construction() -> None:
    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)

    request = ListingsPageRequest(
        seller_id=f"  {SELLER_ID}  ", marketplace_id=f" {MARKETPLACE_ID} ", page_token="  a-token  "
    )
    assert request.seller_id == SELLER_ID
    assert request.marketplace_id == MARKETPLACE_ID
    assert request.page_token == "a-token"

    await client.fetch_page(request)
    sent = transport.listings_requests[0]
    assert sent.url.path == f"/listings/2021-08-01/items/{SELLER_ID}"
    query = parse_qs(sent.url.query.decode())
    assert query["marketplaceIds"] == [MARKETPLACE_ID]
    assert query["pageToken"] == ["a-token"]


@pytest.mark.asyncio
async def test_whitespace_only_page_token_is_treated_as_absent() -> None:
    request = ListingsPageRequest(seller_id=SELLER_ID, marketplace_id=MARKETPLACE_ID, page_token="   ")
    assert request.page_token is None

    transport = _ScriptedTransport([lambda r: _json_response(200, _fixture("04_empty_result.json"))])
    client, _ = _client(transport)
    await client.fetch_page(request)
    query = parse_qs(transport.listings_requests[0].url.query.decode())
    assert "pageToken" not in query


@pytest.mark.asyncio
async def test_next_token_forwarded_unchanged_on_next_request() -> None:
    """Not testing traversal (out of scope) — testing that a token this
    client received is passed through byte-for-byte if the caller supplies
    it again, aside from the documented whitespace-strip normalization."""
    transport = _ScriptedTransport(
        [lambda r: _json_response(200, _fixture("03_pagination_page_1_of_3.json"))]
    )
    client, _ = _client(transport)
    page = await client.fetch_page(_request())
    token = page.next_token
    assert token == "ASI-FIXTURE-PAGE-TOKEN-2"

    transport2 = _ScriptedTransport([lambda r: _json_response(200, _fixture("03_pagination_page_2_of_3.json"))])
    client2, _ = _client(transport2)
    await client2.fetch_page(_request(page_token=token))
    sent_token = parse_qs(transport2.listings_requests[0].url.query.decode())["pageToken"]
    assert sent_token == [token]


@pytest.mark.asyncio
async def test_response_cannot_override_caller_supplied_marketplace_identity() -> None:
    """`ItemSummary.marketplaceId` is a real, distinct field Amazon returns
    per summary entry — it must never be read back to override
    `ListingsPage.marketplace_id`, which always reflects the request."""
    payload = {
        "numberOfResults": 1,
        "items": [
            {
                "sku": "X",
                "summaries": [
                    {
                        "marketplaceId": "A_COMPLETELY_DIFFERENT_MARKETPLACE",
                        "productType": "TOY",
                        "status": [],
                        "createdDate": "2026-01-01T00:00:00Z",
                        "lastUpdatedDate": "2026-01-01T00:00:00Z",
                    }
                ],
            }
        ],
    }
    transport = _ScriptedTransport([lambda r: _json_response(200, payload)])
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    assert page.marketplace_id == MARKETPLACE_ID  # the request's own value, not the response's
    assert page.items[0].summaries[0].marketplace_id == "A_COMPLETELY_DIFFERENT_MARKETPLACE"


@pytest.mark.asyncio
async def test_provenance_headers_are_sanitized_and_length_bounded() -> None:
    oversized = "x" * 10_000
    with_control_chars = "abc\r\n123\tdef" + "\x00"

    def handler(_r: httpx.Request) -> httpx.Response:
        response = httpx.Response(200, json=_fixture("04_empty_result.json"))
        response.headers["x-amzn-RateLimit-Limit"] = oversized
        response.headers["x-amzn-RequestId"] = with_control_chars
        return response

    transport = _ScriptedTransport([handler])
    client, _ = _client(transport)

    page = await client.fetch_page(_request())

    assert page.provenance.rate_limit is not None
    assert len(page.provenance.rate_limit) <= 256
    assert page.provenance.request_id is not None
    assert "\r" not in page.provenance.request_id
    assert "\n" not in page.provenance.request_id
    assert "\x00" not in page.provenance.request_id
    assert "\t" not in page.provenance.request_id


@pytest.mark.asyncio
async def test_retry_attempts_reuse_immutable_request_data_with_fresh_timestamp() -> None:
    error_body = _fixture("11_mid_pagination_failure.json")["response_body"]
    transport = _ScriptedTransport(
        [
            lambda r: httpx.Response(500, json=error_body),
            lambda r: _json_response(200, _fixture("04_empty_result.json")),
        ]
    )
    client, _ = _client(transport, max_attempts=3)

    await client.fetch_page(_request())

    assert len(transport.listings_requests) == 2
    first, second = transport.listings_requests
    # Immutable across attempts: identical path and query.
    assert first.url.path == second.url.path
    assert first.url.query == second.url.query
    # Each attempt computed its own timestamp header (not reused verbatim
    # from before the retry loop started).
    assert "x-amz-date" in first.headers
    assert "x-amz-date" in second.headers

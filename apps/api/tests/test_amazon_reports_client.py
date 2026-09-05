"""12B.6A — AmazonSpApiReportsClient. Fake transport only; no test calls
Amazon. Mirrors `test_amazon_orders_client.py`'s own `_ScriptedTransport`/
`_client` helper conventions."""

from __future__ import annotations

import gzip
import json
import traceback
from datetime import date
from urllib.parse import urlparse

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.lwa import DEFAULT_LWA_TOKEN_URL
from app.amazon.reports_client import (
    AmazonSpApiReportsClient,
    CreateSalesAndTrafficReportRequest,
    ReportDocumentInfo,
)
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

CLIENT_ID = "amzn1.application-oa2-client.reportstest"
CLIENT_SECRET = "test-reports-lwa-client-secret-value"
REFRESH_TOKEN = "Atzr|test-reports-refresh-token"
ACCESS_TOKEN = "Atza|test-reports-access-token"
BASE_URL = "https://sellingpartnerapi-na.amazon.com"
MARKETPLACE_ID = "ATVPDKIKX0DER"
DOCUMENT_HOST = "https://amazon-reports-documents.s3.amazonaws.com"


def _lwa_success_response() -> httpx.Response:
    return httpx.Response(200, json={"access_token": ACCESS_TOKEN, "token_type": "bearer", "expires_in": 3600})


class _ScriptedTransport:
    def __init__(self, responses: list) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == urlparse(DEFAULT_LWA_TOKEN_URL).netloc:
            return _lwa_success_response()
        self.requests.append(request)
        producer = self._responses.pop(0)
        return producer(request) if callable(producer) else producer


def _json_response(status: int, body: dict, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, json=body, headers=headers or {})


def _timeout_response(_request: httpx.Request) -> httpx.Response:
    raise httpx.TimeoutException("timed out")


def _client(
    transport: _ScriptedTransport,
    *,
    download_transport: "_ScriptedDownloadTransport | None" = None,
    max_attempts: int = 3,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 100.0,
    jitter_value: float = 1.0,
    max_document_bytes: int = 64 * 1024 * 1024,
    max_decompressed_bytes: int = 256 * 1024 * 1024,
) -> tuple[AmazonSpApiReportsClient, list[float]]:
    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    client = AmazonSpApiReportsClient(
        client_id=CLIENT_ID,
        client_secret=CLIENT_SECRET,
        refresh_token=SecretStr(REFRESH_TOKEN),
        token_url=DEFAULT_LWA_TOKEN_URL,
        base_url=BASE_URL,
        region="na",
        transport=httpx.MockTransport(transport),
        download_transport=httpx.MockTransport(download_transport) if download_transport else None,
        max_attempts=max_attempts,
        base_delay_seconds=base_delay_seconds,
        max_delay_seconds=max_delay_seconds,
        sleep=fake_sleep,
        jitter=lambda: jitter_value,
        max_document_bytes=max_document_bytes,
        max_decompressed_bytes=max_decompressed_bytes,
    )
    return client, sleep_calls


class _ScriptedDownloadTransport:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response


class _RaisingDownloadTransport:
    """Simulates a transport-level failure (never a real HTTP response),
    e.g. a DNS/connection error hitting the presigned-URL host."""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"Connection refused for {request.url}", request=request)


_SAMPLE_REPORT = {
    "reportSpecification": {
        "reportType": "GET_SALES_AND_TRAFFIC_REPORT",
        "reportOptions": {"dateGranularity": "DAY", "asinGranularity": "SKU"},
        "dataStartTime": "2026-08-01",
        "dataEndTime": "2026-08-01",
        "marketplaceIds": [MARKETPLACE_ID],
    },
    "salesAndTrafficByDate": [
        {
            "date": "2026-08-01",
            "salesByDate": {
                "orderedProductSales": {"amount": 100.00, "currencyCode": "USD"},
                "unitsOrdered": 5, "totalOrderItems": 5,
                "averageSalesPerOrderItem": {"amount": 20.00, "currencyCode": "USD"},
                "averageUnitsPerOrderItem": 1.0,
                "averageSellingPrice": {"amount": 20.00, "currencyCode": "USD"},
                "unitsRefunded": 0, "refundRate": 0.0, "claimsGranted": 0,
                "claimsAmount": {"amount": 0.0, "currencyCode": "USD"},
                "shippedProductSales": {"amount": 100.0, "currencyCode": "USD"},
                "unitsShipped": 5, "ordersShipped": 5,
            },
            "trafficByDate": {
                "browserPageViews": 100, "mobileAppPageViews": 50, "pageViews": 150,
                "browserSessions": 80, "mobileAppSessions": 40, "sessions": 120,
                "buyBoxPercentage": 90.0, "orderItemSessionPercentage": 4.0,
                "unitSessionPercentage": 4.0, "averageOfferCount": 10,
                "averageParentItems": 10, "feedbackReceived": 0,
                "negativeFeedbackReceived": 0, "receivedNegativeFeedbackRate": 0.0,
            },
        }
    ],
    "salesAndTrafficByAsin": [
        {
            "parentAsin": "B0PARENT01", "childAsin": "B0CHILD001", "sku": "SKU-A",
            "salesByAsin": {"unitsOrdered": 5, "orderedProductSales": {"amount": 100.0, "currencyCode": "USD"}, "totalOrderItems": 5},
            "trafficByAsin": {
                "browserSessions": 80, "mobileAppSessions": 40, "sessions": 120,
                "browserSessionPercentage": 100.0, "mobileAppSessionPercentage": 100.0, "sessionPercentage": 100.0,
                "browserPageViews": 100, "mobileAppPageViews": 50, "pageViews": 150,
                "browserPageViewsPercentage": 100.0, "mobileAppPageViewsPercentage": 100.0, "pageViewsPercentage": 100.0,
                "buyBoxPercentage": 90.0, "unitSessionPercentage": 4.16,
            },
        }
    ],
}


# --- createReport ------------------------------------------------------


@pytest.mark.asyncio
async def test_create_report_returns_report_id() -> None:
    transport = _ScriptedTransport([_json_response(202, {"reportId": "AMZN-REPORT-1"})])
    client, _ = _client(transport)
    report_id, attempts = await client.create_report(
        CreateSalesAndTrafficReportRequest(
            marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
        )
    )
    assert report_id == "AMZN-REPORT-1"
    assert attempts == 1
    body = json.loads(transport.requests[0].content)
    assert body["marketplaceIds"] == [MARKETPLACE_ID]
    assert body["reportOptions"] == {"dateGranularity": "DAY", "asinGranularity": "SKU"}


@pytest.mark.asyncio
async def test_create_report_rejects_multiple_marketplace_ids_at_construction() -> None:
    with pytest.raises(SpApiConfigurationError):
        CreateSalesAndTrafficReportRequest(
            marketplace_id="", data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
        )


@pytest.mark.asyncio
async def test_create_report_rejects_start_after_end() -> None:
    with pytest.raises(SpApiConfigurationError):
        CreateSalesAndTrafficReportRequest(
            marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 2), data_end_time=date(2026, 8, 1)
        )


@pytest.mark.asyncio
async def test_create_report_authentication_failure_never_retried() -> None:
    transport = _ScriptedTransport([_json_response(403, {"errors": [{"code": "Unauthorized"}]})])
    client, sleep_calls = _client(transport)
    with pytest.raises(SpApiAuthenticationError):
        await client.create_report(
            CreateSalesAndTrafficReportRequest(
                marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
            )
        )
    assert len(transport.requests) == 1  # never retried
    assert sleep_calls == []


@pytest.mark.asyncio
async def test_create_report_retries_on_429_honoring_retry_after() -> None:
    transport = _ScriptedTransport(
        [
            _json_response(429, {}, headers={"Retry-After": "2"}),
            _json_response(202, {"reportId": "AMZN-REPORT-2"}),
        ]
    )
    client, sleep_calls = _client(transport)
    report_id, attempts = await client.create_report(
        CreateSalesAndTrafficReportRequest(
            marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
        )
    )
    assert report_id == "AMZN-REPORT-2"
    assert attempts == 2
    assert sleep_calls == [2.0]


@pytest.mark.asyncio
async def test_create_report_retries_on_5xx_then_exhausts() -> None:
    transport = _ScriptedTransport([_json_response(500, {}), _json_response(500, {}), _json_response(500, {})])
    client, _ = _client(transport, max_attempts=3)
    with pytest.raises(SpApiRequestFailedError):
        await client.create_report(
            CreateSalesAndTrafficReportRequest(
                marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
            )
        )
    assert len(transport.requests) == 3


@pytest.mark.asyncio
async def test_create_report_transport_failure_retries_then_fails() -> None:
    transport = _ScriptedTransport([_timeout_response, _timeout_response, _timeout_response])
    client, _ = _client(transport, max_attempts=3)
    with pytest.raises(SpApiRequestFailedError):
        await client.create_report(
            CreateSalesAndTrafficReportRequest(
                marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
            )
        )


@pytest.mark.asyncio
async def test_create_report_malformed_response_raises_parse_error() -> None:
    transport = _ScriptedTransport([_json_response(202, {"unexpected": "shape"})])
    client, _ = _client(transport)
    with pytest.raises(SpApiParseFailedError):
        await client.create_report(
            CreateSalesAndTrafficReportRequest(
                marketplace_id=MARKETPLACE_ID, data_start_time=date(2026, 8, 1), data_end_time=date(2026, 8, 1)
            )
        )


# --- getReport (every terminal + non-terminal processing status) -------


@pytest.mark.parametrize("status", ["IN_QUEUE", "IN_PROGRESS", "DONE", "CANCELLED", "FATAL"])
@pytest.mark.asyncio
async def test_get_report_every_processing_status(status: str) -> None:
    body = {"reportId": "AMZN-REPORT-1", "processingStatus": status}
    if status == "DONE":
        body["reportDocumentId"] = "AMZN-DOC-1"
    transport = _ScriptedTransport([_json_response(200, body)])
    client, _ = _client(transport)
    result = await client.get_report("AMZN-REPORT-1")
    assert result.processing_status == status
    assert result.report_id == "AMZN-REPORT-1"
    if status == "DONE":
        assert result.report_document_id == "AMZN-DOC-1"
    else:
        assert result.report_document_id is None


@pytest.mark.asyncio
async def test_get_report_unrecognized_status_raises_parse_error() -> None:
    transport = _ScriptedTransport([_json_response(200, {"reportId": "R1", "processingStatus": "SOMETHING_NEW"})])
    client, _ = _client(transport)
    with pytest.raises(SpApiParseFailedError):
        await client.get_report("R1")


@pytest.mark.asyncio
async def test_get_report_requires_a_report_id() -> None:
    transport = _ScriptedTransport([])
    client, _ = _client(transport)
    with pytest.raises(SpApiConfigurationError):
        await client.get_report("")


@pytest.mark.asyncio
async def test_get_report_done_without_report_document_id_raises_parse_error() -> None:
    """The pinned contract's own enum says DONE is the only status with a
    populated reportDocumentId — a DONE response missing it is a contract
    violation to reject here, at the client boundary, never a "document
    not ready yet" state for the ingestion service to work around with a
    bare `assert`."""
    transport = _ScriptedTransport([_json_response(200, {"reportId": "R1", "processingStatus": "DONE"})])
    client, _ = _client(transport)
    with pytest.raises(SpApiParseFailedError):
        await client.get_report("R1")


# --- getReportDocument ---------------------------------------------------


@pytest.mark.asyncio
async def test_get_report_document_returns_url_and_compression() -> None:
    transport = _ScriptedTransport(
        [_json_response(200, {"reportDocumentId": "D1", "url": f"{DOCUMENT_HOST}/x", "compressionAlgorithm": "GZIP"})]
    )
    client, _ = _client(transport)
    info = await client.get_report_document("D1")
    assert info.url == f"{DOCUMENT_HOST}/x"
    assert info.compression_algorithm == "GZIP"


@pytest.mark.asyncio
async def test_get_report_document_rejects_unsupported_compression() -> None:
    transport = _ScriptedTransport(
        [_json_response(200, {"reportDocumentId": "D1", "url": f"{DOCUMENT_HOST}/x", "compressionAlgorithm": "BROTLI"})]
    )
    client, _ = _client(transport)
    with pytest.raises(SpApiParseFailedError):
        await client.get_report_document("D1")


@pytest.mark.asyncio
async def test_get_report_document_url_is_never_logged(caplog) -> None:
    transport = _ScriptedTransport(
        [_json_response(200, {"reportDocumentId": "D1", "url": f"{DOCUMENT_HOST}/super-secret-path"})]
    )
    client, _ = _client(transport)
    import logging

    with caplog.at_level(logging.DEBUG):
        await client.get_report_document("D1")
    assert "super-secret-path" not in caplog.text


# --- downloadReportDocument ----------------------------------------------


@pytest.mark.asyncio
async def test_download_report_document_uncompressed_json() -> None:
    raw = json.dumps(_SAMPLE_REPORT).encode("utf-8")
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=raw))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    report = await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm=None))
    assert report.sales_and_traffic_by_asin[0].sku == "SKU-A"
    assert report.sales_and_traffic_by_date[0].sales_by_date.units_ordered == 5


@pytest.mark.asyncio
async def test_download_report_document_gzip_compressed() -> None:
    raw = gzip.compress(json.dumps(_SAMPLE_REPORT).encode("utf-8"))
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=raw))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    report = await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm="GZIP"))
    assert report.sales_and_traffic_by_asin[0].sku == "SKU-A"


@pytest.mark.asyncio
async def test_download_report_document_rejects_a_decompression_bomb() -> None:
    """A small, highly-compressible payload (5 MiB of a repeated byte
    compresses to a few KiB) must be rejected once its *decompressed*
    size exceeds the configured ceiling — proving the bound is on
    decompressed output, not merely on the already-capped download size
    (`max_document_bytes` governs the compressed bytes actually
    downloaded; `max_decompressed_bytes` governs what `zlib` is allowed
    to expand them into)."""
    compressed = gzip.compress(b"0" * (5 * 1024 * 1024))
    assert len(compressed) < 64 * 1024  # confirms this is a genuine high compression ratio, not a trivially large upload
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=compressed))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport, max_decompressed_bytes=1024 * 1024)
    with pytest.raises(SpApiParseFailedError):
        await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm="GZIP"))


@pytest.mark.asyncio
async def test_download_report_document_rejects_a_truncated_gzip_stream() -> None:
    """A gzip stream cut off mid-transfer must never be silently accepted
    as valid — whatever partial bytes `zlib` can decode from it should
    still fail at the JSON-parse step, never be treated as a complete
    document."""
    full = gzip.compress(json.dumps(_SAMPLE_REPORT).encode("utf-8"))
    truncated = full[: len(full) // 2]
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=truncated))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    with pytest.raises(SpApiParseFailedError):
        await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm="GZIP"))


@pytest.mark.asyncio
async def test_download_report_document_rejects_non_https_url() -> None:
    client, _ = _client(_ScriptedTransport([]))
    with pytest.raises(SpApiInvalidRequestError):
        await client.download_report_document(
            ReportDocumentInfo(url="http://amazon-reports-documents.s3.amazonaws.com/x", compression_algorithm=None)
        )


@pytest.mark.asyncio
async def test_download_report_document_rejects_a_redirect() -> None:
    download_transport = _ScriptedDownloadTransport(
        httpx.Response(302, headers={"Location": "https://evil.example.com/steal"})
    )
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    with pytest.raises(SpApiRequestFailedError):
        await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm=None))


@pytest.mark.asyncio
async def test_download_report_document_rejects_malformed_json() -> None:
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=b"{not valid json"))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    with pytest.raises(SpApiParseFailedError):
        await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm=None))


@pytest.mark.asyncio
async def test_download_report_document_rejects_oversized_document() -> None:
    raw = json.dumps(_SAMPLE_REPORT).encode("utf-8")
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=raw))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport, max_document_bytes=10)
    with pytest.raises(SpApiRequestFailedError):
        await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm=None))


@pytest.mark.asyncio
async def test_download_report_document_tolerates_unknown_future_field() -> None:
    report_with_unknown_field = dict(_SAMPLE_REPORT)
    report_with_unknown_field["someBrandNewTopLevelField"] = {"nested": "value"}
    raw = json.dumps(report_with_unknown_field).encode("utf-8")
    download_transport = _ScriptedDownloadTransport(httpx.Response(200, content=raw))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    report = await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm=None))
    assert report.sales_and_traffic_by_asin[0].sku == "SKU-A"
    assert not hasattr(report, "some_brand_new_top_level_field")


@pytest.mark.asyncio
async def test_download_report_document_transport_failure_never_chains_the_url_bearing_exception() -> None:
    """`httpx.ConnectError`'s own string representation embeds the
    request URL — for this one call, the presigned document URL. The
    raised `SpApiRequestFailedError` must sever the exception chain
    (`from None`, not `from exc`) so that URL can never become reachable
    via `__cause__`/`__context__` from any future logger.exception() or
    traceback dump upstream, and must never appear in the raised
    exception's own message either."""
    secret_url = f"{DOCUMENT_HOST}/super-secret-path?X-Amz-Signature=leak-me-not"
    download_transport = _RaisingDownloadTransport()
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)

    with pytest.raises(SpApiRequestFailedError) as excinfo:
        await client.download_report_document(ReportDocumentInfo(url=secret_url, compression_algorithm=None))

    # `__cause__` is explicitly severed, and `__suppress_context__` (set
    # automatically by `raise ... from None`) is what makes every
    # standard traceback formatter — `traceback.format_exception`,
    # `logging.Formatter.formatException`, and therefore any
    # `logger.exception(...)` call anywhere upstream — skip printing the
    # original, URL-bearing `httpx.ConnectError` entirely, even though
    # the raw `__context__` reference technically still exists on the
    # object (Python always records it; only display is suppressed).
    assert excinfo.value.__cause__ is None
    assert excinfo.value.__suppress_context__ is True
    assert "super-secret-path" not in str(excinfo.value)
    assert "leak-me-not" not in str(excinfo.value)
    formatted = "".join(traceback.format_exception(type(excinfo.value), excinfo.value, excinfo.value.__traceback__))
    assert "super-secret-path" not in formatted
    assert "leak-me-not" not in formatted


@pytest.mark.asyncio
async def test_download_report_document_never_retried_on_5xx() -> None:
    """A presigned URL's own 5-minute lifetime is short enough that this
    client does not spend it on an in-call retry loop — a failed download
    is surfaced immediately to the caller."""
    download_transport = _ScriptedDownloadTransport(httpx.Response(500))
    client, _ = _client(_ScriptedTransport([]), download_transport=download_transport)
    with pytest.raises(SpApiRequestFailedError):
        await client.download_report_document(ReportDocumentInfo(url=f"{DOCUMENT_HOST}/x", compression_algorithm=None))
    assert len(download_transport.requests) == 1

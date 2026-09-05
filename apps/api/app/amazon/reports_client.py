"""SP-API Reports v2021-06-30 client, scoped to `GET_SALES_AND_TRAFFIC_
REPORT` (schema `sellerSalesAndTrafficReport.json`). Pinned against
`docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md` §1, fetched directly
from `amzn/selling-partner-api-models` during this milestone.

12B.6A scope only. This client:

- calls `createReport`, `getReport`, `getReportDocument` against the
  normal SP-API host, and separately downloads the actual report document
  from the **presigned URL** `getReportDocument` returns — a different
  host, never the SP-API host itself;
- never acquires an ingestion-run lease, creates an ingestion run, writes
  a fact row, advances a checkpoint, or decides run success/failure;
- never accesses a repository or a database session;
- exposes no HTTP route;
- never persists the presigned URL, an access token, or the raw report
  document bytes anywhere (see `docs/AI_HANDOVER/
  12B6A_SALES_TRAFFIC_REPORTS.md` §2) — `download_report_document` parses
  bytes in memory and returns a validated `SalesAndTrafficReport`, never
  writing the intermediate bytes to disk or a database column.

Reuses, rather than duplicates, existing infrastructure: `LwaClient` for
token resolution and the shared `SpApi*` exception taxonomy in
`app.core.exceptions` — no new exception type is needed. Mirrors
`orders_client.AmazonSpApiOrdersClient`'s constructor, retry loop, and
logging-redaction shape as closely as this report-oriented (rather than
page-oriented) API allows.

**Download-host validation, honestly scoped:** the pinned contract does
not publish a fixed allowlist of hostnames a presigned report-document
URL may resolve to (unlike, say, a documented API host per region/
environment) — Amazon's own presigned-URL infrastructure is not named as
a fixed set of hosts anywhere in the pinned schema or the Reports API
model. This client therefore enforces the two properties that *are*
verifiable and safe regardless of the exact host: the URL's scheme must
be `https` (never `http`, never anything else), and the download never
follows a redirect (`follow_redirects=False` — a 3xx response is treated
as a download failure, never silently followed to a second, unvalidated
location). This is a narrower, more conservative guarantee than "matches
an allowlisted Amazon domain," stated honestly as such rather than
inventing a specific hostname suffix this milestone did not independently
verify.

Logging: `logger.warning(...)` calls never include the report id, report
document id, the presigned URL, or any response body — only a fixed
message and (where relevant) an HTTP status code. A process-lifetime
`logging.Filter` on the `httpx` logger (mirroring `orders_client.py`'s and
`listings_client.py`'s own identical mechanism) rewrites the `{reportId}`/
`{reportDocumentId}` path segments of this API's own URLs before a
`LogRecord` is emitted — the presigned document URL itself is fetched via
a bare `httpx.AsyncClient` call outside of any URL this filter needs to
rewrite (its host is never the SP-API host these patterns are anchored
to), but that call's own `httpx` INFO-level URL log line is suppressed
entirely (see `_download_report_document_bytes`) rather than relying on
pattern-matching an unpredictable presigned-URL shape.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import re
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.lwa import LwaClient
from app.amazon.sales_traffic_models import SalesAndTrafficReport
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

CREATE_REPORT = "createReport"
GET_REPORT = "getReport"
GET_REPORT_DOCUMENT = "getReportDocument"
REPORTS_PATH = "/reports/2021-06-30/reports"
REPORT_PATH_TEMPLATE = "/reports/2021-06-30/reports/{report_id}"
REPORT_DOCUMENT_PATH_TEMPLATE = "/reports/2021-06-30/documents/{report_document_id}"
REPORTS_MODEL_VERSION = "reports-api-model/2021-06-30"

SALES_AND_TRAFFIC_REPORT_TYPE = "GET_SALES_AND_TRAFFIC_REPORT"

TERMINAL_PROCESSING_STATUSES = frozenset({"DONE", "CANCELLED", "FATAL"})
_VALID_PROCESSING_STATUSES = frozenset({"IN_QUEUE", "IN_PROGRESS", *TERMINAL_PROCESSING_STATUSES})
_VALID_DATE_GRANULARITIES = frozenset({"DAY", "WEEK", "MONTH"})
_VALID_ASIN_GRANULARITIES = frozenset({"PARENT", "CHILD", "SKU"})
_VALID_COMPRESSION_ALGORITHMS = frozenset({"GZIP"})

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 30.0

# Defensive bounds on the downloaded document itself — never documented by
# Amazon as a hard contract, but a production API must not accept an
# unbounded stream from any external URL, including one Amazon itself
# issued. 64 MiB comfortably covers a full-catalog, full-year, SKU-
# granularity report; anything larger is treated as a failure, not
# silently truncated (truncating a compressed/JSON stream would produce
# corrupt, not partial, data).
MAX_DOCUMENT_BYTES = 64 * 1024 * 1024
DOWNLOAD_TIMEOUT_SECONDS = 60.0

# Separate ceiling on *decompressed* output — a GZIP stream's compression
# ratio is attacker/corruption-controlled, not bounded by its own
# (already-capped) compressed size, so bounding only the download loop
# above (§ MAX_DOCUMENT_BYTES) does nothing to stop a small compressed
# payload from expanding to gigabytes once decompressed (a "decompression
# bomb"). 256 MiB comfortably exceeds any realistic JSON rendering of this
# report's own data (bounded by the requested window/marketplace/ASIN
# count, never unbounded) while still being a hard, enforced ceiling —
# see `_bounded_gzip_decompress`, which never calls `gzip.decompress()`
# directly (that call materializes the *entire* decompressed output in
# one unbounded allocation before this module ever gets a chance to
# reject it).
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024
_DECOMPRESS_CHUNK_BYTES = 1024 * 1024

_MAX_PROVENANCE_HEADER_LENGTH = 256


def _bounded_gzip_decompress(data: bytes, max_output_bytes: int) -> bytes:
    """Streaming GZIP decompression with a hard output-size ceiling.

    `gzip.decompress(data)` (the naive approach) decompresses everything
    in one call, allocating the full output before any caller can inspect
    or reject its size — exactly the shape of a decompression-bomb
    vulnerability (a tiny compressed payload that expands to an
    arbitrarily large output). This function instead feeds the
    (already size-capped, per `MAX_DOCUMENT_BYTES`) compressed bytes
    through `zlib`'s streaming decompressor in bounded output chunks,
    checking the accumulated size after every chunk and raising the
    moment it exceeds `max_output_bytes` — memory use is bounded to
    `max_output_bytes` plus at most one `_DECOMPRESS_CHUNK_BYTES`
    overshoot, regardless of what the compressed stream claims or
    actually contains.

    `zlib.MAX_WBITS | 16` selects GZIP-format framing (RFC 1952) rather
    than raw DEFLATE or zlib-wrapped DEFLATE — the format this report
    type's own `compressionAlgorithm: "GZIP"` value names.

    A stream that runs out of input before reaching `eof` (truncated
    mid-transfer) breaks out of the loop rather than looping forever —
    whatever partial bytes were decoded are handed to the caller's own
    JSON parser, which rejects truncated JSON on its own; this function
    never pretends a truncated stream decompressed successfully.
    """
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    output = bytearray()
    pending = data
    while not decompressor.eof:
        if not pending:
            break
        chunk = decompressor.decompress(pending, _DECOMPRESS_CHUNK_BYTES)
        output += chunk
        if len(output) > max_output_bytes:
            raise SpApiParseFailedError("Amazon report document exceeded the maximum allowed decompressed size.")
        pending = decompressor.unconsumed_tail
    output += decompressor.flush()
    if len(output) > max_output_bytes:
        raise SpApiParseFailedError("Amazon report document exceeded the maximum allowed decompressed size.")
    return bytes(output)


# --- centralized httpx report-ID log redaction (see module docstring) -----

_REPORT_ID_PATH_PATTERN = re.compile(r"(/reports/2021-06-30/reports/)[^/?\s]+")
_REPORT_DOCUMENT_ID_PATH_PATTERN = re.compile(r"(/reports/2021-06-30/documents/)[^/?\s]+")


def _redact_reports_sensitive_url_parts(value: object) -> object:
    text = str(value)
    if "/reports/2021-06-30/reports/" in text:
        text = _REPORT_ID_PATH_PATTERN.sub(r"\1{reportId}", text)
    if "/reports/2021-06-30/documents/" in text:
        text = _REPORT_DOCUMENT_ID_PATH_PATTERN.sub(r"\1{reportDocumentId}", text)
    return text if text != str(value) else value


class _RedactReportsIdFilter(logging.Filter):
    """Mirrors `orders_client._RedactOrdersOrderIdFilter` exactly — see
    that class's docstring for the full reasoning."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    _redact_reports_sensitive_url_parts(a) if isinstance(a, str | httpx.URL) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (_redact_reports_sensitive_url_parts(v) if isinstance(v, str | httpx.URL) else v)
                    for k, v in record.args.items()
                }
        if isinstance(record.msg, str):
            record.msg = _redact_reports_sensitive_url_parts(record.msg)
        return True


_httpx_reports_id_redaction_installed = False


def _ensure_httpx_reports_id_redaction_installed() -> None:
    global _httpx_reports_id_redaction_installed
    if _httpx_reports_id_redaction_installed:
        return
    target = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactReportsIdFilter) for f in target.filters):
        target.addFilter(_RedactReportsIdFilter())
    _httpx_reports_id_redaction_installed = True


def _sanitize_provenance_header(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_PROVENANCE_HEADER_LENGTH]


def _parse_retry_after(raw: str | None) -> float | None:
    """Identical logic to `orders_client._parse_retry_after` — duplicated
    rather than imported for the same reason documented there (a small,
    self-contained, provider-agnostic HTTP helper, not SP-API-specific
    business logic)."""
    if not raw:
        return None
    raw = raw.strip()
    try:
        seconds = float(raw)
        return seconds if seconds >= 0 else None
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        target = parsedate_to_datetime(raw)
        if target.tzinfo is None:
            target = target.replace(tzinfo=UTC)
        delta = (target - datetime.now(UTC)).total_seconds()
        return delta if delta >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


class _TransportFailure(Exception):
    """Internal marker for a timeout/connection failure on one HTTP
    attempt. Never raised to callers of this module."""


class _TransientServerFailure(Exception):
    """Internal marker for a 5xx response on one HTTP attempt. Never
    raised to callers."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"transient server failure status={status}")


@dataclass(frozen=True)
class CreateSalesAndTrafficReportRequest:
    """One `createReport` request for `GET_SALES_AND_TRAFFIC_REPORT`.
    Exactly one marketplace id — the pinned contract's own restriction
    (handover doc §1), enforced here at construction, not left to Amazon
    to reject after a wasted rate-limit-budget request."""

    marketplace_id: str
    data_start_time: date
    data_end_time: date
    date_granularity: str = "DAY"
    asin_granularity: str = "SKU"

    def __post_init__(self) -> None:
        if not self.marketplace_id or not self.marketplace_id.strip():
            raise SpApiConfigurationError("A single marketplace ID is required to request this report.")
        if self.date_granularity not in _VALID_DATE_GRANULARITIES:
            raise SpApiConfigurationError(f"Unsupported dateGranularity: {self.date_granularity!r}")
        if self.asin_granularity not in _VALID_ASIN_GRANULARITIES:
            raise SpApiConfigurationError(f"Unsupported asinGranularity: {self.asin_granularity!r}")
        if self.data_start_time > self.data_end_time:
            raise SpApiConfigurationError("dataStartTime must not be after dataEndTime.")


@dataclass(frozen=True)
class ReportStatus:
    """Sanitized `getReport` result — never carries the presigned URL
    (this object is built before `getReportDocument` is ever called)."""

    report_id: str
    processing_status: str
    report_document_id: str | None


@dataclass(frozen=True)
class ReportDocumentInfo:
    """Sanitized `getReportDocument` result. `url` is intentionally the
    one place in this object graph the presigned URL is ever held — in
    memory, for the immediate download that follows, never logged, never
    passed to any persistence call anywhere in this codebase."""

    url: str
    compression_algorithm: str | None


class AmazonSpApiReportsClient:
    """`POST/GET /reports/2021-06-30/reports(/{reportId})`,
    `GET /reports/2021-06-30/documents/{reportDocumentId}`, and a separate
    presigned-URL document download, with an injected seller refresh
    token."""

    def __init__(
        self,
        *,
        client_id: SecretStr | str | None,
        client_secret: SecretStr | str | None,
        refresh_token: SecretStr,
        token_url: str,
        base_url: str,
        region: str,
        timeout_seconds: float = 30,
        user_agent: str = "AmazonSellerIntelligence/12B.6A (Language=Python/3.12)",
        transport: httpx.BaseTransport | None = None,
        download_transport: httpx.BaseTransport | None = None,
        lwa: LwaClient | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
        max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] | None = None,
        max_document_bytes: int = MAX_DOCUMENT_BYTES,
        max_decompressed_bytes: int = MAX_DECOMPRESSED_BYTES,
        download_timeout_seconds: float = DOWNLOAD_TIMEOUT_SECONDS,
    ) -> None:
        if not isinstance(refresh_token, SecretStr):
            raise SpApiConfigurationError("Amazon seller refresh token is not configured.")
        if max_attempts < 1:
            raise SpApiConfigurationError("Reports client max_attempts must be at least 1.")
        _ensure_httpx_reports_id_redaction_installed()
        self._region = (region or "eu").strip().lower()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._transport = transport
        # A distinct, independently-injectable transport for the presigned-
        # URL download step — that request goes to a different host than
        # every other call this client makes, and a test needs to be able
        # to mock the two independently.
        self._download_transport = download_transport or transport
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.random
        self._max_document_bytes = max_document_bytes
        self._max_decompressed_bytes = max_decompressed_bytes
        self._download_timeout_seconds = download_timeout_seconds
        self._lwa = lwa or LwaClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=token_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "AmazonSpApiReportsClient()"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint_host(self) -> str:
        return urlparse(self._base_url).netloc

    def _backoff_delay(self, attempt_index: int) -> float:
        capped = min(self._max_delay_seconds, self._base_delay_seconds * (2 ** (attempt_index - 1)))
        return capped * self._jitter()

    def _rate_limit_delay(self, retry_after_seconds: float | None, attempt_index: int) -> float:
        if retry_after_seconds is None:
            return self._backoff_delay(attempt_index)
        return min(retry_after_seconds, self._max_delay_seconds)

    def _headers(self, access_token: str) -> dict[str, str]:
        return {
            "x-amz-access-token": access_token,
            "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "user-agent": self._user_agent,
            "accept": "application/json",
        }

    async def _single_get(self, url: str, headers: dict[str, str]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                return await client.get(url, headers=headers)
        except httpx.TimeoutException:
            logger.warning("SP-API reports request timed out")
            raise _TransportFailure("timeout") from None
        except httpx.HTTPError:
            logger.warning("SP-API reports request failed")
            raise _TransportFailure("transport") from None

    async def _single_post(self, url: str, headers: dict[str, str], json_body: dict) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                return await client.post(url, headers=headers, json=json_body)
        except httpx.TimeoutException:
            logger.warning("SP-API reports request timed out")
            raise _TransportFailure("timeout") from None
        except httpx.HTTPError:
            logger.warning("SP-API reports request failed")
            raise _TransportFailure("transport") from None

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status in {200, 202}:
            return
        if status in {401, 403}:
            logger.warning("SP-API reports authentication failed status=%s", status)
            raise SpApiAuthenticationError("Amazon SP-API reports authentication failed.")
        if status == 429:
            logger.warning("SP-API reports rate-limited status=%s", status)
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise SpApiRateLimitedError("Amazon SP-API reports rate limit reached.", retry_after_seconds=retry_after)
        if status >= 500:
            logger.warning("SP-API reports server failure status=%s", status)
            raise _TransientServerFailure(status)
        logger.warning("SP-API reports request rejected status=%s", status)
        raise SpApiInvalidRequestError(f"Amazon SP-API reports request was rejected (status={status}).")

    async def _call_with_retry(self, call: Callable[[str], Awaitable[httpx.Response]]) -> tuple[httpx.Response, int]:
        """Shared retry loop, parameterized over a single already-bound
        HTTP call (a `functools.partial`-style closure over the actual
        `GET`/`POST` and its own fixed URL/body) — this API's three
        operations have different HTTP methods and bodies, unlike Orders'
        two GET-only operations, so the loop takes the call itself rather
        than a fixed `(url, params)` pair."""
        access_token = (await self._lwa.fetch_access_token()).access_token.get_secret_value()
        response: httpx.Response | None = None
        attempt = 0
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await call(access_token)
            except _TransportFailure:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise SpApiRequestFailedError("Amazon SP-API reports request could not be completed.") from None

            try:
                self._raise_for_status(response)
                break
            except SpApiRateLimitedError as exc:
                if attempt < self._max_attempts:
                    await self._sleep(self._rate_limit_delay(exc.retry_after_seconds, attempt))
                    continue
                raise
            except _TransientServerFailure:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise SpApiRequestFailedError("Amazon SP-API reports request failed.") from None

        del access_token
        assert response is not None
        return response, attempt

    async def create_report(self, request: CreateSalesAndTrafficReportRequest) -> tuple[str, int]:
        """Returns `(report_id, attempt_count)`. Never retries a stale
        body across attempts — the same `json_body` (built once, before
        the retry loop) is sent on every attempt, matching the "stable
        request parameters across retries" requirement."""
        url = f"{self._base_url}{REPORTS_PATH}"
        json_body = {
            "reportType": SALES_AND_TRAFFIC_REPORT_TYPE,
            "marketplaceIds": [request.marketplace_id],
            "dataStartTime": request.data_start_time.isoformat(),
            "dataEndTime": request.data_end_time.isoformat(),
            "reportOptions": {
                "dateGranularity": request.date_granularity,
                "asinGranularity": request.asin_granularity,
            },
        }

        async def _call(access_token: str) -> httpx.Response:
            return await self._single_post(url, self._headers(access_token), json_body)

        response, attempt_count = await self._call_with_retry(_call)
        try:
            payload = response.json()
            report_id = str(payload["reportId"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SpApiParseFailedError("Amazon SP-API createReport response could not be parsed.") from exc
        return report_id, attempt_count

    async def get_report(self, report_id: str) -> ReportStatus:
        report_id = (report_id or "").strip()
        if not report_id:
            raise SpApiConfigurationError("A report id is required to call Reports getReport.")
        url = f"{self._base_url}{REPORT_PATH_TEMPLATE.format(report_id=report_id)}"

        async def _call(access_token: str) -> httpx.Response:
            return await self._single_get(url, self._headers(access_token))

        response, _attempt_count = await self._call_with_retry(_call)
        try:
            payload = response.json()
            processing_status = str(payload["processingStatus"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SpApiParseFailedError("Amazon SP-API getReport response could not be parsed.") from exc
        if processing_status not in _VALID_PROCESSING_STATUSES:
            raise SpApiParseFailedError("Amazon SP-API getReport returned an unrecognized processingStatus.")
        report_document_id = payload.get("reportDocumentId")
        # `DONE` is the pinned contract's own documented guarantee of a
        # populated `reportDocumentId` (handover doc §1: "DONE is the only
        # state with a populated reportDocumentId to retrieve") — a DONE
        # response missing it is a contract violation, not a "document not
        # ready yet" state to wait out (there is no such intermediate
        # state; the enum above is exhaustive). Rejecting it here, at the
        # client boundary, means the ingestion service can treat a DONE
        # `ReportStatus` as unconditionally safe to pass to
        # `getReportDocument` — never a bare `assert` on a value this
        # client itself already guaranteed.
        if processing_status == "DONE" and not report_document_id:
            raise SpApiParseFailedError("Amazon SP-API getReport returned DONE without a reportDocumentId.")
        return ReportStatus(
            report_id=report_id,
            processing_status=processing_status,
            report_document_id=report_document_id,
        )

    async def get_report_document(self, report_document_id: str) -> ReportDocumentInfo:
        report_document_id = (report_document_id or "").strip()
        if not report_document_id:
            raise SpApiConfigurationError("A report document id is required to call Reports getReportDocument.")
        url = f"{self._base_url}{REPORT_DOCUMENT_PATH_TEMPLATE.format(report_document_id=report_document_id)}"

        async def _call(access_token: str) -> httpx.Response:
            return await self._single_get(url, self._headers(access_token))

        response, _attempt_count = await self._call_with_retry(_call)
        try:
            payload = response.json()
            url_value = str(payload["url"])
        except (ValueError, KeyError, TypeError) as exc:
            raise SpApiParseFailedError("Amazon SP-API getReportDocument response could not be parsed.") from exc
        compression_algorithm = payload.get("compressionAlgorithm")
        if compression_algorithm is not None and compression_algorithm not in _VALID_COMPRESSION_ALGORITHMS:
            raise SpApiParseFailedError("Amazon SP-API getReportDocument returned an unsupported compressionAlgorithm.")
        return ReportDocumentInfo(url=url_value, compression_algorithm=compression_algorithm)

    async def download_report_document(self, document_info: ReportDocumentInfo) -> SalesAndTrafficReport:
        """Downloads and parses the actual report document from its
        presigned URL — never the SP-API host. Never retried (a presigned
        URL expires after 5 minutes total, per the pinned contract; a
        failed download is a caller-level decision, not this method's own
        retry loop, to avoid silently consuming most of that 5-minute
        window on retries for a URL that may already be near expiry)."""
        parsed = urlparse(document_info.url)
        if parsed.scheme != "https":
            raise SpApiInvalidRequestError("Refusing to download a report document over a non-HTTPS URL.")

        try:
            async with httpx.AsyncClient(
                timeout=self._download_timeout_seconds,
                transport=self._download_transport,
                follow_redirects=False,
            ) as client:
                async with client.stream("GET", document_info.url) as response:
                    if response.status_code != 200:
                        logger.warning("SP-API report document download failed status=%s", response.status_code)
                        raise SpApiRequestFailedError("Amazon report document download failed.")
                    chunks: list[bytes] = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > self._max_document_bytes:
                            raise SpApiRequestFailedError("Amazon report document exceeded the maximum allowed size.")
                        chunks.append(chunk)
        except httpx.TimeoutException:
            logger.warning("SP-API report document download timed out")
            raise SpApiRequestFailedError("Amazon report document download timed out.") from None
        except httpx.HTTPError:
            # `from None` (not `from exc`) deliberately severs the
            # exception chain here — unlike every other `from exc` site
            # in this module, `httpx.HTTPError`'s own string
            # representation embeds the request URL, which for this one
            # call *is* the presigned document URL. Chaining it as
            # `__cause__` would make it reachable by any future logger.
            # exception()/traceback dump anywhere upstream, even though
            # nothing today logs it — severing the chain here removes
            # that possibility structurally rather than relying on every
            # caller, forever, never adding one.
            logger.warning("SP-API report document download transport failure")
            raise SpApiRequestFailedError("Amazon report document download failed.") from None

        raw = b"".join(chunks)
        if document_info.compression_algorithm == "GZIP":
            try:
                raw = _bounded_gzip_decompress(raw, self._max_decompressed_bytes)
            except zlib.error as exc:
                raise SpApiParseFailedError("Amazon report document GZIP payload could not be decompressed.") from exc

        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as exc:
            raise SpApiParseFailedError("Amazon report document was not valid JSON.") from exc

        try:
            return SalesAndTrafficReport.model_validate(payload)
        except ValidationError as exc:
            raise SpApiParseFailedError("Amazon report document did not match the pinned contract shape.") from exc

    def _provenance_headers(self, response: httpx.Response) -> dict[str, str | None]:
        return {
            "rate_limit": _sanitize_provenance_header(response.headers.get("x-amzn-RateLimit-Limit")),
            "request_id": _sanitize_provenance_header(response.headers.get("x-amzn-RequestId")),
        }

"""SP-API Orders v2026-01-01 read client. One official page/order at a time.

`GET /orders/2026-01-01/orders` (`searchOrders`) and
`GET /orders/2026-01-01/orders/{orderId}` (`getOrder`). Pinned against
`docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`, re-verified directly
against the primary-source model file during 12B.4C (see `orders_models.py`
module docstring for the checksum match).

12B.4C scope only. This client:

- fetches and parses exactly one official `searchOrders` page per call —
  never traverses `nextToken`/`paginationToken` itself (no loop inside this
  file);
- fetches exactly one order per `getOrder` call;
- never acquires an ingestion-run lease, creates an ingestion run, writes an
  order/item row, advances a checkpoint, or decides run success/failure;
- never accesses a repository or a database session;
- exposes no HTTP route;
- never requests or retains customer PII (see `orders_models.py`).

Durable pagination, incremental-cursor management, checkpoint advancement,
and worker lifecycle all belong to 12B.4D. This mirrors the same boundary
`AmazonSpApiListingsClient` (`listings_client.py`) already holds relative to
12B.3D.

Reuses, rather than duplicates, existing infrastructure: `LwaClient` for
token resolution, `sp_api_base_url()` for host resolution (region +
environment + override conventions, unchanged), and the shared `SpApi*`
exception taxonomy in `app.core.exceptions` — no new exception type is
needed; every failure mode below (config, auth, rate limit, invalid
request, transient failure, parse failure) already has a corresponding
type from the Listings/Sellers work.

**Signing note (12B.4C Phase 2 finding):** this codebase's existing SP-API
clients (`AmazonSpApiListingsClient`, `AmazonSpApiSellersClient`) authorize
every request with only an LWA bearer access token
(`x-amz-access-token` header) — there is no AWS SigV4 request-signing
module anywhere in this repository to reuse, because this application's
self-authorization model does not require one (SP-API's LWA-only
authorization path, the same one already proven live for Listings and the
Sellers validation handshake). This client follows that same, already-
proven pattern exactly. No new signing infrastructure is introduced.

Logging: this module's own `logger.warning(...)` calls never include the
order ID, the request URL, query parameters, or any response body — only
a fixed message and (where relevant) an HTTP status code. `getOrder`'s URL
path embeds the caller-supplied Amazon order ID
(`/orders/2026-01-01/orders/{orderId}`), and `httpx` logs the full request
URL through its own `httpx` logger at INFO for every call regardless of
what this module's own code does — the identical dependency-level leak
`listings_client.py` already found and fixed for `sellerId`. This module
installs the same kind of fix: a small, centralized, process-lifetime
`logging.Filter` on the `httpx` logger (idempotent, installed once by the
first client instance constructed) that rewrites only the `{orderId}` path
segment of `getOrder` URLs to a fixed placeholder before a record is
emitted. The same filter also rewrites `searchOrders`' `paginationToken`
query-parameter value to a placeholder — Phase 3 explicitly requires this
token never be logged, persisted, or exposed, and `httpx`'s own URL
logging would otherwise put it in every `searchOrders` INFO log line.
Every other part of every URL (LWA, Sellers, Sandbox, Listings, and every
non-token/non-order-ID part of Orders' own URLs) is left fully
unaffected. See
`listings_client.py`'s module docstring for why this deliberately does not
also attach to the bare `httpcore` logger (same reasoning applies
identically here, and this test suite uses `httpx.MockTransport`
everywhere, so `httpcore` code never executes in tests regardless).
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import quote, urlparse

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.lwa import LwaClient
from app.amazon.orders_models import (
    GetOrderResponse,
    OrderResult,
    OrdersPage,
    OrdersPageProvenance,
    SearchOrdersResponse,
)
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

SEARCH_ORDERS = "searchOrders"
GET_ORDER = "getOrder"
ORDERS_PATH = "/orders/2026-01-01/orders"
ORDER_PATH_TEMPLATE = "/orders/2026-01-01/orders/{order_id}"
ORDERS_MODEL_VERSION = "orders-api-model/2026-01-01"

# 12B.4A rule: fixed, non-PII, not caller-overridable. Deliberately excludes
# BUYER, RECIPIENT, PAYMENT, TAX (never requested — see orders_models.py
# module docstring) and, for this first slice, EXPENSE/PROMOTION/
# FULFILLMENT_ORDERS (unneeded for the stated analytics goals). Order
# matters only for producing a stable, reviewable query string — Amazon
# does not document any ordering requirement.
APPROVED_INCLUDED_DATA: tuple[str, ...] = ("PROCEEDS", "FULFILLMENT", "CANCELLATION", "PACKAGES")

MAX_MARKETPLACE_IDS_PER_REQUEST = 50
MIN_RESULTS_PER_PAGE = 1
MAX_RESULTS_PER_PAGE = 100
DEFAULT_RESULTS_PER_PAGE = 100

# Documented usage-plan defaults (12B.4A, primary source: the model's own
# `x-amzn-throttling` extension). Recorded here for callers/tests that need
# to reason about or assert against them — this client's own short retry
# loop below does NOT pace requests against these numbers (that durable,
# cross-call pacing belongs to 12B.4D's worker). Amazon's own
# `x-amzn-RateLimit-Limit` response header, when present, is authoritative
# over these static defaults (12B.4A) — this client surfaces that header
# via `OrdersPageProvenance.rate_limit` for a future caller to act on, but
# does not itself adapt its pacing to it.
SEARCH_ORDERS_DEFAULT_RATE_LIMIT_PER_SECOND = 0.0056
SEARCH_ORDERS_DEFAULT_BURST = 20
GET_ORDER_DEFAULT_RATE_LIMIT_PER_SECOND = 0.5
GET_ORDER_DEFAULT_BURST = 30

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 1.0
DEFAULT_MAX_DELAY_SECONDS = 30.0

_MAX_PROVENANCE_HEADER_LENGTH = 256


# --- centralized httpx order-ID log redaction (see module docstring) ------

_ORDERS_ORDER_ID_PATH_PATTERN = re.compile(r"(/orders/2026-01-01/orders/)[^/?\s]+")

# `paginationToken` never appears in a URL *path* (it is always a query
# parameter — see `search_orders`), so this pattern is anchored on the
# query-string key, not any path prefix. Matches up to the next `&` or the
# end of string/whitespace, covering both a bare query string and one
# already embedded in a full URL as logged by httpx.
_ORDERS_PAGINATION_TOKEN_QUERY_PATTERN = re.compile(r"(paginationToken=)[^&\s]+")


def _redact_orders_sensitive_url_parts(value: object) -> object:
    """Rewrites the `{orderId}` path segment of `getOrder` URLs and the
    `paginationToken` query-parameter value of `searchOrders` URLs before a
    `LogRecord` is emitted. Neither is a secret in the credential sense,
    but both must never appear in logs per 12B.4A/12B.4C: an order ID is a
    business identifier kept out of logs on the same posture already
    enforced for Listings, and a pagination token is explicitly required
    (12B.4C Phase 3) to never be logged, persisted, or exposed — it is a
    live, 24-hour-lived credential-adjacent value naming a specific,
    in-flight Amazon query cursor."""
    text = str(value)
    if "/orders/2026-01-01/orders/" in text:
        text = _ORDERS_ORDER_ID_PATH_PATTERN.sub(r"\1{orderId}", text)
    if "paginationToken=" in text:
        text = _ORDERS_PAGINATION_TOKEN_QUERY_PATTERN.sub(r"\1{paginationToken}", text)
    return text if text != str(value) else value


class _RedactOrdersOrderIdFilter(logging.Filter):
    """Rewrites the `{orderId}` path segment and `paginationToken` query
    value described above in a `LogRecord` before it is emitted. Mirrors
    `listings_client._RedactListingsSellerIdFilter`'s mechanism exactly —
    see that class's docstring for the full reasoning (idempotent
    installation, stateless filtering, why only the `httpx` logger and not
    `httpcore`). Never touches any other endpoint's URL, and never touches
    `searchOrders`' URL beyond its `paginationToken` value (there is no
    order-scoped path segment on that operation's URL at all)."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    _redact_orders_sensitive_url_parts(a) if isinstance(a, str | httpx.URL) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (_redact_orders_sensitive_url_parts(v) if isinstance(v, str | httpx.URL) else v)
                    for k, v in record.args.items()
                }
        if isinstance(record.msg, str):
            record.msg = _redact_orders_sensitive_url_parts(record.msg)
        return True


_httpx_order_id_redaction_installed = False


def _ensure_httpx_order_id_redaction_installed() -> None:
    """Idempotent: installs `_RedactOrdersOrderIdFilter` on the `httpx`
    logger exactly once per process, regardless of how many
    `AmazonSpApiOrdersClient` instances are constructed. Coexists with
    `listings_client`'s own filter on the same logger — both are
    independent, stateless `logging.Filter` instances; installing one does
    not remove or interfere with the other."""
    global _httpx_order_id_redaction_installed
    if _httpx_order_id_redaction_installed:
        return
    target = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactOrdersOrderIdFilter) for f in target.filters):
        target.addFilter(_RedactOrdersOrderIdFilter())
    _httpx_order_id_redaction_installed = True


def _sanitize_provenance_header(value: str | None) -> str | None:
    """Identical policy to `listings_client._sanitize_provenance_header`:
    bounds and strips control characters from a header value before it
    reaches `OrdersPageProvenance`. Not a documented Amazon constraint —
    defensive hygiene applied to attacker-influenced upstream data."""
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_PROVENANCE_HEADER_LENGTH]


def _parse_retry_after(raw: str | None) -> float | None:
    """RFC 7231 `Retry-After`: either delta-seconds or an HTTP-date. Never
    raises — malformed/negative/absent is simply "no usable signal"
    (`None`). Identical logic to `listings_client._parse_retry_after`;
    duplicated rather than imported because it is a small, self-contained,
    provider-agnostic HTTP helper, not SP-API-specific business logic (see
    module docstring's "do not duplicate signing/credential/token logic" —
    this is neither)."""
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
    """Internal marker for a timeout/connection failure on one HTTP attempt.
    Never raised to callers of this module."""


class _TransientServerFailure(Exception):
    """Internal marker for a 5xx response on one HTTP attempt. Never raised
    to callers."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"transient server failure status={status}")


def _normalize_marketplace_ids(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(v.strip() for v in values if v and v.strip())


@dataclass(frozen=True)
class SearchOrdersPageRequest:
    """One `searchOrders` page request.

    Normalization (the only transformation applied to any field here, and
    applied exactly once at construction time so every downstream use —
    the query parameters, every retry attempt — reads the same already-
    normalized values): `marketplace_ids` entries and `pagination_token`
    have surrounding whitespace stripped; blank marketplace IDs are
    dropped; an all-whitespace `pagination_token` is treated as absent
    (`None`). Beyond that, `pagination_token` is forwarded to Amazon
    byte-for-byte unchanged, and is never logged or persisted by this
    module (12B.4A: token lifetime is 24 hours, and it is meaningful only
    within one paginated traversal held in a caller's own memory).
    """

    marketplace_ids: tuple[str, ...]
    created_after: datetime | None = None
    created_before: datetime | None = None
    last_updated_after: datetime | None = None
    last_updated_before: datetime | None = None
    max_results_per_page: int = DEFAULT_RESULTS_PER_PAGE
    pagination_token: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "marketplace_ids", _normalize_marketplace_ids(self.marketplace_ids))
        normalized_token = (self.pagination_token or "").strip()
        object.__setattr__(self, "pagination_token", normalized_token or None)


@dataclass(frozen=True)
class GetOrderRequest:
    """One `getOrder` request. `order_id` is the caller-supplied Amazon
    order identifier — never derived from any other API response."""

    order_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "order_id", (self.order_id or "").strip())


def _validate_search_request(request: SearchOrdersPageRequest) -> None:
    if not request.marketplace_ids:
        raise SpApiConfigurationError("At least one marketplace ID is required to call Orders searchOrders.")
    if len(request.marketplace_ids) > MAX_MARKETPLACE_IDS_PER_REQUEST:
        raise SpApiConfigurationError(
            f"Orders searchOrders accepts at most {MAX_MARKETPLACE_IDS_PER_REQUEST} marketplace IDs per request."
        )
    if not (MIN_RESULTS_PER_PAGE <= request.max_results_per_page <= MAX_RESULTS_PER_PAGE):
        raise SpApiConfigurationError(
            f"Orders searchOrders max_results_per_page must be between "
            f"{MIN_RESULTS_PER_PAGE} and {MAX_RESULTS_PER_PAGE}."
        )

    has_created = request.created_after is not None
    has_last_updated = request.last_updated_after is not None
    if has_created == has_last_updated:
        raise SpApiConfigurationError(
            "Orders searchOrders requires exactly one of created_after or last_updated_after."
        )
    if has_created:
        if request.last_updated_before is not None:
            raise SpApiConfigurationError(
                "Orders searchOrders: last_updated_before is not valid alongside created_after."
            )
        if request.created_before is not None and request.created_before < request.created_after:
            raise SpApiConfigurationError("Orders searchOrders: created_before must not precede created_after.")
    else:
        if request.created_before is not None:
            raise SpApiConfigurationError(
                "Orders searchOrders: created_before is not valid alongside last_updated_after."
            )
        if request.last_updated_before is not None and request.last_updated_before < request.last_updated_after:
            raise SpApiConfigurationError(
                "Orders searchOrders: last_updated_before must not precede last_updated_after."
            )


def _validate_get_order_request(request: GetOrderRequest) -> None:
    if not request.order_id:
        raise SpApiConfigurationError("An Amazon order ID is required to call Orders getOrder.")


class AmazonSpApiOrdersClient:
    """`GET /orders/2026-01-01/orders(/{orderId})` with an injected seller refresh token."""

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
        user_agent: str = "AmazonSellerIntelligence/12B.4C (Language=Python/3.12)",
        transport: httpx.BaseTransport | None = None,
        lwa: LwaClient | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_seconds: float = DEFAULT_BASE_DELAY_SECONDS,
        max_delay_seconds: float = DEFAULT_MAX_DELAY_SECONDS,
        sleep: Callable[[float], Awaitable[None]] | None = None,
        jitter: Callable[[], float] | None = None,
    ) -> None:
        if not isinstance(refresh_token, SecretStr):
            raise SpApiConfigurationError("Amazon seller refresh token is not configured.")
        if max_attempts < 1:
            raise SpApiConfigurationError("Orders client max_attempts must be at least 1.")
        _ensure_httpx_order_id_redaction_installed()
        self._region = (region or "eu").strip().lower()
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._user_agent = user_agent
        self._transport = transport
        self._max_attempts = max_attempts
        self._base_delay_seconds = base_delay_seconds
        self._max_delay_seconds = max_delay_seconds
        self._sleep = sleep or asyncio.sleep
        self._jitter = jitter or random.random
        self._lwa = lwa or LwaClient(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
            token_url=token_url,
            timeout_seconds=timeout_seconds,
            transport=transport,
        )

    def __repr__(self) -> str:
        return "AmazonSpApiOrdersClient()"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint_host(self) -> str:
        return urlparse(self._base_url).netloc

    def _backoff_delay(self, attempt_index: int) -> float:
        """Bounded exponential backoff with full jitter, used when no valid
        `Retry-After` was supplied. `attempt_index` is 1-based (the attempt
        that just failed). This is short-lived, in-call retry only — it is
        not, and must not be read as, the durable ~178.6s `searchOrders`
        pacing documented in 12B.4A; that pacing is 12B.4D's job."""
        capped = min(self._max_delay_seconds, self._base_delay_seconds * (2 ** (attempt_index - 1)))
        return capped * self._jitter()

    def _rate_limit_delay(self, retry_after_seconds: float | None, attempt_index: int) -> float:
        """Amazon supplies no documented `Retry-After` header for either
        Orders operation (12B.4A), but this client honors one defensively
        if a valid, non-negative value is ever present — bounded to
        `max_delay_seconds` so a single malformed or unexpectedly large
        value can never stall this bounded, short-lived retry loop for
        longer than its own configured ceiling. Falls back to exponential
        backoff when the header is absent or unparseable."""
        if retry_after_seconds is None:
            return self._backoff_delay(attempt_index)
        return min(retry_after_seconds, self._max_delay_seconds)

    def _headers(self, access_token: str) -> dict[str, str]:
        """Built fresh for every HTTP attempt: `x-amz-date` reflects when
        THIS attempt was made. The access token, URL, and query parameters
        are part of the request's identity and are reused verbatim,
        unchanged, across every retry attempt."""
        return {
            "x-amz-access-token": access_token,
            "x-amz-date": datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            "user-agent": self._user_agent,
            "accept": "application/json",
        }

    async def _single_attempt(self, url: str, params: dict[str, str], headers: dict[str, str]) -> httpx.Response:
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                return await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException:
            logger.warning("SP-API orders request timed out")
            raise _TransportFailure("timeout") from None
        except httpx.HTTPError:
            logger.warning("SP-API orders request failed")
            raise _TransportFailure("transport") from None

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status == 200:
            return
        if status in {401, 403}:
            logger.warning("SP-API orders authentication failed status=%s", status)
            raise SpApiAuthenticationError("Amazon SP-API orders authentication failed.")
        if status == 429:
            logger.warning("SP-API orders rate-limited status=%s", status)
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise SpApiRateLimitedError("Amazon SP-API orders rate limit reached.", retry_after_seconds=retry_after)
        if status >= 500:
            logger.warning("SP-API orders server failure status=%s", status)
            raise _TransientServerFailure(status)
        logger.warning("SP-API orders request rejected status=%s", status)
        raise SpApiInvalidRequestError(f"Amazon SP-API orders request was rejected (status={status}).")

    async def _call_with_retry(self, url: str, params: dict[str, str]) -> httpx.Response:
        """Shared retry loop for both operations. Only rate-limit,
        transient-server, and transport failures are retried — never
        authentication, invalid-request, or (structurally, since parsing
        happens after this returns) parse failures. The URL and `params`
        are supplied once by the caller and reused verbatim across every
        attempt; only headers (via `_headers`, called fresh per attempt)
        change."""
        access_token = (await self._lwa.fetch_access_token()).access_token.get_secret_value()
        response: httpx.Response | None = None
        attempt = 0
        for attempt in range(1, self._max_attempts + 1):
            headers = self._headers(access_token)
            try:
                response = await self._single_attempt(url, params, headers)
            except _TransportFailure:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise SpApiRequestFailedError("Amazon SP-API orders request could not be completed.") from None

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
                raise SpApiRequestFailedError("Amazon SP-API orders request failed.") from None

        del access_token
        assert response is not None  # loop always returns or raises above
        return response, attempt

    def _provenance(self, response: httpx.Response, *, operation: str, attempt_count: int) -> OrdersPageProvenance:
        return OrdersPageProvenance(
            operation=operation,
            region=self._region,
            endpoint_host=self.endpoint_host,
            fetched_at=datetime.now(UTC),
            http_status=response.status_code,
            api_model_version=ORDERS_MODEL_VERSION,
            attempt_count=attempt_count,
            rate_limit=_sanitize_provenance_header(response.headers.get("x-amzn-RateLimit-Limit")),
            request_id=_sanitize_provenance_header(response.headers.get("x-amzn-RequestId")),
        )

    async def search_orders(self, request: SearchOrdersPageRequest) -> OrdersPage:
        """Fetch and parse exactly one `searchOrders` page. Never loops on
        `pagination.nextToken`/`paginationToken`."""
        _validate_search_request(request)
        url = f"{self._base_url}{ORDERS_PATH}"
        params: dict[str, str] = {
            "marketplaceIds": ",".join(request.marketplace_ids),
            "includedData": ",".join(APPROVED_INCLUDED_DATA),
            "maxResultsPerPage": str(request.max_results_per_page),
        }
        if request.created_after is not None:
            params["createdAfter"] = request.created_after.isoformat()
        if request.created_before is not None:
            params["createdBefore"] = request.created_before.isoformat()
        if request.last_updated_after is not None:
            params["lastUpdatedAfter"] = request.last_updated_after.isoformat()
        if request.last_updated_before is not None:
            params["lastUpdatedBefore"] = request.last_updated_before.isoformat()
        if request.pagination_token:
            params["paginationToken"] = request.pagination_token

        response, attempt_count = await self._call_with_retry(url, params)
        parsed = self._parse_search_response(response)
        next_token = parsed.pagination.next_token if parsed.pagination else None
        return OrdersPage(
            orders=parsed.orders,
            next_token=next_token,
            # Always the caller's own request, never anything read from
            # `parsed` — no field on `Order` should be able to override the
            # scope this page result reports itself as being fetched for.
            marketplace_ids=request.marketplace_ids,
            pagination_token_used=request.pagination_token,
            provenance=self._provenance(response, operation=SEARCH_ORDERS, attempt_count=attempt_count),
        )

    async def get_order(self, request: GetOrderRequest) -> OrderResult:
        """Fetch and parse exactly one order."""
        _validate_get_order_request(request)
        # `quote(..., safe="")` percent-encodes the order ID before it is
        # interpolated into the URL *path* — built by plain string
        # interpolation, so an unexpected character (e.g. a stray `/` or
        # `?`) could otherwise change the URL's structure.
        path = ORDER_PATH_TEMPLATE.format(order_id=quote(request.order_id, safe=""))
        url = f"{self._base_url}{path}"
        params: dict[str, str] = {"includedData": ",".join(APPROVED_INCLUDED_DATA)}

        response, attempt_count = await self._call_with_retry(url, params)
        parsed = self._parse_get_order_response(response)
        return OrderResult(
            order=parsed.order,
            provenance=self._provenance(response, operation=GET_ORDER, attempt_count=attempt_count),
        )

    def _parse_search_response(self, response: httpx.Response) -> SearchOrdersResponse:
        body = self._read_json(response)
        try:
            return SearchOrdersResponse.model_validate(body)
        except ValidationError:
            raise SpApiParseFailedError("Amazon SP-API orders payload was malformed.") from None

    def _parse_get_order_response(self, response: httpx.Response) -> GetOrderResponse:
        body = self._read_json(response)
        try:
            return GetOrderResponse.model_validate(body)
        except ValidationError:
            raise SpApiParseFailedError("Amazon SP-API orders payload was malformed.") from None

    def _read_json(self, response: httpx.Response) -> object:
        try:
            return response.json()
        except ValueError:
            raise SpApiParseFailedError("Amazon SP-API orders response was not JSON.") from None

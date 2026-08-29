"""SP-API Listings Items v2021-08-01 read client. One official page at a time.

`GET /listings/2021-08-01/items/{sellerId}` (`searchListingsItems`). Pinned
against `docs/AI_HANDOVER/12B3A_LISTINGS_ITEMS_API_CONTRACT_REPORT.md`.

12B.3C scope only. This client:

- fetches and parses exactly one official API page per call;
- never traverses pages itself (no loop over `nextToken` inside this file);
- never acquires an ingestion-run lease, creates an ingestion run, writes a
  listing, deactivates anything, or decides snapshot authority;
- never accesses a repository or a database session;
- exposes no HTTP route.

Full-page traversal, run-lifecycle management, and reconciliation belong to
12B.3D. This mirrors the same boundary `AmazonSpApiSellersClient`
(`sellers.py`) already holds for `getMarketplaceParticipations`: a client
performs one typed API call and returns a typed result; it does not decide
what the caller does with it.

Reuses, rather than duplicates, existing infrastructure: `LwaClient` for
token resolution, `sp_api_base_url()` for host resolution (region +
environment + override conventions, unchanged), and the shared
`SpApi*Error` taxonomy in `app.core.exceptions`. Adds exactly one new
exception (`SpApiInvalidRequestError`) because no existing type distinguished
a non-retryable 4xx from an exhausted-retry transient failure — everything
else is reused as-is.

Logging accuracy (12B.3C review correction): this module's own log
statements never include tokens, authorization headers, client secrets, the
seller ID, or raw response bodies — that claim was always true. An earlier
version of this docstring additionally claimed the client "never logs ...
the seller ID" without qualification, which was **inaccurate**: `httpx`
logs the full request URL through its own `httpx` logger at INFO level for
every call, and Amazon's contract puts `sellerId` in the URL *path*
(`/listings/2021-08-01/items/{sellerId}`), not a header — so that
dependency-level logging could include it regardless of anything this
module does. This module installs a small, centralized, process-lifetime
`logging.Filter` on the `httpx` logger (idempotent, installed once by the
first client instance constructed, never re-installed or toggled per
request) that rewrites only the `sellerId` path segment of Listings-Items
URLs to a fixed placeholder before a record is emitted — it does not
change any logger's level, does not touch any handler, and leaves every
other diagnostic value (method, host, status code, timing, and every
other endpoint's URL, including this codebase's own LWA/Sellers/Sandbox
calls) fully intact. Reproduced and proven fixed by
`test_httpx_logger_reproduction_and_redaction_of_seller_id`. See
`_ensure_httpx_seller_id_redaction_installed`.

Scope of this fix, stated precisely: it covers the `httpx` logger only,
because that is the specific, proven leak (INFO level, on by default the
moment anything raises the process's effective log level that far — this
application currently configures no logging at all, so nothing is emitted
today, but that is not a guarantee). It deliberately does **not** claim to
cover `httpcore` (the lower-level transport library `httpx` uses
internally): `httpcore`'s own DEBUG-level connection/protocol tracing logs
through per-component sub-logger names (`httpcore.connection`,
`httpcore.http11`, `httpcore.proxy`, `httpcore.socks`, `httpcore.http2`),
never the bare `httpcore` logger — and Python's logger-level filters are
checked only on the logger a record actually originates from, not
inherited by descendant loggers during propagation (verified directly: a
filter attached to `logging.getLogger("httpcore")` does not run for a
record logged via `logging.getLogger("httpcore.connection")`). A filter
attached only to the parent `httpcore` logger would therefore be a false
sense of protection, not a real one, so this module does not install one
there. Investigated separately: in the installed `httpcore` version, its
trace calls format request info via `repr(request)`, and `httpcore.
_models.Request.__repr__` returns only `<Request [b'GET']>` — the URL is
not part of that repr — so no evidence was found that current `httpcore`
tracing exposes the seller ID even when enabled. That is not a guarantee
about future `httpcore` versions or other trace paths, and `httpcore.*`
tracing only activates if an operator explicitly sets one of those
sub-loggers to DEBUG, which nothing in this codebase does. The production
control for this residual, unproven surface is operational, not code: do
not enable verbose transport/connection-level DEBUG logging in production.
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

from app.amazon.listings_models import ListingsPage, ListingsPageProvenance, SearchListingsItemsResponse
from app.amazon.lwa import LwaClient
from app.core.exceptions import (
    SpApiAuthenticationError,
    SpApiConfigurationError,
    SpApiInvalidRequestError,
    SpApiParseFailedError,
    SpApiRateLimitedError,
    SpApiRequestFailedError,
)

logger = logging.getLogger(__name__)

SEARCH_LISTINGS_ITEMS = "searchListingsItems"
LISTINGS_ITEMS_PATH_TEMPLATE = "/listings/2021-08-01/items/{seller_id}"
LISTINGS_MODEL_VERSION = "listings-items-api-model/2021-08-01"

# V1 rule: exactly this set, in this order. Never `attributes`,
# `relationships`, or `procurement` — see listings_models.py's module
# docstring for why those are not modeled at all, not just unrequested.
APPROVED_INCLUDED_DATA: tuple[str, ...] = (
    "summaries",
    "issues",
    "offers",
    "fulfillmentAvailability",
    "productTypes",
)

# V1 rule: fixed, not caller-overridable — removes an entire class of
# accidental deviation from the approved contract.
LISTINGS_PAGE_SIZE = 20

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.5
DEFAULT_MAX_DELAY_SECONDS = 8.0

# A provenance header value is attacker-influenced upstream data that this
# module cannot fully trust even from Amazon (a misbehaving proxy, a future
# contract change, or a compromised intermediary could send something
# unexpected). Bounded and stripped of control characters before it ever
# reaches a Pydantic model or a log line — cheap defense-in-depth, not a
# documented Amazon constraint.
_MAX_PROVENANCE_HEADER_LENGTH = 256


# --- centralized httpx seller-ID log redaction (see module docstring for
# why this covers httpx only, not httpcore) --------------------------------

_LISTINGS_SELLER_ID_PATH_PATTERN = re.compile(r"(/listings/2021-08-01/items/)[^/?\s]+")


def _redact_listings_seller_id(value: object) -> object:
    text = str(value)
    if "/listings/2021-08-01/items/" not in text:
        return value
    return _LISTINGS_SELLER_ID_PATH_PATTERN.sub(r"\1{sellerId}", text)


class _RedactListingsSellerIdFilter(logging.Filter):
    """Rewrites only the `sellerId` path segment of Listings-Items URLs in
    a `LogRecord` before it is emitted. Does not change any logger's level
    or handlers, and does not touch records for any other URL (LWA,
    Sellers, Sandbox, or anything else) — those pass through completely
    unmodified. Stateless: `filter()` receives a fresh `LogRecord` on every
    call and mutates only that instance, so this is safe under concurrent/
    async use with no shared mutable state and no per-request setup or
    teardown."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    _redact_listings_seller_id(a) if isinstance(a, str | httpx.URL) else a for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (_redact_listings_seller_id(v) if isinstance(v, str | httpx.URL) else v)
                    for k, v in record.args.items()
                }
        if isinstance(record.msg, str):
            record.msg = _redact_listings_seller_id(record.msg)
        return True


_httpx_redaction_installed = False


def _ensure_httpx_seller_id_redaction_installed() -> None:
    """Idempotent: installs `_RedactListingsSellerIdFilter` on the `httpx`
    logger exactly once per process, regardless of how many
    `AmazonSpApiListingsClient` instances are constructed. Deliberately does
    NOT touch `logging.Logger.setLevel` on any logger — this is a permanent,
    always-on filter, not a per-request or ad hoc mutation, so there is
    nothing to race between concurrent requests and nothing to restore
    afterward.

    Deliberately does NOT also attach to the bare `httpcore` logger: Python
    only checks a logger-level filter against the logger a record actually
    originates from, never against an ancestor's filters during
    propagation (verified directly — see the module docstring), and
    `httpcore`'s own tracing always logs through per-component sub-logger
    names (`httpcore.connection`, `httpcore.http11`, ...), never the bare
    `httpcore` name. Installing a filter there would not protect anything
    and would misrepresent what this function actually does."""
    global _httpx_redaction_installed
    if _httpx_redaction_installed:
        return
    target = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactListingsSellerIdFilter) for f in target.filters):
        target.addFilter(_RedactListingsSellerIdFilter())
    _httpx_redaction_installed = True


def _sanitize_provenance_header(value: str | None) -> str | None:
    """Bounds and strips control characters from a header value before it
    reaches `ListingsPageProvenance` (and, later, any log line or eventual
    persistence). Not a documented Amazon constraint — this is defensive
    hygiene applied to attacker-influenced upstream data, independent of
    whether Amazon itself would ever actually send something malformed."""
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_PROVENANCE_HEADER_LENGTH]


class _TransportFailure(Exception):
    """Internal marker for a timeout/connection failure on one HTTP attempt.

    Never raised to callers of this module — the retry loop catches this
    and, once attempts are exhausted, raises the public
    `SpApiRequestFailedError` instead.
    """


class _TransientServerFailure(Exception):
    """Internal marker for a 5xx response on one HTTP attempt. Never raised
    to callers — see `_TransportFailure`."""

    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"transient server failure status={status}")


@dataclass(frozen=True)
class ListingsPageRequest:
    """One page request. `seller_id` must be the stored, OAuth-captured
    `selling_partner_id` — never a value derived from any API response.
    Exactly one `marketplace_id` per request (V1 rule enforced by this being
    a single field, not a list).

    Normalization (the only transformation applied to any field here):
    `seller_id` and `marketplace_id` have surrounding whitespace stripped;
    `page_token` has surrounding whitespace stripped and is treated as
    absent (`None`) if that leaves it empty. This happens exactly once, at
    construction time, so every downstream use (the URL path, the query
    parameters, every retry attempt) reads the same already-normalized
    values — there is no code path that could use a whitespace-padded
    variant in one place and a stripped variant in another. Beyond
    whitespace normalization, `page_token` is forwarded to Amazon
    byte-for-byte unchanged.
    """

    seller_id: str
    marketplace_id: str
    page_token: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "seller_id", (self.seller_id or "").strip())
        object.__setattr__(self, "marketplace_id", (self.marketplace_id or "").strip())
        normalized_token = (self.page_token or "").strip()
        object.__setattr__(self, "page_token", normalized_token or None)


def _validate_request(request: ListingsPageRequest) -> None:
    if not request.seller_id:
        raise SpApiConfigurationError("Amazon seller ID is required to call Listings Items.")
    if not request.marketplace_id:
        raise SpApiConfigurationError("Exactly one marketplace ID is required to call Listings Items.")


class AmazonSpApiListingsClient:
    """`GET /listings/2021-08-01/items/{sellerId}` with an injected seller refresh token."""

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
        user_agent: str = "AmazonSellerIntelligence/12B.3C (Language=Python/3.12)",
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
            raise SpApiConfigurationError("Listings client max_attempts must be at least 1.")
        _ensure_httpx_seller_id_redaction_installed()
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
        return "AmazonSpApiListingsClient()"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint_host(self) -> str:
        return urlparse(self._base_url).netloc

    def _backoff_delay(self, attempt_index: int) -> float:
        """Bounded exponential backoff with full jitter. `attempt_index` is
        1-based (the attempt that just failed). No `Retry-After` handling:
        the pinned contract documents no such header for this operation, on
        either the 200 or the 429 response — see the module docstring."""
        capped = min(self._max_delay_seconds, self._base_delay_seconds * (2 ** (attempt_index - 1)))
        return capped * self._jitter()

    def _headers(self, access_token: str) -> dict[str, str]:
        """Built fresh for every HTTP attempt (not once for the whole
        `fetch_page` call): `x-amz-date` is a per-attempt timestamp, not
        part of the logical request's identity, so it should reflect when
        THIS attempt was actually made rather than when the first attempt
        in a retry sequence started. The access token, URL, and query
        parameters ARE part of the request's identity and are deliberately
        reused verbatim, unchanged, across every retry attempt."""
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
            logger.warning("SP-API listings request timed out operation=%s", SEARCH_LISTINGS_ITEMS)
            raise _TransportFailure("timeout") from None
        except httpx.HTTPError:
            logger.warning("SP-API listings request failed operation=%s", SEARCH_LISTINGS_ITEMS)
            raise _TransportFailure("transport") from None

    async def fetch_page(self, request: ListingsPageRequest) -> ListingsPage:
        """Fetch and parse exactly one page. Never loops on `nextToken`."""
        _validate_request(request)
        token = await self._lwa.fetch_access_token()
        access_token = token.access_token.get_secret_value()
        del token
        # `quote(..., safe="")` percent-encodes the seller ID before it is
        # interpolated into the URL *path* — unlike marketplaceIds/
        # includedData/pageSize/pageToken below, which go through httpx's
        # own `params=` encoding, the path segment is built by this module
        # via plain string interpolation and must be encoded explicitly, or
        # an unexpected character (whitespace already stripped by
        # `ListingsPageRequest.__post_init__`, but e.g. a stray `/` or `?`
        # is not) could otherwise change the URL's structure.
        path = LISTINGS_ITEMS_PATH_TEMPLATE.format(seller_id=quote(request.seller_id, safe=""))
        url = f"{self._base_url}{path}"
        params = {
            "marketplaceIds": request.marketplace_id,
            "includedData": ",".join(APPROVED_INCLUDED_DATA),
            "pageSize": str(LISTINGS_PAGE_SIZE),
        }
        if request.page_token:
            params["pageToken"] = request.page_token

        response: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            headers = self._headers(access_token)
            try:
                response = await self._single_attempt(url, params, headers)
            except _TransportFailure:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise SpApiRequestFailedError("Amazon SP-API listings request could not be completed.") from None

            try:
                self._raise_for_status(response.status_code)
                break
            except SpApiRateLimitedError:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise
            except _TransientServerFailure:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise SpApiRequestFailedError("Amazon SP-API listings request failed.") from None

        del access_token
        assert response is not None  # loop always returns or raises above
        parsed = self._parse_response(response)
        return self._to_page(parsed, response, request, attempt_count=attempt)

    def _raise_for_status(self, status: int) -> None:
        if status == 200:
            return
        if status in {401, 403}:
            logger.warning("SP-API listings authentication failed status=%s", status)
            raise SpApiAuthenticationError("Amazon SP-API listings authentication failed.")
        if status == 429:
            logger.warning("SP-API listings rate-limited status=%s", status)
            raise SpApiRateLimitedError("Amazon SP-API listings rate limit reached.")
        if status >= 500:
            logger.warning("SP-API listings server failure status=%s", status)
            raise _TransientServerFailure(status)
        logger.warning("SP-API listings request rejected status=%s", status)
        raise SpApiInvalidRequestError(f"Amazon SP-API listings request was rejected (status={status}).")

    def _parse_response(self, response: httpx.Response) -> SearchListingsItemsResponse:
        try:
            body = response.json()
        except ValueError:
            raise SpApiParseFailedError("Amazon SP-API listings response was not JSON.") from None
        try:
            return SearchListingsItemsResponse.model_validate(body)
        except ValidationError:
            raise SpApiParseFailedError("Amazon SP-API listings payload was malformed.") from None

    def _to_page(
        self,
        parsed: SearchListingsItemsResponse,
        response: httpx.Response,
        request: ListingsPageRequest,
        *,
        attempt_count: int,
    ) -> ListingsPage:
        next_token = parsed.pagination.next_token if parsed.pagination else None
        return ListingsPage(
            items=parsed.items,
            number_of_results=parsed.number_of_results,
            next_token=next_token,
            # Always the caller's own request, never anything read from
            # `parsed` — an `Item.summaries[].marketplaceId` in the response
            # body must never be able to override the identity this page
            # result reports itself as being scoped to.
            marketplace_id=request.marketplace_id,
            page_token_used=request.page_token,
            provenance=ListingsPageProvenance(
                operation=SEARCH_LISTINGS_ITEMS,
                region=self._region,
                endpoint_host=self.endpoint_host,
                fetched_at=datetime.now(UTC),
                http_status=response.status_code,
                api_model_version=LISTINGS_MODEL_VERSION,
                attempt_count=attempt_count,
                rate_limit=_sanitize_provenance_header(response.headers.get("x-amzn-RateLimit-Limit")),
                request_id=_sanitize_provenance_header(response.headers.get("x-amzn-RequestId")),
            ),
        )

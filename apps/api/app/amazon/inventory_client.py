"""SP-API FBA Inventory v1 read client. One official page at a time.

`GET /fba/inventory/v1/summaries` (`getInventorySummaries`). Pinned
against `amzn/selling-partner-api-models@
cca8d338b6dc56afd3f9fdb822d2125a80ae8090`,
`models/fba-inventory-api-model/fbaInventory.json` — see
`docs/AI_HANDOVER/12B6B_FBA_INVENTORY_INGESTION.md`.

12B.6B scope only. This client:

- fetches and parses exactly one official API page per call;
- never traverses pages itself (no loop over `nextToken` inside this file);
- never acquires an ingestion-run lease, creates an ingestion run, writes
  an inventory row, deactivates anything, or decides observation
  authority;
- never accesses a repository or a database session;
- exposes no HTTP route.

Mirrors `AmazonSpApiListingsClient`'s boundary exactly, adapted for this
operation's own contract:

- **No seller ID in the URL.** Unlike Listings Items
  (`/listings/2021-08-01/items/{sellerId}`), `getInventorySummaries` is a
  pure query-string request (`marketplaceIds`, `granularityType`,
  `granularityId`, `details`, `nextToken`) — there is no path segment
  requiring the seller-ID log-redaction filter Listings needs.
- **No documented `Retry-After` guarantee either way** was found for this
  operation specifically during the 12B.6B audit; this client still reads
  it defensively on a 429 (matching every other client in this codebase)
  and simply yields `None` if absent, exactly like `listings_client.py`'s
  own `_parse_retry_after`.
- **`nextToken` expires 30 seconds after being created** (pinned
  contract, both the reference page and the model file's own parameter
  description). This client neither knows nor cares about that lifetime —
  it always forwards whatever token the caller passes, immediately, in
  the very next call. The 30-second constraint is what makes durable,
  cross-attempt pagination-token persistence architecturally unsound for
  the *ingestion service* (`inventory_ingestion.py`), not something this
  client itself needs to enforce.
- Exactly one marketplace per call (`granularityId`, `granularityType`
  fixed to `"Marketplace"`), matching the pinned contract's own
  `marketplaceIds` `maxItems: 1`.
- No `sellerSkus`/`sellerSku` filter is ever sent — full unfiltered
  traversal per the 12B.6B audit's approved design (a full sweep avoids
  the documented gap where `startDateTime`'s "changed since" filter does
  not reliably detect inbound-quantity changes).

Reuses, rather than duplicates, existing infrastructure: `LwaClient` for
token resolution, `sp_api_base_url()` for host resolution, and the shared
`SpApi*Error` taxonomy in `app.core.exceptions` — identical to
`listings_client.py`.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.inventory_models import GetInventorySummariesResponse, InventoryPage, InventoryPageProvenance
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

GET_INVENTORY_SUMMARIES = "getInventorySummaries"
INVENTORY_SUMMARIES_PATH = "/fba/inventory/v1/summaries"
INVENTORY_MODEL_VERSION = "fba-inventory-api-model/v1"

GRANULARITY_TYPE_MARKETPLACE = "Marketplace"

DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_SECONDS = 0.5
DEFAULT_MAX_DELAY_SECONDS = 8.0

# A provenance header value is attacker-influenced upstream data — see
# `listings_client.py`'s identical constant/reasoning.
_MAX_PROVENANCE_HEADER_LENGTH = 256


def _sanitize_provenance_header(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(ch for ch in value if ch.isprintable()).strip()
    if not cleaned:
        return None
    return cleaned[:_MAX_PROVENANCE_HEADER_LENGTH]


class _TransportFailure(Exception):
    """Internal marker for a timeout/connection failure on one HTTP
    attempt. Never raised to callers — see `listings_client.py`'s
    identical marker."""


class _TransientServerFailure(Exception):
    def __init__(self, status: int) -> None:
        self.status = status
        super().__init__(f"transient server failure status={status}")


def _parse_retry_after(raw: str | None) -> float | None:
    """RFC 7231 `Retry-After`: either delta-seconds or an HTTP-date. Never
    raises. Identical to `listings_client._parse_retry_after`."""
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


@dataclass(frozen=True)
class InventoryPageRequest:
    """One page request. Exactly one `marketplace_id` per request (the
    pinned contract's own `maxItems: 1` on `marketplaceIds`). No
    `seller_skus` filter — full unfiltered traversal is this milestone's
    approved design (see module docstring).

    Normalization (the only transformation applied): `marketplace_id` and
    `page_token` have surrounding whitespace stripped; an empty
    `page_token` after stripping is treated as absent (`None`)."""

    marketplace_id: str
    page_token: str | None = None
    details: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "marketplace_id", (self.marketplace_id or "").strip())
        normalized_token = (self.page_token or "").strip()
        object.__setattr__(self, "page_token", normalized_token or None)


def _validate_request(request: InventoryPageRequest) -> None:
    if not request.marketplace_id:
        raise SpApiConfigurationError("Exactly one marketplace ID is required to call FBA Inventory.")


class AmazonSpApiInventoryClient:
    """`GET /fba/inventory/v1/summaries` with an injected seller refresh
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
        user_agent: str = "AmazonSellerIntelligence/12B.6B (Language=Python/3.12)",
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
            raise SpApiConfigurationError("Inventory client max_attempts must be at least 1.")
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
        return "AmazonSpApiInventoryClient()"

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def endpoint_host(self) -> str:
        return urlparse(self._base_url).netloc

    def _backoff_delay(self, attempt_index: int) -> float:
        capped = min(self._max_delay_seconds, self._base_delay_seconds * (2 ** (attempt_index - 1)))
        return capped * self._jitter()

    def _headers(self, access_token: str) -> dict[str, str]:
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
            logger.warning("SP-API inventory request timed out operation=%s", GET_INVENTORY_SUMMARIES)
            raise _TransportFailure("timeout") from None
        except httpx.HTTPError:
            logger.warning("SP-API inventory request failed operation=%s", GET_INVENTORY_SUMMARIES)
            raise _TransportFailure("transport") from None

    async def fetch_page(self, request: InventoryPageRequest) -> InventoryPage:
        """Fetch and parse exactly one page. Never loops on `nextToken`."""
        _validate_request(request)
        token = await self._lwa.fetch_access_token()
        access_token = token.access_token.get_secret_value()
        del token

        url = f"{self._base_url}{INVENTORY_SUMMARIES_PATH}"
        params: dict[str, str] = {
            "granularityType": GRANULARITY_TYPE_MARKETPLACE,
            "granularityId": request.marketplace_id,
            "marketplaceIds": request.marketplace_id,
            "details": "true" if request.details else "false",
        }
        if request.page_token:
            params["nextToken"] = request.page_token

        response: httpx.Response | None = None
        for attempt in range(1, self._max_attempts + 1):
            headers = self._headers(access_token)
            try:
                response = await self._single_attempt(url, params, headers)
            except _TransportFailure:
                if attempt < self._max_attempts:
                    await self._sleep(self._backoff_delay(attempt))
                    continue
                raise SpApiRequestFailedError("Amazon SP-API inventory request could not be completed.") from None

            try:
                self._raise_for_status(response)
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
                raise SpApiRequestFailedError("Amazon SP-API inventory request failed.") from None

        del access_token
        assert response is not None  # loop always returns or raises above
        parsed = self._parse_response(response)
        return self._to_page(parsed, response, request, attempt_count=attempt)

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status == 200:
            return
        if status in {401, 403}:
            logger.warning("SP-API inventory authentication failed status=%s", status)
            raise SpApiAuthenticationError("Amazon SP-API inventory authentication failed.")
        if status == 429:
            logger.warning("SP-API inventory rate-limited status=%s", status)
            retry_after = _parse_retry_after(response.headers.get("Retry-After"))
            raise SpApiRateLimitedError(
                "Amazon SP-API inventory rate limit reached.", retry_after_seconds=retry_after
            )
        if status >= 500:
            logger.warning("SP-API inventory server failure status=%s", status)
            raise _TransientServerFailure(status)
        logger.warning("SP-API inventory request rejected status=%s", status)
        raise SpApiInvalidRequestError(f"Amazon SP-API inventory request was rejected (status={status}).")

    def _parse_response(self, response: httpx.Response) -> GetInventorySummariesResponse:
        try:
            body = response.json()
        except ValueError:
            raise SpApiParseFailedError("Amazon SP-API inventory response was not JSON.") from None
        try:
            parsed = GetInventorySummariesResponse.model_validate(body)
        except ValidationError:
            raise SpApiParseFailedError("Amazon SP-API inventory payload was malformed.") from None
        if parsed.payload is None:
            raise SpApiParseFailedError("Amazon SP-API inventory response carried no payload.")
        return parsed

    def _to_page(
        self,
        parsed: GetInventorySummariesResponse,
        response: httpx.Response,
        request: InventoryPageRequest,
        *,
        attempt_count: int,
    ) -> InventoryPage:
        assert parsed.payload is not None  # guaranteed by _parse_response
        next_token = parsed.pagination.next_token if parsed.pagination else None
        return InventoryPage(
            granularity=parsed.payload.granularity,
            summaries=parsed.payload.inventory_summaries,
            next_token=next_token,
            # Always the caller's own request, never anything read from
            # `parsed` — see `ListingsPage`'s identical reasoning.
            marketplace_id=request.marketplace_id,
            page_token_used=request.page_token,
            provenance=InventoryPageProvenance(
                operation=GET_INVENTORY_SUMMARIES,
                region=self._region,
                endpoint_host=self.endpoint_host,
                fetched_at=datetime.now(UTC),
                http_status=response.status_code,
                api_model_version=INVENTORY_MODEL_VERSION,
                attempt_count=attempt_count,
                rate_limit=_sanitize_provenance_header(response.headers.get("x-amzn-RateLimit-Limit")),
                request_id=_sanitize_provenance_header(response.headers.get("x-amzn-RequestId")),
            ),
        )

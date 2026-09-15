"""Typed, injectable Amazon Ads REST client boundary. 12C read-only
foundation — no method here ever issues a create/update/delete request;
the `AmazonAdsApiClient` protocol below defines only read operations plus
the async-report lifecycle (create/poll/download), matching this
product's committed read-only scope.

No live request is made by this module during this iteration: every
caller in this codebase (tests, and the inert `ads_worker.py`) injects
`MockAmazonAdsApiClient`. `HttpAmazonAdsApiClient` exists as the
documented, reviewed target for the implementation pass that follows
Ads API approval — it is never constructed by any wired code path yet.

PR B1 — Sponsored Products hierarchy contracts. Every endpoint-specific
fact below (path, media type, request/response fields) is sourced from
`docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md`,
the sole Amazon Ads contract authority — see that document's own
confidence labels for exactly what is officially verified. This module
never asserts a fact the blueprint does not establish; where the
blueprint leaves something unconfirmed (see below), the code says so
inline rather than silently generalizing from a sibling endpoint.

- Headers: `Amazon-Advertising-API-ClientId`, `Authorization: Bearer
  <token>`, `Amazon-Advertising-API-Scope: <profileId>` (blueprint §6.6,
  §10.2-10.4). The blueprint records an unresolved official conflict
  over the ClientId header's own name (§7.1, `Amazon-Ads-ClientId` vs
  `Amazon-Advertising-API-ClientId`) — this client sends the latter,
  matching every SP v3/Reporting v3/Profiles sample and EWise's own
  live-confirmed usage, and fails closed on 401 rather than guessing a
  second header (blueprint §13.3).
- Regional hosts: NA `https://advertising-api.amazon.com`, EU
  `https://advertising-api-eu.amazon.com`, FE
  `https://advertising-api-fe.amazon.com` (blueprint §3).
- Profiles: `GET /v2/profiles` (list, unscoped by profile header;
  blueprint §10.1).
- Reporting v3: `POST /reporting/reports` (create), `GET
  /reporting/reports/{reportId}` (poll) — both profile-scoped (blueprint
  §9). Untouched by PR B1.
- Sponsored Products v3 entity-list endpoints (blueprint §10.2-10.4,
  §11.5-11.6), all `POST`, all profile-scoped:

  | Entity | Path | Media type | Envelope key |
  |---|---|---|---|
  | Campaign | `/sp/campaigns/list` | `application/vnd.spcampaign.v3+json` | `campaigns` — **live-confirmed** (2026-09-13) |
  | Ad group | `/sp/adGroups/list` | `application/vnd.spAdGroup.v3+json` — **officially documented** (§10.3) | `adGroups` — **officially documented** (§10.3) |
  | Product ad | `/sp/productAds/list` | `application/vnd.spProductAd.v3+json` — **officially documented** (§10.4) | `productAds` — **UNCONFIRMED**: §10.4 documents the response *item* schema but never states the envelope key; inferred from the operation name (`ListSponsoredProductsProductAds`) and the confirmed campaigns/ad-groups pattern, not itself extracted from an official page |
  | Keyword | `/sp/keywords/list` | `application/vnd.spKeyword.v3+json` — **officially documented** (§11.5) | `keywords` — **UNCONFIRMED**, same inference basis; §11.5 does not document a response schema at all |
  | Product target | `/sp/targets/list` | `application/vnd.spTargetingClause.v3+json` — **officially documented** (§11.6) | `targetingClauses` — **UNCONFIRMED**, same basis (operation name `ListSponsoredProductsTargetingClauses`); §11.6 documents no response schema either |

  Only the campaign media type/envelope is live-confirmed; the other
  four media types are now taken directly from the blueprint's own
  extracted OpenAPI schema (a materially stronger basis than the prior
  pass's `application/json` placeholder), but their envelope keys and
  response item field names remain fixture-tested only — see
  `docs/AI_HANDOVER/24_AMAZON_ADS_SPONSORED_PRODUCTS_HIERARCHY_CONTRACTS.md`
  for the full per-endpoint evidence matrix and what still needs a
  supervised live check before any of this is wired to a real call.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

import httpx
from pydantic import BaseModel, SecretStr, ValidationError

from app.amazon.ads_models import (
    AdsAdGroupResponse,
    AdsCampaignResponse,
    AdsKeywordResponse,
    AdsProductAdResponse,
    AdsProductTargetResponse,
    AdsProfileResponse,
    AdsReportRequestConfiguration,
    AdsReportStatusResponse,
    CAMPAIGN_STATES,
    SIBLING_ENTITY_STATES,
)
from app.core.exceptions import (
    AdsApiAuthenticationError,
    AdsApiDuplicateReportError,
    AdsApiInvalidRequestError,
    AdsApiParseFailedError,
    AdsApiRateLimitedError,
    AdsApiRequestFailedError,
    AdsConfigurationError,
    AdsReportOversizedError,
)

logger = logging.getLogger(__name__)

REGION_BASE_URLS = {
    "NA": "https://advertising-api.amazon.com",
    "EU": "https://advertising-api-eu.amazon.com",
    "FE": "https://advertising-api-fe.amazon.com",
}

HEADER_CLIENT_ID = "Amazon-Advertising-API-ClientId"
HEADER_SCOPE = "Amazon-Advertising-API-Scope"

_SECRET_HEADER_NAMES = frozenset({"authorization", HEADER_CLIENT_ID.lower()})


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Safe-for-logging header copy — never logs a bearer token or client id."""
    return {k: ("[redacted]" if k.lower() in _SECRET_HEADER_NAMES else v) for k, v in headers.items()}


def resolve_region_base_url(region: str) -> str:
    key = (region or "").strip().upper()
    base = REGION_BASE_URLS.get(key)
    if not base:
        raise AdsApiInvalidRequestError(f"Unsupported Amazon Ads region: {region!r}")
    return base


@dataclass(frozen=True)
class AdsRequestContext:
    """Per-request identity/scope. `correlation_id` is this application's
    own request-correlation value (never Amazon's), logged alongside every
    Ads API call for support/debugging without ever logging tokens."""

    access_token: SecretStr
    client_id: str
    region: str
    profile_id: str | None
    correlation_id: str


@dataclass
class AdsPage:
    items: list[dict]
    next_token: str | None = None


T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class EntityParseResult(Generic[T]):
    """Result of independently validating every item in one page of a
    Sponsored Products v3 entity-list response — the reusable, offline
    parsing behavior PR B1 was asked to build for PR B2's orchestration
    to consume. Never raised for an individual bad item; see
    `parse_entity_list_envelope`'s own docstring for the one thing that
    *does* raise (a genuinely malformed top-level envelope)."""

    items: list[T]
    total_items: int
    accepted_items: int
    schema_rejected_items: int
    unsupported_state_items: int
    next_token: str | None

    @property
    def is_contract_mismatch(self) -> bool:
        """True for a nonempty response where nothing was accepted — must
        never be treated as an ordinary empty page by a caller. Mirrors
        `ads_report_service.py`'s identical `report_row_contract_mismatch`
        guard for Reporting v3 rows; this is the entity-list analogue."""
        return self.total_items > 0 and self.accepted_items == 0


def _parse_next_token(value: object, *, token_present: bool) -> str | None:
    """Validates `payload.get("nextToken")` per `parse_entity_list_envelope`'s
    own pagination-token contract table. `token_present` distinguishes a
    genuinely absent key from a present key whose value happens to be
    `None` — both currently resolve to "pagination complete", but they
    are evaluated as two named, disclosed cases (see the docstring
    table), not one silently-merged default."""
    if not token_present:
        return None
    if value is None:
        # Production-observed (live-confirmed POST /sp/campaigns/list
        # terminal page), not stated by document 23's own prose — see
        # parse_entity_list_envelope's docstring table for the full
        # disclosure of this decision's basis.
        return None
    if not isinstance(value, str):
        raise AdsApiParseFailedError("Amazon Ads API entity-list response's nextToken field had an unsupported type.")
    if not value.strip():
        raise AdsApiParseFailedError("Amazon Ads API entity-list response's nextToken field was blank.")
    return value


def parse_entity_list_envelope(
    payload: dict,
    *,
    response_key: str,
    model: type[T],
    allowed_states: frozenset[str] | None = None,
) -> EntityParseResult[T]:
    """Offline, reusable parsing for one page of a Sponsored Products v3
    entity-list response — no HTTP involved, callable directly against a
    synthetic fixture dict (see the PR B1 contract tests) or a live
    `httpx` response body already decoded to JSON.

    Each item is validated INDEPENDENTLY: one schema-invalid or
    unsupported-state item never discards its valid siblings, and the
    result reports total/accepted/schema-rejected/unsupported-state
    counts rather than an opaque yes/no. `unsupported_state_items` is
    counted only when `allowed_states` is given and a structurally valid
    item's `state` falls outside it — distinct from `schema_rejected_items`
    (the item failed Pydantic validation entirely, e.g. a missing
    required field or a non-losslessly-convertible id).

    Fail-closed envelope contract (second review — a missing or null
    entity key must NEVER be silently read as "zero entities", since the
    envelope key itself is an unconfirmed inference for three of the
    five endpoints; if that inference is wrong, silently returning an
    empty page would hide the mismatch instead of surfacing it):

    | `payload[response_key]` | Result |
    |---|---|
    | key absent | raises `AdsApiParseFailedError` |
    | `null` | raises `AdsApiParseFailedError` |
    | present, not a JSON array | raises `AdsApiParseFailedError` |
    | `[]` | valid, genuinely empty page |

    Every raised message is a fixed, sanitized string naming only
    `response_key` (a constant this module already knows, never
    attacker- or seller-controlled) — never the response body, never any
    entity data.

    Pagination-token contract (also second review):

    | `payload["nextToken"]` | Result |
    |---|---|
    | absent | pagination complete (`next_token=None`) |
    | non-empty string | returned exactly as received — never trimmed or transformed, since no official source states this opaque token has trim-safe whitespace |
    | `null` | pagination complete (`next_token=None`) — **not** stated by document 23's own prose (which only ever says "follow nextToken until absent"); this is a deliberate, disclosed extension based on this codebase's own live-confirmed `POST /sp/campaigns/list` response, whose observed terminal page sends `"nextToken": null` rather than omitting the key. Labeled **Production-observed but not contract authority** per the blueprint's own §0.1 confidence tier — never asserted as something document 23 itself permits |
    | blank/whitespace-only string | raises `AdsApiParseFailedError` — cannot function as a continuation token |
    | any other type (number, bool, array, object) | raises `AdsApiParseFailedError` |
    """
    if response_key not in payload:
        raise AdsApiParseFailedError(
            f"Amazon Ads API entity-list response was missing the {response_key!r} field."
        )
    raw_items = payload[response_key]
    if raw_items is None:
        raise AdsApiParseFailedError(
            f"Amazon Ads API entity-list response's {response_key!r} field was null, not an array."
        )
    if not isinstance(raw_items, list):
        raise AdsApiParseFailedError(
            f"Amazon Ads API entity-list response's {response_key!r} field was not a JSON array."
        )

    accepted: list[T] = []
    schema_rejected = 0
    unsupported_state = 0
    for raw_item in raw_items:
        try:
            parsed = model.model_validate(raw_item)
        except ValidationError:
            schema_rejected += 1
            continue
        if allowed_states is not None and getattr(parsed, "state", None) not in allowed_states:
            unsupported_state += 1
            continue
        accepted.append(parsed)

    next_token = _parse_next_token(payload.get("nextToken", None), token_present="nextToken" in payload)

    return EntityParseResult(
        items=accepted,
        total_items=len(raw_items),
        accepted_items=len(accepted),
        schema_rejected_items=schema_rejected,
        unsupported_state_items=unsupported_state,
        next_token=next_token,
    )


class AmazonAdsApiClient(Protocol):
    """Injectable Ads REST boundary. Every method is profile-scoped except
    `list_profiles` (profiles are discovered before any profile is
    selected). No method mutates Amazon state."""

    async def list_profiles(self, ctx: AdsRequestContext) -> list[AdsProfileResponse]: ...

    async def list_campaigns(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> EntityParseResult[AdsCampaignResponse]: ...

    async def list_ad_groups(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> EntityParseResult[AdsAdGroupResponse]: ...

    async def list_product_ads(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> EntityParseResult[AdsProductAdResponse]: ...

    async def list_keywords(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> EntityParseResult[AdsKeywordResponse]: ...

    async def list_product_targets(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> EntityParseResult[AdsProductTargetResponse]: ...

    async def create_report(
        self, ctx: AdsRequestContext, configuration: AdsReportRequestConfiguration
    ) -> AdsReportStatusResponse: ...

    async def get_report_status(self, ctx: AdsRequestContext, report_id: str) -> AdsReportStatusResponse: ...

    async def download_report(self, ctx: AdsRequestContext, url: str, *, max_bytes: int) -> bytes:
        """Return decompressed report bytes. `url` is Amazon's own
        pre-signed download URL (never carries this app's own auth
        headers — see `HttpAmazonAdsApiClient.download_report`)."""
        ...


def decompress_gzip_json(raw: bytes, *, max_bytes: int) -> bytes:
    """Decompress a `GZIP_JSON` report body, bounded against a zip-bomb
    style payload — refuses to keep inflating past `max_bytes` rather
    than trusting the compressed size alone."""
    # wbits = MAX_WBITS | 16 selects gzip-header/trailer decoding (plain
    # zlib.decompressobj() alone expects a zlib, not gzip, stream).
    decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)
    out = bytearray()
    chunk_size = 65536
    for offset in range(0, len(raw), chunk_size):
        out.extend(decompressor.decompress(raw[offset : offset + chunk_size]))
        if len(out) > max_bytes:
            raise AdsReportOversizedError("Amazon Ads report exceeded the allowed decompressed size.")
    out.extend(decompressor.flush())
    if len(out) > max_bytes:
        raise AdsReportOversizedError("Amazon Ads report exceeded the allowed decompressed size.")
    return bytes(out)


@dataclass
class MockAmazonAdsApiClient:
    """Test/dev double. Configure canned responses per method; raises
    `AssertionError` if a method is called with no canned response
    configured, so a test never silently proceeds on default/empty data
    when it meant to assert a specific call happened. Records every call
    (`self.calls`) for assertions like "no live request escaped the mock
    boundary" (this class never performs I/O, so that assertion is
    trivially true for this double, but the recorded calls let a test
    assert exactly which methods and how many times)."""

    profiles: list[AdsProfileResponse] = field(default_factory=list)
    campaigns_pages: list[tuple[list[AdsCampaignResponse], str | None]] = field(default_factory=list)
    ad_groups_pages: list[tuple[list[AdsAdGroupResponse], str | None]] = field(default_factory=list)
    product_ads_pages: list[tuple[list[AdsProductAdResponse], str | None]] = field(default_factory=list)
    keywords_pages: list[tuple[list[AdsKeywordResponse], str | None]] = field(default_factory=list)
    product_targets_pages: list[tuple[list[AdsProductTargetResponse], str | None]] = field(default_factory=list)
    report_status_sequence: list[AdsReportStatusResponse] = field(default_factory=list)
    report_bodies: dict[str, bytes] = field(default_factory=dict)
    raise_on: dict[str, Exception] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)

    def _maybe_raise(self, name: str) -> None:
        exc = self.raise_on.get(name)
        if exc is not None:
            raise exc

    async def list_profiles(self, ctx: AdsRequestContext) -> list[AdsProfileResponse]:
        self.calls.append("list_profiles")
        self._maybe_raise("list_profiles")
        return list(self.profiles)

    async def _paged(
        self, name: str, pages: list[tuple[list, str | None]], *, next_token: str | None
    ) -> EntityParseResult:
        self.calls.append(name)
        self._maybe_raise(name)
        if not pages:
            items, page_next_token = [], None
        elif next_token is None:
            items, page_next_token = pages[0]
        else:
            # `next_token` is the token the PREVIOUS page handed back —
            # find that page and return the one immediately after it,
            # not the page whose own token happens to equal it (that
            # would return the same page again instead of advancing).
            previous_index = next((i for i, (_, t) in enumerate(pages) if t == next_token), None)
            if previous_index is None or previous_index + 1 >= len(pages):
                items, page_next_token = [], None
            else:
                items, page_next_token = pages[previous_index + 1]
        # The mock hands back already-accepted items — a test that wants
        # to exercise rejection/contract-mismatch accounting constructs
        # an EntityParseResult directly and feeds it through
        # parse_entity_list_envelope's own tests instead (that function
        # needs no client at all).
        return EntityParseResult(
            items=list(items),
            total_items=len(items),
            accepted_items=len(items),
            schema_rejected_items=0,
            unsupported_state_items=0,
            next_token=page_next_token,
        )

    async def list_campaigns(self, ctx, *, next_token=None, page_size=100):
        return await self._paged("list_campaigns", self.campaigns_pages, next_token=next_token)

    async def list_ad_groups(self, ctx, *, next_token=None, page_size=100):
        return await self._paged("list_ad_groups", self.ad_groups_pages, next_token=next_token)

    async def list_product_ads(self, ctx, *, next_token=None, page_size=100):
        return await self._paged("list_product_ads", self.product_ads_pages, next_token=next_token)

    async def list_keywords(self, ctx, *, next_token=None, page_size=100):
        return await self._paged("list_keywords", self.keywords_pages, next_token=next_token)

    async def list_product_targets(self, ctx, *, next_token=None, page_size=100):
        return await self._paged("list_product_targets", self.product_targets_pages, next_token=next_token)

    async def create_report(self, ctx, configuration):
        self.calls.append("create_report")
        self._maybe_raise("create_report")
        return self.report_status_sequence[0]

    async def get_report_status(self, ctx, report_id):
        self.calls.append("get_report_status")
        self._maybe_raise("get_report_status")
        call_count = self.calls.count("get_report_status")
        index = min(call_count - 1, len(self.report_status_sequence) - 1)
        return self.report_status_sequence[max(index, 0)]

    async def download_report(self, ctx, url, *, max_bytes):
        self.calls.append("download_report")
        self._maybe_raise("download_report")
        body = self.report_bodies.get(url, b"")
        if len(body) > max_bytes:
            raise AdsReportOversizedError("Amazon Ads report exceeded the allowed size.")
        return body


class HttpAmazonAdsApiClient:
    """Real Amazon Ads REST client. Never constructed by any wired
    runtime path this iteration (see module docstring) — exercised only
    by tests that inject a fake `httpx` transport, never a live socket."""

    def __init__(self, *, transport: httpx.BaseTransport | None = None, timeout_seconds: float = 30) -> None:
        self._transport = transport
        self._timeout = timeout_seconds

    def __repr__(self) -> str:
        return "HttpAmazonAdsApiClient()"

    def _headers(self, ctx: AdsRequestContext, *, with_scope: bool) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {ctx.access_token.get_secret_value()}",
            HEADER_CLIENT_ID: ctx.client_id,
            "Content-Type": "application/json",
            "X-Correlation-Id": ctx.correlation_id,
        }
        if with_scope and ctx.profile_id:
            headers[HEADER_SCOPE] = ctx.profile_id
        return headers

    def _raise_for_status(self, response: httpx.Response, *, treat_425_as_duplicate_report: bool = False) -> None:
        status = response.status_code
        if status < 300:
            return
        if status in (401, 403):
            raise AdsApiAuthenticationError("Amazon Ads API authentication failed.")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            retry_seconds = float(retry_after) if retry_after and retry_after.strip().isdigit() else None
            raise AdsApiRateLimitedError("Amazon Ads API rate limit reached.", retry_after_seconds=retry_seconds)
        if status == 425 and treat_425_as_duplicate_report:
            # Amazon's documented duplicate/in-flight-report response,
            # scoped deliberately to the ONE operation this is actually
            # documented for (POST /reporting/reports — see
            # AdsApiDuplicateReportError's docstring). `treat_425_as_
            # duplicate_report` must be passed explicitly by that one
            # call site; every other operation (entity lists, profiles,
            # report-status polling, download) falls through to the
            # generic 4xx branch below for a 425, since Amazon's own
            # documentation never states this interpretation applies
            # there — inferring it would be exactly the kind of
            # unverified generalization this codebase has been burned by
            # twice already (campaign media type, reporting body shape).
            #
            # The 425 response BODY schema itself is not documented
            # anywhere consulted — this defensively checks for the one
            # field name Amazon's own documented 200 create/status
            # response is confirmed to use (`reportId`), treating a
            # well-formed, non-blank value as a cautiously usable
            # identifier — never inventing one when absent, and never
            # accepting a whitespace-only or non-string value.
            # response.json() is safe to call here: httpx has already
            # fully read the body for this non-streaming request.
            existing_report_id: str | None = None
            try:
                body = response.json()
            except ValueError:
                body = None
            if isinstance(body, dict):
                candidate = body.get("reportId")
                if isinstance(candidate, str) and candidate.strip():
                    existing_report_id = candidate.strip()
            retry_after = response.headers.get("Retry-After")
            retry_seconds = float(retry_after) if retry_after and retry_after.strip().isdigit() else None
            raise AdsApiDuplicateReportError(
                "Amazon Ads API reported a duplicate/in-flight report request (HTTP 425).",
                existing_report_id=existing_report_id,
                retry_after_seconds=retry_seconds,
            )
        if 400 <= status < 500:
            raise AdsApiInvalidRequestError("Amazon Ads API rejected the request.")
        raise AdsApiRequestFailedError("Amazon Ads API request failed.")

    async def _request_json(
        self,
        ctx: AdsRequestContext,
        method: str,
        path: str,
        *,
        with_scope: bool,
        json_body: dict | None = None,
        media_type: str | None = None,
        treat_425_as_duplicate_report: bool = False,
    ) -> dict:
        base = resolve_region_base_url(ctx.region)
        headers = self._headers(ctx, with_scope=with_scope)
        if media_type:
            headers["Content-Type"] = media_type
            headers["Accept"] = media_type
        logger.info(
            "ads api request method=%s path=%s correlation_id=%s headers=%s",
            method,
            path,
            ctx.correlation_id,
            redact_headers(headers),
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.request(method, f"{base}{path}", headers=headers, json=json_body)
        except httpx.TimeoutException:
            raise AdsApiRequestFailedError("Amazon Ads API request timed out.") from None
        except httpx.HTTPError:
            raise AdsApiRequestFailedError("Could not reach the Amazon Ads API.") from None
        self._raise_for_status(response, treat_425_as_duplicate_report=treat_425_as_duplicate_report)
        try:
            payload = response.json()
        except ValueError:
            raise AdsApiParseFailedError("Amazon Ads API returned a non-JSON response.") from None
        if not isinstance(payload, dict):
            raise AdsApiParseFailedError("Amazon Ads API response was malformed.")
        return payload

    async def list_profiles(self, ctx: AdsRequestContext) -> list[AdsProfileResponse]:
        base = resolve_region_base_url(ctx.region)
        headers = self._headers(ctx, with_scope=False)
        try:
            async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as client:
                response = await client.get(f"{base}/v2/profiles", headers=headers)
        except httpx.TimeoutException:
            raise AdsApiRequestFailedError("Amazon Ads API request timed out.") from None
        except httpx.HTTPError:
            raise AdsApiRequestFailedError("Could not reach the Amazon Ads API.") from None
        self._raise_for_status(response)
        try:
            payload = response.json()
        except ValueError:
            raise AdsApiParseFailedError("Amazon Ads API returned a non-JSON response.") from None
        if not isinstance(payload, list):
            raise AdsApiParseFailedError("Amazon Ads API profiles response was malformed.")
        try:
            return [AdsProfileResponse.model_validate(item) for item in payload]
        except ValidationError:
            raise AdsApiParseFailedError("Amazon Ads API profiles response was malformed.") from None

    async def _list_entities(
        self, ctx, path, model, *, media_type, response_key, next_token, page_size, allowed_states
    ) -> EntityParseResult:
        # Rejects an out-of-range page_size rather than silently
        # clamping it (second review): silent clamping could paper over
        # a caller-side configuration error (e.g. Settings.
        # ads_entity_list_page_size misconfigured, or an integer
        # overflow/typo upstream) instead of surfacing it. The bound
        # itself (1-1000) mirrors ads_entity_list_page_size's own Field
        # constraint — see that setting's docstring for why 1000: SP v3's
        # own exact maxResults ceiling is "Not documented" (blueprint
        # §13.4), so this is a conservative number borrowed from Ads API
        # v1's sibling SPQueryCampaign operation, not an SP v3 fact. This
        # client never reads Settings itself — page_size is always an
        # explicit argument from the caller (PR B2's orchestration is
        # expected to read ads_entity_list_page_size and pass it
        # through).
        if not isinstance(page_size, int) or isinstance(page_size, bool) or not (1 <= page_size <= 1000):
            raise ValueError(f"page_size must be an integer between 1 and 1000, got {page_size!r}.")
        payload = await self._request_json(
            ctx,
            "POST",
            path,
            with_scope=True,
            media_type=media_type,
            json_body={"maxResults": page_size, **({"nextToken": next_token} if next_token else {})},
        )
        return parse_entity_list_envelope(
            payload, response_key=response_key, model=model, allowed_states=allowed_states
        )

    async def list_campaigns(self, ctx, *, next_token=None, page_size=100) -> EntityParseResult[AdsCampaignResponse]:
        # CONFIRMED against a real production POST /sp/campaigns/list
        # response on 2026-09-13: requires this exact versioned
        # Content-Type/Accept media type (a generic application/json
        # request is rejected with 415), and the response envelope key
        # is "campaigns", not the previously-assumed "items". See
        # docs/AI_HANDOVER/23_..._BLUEPRINT.md §10.2.
        return await self._list_entities(
            ctx,
            "/sp/campaigns/list",
            AdsCampaignResponse,
            media_type="application/vnd.spcampaign.v3+json",
            response_key="campaigns",
            next_token=next_token,
            page_size=page_size,
            allowed_states=CAMPAIGN_STATES,
        )

    async def list_ad_groups(self, ctx, *, next_token=None, page_size=100) -> EntityParseResult[AdsAdGroupResponse]:
        # Media type and envelope key ("adGroups") now taken directly
        # from the blueprint's officially documented SP v3 OpenAPI
        # schema (§10.3) — a materially stronger basis than the prior
        # generic application/json placeholder, but NOT yet independently
        # live-verified the way list_campaigns was on 2026-09-13. Do not
        # wire this to a live path until it is.
        return await self._list_entities(
            ctx, "/sp/adGroups/list", AdsAdGroupResponse,
            media_type="application/vnd.spAdGroup.v3+json", response_key="adGroups",
            next_token=next_token, page_size=page_size, allowed_states=SIBLING_ENTITY_STATES,
        )

    async def list_product_ads(self, ctx, *, next_token=None, page_size=100) -> EntityParseResult[AdsProductAdResponse]:
        # Media type is officially documented (blueprint §10.4). The
        # envelope key ("productAds") is UNCONFIRMED — §10.4 documents
        # the response item schema but never states the envelope key;
        # inferred from the confirmed campaigns/adGroups pattern and the
        # operation name (ListSponsoredProductsProductAds). See the
        # module docstring's evidence table.
        return await self._list_entities(
            ctx, "/sp/productAds/list", AdsProductAdResponse,
            media_type="application/vnd.spProductAd.v3+json", response_key="productAds",
            next_token=next_token, page_size=page_size, allowed_states=SIBLING_ENTITY_STATES,
        )

    async def list_keywords(self, ctx, *, next_token=None, page_size=100) -> EntityParseResult[AdsKeywordResponse]:
        # Media type is officially documented (blueprint §11.5). §11.5
        # documents no response schema at all — both the envelope key
        # ("keywords") and AdsKeywordResponse's own field names are
        # UNCONFIRMED. See the module docstring's evidence table.
        return await self._list_entities(
            ctx, "/sp/keywords/list", AdsKeywordResponse,
            media_type="application/vnd.spKeyword.v3+json", response_key="keywords",
            next_token=next_token, page_size=page_size, allowed_states=SIBLING_ENTITY_STATES,
        )

    async def list_product_targets(self, ctx, *, next_token=None, page_size=100) -> EntityParseResult[AdsProductTargetResponse]:
        # Media type is officially documented (blueprint §11.6). Same
        # UNCONFIRMED envelope-key/response-schema caveat as list_keywords
        # — §11.6 documents no response schema either. Envelope key
        # inferred as "targetingClauses" from the operation name
        # (ListSponsoredProductsTargetingClauses).
        return await self._list_entities(
            ctx, "/sp/targets/list", AdsProductTargetResponse,
            media_type="application/vnd.spTargetingClause.v3+json", response_key="targetingClauses",
            next_token=next_token, page_size=page_size, allowed_states=SIBLING_ENTITY_STATES,
        )

    async def create_report(self, ctx: AdsRequestContext, configuration: AdsReportRequestConfiguration) -> AdsReportStatusResponse:
        # Media type per the Reporting v3 get-started guide (see
        # docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md
        # §9). Previously sent generic application/json — that succeeded
        # live twice (PR #33/#34) despite not matching the documented
        # contract; this brings the request in line with the documented
        # media type. Not yet independently live-verified with this
        # exact header — flagged for the next authorized live check.
        payload = await self._request_json(
            ctx,
            "POST",
            "/reporting/reports",
            with_scope=True,
            media_type="application/vnd.createasyncreportrequest.v3+json",
            json_body=configuration.model_dump(by_alias=True, mode="json"),
            # The 425-duplicate-report interpretation applies ONLY to
            # this operation — see _raise_for_status's own comment.
            treat_425_as_duplicate_report=True,
        )
        try:
            return AdsReportStatusResponse.model_validate(payload)
        except ValidationError:
            raise AdsApiParseFailedError("Amazon Ads report-creation response was malformed.") from None

    async def get_report_status(self, ctx: AdsRequestContext, report_id: str) -> AdsReportStatusResponse:
        payload = await self._request_json(ctx, "GET", f"/reporting/reports/{report_id}", with_scope=True)
        try:
            return AdsReportStatusResponse.model_validate(payload)
        except ValidationError:
            raise AdsApiParseFailedError("Amazon Ads report-status response was malformed.") from None

    async def download_report(self, ctx: AdsRequestContext, url: str, *, max_bytes: int) -> bytes:
        # Amazon's pre-signed download URL carries its own auth (query
        # signature) — never attach this app's Bearer token/client id to
        # this request.
        #
        # Download-host validation, honestly scoped: Amazon's Reporting
        # v3 docs do not publish a fixed allowlist of hostnames a
        # presigned download URL may resolve to, mirroring
        # `app.amazon.reports_client`'s identical situation for SP-API's
        # own presigned report-document URLs (see that module's
        # docstring). This enforces the two properties that are
        # verifiable and safe regardless of the exact host: the URL must
        # be `https`, and the download never follows a redirect — a
        # narrower, more conservative guarantee than "matches an
        # allowlisted Amazon domain," stated honestly as such rather than
        # inventing an unverified hostname suffix.
        if not url.lower().startswith("https://"):
            raise AdsApiInvalidRequestError("Refusing to download an Amazon Ads report over a non-HTTPS URL.")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport, follow_redirects=False
            ) as client:
                async with client.stream("GET", url) as response:
                    self._raise_for_status(response)
                    content_length = response.headers.get("Content-Length")
                    if content_length and content_length.strip().isdigit() and int(content_length) > max_bytes:
                        raise AdsReportOversizedError("Amazon Ads report exceeded the allowed size.")
                    chunks = bytearray()
                    async for chunk in response.aiter_bytes():
                        chunks.extend(chunk)
                        if len(chunks) > max_bytes:
                            raise AdsReportOversizedError("Amazon Ads report exceeded the allowed size.")
                    raw = bytes(chunks)
        except httpx.TimeoutException:
            raise AdsApiRequestFailedError("Amazon Ads report download timed out.") from None
        except httpx.HTTPError:
            raise AdsApiRequestFailedError("Could not download the Amazon Ads report.") from None
        return decompress_gzip_json(raw, max_bytes=max_bytes)


# --------------------------------------------------------------------
# Backend selection — `Settings.ads_api_backend`. This is the single
# choke point every caller (the OAuth connection service, the report
# service, the inert worker) goes through to obtain an
# `AmazonAdsApiClient`; none of them ever construct
# `MockAmazonAdsApiClient`/`HttpAmazonAdsApiClient` directly against a
# default. Three values, no silent fallback between them:
#
# - "disabled" (the default — matches every other Amazon credential
#   family in this codebase, e.g. `AMAZON_SECRET_BACKEND`'s own
#   "development" default and fail-closed "production" selection):
#   `DisabledAmazonAdsApiClient` — every method raises
#   `AdsConfigurationError` synchronously, before any network I/O could
#   even be attempted. Authorization/profile-discovery/report-sync all
#   refuse clearly and identically to "Amazon Ads is not configured."
# - "mock": `MockAmazonAdsApiClient` — tests and local development only.
#   Never selected by default; an operator must explicitly set
#   `ADS_API_BACKEND=mock`.
# - "http": `HttpAmazonAdsApiClient` — the real, live client. Only
#   meaningful once Amazon has approved the Partner application and
#   `ADS_LWA_CLIENT_ID`/`ADS_LWA_CLIENT_SECRET`/`ADS_OAUTH_REDIRECT_URI`
#   are configured (checked separately by
#   `AmazonAdsConnectionService.is_configured`/`_require_configured` —
#   the client backend and the OAuth-credential check are independent
#   gates, both must pass before a live call can ever happen).
#
# An unrecognized value is a configuration error (raised), never
# treated as any of the three above — this is what makes "never falls
# back to mock" true even under a typo'd environment variable.
ADS_API_BACKEND_DISABLED = "disabled"
ADS_API_BACKEND_MOCK = "mock"
ADS_API_BACKEND_HTTP = "http"
_ADS_API_BACKENDS = frozenset({ADS_API_BACKEND_DISABLED, ADS_API_BACKEND_MOCK, ADS_API_BACKEND_HTTP})

_DISABLED_MESSAGE = (
    "Amazon Ads is not configured (ADS_API_BACKEND=disabled). "
    "Authorization, profile discovery, and report synchronization are unavailable."
)


class DisabledAmazonAdsApiClient:
    """The fail-closed default. Every method raises `AdsConfigurationError`
    immediately — no `httpx` client is ever constructed, no host is ever
    resolved, no socket is ever opened. This is what makes "disabled"
    structurally incapable of a live call, rather than merely
    unconfigured-and-hoping nothing calls it."""

    def __repr__(self) -> str:
        return "DisabledAmazonAdsApiClient()"

    async def list_profiles(self, ctx: AdsRequestContext) -> list:
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_campaigns(self, ctx, *, next_token=None, page_size=100):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_ad_groups(self, ctx, *, next_token=None, page_size=100):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_product_ads(self, ctx, *, next_token=None, page_size=100):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_keywords(self, ctx, *, next_token=None, page_size=100):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_product_targets(self, ctx, *, next_token=None, page_size=100):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def create_report(self, ctx, configuration):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def get_report_status(self, ctx, report_id):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def download_report(self, ctx, url, *, max_bytes):
        raise AdsConfigurationError(_DISABLED_MESSAGE)


def resolve_ads_api_backend(settings) -> str:  # noqa: ANN001 - app.core.config.Settings, avoiding an import cycle in the type position
    """Normalize `Settings.ads_api_backend`. Default is "disabled" —
    mirrors `resolve_amazon_secret_backend`'s own "safe unless
    deliberately opted in" convention."""
    return (getattr(settings, "ads_api_backend", "") or ADS_API_BACKEND_DISABLED).strip().lower()


def build_amazon_ads_api_client(settings=None) -> "AmazonAdsApiClient":  # noqa: ANN001
    """The single factory every caller uses to obtain an Ads client.
    Raises `AdsConfigurationError` for anything other than the three
    recognized backend values — never silently falls back to mock or to
    disabled for an unrecognized/typo'd value."""
    if settings is None:
        from app.core.config import get_settings

        settings = get_settings()
    backend = resolve_ads_api_backend(settings)
    if backend == ADS_API_BACKEND_DISABLED:
        return DisabledAmazonAdsApiClient()
    if backend == ADS_API_BACKEND_MOCK:
        return MockAmazonAdsApiClient()
    if backend == ADS_API_BACKEND_HTTP:
        return HttpAmazonAdsApiClient(timeout_seconds=getattr(settings, "ads_api_timeout_seconds", 30))
    raise AdsConfigurationError(
        f"Unknown ADS_API_BACKEND value: {backend!r}. Must be one of {sorted(_ADS_API_BACKENDS)}."
    )

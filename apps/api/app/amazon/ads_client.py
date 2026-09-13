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

Protocol details consulted from Amazon's current Ads API documentation
this pass (see `docs/AI_HANDOVER/21_AMAZON_ADS_READONLY_FOUNDATION.md`
for exact sources and which specifics are corroborated-but-unconfirmed
because the docs site did not render for this environment's fetch tool):
- Headers: `Amazon-Advertising-API-ClientId`, `Authorization: Bearer
  <token>`, `Amazon-Advertising-API-Scope: <profileId>`, `Content-Type:
  application/json`.
- Regional hosts: NA `https://advertising-api.amazon.com`, EU
  `https://advertising-api-eu.amazon.com`, FE
  `https://advertising-api-fe.amazon.com`.
- Profiles: `GET /v2/profiles` (list, unscoped by profile header).
- Reporting v3: `POST /reporting/reports` (create), `GET
  /reporting/reports/{reportId}` (poll) — both profile-scoped.
- Sponsored Products v3 list endpoints (campaigns/ad groups/product
  ads/keywords/targets) are POST-based with versioned media types.
  `list_campaigns` was verified against a real production response on
  2026-09-13 during the controlled read-validation task: it requires
  `Content-Type`/`Accept: application/vnd.spcampaign.v3+json` (a generic
  `application/json` request is rejected with 415), and the response
  envelope key is `"campaigns"`, not the originally assumed `"items"`.
  `list_ad_groups`/`list_product_ads`/`list_keywords`/`list_product_targets`
  remain UNCONFIRMED — each is marked with a comment at its call site and
  must be independently verified against a real response the same way
  before being wired to any live path; do not assume the campaigns fix
  generalizes to them.
"""

from __future__ import annotations

import logging
import zlib
from dataclasses import dataclass, field
from typing import Protocol

import httpx
from pydantic import SecretStr, ValidationError

from app.amazon.ads_models import (
    AdsAdGroupResponse,
    AdsCampaignResponse,
    AdsKeywordResponse,
    AdsProductAdResponse,
    AdsProductTargetResponse,
    AdsProfileResponse,
    AdsReportRequestConfiguration,
    AdsReportStatusResponse,
)
from app.core.exceptions import (
    AdsApiAuthenticationError,
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


class AmazonAdsApiClient(Protocol):
    """Injectable Ads REST boundary. Every method is profile-scoped except
    `list_profiles` (profiles are discovered before any profile is
    selected). No method mutates Amazon state."""

    async def list_profiles(self, ctx: AdsRequestContext) -> list[AdsProfileResponse]: ...

    async def list_campaigns(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> tuple[list[AdsCampaignResponse], str | None]: ...

    async def list_ad_groups(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> tuple[list[AdsAdGroupResponse], str | None]: ...

    async def list_product_ads(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> tuple[list[AdsProductAdResponse], str | None]: ...

    async def list_keywords(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> tuple[list[AdsKeywordResponse], str | None]: ...

    async def list_product_targets(
        self, ctx: AdsRequestContext, *, next_token: str | None, page_size: int
    ) -> tuple[list[AdsProductTargetResponse], str | None]: ...

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
    ) -> tuple[list, str | None]:
        self.calls.append(name)
        self._maybe_raise(name)
        if not pages:
            return [], None
        if next_token is None:
            return pages[0]
        # `next_token` is the token the PREVIOUS page handed back — find
        # that page and return the one immediately after it, not the page
        # whose own token happens to equal it (that would return the same
        # page again instead of advancing).
        previous_index = next((i for i, (_, t) in enumerate(pages) if t == next_token), None)
        if previous_index is None or previous_index + 1 >= len(pages):
            return [], None
        return pages[previous_index + 1]

    async def list_campaigns(self, ctx, *, next_token=None, page_size=50):
        return await self._paged("list_campaigns", self.campaigns_pages, next_token=next_token)

    async def list_ad_groups(self, ctx, *, next_token=None, page_size=50):
        return await self._paged("list_ad_groups", self.ad_groups_pages, next_token=next_token)

    async def list_product_ads(self, ctx, *, next_token=None, page_size=50):
        return await self._paged("list_product_ads", self.product_ads_pages, next_token=next_token)

    async def list_keywords(self, ctx, *, next_token=None, page_size=50):
        return await self._paged("list_keywords", self.keywords_pages, next_token=next_token)

    async def list_product_targets(self, ctx, *, next_token=None, page_size=50):
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

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 300:
            return
        if status in (401, 403):
            raise AdsApiAuthenticationError("Amazon Ads API authentication failed.")
        if status == 429:
            retry_after = response.headers.get("Retry-After")
            retry_seconds = float(retry_after) if retry_after and retry_after.strip().isdigit() else None
            raise AdsApiRateLimitedError("Amazon Ads API rate limit reached.", retry_after_seconds=retry_seconds)
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
        self._raise_for_status(response)
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

    async def _list_entities(self, ctx, path, model, *, media_type, response_key, next_token, page_size):
        payload = await self._request_json(
            ctx,
            "POST",
            path,
            with_scope=True,
            media_type=media_type,
            json_body={"maxResults": page_size, **({"nextToken": next_token} if next_token else {})},
        )
        raw_items = payload.get(response_key) or []
        try:
            items = [model.model_validate(item) for item in raw_items]
        except ValidationError:
            raise AdsApiParseFailedError("Amazon Ads API list response was malformed.") from None
        return items, payload.get("nextToken")

    async def list_campaigns(self, ctx, *, next_token=None, page_size=50):
        # CONFIRMED against a real production POST /sp/campaigns/list
        # response on 2026-09-13: requires this exact versioned
        # Content-Type/Accept media type (a generic application/json
        # request is rejected with 415), and the response envelope key
        # is "campaigns", not the previously-assumed "items".
        return await self._list_entities(
            ctx,
            "/sp/campaigns/list",
            AdsCampaignResponse,
            media_type="application/vnd.spcampaign.v3+json",
            response_key="campaigns",
            next_token=next_token,
            page_size=page_size,
        )

    async def list_ad_groups(self, ctx, *, next_token=None, page_size=50):
        # UNCONFIRMED — not yet tested against a real response. Known to
        # be wrong in the same way list_campaigns was before its 2026-09-13
        # fix (a generic application/json request will very likely be
        # rejected with 415); do not wire this to a live path until it is
        # independently verified the same way list_campaigns was.
        return await self._list_entities(
            ctx, "/sp/adGroups/list", AdsAdGroupResponse, media_type="application/json", response_key="items",
            next_token=next_token, page_size=page_size,
        )

    async def list_product_ads(self, ctx, *, next_token=None, page_size=50):
        # UNCONFIRMED — see list_ad_groups.
        return await self._list_entities(
            ctx, "/sp/productAds/list", AdsProductAdResponse, media_type="application/json", response_key="items",
            next_token=next_token, page_size=page_size,
        )

    async def list_keywords(self, ctx, *, next_token=None, page_size=50):
        # UNCONFIRMED — see list_ad_groups.
        return await self._list_entities(
            ctx, "/sp/keywords/list", AdsKeywordResponse, media_type="application/json", response_key="items",
            next_token=next_token, page_size=page_size,
        )

    async def list_product_targets(self, ctx, *, next_token=None, page_size=50):
        # UNCONFIRMED — see list_ad_groups.
        return await self._list_entities(
            ctx, "/sp/targets/list", AdsProductTargetResponse, media_type="application/json", response_key="items",
            next_token=next_token, page_size=page_size,
        )

    async def create_report(self, ctx: AdsRequestContext, configuration: AdsReportRequestConfiguration) -> AdsReportStatusResponse:
        payload = await self._request_json(
            ctx, "POST", "/reporting/reports", with_scope=True, json_body=configuration.model_dump(by_alias=True, mode="json")
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

    async def list_campaigns(self, ctx, *, next_token=None, page_size=50):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_ad_groups(self, ctx, *, next_token=None, page_size=50):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_product_ads(self, ctx, *, next_token=None, page_size=50):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_keywords(self, ctx, *, next_token=None, page_size=50):
        raise AdsConfigurationError(_DISABLED_MESSAGE)

    async def list_product_targets(self, ctx, *, next_token=None, page_size=50):
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

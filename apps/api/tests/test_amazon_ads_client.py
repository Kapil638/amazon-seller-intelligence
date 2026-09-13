from __future__ import annotations

import gzip
import json

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.ads_client import (
    AdsRequestContext,
    HttpAmazonAdsApiClient,
    MockAmazonAdsApiClient,
    decompress_gzip_json,
    redact_headers,
    resolve_region_base_url,
)
from app.amazon.ads_models import AdsCampaignResponse
from app.core.exceptions import AdsApiInvalidRequestError, AdsApiParseFailedError, AdsReportOversizedError


def _ctx(profile_id: str | None = "111") -> AdsRequestContext:
    return AdsRequestContext(
        access_token=SecretStr("Atza|fake"),
        client_id="amzn1.application-oa2-client.test",
        region="NA",
        profile_id=profile_id,
        correlation_id="corr-1",
    )


def test_resolve_region_base_url_known_regions() -> None:
    assert resolve_region_base_url("NA") == "https://advertising-api.amazon.com"
    assert resolve_region_base_url("eu") == "https://advertising-api-eu.amazon.com"
    assert resolve_region_base_url("FE") == "https://advertising-api-fe.amazon.com"


def test_resolve_region_base_url_rejects_unknown_region() -> None:
    with pytest.raises(AdsApiInvalidRequestError):
        resolve_region_base_url("APAC")


def test_redact_headers_hides_bearer_and_client_id_never_scope() -> None:
    headers = {
        "Authorization": "Bearer super-secret-token",
        "Amazon-Advertising-API-ClientId": "amzn1.application-oa2-client.abc",
        "Amazon-Advertising-API-Scope": "111",
        "Content-Type": "application/json",
    }
    redacted = redact_headers(headers)
    assert redacted["Authorization"] == "[redacted]"
    assert redacted["Amazon-Advertising-API-ClientId"] == "[redacted]"
    assert redacted["Amazon-Advertising-API-Scope"] == "111"
    assert redacted["Content-Type"] == "application/json"


def test_decompress_gzip_json_round_trip() -> None:
    payload = [{"date": "2026-09-01", "campaignId": "1", "impressions": 10}]
    raw = gzip.compress(json.dumps(payload).encode("utf-8"))
    decompressed = decompress_gzip_json(raw, max_bytes=1_000_000)
    assert json.loads(decompressed) == payload


def test_decompress_gzip_json_refuses_zip_bomb_style_payload() -> None:
    huge = json.dumps([{"x": "a" * 1000}] * 5000).encode("utf-8")
    raw = gzip.compress(huge)
    with pytest.raises(AdsReportOversizedError):
        decompress_gzip_json(raw, max_bytes=1000)


@pytest.mark.asyncio
async def test_mock_client_paginates_by_next_token() -> None:
    page1 = ([AdsCampaignResponse(campaignId="1", name="A", state="ENABLED")], "token-2")
    page2 = ([AdsCampaignResponse(campaignId="2", name="B", state="PAUSED")], None)
    client = MockAmazonAdsApiClient(campaigns_pages=[page1, page2])

    items1, next_token1 = await client.list_campaigns(_ctx(), next_token=None, page_size=1)
    assert [i.campaign_id for i in items1] == ["1"]
    assert next_token1 == "token-2"

    items2, next_token2 = await client.list_campaigns(_ctx(), next_token="token-2", page_size=1)
    assert [i.campaign_id for i in items2] == ["2"]
    assert next_token2 is None


@pytest.mark.asyncio
async def test_mock_client_raises_when_configured_and_records_call() -> None:
    client = MockAmazonAdsApiClient(raise_on={"list_profiles": RuntimeError("boom")})
    with pytest.raises(RuntimeError):
        await client.list_profiles(_ctx())
    assert client.calls == ["list_profiles"]


@pytest.mark.asyncio
async def test_mock_client_download_report_enforces_max_bytes() -> None:
    client = MockAmazonAdsApiClient(report_bodies={"https://x/report": b"0123456789"})
    with pytest.raises(AdsReportOversizedError):
        await client.download_report(_ctx(), "https://x/report", max_bytes=5)


# --- HttpAmazonAdsApiClient.list_campaigns() contract, fixture-based on a
# real production POST /sp/campaigns/list response captured 2026-09-13
# during the controlled read-validation task (field values below are
# synthetic — not the real advertiser's actual campaign data — but the
# envelope shape, media type requirement, and field names are the real,
# confirmed ones). See ads_client.py's module docstring and
# list_campaigns()'s comment for the finding this guards against
# regressing: a generic application/json request is rejected by Amazon
# with 415, and the envelope key is "campaigns", not "items".

REAL_CAMPAIGN_LIST_RESPONSE_FIXTURE = {
    "campaigns": [
        {
            "campaignId": "111222333444",
            "name": "Example Campaign",
            "state": "ENABLED",
            "targetingType": "MANUAL",
            "budget": {"budget": 25.0, "budgetType": "DAILY"},
            "startDate": "2026-01-01",
            "endDate": None,
            "marketplaceBudgetAllocation": "MANUAL",
            "dynamicBidding": {"placementBidding": []},
            "offAmazonSettings": {},
            "tags": {},
        }
    ],
    "nextToken": None,
}


def _campaign_list_transport(expected_media_type: str = "application/vnd.spcampaign.v3+json") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == expected_media_type
        assert request.headers["accept"] == expected_media_type
        return httpx.Response(200, json=REAL_CAMPAIGN_LIST_RESPONSE_FIXTURE)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_list_campaigns_sends_the_confirmed_versioned_media_type() -> None:
    client = HttpAmazonAdsApiClient(transport=_campaign_list_transport())
    items, next_token = await client.list_campaigns(_ctx())
    assert next_token is None
    assert len(items) == 1


@pytest.mark.asyncio
async def test_list_campaigns_parses_the_campaigns_envelope_key_not_items() -> None:
    client = HttpAmazonAdsApiClient(transport=_campaign_list_transport())
    items, _ = await client.list_campaigns(_ctx())
    campaign = items[0]
    assert campaign.campaign_id == "111222333444"
    assert campaign.name == "Example Campaign"


@pytest.mark.asyncio
async def test_list_campaigns_parses_the_nested_budget_object_not_a_flat_daily_budget_field() -> None:
    client = HttpAmazonAdsApiClient(transport=_campaign_list_transport())
    items, _ = await client.list_campaigns(_ctx())
    campaign = items[0]
    assert campaign.budget is not None
    assert campaign.budget.budget_type == "DAILY"
    assert campaign.budget.budget == 25.0


@pytest.mark.asyncio
async def test_list_campaigns_with_an_items_envelope_finds_no_campaigns() -> None:
    """Regression guard for the exact bug found live: a response shaped
    to the OLD (wrong) assumption ("items" key) must not be silently
    accepted as if it had campaigns in it — the real key is "campaigns"."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [{"campaignId": "1", "name": "A", "state": "ENABLED"}], "nextToken": None})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    items, _ = await client.list_campaigns(_ctx())
    assert items == []


@pytest.mark.asyncio
async def test_list_campaigns_surfaces_a_clear_error_when_amazon_rejects_the_media_type() -> None:
    """Documents the real failure mode observed live before the fix: a
    415 from Amazon (wrong Content-Type) must surface as a typed error,
    never a silent empty result."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            415,
            json={"message": "Server cannot provide a response with Content-Type `*/*`."},
        )

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiInvalidRequestError):
        await client.list_campaigns(_ctx())


@pytest.mark.asyncio
async def test_unconfirmed_entity_list_endpoints_still_use_the_old_generic_media_type() -> None:
    """Documents current (unfixed, unconfirmed) state: only list_campaigns
    has been verified against a real response. The other four entity-list
    methods must not silently start claiming the same fix — this test
    fails loudly if someone changes their media type without also adding
    the same kind of live-verified regression coverage list_campaigns has
    above."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/json"
        return httpx.Response(200, json={"items": [], "nextToken": None})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    items, _ = await client.list_ad_groups(_ctx())
    assert items == []

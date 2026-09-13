from __future__ import annotations

import gzip
import json

import pytest
from pydantic import SecretStr

from app.amazon.ads_client import (
    AdsRequestContext,
    MockAmazonAdsApiClient,
    decompress_gzip_json,
    redact_headers,
    resolve_region_base_url,
)
from app.amazon.ads_models import AdsCampaignResponse
from app.core.exceptions import AdsApiInvalidRequestError, AdsReportOversizedError


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

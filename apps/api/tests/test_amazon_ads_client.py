from __future__ import annotations

import gzip
import inspect
import json

import httpx
import pytest
from pydantic import SecretStr

from app.amazon.ads_client import (
    AdsRequestContext,
    AmazonAdsApiClient,
    EntityParseResult,
    HttpAmazonAdsApiClient,
    MockAmazonAdsApiClient,
    decompress_gzip_json,
    parse_entity_list_envelope,
    redact_headers,
    resolve_region_base_url,
)
from app.amazon.ads_models import (
    CAMPAIGN_STATES,
    SIBLING_ENTITY_STATES,
    AdsAdGroupResponse,
    AdsCampaignResponse,
    AdsKeywordResponse,
    AdsProductAdResponse,
    AdsProductTargetResponse,
)
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

    result1 = await client.list_campaigns(_ctx(), next_token=None, page_size=1)
    assert [i.campaign_id for i in result1.items] == ["1"]
    assert result1.next_token == "token-2"
    assert result1.total_items == 1
    assert result1.accepted_items == 1

    result2 = await client.list_campaigns(_ctx(), next_token="token-2", page_size=1)
    assert [i.campaign_id for i in result2.items] == ["2"]
    assert result2.next_token is None


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


# --------------------------------------------------------------------
# parse_entity_list_envelope — offline, reusable per-item parsing.
# No HTTP client involved; PR B2's orchestration is meant to call this
# same function directly. See docs/AI_HANDOVER/
# 24_AMAZON_ADS_SPONSORED_PRODUCTS_HIERARCHY_CONTRACTS.md.
# --------------------------------------------------------------------


def test_parse_entity_list_envelope_accepts_every_valid_item() -> None:
    payload = {
        "campaigns": [
            {"campaignId": "1", "name": "A", "state": "ENABLED"},
            {"campaignId": "2", "name": "B", "state": "PAUSED"},
        ],
        "nextToken": "abc",
    }
    result = parse_entity_list_envelope(
        payload, response_key="campaigns", model=AdsCampaignResponse, allowed_states=CAMPAIGN_STATES
    )
    assert result.total_items == 2
    assert result.accepted_items == 2
    assert result.schema_rejected_items == 0
    assert result.unsupported_state_items == 0
    assert result.next_token == "abc"
    assert not result.is_contract_mismatch


def test_parse_entity_list_envelope_isolates_a_malformed_item_from_valid_siblings() -> None:
    """One item missing a required field must not discard the others."""
    payload = {
        "campaigns": [
            {"campaignId": "1", "name": "Good", "state": "ENABLED"},
            {"name": "Missing campaignId", "state": "ENABLED"},  # schema-rejected
            {"campaignId": "3", "name": "Also good", "state": "PAUSED"},
        ]
    }
    result = parse_entity_list_envelope(payload, response_key="campaigns", model=AdsCampaignResponse)
    assert result.total_items == 3
    assert result.accepted_items == 2
    assert result.schema_rejected_items == 1
    assert [i.campaign_id for i in result.items] == ["1", "3"]


def test_parse_entity_list_envelope_classifies_unsupported_state_distinctly_from_schema_rejection() -> None:
    """An ad group with a state outside the confirmed 3-value sibling
    enum is structurally valid (schema-wise) but must be rejected as
    unsupported-state, not silently accepted and not folded into the
    schema-rejected count."""
    payload = {
        "adGroups": [
            {"adGroupId": "1", "campaignId": "c1", "name": "A", "state": "ENABLED"},
            {"adGroupId": "2", "campaignId": "c1", "name": "B", "state": "PROPOSED"},  # unsupported, not documented for ad groups
        ]
    }
    result = parse_entity_list_envelope(
        payload, response_key="adGroups", model=AdsAdGroupResponse, allowed_states=SIBLING_ENTITY_STATES
    )
    assert result.total_items == 2
    assert result.accepted_items == 1
    assert result.schema_rejected_items == 0
    assert result.unsupported_state_items == 1
    assert [i.ad_group_id for i in result.items] == ["1"]


def test_parse_entity_list_envelope_campaign_accepts_the_full_seven_value_enum() -> None:
    payload = {
        "campaigns": [
            {"campaignId": str(n), "name": f"C{n}", "state": state}
            for n, state in enumerate(sorted(CAMPAIGN_STATES), start=1)
        ]
    }
    result = parse_entity_list_envelope(
        payload, response_key="campaigns", model=AdsCampaignResponse, allowed_states=CAMPAIGN_STATES
    )
    assert result.accepted_items == len(CAMPAIGN_STATES) == 7
    assert result.unsupported_state_items == 0


def test_parse_entity_list_envelope_treats_a_nonempty_all_rejected_page_as_contract_mismatch() -> None:
    payload = {"campaigns": [{"name": "No id at all", "state": "ENABLED"}]}
    result = parse_entity_list_envelope(payload, response_key="campaigns", model=AdsCampaignResponse)
    assert result.total_items == 1
    assert result.accepted_items == 0
    assert result.is_contract_mismatch


def test_parse_entity_list_envelope_treats_a_genuinely_empty_page_as_not_a_contract_mismatch() -> None:
    payload = {"campaigns": [], "nextToken": None}
    result = parse_entity_list_envelope(payload, response_key="campaigns", model=AdsCampaignResponse)
    assert result.total_items == 0
    assert result.accepted_items == 0
    assert not result.is_contract_mismatch


def test_parse_entity_list_envelope_missing_response_key_is_an_empty_page_not_malformed() -> None:
    payload = {"nextToken": None}
    result = parse_entity_list_envelope(payload, response_key="campaigns", model=AdsCampaignResponse)
    assert result.total_items == 0
    assert not result.is_contract_mismatch


def test_parse_entity_list_envelope_rejects_a_non_array_top_level_value() -> None:
    """The malformed-top-level case: response_key present but not a JSON
    array (e.g. Amazon changed the envelope to an object or a string)."""
    payload = {"campaigns": {"unexpected": "object, not an array"}}
    with pytest.raises(AdsApiParseFailedError):
        parse_entity_list_envelope(payload, response_key="campaigns", model=AdsCampaignResponse)


def test_parse_entity_list_envelope_ignores_a_malformed_next_token_type() -> None:
    payload = {"campaigns": [], "nextToken": 12345}
    result = parse_entity_list_envelope(payload, response_key="campaigns", model=AdsCampaignResponse)
    assert result.next_token is None


# --------------------------------------------------------------------
# Canonical identifier normalization (AdsCampaignResponse and siblings)
# --------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw_id,expected",
    [
        ("111222333444", "111222333444"),
        (111222333444, "111222333444"),
        ("  111222333444  ", "111222333444"),
    ],
)
def test_campaign_id_accepts_valid_string_and_integer_forms(raw_id, expected) -> None:
    campaign = AdsCampaignResponse(campaignId=raw_id, name="A", state="ENABLED")
    assert campaign.campaign_id == expected


@pytest.mark.parametrize("raw_id", [True, False, 1.0, 111222333444.0, "", "   ", None, [1], {"a": 1}])
def test_campaign_id_rejects_bool_float_blank_and_malformed_values(raw_id) -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic.ValidationError, deliberately broad for the parametrized cases
        AdsCampaignResponse(campaignId=raw_id, name="A", state="ENABLED")


def test_campaign_id_never_loses_precision_through_a_large_integer() -> None:
    """A losslessly-representable large int must round-trip exactly — the
    normalizer must never cast through float, which would silently
    corrupt an id this large."""
    huge = 123456789012345678901234567890
    campaign = AdsCampaignResponse(campaignId=huge, name="A", state="ENABLED")
    assert campaign.campaign_id == str(huge)


def test_ad_group_parent_campaign_id_is_normalized_too() -> None:
    ad_group = AdsAdGroupResponse(adGroupId=1, campaignId=2, name="AG", state="ENABLED")
    assert ad_group.ad_group_id == "1"
    assert ad_group.campaign_id == "2"


def test_ad_group_rejects_a_float_parent_campaign_id() -> None:
    with pytest.raises(Exception):  # noqa: B017 - pydantic.ValidationError
        AdsAdGroupResponse(adGroupId="1", campaignId=2.0, name="AG", state="ENABLED")


def test_product_ad_normalizes_all_three_id_fields() -> None:
    product_ad = AdsProductAdResponse(adId=1, adGroupId=2, campaignId=3, state="ENABLED")
    assert (product_ad.ad_id, product_ad.ad_group_id, product_ad.campaign_id) == ("1", "2", "3")


def test_keyword_normalizes_all_three_id_fields() -> None:
    keyword = AdsKeywordResponse(
        keywordId=1, adGroupId=2, campaignId=3, keywordText="running shoes", matchType="BROAD", state="ENABLED"
    )
    assert (keyword.keyword_id, keyword.ad_group_id, keyword.campaign_id) == ("1", "2", "3")


def test_product_target_normalizes_all_three_id_fields() -> None:
    target = AdsProductTargetResponse(targetId=1, adGroupId=2, campaignId=3, state="ENABLED")
    assert (target.target_id, target.ad_group_id, target.campaign_id) == ("1", "2", "3")


def test_portfolio_id_is_normalized_when_present_but_remains_opaque() -> None:
    campaign = AdsCampaignResponse(campaignId="1", name="A", state="ENABLED", portfolioId=555)
    assert campaign.portfolio_id == "555"


def test_portfolio_id_absent_stays_none() -> None:
    campaign = AdsCampaignResponse(campaignId="1", name="A", state="ENABLED")
    assert campaign.portfolio_id is None


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
    result = await client.list_campaigns(_ctx())
    assert result.next_token is None
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_list_campaigns_parses_the_campaigns_envelope_key_not_items() -> None:
    client = HttpAmazonAdsApiClient(transport=_campaign_list_transport())
    result = await client.list_campaigns(_ctx())
    campaign = result.items[0]
    assert campaign.campaign_id == "111222333444"
    assert campaign.name == "Example Campaign"


@pytest.mark.asyncio
async def test_list_campaigns_parses_the_nested_budget_object_not_a_flat_daily_budget_field() -> None:
    client = HttpAmazonAdsApiClient(transport=_campaign_list_transport())
    result = await client.list_campaigns(_ctx())
    campaign = result.items[0]
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
    result = await client.list_campaigns(_ctx())
    assert result.items == []
    assert result.total_items == 0  # genuinely empty (missing "campaigns"), not a contract mismatch


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


# --------------------------------------------------------------------
# The four sibling entity-list endpoints — PR B1: media types are now
# taken from the blueprint's officially documented SP v3 OpenAPI schema
# (a materially stronger basis than the prior application/json
# placeholder), and are fixture-tested here. Envelope keys for
# product ads/keywords/product targets remain UNCONFIRMED inferences —
# see ads_client.py's own module docstring — so these tests prove the
# code's *current, intended* behavior, not a live-verified contract.
# --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_ad_groups_sends_the_documented_media_type_and_parses_the_adgroups_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/vnd.spAdGroup.v3+json"
        assert request.headers["accept"] == "application/vnd.spAdGroup.v3+json"
        return httpx.Response(
            200,
            json={
                "adGroups": [{"adGroupId": "1", "campaignId": "10", "name": "AG1", "state": "ENABLED", "defaultBid": 0.75}],
                "nextToken": None,
            },
        )

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    result = await client.list_ad_groups(_ctx())
    assert len(result.items) == 1
    assert result.items[0].ad_group_id == "1"
    assert result.items[0].campaign_id == "10"


@pytest.mark.asyncio
async def test_list_product_ads_sends_the_documented_media_type_and_parses_the_productads_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/vnd.spProductAd.v3+json"
        return httpx.Response(
            200,
            json={
                "productAds": [{"adId": "1", "adGroupId": "2", "campaignId": "3", "state": "ENABLED", "sku": "SKU-1"}],
                "nextToken": None,
            },
        )

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    result = await client.list_product_ads(_ctx())
    assert len(result.items) == 1
    assert result.items[0].sku == "SKU-1"


@pytest.mark.asyncio
async def test_list_keywords_sends_the_documented_media_type_and_parses_the_keywords_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/vnd.spKeyword.v3+json"
        return httpx.Response(
            200,
            json={
                "keywords": [
                    {
                        "keywordId": "1", "adGroupId": "2", "campaignId": "3",
                        "keywordText": "running shoes", "matchType": "BROAD", "state": "ENABLED",
                    }
                ],
                "nextToken": None,
            },
        )

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    result = await client.list_keywords(_ctx())
    assert len(result.items) == 1
    assert result.items[0].keyword_text == "running shoes"


@pytest.mark.asyncio
async def test_list_product_targets_sends_the_documented_media_type_and_parses_the_targetingclauses_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["content-type"] == "application/vnd.spTargetingClause.v3+json"
        return httpx.Response(
            200,
            json={
                "targetingClauses": [
                    {"targetId": "1", "adGroupId": "2", "campaignId": "3", "expressionType": "AUTO", "state": "ENABLED"}
                ],
                "nextToken": None,
            },
        )

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    result = await client.list_product_targets(_ctx())
    assert len(result.items) == 1
    assert result.items[0].expression_type == "AUTO"


@pytest.mark.asyncio
async def test_entity_list_endpoints_reject_a_malformed_top_level_envelope() -> None:
    """A response where the entity key exists but is not a JSON array
    must surface as a typed parse error, not silently become an empty
    list or crash with an unrelated exception."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"adGroups": "not-an-array", "nextToken": None})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    with pytest.raises(AdsApiParseFailedError):
        await client.list_ad_groups(_ctx())


@pytest.mark.asyncio
async def test_entity_list_page_size_is_bounded_even_if_caller_passes_an_absurd_value() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"campaigns": [], "nextToken": None})

    client = HttpAmazonAdsApiClient(transport=httpx.MockTransport(handler))
    await client.list_campaigns(_ctx(), page_size=999_999)
    assert captured["body"]["maxResults"] == 1000  # clamped, never sent unbounded

    await client.list_campaigns(_ctx(), page_size=0)
    assert captured["body"]["maxResults"] == 1  # clamped up, never zero/negative


# --------------------------------------------------------------------
# Read-only structural guarantee: no mutation-shaped method exists on
# the client surface. create_report() is the one deliberate exception —
# it creates an async REPORT REQUEST (a read operation per the
# blueprint's own §9: "Access | Read (async create is not a campaign
# mutation)"), never a campaign/ad/keyword/target mutation.
# --------------------------------------------------------------------

_MUTATION_VERB_PREFIXES = ("create_campaign", "update_", "delete_", "archive_", "modify_", "apply_", "set_bid", "set_budget")


def test_client_protocol_exposes_no_mutation_shaped_methods() -> None:
    method_names = [
        name for name, _ in inspect.getmembers(AmazonAdsApiClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    assert method_names, "sanity: the protocol must expose at least its read methods"
    for name in method_names:
        assert not any(name.startswith(prefix) for prefix in _MUTATION_VERB_PREFIXES), (
            f"{name!r} looks mutation-shaped — PR B1 is read-only"
        )
    # create_report is the sole, deliberate exception to a blanket
    # "never starts with create_" rule — assert it exists and that
    # nothing else does.
    assert "create_report" in method_names
    other_create_methods = [n for n in method_names if n.startswith("create_") and n != "create_report"]
    assert other_create_methods == []


def test_http_client_implementation_exposes_no_mutation_shaped_methods() -> None:
    method_names = [
        name for name, _ in inspect.getmembers(HttpAmazonAdsApiClient, predicate=inspect.isfunction)
        if not name.startswith("_")
    ]
    for name in method_names:
        assert not any(name.startswith(prefix) for prefix in _MUTATION_VERB_PREFIXES)
    other_create_methods = [n for n in method_names if n.startswith("create_") and n != "create_report"]
    assert other_create_methods == []

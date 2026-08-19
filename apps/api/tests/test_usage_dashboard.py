from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from app.ai.openai_provider import OpenAIProvider, _usage_from_response
from app.models.ai_listing_intelligence import AIListingIntelligence
from app.prompts.listing_intelligence import PROMPT_VERSION
from app.usage.dashboard import UsageDashboardService
from app.usage.ledger import get_usage_ledger
from app.usage.openai_account import OpenAIAccountClient, sum_openai_costs_usd
from app.usage.provider_cache import TimedValueCache
from app.usage.rainforest_account import RainforestAccountClient
from tests.test_ai_listing_intelligence import sample_intelligence
from tests.test_openai_provider import FakeClient
from tests.test_rainforest_account import load_account

ADMIN_KEY = "sk-admin-secret-usage-key"
RF_KEY = "super-secret-rainforest-account-key"


def costs_payload(spend: float = 0.18) -> dict:
    return {
        "object": "page",
        "data": [
            {
                "object": "bucket",
                "start_time": 1754006400,
                "results": [
                    {
                        "object": "organization.costs.result",
                        "amount": {"value": spend, "currency": "usd"},
                        "line_item": "GPT-5.4",
                    }
                ],
            }
        ],
        "has_more": False,
        "next_page": None,
    }


def test_sum_openai_costs_from_official_buckets() -> None:
    assert sum_openai_costs_usd(costs_payload(0.18)) == 0.18
    assert sum_openai_costs_usd({"data": []}) == 0.0


def test_usage_from_response_reads_cached_input_tokens() -> None:
    response = SimpleNamespace(
        usage=SimpleNamespace(
            input_tokens=9000,
            output_tokens=6253,
            total_tokens=15253,
            input_tokens_details=SimpleNamespace(cached_tokens=1200),
        )
    )
    usage = _usage_from_response(response)
    assert usage is not None
    assert usage.input_tokens == 9000
    assert usage.cached_input_tokens == 1200
    assert usage.output_tokens == 6253
    assert usage.total_tokens == 15253


@pytest.mark.asyncio
async def test_openai_account_maps_provider_spend() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {ADMIN_KEY}"
        assert "start_time" in request.url.params
        return httpx.Response(200, json=costs_payload())

    client = OpenAIAccountClient(
        admin_api_key=ADMIN_KEY,
        budget_usd=100,
        transport=httpx.MockTransport(handler),
        cache=TimedValueCache(),
    )
    usage = await client.get_usage()
    assert usage.available is True
    assert usage.spend_usd == 0.18
    assert usage.budget_usd == 100
    assert usage.usage_percentage == 0.2
    assert usage.warning_level == "normal"
    dumped = usage.model_dump_json()
    assert ADMIN_KEY not in dumped
    assert "sk-admin" not in dumped


@pytest.mark.asyncio
async def test_openai_account_usage_is_cached() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=costs_payload())

    client = OpenAIAccountClient(
        admin_api_key=ADMIN_KEY,
        budget_usd=100,
        transport=httpx.MockTransport(handler),
        cache=TimedValueCache(),
        cache_ttl_seconds=300,
    )
    await client.get_usage()
    await client.get_usage()
    assert calls["count"] == 1


@pytest.mark.asyncio
async def test_openai_account_failure_does_not_raise() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "nope"}})

    client = OpenAIAccountClient(
        admin_api_key=ADMIN_KEY,
        budget_usd=100,
        transport=httpx.MockTransport(handler),
        cache=TimedValueCache(),
    )
    usage = await client.get_usage()
    assert usage.available is False
    assert usage.spend_usd is None
    assert usage.message == "Usage temporarily unavailable"
    assert ADMIN_KEY not in usage.model_dump_json()


@pytest.mark.asyncio
async def test_openai_account_without_admin_key_is_not_configured() -> None:
    client = OpenAIAccountClient(admin_api_key="", budget_usd=100, cache=TimedValueCache())
    usage = await client.get_usage()
    assert usage.available is False
    assert usage.status == "not_configured"
    assert usage.budget_usd == 100


@pytest.mark.asyncio
async def test_dashboard_separates_provider_and_app_usage() -> None:
    get_usage_ledger().record_rainforest_product_call()
    get_usage_ledger().record_rainforest_search_call()
    get_usage_ledger().record_rainforest_cache_hit("product")
    get_usage_ledger().record_openai_call(
        workflow="listing_intelligence",
        model="gpt-5.4",
        input_tokens=9000,
        output_tokens=6253,
        total_tokens=15253,
    )
    get_usage_ledger().record_openai_cache_hit()

    def rf_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_account())

    def oa_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=costs_payload())

    cache = TimedValueCache()
    service = UsageDashboardService(
        rainforest_client=RainforestAccountClient(
            api_key=RF_KEY,
            transport=httpx.MockTransport(rf_handler),
            cache=cache,
        ),
        openai_client=OpenAIAccountClient(
            admin_api_key=ADMIN_KEY,
            budget_usd=100,
            transport=httpx.MockTransport(oa_handler),
            cache=cache,
        ),
    )
    dashboard = await service.get_dashboard()
    assert dashboard.rainforest.account.source == "rainforest_account_api"
    assert dashboard.rainforest.app.source == "application_ledger"
    assert dashboard.rainforest.account.credits_used == 21
    assert dashboard.rainforest.account.credits_remaining == 79
    assert dashboard.rainforest.app.product_calls == 1
    assert dashboard.rainforest.app.search_calls == 1
    assert dashboard.rainforest.app.calls_saved == 1
    assert dashboard.openai.account.source == "openai_organization_costs_api"
    assert dashboard.openai.app.source == "application_ledger"
    assert dashboard.openai.account.spend_usd == 0.18
    assert dashboard.openai.account.budget_usd == 100
    assert dashboard.openai.app.requests == 1
    assert dashboard.openai.app.total_tokens == 15253
    assert dashboard.openai.app.calls_saved == 1
    assert dashboard.openai.app.estimated_spend_usd is not None
    assert dashboard.openai.app.estimated_spend_usd != dashboard.openai.account.spend_usd
    blob = dashboard.model_dump_json()
    assert RF_KEY not in blob
    assert ADMIN_KEY not in blob
    assert "seller@example.com" not in blob


def test_dashboard_endpoint_uses_injected_service(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def rf_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_account())

    def oa_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=costs_payload(0.18))

    cache = TimedValueCache()
    service = UsageDashboardService(
        rainforest_client=RainforestAccountClient(
            api_key=RF_KEY,
            transport=httpx.MockTransport(rf_handler),
            cache=cache,
        ),
        openai_client=OpenAIAccountClient(
            admin_api_key=ADMIN_KEY,
            budget_usd=100,
            transport=httpx.MockTransport(oa_handler),
            cache=cache,
        ),
    )
    monkeypatch.setattr("app.api.routes.usage.get_usage_dashboard_service", lambda: service)
    response = client.get("/api/v1/usage/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["rainforest"]["account"]["credits_used"] == 21
    assert body["rainforest"]["account"]["usage_percentage"] == 21
    assert body["rainforest"]["app"]["product_calls"] == 0
    assert body["openai"]["account"]["spend_usd"] == 0.18
    assert body["openai"]["account"]["available"] is True
    assert RF_KEY not in response.text
    assert ADMIN_KEY not in response.text
    assert "api_key" not in body["rainforest"]["account"]
    assert body["rainforest"]["account"]["source"] != body["rainforest"]["app"]["source"]
    assert body["openai"]["account"]["source"] != body["openai"]["app"]["source"]


def test_dashboard_endpoint_survives_provider_failures(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    cache = TimedValueCache()
    service = UsageDashboardService(
        rainforest_client=RainforestAccountClient(
            api_key=RF_KEY,
            transport=httpx.MockTransport(boom),
            cache=cache,
        ),
        openai_client=OpenAIAccountClient(
            admin_api_key=ADMIN_KEY,
            budget_usd=100,
            transport=httpx.MockTransport(boom),
            cache=cache,
        ),
    )
    monkeypatch.setattr("app.api.routes.usage.get_usage_dashboard_service", lambda: service)
    response = client.get("/api/v1/usage/dashboard")
    assert response.status_code == 200
    body = response.json()
    assert body["rainforest"]["account"]["available"] is False
    assert body["openai"]["account"]["available"] is False
    assert body["rainforest"]["app"]["product_calls"] == 0
    assert "Usage temporarily unavailable" in body["rainforest"]["account"]["message"]


@pytest.mark.asyncio
async def test_openai_provider_captures_cached_tokens() -> None:
    payload = sample_intelligence()
    client = FakeClient(
        [
            SimpleNamespace(
                output_parsed=payload,
                usage=SimpleNamespace(
                    input_tokens=40,
                    output_tokens=10,
                    total_tokens=50,
                    input_tokens_details=SimpleNamespace(cached_tokens=8),
                ),
                incomplete_details=None,
            )
        ]
    )
    provider = OpenAIProvider(api_key="test-openai-key", model="gpt-5.4", client=client)
    result = await provider.generate_structured(
        schema=AIListingIntelligence,
        system_prompt="sys",
        user_prompt="user",
        repair_prompt="repair",
        prompt_version=PROMPT_VERSION,
    )
    assert result.usage is not None
    assert result.usage.cached_input_tokens == 8
    assert "test-openai-key" not in repr(provider)

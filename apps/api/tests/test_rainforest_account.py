import json
from pathlib import Path

import httpx
import pytest

from app.providers.memory_cache import MemoryTtlCache
from app.providers.rainforest import RainforestProductDataProvider
from app.usage.ledger import get_usage_ledger
from app.usage.levels import usage_percentage, warning_level
from app.usage.provider_cache import TimedValueCache
from app.usage.rainforest_account import RainforestAccountClient, map_rainforest_account
from app.models.usage import RainforestUsagePoint

FIXTURES = Path(__file__).parent / "fixtures" / "rainforest"
ACCOUNT_KEY = "super-secret-rainforest-account-key"
PRODUCT_KEY = "test-rainforest-product-key"


def load_account() -> dict:
    return json.loads((FIXTURES / "account.json").read_text(encoding="utf-8"))


def test_account_mapping_keeps_credits_and_hides_identity() -> None:
    usage = map_rainforest_account(load_account())
    assert usage.available is True
    assert usage.credits_used == 21
    assert usage.credits_limit == 100
    assert usage.credits_remaining == 79
    assert usage.usage_percentage == 21.0
    assert usage.warning_level == "normal"
    assert usage.reset_at is not None
    assert usage.reset_at.year == 2026
    assert usage.reset_at.month == 9
    assert usage.reset_at.day == 19
    assert [point.credits_used for point in usage.usage_history] == [4, 17]
    dumped = usage.model_dump(mode="json")
    blob = json.dumps(dumped)
    assert "api_key" not in dumped
    assert "email" not in dumped
    assert "name" not in dumped
    assert ACCOUNT_KEY not in blob
    assert "seller@example.com" not in blob
    assert "Example Seller" not in blob


def test_account_mapping_derives_limit_and_flattens_monthly_history() -> None:
    payload = {
        "account_info": {
            "api_key": "super-secret-rainforest-account-key",
            "email": "seller@example.com",
            "name": "Example Seller",
            "credits_used": 23,
            "credits_remaining": 77,
            "credits_reset_at": "2026-09-19T04:26:34.000Z",
            "usage_history": [
                {
                    "month": "August",
                    "year": 2026,
                    "month_number": 8,
                    "is_current_month": True,
                    "credits_total_for_month": 23,
                    "credits_total_per_day": {"18": 0, "19": 23, "20": 0},
                }
            ],
        }
    }
    usage = map_rainforest_account(payload)
    assert usage.credits_used == 23
    assert usage.credits_remaining == 77
    assert usage.credits_limit == 100
    assert usage.usage_percentage == 23.0
    assert usage.warning_level == "normal"
    assert usage.reset_at is not None
    assert usage.reset_at.day == 19
    assert usage.usage_history == [
        RainforestUsagePoint(date="2026-08-19", credits_used=23),
    ]
    blob = usage.model_dump_json()
    assert "super-secret-rainforest-account-key" not in blob
    assert "seller@example.com" not in blob


def test_usage_percentage_and_warning_levels() -> None:
    assert usage_percentage(21, 100) == 21.0
    assert usage_percentage(0, 100) == 0.0
    assert usage_percentage(10, 0) is None
    assert warning_level(69.9) == "normal"
    assert warning_level(70) == "warning"
    assert warning_level(89.9) == "warning"
    assert warning_level(90) == "critical"


@pytest.mark.asyncio
async def test_account_client_uses_account_endpoint_not_product_api() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        assert request.url.params.get("api_key") == ACCOUNT_KEY
        assert "type" not in request.url.params
        return httpx.Response(200, json=load_account())

    cache = TimedValueCache()
    client = RainforestAccountClient(
        api_key=ACCOUNT_KEY,
        account_url="https://api.rainforestapi.com/account",
        transport=httpx.MockTransport(handler),
        cache=cache,
        cache_ttl_seconds=60,
    )
    first = await client.get_usage()
    second = await client.get_usage()
    assert first.credits_used == 21
    assert second.credits_used == 21
    assert len(calls) == 1
    assert "/account" in calls[0]
    assert "type=product" not in calls[0]
    ledger = get_usage_ledger()
    assert ledger.rainforest_account_lookups == 1
    assert ledger.rainforest_product_calls == 0
    assert ledger.rainforest_search_calls == 0


@pytest.mark.asyncio
async def test_account_client_force_refresh_bypasses_cache() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json=load_account())

    client = RainforestAccountClient(
        api_key=ACCOUNT_KEY,
        transport=httpx.MockTransport(handler),
        cache=TimedValueCache(),
        cache_ttl_seconds=60,
    )
    await client.get_usage()
    await client.get_usage(force_refresh=True)
    assert calls["count"] == 2


@pytest.mark.asyncio
async def test_account_endpoint_failure_does_not_raise() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "nope"})

    client = RainforestAccountClient(
        api_key=ACCOUNT_KEY,
        transport=httpx.MockTransport(handler),
        cache=TimedValueCache(),
    )
    usage = await client.get_usage()
    assert usage.available is False
    assert usage.status == "unavailable"
    assert usage.message == "Usage temporarily unavailable"
    assert usage.credits_used is None


@pytest.mark.asyncio
async def test_missing_account_key_is_not_configured() -> None:
    client = RainforestAccountClient(api_key="", cache=TimedValueCache())
    usage = await client.get_usage()
    assert usage.available is False
    assert usage.status == "not_configured"


@pytest.mark.asyncio
async def test_product_lookup_is_counted_but_account_lookup_is_not() -> None:
    product_calls = {"count": 0}

    def product_handler(_request: httpx.Request) -> httpx.Response:
        product_calls["count"] += 1
        payload = json.loads((FIXTURES / "product.json").read_text(encoding="utf-8"))
        return httpx.Response(200, json=payload)

    provider = RainforestProductDataProvider(
        api_key=PRODUCT_KEY,
        cache=MemoryTtlCache(ttl_seconds=60),
        transport=httpx.MockTransport(product_handler),
    )
    await provider.get_product("B07J4TNYV8", "amazon.in")
    await provider.get_product("B07J4TNYV8", "amazon.in")

    def account_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_account())

    account = RainforestAccountClient(
        api_key=ACCOUNT_KEY,
        transport=httpx.MockTransport(account_handler),
        cache=TimedValueCache(),
    )
    await account.get_usage()

    ledger = get_usage_ledger()
    assert product_calls["count"] == 1
    assert ledger.rainforest_product_calls == 1
    assert ledger.rainforest_cache_hits == 1
    assert ledger.rainforest_app_snapshot().calls_saved == 1
    assert ledger.rainforest_account_lookups == 1
    assert ledger.rainforest_search_calls == 0

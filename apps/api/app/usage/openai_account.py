from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.usage import OpenAIAccountUsage
from app.usage.levels import usage_percentage, warning_level
from app.usage.provider_cache import TimedValueCache, get_provider_usage_cache

ACCOUNT_CACHE_KEY = "openai-account-costs"
COSTS_URL = "https://api.openai.com/v1/organization/costs"
UNAVAILABLE_MESSAGE = "Usage temporarily unavailable"
NOT_CONFIGURED_MESSAGE = (
    "OpenAI provider spend is not configured. Set OPENAI_ADMIN_API_KEY "
    "(an Admin API key) on the backend to load organization costs."
)


class OpenAIAccountClient:
    """Official organization Costs API. Requires an Admin API key, not OPENAI_API_KEY."""

    def __init__(
        self,
        admin_api_key: str | None = None,
        budget_usd: float | None = None,
        transport: httpx.BaseTransport | None = None,
        costs_url: str | None = None,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: int | None = None,
        cache: TimedValueCache[object] | None = None,
    ) -> None:
        settings = get_settings()
        if admin_api_key is None:
            secret = settings.openai_admin_api_key
            admin_api_key = secret.get_secret_value() if secret is not None else ""
        self._admin_api_key = (admin_api_key or "").strip()
        self._budget_usd = settings.openai_budget_usd if budget_usd is None else budget_usd
        self._transport = transport
        self._costs_url = costs_url or COSTS_URL
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.openai_account_timeout_seconds
        )
        self._cache_ttl = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else settings.openai_account_cache_ttl_seconds
        )
        self._cache = cache if cache is not None else get_provider_usage_cache()

    async def get_usage(self, *, force_refresh: bool = False) -> OpenAIAccountUsage:
        if not force_refresh:
            cached = self._cache.get(ACCOUNT_CACHE_KEY)
            if isinstance(cached, OpenAIAccountUsage):
                return cached
        snapshot = await self._fetch_usage()
        self._cache.set(ACCOUNT_CACHE_KEY, snapshot, self._cache_ttl)
        return snapshot

    async def _fetch_usage(self) -> OpenAIAccountUsage:
        budget = self._budget_usd
        if not self._admin_api_key:
            return openai_account_unavailable(
                status="not_configured",
                message=NOT_CONFIGURED_MESSAGE,
                budget_usd=budget,
            )
        period_start = _month_start_utc()
        try:
            payload = await self._get_costs(period_start)
        except Exception:
            return openai_account_unavailable(budget_usd=budget)
        if payload is None:
            return openai_account_unavailable(budget_usd=budget)
        spend = sum_openai_costs_usd(payload)
        if spend is None:
            return openai_account_unavailable(budget_usd=budget)
        percentage = usage_percentage(spend, budget)
        return OpenAIAccountUsage(
            available=True,
            status="ok",
            spend_usd=round(spend, 6),
            budget_usd=budget,
            usage_percentage=percentage,
            warning_level=warning_level(percentage),
            period_start=period_start,
            last_updated=datetime.now(UTC),
            message=None,
        )

    async def _get_costs(self, period_start: datetime) -> dict[str, Any] | None:
        params: dict[str, str | int] = {
            "start_time": int(period_start.timestamp()),
            "limit": 31,
        }
        headers = {
            "Authorization": f"Bearer {self._admin_api_key}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
            transport=self._transport,
        ) as client:
            buckets: list[dict[str, Any]] = []
            for _ in range(4):
                response = await client.get(self._costs_url, params=params, headers=headers)
                if response.status_code != 200:
                    return None
                try:
                    payload = response.json()
                except ValueError:
                    return None
                if not isinstance(payload, dict):
                    return None
                data = payload.get("data")
                if isinstance(data, list):
                    buckets.extend(item for item in data if isinstance(item, dict))
                next_page = payload.get("next_page")
                if payload.get("has_more") and isinstance(next_page, str) and next_page:
                    params = {"start_time": int(period_start.timestamp()), "limit": 31, "page": next_page}
                    continue
                return {"data": buckets, "object": payload.get("object", "page")}
        return {"data": buckets}


def openai_account_unavailable(
    *,
    status: str = "unavailable",
    message: str = UNAVAILABLE_MESSAGE,
    budget_usd: float | None = None,
) -> OpenAIAccountUsage:
    settings_budget = budget_usd
    if settings_budget is None:
        settings_budget = get_settings().openai_budget_usd
    return OpenAIAccountUsage(
        available=False,
        status=status,  # type: ignore[arg-type]
        spend_usd=None,
        budget_usd=settings_budget,
        message=message,
        last_updated=datetime.now(UTC),
    )


def sum_openai_costs_usd(payload: dict[str, Any]) -> float | None:
    data = payload.get("data")
    if not isinstance(data, list):
        return None
    total = 0.0
    for bucket in data:
        if not isinstance(bucket, dict):
            continue
        results = bucket.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            amount = _usd_amount(result)
            if amount is None:
                continue
            total += amount
    return round(total, 8)


def _usd_amount(result: Any) -> float | None:
    if not isinstance(result, dict):
        return None
    amount = result.get("amount")
    if not isinstance(amount, dict):
        return None
    currency = str(amount.get("currency") or "usd").lower()
    if currency != "usd":
        return None
    value = amount.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _month_start_utc(now: datetime | None = None) -> datetime:
    current = now or datetime.now(UTC)
    return datetime(current.year, current.month, 1, tzinfo=UTC)

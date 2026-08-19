from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.usage import RainforestAccountUsage, RainforestUsagePoint
from app.usage.ledger import get_usage_ledger
from app.usage.levels import usage_percentage, warning_level
from app.usage.provider_cache import TimedValueCache, get_provider_usage_cache

ACCOUNT_CACHE_KEY = "rainforest-account"
UNAVAILABLE_MESSAGE = "Usage temporarily unavailable"
NOT_CONFIGURED_MESSAGE = "Rainforest account usage is not configured."


def map_rainforest_account(
    payload: dict[str, Any],
    *,
    last_updated: datetime | None = None,
) -> RainforestAccountUsage:
    info = payload.get("account_info")
    raw = info if isinstance(info, dict) else payload

    used = _optional_int(raw.get("credits_used"))
    limit = _optional_int(raw.get("credits_limit"))
    remaining = _optional_int(raw.get("credits_remaining"))
    if remaining is None and used is not None and limit is not None:
        remaining = max(limit - used, 0)
    if limit is None and used is not None and remaining is not None:
        limit = used + remaining
    percentage = usage_percentage(used, limit)
    reset_at = _parse_datetime(raw.get("credits_reset_at"))
    history = _map_usage_history(raw.get("usage_history"))

    return RainforestAccountUsage(
        available=True,
        status="ok",
        credits_used=used,
        credits_limit=limit,
        credits_remaining=remaining,
        usage_percentage=percentage,
        warning_level=warning_level(percentage),
        reset_at=reset_at,
        usage_history=history,
        last_updated=last_updated or datetime.now(UTC),
        message=None,
    )


def rainforest_account_unavailable(
    *,
    status: str = "unavailable",
    message: str = UNAVAILABLE_MESSAGE,
) -> RainforestAccountUsage:
    return RainforestAccountUsage(
        available=False,
        status=status,  # type: ignore[arg-type]
        message=message,
        last_updated=datetime.now(UTC),
    )


class RainforestAccountClient:
    """GET /account — documented as free. Must never increment paid product/search counters."""

    def __init__(
        self,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        account_url: str | None = None,
        timeout_seconds: float | None = None,
        cache_ttl_seconds: int | None = None,
        cache: TimedValueCache[object] | None = None,
    ) -> None:
        settings = get_settings()
        if api_key is None:
            secret = settings.rainforest_api_key
            api_key = secret.get_secret_value() if secret is not None else ""
        self._api_key = (api_key or "").strip()
        self._transport = transport
        self._account_url = account_url or settings.rainforest_account_url
        self._timeout = (
            timeout_seconds if timeout_seconds is not None else settings.rainforest_account_timeout_seconds
        )
        self._cache_ttl = (
            cache_ttl_seconds
            if cache_ttl_seconds is not None
            else settings.rainforest_account_cache_ttl_seconds
        )
        self._cache = cache if cache is not None else get_provider_usage_cache()

    async def get_usage(self, *, force_refresh: bool = False) -> RainforestAccountUsage:
        if not force_refresh:
            cached = self._cache.get(ACCOUNT_CACHE_KEY)
            if isinstance(cached, RainforestAccountUsage):
                return cached

        snapshot = await self._fetch_usage()
        self._cache.set(ACCOUNT_CACHE_KEY, snapshot, self._cache_ttl)
        return snapshot

    async def _fetch_usage(self) -> RainforestAccountUsage:
        if not self._api_key:
            return rainforest_account_unavailable(
                status="not_configured",
                message=NOT_CONFIGURED_MESSAGE,
            )
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                transport=self._transport,
            ) as client:
                response = await client.get(self._account_url, params={"api_key": self._api_key})
        except httpx.HTTPError:
            return rainforest_account_unavailable()

        get_usage_ledger().record_rainforest_account_lookup()

        if response.status_code != 200:
            return rainforest_account_unavailable()
        try:
            payload = response.json()
        except ValueError:
            return rainforest_account_unavailable()
        if not isinstance(payload, dict):
            return rainforest_account_unavailable()
        try:
            return map_rainforest_account(payload)
        except Exception:
            return rainforest_account_unavailable()


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return datetime.fromtimestamp(value, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _map_usage_history(value: Any) -> list[RainforestUsagePoint]:
    if not isinstance(value, list):
        return []
    points: list[RainforestUsagePoint] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if isinstance(item.get("credits_total_per_day"), dict):
            points.extend(_map_monthly_history(item))
            continue
        date = item.get("date") or item.get("day") or item.get("timestamp")
        used = _optional_int(item.get("credits_used") if "credits_used" in item else item.get("credits"))
        if not isinstance(date, str) or used is None:
            continue
        points.append(RainforestUsagePoint(date=date, credits_used=used))
    return points[-14:]


def _map_monthly_history(item: dict[str, Any]) -> list[RainforestUsagePoint]:
    year = _optional_int(item.get("year"))
    month = _optional_int(item.get("month_number"))
    per_day = item.get("credits_total_per_day")
    if year is None or month is None or not isinstance(per_day, dict):
        return []
    points: list[RainforestUsagePoint] = []
    for day_key, credits in per_day.items():
        day = _optional_int(day_key)
        used = _optional_int(credits)
        if day is None or used is None or used <= 0:
            continue
        points.append(
            RainforestUsagePoint(date=f"{year:04d}-{month:02d}-{day:02d}", credits_used=used)
        )
    points.sort(key=lambda point: point.date)
    return points

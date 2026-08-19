from copy import deepcopy
from time import monotonic
from typing import Any


class KeyedTtlCache:
    """In-process TTL cache keyed by a stable string. Replace later with Redis if needed."""

    def __init__(self, ttl_seconds: int, clock=monotonic) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if self._clock() >= expires_at:
            self._data.pop(key, None)
            return None
        return deepcopy(value)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (self._clock() + max(self.ttl_seconds, 0), deepcopy(value))

    def clear(self) -> None:
        self._data.clear()


def product_cache_key(provider: str, marketplace: str, asin: str) -> str:
    return f"{provider}|{marketplace}|{asin}"

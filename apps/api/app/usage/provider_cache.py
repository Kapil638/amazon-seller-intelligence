from time import monotonic
from typing import Generic, TypeVar

T = TypeVar("T")


class TimedValueCache(Generic[T]):
    """In-process TTL cache for provider-account snapshots."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[float, T]] = {}

    def get(self, key: str) -> T | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if monotonic() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: T, ttl_seconds: int) -> None:
        self._data[key] = (monotonic() + max(ttl_seconds, 0), value)

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


_PROVIDER_CACHE: TimedValueCache[object] = TimedValueCache()


def get_provider_usage_cache() -> TimedValueCache[object]:
    return _PROVIDER_CACHE

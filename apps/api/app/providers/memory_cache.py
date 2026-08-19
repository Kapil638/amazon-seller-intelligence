from time import monotonic
from typing import Any

from app.models.product import Product


class MemoryTtlCache:
    """Small in-process TTL cache. Replace later with Redis if needed."""

    def __init__(self, ttl_seconds: int = 600) -> None:
        self.ttl_seconds = ttl_seconds
        self._data: dict[tuple[str, str], tuple[float, Product]] = {}

    def get(self, asin: str, marketplace: str) -> Product | None:
        item = self._data.get((asin, marketplace))
        if item is None:
            return None
        expires_at, product = item
        if monotonic() >= expires_at:
            self._data.pop((asin, marketplace), None)
            return None
        return product.model_copy(deep=True)

    def set(self, asin: str, marketplace: str, product: Product) -> None:
        self._data[(asin, marketplace)] = (
            monotonic() + self.ttl_seconds,
            product.model_copy(deep=True),
        )


class MemoryTtlValueCache:
    """Hash-keyed in-process TTL cache for AI results."""

    def __init__(self, ttl_seconds: int = 2700) -> None:
        self.ttl_seconds = ttl_seconds
        self._data: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        item = self._data.get(key)
        if item is None:
            return None
        expires_at, value = item
        if monotonic() >= expires_at:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (monotonic() + self.ttl_seconds, value)

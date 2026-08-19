from functools import lru_cache

from app.core.config import get_settings
from app.search.base import AmazonSearchProvider
from app.search.mock import MockAmazonSearchProvider
from app.search.rainforest_search_provider import RainforestAmazonSearchProvider


@lru_cache
def get_search_provider() -> AmazonSearchProvider:
    settings = get_settings()
    if settings.product_provider == "mock":
        return MockAmazonSearchProvider()
    return RainforestAmazonSearchProvider()

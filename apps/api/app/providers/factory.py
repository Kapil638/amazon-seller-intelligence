from functools import lru_cache

from app.core.config import get_settings
from app.providers.amazon_public import AmazonPublicProductDataProvider
from app.providers.base import ProductDataProvider
from app.providers.mock import MockProductDataProvider
from app.providers.rainforest import RainforestProductDataProvider


@lru_cache
def get_product_provider() -> ProductDataProvider:
    settings = get_settings()
    if settings.product_provider == "mock":
        return MockProductDataProvider()
    if settings.product_provider == "rainforest":
        return RainforestProductDataProvider()
    if settings.product_provider == "amazon_public":
        return AmazonPublicProductDataProvider()
    raise ValueError(f"Unknown product provider: {settings.product_provider}")

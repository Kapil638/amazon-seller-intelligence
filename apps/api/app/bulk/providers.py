from app.ai.base import AIProvider
from app.ai.mock import MockAIProvider
from app.bulk.mock_product import BulkMockProductDataProvider
from app.core.config import get_settings
from app.core.exceptions import BulkLiveProviderForbiddenError
from app.providers.base import ProductDataProvider

LIVE_PRODUCT_PROVIDERS = frozenset({"rainforest", "amazon_public"})
LIVE_AI_PROVIDERS = frozenset({"openai"})


def assert_bulk_live_calls_allowed(kind: str, provider_name: str) -> None:
    settings = get_settings()
    live = provider_name in LIVE_PRODUCT_PROVIDERS or provider_name in LIVE_AI_PROVIDERS
    if live and not settings.bulk_live_provider_calls_enabled:
        raise BulkLiveProviderForbiddenError(
            f"Bulk analysis refused to use live {kind} provider '{provider_name}'. "
            "BULK_LIVE_PROVIDER_CALLS_ENABLED is false. Mock providers are required "
            "so Rainforest and OpenAI credits are not consumed."
        )


def get_bulk_product_provider() -> ProductDataProvider:
    settings = get_settings()
    name = settings.bulk_product_provider
    assert_bulk_live_calls_allowed("product", name)
    if name == "mock":
        return BulkMockProductDataProvider()
    if name == "rainforest":
        from app.providers.rainforest import RainforestProductDataProvider

        return RainforestProductDataProvider()
    if name == "amazon_public":
        from app.providers.amazon_public import AmazonPublicProductDataProvider

        return AmazonPublicProductDataProvider()
    raise ValueError(f"Unknown bulk product provider: {name}")


def get_bulk_ai_provider() -> AIProvider:
    settings = get_settings()
    name = settings.bulk_ai_provider
    assert_bulk_live_calls_allowed("AI", name)
    if name == "mock":
        return MockAIProvider()
    if name == "openai":
        from app.ai.openai_provider import OpenAIProvider

        return OpenAIProvider()
    raise ValueError(f"Unknown bulk AI provider: {name}")

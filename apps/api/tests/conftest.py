import os

os.environ["PRODUCT_PROVIDER"] = "mock"

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.providers.factory import get_product_provider
from app.ai.factory import get_ai_provider
from app.search.factory import get_search_provider
from app.usage.ledger import get_usage_ledger
from app.usage.provider_cache import get_provider_usage_cache
from app.bulk.runtime import reset_bulk_runtime

get_settings.cache_clear()
get_product_provider.cache_clear()
get_ai_provider.cache_clear()
get_search_provider.cache_clear()
get_usage_ledger().reset()
get_provider_usage_cache().clear()
reset_bulk_runtime()

from app.main import app  # noqa: E402


@pytest.fixture(autouse=True)
def _force_mock_provider() -> Generator[None, None, None]:
    os.environ["PRODUCT_PROVIDER"] = "mock"
    get_settings.cache_clear()
    get_product_provider.cache_clear()
    get_ai_provider.cache_clear()
    get_search_provider.cache_clear()
    get_usage_ledger().reset()
    get_provider_usage_cache().clear()
    reset_bulk_runtime()
    yield
    get_settings.cache_clear()
    get_product_provider.cache_clear()
    get_ai_provider.cache_clear()
    get_search_provider.cache_clear()
    get_usage_ledger().reset()
    get_provider_usage_cache().clear()
    reset_bulk_runtime()


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client

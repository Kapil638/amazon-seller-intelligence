import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.exceptions import (
    ProductFetchBlockedError,
    ProductFetchFailedError,
    ProductNotFoundError,
    ProviderConfigurationError,
)
from app.parsers.rainforest_product_mapper import map_rainforest_product
from app.providers.factory import get_product_provider
from app.providers.memory_cache import MemoryTtlCache
from app.providers.rainforest import RainforestProductDataProvider

FIXTURES = Path(__file__).parent / "fixtures" / "rainforest"
TEST_KEY = "test-rainforest-key"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_mapper_maps_documented_product_fields() -> None:
    product = map_rainforest_product(load_fixture("product.json"), "B07J4TNYV8", "amazon.in")
    assert product.asin == "B07J4TNYV8"
    assert product.marketplace == "amazon.in"
    assert product.title.startswith("AKASO V50 Elite")
    assert product.brand == "AKASO"
    assert product.price is not None
    assert product.price.amount == 139.99
    assert product.price.currency == "INR"
    assert product.rating == 4.4
    assert product.review_count == 5080
    assert len(product.bullet_points) == 3
    assert product.description == "The text from the main product description block."
    assert len(product.images) == 2
    assert product.images[0].url == "https://m.media-amazon.com/images/I/71kM3BRnDaL.jpg"
    assert product.images[0].is_main is True
    assert product.images[1].url.startswith("https://m.media-amazon.com/")
    assert product.videos == []
    assert product.category == "Electronics > Camera & Photo > Video > Sports & Action Video Cameras"
    assert product.bsr is not None
    assert product.bsr.rank == 32614
    assert product.bsr.category == "Electronics"
    assert product.availability == "In Stock"
    assert product.seller is not None
    assert product.seller.name == "AKASO OUTDOOR"
    assert product.seller.id == "A2SITDWYE2UYD"
    assert product.seller.is_fba is True
    assert len(product.variations) == 2
    assert product.variations[0].attributes["Style"] == "With 128GB MicroSD Card"


def test_mapper_leaves_missing_fields_null_or_empty() -> None:
    product = map_rainforest_product(load_fixture("missing_fields.json"), "B0MISSING1", "amazon.in")
    assert product.title
    assert product.brand is None
    assert product.price is None
    assert product.rating is None
    assert product.review_count is None
    assert product.bullet_points == []
    assert product.images == []
    assert product.videos == []
    assert product.description is None
    assert product.bsr is None
    assert product.seller is None
    assert product.variations == []
    assert product.availability is None
    assert product.category is None


def test_mapper_does_not_invent_price_without_buybox() -> None:
    payload = {
        "product": {
            "title": "No buybox listing",
            "asin": "B0NOPRICE1",
        }
    }
    product = map_rainforest_product(payload, "B0NOPRICE1", "amazon.in")
    assert product.price is None


def test_media_mapping_prefers_main_and_highest_quality() -> None:
    product = map_rainforest_product(load_fixture("media.json"), "B0MEDIA001", "amazon.in")
    assert [image.url for image in product.images] == [
        "https://m.media-amazon.com/images/I/71MAINIMAGE.jpg",
        "https://m.media-amazon.com/images/I/81SECONDIMG.jpg",
    ]
    assert product.images[0].is_main is True
    assert product.images[0].variant == "MAIN"
    assert all("play-icon-overlay" not in image.url for image in product.images)
    assert len(product.videos) == 2
    playable = next(item for item in product.videos if item.video_url)
    assert playable.title == "Product overview"
    assert playable.duration_seconds == 16
    assert playable.video_url and playable.video_url.endswith(".mp4")
    overlay = next(item for item in product.videos if item.video_url is None)
    assert overlay.thumbnail_url
    assert "play-icon-overlay" not in overlay.thumbnail_url


def test_media_mapping_handles_missing_images_and_videos() -> None:
    product = map_rainforest_product(
        {"product": {"title": "No media", "asin": "B0NOMEDIA01"}},
        "B0NOMEDIA01",
        "amazon.in",
    )
    assert product.images == []
    assert product.videos == []


def test_listing_intelligence_counts_still_images_not_videos() -> None:
    from app.models.product import ProductVideo
    from app.services.listing_analysis_service import ListingAnalysisService
    from tests.test_listing_analysis import make_product

    product = make_product(
        videos=[
            ProductVideo(title="Clip", thumbnail_url="https://m.media-amazon.com/images/I/71VID.jpg"),
            ProductVideo(thumbnail_url="https://m.media-amazon.com/images/I/51OVER.jpg"),
        ]
    )
    analysis = ListingAnalysisService().analyze(product)
    assert analysis.sections.images.metrics["image_count"] == 5
    assert "NO_IMAGES" not in {item.code for item in analysis.findings}


@pytest.mark.asyncio
async def test_provider_uses_httpx_params_and_normalizes_response() -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["api_key"] = request.url.params.get("api_key", "")
        captured["type"] = request.url.params.get("type", "")
        captured["amazon_domain"] = request.url.params.get("amazon_domain", "")
        captured["asin"] = request.url.params.get("asin", "")
        return httpx.Response(200, json=load_fixture("product.json"))

    provider = RainforestProductDataProvider(
        api_key=TEST_KEY,
        cache=MemoryTtlCache(ttl_seconds=60),
        transport=httpx.MockTransport(handler),
    )
    product = await provider.get_product("B07J4TNYV8", "amazon.in")
    assert product is not None
    assert product.title.startswith("AKASO")
    assert provider.name == "rainforest"
    assert captured == {
        "api_key": TEST_KEY,
        "type": "product",
        "amazon_domain": "amazon.in",
        "asin": "B07J4TNYV8",
    }


@pytest.mark.asyncio
async def test_provider_maps_not_found_payload() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_fixture("not_found.json"))

    provider = RainforestProductDataProvider(
        api_key=TEST_KEY,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProductNotFoundError):
        await provider.get_product("B0NOTFOUND", "amazon.in")


@pytest.mark.asyncio
async def test_provider_maps_http_429_as_blocked() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"request_info": {"success": False}})

    provider = RainforestProductDataProvider(
        api_key=TEST_KEY,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProductFetchBlockedError):
        await provider.get_product("B07J4TNYV8", "amazon.in")


@pytest.mark.asyncio
async def test_provider_maps_http_500_as_fetch_failure() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="error")

    provider = RainforestProductDataProvider(
        api_key=TEST_KEY,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProductFetchFailedError):
        await provider.get_product("B07J4TNYV8", "amazon.in")


@pytest.mark.asyncio
async def test_missing_api_key_is_configuration_error() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Must not call Rainforest without a key")

    provider = RainforestProductDataProvider(
        api_key="",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderConfigurationError, match="RAINFOREST_API_KEY"):
        await provider.get_product("B07J4TNYV8", "amazon.in")


@pytest.mark.asyncio
async def test_exceptions_do_not_include_api_key() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"request_info": {"success": False, "message": TEST_KEY}})

    provider = RainforestProductDataProvider(
        api_key=TEST_KEY,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ProviderConfigurationError) as exc_info:
        await provider.get_product("B07J4TNYV8", "amazon.in")
    assert TEST_KEY not in str(exc_info.value)


def test_lookup_source_metadata_for_rainforest_provider(client: TestClient) -> None:
    from app.api.routes.products import get_product_service
    from app.main import app
    from app.services.product_service import ProductService

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=load_fixture("product.json"))

    service = ProductService(
        provider=RainforestProductDataProvider(
            api_key=TEST_KEY,
            transport=httpx.MockTransport(handler),
        )
    )
    app.dependency_overrides[get_product_service] = lambda: service
    try:
        response = client.get("/api/v1/products/B07J4TNYV8?marketplace=amazon.in")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "rainforest"
    assert body["product"]["title"]
    assert "api_key" not in json.dumps(body)


def test_demo_asin_stays_mock_even_with_rainforest_provider(client: TestClient) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Demo ASINs must not call Rainforest")

    from app.api.routes.products import get_product_service
    from app.main import app
    from app.services.product_service import ProductService

    service = ProductService(
        provider=RainforestProductDataProvider(
            api_key=TEST_KEY,
            transport=httpx.MockTransport(handler),
        )
    )
    app.dependency_overrides[get_product_service] = lambda: service
    try:
        response = client.get("/api/v1/products/B0TEST0001")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["meta"]["source"] == "mock"


def test_missing_key_returns_503(client: TestClient) -> None:
    from app.api.routes.products import get_product_service
    from app.main import app
    from app.services.product_service import ProductService

    service = ProductService(provider=RainforestProductDataProvider(api_key=""))
    app.dependency_overrides[get_product_service] = lambda: service
    try:
        response = client.get("/api/v1/products/B07J4TNYV8")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503
    assert "RAINFOREST_API_KEY" in response.json()["detail"]
    assert "test-rainforest-key" not in response.text


def test_factory_selects_rainforest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRODUCT_PROVIDER", "rainforest")
    monkeypatch.setenv("RAINFOREST_API_KEY", TEST_KEY)
    get_settings.cache_clear()
    get_product_provider.cache_clear()
    try:
        provider = get_product_provider()
        assert provider.name == "rainforest"
    finally:
        get_settings.cache_clear()
        get_product_provider.cache_clear()

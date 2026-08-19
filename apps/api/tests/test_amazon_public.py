from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.core.exceptions import (
    ProductFetchBlockedError,
    ProductNotFoundError,
    ProductParseFailedError,
)
from app.parsers.amazon_product_parser import AmazonProductParser
from app.providers.amazon_public import AmazonPublicProductDataProvider
from app.providers.factory import get_product_provider
from app.providers.memory_cache import MemoryTtlCache

FIXTURES = Path(__file__).parent / "fixtures" / "amazon"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_title_price_rating_reviews_from_json_ld() -> None:
    product = AmazonProductParser().parse(load_fixture("product.html"), "B0TEST0001", "amazon.in")
    assert product.title == "AuroraGlow Vitamin D3 Softgels, 60 Count"
    assert product.price is not None
    assert product.price.amount == 449
    assert product.price.currency == "INR"
    assert product.rating == 4.4
    assert product.review_count == 1284
    assert product.brand == "Lumora Wellness"
    assert product.marketplace == "amazon.in"
    assert product.asin == "B0TEST0001"


def test_bullets_and_images_extraction() -> None:
    product = AmazonProductParser().parse(load_fixture("product.html"), "B0TEST0001", "amazon.in")
    assert len(product.bullet_points) == 3
    assert "Make sure this fits" not in " ".join(product.bullet_points)
    assert len(product.images) >= 1
    assert product.images[0].url.startswith("https://placehold.co/")
    assert product.availability == "In Stock"
    assert product.seller is not None
    assert product.bsr is not None
    assert product.bsr.rank == 1842
    assert product.variations


def test_dom_fallback_without_json_ld() -> None:
    product = AmazonProductParser().parse(
        load_fixture("product_dom_only.html"),
        "B0TEST0002",
        "amazon.in",
    )
    assert product.title.startswith("NimbusFoam")
    assert product.price is not None
    assert product.price.amount == 1299
    assert product.rating == 4.2
    assert product.review_count == 856
    assert len(product.bullet_points) == 2
    assert product.images
    assert product.brand == "Restora Home"


def test_optional_missing_fields_are_null_or_empty() -> None:
    product = AmazonProductParser().parse(
        load_fixture("missing_fields.html"),
        "B0TEST0003",
        "amazon.in",
    )
    assert product.title
    assert product.brand is None
    assert product.price is None
    assert product.rating is None
    assert product.review_count is None
    assert product.bullet_points == []
    assert product.images == []
    assert product.description is None
    assert product.bsr is None
    assert product.seller is None


def test_not_found_page() -> None:
    with pytest.raises(ProductNotFoundError):
        AmazonProductParser().parse(load_fixture("not_found.html"), "B0MISSING1", "amazon.in")


def test_captcha_page_is_blocked() -> None:
    with pytest.raises(ProductFetchBlockedError):
        AmazonProductParser().parse(load_fixture("captcha.html"), "B0BLOCKED01", "amazon.in")


def test_missing_title_is_parse_failure() -> None:
    with pytest.raises(ProductParseFailedError):
        AmazonProductParser().parse("<html><body>No title here</body></html>", "B0NOTITLE1", "amazon.in")


@pytest.mark.asyncio
async def test_provider_normalizes_mocked_http_response() -> None:
    html = load_fixture("product.html")

    def handler(request: httpx.Request) -> httpx.Response:
        assert "/dp/B0TEST0001" in str(request.url)
        return httpx.Response(200, text=html)

    provider = AmazonPublicProductDataProvider(
        cache=MemoryTtlCache(ttl_seconds=60),
        transport=httpx.MockTransport(handler),
    )
    product = await provider.get_product("B0TEST0001", "amazon.in")
    assert product is not None
    assert product.title.startswith("AuroraGlow")
    assert provider.name == "amazon_public"


@pytest.mark.asyncio
async def test_provider_maps_http_404() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    provider = AmazonPublicProductDataProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ProductNotFoundError):
        await provider.get_product("B0MISSING1", "amazon.in")


@pytest.mark.asyncio
async def test_provider_maps_http_503_as_blocked() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="throttled")

    provider = AmazonPublicProductDataProvider(transport=httpx.MockTransport(handler))
    with pytest.raises(ProductFetchBlockedError):
        await provider.get_product("B0BLOCKED01", "amazon.in")


def test_lookup_source_metadata_for_public_provider(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html = load_fixture("product.html")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=html)

    from app.api.routes.products import get_product_service
    from app.main import app
    from app.services.product_service import ProductService

    service = ProductService(
        provider=AmazonPublicProductDataProvider(transport=httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_product_service] = lambda: service
    try:
        response = client.get("/api/v1/products/B0PUBLIC01?marketplace=amazon.in")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["source"] == "amazon_public"
    assert body["product"]["title"]
    get_settings.cache_clear()
    get_product_provider.cache_clear()


def test_demo_asin_stays_mock_even_with_public_provider(client: TestClient) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("Demo ASINs must not call Amazon.in")

    from app.api.routes.products import get_product_service
    from app.main import app
    from app.services.product_service import ProductService

    service = ProductService(
        provider=AmazonPublicProductDataProvider(transport=httpx.MockTransport(handler))
    )
    app.dependency_overrides[get_product_service] = lambda: service
    try:
        response = client.get("/api/v1/products/B0TEST0001")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["meta"]["source"] == "mock"

import asyncio
import json
import time

import pytest
from fastapi.testclient import TestClient

from app.analytics.competitor_rules import compare_listings
from app.analytics.listing_rules import SCORE_VERSION
from app.core.exceptions import (
    CompetitorValidationError,
    NoCompetitorsRetrievedError,
    ProductFetchFailedError,
)
from app.models.competitor_comparison import ComparedListing, COMPARISON_VERSION, GapSeverity
from app.models.product import Image, Price, Product
from app.providers.base import ProductDataProvider, ProviderCapabilities
from app.services.competitor_comparison_service import CompetitorComparisonService
from app.services.listing_analysis_service import ListingAnalysisService
from app.services.product_service import ProductService
from tests.test_listing_analysis import make_product


class FakeCatalogProvider(ProductDataProvider):
    def __init__(
        self,
        products: dict[str, Product] | None = None,
        missing: set[str] | None = None,
        failures: set[str] | None = None,
        delay: float = 0,
    ) -> None:
        self.products = products or {}
        self.missing = missing or set()
        self.failures = failures or set()
        self.delay = delay
        self.calls: list[str] = []
        self.started_at: dict[str, float] = {}

    @property
    def name(self) -> str:
        return "rainforest"

    @property
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(product_details=True)

    async def get_product(self, asin: str, marketplace: str) -> Product | None:
        self.calls.append(asin)
        self.started_at[asin] = time.monotonic()
        if self.delay:
            await asyncio.sleep(self.delay)
        if asin in self.failures:
            raise ProductFetchFailedError(asin, marketplace, "upstream failed")
        if asin in self.missing:
            return None
        product = self.products.get(asin)
        if product is None:
            return None
        return product


def _row(product: Product) -> ComparedListing:
    return ComparedListing(product=product, analysis=ListingAnalysisService().analyze(product))


def _metric(comparison, key: str):
    return next(item for item in comparison.metrics if item.key == key)


def test_one_competitor_comparison() -> None:
    target = _row(make_product(asin="B0TARGET01", price=Price(amount=899, currency="INR")))
    competitor = _row(make_product(
        asin="B0COMP0001",
        price=Price(amount=799, currency="INR"),
        rating=4.5,
        review_count=5430,
        images=[Image(url=f"https://placehold.co/800?text={i}") for i in range(8)],
    ))
    comparison = compare_listings(target, [competitor])
    assert comparison.summary.retrieved_count == 1
    assert _metric(comparison, "listing_score").target_value == target.analysis.overall_score
    assert _metric(comparison, "listing_score").competitor_values["B0COMP0001"] == competitor.analysis.overall_score


def test_three_competitors_comparison() -> None:
    target = _row(make_product(asin="B0TARGET01"))
    competitors = [_row(make_product(asin=f"B0COMP000{i}")) for i in (1, 2, 3)]
    comparison = compare_listings(target, competitors)
    assert comparison.summary.retrieved_count == 3
    assert len(comparison.metrics[0].competitor_values) == 3


def test_price_and_percentage_difference() -> None:
    target = _row(make_product(asin="B0TARGET01", price=Price(amount=899, currency="INR")))
    competitor = _row(make_product(asin="B0COMP0001", price=Price(amount=799, currency="INR")))
    comparison = compare_listings(target, [competitor])
    delta = comparison.price_deltas[0]
    assert delta.absolute_difference == -100
    assert delta.percentage_difference == -11.1
    assert _metric(comparison, "price").target_value == 899
    assert _metric(comparison, "price").competitor_values["B0COMP0001"] == 799


def test_rating_and_review_comparison() -> None:
    target = _row(make_product(asin="B0TARGET01", rating=4.2, review_count=842))
    competitor = _row(make_product(asin="B0COMP0001", rating=4.5, review_count=5430))
    comparison = compare_listings(target, [competitor])
    assert _metric(comparison, "rating").competitor_values["B0COMP0001"] == 4.5
    assert _metric(comparison, "review_count").competitor_values["B0COMP0001"] == 5430
    review_gap = next(item for item in comparison.gaps if item.dimension == "review_count")
    assert review_gap.severity == GapSeverity.HIGH
    rating_gap = next(item for item in comparison.gaps if item.dimension == "rating")
    assert rating_gap.target_value == 4.2
    assert "visible reviews" in review_gap.evidence


def test_image_and_listing_score_comparison() -> None:
    target = _row(make_product(asin="B0TARGET01", images=[Image(url=f"https://x/{i}") for i in range(5)]))
    competitor = _row(make_product(asin="B0COMP0001", images=[Image(url=f"https://x/{i}") for i in range(8)]))
    comparison = compare_listings(target, [competitor])
    assert _metric(comparison, "image_count").target_value == 5
    assert _metric(comparison, "image_count").competitor_values["B0COMP0001"] == 8
    image_gap = next(item for item in comparison.gaps if item.dimension == "images")
    assert image_gap.severity == GapSeverity.HIGH
    assert image_gap.direction == "below"


def test_missing_fields_are_explicit() -> None:
    target = _row(make_product(asin="B0TARGET01", price=None, rating=None, review_count=None, bsr=None))
    competitor = _row(make_product(asin="B0COMP0001", price=None, rating=None, review_count=None, bsr=None))
    comparison = compare_listings(target, [competitor])
    assert comparison.price_deltas == []
    assert _metric(comparison, "price").target_value is None
    assert _metric(comparison, "rating").target_value is None
    assert not any(gap.dimension == "price" for gap in comparison.gaps)
    assert not any(gap.dimension == "review_count" for gap in comparison.gaps)


def test_gap_severity_thresholds() -> None:
    target = _row(make_product(asin="B0TARGET01", review_count=100, rating=4.0, images=[Image(url="https://x/1")]))
    high = _row(make_product(asin="B0COMPHI01", review_count=600, rating=4.6, images=[Image(url=f"https://x/{i}") for i in range(5)]))
    comparison = compare_listings(target, [high])
    assert next(item for item in comparison.gaps if item.dimension == "review_count").severity == GapSeverity.HIGH
    assert next(item for item in comparison.gaps if item.dimension == "rating").severity == GapSeverity.HIGH
    assert next(item for item in comparison.gaps if item.dimension == "images").severity == GapSeverity.HIGH


def test_same_listing_analysis_service_and_unchanged_target_score() -> None:
    product = make_product()
    expected = ListingAnalysisService().analyze(product)
    comparison = compare_listings(_row(product), [_row(make_product(asin="B0COMP0001"))])
    assert comparison.summary.target_listing_score == expected.overall_score
    assert _metric(comparison, "listing_score").target_value == expected.overall_score


def test_comparison_accepts_v2_foundation_fields_without_changing_v1_score() -> None:
    from app.models.product import APlusContent, BSR

    target = make_product(
        asin="B0TARGET01",
        bsr_ranks=[BSR(rank=10, category="Leaf")],
        a_plus=APlusContent(has_a_plus_content=True),
        recent_sales_text="50+ bought in past month",
    )
    competitor = make_product(asin="B0COMP0001", videos_count=2)
    expected = ListingAnalysisService().analyze(target)
    comparison = compare_listings(_row(target), [_row(competitor)])
    assert comparison.summary.target_listing_score == expected.overall_score


@pytest.mark.asyncio
async def test_service_one_and_three_competitors() -> None:
    target = make_product(asin="B0TARGET01")
    catalog = {
        "B0COMP0001": make_product(asin="B0COMP0001"),
        "B0COMP0002": make_product(asin="B0COMP0002"),
        "B0COMP0003": make_product(asin="B0COMP0003"),
    }
    service = CompetitorComparisonService(products=ProductService(provider=FakeCatalogProvider(catalog)))
    one = await service.compare(target, ["B0COMP0001"])
    assert len(one.competitors) == 1
    three = await service.compare(target, ["B0COMP0001", "B0COMP0002", "B0COMP0003"])
    assert len(three.competitors) == 3
    assert three.meta.comparison_version == COMPARISON_VERSION
    assert three.meta.score_version == SCORE_VERSION


@pytest.mark.asyncio
async def test_validation_max_invalid_duplicate_and_target() -> None:
    target = make_product(asin="B0TARGET01")
    service = CompetitorComparisonService(products=ProductService(provider=FakeCatalogProvider({})))
    with pytest.raises(CompetitorValidationError, match="at most three"):
        await service.compare(target, ["B0COMP0001", "B0COMP0002", "B0COMP0003", "B0COMP0004"])
    with pytest.raises(CompetitorValidationError, match="Invalid ASIN"):
        await service.compare(target, ["BADASIN"])
    with pytest.raises(CompetitorValidationError, match="unique"):
        await service.compare(target, ["B0COMP0001", "B0COMP0001"])
    with pytest.raises(CompetitorValidationError, match="cannot be entered"):
        await service.compare(target, ["B0TARGET01"])


@pytest.mark.asyncio
async def test_concurrent_retrieval() -> None:
    catalog = {
        "B0COMP0001": make_product(asin="B0COMP0001"),
        "B0COMP0002": make_product(asin="B0COMP0002"),
        "B0COMP0003": make_product(asin="B0COMP0003"),
    }
    provider = FakeCatalogProvider(catalog, delay=0.2)
    service = CompetitorComparisonService(products=ProductService(provider=provider))
    started = time.monotonic()
    result = await service.compare(make_product(asin="B0TARGET01"), list(catalog))
    elapsed = time.monotonic() - started
    assert len(result.competitors) == 3
    starts = list(provider.started_at.values())
    assert max(starts) - min(starts) < 0.1
    assert elapsed < 0.45
    assert "B0TARGET01" not in provider.calls


@pytest.mark.asyncio
async def test_partial_and_total_failure() -> None:
    catalog = {
        "B0COMP0001": make_product(asin="B0COMP0001"),
        "B0COMP0002": make_product(asin="B0COMP0002"),
    }
    provider = FakeCatalogProvider(catalog, missing={"B0COMP0003"})
    service = CompetitorComparisonService(products=ProductService(provider=provider))
    partial = await service.compare(
        make_product(asin="B0TARGET01"),
        ["B0COMP0001", "B0COMP0002", "B0COMP0003"],
    )
    assert len(partial.competitors) == 2
    assert partial.failed_competitors[0].asin == "B0COMP0003"
    assert partial.comparison.summary.requested_count == 3
    assert partial.comparison.summary.retrieved_count == 2

    failing = CompetitorComparisonService(
        products=ProductService(provider=FakeCatalogProvider({}, missing={"B0COMP0001", "B0COMP0002"}))
    )
    with pytest.raises(NoCompetitorsRetrievedError):
        await failing.compare(make_product(asin="B0TARGET01"), ["B0COMP0001", "B0COMP0002"])


def _dump(product: Product) -> dict:
    return json.loads(product.model_dump_json())


def test_comparison_api_structure(client: TestClient) -> None:
    product = make_product()
    expected = ListingAnalysisService().analyze(product)
    response = client.post(
        "/api/v1/analysis/competitors",
        json={
            "target_product": _dump(product),
            "competitor_asins": ["B0TEST0001"],
            "source": "mock",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["target"]["product"]["asin"] == product.asin
    assert body["target"]["analysis"]["overall_score"] == expected.overall_score
    assert body["competitors"][0]["product"]["asin"] == "B0TEST0001"
    assert "metrics" in body["comparison"]
    assert "gaps" in body["comparison"]
    assert "summary" in body["comparison"]
    assert body["meta"]["comparison_version"] == COMPARISON_VERSION
    assert body["meta"]["source"] == "mock"


def test_comparison_api_three_competitors(client: TestClient) -> None:
    response = client.post(
        "/api/v1/analysis/competitors",
        json={
            "target_product": _dump(make_product()),
            "competitor_asins": ["B0TEST0001", "B0TEST0002", "B0TEST0003"],
        },
    )
    assert response.status_code == 200
    assert len(response.json()["competitors"]) == 3


def test_comparison_api_validation(client: TestClient) -> None:
    target = _dump(make_product(asin="B0TARGET01"))
    too_many = client.post(
        "/api/v1/analysis/competitors",
        json={"target_product": target, "competitor_asins": ["B0TEST0001", "B0TEST0002", "B0TEST0003", "B0TEST0004"]},
    )
    assert too_many.status_code == 400
    invalid = client.post(
        "/api/v1/analysis/competitors",
        json={"target_product": target, "competitor_asins": ["NOTANASIN!"]},
    )
    assert invalid.status_code == 400
    duplicate = client.post(
        "/api/v1/analysis/competitors",
        json={"target_product": target, "competitor_asins": ["B0TEST0001", "B0TEST0001"]},
    )
    assert duplicate.status_code == 400
    self_comp = client.post(
        "/api/v1/analysis/competitors",
        json={"target_product": target, "competitor_asins": ["B0TARGET01"]},
    )
    assert self_comp.status_code == 400


def test_comparison_api_partial_and_total_failure(client: TestClient) -> None:
    partial = client.post(
        "/api/v1/analysis/competitors",
        json={
            "target_product": _dump(make_product()),
            "competitor_asins": ["B0TEST0001", "B0MISS0001"],
        },
    )
    assert partial.status_code == 200
    body = partial.json()
    assert len(body["competitors"]) == 1
    assert body["failed_competitors"][0]["asin"] == "B0MISS0001"

    total = client.post(
        "/api/v1/analysis/competitors",
        json={
            "target_product": _dump(make_product()),
            "competitor_asins": ["B0MISS0001", "B0MISS0002"],
        },
    )
    assert total.status_code == 502


def test_mock_and_manual_flows_still_work(client: TestClient) -> None:
    mock = client.get("/api/v1/products/B0TEST0001")
    assert mock.status_code == 200
    assert mock.json()["meta"]["source"] == "mock"
    manual = client.post(
        "/api/v1/products/manual",
        json={
            "asin": "B0MANUAL01",
            "title": "Manual listing used only for regression coverage in competitor tests",
            "bullet_points": ["One benefit already present in the listing"],
            "image_urls": ["https://placehold.co/800"],
        },
    )
    assert manual.status_code == 200
    assert manual.json()["meta"]["source"] == "manual"
    listing = client.post(
        "/api/v1/analysis/listing",
        json={"product": mock.json()["product"], "source": "mock"},
    )
    assert listing.status_code == 200
    assert listing.json()["meta"]["engine"] == "deterministic"


def test_rainforest_target_lookup_still_works(client: TestClient) -> None:
    import httpx

    from app.api.routes.products import get_product_service
    from app.main import app
    from app.providers.rainforest import RainforestProductDataProvider
    from tests.test_rainforest import TEST_KEY, load_fixture

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
    assert response.json()["meta"]["source"] == "rainforest"
    assert response.json()["product"]["asin"] == "B07J4TNYV8"

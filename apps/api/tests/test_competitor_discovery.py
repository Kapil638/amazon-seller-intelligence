import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.analytics.competitor_relevance import score_relevance
from app.core.exceptions import (
    ProviderConfigurationError,
    SearchBlockedError,
    SearchFetchFailedError,
    SearchParseFailedError,
)
from app.models.competitor_discovery import DISCOVERY_VERSION
from app.parsers.rainforest_search_mapper import map_rainforest_search
from app.providers.memory_cache import MemoryTtlValueCache
from app.search.base import AmazonSearchHit, AmazonSearchProvider
from app.search.rainforest_search_provider import RainforestAmazonSearchProvider
from app.services.competitor_discovery_service import CompetitorDiscoveryService
from app.services.listing_analysis_service import ListingAnalysisService
from tests.test_listing_analysis import make_product

FIXTURES = Path(__file__).parent / "fixtures" / "rainforest"
TEST_KEY = "test-rainforest-key"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeSearchProvider(AmazonSearchProvider):
    def __init__(
        self,
        hits: list[AmazonSearchHit] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.hits = hits or []
        self.error = error
        self.calls: list[tuple[str, str]] = []

    @property
    def name(self) -> str:
        return "rainforest"

    async def search(self, query: str, marketplace: str) -> list[AmazonSearchHit]:
        self.calls.append((query, marketplace))
        if self.error is not None:
            raise self.error
        return [item.model_copy() for item in self.hits]


def _hit(**overrides: object) -> AmazonSearchHit:
    data: dict[str, object] = {
        "asin": "B0CAND0001",
        "title": "MuscleFuel Whey Protein Powder Chocolate",
        "brand": "MuscleFuel",
        "price": 799.0,
        "currency": "INR",
        "rating": 4.5,
        "review_count": 5430,
        "image": "https://example.com/a.jpg",
        "position": 2,
        "is_sponsored": True,
        "category": "Health & Personal Care",
    }
    data.update(overrides)
    return AmazonSearchHit.model_validate(data)


def test_rainforest_search_mapper_maps_documented_fields() -> None:
    hits = map_rainforest_search(load_fixture("search.json"), "amazon.in")
    first = hits[0]
    assert first.asin == "B0CAND0001"
    assert first.title.startswith("MuscleFuel")
    assert first.price == 799
    assert first.currency == "INR"
    assert first.rating == 4.5
    assert first.review_count == 5430
    assert first.image
    assert first.position == 1
    assert first.is_sponsored is True
    assert first.category == "Health & Personal Care"
    assert first.brand is None


def test_mapper_leaves_missing_candidate_fields_null() -> None:
    payload = {
        "search_results": [
            {
                "asin": "B0CAND0003",
                "title": "Plain listing without extras",
            }
        ]
    }
    hit = map_rainforest_search(payload, "amazon.in")[0]
    assert hit.brand is None
    assert hit.price is None
    assert hit.rating is None
    assert hit.review_count is None
    assert hit.image is None
    assert hit.is_sponsored is None
    assert hit.position is None


@pytest.mark.asyncio
async def test_target_filtered_and_duplicates_removed() -> None:
    target = make_product(asin="B0TARGET01", title="Brand X Whey Protein Powder Chocolate 1kg", brand="Brand X")
    hits = map_rainforest_search(load_fixture("search.json"), "amazon.in")
    result = await CompetitorDiscoveryService(FakeSearchProvider(hits)).discover(
        target, "whey protein powder chocolate"
    )
    asins = [item.asin for item in result.candidates]
    assert "B0TARGET01" not in asins
    assert asins.count("B0CAND0001") == 1


@pytest.mark.asyncio
async def test_max_candidate_limit() -> None:
    target = make_product(asin="B0TARGET01")
    hits = [_hit(asin=f"B0CAND00{index:02d}", title=target.title, position=index) for index in range(1, 20)]
    provider = FakeSearchProvider(hits)
    service = CompetitorDiscoveryService(search_provider=provider, max_candidates=12)
    result = await service.discover(target, "vitamin d3")
    assert len(result.candidates) == 12
    assert result.meta.displayed_count == 12


def test_relevance_score_range_and_signals() -> None:
    target = make_product(
        asin="B0TARGET01",
        title="Whey Protein Powder Chocolate",
        brand="Brand X",
        category="Health & Personal Care",
    )
    close = _hit(title="Whey Protein Powder Chocolate Isolate", category="Health & Personal Care", brand="Other")
    far = _hit(asin="B0IRREL001", title="USB charging cable", category="Electronics", brand="Other")
    close_score = score_relevance(target, close, "whey protein powder chocolate")
    far_score = score_relevance(target, far, "whey protein powder chocolate")
    assert 0 <= close_score <= 100
    assert 0 <= far_score <= 100
    assert close_score > far_score
    same_category = score_relevance(target, close, "whey protein powder chocolate")
    different_category = score_relevance(
        target,
        close.model_copy(update={"category": "Electronics"}),
        "whey protein powder chocolate",
    )
    assert same_category > different_category
    high_overlap = score_relevance(target, close, "whey protein powder chocolate")
    low_overlap = score_relevance(
        target,
        close.model_copy(update={"title": "Daily vitamin tablets"}),
        "whey protein powder chocolate",
    )
    assert high_overlap > low_overlap


@pytest.mark.asyncio
async def test_sponsored_and_position_are_captured_not_scored() -> None:
    target = make_product(asin="B0TARGET01", title="Whey Protein Powder Chocolate")
    organic = _hit(is_sponsored=False, position=4, asin="B0CAND0004")
    sponsored = _hit(is_sponsored=True, position=1, asin="B0CAND0005")
    assert score_relevance(target, organic, "whey protein powder chocolate") == score_relevance(
        target, sponsored, "whey protein powder chocolate"
    )
    result = await CompetitorDiscoveryService(FakeSearchProvider([organic, sponsored])).discover(
        target, "whey protein powder chocolate"
    )
    by_asin = {item.asin: item for item in result.candidates}
    assert by_asin["B0CAND0004"].position == 4
    assert by_asin["B0CAND0005"].is_sponsored is True


@pytest.mark.asyncio
async def test_no_results_returns_empty_candidates() -> None:
    target = make_product(asin="B0TARGET01")
    service = CompetitorDiscoveryService(search_provider=FakeSearchProvider([]))
    result = await service.discover(target, "obscure query")
    assert result.candidates == []
    assert result.search_query == "obscure query"
    assert result.meta.displayed_count == 0


@pytest.mark.asyncio
async def test_discovery_endpoint_and_metadata(client: TestClient) -> None:
    product = make_product(asin="B0TARGET01", title="Brand X Whey Protein Powder Chocolate 1kg", brand="Brand X")
    hits = [_hit(), _hit(asin="B0CAND0002", title="PeakPulse Whey Isolate Powder Chocolate", is_sponsored=False, position=4)]
    from app.api.routes.competitors import get_competitor_discovery_service
    from app.main import app

    service = CompetitorDiscoveryService(search_provider=FakeSearchProvider(hits))
    app.dependency_overrides[get_competitor_discovery_service] = lambda: service
    try:
        response = client.post(
            "/api/v1/competitors/discover",
            json={"target_product": json.loads(product.model_dump_json()), "search_query": None},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["target_asin"] == "B0TARGET01"
    assert body["search_query"]
    assert body["meta"]["provider"] == "rainforest"
    assert body["meta"]["discovery_version"] == DISCOVERY_VERSION
    assert "search_query" in body
    assert all(item["asin"] != "B0TARGET01" for item in body["candidates"])
    assert "test-rainforest-key" not in response.text


@pytest.mark.asyncio
async def test_manual_query_override_endpoint(client: TestClient) -> None:
    product = make_product(asin="B0TARGET01")
    provider = FakeSearchProvider([_hit()])
    from app.api.routes.competitors import get_competitor_discovery_service
    from app.main import app

    app.dependency_overrides[get_competitor_discovery_service] = lambda: CompetitorDiscoveryService(provider)
    try:
        response = client.post(
            "/api/v1/competitors/discover",
            json={
                "target_product": json.loads(product.model_dump_json()),
                "search_query": "protein powder chocolate",
            },
        )
        invalid = client.post(
            "/api/v1/competitors/discover",
            json={"target_product": json.loads(product.model_dump_json()), "search_query": "x"},
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert response.json()["search_query"] == "protein powder chocolate"
    assert response.json()["meta"]["query_generated"] is False
    assert invalid.status_code == 400
    assert provider.calls[0][0] == "protein powder chocolate"


@pytest.mark.asyncio
async def test_discovery_errors(client: TestClient) -> None:
    product = json.loads(make_product().model_dump_json())
    from app.api.routes.competitors import get_competitor_discovery_service
    from app.main import app

    cases = [
        (ProviderConfigurationError("missing"), 503),
        (SearchBlockedError(), 503),
        (SearchFetchFailedError(), 502),
        (SearchParseFailedError(), 502),
    ]
    for error, status in cases:
        app.dependency_overrides[get_competitor_discovery_service] = lambda err=error: CompetitorDiscoveryService(
            FakeSearchProvider(error=err)
        )
        try:
            response = client.post(
                "/api/v1/competitors/discover",
                json={"target_product": product, "search_query": "protein powder"},
            )
        finally:
            app.dependency_overrides.clear()
        assert response.status_code == status
        assert "api_key" not in response.text.lower()


@pytest.mark.asyncio
async def test_provider_timeout_and_malformed_response() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("slow")

    provider = RainforestAmazonSearchProvider(
        api_key=TEST_KEY,
        cache=MemoryTtlValueCache(60),
        transport=httpx.MockTransport(timeout),
    )
    with pytest.raises(SearchFetchFailedError):
        await provider.search("protein powder", "amazon.in")

    def malformed(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="not-json")

    bad = RainforestAmazonSearchProvider(
        api_key=TEST_KEY,
        cache=MemoryTtlValueCache(60),
        transport=httpx.MockTransport(malformed),
    )
    with pytest.raises(SearchParseFailedError):
        await bad.search("protein powder", "amazon.in")


@pytest.mark.asyncio
async def test_provider_auth_and_rate_limit() -> None:
    def auth(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"request_info": {"success": False}})

    provider = RainforestAmazonSearchProvider(
        api_key=TEST_KEY,
        cache=MemoryTtlValueCache(60),
        transport=httpx.MockTransport(auth),
    )
    with pytest.raises(ProviderConfigurationError):
        await provider.search("protein powder", "amazon.in")

    def limited(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    limited_provider = RainforestAmazonSearchProvider(
        api_key=TEST_KEY,
        cache=MemoryTtlValueCache(60),
        transport=httpx.MockTransport(limited),
    )
    with pytest.raises(SearchBlockedError):
        await limited_provider.search("protein powder", "amazon.in")


@pytest.mark.asyncio
async def test_cache_prevents_repeated_search() -> None:
    calls = {"count": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        assert _request.url.params["type"] == "search"
        assert "page" not in _request.url.params
        return httpx.Response(200, json=load_fixture("search.json"))

    cache = MemoryTtlValueCache(60)
    provider = RainforestAmazonSearchProvider(
        api_key=TEST_KEY,
        cache=cache,
        transport=httpx.MockTransport(handler),
    )
    first = await provider.search("whey protein powder chocolate", "amazon.in")
    second = await provider.search("whey protein powder chocolate", "amazon.in")
    assert calls["count"] == 1
    assert first[0].asin == second[0].asin


@pytest.mark.asyncio
async def test_selected_candidates_use_existing_comparison(client: TestClient) -> None:
    target = make_product(asin="B0TARGET01")
    discovery = await CompetitorDiscoveryService(
        FakeSearchProvider([_hit(asin="B0TEST0002", title="NimbusFoam Memory Contour Pillow")])
    ).discover(target, "memory contour pillow")
    asins = [item.asin for item in discovery.candidates]
    assert asins == ["B0TEST0002"]

    response = client.post(
        "/api/v1/analysis/competitors",
        json={
            "target_product": json.loads(target.model_dump_json()),
            "competitor_asins": asins,
            "source": "mock",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["competitors"][0]["product"]["asin"] == "B0TEST0002"
    assert body["comparison"]["metrics"]
    expected = ListingAnalysisService().analyze(target)
    assert body["target"]["analysis"]["overall_score"] == expected.overall_score


def test_query_endpoint_and_existing_listing_flow(client: TestClient) -> None:
    product = make_product(title="Brand X Whey Protein Powder Chocolate 1kg", brand="Brand X")
    query = client.post(
        "/api/v1/competitors/query",
        json={"target_product": json.loads(product.model_dump_json())},
    )
    assert query.status_code == 200
    assert query.json()["search_query"] == "whey protein powder chocolate"

    listing = client.post(
        "/api/v1/analysis/listing",
        json={"product": json.loads(product.model_dump_json()), "source": "mock"},
    )
    assert listing.status_code == 200
    assert listing.json()["meta"]["engine"] == "deterministic"
    assert "ai_intelligence" not in listing.json()

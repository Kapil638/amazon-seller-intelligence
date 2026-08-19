import pytest

from app.analytics.competitor_search_query import generate_search_query, normalize_search_query
from app.core.exceptions import SearchQueryValidationError
from app.services.competitor_search_query_service import CompetitorSearchQueryService
from tests.test_listing_analysis import make_product


def test_generated_search_query_is_concise() -> None:
    product = make_product(
        title="Brand X Whey Protein Powder Chocolate 1kg",
        brand="Brand X",
    )
    query = generate_search_query(product)
    assert query == "whey protein powder chocolate"
    assert "brand" not in query
    assert "1kg" not in query
    assert len(query) <= 80


def test_brand_is_removed_from_generated_query() -> None:
    product = make_product(
        title="Lumora Wellness Vitamin D3 Softgels Vegetarian 60 Count Bottle",
        brand="Lumora Wellness",
    )
    query = generate_search_query(product)
    assert "lumora" not in query
    assert "wellness" not in query
    assert "vitamin" in query
    assert "d3" in query or "softgels" in query


def test_query_normalization_and_override() -> None:
    service = CompetitorSearchQueryService()
    product = make_product(title="Brand X Whey Protein Powder Chocolate 1kg", brand="Brand X")
    generated, was_generated = service.resolve(product, None)
    assert was_generated is True
    assert generated == "whey protein powder chocolate"
    override, was_generated = service.resolve(product, "  protein powder chocolate  ")
    assert was_generated is False
    assert override == "protein powder chocolate"


def test_invalid_and_empty_query() -> None:
    with pytest.raises(SearchQueryValidationError):
        normalize_search_query(" ")
    with pytest.raises(SearchQueryValidationError):
        normalize_search_query("x")
    with pytest.raises(SearchQueryValidationError):
        normalize_search_query("a" * 81)
    product = make_product()
    service = CompetitorSearchQueryService()
    with pytest.raises(SearchQueryValidationError):
        service.resolve(product, " ")

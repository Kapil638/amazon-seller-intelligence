"""Deterministic candidate relevance to a target listing (relevance version v1).

This is search-result relevance only. It is not market competitiveness,
sales rank, or conversion.
"""

from __future__ import annotations

from app.analytics.competitor_search_query import _tokenize
from app.models.product import Product
from app.search.base import AmazonSearchHit

RELEVANCE_VERSION = "v1"
TITLE_WEIGHT = 50
CORE_WEIGHT = 30
CATEGORY_WEIGHT = 15
BRAND_WEIGHT = 5


def score_relevance(
    target: Product,
    hit: AmazonSearchHit,
    search_query: str,
) -> int:
    title_score = TITLE_WEIGHT * _jaccard(_tokenize(target.title), _tokenize(hit.title))
    core_score = CORE_WEIGHT * _overlap(_tokenize(search_query), _tokenize(hit.title))
    category_score = CATEGORY_WEIGHT if _categories_match(target.category, hit.category) else 0.0
    brand_score = 0.0
    target_brand = (target.brand or "").strip().casefold()
    hit_brand = (hit.brand or "").strip().casefold()
    if target_brand and hit_brand and target_brand != hit_brand:
        brand_score = float(BRAND_WEIGHT)
    total = title_score + core_score + category_score + brand_score
    return max(0, min(100, round(total)))


def _jaccard(left: list[str], right: list[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _overlap(query_tokens: list[str], title_tokens: list[str]) -> float:
    if not query_tokens:
        return 0.0
    title_set = set(title_tokens)
    matched = sum(1 for token in query_tokens if token in title_set)
    return matched / len(query_tokens)


def _categories_match(target_category: str | None, hit_category: str | None) -> bool:
    if not target_category or not hit_category:
        return False
    left = target_category.casefold().strip()
    right = hit_category.casefold().strip()
    if not left or not right:
        return False
    return left == right or left in right or right in left

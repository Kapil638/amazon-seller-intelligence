"""Deterministic Amazon search-query generation (query version v1).

These rules are transparent heuristics. They do not use AI.
"""

from __future__ import annotations

import re

from app.core.exceptions import SearchQueryValidationError
from app.models.product import Product

QUERY_VERSION = "v1"
MAX_QUERY_CHARS = 80
MIN_QUERY_CHARS = 2
MAX_QUERY_TOKENS = 6

STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "by",
    "for",
    "from",
    "in",
    "is",
    "of",
    "on",
    "or",
    "the",
    "this",
    "that",
    "to",
    "with",
    "your",
    "our",
}

PROMO_WORDS = {
    "amazon",
    "authentic",
    "best",
    "bestseller",
    "choice",
    "combo",
    "deal",
    "exclusive",
    "free",
    "new",
    "offer",
    "official",
    "original",
    "premium",
    "sale",
    "shipping",
    "super",
}

PACK_WORDS = {
    "bottle",
    "bottles",
    "box",
    "capsule",
    "capsules",
    "count",
    "ct",
    "kg",
    "g",
    "gm",
    "grams",
    "l",
    "lb",
    "lbs",
    "ml",
    "oz",
    "pack",
    "packet",
    "pcs",
    "piece",
    "pieces",
    "pk",
    "set",
    "softgel",
    "softgels",
    "tablet",
    "tablets",
}

QUANTITY_RE = re.compile(
    r"^\d+([./]\d+)?(mg|g|gm|kg|ml|l|oz|lb|lbs|count|ct|pk|pack)?$",
    re.IGNORECASE,
)
NON_ALNUM_RE = re.compile(r"[^a-z0-9\s]+")


def generate_search_query(product: Product) -> str:
    tokens = _tokenize(product.title)
    brand_tokens = set(_tokenize(product.brand or ""))
    cleaned = [
        token
        for token in tokens
        if token not in brand_tokens
        and token not in STOPWORDS
        and token not in PROMO_WORDS
        and token not in PACK_WORDS
        and not QUANTITY_RE.fullmatch(token)
    ]
    cleaned = _unique(cleaned)

    if len(cleaned) < 2:
        category_tokens = _category_tokens(product.category)
        cleaned = _unique(cleaned + [token for token in category_tokens if token not in brand_tokens])

    if len(cleaned) < 2:
        cleaned = [
            token
            for token in tokens
            if token not in brand_tokens and token not in STOPWORDS and not QUANTITY_RE.fullmatch(token)
        ]
        cleaned = _unique(cleaned)

    if not cleaned:
        cleaned = _unique(tokens[:MAX_QUERY_TOKENS]) or ["product"]

    query = " ".join(cleaned[:MAX_QUERY_TOKENS])
    if len(query) > MAX_QUERY_CHARS:
        query = query[:MAX_QUERY_CHARS].rsplit(" ", 1)[0]
    return query.strip()


def normalize_search_query(query: str) -> str:
    cleaned = " ".join(query.strip().split())
    if len(cleaned) < MIN_QUERY_CHARS:
        raise SearchQueryValidationError("Enter a search query with at least two characters.")
    if len(cleaned) > MAX_QUERY_CHARS:
        raise SearchQueryValidationError(f"Search query must be {MAX_QUERY_CHARS} characters or fewer.")
    return cleaned


def _tokenize(text: str) -> list[str]:
    lowered = NON_ALNUM_RE.sub(" ", text.casefold())
    return [token for token in lowered.split() if token]


def _category_tokens(category: str | None) -> list[str]:
    if not category:
        return []
    last = category.split(">")[-1]
    return [
        token
        for token in _tokenize(last)
        if token not in STOPWORDS and token not in PROMO_WORDS
    ]


def _unique(tokens: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result

"""Map a Rainforest type=search payload onto AmazonSearchHit snippets.

Field names follow the official Rainforest search results schema.
Missing values stay None. Nothing is invented. recent_sales is ignored.
"""

from __future__ import annotations

from typing import Any

from app.core.exceptions import SearchParseFailedError
from app.core.validation import is_valid_asin, normalize_asin
from app.search.base import AmazonSearchHit

GENERIC_CATEGORIES = {"all departments", "aps"}


def map_rainforest_search(payload: dict[str, Any], marketplace: str) -> list[AmazonSearchHit]:
    results = payload.get("search_results")
    if results is None:
        raise SearchParseFailedError("Amazon search results could not be read.")
    if not isinstance(results, list):
        raise SearchParseFailedError("Amazon search results could not be read.")

    hits: list[AmazonSearchHit] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        mapped = _map_hit(item)
        if mapped is not None:
            hits.append(mapped)
    return hits


def _map_hit(item: dict[str, Any]) -> AmazonSearchHit | None:
    asin = _as_str(item.get("asin"))
    title = _as_str(item.get("title"))
    if not asin or not title:
        return None
    asin = normalize_asin(asin)
    if not is_valid_asin(asin):
        return None

    price_amount, currency = _price(item)
    return AmazonSearchHit(
        asin=asin,
        title=title,
        brand=_as_str(item.get("brand")),
        price=price_amount,
        currency=currency,
        rating=_rating(item.get("rating")),
        review_count=_as_int(item.get("ratings_total")),
        image=_as_str(item.get("image")),
        position=_as_int(item.get("position")),
        is_sponsored=_as_bool(item.get("sponsored")),
        category=_category(item),
    )


def _price(item: dict[str, Any]) -> tuple[float | None, str | None]:
    price = item.get("price")
    if not isinstance(price, dict):
        return None, None
    amount = _as_float(price.get("value"))
    currency = _as_str(price.get("currency"))
    if amount is None:
        return None, None
    return amount, currency


def _category(item: dict[str, Any]) -> str | None:
    categories = item.get("categories")
    if not isinstance(categories, list):
        return None
    names: list[str] = []
    for entry in categories:
        if isinstance(entry, dict):
            name = _as_str(entry.get("name"))
            if name and name.casefold() not in GENERIC_CATEGORIES:
                names.append(name)
        elif isinstance(entry, str) and entry.strip() and entry.strip().casefold() not in GENERIC_CATEGORIES:
            names.append(entry.strip())
    if not names:
        return None
    return " > ".join(names)


def _rating(value: Any) -> float | None:
    number = _as_float(value)
    if number is None or number < 0 or number > 5:
        return None
    return number


def _as_str(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _as_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            return None
    return None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    number = _as_float(value)
    if number is None:
        return None
    return int(number)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    return None

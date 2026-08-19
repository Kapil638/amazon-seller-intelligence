"""Parse public Amazon.in product HTML into the normalized Product model.

Selectors are centralized here. Prefer JSON-LD when present, then DOM fallbacks.
This is experimental and will break when Amazon changes page markup.
"""

from __future__ import annotations

from datetime import UTC, datetime
import json
import re
from typing import Any

from bs4 import BeautifulSoup, Tag

from app.core.exceptions import (
    ProductFetchBlockedError,
    ProductNotFoundError,
    ProductParseFailedError,
)
from app.models.product import BSR, Image, Price, Product, Seller, Variation

SELECTORS: dict[str, tuple[str, ...]] = {
    "title": (
        "#productTitle",
        "#title span",
        "h1#title",
        "span.product-title-word-break",
    ),
    "brand": (
        "#bylineInfo",
        "a#brand",
        "tr.po-brand .po-break-word",
        "#brand",
    ),
    "price": (
        "span.a-price .a-offscreen",
        "#priceblock_ourprice",
        "#priceblock_dealprice",
        "#priceblock_saleprice",
        "#corePrice_feature_div span.a-offscreen",
        "#corePriceDisplay_desktop_feature_div span.a-offscreen",
    ),
    "rating": (
        "#acrPopover span.a-icon-alt",
        "#averageCustomerReviews span.a-icon-alt",
        "i.a-icon-star span.a-icon-alt",
    ),
    "review_count": (
        "#acrCustomerReviewText",
        "#acrCustomerReviewLink #acrCustomerReviewText",
    ),
    "bullets": (
        "#feature-bullets ul li span.a-list-item",
        "#feature-bullets ul li",
    ),
    "description": (
        "#productDescription",
        "#productDescription p",
        "#aplus_feature_div",
    ),
    "availability": (
        "#availability span",
        "#availability",
    ),
    "seller": (
        "#sellerProfileTriggerId",
        "a#sellerProfileTriggerId",
        "#merchant-info",
        "#tabular-buybox .tabular-buybox-text",
    ),
    "category": (
        "#wayfinding-breadcrumbs_feature_div ul li span.a-list-item a",
        "#nav-subnav .nav-a-content",
    ),
    "bsr": (
        "#SalesRank",
        "#productDetails_detailBullets_sections_id tr",
        "#detailBulletsWrapper_feature_div li",
        "#detailBullets_feature_div li",
    ),
    "images": (
        "img#landingImage",
        "#imgTagWrapperId img",
        "#altImages img",
        "#main-image-container img",
    ),
    "variations": (
        "li[data-defaultasin]",
        "#twister li[data-defaultasin]",
    ),
}

BLOCK_MARKERS = (
    "api-services-support@amazon.com",
    "enter the characters you see below",
    "validatecaptcha",
    "opfcaptcha",
    "sorry, we just need to make sure you're not a robot",
    "to discuss automated access to amazon data",
)

NOT_FOUND_MARKERS = (
    "we couldn't find that page",
    "sorry, we couldn't find that page",
    "looking for something?",
    "page not found",
    "dogs of amazon",
)

SKIP_BULLET_PREFIXES = (
    "make sure this fits",
    "see more product details",
)


class AmazonProductParser:
    def parse(self, html: str, asin: str, marketplace: str) -> Product:
        text = html or ""
        lowered = text.lower()
        if _looks_blocked(lowered):
            raise ProductFetchBlockedError(asin, marketplace, "Amazon returned a challenge page")
        if _looks_not_found(lowered):
            raise ProductNotFoundError(asin, marketplace)

        soup = BeautifulSoup(text, "html.parser")
        structured = _json_ld_product(soup)

        title = _first_text(soup, SELECTORS["title"]) or _ld_str(structured, "name")
        if not title:
            raise ProductParseFailedError(asin, marketplace, "Could not extract a product title")

        brand = _clean_brand(_first_text(soup, SELECTORS["brand"]) or _ld_brand(structured))
        price = _parse_price(_first_text(soup, SELECTORS["price"])) or _ld_price(structured)
        rating = _parse_rating(_first_text(soup, SELECTORS["rating"])) or _ld_rating(structured)
        review_count = _parse_review_count(_first_text(soup, SELECTORS["review_count"])) or _ld_review_count(
            structured
        )
        bullets = _parse_bullets(soup)
        description = _first_text(soup, SELECTORS["description"]) or _ld_str(structured, "description")
        availability = _clean_availability(_first_text(soup, SELECTORS["availability"]))
        seller_name = _clean_seller(_first_text(soup, SELECTORS["seller"]))
        category = _parse_category(soup) or _ld_str(structured, "category")
        bsr = _parse_bsr(soup)
        images = _parse_images(soup, structured, title)
        variations = _parse_variations(soup)

        return Product(
            asin=asin,
            marketplace=marketplace,
            title=title,
            brand=brand,
            price=price,
            rating=rating,
            review_count=review_count,
            bullet_points=bullets,
            description=description,
            images=images,
            category=category,
            bsr=bsr,
            availability=availability,
            seller=Seller(name=seller_name) if seller_name else None,
            variations=variations,
            last_fetched_at=datetime.now(UTC),
        )


def _looks_blocked(lowered: str) -> bool:
    return any(marker in lowered for marker in BLOCK_MARKERS)


def _looks_not_found(lowered: str) -> bool:
    return any(marker in lowered for marker in NOT_FOUND_MARKERS)


def _text(node: Tag | None) -> str | None:
    if node is None:
        return None
    value = node.get_text(" ", strip=True)
    return value or None


def _first_text(soup: BeautifulSoup, selectors: tuple[str, ...]) -> str | None:
    for selector in selectors:
        value = _text(soup.select_one(selector))
        if value:
            return value
    return None


def _json_ld_product(soup: BeautifulSoup) -> dict[str, Any] | None:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text() or ""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        for candidate in _walk_ld(data):
            types = candidate.get("@type")
            names = types if isinstance(types, list) else [types]
            if "Product" in names:
                return candidate
    return None


def _walk_ld(data: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(data, list):
        for item in data:
            found.extend(_walk_ld(item))
    elif isinstance(data, dict):
        found.append(data)
        if "@graph" in data:
            found.extend(_walk_ld(data["@graph"]))
    return found


def _ld_str(data: dict[str, Any] | None, key: str) -> str | None:
    if not data:
        return None
    value = data.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _ld_brand(data: dict[str, Any] | None) -> str | None:
    if not data:
        return None
    brand = data.get("brand")
    if isinstance(brand, str):
        return brand
    if isinstance(brand, dict):
        name = brand.get("name")
        if isinstance(name, str):
            return name
    return None


def _ld_price(data: dict[str, Any] | None) -> Price | None:
    if not data:
        return None
    offers = data.get("offers")
    if isinstance(offers, list) and offers:
        offers = offers[0]
    if not isinstance(offers, dict):
        return None
    amount = _parse_amount(str(offers.get("price") or ""))
    currency = offers.get("priceCurrency") or "INR"
    if amount is None or not isinstance(currency, str):
        return None
    return Price(amount=amount, currency=currency)


def _ld_rating(data: dict[str, Any] | None) -> float | None:
    if not data:
        return None
    aggregate = data.get("aggregateRating")
    if not isinstance(aggregate, dict):
        return None
    return _parse_rating(str(aggregate.get("ratingValue") or ""))


def _ld_review_count(data: dict[str, Any] | None) -> int | None:
    if not data:
        return None
    aggregate = data.get("aggregateRating")
    if not isinstance(aggregate, dict):
        return None
    return _parse_review_count(str(aggregate.get("reviewCount") or aggregate.get("ratingCount") or ""))


def _parse_amount(value: str) -> float | None:
    cleaned = value.replace("₹", "").replace(",", "").replace("\xa0", " ").strip()
    match = re.search(r"(\d+(?:\.\d+)?)", cleaned)
    if not match:
        return None
    amount = float(match.group(1))
    return amount if amount >= 0 else None


def _parse_price(value: str | None) -> Price | None:
    if not value:
        return None
    amount = _parse_amount(value)
    if amount is None:
        return None
    return Price(amount=amount, currency="INR")


def _parse_rating(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", value)
    if not match:
        return None
    rating = float(match.group(1))
    if rating < 0 or rating > 5:
        return None
    return rating


def _parse_review_count(value: str | None) -> int | None:
    if not value:
        return None
    match = re.search(r"(\d[\d,]*)", value)
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _clean_brand(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"^(visit the|brand:\s*)", "", value, flags=re.I).strip()
    cleaned = re.sub(r"\s+store$", "", cleaned, flags=re.I).strip()
    return cleaned or None


def _clean_seller(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"^(sold by|ships from)\s*", "", value, flags=re.I).strip()
    return cleaned or None


def _clean_availability(value: str | None) -> str | None:
    if not value:
        return None
    compact = re.sub(r"\s+", " ", value).strip()
    return compact or None


def _parse_bullets(soup: BeautifulSoup) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for selector in SELECTORS["bullets"]:
        for node in soup.select(selector):
            text = _text(node)
            if not text:
                continue
            lowered = text.lower()
            if any(lowered.startswith(prefix) for prefix in SKIP_BULLET_PREFIXES):
                continue
            if text in seen:
                continue
            seen.add(text)
            found.append(text)
        if found:
            break
    return found[:10]


def _parse_category(soup: BeautifulSoup) -> str | None:
    crumbs = [
        text
        for node in soup.select(SELECTORS["category"][0])
        if (text := _text(node))
    ]
    if crumbs:
        return crumbs[-1]
    return _first_text(soup, SELECTORS["category"][1:])


def _parse_bsr(soup: BeautifulSoup) -> BSR | None:
    for selector in SELECTORS["bsr"]:
        for node in soup.select(selector):
            text = _text(node)
            if not text or "best seller" not in text.lower():
                continue
            match = re.search(r"#\s*([\d,]+)\s+in\s+(.+)", text, flags=re.I)
            if not match:
                continue
            rank = int(match.group(1).replace(",", ""))
            category = re.split(r"[\(\n]", match.group(2), maxsplit=1)[0].strip(" .")
            if rank >= 1 and category:
                return BSR(rank=rank, category=category)
    return None


def _parse_images(soup: BeautifulSoup, structured: dict[str, Any] | None, alt: str) -> list[Image]:
    urls: list[str] = []
    ld_images = structured.get("image") if structured else None
    if isinstance(ld_images, str):
        urls.append(ld_images)
    elif isinstance(ld_images, list):
        urls.extend(item for item in ld_images if isinstance(item, str))

    for selector in SELECTORS["images"]:
        for node in soup.select(selector):
            urls.extend(_image_urls_from(node))

    unique: list[Image] = []
    seen: set[str] = set()
    for url in urls:
        cleaned = url.strip()
        if not cleaned.startswith("http") or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(Image(url=cleaned, alt=alt))
        if len(unique) >= 8:
            break
    return unique


def _image_urls_from(node: Tag) -> list[str]:
    urls: list[str] = []
    for attr in ("data-old-hires", "data-src", "src"):
        value = node.get(attr)
        if isinstance(value, str) and value.startswith("http"):
            urls.append(value)
    dynamic = node.get("data-a-dynamic-image")
    if isinstance(dynamic, str) and dynamic.startswith("{"):
        try:
            parsed = json.loads(dynamic)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            urls.extend(key for key in parsed if isinstance(key, str))
    return urls


def _parse_variations(soup: BeautifulSoup) -> list[Variation]:
    found: list[Variation] = []
    seen: set[str] = set()
    for selector in SELECTORS["variations"]:
        for node in soup.select(selector):
            asin = node.get("data-defaultasin") or node.get("data-asin")
            if not isinstance(asin, str) or asin in seen:
                continue
            label = node.get("title") or _text(node) or asin
            seen.add(asin)
            found.append(Variation(asin=asin, label=str(label), attributes={}))
        if found:
            break
    return found[:12]

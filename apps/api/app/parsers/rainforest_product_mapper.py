"""Map a Rainforest Product Data API payload onto the normalized Product model.

Field names follow the official Rainforest product results schema.
Missing values stay None or []. Nothing is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import ProductParseFailedError
from app.models.product import BSR, Image, Price, Product, ProductVideo, Seller, Variation
from app.parsers.amazon_media import (
    amazon_image_id,
    choose_best_image_url,
    is_playable_video_url,
    is_video_thumbnail_url,
    unsized_amazon_url,
)


def map_rainforest_product(payload: dict[str, Any], asin: str, marketplace: str) -> Product:
    product = payload.get("product")
    if not isinstance(product, dict):
        raise ProductParseFailedError(asin, marketplace, "Rainforest response did not include a product")

    title = _as_str(product.get("title"))
    if not title:
        raise ProductParseFailedError(asin, marketplace, "Rainforest product is missing a title")

    return Product(
        asin=_as_str(product.get("asin")) or asin,
        marketplace=marketplace,
        title=title,
        brand=_as_str(product.get("brand")),
        price=_price(product),
        rating=_rating(product.get("rating")),
        review_count=_review_count(product),
        bullet_points=_bullets(product.get("feature_bullets")),
        description=_description(product),
        images=_images(product),
        videos=_videos(product),
        category=_category(product),
        bsr=_bsr(product.get("bestsellers_rank")),
        availability=_availability(product),
        seller=_seller(product),
        variations=_variations(product.get("variants")),
        last_fetched_at=datetime.now(UTC),
    )


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


def _price(product: dict[str, Any]) -> Price | None:
    buybox = product.get("buybox_winner")
    if not isinstance(buybox, dict):
        return None
    raw = buybox.get("price")
    if not isinstance(raw, dict):
        return None
    amount = _as_float(raw.get("value"))
    currency = _as_str(raw.get("currency"))
    if amount is None or currency is None or amount < 0:
        return None
    return Price(amount=amount, currency=currency)


def _rating(value: Any) -> float | None:
    rating = _as_float(value)
    if rating is None or rating < 0 or rating > 5:
        return None
    return rating


def _review_count(product: dict[str, Any]) -> int | None:
    count = _as_int(product.get("reviews_total"))
    if count is None:
        count = _as_int(product.get("ratings_total"))
    if count is None or count < 0:
        return None
    return count


def _bullets(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    bullets: list[str] = []
    for item in value:
        text = _as_str(item)
        if text:
            bullets.append(text)
    return bullets


def _description(product: dict[str, Any]) -> str | None:
    return _as_str(product.get("description")) or _as_str(product.get("book_description"))


def _images(product: dict[str, Any]) -> list[Image]:
    grouped: dict[str, list[str]] = {}
    order: list[str] = []
    variants: dict[str, str] = {}
    video_ids = _video_thumbnail_ids(product)

    def consider(url: str | None, variant: str | None = None) -> None:
        if not url:
            return
        if is_video_thumbnail_url(url):
            return
        image_id = amazon_image_id(url) or url
        if image_id in video_ids:
            return
        grouped.setdefault(image_id, [])
        if url not in grouped[image_id]:
            grouped[image_id].append(url)
        if image_id not in order:
            order.append(image_id)
        if variant and image_id not in variants:
            variants[image_id] = variant

    main = product.get("main_image")
    main_url = _as_str(main.get("link")) if isinstance(main, dict) else None
    consider(main_url, "MAIN")
    main_id = amazon_image_id(main_url) if main_url else None

    raw_images = product.get("images")
    if isinstance(raw_images, list):
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            consider(_as_str(item.get("link")), _as_str(item.get("variant")))

    flat = _as_str(product.get("images_flat"))
    if flat:
        for piece in flat.split(","):
            consider(_as_str(piece))

    if main_id and main_id in order:
        order.remove(main_id)
        order.insert(0, main_id)

    images: list[Image] = []
    seen_urls: set[str] = set()
    for image_id in order:
        best = choose_best_image_url(grouped[image_id])
        if not best or best in seen_urls:
            continue
        seen_urls.add(best)
        variant = variants.get(image_id)
        is_main = image_id == main_id or (variant or "").upper() == "MAIN"
        images.append(Image(url=best, variant=variant, is_main=is_main))
    return images


def _videos(product: dict[str, Any]) -> list[ProductVideo]:
    videos: list[ProductVideo] = []
    seen: set[str] = set()

    raw = product.get("videos")
    if isinstance(raw, list):
        for item in raw:
            if not isinstance(item, dict):
                continue
            thumbnail = _as_str(item.get("thumbnail"))
            link = _as_str(item.get("link"))
            video_url = link if link and is_playable_video_url(link) else None
            poster = choose_best_image_url([thumbnail] if thumbnail else []) or thumbnail
            key = video_url or poster or thumbnail or ""
            if not key or key in seen:
                continue
            seen.add(key)
            duration = _as_int(item.get("duration_seconds"))
            videos.append(
                ProductVideo(
                    title=_as_str(item.get("title")),
                    thumbnail_url=poster,
                    video_url=video_url,
                    duration_seconds=duration if duration is not None and duration >= 0 else None,
                )
            )

    raw_images = product.get("images")
    if isinstance(raw_images, list):
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            url = _as_str(item.get("link"))
            if not url or not is_video_thumbnail_url(url):
                continue
            poster = unsized_amazon_url(url) or url
            if poster in seen:
                continue
            seen.add(poster)
            videos.append(ProductVideo(thumbnail_url=poster))

    return videos


def _video_thumbnail_ids(product: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    raw = product.get("videos")
    if not isinstance(raw, list):
        return ids
    for item in raw:
        if not isinstance(item, dict):
            continue
        thumb = _as_str(item.get("thumbnail"))
        image_id = amazon_image_id(thumb) if thumb else None
        if image_id:
            ids.add(image_id)
    return ids


def _category(product: dict[str, Any]) -> str | None:
    flat = _as_str(product.get("categories_flat"))
    if flat:
        return flat

    categories = product.get("categories")
    if isinstance(categories, list):
        names = [_as_str(item.get("name")) for item in categories if isinstance(item, dict)]
        joined = " > ".join(name for name in names if name)
        if joined:
            return joined

    alias = product.get("search_alias")
    if isinstance(alias, dict):
        return _as_str(alias.get("title"))
    return None


def _bsr(value: Any) -> BSR | None:
    if not isinstance(value, list) or not value:
        return None
    first = value[0]
    if not isinstance(first, dict):
        return None
    rank = _as_int(first.get("rank"))
    category = _as_str(first.get("category"))
    if rank is None or rank < 1 or not category:
        return None
    return BSR(rank=rank, category=category)


def _availability(product: dict[str, Any]) -> str | None:
    buybox = product.get("buybox_winner")
    if not isinstance(buybox, dict):
        return None
    availability = buybox.get("availability")
    if not isinstance(availability, dict):
        return None
    return _as_str(availability.get("raw"))


def _seller(product: dict[str, Any]) -> Seller | None:
    buybox = product.get("buybox_winner")
    if not isinstance(buybox, dict):
        return None
    fulfillment = buybox.get("fulfillment")
    if not isinstance(fulfillment, dict):
        return None
    third_party = fulfillment.get("third_party_seller")
    if not isinstance(third_party, dict):
        return None
    name = _as_str(third_party.get("name"))
    if not name:
        return None
    is_fba = fulfillment.get("is_fulfilled_by_amazon")
    return Seller(
        name=name,
        id=_as_str(third_party.get("id")),
        is_fba=is_fba if isinstance(is_fba, bool) else None,
        rating=None,
    )


def _variations(value: Any) -> list[Variation]:
    if not isinstance(value, list):
        return []
    variations: list[Variation] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        asin = _as_str(item.get("asin"))
        if not asin:
            continue
        attributes: dict[str, str] = {}
        dimensions = item.get("dimensions")
        if isinstance(dimensions, list):
            for dimension in dimensions:
                if not isinstance(dimension, dict):
                    continue
                name = _as_str(dimension.get("name"))
                dim_value = _as_str(dimension.get("value"))
                if name and dim_value:
                    attributes[name] = dim_value
        label = _as_str(item.get("title")) or (" / ".join(attributes.values()) if attributes else asin)
        variations.append(Variation(asin=asin, label=label, attributes=attributes))
    return variations

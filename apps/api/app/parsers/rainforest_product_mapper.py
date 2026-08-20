"""Map a Rainforest Product Data API payload onto the normalized Product model.

Field names follow the official Rainforest product results schema.
Missing values stay None or []. Nothing is invented.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.core.exceptions import ProductParseFailedError
from app.models.product import (
    APlusContent,
    APlusImage,
    BrandStory,
    BSR,
    CategoryNode,
    FeaturedReview,
    Image,
    Price,
    Product,
    ProductAttributes,
    ProductSpecification,
    ProductVideo,
    RatingBand,
    RatingBreakdown,
    Seller,
    Variation,
)
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
        bsr_ranks=_bsr_ranks(product.get("bestsellers_rank")),
        category_path=_category_path(product.get("categories")),
        is_sold_by_amazon=_is_sold_by_amazon(product),
        availability_type=_availability_type(product),
        videos_count=_videos_count(product.get("videos_count")),
        a_plus=_a_plus(product.get("a_plus_content")),
        specifications=_named_pairs(product.get("specifications")),
        specifications_flat=_as_str(product.get("specifications_flat")),
        attributes=_attributes(product),
        rating_breakdown=_rating_breakdown(product.get("rating_breakdown")),
        featured_reviews=_featured_reviews(product.get("top_reviews")),
        recent_sales_text=_as_str(product.get("recent_sales")),
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


def _as_bool(value: Any) -> bool | None:
    return value if isinstance(value, bool) else None


def _as_id(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value)
    return _as_str(value)


def _positive_int(value: Any) -> int | None:
    number = _as_int(value)
    if number is None or number < 1:
        return None
    return number


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        text = _as_str(item)
        if text:
            items.append(text)
    return items


def _named_pairs(value: Any) -> list[ProductSpecification]:
    if not isinstance(value, list):
        return []
    pairs: list[ProductSpecification] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("name"))
        pair_value = _as_str(item.get("value"))
        if name and pair_value:
            pairs.append(ProductSpecification(name=name, value=pair_value))
    return pairs


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
    dimensions: dict[str, tuple[int | None, int | None]] = {}
    video_ids = _video_thumbnail_ids(product)

    def consider(url: str | None, variant: str | None = None, width: Any = None, height: Any = None) -> None:
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
        if url not in dimensions:
            dimensions[url] = (_positive_int(width), _positive_int(height))

    main = product.get("main_image")
    main_url = _as_str(main.get("link")) if isinstance(main, dict) else None
    if isinstance(main, dict):
        consider(main_url, "MAIN", main.get("width"), main.get("height"))
    else:
        consider(main_url, "MAIN")
    main_id = amazon_image_id(main_url) if main_url else None

    raw_images = product.get("images")
    if isinstance(raw_images, list):
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            consider(
                _as_str(item.get("link")),
                _as_str(item.get("variant")),
                item.get("width"),
                item.get("height"),
            )

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
        width, height = dimensions.get(best, (None, None))
        images.append(Image(url=best, variant=variant, is_main=is_main, width=width, height=height))
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
                    group_type=_as_str(item.get("group_type")),
                    group_id=_as_str(item.get("group_id")),
                    width=_positive_int(item.get("width")),
                    height=_positive_int(item.get("height")),
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


def _videos_count(value: Any) -> int | None:
    count = _as_int(value)
    if count is None or count < 0:
        return None
    return count


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


def _category_path(value: Any) -> list[CategoryNode]:
    if not isinstance(value, list):
        return []
    nodes: list[CategoryNode] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _as_str(item.get("name"))
        if not name:
            continue
        nodes.append(CategoryNode(name=name, category_id=_as_id(item.get("category_id"))))
    return nodes


def _bsr_entry(item: Any) -> BSR | None:
    if not isinstance(item, dict):
        return None
    rank = _as_int(item.get("rank"))
    category = _as_str(item.get("category"))
    if rank is None or rank < 1 or not category:
        return None
    return BSR(rank=rank, category=category)


def _bsr(value: Any) -> BSR | None:
    if not isinstance(value, list) or not value:
        return None
    return _bsr_entry(value[0])


def _bsr_ranks(value: Any) -> list[BSR]:
    if not isinstance(value, list):
        return []
    ranks: list[BSR] = []
    for item in value:
        parsed = _bsr_entry(item)
        if parsed is not None:
            ranks.append(parsed)
    return ranks


def _buybox(product: dict[str, Any]) -> dict[str, Any] | None:
    buybox = product.get("buybox_winner")
    return buybox if isinstance(buybox, dict) else None


def _availability(product: dict[str, Any]) -> str | None:
    buybox = _buybox(product)
    if buybox is None:
        return None
    availability = buybox.get("availability")
    if not isinstance(availability, dict):
        return None
    return _as_str(availability.get("raw"))


def _availability_type(product: dict[str, Any]) -> str | None:
    buybox = _buybox(product)
    if buybox is None:
        return None
    availability = buybox.get("availability")
    if not isinstance(availability, dict):
        return None
    return _as_str(availability.get("type"))


def _fulfillment(product: dict[str, Any]) -> dict[str, Any] | None:
    buybox = _buybox(product)
    if buybox is None:
        return None
    fulfillment = buybox.get("fulfillment")
    return fulfillment if isinstance(fulfillment, dict) else None


def _is_sold_by_amazon(product: dict[str, Any]) -> bool | None:
    fulfillment = _fulfillment(product)
    if fulfillment is None:
        return None
    return _as_bool(fulfillment.get("is_sold_by_amazon"))


def _seller(product: dict[str, Any]) -> Seller | None:
    fulfillment = _fulfillment(product)
    if fulfillment is None:
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
        variations.append(
            Variation(
                asin=asin,
                label=label,
                attributes=attributes,
                is_current_product=_as_bool(item.get("is_current_product")),
            )
        )
    return variations


def _attributes(product: dict[str, Any]) -> ProductAttributes | None:
    manufacturer = _as_str(product.get("manufacturer"))
    ingredients = _string_list(product.get("ingredients"))
    diet_type = _string_list(product.get("diet_type"))
    listed = _named_pairs(product.get("attributes"))
    if not manufacturer and not ingredients and not diet_type and not listed:
        return None
    return ProductAttributes(
        manufacturer=manufacturer,
        ingredients=ingredients,
        diet_type=diet_type,
        listed=listed,
    )


def _a_plus(value: Any) -> APlusContent | None:
    if not isinstance(value, dict):
        return None
    brand_story = _brand_story(value.get("brand_story"))
    images: list[APlusImage] = []
    raw_images = value.get("all_images")
    if isinstance(raw_images, list):
        for item in raw_images:
            if not isinstance(item, dict):
                continue
            url = _as_str(item.get("link"))
            if not url:
                continue
            images.append(APlusImage(url=url, alt=_as_str(item.get("name"))))
    return APlusContent(
        has_a_plus_content=_as_bool(value.get("has_a_plus_content")),
        has_brand_story=_as_bool(value.get("has_brand_story")),
        third_party=_as_bool(value.get("third_party")),
        company_logo=_as_str(value.get("company_logo")),
        company_description=_as_str(value.get("company_description_text")),
        body_text=_as_str(value.get("body_text")),
        images=images,
        brand_story=brand_story,
    )


def _brand_story(value: Any) -> BrandStory | None:
    if not isinstance(value, dict):
        return None
    images = _string_list(value.get("images"))
    story = BrandStory(
        hero_image=_as_str(value.get("hero_image")),
        brand_logo=_as_str(value.get("brand_logo")),
        description=_as_str(value.get("description")),
        images=images,
    )
    if (
        story.hero_image is None
        and story.brand_logo is None
        and story.description is None
        and not story.images
    ):
        return None
    return story


def _rating_band(value: Any) -> RatingBand | None:
    if not isinstance(value, dict):
        return None
    percentage = _as_int(value.get("percentage"))
    count = _as_int(value.get("count"))
    if percentage is not None and (percentage < 0 or percentage > 100):
        percentage = None
    if count is not None and count < 0:
        count = None
    if percentage is None and count is None:
        return None
    return RatingBand(percentage=percentage, count=count)


def _rating_breakdown(value: Any) -> RatingBreakdown | None:
    if not isinstance(value, dict):
        return None
    breakdown = RatingBreakdown(
        five_star=_rating_band(value.get("five_star")),
        four_star=_rating_band(value.get("four_star")),
        three_star=_rating_band(value.get("three_star")),
        two_star=_rating_band(value.get("two_star")),
        one_star=_rating_band(value.get("one_star")),
    )
    if all(
        getattr(breakdown, name) is None
        for name in ("five_star", "four_star", "three_star", "two_star", "one_star")
    ):
        return None
    return breakdown


def _featured_reviews(value: Any) -> list[FeaturedReview]:
    if not isinstance(value, list):
        return []
    reviews: list[FeaturedReview] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        date = item.get("date") if isinstance(item.get("date"), dict) else {}
        profile = item.get("profile") if isinstance(item.get("profile"), dict) else {}
        rating = _rating(item.get("rating"))
        review = FeaturedReview(
            id=_as_str(item.get("id")),
            title=_as_str(item.get("title")),
            body=_as_str(item.get("body")),
            rating=rating,
            profile_name=_as_str(profile.get("name")) if isinstance(profile, dict) else None,
            verified_purchase=_as_bool(item.get("verified_purchase")),
            date_raw=_as_str(date.get("raw")) if isinstance(date, dict) else None,
            date_utc=_as_str(date.get("utc")) if isinstance(date, dict) else None,
        )
        if not any(
            (
                review.id,
                review.title,
                review.body,
                review.rating is not None,
                review.profile_name,
            )
        ):
            continue
        reviews.append(review)
    return reviews

from __future__ import annotations

LABELS: dict[str, str] = {
    "title": "Title Optimization",
    "bullets": "Bullet Content & SEO Readiness",
    "description": "Product Description",
    "description_a_plus": "Description & A+ Content",
    "media": "Media Coverage",
    "media_coverage": "Media Coverage",
    "content_structure": "Content Structure & Readability",
    "a_plus": "A+ Content",
    "brand_story": "Brand Story",
    "specifications": "Specifications",
    "category": "Category",
    "bsr_ranks": "Best Seller Rank",
    "bsr": "Best Seller Rank",
    "review_count": "Review Count",
    "rating": "Rating",
    "price": "Price",
    "availability": "Availability",
    "seller": "Seller",
    "images": "Product Images",
    "video": "Video",
    "videos": "Video",
    "core_listing_content": "Core Listing",
    "enhanced_content": "Enhanced Content",
    "category_context": "Category Context",
    "market_signals": "Market Signals",
    "product_only": "Product only",
    "feature": "Feature",
    "benefit": "Benefit",
    "lifestyle": "Lifestyle",
    "dimensions": "Dimensions",
    "how_to_use": "How to use",
    "packaging": "Packaging",
    "comparison": "Comparison",
    "detail_closeup": "Detail close-up",
    "other": "Other",
    "observed": "Observed",
    "reported_absent": "Reported absent",
    "unknown": "Unknown",
    "excellent": "Excellent",
    "good": "Good",
    "fair": "Fair",
    "poor": "Poor",
    "high": "High",
    "medium": "Medium",
    "low": "Low",
    "info": "Information",
}

SHORT_SECTION_LABELS: dict[str, str] = {
    "title": "Title",
    "bullets": "Bullets & SEO",
    "description_a_plus": "Description & A+",
    "media_coverage": "Media",
    "content_structure": "Structure",
}

SECTION_ORDER = [
    ("title", "Title Optimization"),
    ("bullets", "Bullet Content & SEO Readiness"),
    ("description_a_plus", "Description & A+ Content"),
    ("media_coverage", "Media Coverage"),
    ("content_structure", "Content Structure & Readability"),
]


def friendly_label(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return "Not available"
    key = raw.lower()
    if key in LABELS:
        return LABELS[key]
    if raw in LABELS:
        return LABELS[raw]
    return _title_from_snake(raw)


def short_section_label(key: str) -> str:
    return SHORT_SECTION_LABELS.get(key, friendly_label(key))


def _title_from_snake(value: str) -> str:
    cleaned = value.replace("-", " ").replace("_", " ").strip()
    if not cleaned:
        return "Not available"
    return " ".join(word.capitalize() for word in cleaned.split())

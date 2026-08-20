from __future__ import annotations

import re
from typing import Any

from app.analytics.listing_rules_v2 import (
    _a_plus_image_count,
    _a_plus_presence,
    _brand_story_media,
    _brand_story_state,
    _video_evidence_state,
)
from app.models.listing_analysis_v2 import EvidenceState, ListingAnalysisV2
from app.models.product import Product

MAX_BODY_CHARS = 3500
MAX_SPEC_FLAT_CHARS = 2000
MAX_SPECS = 40
MAX_LISTED_ATTRIBUTES = 24
MAX_BULLETS = 10
MAX_ALT_TEXTS = 12
MAX_VARIATION_KEYS = 16


def build_ai_listing_v2_context(product: Product, analysis: ListingAnalysisV2) -> dict[str, Any]:
    """Compact V2 AI evidence. No Rainforest JSON, HTML, image URLs, or review corpus."""

    return {
        "product": _product_identity(product),
        "a_plus": _a_plus_block(product),
        "specifications_block": _specifications_block(product),
        "media": _media_coverage(product),
        "analysis": _analysis_block(analysis),
    }


def _clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    cleaned = _plain_text(text)
    if not cleaned:
        return None
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit].rstrip() + "…"


def _plain_text(text: str) -> str:
    stripped = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", stripped).strip()


def _product_identity(product: Product) -> dict[str, Any]:
    current_variation_attributes: dict[str, str] = {}
    variation_attribute_keys: list[str] = []
    seen_keys: set[str] = set()
    for variation in product.variations:
        if variation.is_current_product:
            current_variation_attributes = dict(list(variation.attributes.items())[:MAX_VARIATION_KEYS])
        for key in variation.attributes:
            lowered = key.strip()
            if lowered and lowered.lower() not in seen_keys:
                seen_keys.add(lowered.lower())
                variation_attribute_keys.append(lowered)
                if len(variation_attribute_keys) >= MAX_VARIATION_KEYS:
                    break
        if len(variation_attribute_keys) >= MAX_VARIATION_KEYS:
            break

    seller = None
    if product.seller is not None:
        seller = {
            "name": product.seller.name,
            "is_fba": product.seller.is_fba,
            "rating": product.seller.rating,
        }

    return {
        "asin": product.asin,
        "marketplace": product.marketplace,
        "title": product.title,
        "brand": product.brand,
        "category": product.category,
        "category_path": [
            {"name": node.name, "category_id": node.category_id} for node in product.category_path
        ],
        "bullet_points": [item for item in product.bullet_points if item.strip()][:MAX_BULLETS],
        "description": _clip(product.description, MAX_BODY_CHARS),
        "variation_attribute_keys": variation_attribute_keys,
        "current_variation_attributes": current_variation_attributes or None,
        "seller": seller,
    }


def _a_plus_block(product: Product) -> dict[str, Any]:
    state = _a_plus_presence(product)
    payload = product.a_plus
    if payload is None:
        return {
            "evidence_state": state.value,
            "has_a_plus_content": None,
            "note": "A+ data was not available in the supplied evidence.",
        }

    story = payload.brand_story
    brand_story = None
    if story is not None:
        brand_story = {
            "has_hero_image": bool(story.hero_image),
            "has_brand_logo": bool(story.brand_logo),
            "image_count": len(story.images),
            "description": _clip(story.description, 1200),
        }

    alt_texts = [item.alt for item in payload.images if item.alt and item.alt.strip()][:MAX_ALT_TEXTS]
    body_text = _clip(payload.body_text, MAX_BODY_CHARS)
    return {
        "evidence_state": state.value,
        "has_a_plus_content": payload.has_a_plus_content,
        "has_brand_story": payload.has_brand_story,
        "brand_story_evidence_state": _brand_story_state(product).value,
        "third_party": payload.third_party,
        "has_company_logo": bool(payload.company_logo),
        "company_description": _clip(payload.company_description, 1200),
        "body_text": body_text,
        "body_text_available": bool(body_text),
        "image_count": len(payload.images),
        "image_alt_texts": alt_texts,
        "brand_story": brand_story,
        "brand_story_media_present": _brand_story_media(product),
    }


def _specifications_block(product: Product) -> dict[str, Any]:
    specs = [
        {"name": item.name, "value": item.value}
        for item in product.specifications[:MAX_SPECS]
        if item.name.strip() and item.value.strip()
    ]
    attributes = None
    if product.attributes is not None:
        attributes = {
            "manufacturer": product.attributes.manufacturer,
            "ingredients": list(product.attributes.ingredients[:20]),
            "diet_type": list(product.attributes.diet_type[:12]),
            "listed": [
                {"name": item.name, "value": item.value}
                for item in product.attributes.listed[:MAX_LISTED_ATTRIBUTES]
                if item.name.strip() and item.value.strip()
            ],
        }
    return {
        "specifications": specs,
        "specifications_flat": _clip(product.specifications_flat, MAX_SPEC_FLAT_CHARS),
        "attributes": attributes,
    }


def _media_coverage(product: Product) -> dict[str, Any]:
    video_state = _video_evidence_state(product)
    return {
        "gallery_image_count": len(product.images),
        "main_image_present": any(image.is_main for image in product.images) or bool(product.images),
        "videos_count": product.videos_count,
        "video_object_count": len(product.videos),
        "video_evidence_state": video_state.value,
        "a_plus_media_count": _a_plus_image_count(product),
        "brand_story_media_present": _brand_story_media(product),
        "visual_composition_not_evaluated": True,
        "note": "Media facts are coverage only. Visual composition was not evaluated in this analysis.",
    }


def _analysis_block(analysis: ListingAnalysisV2) -> dict[str, Any]:
    section_names = ("title", "bullets", "description_a_plus", "media_coverage", "content_structure")
    return {
        "listing_quality_score": analysis.listing_quality_score,
        "score_version": analysis.score_version,
        "status": analysis.status.value,
        "section_scores": {
            name: {
                "score": getattr(analysis.sections, name).score,
                "status": getattr(analysis.sections, name).status.value,
                "metrics": _compact_metrics(getattr(analysis.sections, name).metrics),
            }
            for name in section_names
        },
        "findings": [
            {
                "code": item.code,
                "severity": item.severity.value,
                "category": item.category,
                "message": item.message,
            }
            for item in analysis.findings
        ],
        "finding_codes": [item.code for item in analysis.findings],
        "deterministic_recommendations": [
            {
                "code": item.code,
                "category": item.category,
                "priority": item.priority.value,
                "action": item.action,
                "finding_code": item.finding_code,
            }
            for item in analysis.recommendations
        ],
        "market_signals": {
            "rating": analysis.market_signals.rating,
            "review_count": analysis.market_signals.review_count,
            "price": analysis.market_signals.price.model_dump() if analysis.market_signals.price else None,
            "availability": analysis.market_signals.availability,
            "availability_type": analysis.market_signals.availability_type,
            "is_sold_by_amazon": analysis.market_signals.is_sold_by_amazon,
            "bsr_ranks": [item.model_dump() for item in analysis.market_signals.bsr_ranks],
            "recent_sales_text": analysis.market_signals.recent_sales_text,
            "note": "Market signals are factual context only. They do not prove listing-copy quality or conversion.",
        },
        "data_coverage": {
            "overall_percentage": analysis.data_coverage.overall_percentage,
            "groups": {
                name: {
                    "available": group.available,
                    "expected": group.expected,
                    "percentage": group.percentage,
                    "fields": [
                        {
                            "name": field.name,
                            "evidence_state": field.evidence_state.value,
                            "available": field.available,
                            "note": field.note,
                        }
                        for field in group.fields
                    ],
                }
                for name, group in (
                    ("core_listing_content", analysis.data_coverage.core_listing_content),
                    ("media", analysis.data_coverage.media),
                    ("enhanced_content", analysis.data_coverage.enhanced_content),
                    ("category_context", analysis.data_coverage.category_context),
                    ("market_signals", analysis.data_coverage.market_signals),
                )
            },
        },
        "score_authority_note": "listing_quality_score and section scores are authoritative. Do not recalculate them.",
        "a_plus_coverage_state": _coverage_state(analysis, "enhanced_content", "a_plus"),
        "video_coverage_state": _coverage_state(analysis, "media", "video"),
    }


def _coverage_state(analysis: ListingAnalysisV2, group_name: str, field_name: str) -> str:
    group = getattr(analysis.data_coverage, group_name)
    for field in group.fields:
        if field.name == field_name:
            return field.evidence_state.value
    return EvidenceState.UNKNOWN.value


def _compact_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in metrics.items():
        if isinstance(value, str) and len(value) > 240:
            compact[key] = value[:240].rstrip() + "…"
        elif isinstance(value, list) and len(value) > 16:
            compact[key] = value[:16]
        else:
            compact[key] = value
    return compact

from __future__ import annotations

from typing import Any

from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product

SELECTED_METRIC_KEYS = (
    "character_count",
    "word_count",
    "bullet_count",
    "image_count",
    "fields_present",
    "fields_total",
    "missing",
    "rating",
    "review_count",
)


def build_ai_listing_context(product: Product, analysis: ListingAnalysis) -> dict[str, Any]:
    """Compact, normalized context. Never includes Rainforest/HTML/provider payloads."""

    seller = None
    if product.seller is not None:
        seller = {
            "name": product.seller.name,
            "is_fba": product.seller.is_fba,
            "rating": product.seller.rating,
        }

    return {
        "product": {
            "asin": product.asin,
            "title": product.title,
            "brand": product.brand,
            "category": product.category,
            "price": product.price.model_dump() if product.price else None,
            "rating": product.rating,
            "review_count": product.review_count,
            "bullet_points": list(product.bullet_points),
            "description": product.description,
            "image_count": len(product.images),
            "bsr": product.bsr.model_dump() if product.bsr else None,
            "availability": product.availability,
            "seller": seller,
        },
        "analysis": {
            "overall_score": analysis.overall_score,
            "score_version": analysis.score_version,
            "section_scores": {
                name: {
                    "score": getattr(analysis.sections, name).score,
                    "status": getattr(analysis.sections, name).status,
                }
                for name in (
                    "title",
                    "bullets",
                    "description",
                    "images",
                    "completeness",
                    "social_proof",
                )
            },
            "findings": [
                {
                    "code": item.code,
                    "severity": item.severity,
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
                    "message": item.message,
                }
                for item in analysis.recommendations
            ],
            "selected_metrics": {
                name: {
                    key: getattr(analysis.sections, name).metrics[key]
                    for key in SELECTED_METRIC_KEYS
                    if key in getattr(analysis.sections, name).metrics
                }
                for name in (
                    "title",
                    "bullets",
                    "description",
                    "images",
                    "completeness",
                    "social_proof",
                )
            },
        },
    }

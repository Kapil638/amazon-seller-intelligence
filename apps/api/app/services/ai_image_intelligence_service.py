from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.ai.base import AIGenerationResult, AIProvider
from app.analytics.listing_rules_v2 import _a_plus_presence, _brand_story_state
from app.core.config import get_settings
from app.core.exceptions import NoValidMediaError
from app.media.selector import select_media_evidence
from app.models.ai_image_intelligence import AIImageIntelligence
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.media_evidence import MediaSelectionResult
from app.models.product import Product
from app.prompts.image_intelligence import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from app.providers.memory_cache import MemoryTtlValueCache

logger = logging.getLogger("app.ai.image_intelligence")


class AIImageIntelligenceService:
    """Multimodal visual intelligence. Does not change ListingAnalysisV2 scores."""

    def __init__(
        self,
        provider: AIProvider,
        cache: MemoryTtlValueCache | None = None,
    ) -> None:
        settings = get_settings()
        self._provider = provider
        self._cache = cache if cache is not None else MemoryTtlValueCache(settings.ai_cache_ttl_seconds)

    async def generate(
        self,
        product: Product,
        analysis: ListingAnalysisV2,
    ) -> tuple[AIGenerationResult, MediaSelectionResult]:
        selection = select_media_evidence(product)
        if not selection.selected:
            raise NoValidMediaError()

        context = _build_context(product, analysis, selection)
        model = getattr(self._provider, "vision_model", None) or self._provider.model
        cache_key = _cache_key(
            context,
            [item.url for item in selection.selected],
            model,
            PROMPT_VERSION,
            self._provider.name,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_openai_cache_hit()
            return cached, selection

        try:
            result = await self._provider.generate_multimodal_structured(
                schema=AIImageIntelligence,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(context, selection.selected),
                repair_prompt=build_repair_prompt(context, selection.selected),
                prompt_version=PROMPT_VERSION,
                images=selection.selected,
            )
        except Exception:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_openai_failure()
            logger.info(
                "provider=%s model=%s prompt_version=%s images_selected=%s success=%s",
                self._provider.name,
                model,
                PROMPT_VERSION,
                selection.images_selected,
                False,
            )
            raise
        from app.usage.openai_recording import record_openai_generation

        record_openai_generation("image_intelligence_v1", result)
        self._cache.set(cache_key, result)
        return result, selection


def _build_context(
    product: Product,
    analysis: ListingAnalysisV2,
    selection: MediaSelectionResult,
) -> dict[str, Any]:
    a_plus_state = _a_plus_presence(product)
    brand_state = _brand_story_state(product)
    payload = product.a_plus
    a_plus_block: dict[str, Any]
    if payload is None:
        a_plus_block = {
            "evidence_state": a_plus_state.value,
            "has_a_plus_content": None,
            "note": "A+ evidence was unavailable from the supplied product data.",
        }
    else:
        a_plus_block = {
            "evidence_state": a_plus_state.value,
            "has_a_plus_content": payload.has_a_plus_content,
            "has_brand_story": payload.has_brand_story,
            "brand_story_evidence_state": brand_state.value,
            "body_text_available": bool(payload.body_text and payload.body_text.strip()),
            "a_plus_image_count": len(payload.images),
        }
    return {
        "product": {
            "asin": product.asin,
            "marketplace": product.marketplace,
            "title": product.title,
            "brand": product.brand,
            "category": product.category,
            "bullet_points": list(product.bullet_points)[:10],
            "description": (product.description or "")[:1500] or None,
            "specifications": [
                {"name": item.name, "value": item.value} for item in product.specifications[:20]
            ],
            "attributes": (
                {
                    "manufacturer": product.attributes.manufacturer,
                    "diet_type": list(product.attributes.diet_type[:8]),
                    "listed": [
                        {"name": item.name, "value": item.value}
                        for item in product.attributes.listed[:16]
                    ],
                }
                if product.attributes
                else None
            ),
            "current_variation": next(
                (
                    {
                        "asin": item.asin,
                        "label": item.label,
                        "attributes": dict(list(item.attributes.items())[:12]),
                    }
                    for item in product.variations
                    if item.is_current_product
                ),
                None,
            ),
        },
        "a_plus": a_plus_block,
        "analysis": {
            "listing_quality_score": analysis.listing_quality_score,
            "score_version": analysis.score_version,
            "score_authority_note": "listing_quality_score is authoritative and must not be changed or replaced.",
            "finding_codes": [item.code for item in analysis.findings],
            "a_plus_coverage_state": a_plus_state.value,
        },
        "media": {
            "images_available": selection.images_available,
            "images_selected": selection.images_selected,
            "images_skipped": selection.images_skipped,
            "selection_reason": selection.selection_reason,
            "video": selection.video.model_dump(),
            "visual_composition_evaluated": True,
            "video_frames_not_analyzed": True,
        },
    }


def _cache_key(
    context: dict[str, Any],
    selected_urls: list[str],
    model: str,
    prompt_version: str,
    provider: str,
) -> str:
    payload = json.dumps(
        {
            "context": context,
            "selected_urls": selected_urls,
            "model": model,
            "prompt_version": prompt_version,
            "provider": provider,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

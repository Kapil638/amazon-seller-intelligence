from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.ai.base import AIGenerationResult, AIProvider
from app.ai.context_v2 import build_ai_listing_v2_context
from app.core.config import get_settings
from app.models.ai_listing_intelligence_v2 import AIListingIntelligenceV2
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.product import Product
from app.prompts.listing_intelligence_v2 import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from app.providers.memory_cache import MemoryTtlValueCache

logger = logging.getLogger("app.ai.listing_intelligence_v2")


class AIListingIntelligenceV2Service:
    """Semantic listing strategy on top of ListingAnalysisV2. Does not change scores."""

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
    ) -> AIGenerationResult:
        context = build_ai_listing_v2_context(product, analysis)
        cache_key = _cache_key(
            context,
            self._provider.model,
            PROMPT_VERSION,
            self._provider.name,
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_openai_cache_hit()
            return cached

        try:
            result = await self._provider.generate_structured(
                schema=AIListingIntelligenceV2,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(context),
                repair_prompt=build_repair_prompt(context),
                prompt_version=PROMPT_VERSION,
            )
        except Exception:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_openai_failure()
            logger.info(
                "provider=%s model=%s prompt_version=%s latency_ms=%s input_tokens=%s output_tokens=%s total_tokens=%s success=%s",
                self._provider.name,
                self._provider.model,
                PROMPT_VERSION,
                None,
                None,
                None,
                None,
                False,
            )
            raise
        from app.usage.openai_recording import record_openai_generation

        record_openai_generation("listing_intelligence_v2", result)
        self._cache.set(cache_key, result)
        _log_result(result, success=True)
        return result


def _cache_key(context: dict[str, Any], model: str, prompt_version: str, provider: str) -> str:
    payload = json.dumps(
        {
            "context": context,
            "model": model,
            "prompt_version": prompt_version,
            "provider": provider,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _log_result(result: AIGenerationResult, success: bool) -> None:
    usage = result.usage
    logger.info(
        "provider=%s model=%s prompt_version=%s latency_ms=%s input_tokens=%s output_tokens=%s total_tokens=%s success=%s",
        result.provider,
        result.model,
        result.prompt_version,
        result.latency_ms,
        usage.input_tokens if usage else None,
        usage.output_tokens if usage else None,
        usage.total_tokens if usage else None,
        success,
    )

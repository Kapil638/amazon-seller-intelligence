from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.ai.base import AIGenerationResult, AIProvider
from app.ai.context import build_ai_listing_context
from app.core.config import get_settings
from app.models.ai_listing_intelligence import AIListingIntelligence
from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product
from app.prompts.listing_intelligence import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from app.providers.memory_cache import MemoryTtlValueCache

logger = logging.getLogger("app.ai.listing_intelligence")


class AIListingIntelligenceService:
    """Sits on top of deterministic ListingAnalysis. Knows nothing about OpenAI SDK types."""

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
        analysis: ListingAnalysis,
    ) -> AIGenerationResult:
        context = build_ai_listing_context(product, analysis)
        context_json = json.dumps(context, ensure_ascii=False, default=str)
        cache_key = _cache_key(context, self._provider.model, PROMPT_VERSION)
        cached = self._cache.get(cache_key)
        if cached is not None:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_openai_cache_hit()
            return cached

        try:
            result = await self._provider.generate_structured(
                schema=AIListingIntelligence,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(context_json),
                repair_prompt=build_repair_prompt(context_json),
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

        record_openai_generation("listing_intelligence", result)
        self._cache.set(cache_key, result)
        _log_result(result, success=True)
        return result


def _cache_key(context: dict[str, Any], model: str, prompt_version: str) -> str:
    payload = json.dumps(
        {
            "context": context,
            "model": model,
            "prompt_version": prompt_version,
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

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from app.ai.base import AIGenerationResult, AIProvider
from app.ai.competitive_context import build_ai_competitive_context
from app.core.config import get_settings
from app.models.ai_competitive_intelligence import AICompetitiveIntelligence
from app.models.competitor_comparison import CompetitorComparisonResponse
from app.prompts.competitive_intelligence import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from app.providers.memory_cache import MemoryTtlValueCache

logger = logging.getLogger("app.ai.competitive_intelligence")


class AICompetitiveIntelligenceService:
    """Sits on top of CompetitorComparison. Knows nothing about OpenAI SDK types."""

    def __init__(
        self,
        provider: AIProvider,
        cache: MemoryTtlValueCache | None = None,
    ) -> None:
        settings = get_settings()
        self._provider = provider
        self._cache = cache if cache is not None else MemoryTtlValueCache(settings.ai_cache_ttl_seconds)

    async def generate(self, comparison: CompetitorComparisonResponse) -> AIGenerationResult:
        context = build_ai_competitive_context(comparison)
        target_json = json.dumps(context["target"], ensure_ascii=False, default=str)
        competitors_json = json.dumps(context["competitors"], ensure_ascii=False, default=str)
        comparison_json = json.dumps(
            {
                "comparison": context["comparison"],
                "failed_competitors": context["failed_competitors"],
                "meta": context["meta"],
            },
            ensure_ascii=False,
            default=str,
        )
        cache_key = _cache_key(context, self._provider.model, PROMPT_VERSION)
        cached = self._cache.get(cache_key)
        if cached is not None:
            from app.usage.ledger import get_usage_ledger

            get_usage_ledger().record_openai_cache_hit()
            return cached

        try:
            result = await self._provider.generate_structured(
                schema=AICompetitiveIntelligence,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=build_user_prompt(target_json, competitors_json, comparison_json),
                repair_prompt=build_repair_prompt(target_json, competitors_json, comparison_json),
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

        record_openai_generation("competitive_intelligence", result)
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

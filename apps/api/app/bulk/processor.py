from __future__ import annotations

import hashlib
import json
from typing import Any

from app.ai.base import AIGenerationResult, AIProvider
from app.ai.context import build_ai_listing_context
from app.bulk.cache import KeyedTtlCache, product_cache_key
from app.bulk.portfolio import aggregate_portfolio, attention_results, sort_results
from app.bulk.priority import classify_priority
from app.core.exceptions import (
    ProductFetchFailedError,
    ProductNotFoundError,
    ProductParseFailedError,
)
from app.models.ai_listing_intelligence import AIListingIntelligence
from app.models.bulk import (
    BulkAISelection,
    BulkAnalysisMode,
    BulkASINProductResult,
    BulkFailure,
    BulkJobOptions,
    BulkUsageStats,
)
from app.models.listing_analysis import ListingAnalysis
from app.models.product import Product
from app.prompts.listing_intelligence import (
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_repair_prompt,
    build_user_prompt,
)
from app.providers.base import ProductDataProvider
from app.services.listing_analysis_service import ListingAnalysisService

ATTENTION_LIMIT = 20


class BulkProcessor:
    def __init__(
        self,
        product_provider: ProductDataProvider,
        product_cache: KeyedTtlCache,
        ai_provider: AIProvider | None,
        ai_cache: KeyedTtlCache,
        analysis: ListingAnalysisService | None = None,
        concurrency: int = 3,
    ) -> None:
        self._products = product_provider
        self._product_cache = product_cache
        self._ai = ai_provider
        self._ai_cache = ai_cache
        self._analysis = analysis or ListingAnalysisService()
        self._concurrency = max(concurrency, 1)

    async def process(
        self,
        asins: list[str],
        *,
        marketplace: str,
        options: BulkJobOptions,
        ingest_failures: list[BulkFailure],
        on_progress: Any | None = None,
    ) -> tuple[list[BulkASINProductResult], list[BulkFailure], BulkUsageStats]:
        import asyncio

        usage = BulkUsageStats(
            product_provider=self._products.name,
            ai_provider=self._ai.name if self._ai is not None and options.analysis_mode == "deep_ai" else None,
            paid_api_usage=False,
            note="Mock provider — no paid API usage",
            requested_asins=len(asins),
        )
        semaphore = asyncio.Semaphore(self._concurrency)
        results: list[BulkASINProductResult] = []
        failures: list[BulkFailure] = list(ingest_failures)
        lock = asyncio.Lock()

        async def run_one(asin: str) -> None:
            async with semaphore:
                result, failure, stats = await self._lookup_and_analyze(asin, marketplace)
            async with lock:
                usage.cache_hits += stats["cache_hits"]
                usage.provider_calls += stats["provider_calls"]
                usage.calls_saved += stats["calls_saved"]
                usage.retries += stats["retries"]
                if result is not None:
                    results.append(result)
                if failure is not None:
                    failures.append(failure)
                    usage.failures += 1
                if on_progress is not None:
                    on_progress(
                        processed=len(results) + sum(1 for item in failures if item.kind != "invalid"),
                        successful=len(results),
                        failed=sum(1 for item in failures if item.kind != "invalid"),
                        cache_hits=usage.cache_hits,
                        provider_calls=usage.provider_calls,
                    )

        await asyncio.gather(*(run_one(asin) for asin in asins))
        ordered = sort_results(results)

        if options.analysis_mode == "deep_ai":
            if self._ai is None:
                raise RuntimeError("Deep AI mode requires an AI provider")
            selected = select_ai_targets(ordered, options.ai_selection, options.top_n)
            usage.ai_eligible = len(selected)
            selected_asins = {item.asin for item in selected}
            for item in ordered:
                if item.asin not in selected_asins:
                    item.ai_status = "skipped"
                    continue
                intelligence, cache_hit = await self._generate_ai(item.product, item.listing_analysis)
                item.ai_intelligence = intelligence
                if cache_hit:
                    usage.ai_cache_hits += 1
                    usage.ai_calls_saved += 1
                    item.ai_status = "cached"
                else:
                    usage.ai_provider_calls += 1
                    item.ai_status = "mock"
        else:
            usage.ai_eligible = 0
            usage.ai_provider_calls = 0
            for item in ordered:
                item.ai_status = "not_requested"

        return ordered, failures, usage

    async def _lookup_and_analyze(
        self,
        asin: str,
        marketplace: str,
    ) -> tuple[BulkASINProductResult | None, BulkFailure | None, dict[str, int]]:
        stats = {"cache_hits": 0, "provider_calls": 0, "calls_saved": 0, "retries": 0}
        key = product_cache_key(self._products.name, marketplace, asin)
        cached = self._product_cache.get(key)
        if cached is not None:
            stats["cache_hits"] = 1
            stats["calls_saved"] = 1
            product = cached
            cache_hit = True
        else:
            product, failure, retries, calls = await self._fetch_with_retry(asin, marketplace)
            stats["retries"] = retries
            stats["provider_calls"] = calls
            if failure is not None:
                return None, failure, stats
            assert product is not None
            self._product_cache.set(key, product)
            cache_hit = False

        analysis = self._analysis.analyze(product)
        result = BulkASINProductResult(
            asin=asin,
            product=product,
            listing_analysis=analysis,
            ai_intelligence=None,
            priority=classify_priority(analysis),
            cache_hit=cache_hit,
        )
        return result, None, stats

    async def _fetch_with_retry(
        self,
        asin: str,
        marketplace: str,
    ) -> tuple[Product | None, BulkFailure | None, int, int]:
        retries = 0
        calls = 0
        last_transient: Exception | None = None
        for attempt in (1, 2):
            try:
                calls += 1
                product = await self._products.get_product(asin, marketplace)
            except ProductNotFoundError:
                return None, BulkFailure(
                    row=None,
                    input_asin=asin,
                    reason="Product was not found in the mock catalog.",
                    kind="not_found",
                ), retries, calls
            except ProductFetchFailedError as exc:
                last_transient = exc
                if attempt == 1:
                    retries += 1
                    continue
                return None, BulkFailure(
                    row=None,
                    input_asin=asin,
                    reason="Transient provider failure after one retry.",
                    kind="transient",
                ), retries, calls
            except ProductParseFailedError as exc:
                return None, BulkFailure(
                    row=None,
                    input_asin=asin,
                    reason=str(exc),
                    kind="provider",
                ), retries, calls
            if product is None:
                return None, BulkFailure(
                    row=None,
                    input_asin=asin,
                    reason="Product was not found in the mock catalog.",
                    kind="not_found",
                ), retries, calls
            return product, None, retries, calls
        reason = str(last_transient) if last_transient else "Provider failure."
        return None, BulkFailure(row=None, input_asin=asin, reason=reason, kind="provider"), retries, calls

    async def _generate_ai(
        self,
        product: Product,
        analysis: ListingAnalysis,
    ) -> tuple[AIListingIntelligence, bool]:
        assert self._ai is not None
        key = ai_cache_key(product, analysis, self._ai.name, self._ai.model, PROMPT_VERSION)
        cached = self._ai_cache.get(key)
        if cached is not None:
            return cached, True
        context = build_ai_listing_context(product, analysis)
        context_json = json.dumps(context, ensure_ascii=False, default=str)
        result: AIGenerationResult = await self._ai.generate_structured(
            schema=AIListingIntelligence,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=build_user_prompt(context_json),
            repair_prompt=build_repair_prompt(context_json),
            prompt_version=PROMPT_VERSION,
        )
        payload = result.payload
        self._ai_cache.set(key, payload)
        return payload, False


def select_ai_targets(
    results: list[BulkASINProductResult],
    selection: BulkAISelection,
    top_n: int,
) -> list[BulkASINProductResult]:
    if selection == "all":
        return list(results)
    if selection == "top_n":
        ordered = sort_results(results)
        return ordered[: max(top_n, 0)]
    return [item for item in results if item.priority == "high"]


def ai_cache_key(
    product: Product,
    analysis: ListingAnalysis,
    provider: str,
    model: str,
    prompt_version: str,
) -> str:
    payload = json.dumps(
        {
            "product": product.model_dump(mode="json"),
            "analysis": analysis.model_dump(mode="json"),
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_attention_and_summary(
    asins: list[str],
    results: list[BulkASINProductResult],
    failures: list[BulkFailure],
) -> tuple[list[BulkASINProductResult], Any]:
    summary = aggregate_portfolio(submitted=len(asins), results=results, failures=failures)
    attention = attention_results(results, ATTENTION_LIMIT)
    return attention, summary

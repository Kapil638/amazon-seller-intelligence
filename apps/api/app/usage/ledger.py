from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock
from typing import Literal

from app.models.usage import OpenAIAppUsage, RainforestAppUsage
from app.usage.openai_pricing import estimate_openai_cost_usd

RainforestCallKind = Literal["product", "search"]
AIWorkflow = Literal[
    "listing_intelligence",
    "listing_intelligence_v2",
    "image_intelligence_v1",
    "competitive_intelligence",
    "bulk_listing_intelligence",
    "portfolio_summary",
]


@dataclass
class OpenAICallRecord:
    timestamp: datetime
    workflow: AIWorkflow
    model: str
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    estimated_cost_usd: float | None


@dataclass
class ApplicationUsageLedger:
    """Process-lifetime application usage. Separate from provider-account bills."""

    rainforest_product_calls: int = 0
    rainforest_search_calls: int = 0
    rainforest_cache_hits: int = 0
    rainforest_failed_calls: int = 0
    rainforest_account_lookups: int = 0

    openai_requests: int = 0
    openai_cache_hits: int = 0
    openai_failed_calls: int = 0
    openai_input_tokens: int = 0
    openai_cached_input_tokens: int = 0
    openai_output_tokens: int = 0
    openai_total_tokens: int = 0
    openai_estimated_spend_usd: float = 0.0
    openai_priced_requests: int = 0
    openai_unpriced_requests: int = 0
    openai_calls: list[OpenAICallRecord] = field(default_factory=list)

    _lock: Lock = field(default_factory=Lock, repr=False)

    def reset(self) -> None:
        with self._lock:
            self.rainforest_product_calls = 0
            self.rainforest_search_calls = 0
            self.rainforest_cache_hits = 0
            self.rainforest_failed_calls = 0
            self.rainforest_account_lookups = 0
            self.openai_requests = 0
            self.openai_cache_hits = 0
            self.openai_failed_calls = 0
            self.openai_input_tokens = 0
            self.openai_cached_input_tokens = 0
            self.openai_output_tokens = 0
            self.openai_total_tokens = 0
            self.openai_estimated_spend_usd = 0.0
            self.openai_priced_requests = 0
            self.openai_unpriced_requests = 0
            self.openai_calls.clear()

    def record_rainforest_product_call(self) -> None:
        with self._lock:
            self.rainforest_product_calls += 1

    def record_rainforest_search_call(self) -> None:
        with self._lock:
            self.rainforest_search_calls += 1

    def record_rainforest_cache_hit(self, _kind: RainforestCallKind) -> None:
        with self._lock:
            self.rainforest_cache_hits += 1

    def record_rainforest_failure(self, _kind: RainforestCallKind) -> None:
        with self._lock:
            self.rainforest_failed_calls += 1

    def record_rainforest_account_lookup(self) -> None:
        """Account API is free. Tracked only so tests can prove it is not a paid product call."""
        with self._lock:
            self.rainforest_account_lookups += 1

    def record_openai_cache_hit(self) -> None:
        with self._lock:
            self.openai_cache_hits += 1

    def record_openai_failure(self) -> None:
        with self._lock:
            self.openai_failed_calls += 1

    def record_openai_call(
        self,
        *,
        workflow: AIWorkflow,
        model: str,
        input_tokens: int | None = None,
        cached_input_tokens: int | None = None,
        output_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> float | None:
        inp = max(int(input_tokens or 0), 0)
        cached = max(int(cached_input_tokens or 0), 0)
        out = max(int(output_tokens or 0), 0)
        total = int(total_tokens) if total_tokens is not None else inp + out
        cost = estimate_openai_cost_usd(
            model=model,
            input_tokens=inp,
            cached_input_tokens=cached,
            output_tokens=out,
        )
        record = OpenAICallRecord(
            timestamp=datetime.now(UTC),
            workflow=workflow,
            model=model,
            input_tokens=inp,
            cached_input_tokens=cached,
            output_tokens=out,
            estimated_cost_usd=cost,
        )
        with self._lock:
            self.openai_requests += 1
            self.openai_input_tokens += inp
            self.openai_cached_input_tokens += cached
            self.openai_output_tokens += out
            self.openai_total_tokens += max(total, 0)
            if cost is None:
                self.openai_unpriced_requests += 1
            else:
                self.openai_priced_requests += 1
                self.openai_estimated_spend_usd += cost
            self.openai_calls.append(record)
            if len(self.openai_calls) > 500:
                self.openai_calls = self.openai_calls[-500:]
        return cost

    def rainforest_app_snapshot(self) -> RainforestAppUsage:
        with self._lock:
            return RainforestAppUsage(
                product_calls=self.rainforest_product_calls,
                search_calls=self.rainforest_search_calls,
                cache_hits=self.rainforest_cache_hits,
                calls_saved=self.rainforest_cache_hits,
                failed_calls=self.rainforest_failed_calls,
            )

    def openai_app_snapshot(self) -> OpenAIAppUsage:
        with self._lock:
            requests = self.openai_requests
            unpriced = self.openai_unpriced_requests
            priced = self.openai_priced_requests
            if requests == 0:
                cost_status: Literal["ok", "unavailable", "partial"] = "ok"
                estimated: float | None = 0.0
            elif priced == 0:
                cost_status = "unavailable"
                estimated = None
            elif unpriced > 0:
                cost_status = "partial"
                estimated = round(self.openai_estimated_spend_usd, 8)
            else:
                cost_status = "ok"
                estimated = round(self.openai_estimated_spend_usd, 8)
            return OpenAIAppUsage(
                estimated_spend_usd=estimated,
                cost_status=cost_status,
                requests=requests,
                input_tokens=self.openai_input_tokens,
                cached_input_tokens=self.openai_cached_input_tokens,
                output_tokens=self.openai_output_tokens,
                total_tokens=self.openai_total_tokens,
                cache_hits=self.openai_cache_hits,
                calls_saved=self.openai_cache_hits,
                failed_calls=self.openai_failed_calls,
                unpriced_requests=unpriced,
            )


_LEDGER = ApplicationUsageLedger()


def get_usage_ledger() -> ApplicationUsageLedger:
    return _LEDGER

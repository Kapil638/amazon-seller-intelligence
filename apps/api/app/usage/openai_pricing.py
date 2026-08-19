"""Application-side OpenAI cost estimates.

This is NOT the OpenAI invoice. Prices are centralized and versioned so they
can be updated in one place when OpenAI changes public list rates.

Unknown models return None. Do not invent a price.

PRICING_VERSION records the public list-price snapshot used below
(standard short-context API rates, USD per 1M tokens). Long-context
surcharges, batch, flex, priority, and regional uplifts are not applied.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICING_VERSION = "2026-08-19"

# USD per 1,000,000 tokens. Cached input is OpenAI prompt-cache reads, not our app cache.
_PRICES: dict[str, tuple[float, float, float]] = {
    # model: (input, cached_input, output)
    "gpt-5.4": (2.50, 0.25, 15.00),
    "gpt-5.4-mini": (0.75, 0.075, 4.50),
    "gpt-5.4-nano": (0.20, 0.02, 1.25),
    "gpt-5.2": (1.75, 0.175, 14.00),
    "gpt-5": (1.25, 0.125, 10.00),
    "gpt-5-mini": (0.25, 0.025, 2.00),
    "gpt-5-nano": (0.05, 0.005, 0.40),
    "gpt-4o": (2.50, 1.25, 10.00),
    "gpt-4o-mini": (0.15, 0.075, 0.60),
}

_ALIASES: dict[str, str] = {
    "gpt-5.4-2026-03-05": "gpt-5.4",
}


@dataclass(frozen=True)
class OpenAIModelPricing:
    model: str
    input_price_per_million: float
    cached_input_price_per_million: float
    output_price_per_million: float
    pricing_version: str = PRICING_VERSION


def normalize_model_name(model: str | None) -> str:
    return (model or "").strip()


def lookup_openai_pricing(model: str | None) -> OpenAIModelPricing | None:
    key = normalize_model_name(model)
    if not key:
        return None
    canonical = _ALIASES.get(key, key)
    prices = _PRICES.get(canonical)
    if prices is None:
        return None
    input_price, cached_price, output_price = prices
    return OpenAIModelPricing(
        model=canonical,
        input_price_per_million=input_price,
        cached_input_price_per_million=cached_price,
        output_price_per_million=output_price,
    )


def estimate_openai_cost_usd(
    *,
    model: str | None,
    input_tokens: int | None = None,
    cached_input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> float | None:
    """Return estimated USD cost, or None when the model has no configured price."""

    pricing = lookup_openai_pricing(model)
    if pricing is None:
        return None

    total_input = max(int(input_tokens or 0), 0)
    cached = min(max(int(cached_input_tokens or 0), 0), total_input)
    uncached = total_input - cached
    output = max(int(output_tokens or 0), 0)

    cost = (
        (uncached / 1_000_000) * pricing.input_price_per_million
        + (cached / 1_000_000) * pricing.cached_input_price_per_million
        + (output / 1_000_000) * pricing.output_price_per_million
    )
    return round(cost, 8)

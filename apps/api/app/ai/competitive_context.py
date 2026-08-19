from __future__ import annotations

from typing import Any

from app.ai.context import build_ai_listing_context
from app.models.competitor_comparison import CompetitorComparisonResponse


def build_ai_competitive_context(payload: CompetitorComparisonResponse) -> dict[str, Any]:
    """Compact comparison context. Never includes Rainforest/HTML/provider payloads."""

    return {
        "target": build_ai_listing_context(payload.target.product, payload.target.analysis),
        "competitors": [
            build_ai_listing_context(item.product, item.analysis) for item in payload.competitors
        ],
        "comparison": {
            "metrics": [metric.model_dump() for metric in payload.comparison.metrics],
            "gaps": [gap.model_dump() for gap in payload.comparison.gaps],
            "price_deltas": [delta.model_dump() for delta in payload.comparison.price_deltas],
            "summary": payload.comparison.summary.model_dump(),
        },
        "failed_competitors": [item.model_dump() for item in payload.failed_competitors],
        "meta": payload.meta.model_dump(),
    }

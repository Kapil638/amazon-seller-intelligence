"""Listing V2 tool. Scoring stays in ListingAnalysisV2Service."""

from __future__ import annotations

from app.copilot.budget import COST_RAINFOREST_PRODUCT
from app.copilot.evidence import EvidenceEnvelope, claim, envelope
from app.copilot.listing_evidence import listing_analysis_claims
from app.copilot.registry import ToolDefinition, ToolRegistry
from app.copilot.schemas import AnalyzeListingV2Input
from app.core.config import get_settings
from app.providers.factory import get_product_provider
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from app.services.product_service import ProductService


def register(
    registry: ToolRegistry,
    *,
    listing: ListingAnalysisV2Service | None = None,
    products: ProductService | None = None,
) -> None:
    analyzer = listing or ListingAnalysisV2Service()
    product_service = products or ProductService(provider=get_product_provider())

    async def handler(payload: AnalyzeListingV2Input) -> EvidenceEnvelope:
        return await _analyze_listing_v2(analyzer, product_service, payload)

    registry.register(
        ToolDefinition(
            name="analyze_listing_v2",
            description=(
                "Run Listing Intelligence V2 for an ASIN. Loads the product through "
                "ProductService, then scores with the deterministic V2 engine. "
                "Does not accept a product payload."
            ),
            input_schema=AnalyzeListingV2Input,
            handler=handler,  # type: ignore[arg-type]
            estimated_provider_cost=COST_RAINFOREST_PRODUCT,
        )
    )


async def _analyze_listing_v2(
    analyzer: ListingAnalysisV2Service,
    products: ProductService,
    payload: AnalyzeListingV2Input,
) -> EvidenceEnvelope:
    marketplace = payload.marketplace or get_settings().default_marketplace
    product, origin = await products.fetch_product(payload.asin, marketplace)
    analysis = analyzer.analyze(product)
    signals = analysis.market_signals
    market = {
        "rating": signals.rating,
        "review_count": signals.review_count,
        "availability": signals.availability,
        "price_amount": signals.price.amount if signals.price else None,
        "price_currency": signals.price.currency if signals.price else None,
    }
    return envelope(
        "analyze_listing_v2",
        [
            claim("asin", product.asin, kind="observed", source=origin),
            *listing_analysis_claims(analysis, kind="calculated", source="derived"),
            claim("status", analysis.status.value, kind="calculated", source="derived"),
            claim(
                "coverage_overall_percentage",
                analysis.data_coverage.overall_percentage,
                kind="calculated",
                source="derived",
            ),
            claim("market_signals", market, kind="observed", source=origin),
        ],
    )

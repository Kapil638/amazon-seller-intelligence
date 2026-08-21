"""Product lookup tool. Wraps ProductService; provider cache is unchanged."""

from __future__ import annotations

from app.copilot.budget import COST_RAINFOREST_PRODUCT
from app.copilot.evidence import EvidenceEnvelope, claim, envelope
from app.copilot.registry import ToolDefinition, ToolRegistry
from app.copilot.schemas import GetProductInput
from app.core.config import get_settings
from app.providers.factory import get_product_provider
from app.services.product_service import ProductService


def register(registry: ToolRegistry, products: ProductService | None = None) -> None:
    service = products or ProductService(provider=get_product_provider())

    async def handler(payload: GetProductInput) -> EvidenceEnvelope:
        return await _get_product(service, payload)

    registry.register(
        ToolDefinition(
            name="get_product",
            description="Load a normalized product by ASIN. May use Amazon product credits on a cache miss.",
            input_schema=GetProductInput,
            handler=handler,  # type: ignore[arg-type]
            estimated_provider_cost=COST_RAINFOREST_PRODUCT,
        )
    )


async def _get_product(products: ProductService, payload: GetProductInput) -> EvidenceEnvelope:
    marketplace = payload.marketplace or get_settings().default_marketplace
    product, origin = await products.fetch_product(payload.asin, marketplace)
    as_of = product.last_fetched_at
    return envelope(
        "get_product",
        [
            claim("asin", product.asin, kind="observed", source=origin, as_of=as_of),
            claim("marketplace", product.marketplace, kind="observed", source=origin, as_of=as_of),
            claim("title", product.title, kind="observed", source=origin, as_of=as_of),
            claim("brand", product.brand, kind="observed", source=origin, as_of=as_of),
            claim(
                "price_amount",
                product.price.amount if product.price else None,
                kind="observed" if product.price else "unknown",
                source=origin,
                as_of=as_of,
                confidence="high" if product.price else "none",
            ),
            claim(
                "price_currency",
                product.price.currency if product.price else None,
                kind="observed" if product.price else "unknown",
                source=origin,
                as_of=as_of,
                confidence="high" if product.price else "none",
            ),
            claim("provider_source", origin, kind="observed", source=origin, as_of=as_of),
        ],
    )

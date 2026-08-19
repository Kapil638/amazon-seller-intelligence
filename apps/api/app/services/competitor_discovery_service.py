from app.analytics.competitor_relevance import RELEVANCE_VERSION, score_relevance
from app.analytics.competitor_search_query import QUERY_VERSION
from app.core.exceptions import UnsupportedMarketplaceError
from app.core.config import get_settings
from app.models.competitor_discovery import (
    DISCOVERY_VERSION,
    MAX_DISPLAYED_CANDIDATES,
    CompetitorDiscoveryMeta,
    CompetitorDiscoveryResult,
    DiscoveredProductCandidate,
)
from app.models.product import Product
from app.search.base import AmazonSearchHit, AmazonSearchProvider
from app.services.competitor_search_query_service import CompetitorSearchQueryService


class CompetitorDiscoveryService:
    """Discover candidate Amazon listings. Does not fetch full Products or call AI."""

    def __init__(
        self,
        search_provider: AmazonSearchProvider,
        query_service: CompetitorSearchQueryService | None = None,
        max_candidates: int = MAX_DISPLAYED_CANDIDATES,
    ) -> None:
        self._search = search_provider
        self._queries = query_service or CompetitorSearchQueryService()
        self._max_candidates = max_candidates

    async def discover(
        self,
        target_product: Product,
        search_query: str | None = None,
        marketplace: str | None = None,
    ) -> CompetitorDiscoveryResult:
        settings = get_settings()
        resolved_marketplace = marketplace or target_product.marketplace or settings.default_marketplace
        if resolved_marketplace not in settings.supported_marketplaces:
            raise UnsupportedMarketplaceError(resolved_marketplace)

        query, generated = self._queries.resolve(target_product, search_query)
        hits = await self._search.search(query, resolved_marketplace)
        candidates = _prepare_candidates(
            target=target_product,
            hits=hits,
            search_query=query,
            limit=self._max_candidates,
        )
        return CompetitorDiscoveryResult(
            target_asin=target_product.asin,
            search_query=query,
            candidates=candidates,
            meta=CompetitorDiscoveryMeta(
                provider=self._search.name,
                marketplace=resolved_marketplace,
                discovery_version=DISCOVERY_VERSION,
                query_generated=generated,
                query_version=QUERY_VERSION,
                relevance_version=RELEVANCE_VERSION,
                result_count=len(hits),
                displayed_count=len(candidates),
            ),
        )


def _prepare_candidates(
    target: Product,
    hits: list[AmazonSearchHit],
    search_query: str,
    limit: int,
) -> list[DiscoveredProductCandidate]:
    target_asin = target.asin.strip().upper()
    seen: set[str] = set()
    ranked: list[DiscoveredProductCandidate] = []
    for hit in hits:
        asin = hit.asin.strip().upper()
        if asin == target_asin or asin in seen:
            continue
        seen.add(asin)
        ranked.append(
            DiscoveredProductCandidate(
                asin=asin,
                title=hit.title,
                brand=hit.brand,
                price=hit.price,
                currency=hit.currency,
                rating=hit.rating,
                review_count=hit.review_count,
                image=hit.image,
                position=hit.position,
                is_sponsored=hit.is_sponsored,
                category=hit.category,
                search_query=search_query,
                relevance_score=score_relevance(target, hit, search_query),
            )
        )
    ranked.sort(key=lambda item: (-item.relevance_score, item.position or 10_000, item.asin))
    return ranked[:limit]

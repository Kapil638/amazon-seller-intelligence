from fastapi import APIRouter, Depends, HTTPException

from app.core.exceptions import (
    ProviderConfigurationError,
    SearchBlockedError,
    SearchFetchFailedError,
    SearchParseFailedError,
    SearchQueryValidationError,
    UnsupportedMarketplaceError,
)
from app.models.competitor_discovery import (
    CompetitorDiscoveryRequest,
    CompetitorDiscoveryResult,
    CompetitorSearchQueryRequest,
    CompetitorSearchQueryResponse,
)
from app.search.factory import get_search_provider
from app.services.competitor_discovery_service import CompetitorDiscoveryService
from app.services.competitor_search_query_service import CompetitorSearchQueryService

router = APIRouter(prefix="/api/v1/competitors", tags=["competitors"])


def get_competitor_discovery_service() -> CompetitorDiscoveryService:
    return CompetitorDiscoveryService(search_provider=get_search_provider())


def get_competitor_search_query_service() -> CompetitorSearchQueryService:
    return CompetitorSearchQueryService()


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, SearchQueryValidationError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, UnsupportedMarketplaceError):
        return HTTPException(status_code=400, detail=f"Unsupported marketplace: {exc.marketplace}")
    if isinstance(exc, ProviderConfigurationError):
        return HTTPException(status_code=503, detail="Competitor discovery is temporarily unavailable.")
    if isinstance(exc, SearchBlockedError):
        return HTTPException(status_code=503, detail="Competitor discovery is temporarily unavailable.")
    if isinstance(exc, (SearchFetchFailedError, SearchParseFailedError)):
        return HTTPException(status_code=502, detail="Competitor discovery is temporarily unavailable.")
    raise exc


@router.post("/query", response_model=CompetitorSearchQueryResponse)
def generate_competitor_query(
    payload: CompetitorSearchQueryRequest,
    service: CompetitorSearchQueryService = Depends(get_competitor_search_query_service),
) -> CompetitorSearchQueryResponse:
    query = service.generate(payload.target_product)
    return CompetitorSearchQueryResponse(
        search_query=query,
        meta={"query_version": "v1"},
    )


@router.post("/discover", response_model=CompetitorDiscoveryResult)
async def discover_competitors(
    payload: CompetitorDiscoveryRequest,
    service: CompetitorDiscoveryService = Depends(get_competitor_discovery_service),
) -> CompetitorDiscoveryResult:
    try:
        return await service.discover(
            target_product=payload.target_product,
            search_query=payload.search_query,
            marketplace=payload.marketplace,
        )
    except (
        SearchQueryValidationError,
        UnsupportedMarketplaceError,
        ProviderConfigurationError,
        SearchBlockedError,
        SearchFetchFailedError,
        SearchParseFailedError,
    ) as exc:
        raise _http_error(exc) from exc

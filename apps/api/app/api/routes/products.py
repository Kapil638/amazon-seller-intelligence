from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.config import get_settings
from app.core.exceptions import (
    ProductFetchBlockedError,
    ProductFetchFailedError,
    ProductNotFoundError,
    ProductParseFailedError,
    ProviderConfigurationError,
    UnsupportedMarketplaceError,
)
from app.models.manual import ManualProductInput
from app.models.product import ProductMeta, ProductResponse, ProductSource
from app.providers.factory import get_product_provider
from app.services.product_service import ProductService

router = APIRouter(prefix="/api/v1/products", tags=["products"])


def get_product_service() -> ProductService:
    return ProductService(provider=get_product_provider())


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    if isinstance(exc, UnsupportedMarketplaceError):
        return HTTPException(
            status_code=400,
            detail=f"Unsupported marketplace: {exc.marketplace}",
        )
    if isinstance(exc, ProductNotFoundError):
        return HTTPException(
            status_code=404,
            detail=f"Product {exc.asin} was not found for marketplace {exc.marketplace}",
        )
    if isinstance(exc, ProviderConfigurationError):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, ProductFetchBlockedError):
        return HTTPException(
            status_code=503,
            detail=exc.reason
            or "This product lookup is temporarily unavailable. Try again later, or enter the listing manually.",
        )
    if isinstance(exc, (ProductFetchFailedError, ProductParseFailedError)):
        return HTTPException(
            status_code=502,
            detail="Could not retrieve this Amazon.in listing. Try again later, or enter the listing manually.",
        )
    raise exc


@router.post("/manual", response_model=ProductResponse)
def create_manual_product(
    payload: ManualProductInput,
    service: ProductService = Depends(get_product_service),
) -> ProductResponse:
    try:
        product = service.create_from_manual(payload)
    except (ValueError, UnsupportedMarketplaceError) as exc:
        raise _http_error(exc) from exc
    return ProductResponse(
        product=product,
        meta=ProductMeta(source=ProductSource.MANUAL),
    )


@router.get("/{asin}", response_model=ProductResponse)
async def get_product(
    asin: str,
    marketplace: str | None = Query(
        default=None,
        description="Amazon marketplace domain identifier. Defaults to amazon.in.",
        examples=["amazon.in"],
    ),
    service: ProductService = Depends(get_product_service),
) -> ProductResponse:
    settings = get_settings()
    resolved_marketplace = marketplace or settings.default_marketplace
    try:
        product = await service.get_product(asin, resolved_marketplace)
    except (
        ValueError,
        UnsupportedMarketplaceError,
        ProductNotFoundError,
        ProductFetchBlockedError,
        ProductFetchFailedError,
        ProductParseFailedError,
        ProviderConfigurationError,
    ) as exc:
        raise _http_error(exc) from exc
    return ProductResponse(
        product=product,
        meta=ProductMeta(source=ProductSource(service.provider_name)),
    )

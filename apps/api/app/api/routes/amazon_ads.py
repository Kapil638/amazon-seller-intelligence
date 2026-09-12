"""Amazon Ads read-only data HTTP API. 12C foundation. Every endpoint
here requires normal API authentication (Cloudflare Access once enabled
in production — see `app.core.cloudflare_access`; none of these paths
are in `PUBLIC_PATHS`) and is organization + advertiser-profile scoped.
Never returns tokens, secret references, internal lease details, or raw
Amazon responses — see each response model in `app.amazon.ads_read`.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query

from app.amazon.ads_connection import AmazonAdsConnectionService, get_amazon_ads_connection_service
from app.amazon.ads_read import (
    AdsAdGroupPage,
    AdsAdvertisedProductPage,
    AdsCampaignPage,
    AdsKeywordPage,
    AdsOverview,
    AdsPerformanceSeries,
    AdsProductTargetPage,
    AdsSyncStatusRead,
    AmazonAdsReadService,
    DEFAULT_PAGE_SIZE,
    get_amazon_ads_read_service,
)
from app.core.exceptions import AdsProfileNotFoundError, AdsProfileNotSelectedError

router = APIRouter(prefix="/api/v1/amazon/ads", tags=["amazon-ads"])


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AdsProfileNotFoundError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, AdsProfileNotSelectedError):
        return HTTPException(status_code=409, detail=str(exc))
    raise exc


def _default_window() -> tuple[date, date]:
    end = date.today()
    return end - timedelta(days=30), end


@router.get("/sync-status", response_model=AdsSyncStatusRead)
def get_ads_sync_status(
    ads_profile_id: str = Query(...),
    read_service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
    connection_service: AmazonAdsConnectionService = Depends(get_amazon_ads_connection_service),
) -> AdsSyncStatusRead:
    try:
        return read_service.sync_status(ads_profile_id, configured=connection_service.is_configured())
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/overview", response_model=AdsOverview)
def get_ads_overview(
    ads_profile_id: str = Query(...),
    start: date | None = None,
    end: date | None = None,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsOverview:
    default_start, default_end = _default_window()
    try:
        return service.overview(ads_profile_id, start=start or default_start, end=end or default_end)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/performance", response_model=AdsPerformanceSeries)
def get_ads_performance_series(
    ads_profile_id: str = Query(...),
    start: date | None = None,
    end: date | None = None,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsPerformanceSeries:
    default_start, default_end = _default_window()
    try:
        return service.performance_series(ads_profile_id, start=start or default_start, end=end or default_end)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/campaigns", response_model=AdsCampaignPage)
def get_ads_campaigns(
    ads_profile_id: str = Query(...),
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsCampaignPage:
    try:
        return service.campaigns(ads_profile_id, offset=offset, limit=limit)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/ad-groups", response_model=AdsAdGroupPage)
def get_ads_ad_groups(
    ads_profile_id: str = Query(...),
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsAdGroupPage:
    try:
        return service.ad_groups(ads_profile_id, offset=offset, limit=limit)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/keywords", response_model=AdsKeywordPage)
def get_ads_keywords(
    ads_profile_id: str = Query(...),
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsKeywordPage:
    try:
        return service.keywords(ads_profile_id, offset=offset, limit=limit)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/product-targets", response_model=AdsProductTargetPage)
def get_ads_product_targets(
    ads_profile_id: str = Query(...),
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsProductTargetPage:
    try:
        return service.product_targets(ads_profile_id, offset=offset, limit=limit)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc


@router.get("/advertised-products", response_model=AdsAdvertisedProductPage)
def get_ads_advertised_products(
    ads_profile_id: str = Query(...),
    offset: int = 0,
    limit: int = DEFAULT_PAGE_SIZE,
    service: AmazonAdsReadService = Depends(get_amazon_ads_read_service),
) -> AdsAdvertisedProductPage:
    try:
        return service.advertised_products(ads_profile_id, offset=offset, limit=limit)
    except AdsProfileNotFoundError as exc:
        raise _http_error(exc) from exc

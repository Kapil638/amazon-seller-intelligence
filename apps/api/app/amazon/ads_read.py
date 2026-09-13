"""Read-only API surface for Amazon Ads data. 12C foundation. Every
method here is organization-scoped and, once a profile is resolved,
profile-scoped — never returns another organization's or another
profile's rows. Never returns a token, secret reference, internal lease
detail, or a raw Amazon response (see each response model's `extra`/
field set below — narrower than the persisted row by construction, not
by after-the-fact filtering)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from app.core.exceptions import AdsProfileNotFoundError, AdsProfileNotSelectedError
from app.persistence.database import current_organization_id, session_scope
from app.persistence.repositories import (
    AmazonAdsAdGroupRepository,
    AmazonAdsAdvertisedProductRepository,
    AmazonAdsCampaignRepository,
    AmazonAdsConnectionRepository,
    AmazonAdsDailyPerformanceFactRepository,
    AmazonAdsKeywordRepository,
    AmazonAdsProductTargetRepository,
    AmazonAdsProfileRepository,
    AmazonAdsReportRunRepository,
    AmazonAdsSyncCheckpointRepository,
)

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25

SyncStatus = Literal[
    "not_configured",
    "not_connected",
    "connected_no_profile",
    "awaiting_first_sync",
    "synced",
    "delayed",
    "failed",
]


class AdsPageMeta(BaseModel):
    model_config = ConfigDict(extra="forbid")

    offset: int
    limit: int
    total: int


class AdsCampaignRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    external_campaign_id: str
    name: str
    state: str
    targeting_type: str | None
    daily_budget: Decimal | None
    currency_code: str | None
    start_date: date | None
    end_date: date | None


class AdsCampaignPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdsCampaignRead]
    page: AdsPageMeta


class AdsAdGroupRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    external_ad_group_id: str
    campaign_id: str
    name: str
    state: str
    default_bid: Decimal | None
    currency_code: str | None


class AdsAdGroupPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdsAdGroupRead]
    page: AdsPageMeta


class AdsKeywordRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    external_keyword_id: str
    ad_group_id: str
    keyword_text: str
    match_type: str
    state: str
    bid: Decimal | None
    currency_code: str | None


class AdsKeywordPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdsKeywordRead]
    page: AdsPageMeta


class AdsProductTargetRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    external_target_id: str
    ad_group_id: str
    expression_type: str | None
    expression: str | None
    state: str
    bid: Decimal | None
    currency_code: str | None


class AdsProductTargetPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdsProductTargetRead]
    page: AdsPageMeta


class AdsAdvertisedProductRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    external_ad_id: str
    ad_group_id: str
    asin: str | None
    sku: str | None
    state: str


class AdsAdvertisedProductPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[AdsAdvertisedProductRead]
    page: AdsPageMeta


class AdsOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_date: date
    end_date: date
    currency_code: str | None
    spend: Decimal
    attributed_sales: Decimal
    impressions: int
    clicks: int
    attributed_orders: int
    acos: Decimal | None
    roas: Decimal | None
    ctr: Decimal | None
    cpc: Decimal | None


class AdsPerformancePoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    spend: Decimal
    attributed_sales: Decimal
    impressions: int
    clicks: int
    attributed_orders: int


class AdsPerformanceSeries(BaseModel):
    model_config = ConfigDict(extra="forbid")

    points: list[AdsPerformancePoint]


class AdsSyncStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: SyncStatus
    last_synced_at: str | None
    synced_through_date: date | None
    last_report_status: str | None


def _clamp_page(offset: int, limit: int) -> tuple[int, int]:
    return max(offset, 0), max(1, min(limit, MAX_PAGE_SIZE))


def _require_profile(session, organization_id: UUID, ads_profile_id: str):
    try:
        profile_uuid = UUID(ads_profile_id)
    except ValueError:
        raise AdsProfileNotFoundError(ads_profile_id) from None
    profile = AmazonAdsProfileRepository(session).get_owned(organization_id, profile_uuid)
    if profile is None:
        raise AdsProfileNotFoundError(ads_profile_id)
    return profile


class AmazonAdsReadService:
    def _org_id(self) -> UUID:
        return current_organization_id()

    def sync_status(self, ads_profile_id: str, *, configured: bool) -> AdsSyncStatusRead:
        org_id = self._org_id()
        with session_scope() as session:
            connection = AmazonAdsConnectionRepository(session).get_for_org(org_id)
            if not configured:
                return AdsSyncStatusRead(
                    status="not_configured", last_synced_at=None, synced_through_date=None, last_report_status=None
                )
            if connection is None or connection.status != "connected":
                return AdsSyncStatusRead(
                    status="not_connected", last_synced_at=None, synced_through_date=None, last_report_status=None
                )
            profile = _require_profile(session, org_id, ads_profile_id)
            checkpoint = AmazonAdsSyncCheckpointRepository(session).get(profile.id)
            recent_runs = AmazonAdsReportRunRepository(session).list_for_profile(org_id, profile.id, limit=1)
            last_run = recent_runs[0] if recent_runs else None
            status: SyncStatus
            if profile.sync_state == "failed" or (last_run is not None and last_run.status == "failed"):
                status = "failed"
            elif last_run is not None and last_run.status in ("started", "waiting_to_retry"):
                status = "delayed"
            elif checkpoint is None or checkpoint.synced_through_date is None:
                status = "awaiting_first_sync"
            else:
                status = "synced"
            return AdsSyncStatusRead(
                status=status,
                last_synced_at=profile.last_synced_at.isoformat() if profile.last_synced_at else None,
                synced_through_date=checkpoint.synced_through_date if checkpoint else None,
                last_report_status=last_run.status if last_run else None,
            )

    def overview(self, ads_profile_id: str, *, start: date, end: date) -> AdsOverview:
        org_id = self._org_id()
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            totals = AmazonAdsDailyPerformanceFactRepository(session).overview_totals(
                org_id, profile.id, start=start, end=end
            )
            currency_code = profile.currency_code
        spend = Decimal(totals["spend"])
        sales = Decimal(totals["attributed_sales"])
        impressions = int(totals["impressions"])
        clicks = int(totals["clicks"])
        orders = int(totals["attributed_conversions"])
        acos = (spend / sales * 100) if sales else None
        roas = (sales / spend) if spend else None
        ctr = (Decimal(clicks) / Decimal(impressions) * 100) if impressions else None
        cpc = (spend / Decimal(clicks)) if clicks else None
        return AdsOverview(
            start_date=start,
            end_date=end,
            currency_code=currency_code,
            spend=spend,
            attributed_sales=sales,
            impressions=impressions,
            clicks=clicks,
            attributed_orders=orders,
            acos=acos,
            roas=roas,
            ctr=ctr,
            cpc=cpc,
        )

    def performance_series(self, ads_profile_id: str, *, start: date, end: date) -> AdsPerformanceSeries:
        org_id = self._org_id()
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            facts = AmazonAdsDailyPerformanceFactRepository(session).series_for_profile(
                org_id, profile.id, start=start, end=end, entity_type="campaign"
            )
        by_date: dict[date, dict] = {}
        for fact in facts:
            bucket = by_date.setdefault(
                fact.fact_date, {"spend": Decimal("0"), "sales": Decimal("0"), "impressions": 0, "clicks": 0, "orders": 0}
            )
            bucket["spend"] += fact.cost
            bucket["sales"] += fact.attributed_sales
            bucket["impressions"] += fact.impressions
            bucket["clicks"] += fact.clicks
            bucket["orders"] += fact.attributed_conversions
        points = [
            AdsPerformancePoint(
                date=d,
                spend=v["spend"],
                attributed_sales=v["sales"],
                impressions=v["impressions"],
                clicks=v["clicks"],
                attributed_orders=v["orders"],
            )
            for d, v in sorted(by_date.items())
        ]
        return AdsPerformanceSeries(points=points)

    def campaigns(self, ads_profile_id: str, *, offset: int, limit: int) -> AdsCampaignPage:
        org_id = self._org_id()
        offset, limit = _clamp_page(offset, limit)
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            rows, total = AmazonAdsCampaignRepository(session).list_for_profile(
                org_id, profile.id, offset=offset, limit=limit
            )
            items = [
                AdsCampaignRead(
                    id=str(r.id),
                    external_campaign_id=r.external_campaign_id,
                    name=r.name,
                    state=r.state,
                    targeting_type=r.targeting_type,
                    daily_budget=r.daily_budget,
                    currency_code=r.currency_code,
                    start_date=r.start_date,
                    end_date=r.end_date,
                )
                for r in rows
            ]
        return AdsCampaignPage(items=items, page=AdsPageMeta(offset=offset, limit=limit, total=total))

    def ad_groups(self, ads_profile_id: str, *, offset: int, limit: int) -> AdsAdGroupPage:
        org_id = self._org_id()
        offset, limit = _clamp_page(offset, limit)
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            rows, total = AmazonAdsAdGroupRepository(session).list_for_profile(
                org_id, profile.id, offset=offset, limit=limit
            )
            items = [
                AdsAdGroupRead(
                    id=str(r.id),
                    external_ad_group_id=r.external_ad_group_id,
                    campaign_id=str(r.ads_campaign_id),
                    name=r.name,
                    state=r.state,
                    default_bid=r.default_bid,
                    currency_code=r.currency_code,
                )
                for r in rows
            ]
        return AdsAdGroupPage(items=items, page=AdsPageMeta(offset=offset, limit=limit, total=total))

    def keywords(self, ads_profile_id: str, *, offset: int, limit: int) -> AdsKeywordPage:
        org_id = self._org_id()
        offset, limit = _clamp_page(offset, limit)
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            rows, total = AmazonAdsKeywordRepository(session).list_for_profile(
                org_id, profile.id, offset=offset, limit=limit
            )
            items = [
                AdsKeywordRead(
                    id=str(r.id),
                    external_keyword_id=r.external_keyword_id,
                    ad_group_id=str(r.ads_ad_group_id),
                    keyword_text=r.keyword_text,
                    match_type=r.match_type,
                    state=r.state,
                    bid=r.bid,
                    currency_code=r.currency_code,
                )
                for r in rows
            ]
        return AdsKeywordPage(items=items, page=AdsPageMeta(offset=offset, limit=limit, total=total))

    def product_targets(self, ads_profile_id: str, *, offset: int, limit: int) -> AdsProductTargetPage:
        org_id = self._org_id()
        offset, limit = _clamp_page(offset, limit)
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            rows, total = AmazonAdsProductTargetRepository(session).list_for_profile(
                org_id, profile.id, offset=offset, limit=limit
            )
            items = [
                AdsProductTargetRead(
                    id=str(r.id),
                    external_target_id=r.external_target_id,
                    ad_group_id=str(r.ads_ad_group_id),
                    expression_type=r.expression_type,
                    expression=r.expression,
                    state=r.state,
                    bid=r.bid,
                    currency_code=r.currency_code,
                )
                for r in rows
            ]
        return AdsProductTargetPage(items=items, page=AdsPageMeta(offset=offset, limit=limit, total=total))

    def advertised_products(self, ads_profile_id: str, *, offset: int, limit: int) -> AdsAdvertisedProductPage:
        org_id = self._org_id()
        offset, limit = _clamp_page(offset, limit)
        with session_scope() as session:
            profile = _require_profile(session, org_id, ads_profile_id)
            rows, total = AmazonAdsAdvertisedProductRepository(session).list_for_profile(
                org_id, profile.id, offset=offset, limit=limit
            )
            items = [
                AdsAdvertisedProductRead(
                    id=str(r.id),
                    external_ad_id=r.external_ad_id,
                    ad_group_id=str(r.ads_ad_group_id),
                    asin=r.asin,
                    sku=r.sku,
                    state=r.state,
                )
                for r in rows
            ]
        return AdsAdvertisedProductPage(items=items, page=AdsPageMeta(offset=offset, limit=limit, total=total))


def require_selected_profile_id(profile_id: str | None) -> str:
    if not profile_id or not profile_id.strip():
        raise AdsProfileNotSelectedError()
    return profile_id.strip()


def get_amazon_ads_read_service() -> AmazonAdsReadService:
    return AmazonAdsReadService()

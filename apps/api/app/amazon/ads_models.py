"""External-provider DTOs for the Amazon Ads API (Sponsored Products v3 +
Profiles v2 + Reporting v3). Not the ASI canonical data model — see
`app.persistence.models` for the persisted, tenant-scoped shape.

Field names/shapes below are Amazon's own documented response fields,
consulted this pass against Amazon's current Ads API guides and
corroborating sources (the docs site is a client-rendered SPA that could
not be fetched server-side in this environment — see
`docs/AI_HANDOVER/21_AMAZON_ADS_READONLY_FOUNDATION.md` for exactly what
was and was not directly confirmed). `AdsReportStatus`'s non-`PENDING`
values are a recorded assumption pending confirmation against a real
response once Ads API approval completes — see that doc.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

AdsRegion = Literal["NA", "EU", "FE"]
AdsReportStatus = Literal["PENDING", "PROCESSING", "COMPLETED", "CANCELLED", "FAILURE"]
AdsEntityState = Literal["ENABLED", "PAUSED", "ARCHIVED"]


class AdsAccountInfo(BaseModel):
    model_config = ConfigDict(extra="ignore")

    marketplace_string_id: str = Field(alias="marketplaceStringId")
    id: str
    type: str
    name: str | None = None
    valid_payment_method: bool | None = Field(default=None, alias="validPaymentMethod")


class AdsProfileResponse(BaseModel):
    """One entry from `GET /v2/profiles`. A single authorization-code
    exchange commonly returns access to several profiles (one per
    marketplace/account combination) — see `AdsProfileListResponse`."""

    model_config = ConfigDict(extra="ignore")

    profile_id: int = Field(alias="profileId")
    country_code: str = Field(alias="countryCode")
    currency_code: str = Field(alias="currencyCode")
    timezone: str
    account_info: AdsAccountInfo = Field(alias="accountInfo")
    daily_budget: Decimal | None = Field(default=None, alias="dailyBudget")


class AdsProfileListResponse(BaseModel):
    """`GET /v2/profiles` returns a bare JSON array, not an envelope —
    modeled here as a `root` list for a single, consistent parse call
    site (`AdsApiClient.list_profiles`)."""

    model_config = ConfigDict(extra="ignore")

    profiles: list[AdsProfileResponse]


class AdsCampaignBudget(BaseModel):
    """Confirmed against a real production `POST /sp/campaigns/list`
    response on 2026-09-13: campaign budget arrives as a nested object,
    not the flat `dailyBudget` field this pass previously assumed."""

    model_config = ConfigDict(extra="ignore")

    budget: Decimal
    budget_type: str = Field(alias="budgetType")


class AdsCampaignResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    campaign_id: str = Field(alias="campaignId")
    name: str
    state: AdsEntityState
    targeting_type: str | None = Field(default=None, alias="targetingType")
    budget: AdsCampaignBudget | None = None
    start_date: date | None = Field(default=None, alias="startDate")
    end_date: date | None = Field(default=None, alias="endDate")
    portfolio_id: str | None = Field(default=None, alias="portfolioId")


class AdsAdGroupResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    name: str
    state: AdsEntityState
    default_bid: Decimal | None = Field(default=None, alias="defaultBid")


class AdsProductAdResponse(BaseModel):
    """An "advertised product" — a Sponsored Products ad linking an ad
    group to a specific ASIN/SKU."""

    model_config = ConfigDict(extra="ignore")

    ad_id: str = Field(alias="adId")
    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    state: AdsEntityState
    asin: str | None = None
    sku: str | None = None


class AdsKeywordResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    keyword_id: str = Field(alias="keywordId")
    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    keyword_text: str = Field(alias="keywordText")
    match_type: str = Field(alias="matchType")
    state: AdsEntityState
    bid: Decimal | None = None


class AdsProductTargetResponse(BaseModel):
    """A product/category/audience target (non-keyword targeting)."""

    model_config = ConfigDict(extra="ignore")

    target_id: str = Field(alias="targetId")
    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    expression_type: str | None = Field(default=None, alias="expressionType")
    expression: str | None = None
    state: AdsEntityState
    bid: Decimal | None = None


class AdsReportConfigurationBody(BaseModel):
    """The nested `configuration` object Amazon requires inside a
    `POST /reporting/reports` request body — see
    `AdsReportRequestConfiguration`'s docstring for how this was
    confirmed."""

    model_config = ConfigDict(extra="ignore")

    ad_product: str = Field(alias="adProduct")
    report_type_id: str = Field(alias="reportTypeId")
    time_unit: Literal["DAILY", "SUMMARY"] = Field(alias="timeUnit")
    format: Literal["GZIP_JSON"] = "GZIP_JSON"
    group_by: list[str] = Field(alias="groupBy")
    columns: list[str]


class AdsReportRequestConfiguration(BaseModel):
    """Request body for `POST /reporting/reports`.

    CONFIRMED against a real production response on 2026-09-13: Amazon
    requires `adProduct`/`reportTypeId`/`timeUnit`/`format`/`groupBy`/
    `columns` nested under a top-level `configuration` object, with only
    `name`/`startDate`/`endDate` at the top level. The previous flat
    shape (all fields top-level, no `configuration` wrapper, no `name`)
    was rejected outright: `{"code":"400","detail":"Required fields are
    invalid or missing: configuration"}` — a real implementation bug, not
    an unconfirmed assumption."""

    model_config = ConfigDict(extra="ignore")

    name: str
    start_date: date = Field(alias="startDate")
    end_date: date = Field(alias="endDate")
    configuration: AdsReportConfigurationBody


class AdsReportStatusResponse(BaseModel):
    """`POST /reporting/reports` and `GET /reporting/reports/{reportId}`
    share this response shape. `url`/`url_expires_at` are present only
    once `status == "COMPLETED"`; never persisted (see
    `app.amazon.ads_report_service`'s module docstring)."""

    model_config = ConfigDict(extra="ignore")

    report_id: str = Field(alias="reportId")
    status: AdsReportStatus
    failure_reason: str | None = Field(default=None, alias="failureReason")
    file_size: int | None = Field(default=None, alias="fileSize")
    generated_at: datetime | None = Field(default=None, alias="generatedAt")
    url: str | None = None
    url_expires_at: datetime | None = Field(default=None, alias="urlExpiresAt")


class AdsApiErrorResponse(BaseModel):
    """Amazon's documented error envelope for a non-2xx Ads API response.
    Never logged/surfaced verbatim — `detail` may echo request parameters."""

    model_config = ConfigDict(extra="ignore")

    code: str | None = None
    detail: str | None = None
    request_id: str | None = Field(default=None, alias="requestId")


class AdsReportRow(BaseModel):
    """One normalized row from a decompressed Sponsored Products report.
    Amazon's own report columns vary by `reportTypeId`; this is the
    subset this product's read-only foundation persists (matches the
    Ads overview/performance metrics in the frontend). Unknown columns
    are ignored, not rejected, so a future report-column addition never
    breaks ingestion of the columns already handled.

    CONFIRMED against a real completed `spCampaigns` report on
    2026-09-13: Amazon's Reporting v3 API returns entity ids
    (`campaignId` observed directly; `adGroupId`/`keywordId`/`targetId`/
    `adId` follow the identical id-field convention in the same API
    family) as JSON **integers**, not strings — unlike the v3
    entity-list endpoints (e.g. `/sp/campaigns/list`), which return
    these same ids as strings (also confirmed live, see
    `AdsCampaignResponse`). Every one of 35 real report rows failed
    validation for this exact reason before the fix below
    (`campaignId: string_type — Input should be a valid string`)."""

    model_config = ConfigDict(extra="ignore")

    # Named `report_date`, not `date` — a field named identically to the
    # `date` type it's annotated with breaks Pydantic's forward-ref
    # evaluation (the class attribute shadows the type in its own
    # namespace). Amazon's own report column is still `date`; mapped via
    # the alias below.
    report_date: date | None = Field(default=None, alias="date")
    campaign_id: str | None = Field(default=None, alias="campaignId")
    ad_group_id: str | None = Field(default=None, alias="adGroupId")
    keyword_id: str | None = Field(default=None, alias="keywordId")
    target_id: str | None = Field(default=None, alias="targetId")
    ad_id: str | None = Field(default=None, alias="adId")
    impressions: int = 0
    clicks: int = 0
    cost: Decimal = Decimal("0")
    attributed_sales_14d: Decimal = Field(default=Decimal("0"), alias="sales14d")
    attributed_conversions_14d: int = Field(default=0, alias="purchases14d")
    currency: str | None = None

    @field_validator("campaign_id", "ad_group_id", "keyword_id", "target_id", "ad_id", mode="before")
    @classmethod
    def _normalize_entity_id(cls, value: object) -> object:
        """Only a safe, lossless `int` -> `str` conversion is performed
        (and `bool` is explicitly excluded, since `True`/`False` are a
        `int` subclass in Python but would never be a legitimate entity
        id) — anything else (a float, a dict, a list) is passed through
        unchanged so it still fails validation visibly rather than being
        silently coerced into a plausible-looking but wrong string."""
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        return value

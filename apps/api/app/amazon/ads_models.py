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

# Officially documented per-entity state enums — PR B1, governed by
# docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md.
#
# Deliberately NOT enforced as a Pydantic `Literal` on the response DTOs
# below: a `state` value this system does not yet recognize for a given
# entity type must still parse as a syntactically well-formed item
# (schema-valid) so the offline per-item parser in `ads_client.py` can
# classify it as a distinct "unsupported state" rejection rather than an
# opaque schema failure — see `EntityParseResult`. Each DTO's `state`
# field is therefore a plain `str`; these frozensets are the enums the
# parser checks a successfully-parsed item's `state` against.
#
# Campaign — §10.2's `SponsoredProductsCampaign` schema (from Amazon's
# own SP v3 OpenAPI spec) enumerates all seven values explicitly.
CAMPAIGN_STATES: frozenset[str] = frozenset(
    {"ENABLED", "PAUSED", "ARCHIVED", "PROPOSED", "ENABLING", "USER_DELETED", "OTHER"}
)
# Ad group (§10.3), product ad (§10.4), keyword (§11.5), product target
# (§11.6): the blueprint's extracted schemas require a `state` field on
# each but do NOT enumerate its permitted values the way the campaign
# schema does. Do not assume they share the campaign enum (explicit
# operator instruction, and the exact mistake PR #32 already found once
# for the campaign media type). Only the three values every SP v3 list
# endpoint's own `stateFilter` request parameter is documented to accept
# are treated as confirmed here.
SIBLING_ENTITY_STATES: frozenset[str] = frozenset({"ENABLED", "PAUSED", "ARCHIVED"})


def normalize_ads_entity_id(value: object) -> str:
    """Canonical string normalization for an Amazon Ads entity identifier
    — a campaign/ad-group/ad/keyword/target's own id, or a parent-id
    reference to one of those. Amazon's SP v3 list endpoints return these
    as JSON strings (confirmed live for `campaignId` — see
    `AdsCampaignResponse`); Reporting v3 separately returns some of the
    same ids as JSON integers instead (see `AdsReportRow`'s own,
    independently-scoped normalizer, left untouched by this PR — B1 does
    not touch reporting). This function accepts both wire shapes
    losslessly, and only those:

    - a non-blank string, whitespace-trimmed and otherwise preserved
      exactly (no case-folding, no internal-whitespace changes);
    - a Python `int` (arbitrary precision, so `int` -> `str` is always
      lossless).

    Never a `bool` (an `int` subclass in Python, but never a legitimate
    id), never a `float` (even an integral one — this must never cast
    through floating point, since that is exactly how precision silently
    gets lost for a large id), never any other type. Raises `ValueError`,
    which Pydantic turns into an ordinary per-field validation error the
    offline parser already treats as a schema rejection."""
    if isinstance(value, bool):
        raise ValueError("Amazon Ads entity id must not be a boolean.")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            raise ValueError("Amazon Ads entity id must not be blank.")
        return stripped
    raise ValueError(f"Amazon Ads entity id has an unsupported type: {type(value).__name__}")


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
    """`SponsoredProductsCampaign` — blueprint §10.2. Every field here is
    named on that section's official schema table; `state` is validated
    against `CAMPAIGN_STATES` by the offline parser, not by this model
    (see `normalize_ads_entity_id`'s own docstring for why)."""

    model_config = ConfigDict(extra="ignore")

    campaign_id: str = Field(alias="campaignId")
    name: str
    state: str
    targeting_type: str | None = Field(default=None, alias="targetingType")
    budget: AdsCampaignBudget | None = None
    start_date: date | None = Field(default=None, alias="startDate")
    end_date: date | None = Field(default=None, alias="endDate")
    # Opaque external reference only — blueprint §10.5 explicitly scopes
    # portfolio *listing* (names/states) to Phase B; B1 persists this id
    # as-is and never joins or resolves it.
    portfolio_id: str | None = Field(default=None, alias="portfolioId")

    @field_validator("campaign_id", "portfolio_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: object) -> object:
        if value is None:
            return value
        return normalize_ads_entity_id(value)


class AdsAdGroupResponse(BaseModel):
    """`SponsoredProductsAdGroup` — blueprint §10.3. `state`'s permitted
    values are NOT enumerated by that section (unlike campaigns) — see
    `SIBLING_ENTITY_STATES`'s own docstring."""

    model_config = ConfigDict(extra="ignore")

    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    name: str
    state: str
    default_bid: Decimal | None = Field(default=None, alias="defaultBid")

    @field_validator("ad_group_id", "campaign_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: object) -> object:
        return normalize_ads_entity_id(value)


class AdsProductAdResponse(BaseModel):
    """`SponsoredProductsProductAd` — blueprint §10.4. An "advertised
    product": a Sponsored Products ad linking an ad group to a specific
    ASIN/SKU. `asin` is vendor-only, `sku` is seller-only per that
    section — both remain optional here since which applies depends on
    the authorizing account type, not on this DTO."""

    model_config = ConfigDict(extra="ignore")

    ad_id: str = Field(alias="adId")
    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    state: str
    asin: str | None = None
    sku: str | None = None

    @field_validator("ad_id", "ad_group_id", "campaign_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: object) -> object:
        return normalize_ads_entity_id(value)


class AdsKeywordResponse(BaseModel):
    """Sponsored Products keyword — blueprint §11.5 (`POST
    /sp/keywords/list`). §11.5 documents the endpoint's path, media type,
    and request filter fields, but — unlike campaigns/ad groups/product
    ads — does NOT enumerate a response item schema. The field names
    below (`keywordText`, `matchType`, `bid`) are inferred from the
    documented request-side filter names (`keywordTextFilter`,
    `matchTypeFilter`) and this codebase's pre-existing implementation;
    treat as UNCONFIRMED until independently verified against a real
    response, exactly like the envelope key in `ads_client.py`."""

    model_config = ConfigDict(extra="ignore")

    keyword_id: str = Field(alias="keywordId")
    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    keyword_text: str = Field(alias="keywordText")
    match_type: str = Field(alias="matchType")
    state: str
    bid: Decimal | None = None

    @field_validator("keyword_id", "ad_group_id", "campaign_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: object) -> object:
        return normalize_ads_entity_id(value)


class AdsProductTargetResponse(BaseModel):
    """A product/category/automatic target (non-keyword targeting) —
    blueprint §11.6 (`POST /sp/targets/list`,
    `ListSponsoredProductsTargetingClauses`). Same UNCONFIRMED-response-
    schema caveat as `AdsKeywordResponse` above: §11.6 documents the
    request filters (`expressionTypeFilter`, `targetIdFilter`, ...) but
    not a response item schema."""

    model_config = ConfigDict(extra="ignore")

    target_id: str = Field(alias="targetId")
    ad_group_id: str = Field(alias="adGroupId")
    campaign_id: str = Field(alias="campaignId")
    expression_type: str | None = Field(default=None, alias="expressionType")
    expression: str | None = None
    state: str
    bid: Decimal | None = None

    @field_validator("target_id", "ad_group_id", "campaign_id", mode="before")
    @classmethod
    def _normalize_ids(cls, value: object) -> object:
        return normalize_ads_entity_id(value)


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

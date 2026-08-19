from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Field, PlainSerializer

Money = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]
Rate = Annotated[
    Decimal,
    PlainSerializer(lambda value: format(value, "f"), return_type=str, when_used="json"),
]


class ReportFindingSeverity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


class SearchTermPerformanceRow(BaseModel):
    date: str | None = None
    campaign_name: str | None = None
    campaign_id: str | None = None
    ad_group_name: str | None = None
    ad_group_id: str | None = None
    targeting: str | None = None
    match_type: str | None = None
    customer_search_term: str
    impressions: int
    clicks: int
    spend: Money
    sales: Money
    orders: int
    units: int | None = None
    currency: str | None = None


class BusinessPerformanceRow(BaseModel):
    date: str | None = None
    asin: str
    parent_asin: str | None = None
    sku: str | None = None
    title: str | None = None
    sessions: int
    page_views: int | None = None
    buy_box_percentage: Rate | None = None
    units_ordered: int | None = None
    ordered_product_sales: Money | None = None
    unit_session_percentage: Rate | None = None


class PpcMetrics(BaseModel):
    impressions: int
    clicks: int
    spend: Money
    sales: Money
    orders: int
    units: int | None = None
    ctr: Rate | None = None
    cpc: Money | None = None
    cvr: Rate | None = None
    acos: Rate | None = None
    roas: Rate | None = None


class SearchTermSummary(PpcMetrics):
    search_term: str
    campaign_count: int = 0


class CampaignSummary(PpcMetrics):
    campaign_name: str
    campaign_id: str | None = None


class WastedSpendRow(BaseModel):
    search_term: str
    spend: Money
    clicks: int
    orders: int
    sales: Money
    reason_code: str
    reason: str
    severity: ReportFindingSeverity


class NegativeKeywordCandidate(BaseModel):
    search_term: str
    spend: Money
    clicks: int
    orders: int
    sales: Money
    reason_code: str
    severity: ReportFindingSeverity
    message: str = "Review as negative-keyword candidate"


class ReportFinding(BaseModel):
    code: str
    severity: ReportFindingSeverity
    message: str
    entity: str | None = None


class ProductPerformanceRow(BaseModel):
    asin: str
    title: str | None = None
    sku: str | None = None
    sessions: int
    page_views: int | None = None
    units_ordered: int | None = None
    ordered_product_sales: Money | None = None
    conversion: Rate | None = None
    buy_box_percentage: Rate | None = None


class BusinessSummary(BaseModel):
    sessions: int
    page_views: int | None = None
    units_ordered: int | None = None
    ordered_product_sales: Money | None = None
    conversion: Rate | None = None
    buy_box_percentage: Rate | None = None
    asin_count: int


class SearchTermTables(BaseModel):
    wasted_spend: list[WastedSpendRow] = Field(default_factory=list)
    negative_keyword_candidates: list[NegativeKeywordCandidate] = Field(default_factory=list)
    search_terms: list[SearchTermSummary] = Field(default_factory=list)
    campaigns: list[CampaignSummary] = Field(default_factory=list)
    strong_search_terms: list[SearchTermSummary] = Field(default_factory=list)


class BusinessTables(BaseModel):
    products: list[ProductPerformanceRow] = Field(default_factory=list)


class ReportAnalysisMeta(BaseModel):
    parser_version: str
    analytics_version: str
    filename: str | None = None
    file_size_bytes: int
    source_format: str
    valid_rows: int
    invalid_rows: int
    currency: str = "INR"


class SearchTermReportAnalysis(BaseModel):
    report_type: Literal["search_term_report"] = "search_term_report"
    summary: PpcMetrics
    findings: list[ReportFinding] = Field(default_factory=list)
    tables: SearchTermTables
    warnings: list[str] = Field(default_factory=list)
    meta: ReportAnalysisMeta


class BusinessReportAnalysis(BaseModel):
    report_type: Literal["business_report"] = "business_report"
    summary: BusinessSummary
    findings: list[ReportFinding] = Field(default_factory=list)
    tables: BusinessTables
    warnings: list[str] = Field(default_factory=list)
    meta: ReportAnalysisMeta


ReportAnalysisResponse = Annotated[
    SearchTermReportAnalysis | BusinessReportAnalysis,
    Field(discriminator="report_type"),
]

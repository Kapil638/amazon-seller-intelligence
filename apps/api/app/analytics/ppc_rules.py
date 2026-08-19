"""Deterministic Sponsored Products search-term analytics (ppc-analytics-v1).

Formulas:
    CTR  = clicks / impressions
    CPC  = spend / clicks
    CVR  = orders / clicks
    ACOS = spend / sales
    ROAS = sales / spend

Zero denominators yield null metrics. They are not treated as zero.

Heuristics below are V1 defaults, not profitability evidence:
    wasted spend  — orders = 0 AND spend >= PPC_WASTED_SPEND_MIN
    high ACOS     — ACOS >= PPC_HIGH_ACOS (label: High observed ACOS)
    low conversion — clicks >= PPC_LOW_CVR_MIN_CLICKS AND CVR < PPC_LOW_CVR
    strong term   — orders > 0 AND clicks >= PPC_STRONG_MIN_CLICKS AND CVR >= PPC_STRONG_MIN_CVR
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP

from app.core.config import get_settings
from app.models.reports import (
    CampaignSummary,
    NegativeKeywordCandidate,
    PpcMetrics,
    ReportFinding,
    ReportFindingSeverity,
    SearchTermPerformanceRow,
    SearchTermSummary,
    SearchTermTables,
    WastedSpendRow,
)
from app.reports.values import RATE_QUANT

PPC_ANALYTICS_VERSION = "ppc-analytics-v1"
UNTITLED_CAMPAIGN = "(untitled campaign)"
MONEY_QUANT = Decimal("0.01")


def compute_ppc_metrics(
    impressions: int,
    clicks: int,
    spend: Decimal,
    sales: Decimal,
    orders: int,
    units: int | None = None,
) -> PpcMetrics:
    return PpcMetrics(
        impressions=impressions,
        clicks=clicks,
        spend=_money(spend),
        sales=_money(sales),
        orders=orders,
        units=units,
        ctr=_ratio(Decimal(clicks), Decimal(impressions)),
        cpc=_ratio(spend, Decimal(clicks), money=True),
        cvr=_ratio(Decimal(orders), Decimal(clicks)),
        acos=_ratio(spend, sales),
        roas=_ratio(sales, spend),
    )


def analyze_search_terms(rows: list[SearchTermPerformanceRow]) -> tuple[PpcMetrics, SearchTermTables, list[ReportFinding]]:
    settings = get_settings()
    wasted_min = Decimal(str(settings.ppc_wasted_spend_min))
    high_acos = Decimal(str(settings.ppc_high_acos))
    low_cvr = Decimal(str(settings.ppc_low_cvr))
    low_cvr_clicks = settings.ppc_low_cvr_min_clicks
    strong_clicks = settings.ppc_strong_min_clicks
    strong_cvr = Decimal(str(settings.ppc_strong_min_cvr))

    summary = _totals(rows)
    term_rows = _aggregate_search_terms(rows)
    campaign_rows = _aggregate_campaigns(rows)

    wasted: list[WastedSpendRow] = []
    negatives: list[NegativeKeywordCandidate] = []
    strong: list[SearchTermSummary] = []
    findings: list[ReportFinding] = []

    for item in term_rows:
        if item.orders == 0 and item.spend >= wasted_min:
            wasted.append(
                WastedSpendRow(
                    search_term=item.search_term,
                    spend=item.spend,
                    clicks=item.clicks,
                    orders=item.orders,
                    sales=item.sales,
                    reason_code="ZERO_ORDER_SPEND",
                    reason="Spend with zero orders",
                    severity=ReportFindingSeverity.HIGH,
                )
            )
            negatives.append(
                NegativeKeywordCandidate(
                    search_term=item.search_term,
                    spend=item.spend,
                    clicks=item.clicks,
                    orders=item.orders,
                    sales=item.sales,
                    reason_code="ZERO_ORDER_SPEND",
                    severity=ReportFindingSeverity.HIGH,
                )
            )
        elif (
            item.acos is not None
            and item.acos >= high_acos
            and item.spend >= wasted_min
        ):
            wasted.append(
                WastedSpendRow(
                    search_term=item.search_term,
                    spend=item.spend,
                    clicks=item.clicks,
                    orders=item.orders,
                    sales=item.sales,
                    reason_code="HIGH_ACOS",
                    reason="High observed ACOS",
                    severity=ReportFindingSeverity.MEDIUM,
                )
            )

        if (
            item.cvr is not None
            and item.clicks >= low_cvr_clicks
            and item.cvr < low_cvr
        ):
            findings.append(
                ReportFinding(
                    code="LOW_CONVERSION_SEARCH_TERM",
                    severity=ReportFindingSeverity.MEDIUM,
                    entity=item.search_term,
                    message=(
                        f"“{item.search_term}” has low observed conversion "
                        f"({_pct(item.cvr)}) with {item.clicks} clicks."
                    ),
                )
            )

        if (
            item.orders > 0
            and item.clicks >= strong_clicks
            and item.cvr is not None
            and item.cvr >= strong_cvr
        ):
            strong.append(item)

    wasted.sort(key=lambda row: row.spend, reverse=True)
    negatives.sort(key=lambda row: row.spend, reverse=True)
    strong.sort(key=lambda row: (row.orders, row.sales), reverse=True)
    term_rows.sort(key=lambda row: row.spend, reverse=True)
    campaign_rows.sort(key=lambda row: row.spend, reverse=True)

    if wasted:
        findings.insert(
            0,
            ReportFinding(
                code="WASTED_SPEND_PRESENT",
                severity=ReportFindingSeverity.HIGH,
                message=(
                    f"{len([row for row in wasted if row.reason_code == 'ZERO_ORDER_SPEND'])} "
                    "search terms have spend with zero orders. "
                    "Review as negative-keyword candidates; nothing was applied automatically."
                ),
            ),
        )

    tables = SearchTermTables(
        wasted_spend=wasted,
        negative_keyword_candidates=negatives,
        search_terms=term_rows,
        campaigns=campaign_rows,
        strong_search_terms=strong,
    )
    return summary, tables, findings


def _totals(rows: list[SearchTermPerformanceRow]) -> PpcMetrics:
    impressions = sum(row.impressions for row in rows)
    clicks = sum(row.clicks for row in rows)
    spend = sum((row.spend for row in rows), Decimal("0"))
    sales = sum((row.sales for row in rows), Decimal("0"))
    orders = sum(row.orders for row in rows)
    unit_values = [row.units for row in rows if row.units is not None]
    units = sum(unit_values) if unit_values else None
    return compute_ppc_metrics(impressions, clicks, spend, sales, orders, units)


def _aggregate_search_terms(rows: list[SearchTermPerformanceRow]) -> list[SearchTermSummary]:
    buckets: dict[str, list[SearchTermPerformanceRow]] = defaultdict(list)
    for row in rows:
        buckets[row.customer_search_term.casefold()].append(row)
    summaries: list[SearchTermSummary] = []
    for group in buckets.values():
        metrics = _totals(group)
        campaigns = {
            (item.campaign_id or item.campaign_name or "").casefold()
            for item in group
            if item.campaign_id or item.campaign_name
        }
        summaries.append(
            SearchTermSummary(
                search_term=group[0].customer_search_term,
                campaign_count=len(campaigns),
                **metrics.model_dump(),
            )
        )
    return summaries


def _aggregate_campaigns(rows: list[SearchTermPerformanceRow]) -> list[CampaignSummary]:
    buckets: dict[str, list[SearchTermPerformanceRow]] = defaultdict(list)
    for row in rows:
        key = (row.campaign_id or row.campaign_name or UNTITLED_CAMPAIGN).casefold()
        buckets[key].append(row)
    summaries: list[CampaignSummary] = []
    for group in buckets.values():
        metrics = _totals(group)
        name = group[0].campaign_name or UNTITLED_CAMPAIGN
        summaries.append(
            CampaignSummary(
                campaign_name=name,
                campaign_id=group[0].campaign_id,
                **metrics.model_dump(),
            )
        )
    return summaries


def _ratio(numerator: Decimal, denominator: Decimal, money: bool = False) -> Decimal | None:
    if denominator == 0:
        return None
    value = numerator / denominator
    if money:
        return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    return value.quantize(RATE_QUANT, rounding=ROUND_HALF_UP)


def _money(value: Decimal) -> Decimal:
    return value.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)


def _pct(value: Decimal) -> str:
    return f"{(value * 100).quantize(Decimal('0.1'))}%"

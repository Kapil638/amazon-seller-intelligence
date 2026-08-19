from decimal import Decimal

from app.analytics.ppc_rules import compute_ppc_metrics
from app.models.reports import SearchTermPerformanceRow
from app.services.ppc_analytics_service import PPCAnalyticsService


def _row(**overrides: object) -> SearchTermPerformanceRow:
    payload: dict[str, object] = {
        "customer_search_term": "whey protein powder",
        "impressions": 1000,
        "clicks": 50,
        "spend": Decimal("100.00"),
        "sales": Decimal("500.00"),
        "orders": 10,
        "campaign_name": "Protein Brand Exact",
    }
    payload.update(overrides)
    return SearchTermPerformanceRow.model_validate(payload)


def test_ctr_cpc_cvr_acos_roas() -> None:
    metrics = compute_ppc_metrics(
        impressions=1000,
        clicks=50,
        spend=Decimal("100"),
        sales=Decimal("500"),
        orders=10,
    )
    assert metrics.ctr == Decimal("0.050000")
    assert metrics.cpc == Decimal("2.00")
    assert metrics.cvr == Decimal("0.200000")
    assert metrics.acos == Decimal("0.200000")
    assert metrics.roas == Decimal("5.000000")


def test_zero_sales_acos_is_null() -> None:
    metrics = compute_ppc_metrics(100, 10, Decimal("50"), Decimal("0"), 0)
    assert metrics.acos is None
    assert metrics.roas == Decimal("0.000000")


def test_zero_click_cpc_and_cvr_are_null() -> None:
    metrics = compute_ppc_metrics(100, 0, Decimal("0"), Decimal("0"), 0)
    assert metrics.cpc is None
    assert metrics.cvr is None
    assert metrics.ctr == Decimal("0.000000")


def test_zero_impressions_ctr_is_null() -> None:
    metrics = compute_ppc_metrics(0, 0, Decimal("0"), Decimal("0"), 0)
    assert metrics.ctr is None


def test_aggregation_and_heuristics() -> None:
    rows = [
        _row(),
        _row(
            customer_search_term="cheap protein",
            impressions=400,
            clicks=20,
            spend=Decimal("600"),
            sales=Decimal("0"),
            orders=0,
        ),
        _row(
            customer_search_term="low converting query",
            impressions=500,
            clicks=20,
            spend=Decimal("80"),
            sales=Decimal("0"),
            orders=0,
        ),
        _row(
            customer_search_term="high acos query",
            impressions=300,
            clicks=15,
            spend=Decimal("600"),
            sales=Decimal("1000"),
            orders=4,
            campaign_name="Other Campaign",
        ),
        _row(
            customer_search_term="whey protein powder",
            impressions=200,
            clicks=10,
            spend=Decimal("20"),
            sales=Decimal("100"),
            orders=2,
            campaign_name="Second Campaign",
        ),
    ]
    summary, tables, findings = PPCAnalyticsService().analyze(rows)
    assert summary.spend == Decimal("1400.00")
    assert summary.orders == 16

    whey = next(item for item in tables.search_terms if item.search_term == "whey protein powder")
    assert whey.impressions == 1200
    assert whey.clicks == 60
    assert whey.campaign_count == 2

    campaigns = {item.campaign_name for item in tables.campaigns}
    assert "Protein Brand Exact" in campaigns
    assert "Other Campaign" in campaigns

    wasted_terms = {item.search_term: item for item in tables.wasted_spend}
    assert wasted_terms["cheap protein"].reason_code == "ZERO_ORDER_SPEND"
    assert wasted_terms["cheap protein"].severity == "high"
    assert wasted_terms["high acos query"].reason_code == "HIGH_ACOS"
    assert wasted_terms["high acos query"].reason == "High observed ACOS"
    assert "unprofitable" not in wasted_terms["high acos query"].reason.casefold()

    negative_terms = {item.search_term for item in tables.negative_keyword_candidates}
    assert "cheap protein" in negative_terms
    assert "high acos query" not in negative_terms
    assert all("Review as negative-keyword candidate" in item.message for item in tables.negative_keyword_candidates)

    low_conversion_entities = {item.entity for item in findings if item.code == "LOW_CONVERSION_SEARCH_TERM"}
    assert "low converting query" in low_conversion_entities
    assert "cheap protein" in low_conversion_entities

    strong_terms = {item.search_term for item in tables.strong_search_terms}
    assert "whey protein powder" in strong_terms
    assert "cheap protein" not in strong_terms
    assert "winning" not in " ".join(item.search_term for item in tables.strong_search_terms).casefold()

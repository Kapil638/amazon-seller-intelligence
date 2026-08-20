from app.analytics.listing_rules import SCORE_VERSION, analyze_listing
from app.models.product import (
    APlusContent,
    BSR,
    CategoryNode,
    FeaturedReview,
    ProductSpecification,
    RatingBand,
    RatingBreakdown,
)
from app.parsers.rainforest_product_mapper import map_rainforest_product
from app.services.listing_analysis_service import ListingAnalysisService
from tests.test_listing_analysis import make_product
from tests.test_rainforest import load_fixture

MAKE_PRODUCT_V1 = {
    "overall": 97,
    "title": 100,
    "bullets": 100,
    "description": 95,
    "images": 88,
    "completeness": 100,
    "social_proof": 95,
    "findings": ["COMPLETENESS_SUMMARY", "STRONG_SOCIAL_PROOF", "TITLE_LENGTH_OK"],
}

PRODUCT_JSON_V1 = {
    "overall": 78,
    "title": 84,
    "bullets": 88,
    "description": 40,
    "images": 65,
    "completeness": 100,
    "social_proof": 86,
    "findings": [
        "DESCRIPTION_SHORT",
        "FEW_IMAGES",
        "TITLE_REPEATED_WORDS",
        "COMPLETENESS_SUMMARY",
        "TITLE_LENGTH_OK",
    ],
}


def _section_scores(analysis) -> dict[str, int]:
    return {
        "title": analysis.sections.title.score,
        "bullets": analysis.sections.bullets.score,
        "description": analysis.sections.description.score,
        "images": analysis.sections.images.score,
        "completeness": analysis.sections.completeness.score,
        "social_proof": analysis.sections.social_proof.score,
    }


def test_v1_score_version_unchanged() -> None:
    assert SCORE_VERSION == "v1"


def test_make_product_v1_scores_are_unchanged() -> None:
    analysis = ListingAnalysisService().analyze(make_product())
    assert analysis.overall_score == MAKE_PRODUCT_V1["overall"]
    assert _section_scores(analysis) == {
        key: MAKE_PRODUCT_V1[key]
        for key in ("title", "bullets", "description", "images", "completeness", "social_proof")
    }
    assert [item.code for item in analysis.findings] == MAKE_PRODUCT_V1["findings"]


def test_observed_rainforest_fixture_v1_scores_are_unchanged() -> None:
    product = map_rainforest_product(load_fixture("product.json"), "B07J4TNYV8", "amazon.in")
    analysis = analyze_listing(product)
    assert analysis.score_version == "v1"
    assert analysis.overall_score == PRODUCT_JSON_V1["overall"]
    assert _section_scores(analysis) == {
        key: PRODUCT_JSON_V1[key]
        for key in ("title", "bullets", "description", "images", "completeness", "social_proof")
    }
    assert [item.code for item in analysis.findings] == PRODUCT_JSON_V1["findings"]


def test_new_foundation_fields_do_not_change_v1_scores() -> None:
    base = make_product()
    enriched = make_product(
        bsr_ranks=[BSR(rank=10, category="Leaf"), BSR(rank=1000, category="Root")],
        category_path=[CategoryNode(name="Health", category_id="1")],
        is_sold_by_amazon=True,
        availability_type="in_stock",
        videos_count=4,
        a_plus=APlusContent(has_a_plus_content=True, has_brand_story=True),
        specifications=[ProductSpecification(name="Color", value="Black")],
        rating_breakdown=RatingBreakdown(five_star=RatingBand(percentage=80, count=100)),
        featured_reviews=[FeaturedReview(title="Great", body="Worked well", rating=5)],
        recent_sales_text="50+ bought in past month",
    )
    first = analyze_listing(base)
    second = analyze_listing(enriched)
    assert first.overall_score == second.overall_score
    assert first.sections.model_dump() == second.sections.model_dump()
    assert first.findings == second.findings
    assert first.recommendations == second.recommendations

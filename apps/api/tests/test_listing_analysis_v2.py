from fastapi.testclient import TestClient

from app.analytics.listing_rules import SCORE_VERSION as V1_VERSION
from app.analytics.listing_rules import analyze_listing
from app.analytics.listing_rules_v2 import SCORE_VERSION as V2_VERSION
from app.analytics.listing_rules_v2 import analyze_listing_v2
from app.models.listing_analysis import FindingSeverity
from app.models.listing_analysis_v2 import EvidenceState
from app.models.product import (
    APlusContent,
    APlusImage,
    BrandStory,
    BSR,
    CategoryNode,
    Image,
    Price,
    ProductSpecification,
    ProductVideo,
    RatingBand,
    RatingBreakdown,
    Seller,
)
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from tests.test_listing_analysis import make_product


def _codes(analysis) -> set[str]:
    return {item.code for item in analysis.findings}


def _coverage_field(analysis, group: str, name: str):
    return next(item for item in getattr(analysis.data_coverage, group).fields if item.name == name)


def test_v2_score_version() -> None:
    analysis = analyze_listing_v2(make_product())
    assert analysis.score_version == "v2"
    assert V2_VERSION == "v2"
    assert V1_VERSION == "v1"


def test_strong_listing_has_solid_quality_score() -> None:
    analysis = ListingAnalysisV2Service().analyze(make_product())
    assert analysis.listing_quality_score >= 70
    assert analysis.sections.title.score >= 70
    assert analysis.sections.bullets.score >= 70
    assert "NO_BULLETS" not in _codes(analysis)
    assert "MAIN_IMAGE_MISSING" not in _codes(analysis)


def test_empty_listing_is_weak() -> None:
    analysis = analyze_listing_v2(
        make_product(
            title="",
            bullet_points=[],
            description=None,
            images=[],
            rating=None,
            review_count=None,
            bsr=None,
            seller=None,
        )
    )
    assert analysis.listing_quality_score < 50
    assert analysis.status.value == "poor"
    assert "TITLE_MISSING" in _codes(analysis)
    assert "NO_BULLETS" in _codes(analysis)
    assert "MAIN_IMAGE_MISSING" in _codes(analysis)


def test_low_reviews_do_not_reduce_listing_quality() -> None:
    strong = analyze_listing_v2(make_product())
    weak_social = analyze_listing_v2(make_product(rating=2.0, review_count=3))
    assert weak_social.listing_quality_score == strong.listing_quality_score
    assert weak_social.market_signals.rating == 2.0
    assert weak_social.market_signals.review_count == 3
    assert strong.sections.title.score == weak_social.sections.title.score


def test_huge_review_count_does_not_rescue_weak_listing() -> None:
    analysis = analyze_listing_v2(
        make_product(
            title="X",
            bullet_points=["Tiny"],
            description="Short",
            images=[Image(url="https://example.test/1.jpg")],
            rating=4.9,
            review_count=50000,
        )
    )
    assert analysis.listing_quality_score < 70
    assert analysis.market_signals.review_count == 50000
    assert analysis.listing_quality_score != analyze_listing(
        make_product(title="X", bullet_points=["Tiny"], description="Short", images=[Image(url="https://example.test/1.jpg")], rating=4.9, review_count=50000)
    ).overall_score


def test_amazon_sold_without_seller_is_not_missing_seller_evidence() -> None:
    analysis = analyze_listing_v2(
        make_product(seller=None, is_sold_by_amazon=True)
    )
    seller_field = _coverage_field(analysis, "market_signals", "seller")
    assert seller_field.evidence_state == EvidenceState.OBSERVED
    assert seller_field.available is True
    assert "AMAZON_SOLD_NO_THIRD_PARTY_SELLER" in _codes(analysis)
    with_seller = analyze_listing_v2(make_product(is_sold_by_amazon=False, seller=Seller(name="Third Party")))
    assert analysis.listing_quality_score == with_seller.listing_quality_score


def test_multiple_bsr_rows_are_market_signals_only() -> None:
    ranks = [
        BSR(rank=32614, category="Electronics"),
        BSR(rank=161, category="Sports & Action Video Cameras"),
    ]
    analysis = analyze_listing_v2(make_product(bsr=ranks[0], bsr_ranks=ranks))
    assert [item.rank for item in analysis.market_signals.bsr_ranks] == [32614, 161]
    assert analysis.listing_quality_score == analyze_listing_v2(make_product()).listing_quality_score


def test_a_plus_confirmed_present() -> None:
    analysis = analyze_listing_v2(
        make_product(
            a_plus=APlusContent(
                has_a_plus_content=True,
                has_brand_story=True,
                body_text="From the manufacturer: sealed housing and a two year warranty for outdoor use.",
                images=[APlusImage(url="https://example.test/aplus.jpg", alt="Housing")],
                brand_story=BrandStory(hero_image="https://example.test/hero.jpg", description="Built for trips."),
            )
        )
    )
    assert "A_PLUS_PRESENT" in _codes(analysis)
    assert "A_PLUS_TEXT_AVAILABLE" in _codes(analysis)
    assert "BRAND_STORY_PRESENT" in _codes(analysis)
    assert analysis.data_coverage.enhanced_content.fields[0].evidence_state == EvidenceState.OBSERVED


def test_a_plus_confirmed_absent() -> None:
    analysis = analyze_listing_v2(make_product(a_plus=APlusContent(has_a_plus_content=False, has_brand_story=False)))
    assert "A_PLUS_NOT_PRESENT" in _codes(analysis)
    assert _coverage_field(analysis, "enhanced_content", "a_plus").evidence_state == EvidenceState.REPORTED_ABSENT


def test_a_plus_unknown_is_not_treated_as_absent() -> None:
    analysis = analyze_listing_v2(make_product(a_plus=None))
    assert "A_PLUS_UNKNOWN" in _codes(analysis)
    assert "A_PLUS_NOT_PRESENT" not in _codes(analysis)
    assert _coverage_field(analysis, "enhanced_content", "a_plus").evidence_state == EvidenceState.UNKNOWN
    assert _coverage_field(analysis, "enhanced_content", "a_plus").available is False


def test_videos_present() -> None:
    analysis = analyze_listing_v2(
        make_product(videos=[ProductVideo(title="Overview", video_url="https://example.test/clip.mp4")])
    )
    assert "VIDEO_PRESENT" in _codes(analysis)
    assert "VIDEO_REPORTED_DETAILS_UNAVAILABLE" not in _codes(analysis)
    assert _coverage_field(analysis, "media", "video").evidence_state == EvidenceState.OBSERVED


def test_videos_count_without_objects_is_not_no_video() -> None:
    analysis = analyze_listing_v2(make_product(videos=[], videos_count=3))
    assert "VIDEO_REPORTED_DETAILS_UNAVAILABLE" in _codes(analysis)
    assert "VIDEO_PRESENT" not in _codes(analysis)
    assert _coverage_field(analysis, "media", "video").available is True


def test_specifications_missing_from_bullets() -> None:
    analysis = analyze_listing_v2(
        make_product(
            specifications=[
                ProductSpecification(name="Color Name", value="Obsidian"),
                ProductSpecification(name="Material", value="Titanium"),
            ]
        )
    )
    assert "SPECIFICATION_COVERAGE_GAP" in _codes(analysis)
    assert "STRUCTURE_SPEC_GAP" in _codes(analysis)


def test_specifications_represented_in_bullets() -> None:
    analysis = analyze_listing_v2(
        make_product(
            bullet_points=[
                "Obsidian color housing with a matte finish for daily carry",
                "Titanium frame designed for outdoor use and long trips",
                "Includes 60 vegetarian softgels in a moisture-resistant bottle",
                "Third-party tested for purity with no artificial colors added",
                "Easy-to-swallow size with a mild citrus coating for daily use",
            ],
            specifications=[
                ProductSpecification(name="Color Name", value="Obsidian"),
                ProductSpecification(name="Material", value="Titanium"),
            ],
        )
    )
    assert "SPECIFICATION_COVERAGE_GAP" not in _codes(analysis)


def test_duplicate_bullets() -> None:
    bullets = [
        "Supports daily immune and bone health with 600 IU vitamin D3 per serving",
        "Supports daily immune and bone health with 600 IU vitamin D3 per serving",
        "Includes 60 vegetarian softgels in a moisture-resistant bottle",
    ]
    analysis = analyze_listing_v2(make_product(bullet_points=bullets))
    assert "BULLET_DUPLICATION" in _codes(analysis)


def test_keyword_stuffed_title() -> None:
    analysis = analyze_listing_v2(
        make_product(title="Whey Protein Whey Protein Whey Protein Powder Whey Protein Shake")
    )
    assert "TITLE_POSSIBLE_STUFFING" in _codes(analysis) or "TITLE_REPETITION" in _codes(analysis)


def test_limited_gallery() -> None:
    analysis = analyze_listing_v2(make_product(images=[Image(url="https://example.test/1.jpg", is_main=True)]))
    assert "LIMITED_GALLERY" in _codes(analysis)
    assert analysis.sections.media_coverage.score < 70


def test_duplicate_media_urls() -> None:
    analysis = analyze_listing_v2(
        make_product(
            images=[
                Image(url="https://example.test/1.jpg", is_main=True),
                Image(url="https://example.test/1.jpg"),
                Image(url="https://example.test/2.jpg"),
            ]
        )
    )
    assert "DUPLICATE_MEDIA" in _codes(analysis)


def test_sparse_payload_does_not_invent_a_plus_or_video() -> None:
    analysis = analyze_listing_v2(
        make_product(
            category=None,
            category_path=[],
            bsr=None,
            bsr_ranks=[],
            a_plus=None,
            videos=[],
            videos_count=None,
            specifications=[],
            rating=None,
            review_count=None,
        )
    )
    assert _coverage_field(analysis, "enhanced_content", "a_plus").evidence_state == EvidenceState.UNKNOWN
    assert _coverage_field(analysis, "media", "video").evidence_state == EvidenceState.UNKNOWN
    assert analysis.market_signals.bsr_ranks == []


def test_v1_results_unchanged_for_same_product() -> None:
    product = make_product()
    v1 = analyze_listing(product)
    assert v1.score_version == "v1"
    assert v1.overall_score == 97
    v2 = analyze_listing_v2(product)
    assert v2.score_version == "v2"
    assert "social_proof" not in v2.sections.model_dump()


def test_v2_endpoint_and_v1_endpoint_are_separate(client: TestClient) -> None:
    product = make_product()
    payload = {"product": product.model_dump(mode="json"), "source": "mock"}
    v1 = client.post("/api/v1/analysis/listing", json=payload)
    v2 = client.post("/api/v1/analysis/listing/v2", json=payload)
    assert v1.status_code == 200
    assert v2.status_code == 200
    assert v1.json()["meta"]["score_version"] == "v1"
    assert v2.json()["meta"]["score_version"] == "v2"
    assert v2.json()["analysis"]["listing_quality_score"] == v2.json()["analysis"]["listing_quality_score"]
    assert "market_signals" in v2.json()["analysis"]
    assert "data_coverage" in v2.json()["analysis"]
    assert v1.json()["analysis"]["overall_score"] == 97


def test_recommendations_are_traceable() -> None:
    analysis = analyze_listing_v2(make_product(bullet_points=[]))
    rec = next(item for item in analysis.recommendations if item.code == "NO_BULLETS")
    assert rec.finding_code == "NO_BULLETS"
    assert rec.priority.value == "high"
    assert "bullet" in rec.action.lower()


def test_thin_description_with_substantial_a_plus_is_not_heavily_punished() -> None:
    thin = analyze_listing_v2(make_product(description="Too short.", a_plus=None))
    with_a_plus = analyze_listing_v2(
        make_product(
            description="Too short.",
            a_plus=APlusContent(
                has_a_plus_content=True,
                body_text="From the manufacturer: this module explains materials, warranty, and setup in several paragraphs of observed copy.",
                images=[APlusImage(url="https://example.test/a.jpg")],
            ),
        )
    )
    assert with_a_plus.sections.description_a_plus.score > thin.sections.description_a_plus.score
    assert with_a_plus.sections.description_a_plus.score >= 70


def test_category_path_preserved_as_context_not_score() -> None:
    with_path = analyze_listing_v2(
        make_product(category_path=[CategoryNode(name="Electronics", category_id="1")])
    )
    without = analyze_listing_v2(make_product(category_path=[]))
    assert with_path.listing_quality_score == without.listing_quality_score


def test_rating_breakdown_is_market_signal_only() -> None:
    analysis = analyze_listing_v2(
        make_product(rating_breakdown=RatingBreakdown(five_star=RatingBand(percentage=80, count=100)))
    )
    assert analysis.market_signals.rating_breakdown is not None
    assert analysis.market_signals.rating_breakdown.five_star.count == 100
    assert analysis.listing_quality_score == analyze_listing_v2(make_product()).listing_quality_score


def test_price_and_availability_are_not_in_quality_sections() -> None:
    analysis = analyze_listing_v2(
        make_product(price=Price(amount=12, currency="INR"), availability="Out of Stock", availability_type="out_of_stock")
    )
    blob = str(analysis.sections.model_dump())
    assert "Out of Stock" not in blob
    assert analysis.market_signals.availability == "Out of Stock"
    assert analysis.market_signals.price is not None

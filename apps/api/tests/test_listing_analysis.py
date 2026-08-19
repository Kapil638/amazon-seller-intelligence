from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.analytics.listing_rules import SCORE_VERSION, analyze_listing
from app.models.product import BSR, Image, Price, Product, Seller
from app.services.listing_analysis_service import ListingAnalysisService

FIXED_FETCHED_AT = datetime(2026, 8, 19, 6, 0, tzinfo=UTC)

STRONG_TITLE = (
    "AuroraGlow Vitamin D3 Softgels Daily Immune and Bone Health Support, "
    "Vegetarian 60 Count Bottle"
)

STRONG_BULLETS = [
    "Supports daily immune and bone health with 600 IU vitamin D3 per serving",
    "Designed for adults who want a simple, consistent supplement routine",
    "Includes 60 vegetarian softgels in a moisture-resistant bottle",
    "Third-party tested for purity with no artificial colors added",
    "Easy-to-swallow size with a mild citrus coating for daily use",
]

STRONG_DESCRIPTION = (
    "AuroraGlow Vitamin D3 is a daily wellness supplement designed for adults "
    "who want a simple, consistent source of vitamin D. Each bottle contains "
    "60 vegetarian softgels intended for everyday use. This fictional listing "
    "is used only to test deterministic listing analysis. The copy is long "
    "enough to sit inside the preferred description range used by score version v1."
)


def make_product(**overrides: object) -> Product:
    data: dict[str, object] = {
        "asin": "B0TESTAN01",
        "marketplace": "amazon.in",
        "title": STRONG_TITLE,
        "brand": "Lumora Wellness",
        "price": Price(amount=449, currency="INR"),
        "rating": 4.6,
        "review_count": 2100,
        "bullet_points": list(STRONG_BULLETS),
        "description": STRONG_DESCRIPTION,
        "images": [
            Image(url=f"https://placehold.co/800x800?text=Image+{index}")
            for index in range(1, 6)
        ],
        "category": "Health & Personal Care",
        "bsr": BSR(rank=1842, category="Health & Personal Care"),
        "availability": "In Stock",
        "seller": Seller(name="Lumora Retail India"),
        "variations": [],
        "last_fetched_at": FIXED_FETCHED_AT,
    }
    data.update(overrides)
    return Product.model_validate(data)


def finding_codes(analysis) -> set[str]:
    return {item.code for item in analysis.findings}


def test_strong_complete_listing_score_range() -> None:
    analysis = ListingAnalysisService().analyze(make_product())
    assert 75 <= analysis.overall_score <= 100
    assert analysis.score_version == SCORE_VERSION
    assert analysis.sections.title.score >= 70
    assert analysis.sections.bullets.score >= 70
    assert "NO_BULLETS" not in finding_codes(analysis)
    assert "NO_DESCRIPTION" not in finding_codes(analysis)
    assert "NO_IMAGES" not in finding_codes(analysis)


def test_missing_title_is_flagged() -> None:
    analysis = analyze_listing(make_product(title=""))
    assert analysis.sections.title.score == 0
    assert "TITLE_MISSING" in finding_codes(analysis)
    assert any(item.code == "TITLE_MISSING" for item in analysis.recommendations)


def test_no_bullets() -> None:
    analysis = analyze_listing(make_product(bullet_points=[]))
    assert analysis.sections.bullets.score == 0
    assert "NO_BULLETS" in finding_codes(analysis)
    assert any("Add product bullet points." in item.message for item in analysis.recommendations)


def test_extremely_long_bullet() -> None:
    long_bullet = "Supports daily wellness " * 40
    bullets = ["Short but valid bullet point about the product formula.", long_bullet]
    analysis = analyze_listing(make_product(bullet_points=bullets))
    assert "LONG_BULLET" in finding_codes(analysis)
    assert analysis.sections.bullets.metrics["long_bullet_indexes"] == [2]


def test_missing_description() -> None:
    analysis = analyze_listing(make_product(description=None))
    assert analysis.sections.description.score == 0
    assert "NO_DESCRIPTION" in finding_codes(analysis)


def test_no_images() -> None:
    analysis = analyze_listing(make_product(images=[]))
    assert analysis.sections.images.score == 0
    assert "NO_IMAGES" in finding_codes(analysis)


def test_low_rating_and_reviews() -> None:
    analysis = analyze_listing(make_product(rating=2.1, review_count=4))
    assert analysis.sections.social_proof.score < 60
    codes = finding_codes(analysis)
    assert "LOW_RATING" in codes or "FEW_REVIEWS" in codes


def test_complete_listing_produces_expected_score_range() -> None:
    analysis = analyze_listing(make_product())
    assert analysis.sections.completeness.score == 100
    assert 80 <= analysis.overall_score <= 100


def test_same_input_produces_same_score() -> None:
    product = make_product()
    first = analyze_listing(product)
    second = analyze_listing(product)
    assert first.overall_score == second.overall_score
    assert first.sections.model_dump() == second.sections.model_dump()
    assert first.findings == second.findings


def test_scores_remain_between_0_and_100() -> None:
    products = [
        make_product(),
        make_product(title="", bullet_points=[], description=None, images=[]),
        make_product(title="X" * 400, rating=1.0, review_count=0),
    ]
    for product in products:
        analysis = analyze_listing(product)
        assert 0 <= analysis.overall_score <= 100
        for section in analysis.sections.model_dump().values():
            assert 0 <= section["score"] <= 100
            assert section["max_score"] == 100


def test_analysis_endpoint_returns_envelope(client: TestClient) -> None:
    product = make_product()
    response = client.post(
        "/api/v1/analysis/listing",
        json={"product": product.model_dump(mode="json"), "source": "mock"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["meta"]["engine"] == "deterministic"
    assert body["meta"]["score_version"] == SCORE_VERSION
    assert body["meta"]["source"] == "mock"
    assert body["product"]["asin"] == product.asin
    assert 0 <= body["analysis"]["overall_score"] <= 100
    assert "title" in body["analysis"]["sections"]

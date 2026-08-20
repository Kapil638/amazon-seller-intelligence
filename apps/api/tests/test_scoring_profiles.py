from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient

from app.analytics.listing_rules_v2 import analyze_listing_v2
from app.analytics.scoring_profiles import (
    STANDARD_PROFILE_ID,
    STANDARD_V2_WEIGHTS,
    SectionScores,
    calculate_weighted_listing_score,
    section_scores_from_analysis,
    weights_total,
)
from app.models.product import Image
from app.models.scoring_profile import ScoringWeights
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import Organization, ScoringProfile
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from app.usage.ledger import get_usage_ledger
from tests.test_listing_analysis import make_product

MEDIA_FIRST = ScoringWeights(
    title=15,
    bullets=20,
    description_a_plus=15,
    media=40,
    content_structure=10,
)

EXAMPLE_SCORES = SectionScores(
    title=80,
    bullets=70,
    description_a_plus=60,
    media=90,
    content_structure=75,
)


def _product():
    return make_product(images=[Image(url="https://placehold.co/800?text=Main", is_main=True)])


def _weights_payload(weights: ScoringWeights) -> dict:
    return weights.model_dump()


def _create_profile(client: TestClient, name: str = "Media First", weights: ScoringWeights | None = None, **extra):
    payload = {
        "name": name,
        "description": extra.pop("description", None),
        "weights": _weights_payload(weights or MEDIA_FIRST),
        **extra,
    }
    response = client.post("/api/v1/scoring-profiles", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_standard_weights_total_100() -> None:
    assert weights_total(STANDARD_V2_WEIGHTS) == Decimal("100")


def test_standard_profile_cannot_be_edited(client: TestClient) -> None:
    response = client.patch(
        f"/api/v1/scoring-profiles/{STANDARD_PROFILE_ID}",
        json={"name": "Hacked"},
    )
    assert response.status_code == 403


def test_standard_profile_cannot_be_deleted(client: TestClient) -> None:
    response = client.delete(f"/api/v1/scoring-profiles/{STANDARD_PROFILE_ID}")
    assert response.status_code == 403


def test_custom_valid_profile_creation(client: TestClient) -> None:
    profile = _create_profile(client)
    assert profile["name"] == "Media First"
    assert profile["editable"] is True
    listed = client.get("/api/v1/scoring-profiles")
    names = [item["name"] for item in listed.json()["items"]]
    assert names[0] == "Standard V2"
    assert "Media First" in names


def test_invalid_total_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scoring-profiles",
        json={
            "name": "Bad total",
            "weights": {
                "title": 40,
                "bullets": 40,
                "description_a_plus": 40,
                "media": 0,
                "content_structure": 0,
            },
        },
    )
    assert response.status_code == 400
    assert "100" in response.json()["detail"]


def test_negative_weight_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scoring-profiles",
        json={
            "name": "Negative",
            "weights": {
                "title": -5,
                "bullets": 25,
                "description_a_plus": 20,
                "media": 40,
                "content_structure": 20,
            },
        },
    )
    assert response.status_code == 400


def test_weight_over_100_rejected(client: TestClient) -> None:
    response = client.post(
        "/api/v1/scoring-profiles",
        json={
            "name": "Too large",
            "weights": {
                "title": 101,
                "bullets": 0,
                "description_a_plus": 0,
                "media": 0,
                "content_structure": -1,
            },
        },
    )
    assert response.status_code == 400


def test_zero_weight_allowed(client: TestClient) -> None:
    profile = _create_profile(
        client,
        name="No media",
        weights=ScoringWeights(title=20, bullets=25, description_a_plus=20, media=0, content_structure=35),
    )
    assert profile["weights"]["media"] == 0


def test_duplicate_profile_name_rejected(client: TestClient) -> None:
    _create_profile(client, name="Media First")
    response = client.post(
        "/api/v1/scoring-profiles",
        json={"name": "media first", "weights": _weights_payload(MEDIA_FIRST)},
    )
    assert response.status_code == 409


def test_organization_scoping(client: TestClient) -> None:
    other_org = uuid4()
    hidden_id = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(
            ScoringProfile(
                id=hidden_id,
                organization_id=other_org,
                name="Hidden Other Org",
                title_weight=20,
                bullets_weight=25,
                description_a_plus_weight=20,
                media_weight=20,
                content_structure_weight=15,
            )
        )
    listed = client.get("/api/v1/scoring-profiles")
    names = [item["name"] for item in listed.json()["items"]]
    assert "Hidden Other Org" not in names
    assert client.get(f"/api/v1/scoring-profiles/{hidden_id}").status_code == 404


def test_set_default_custom_profile_and_only_one_default(client: TestClient) -> None:
    first = _create_profile(client, name="Content First", is_default=True)
    second = _create_profile(client, name="Media First", is_default=True)
    listed = {item["name"]: item for item in client.get("/api/v1/scoring-profiles").json()["items"]}
    assert listed["Media First"]["is_default"] is True
    assert listed["Content First"]["is_default"] is False
    assert first["id"] != second["id"]
    patched = client.patch(f"/api/v1/scoring-profiles/{first['id']}", json={"is_default": True})
    assert patched.status_code == 200
    listed = {item["id"]: item for item in client.get("/api/v1/scoring-profiles").json()["items"]}
    assert listed[first["id"]]["is_default"] is True
    assert listed[second["id"]]["is_default"] is False


def test_update_profile(client: TestClient) -> None:
    profile = _create_profile(client)
    updated = client.patch(
        f"/api/v1/scoring-profiles/{profile['id']}",
        json={"name": "Media Heavy", "weights": _weights_payload(MEDIA_FIRST)},
    )
    assert updated.status_code == 200
    assert updated.json()["name"] == "Media Heavy"


def test_archive_profile_unavailable_for_new_selection(client: TestClient) -> None:
    profile = _create_profile(client)
    archived = client.delete(f"/api/v1/scoring-profiles/{profile['id']}")
    assert archived.status_code == 200
    assert archived.json()["is_archived"] is True
    names = [item["name"] for item in client.get("/api/v1/scoring-profiles").json()["items"]]
    assert "Media First" not in names
    product = _product()
    analysis = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": product.model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    assert analysis.status_code == 400


def test_historical_report_survives_profile_archive(client: TestClient) -> None:
    profile = _create_profile(client)
    product = _product()
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": product.model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    report_id = created.json()["meta"]["report_id"]
    snapshot = created.json()["custom_score"]
    client.delete(f"/api/v1/scoring-profiles/{profile['id']}")
    detail = client.get(f"/api/v1/reports/{report_id}")
    assert detail.status_code == 200
    assert detail.json()["custom_score"]["custom_listing_quality_score"] == snapshot["custom_listing_quality_score"]
    assert detail.json()["custom_score"]["profile"]["weights"] == snapshot["profile"]["weights"]


def test_standard_and_section_scores_unchanged_with_custom_profile(client: TestClient) -> None:
    product = _product()
    baseline = ListingAnalysisV2Service().analyze(product)
    profile = _create_profile(client)
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": product.model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    body = created.json()
    assert body["analysis"]["listing_quality_score"] == baseline.listing_quality_score
    assert body["analysis"]["sections"]["title"]["score"] == baseline.sections.title.score
    assert body["analysis"]["sections"]["bullets"]["score"] == baseline.sections.bullets.score
    assert body["analysis"]["sections"]["media_coverage"]["score"] == baseline.sections.media_coverage.score
    assert body["custom_score"] is not None
    assert body["meta"]["score_version"] == "v2"


def test_custom_score_calculation_example() -> None:
    standard = calculate_weighted_listing_score(EXAMPLE_SCORES, STANDARD_V2_WEIGHTS)
    custom = calculate_weighted_listing_score(EXAMPLE_SCORES, MEDIA_FIRST)
    assert standard == 75
    assert custom == 78
    assert EXAMPLE_SCORES.title == 80
    assert EXAMPLE_SCORES.media == 90
    assert standard != custom


def test_custom_score_matches_profile_weights_and_engine_standard() -> None:
    analysis = analyze_listing_v2(_product())
    scores = section_scores_from_analysis(analysis)
    assert calculate_weighted_listing_score(scores, STANDARD_V2_WEIGHTS) == analysis.listing_quality_score
    custom = calculate_weighted_listing_score(scores, MEDIA_FIRST)
    expected = calculate_weighted_listing_score(
        scores,
        ScoringWeights(title=15, bullets=20, description_a_plus=15, media=40, content_structure=10),
    )
    assert custom == expected


def test_standard_and_custom_returned_together(client: TestClient) -> None:
    profile = _create_profile(client, is_default=True)
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={"product": _product().model_dump(mode="json"), "source": "mock"},
    )
    body = created.json()
    assert body["analysis"]["listing_quality_score"] is not None
    assert body["custom_score"]["profile"]["profile_id"] == profile["id"]
    explicit_standard = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": _product().model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": STANDARD_PROFILE_ID,
        },
    )
    assert explicit_standard.json()["custom_score"] is None


def test_editing_profile_does_not_mutate_old_report_snapshot(client: TestClient) -> None:
    profile = _create_profile(client)
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": _product().model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    report_id = created.json()["meta"]["report_id"]
    original = created.json()["custom_score"]
    client.patch(
        f"/api/v1/scoring-profiles/{profile['id']}",
        json={
            "weights": {
                "title": 50,
                "bullets": 20,
                "description_a_plus": 10,
                "media": 10,
                "content_structure": 10,
            }
        },
    )
    detail = client.get(f"/api/v1/reports/{report_id}")
    assert detail.json()["custom_score"]["profile"]["weights"]["media"] == 40
    assert (
        detail.json()["custom_score"]["custom_listing_quality_score"]
        == original["custom_listing_quality_score"]
    )
    assert detail.json()["analysis"]["listing_quality_score"] == created.json()["analysis"]["listing_quality_score"]


def test_report_detail_returns_profile_snapshot(client: TestClient) -> None:
    profile = _create_profile(client, name="Media First")
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": _product().model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    report_id = created.json()["meta"]["report_id"]
    detail = client.get(f"/api/v1/reports/{report_id}")
    custom = detail.json()["custom_score"]
    assert custom["profile"]["profile_name"] == "Media First"
    assert custom["profile"]["type"] == "custom"
    listed = client.get("/api/v1/reports")
    item = next(row for row in listed.json()["items"] if row["report_id"] == report_id)
    assert item["scoring_profile_name"] == "Media First"
    assert item["custom_listing_quality_score"] == custom["custom_listing_quality_score"]


def test_custom_scoring_and_reweight_perform_zero_provider_calls(client: TestClient) -> None:
    get_usage_ledger().reset()
    profile = _create_profile(client)
    product = _product()
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": product.model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    reweight = client.post(
        "/api/v1/analysis/listing/v2/reweight",
        json={
            "scoring_profile_id": profile["id"],
            "report_id": created.json()["meta"]["report_id"],
            "persist": False,
        },
    )
    assert reweight.status_code == 200
    assert reweight.json()["preview"] is True
    assert get_usage_ledger().rainforest_product_calls == 0
    assert get_usage_ledger().openai_requests == 0


def test_reweight_preview_does_not_mutate_historical_report(client: TestClient) -> None:
    profile = _create_profile(client, name="Media First")
    other = _create_profile(
        client,
        name="Content First",
        weights=ScoringWeights(title=30, bullets=30, description_a_plus=20, media=10, content_structure=10),
    )
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": _product().model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    report_id = created.json()["meta"]["report_id"]
    original = created.json()["custom_score"]["custom_listing_quality_score"]
    preview = client.post(
        "/api/v1/analysis/listing/v2/reweight",
        json={"scoring_profile_id": other["id"], "report_id": report_id, "persist": False},
    )
    assert preview.json()["custom_score"]["profile"]["profile_name"] == "Content First"
    detail = client.get(f"/api/v1/reports/{report_id}")
    assert detail.json()["custom_score"]["custom_listing_quality_score"] == original
    assert detail.json()["custom_score"]["profile"]["profile_name"] == "Media First"


def test_market_signals_and_data_coverage_unaffected(client: TestClient) -> None:
    product = _product()
    baseline = client.post(
        "/api/v1/analysis/listing/v2",
        json={"product": product.model_dump(mode="json"), "source": "mock", "scoring_profile_id": STANDARD_PROFILE_ID},
    )
    profile = _create_profile(client)
    custom = client.post(
        "/api/v1/analysis/listing/v2",
        json={
            "product": product.model_dump(mode="json"),
            "source": "mock",
            "scoring_profile_id": profile["id"],
        },
    )
    assert custom.json()["analysis"]["market_signals"] == baseline.json()["analysis"]["market_signals"]
    assert custom.json()["analysis"]["data_coverage"] == baseline.json()["analysis"]["data_coverage"]
    assert "rating" not in custom.json()["custom_score"]["profile"]["weights"]


def test_standard_profile_is_listed_and_readable(client: TestClient) -> None:
    listed = client.get("/api/v1/scoring-profiles")
    standard = listed.json()["items"][0]
    assert standard["id"] == STANDARD_PROFILE_ID
    assert standard["editable"] is False
    assert standard["deletable"] is False
    fetched = client.get(f"/api/v1/scoring-profiles/{STANDARD_PROFILE_ID}")
    assert fetched.status_code == 200
    assert fetched.json()["weights"]["title"] == 20
    assert fetched.json()["weights"]["bullets"] == 25

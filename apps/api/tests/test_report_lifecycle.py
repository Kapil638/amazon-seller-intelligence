from __future__ import annotations

from datetime import UTC, datetime
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import func, select

from app.analytics.listing_rules_v2 import SCORE_VERSION as SCORE_VERSION_V2
from app.models.product import Image, ProductSource
from app.models.scoring_profile import CustomScoreResult, ScoringProfileSnapshot, ScoringWeights
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import (
    AnalysisRun,
    GeneratedReport,
    ListingAnalysisResult,
    Organization,
    ProductSnapshot,
)
from app.persistence.repositories import AnalysisRunRepository
from app.reports.client_analysis_report import REPORT_TEMPLATE_VERSION
from app.reports.pdf_report_renderer import PdfReportRenderer
from app.services.analysis_history_service import AnalysisHistoryService
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from app.usage.ledger import get_usage_ledger
from tests.test_ai_image_intelligence import sample_image_intelligence
from tests.test_ai_listing_intelligence_v2 import sample_intelligence_v2
from tests.test_listing_analysis import make_product


@pytest.fixture(autouse=True)
def _no_cover_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.client_report_service.load_cover_image_bytes",
        lambda url: None,
    )


def _persist_report(
    *,
    product=None,
    custom: CustomScoreResult | None = None,
    with_ai: bool = False,
    with_image: bool = False,
):
    product = product or make_product(
        images=[Image(url="https://placehold.co/800?text=Main", is_main=True)]
    )
    analysis = ListingAnalysisV2Service().analyze(product)
    persist = AnalysisHistoryService().record_listing_v2(
        product, analysis, ProductSource.MOCK.value, custom_score=custom
    )
    assert persist.persisted is True
    assert persist.report_id is not None
    if with_ai:
        AnalysisHistoryService().record_ai_v2(
            product,
            analysis,
            sample_intelligence_v2(),
            report_id=persist.report_id,
            source="mock",
            provider="openai",
            model="gpt-5.4",
            prompt_version="listing-intelligence-v2",
            usage=None,
            latency_ms=12,
            estimated_cost_usd=None,
        )
    if with_image:
        AnalysisHistoryService().record_image_intelligence(
            product,
            analysis,
            sample_image_intelligence(),
            report_id=persist.report_id,
            source="mock",
            provider="openai",
            model="gpt-5.4",
            prompt_version="image-intelligence-v1",
            images_available=1,
            images_selected=1,
            images_skipped=0,
            usage=None,
            latency_ms=9,
            estimated_cost_usd=None,
        )
    return product, analysis, persist


def _custom_score() -> CustomScoreResult:
    return CustomScoreResult(
        custom_listing_quality_score=84,
        profile=ScoringProfileSnapshot(
            profile_id=str(uuid4()),
            profile_name="SEO Emphasis",
            type="custom",
            weights=ScoringWeights(
                title=30,
                bullets=25,
                description_a_plus=20,
                media=15,
                content_structure=10,
            ),
        ),
    )


def _pdf_text(payload: bytes) -> str:
    reader = PdfReader(BytesIO(payload))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _provider_counts() -> tuple[int, int]:
    ledger = get_usage_ledger()
    return ledger.rainforest_product_calls, ledger.openai_requests


def _create_other_org_report(product, analysis):
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        snapshot = ProductSnapshot(
            organization_id=other_org,
            asin=product.asin,
            marketplace=product.marketplace,
            source="mock",
            normalized_product=product.model_dump(mode="json"),
            content_hash="other-org",
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        run = AnalysisRun(
            organization_id=other_org,
            product_snapshot_id=snapshot.id,
            asin=product.asin,
            marketplace=product.marketplace,
            status="complete",
            listing_score_version=SCORE_VERSION_V2,
        )
        session.add(run)
        session.flush()
        session.add(
            ListingAnalysisResult(
                analysis_run_id=run.id,
                score_version=SCORE_VERSION_V2,
                listing_quality_score=11,
                payload=analysis.model_dump(mode="json"),
            )
        )
        return run.id


def test_soft_delete_hides_report_and_keeps_rows(client: TestClient) -> None:
    product, analysis, persist = _persist_report()
    report_id = str(persist.report_id)
    rain, openai = _provider_counts()

    deleted = client.delete(f"/api/v1/reports/{report_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"report_id": report_id, "deleted": True}

    listed = client.get("/api/v1/reports", params={"asin": product.asin})
    assert listed.status_code == 200
    ids = {item["report_id"] for item in listed.json()["items"]}
    assert report_id not in ids

    detail = client.get(f"/api/v1/reports/{report_id}")
    assert detail.status_code == 404

    pdf = client.post(f"/api/v1/reports/{report_id}/pdf")
    assert pdf.status_code == 404
    download = client.get(f"/api/v1/reports/{report_id}/pdf")
    assert download.status_code == 404

    with session_scope() as session:
        run = AnalysisRunRepository(session).get(
            current_organization_id(), persist.report_id, include_deleted=True
        )
        assert run is not None
        assert run.deleted_at is not None
        assert run.listing_result is not None
        assert run.listing_result.payload["listing_quality_score"] == analysis.listing_quality_score
        snapshot = session.get(ProductSnapshot, run.product_snapshot_id)
        assert snapshot is not None

    later_rain, later_openai = _provider_counts()
    assert later_rain == rain
    assert later_openai == openai


def test_cross_org_delete_and_pdf_return_404(client: TestClient) -> None:
    product, analysis, persist = _persist_report()
    other_id = _create_other_org_report(product, analysis)
    rain, openai = _provider_counts()

    assert client.delete(f"/api/v1/reports/{other_id}").status_code == 404
    assert client.post(f"/api/v1/reports/{other_id}/pdf").status_code == 404
    assert client.get(f"/api/v1/reports/{other_id}/pdf").status_code == 404
    assert client.get(f"/api/v1/reports/{persist.report_id}").status_code == 200

    with session_scope() as session:
        other = session.get(AnalysisRun, other_id)
        assert other is not None
        assert other.deleted_at is None

    later_rain, later_openai = _provider_counts()
    assert later_rain == rain
    assert later_openai == openai


def test_unknown_report_is_404(client: TestClient) -> None:
    missing = uuid4()
    assert client.delete(f"/api/v1/reports/{missing}").status_code == 404
    assert client.post(f"/api/v1/reports/{missing}/pdf").status_code == 404
    assert client.get(f"/api/v1/reports/{missing}/pdf").status_code == 404


def test_deterministic_pdf_generate_download_and_reuse(client: TestClient) -> None:
    product, analysis, persist = _persist_report()
    report_id = persist.report_id
    rain, openai = _provider_counts()
    calls = {"n": 0}
    original = PdfReportRenderer.render

    def wrapped(self, report):
        calls["n"] += 1
        return original(self, report)

    PdfReportRenderer.render = wrapped  # type: ignore[method-assign]
    try:
        first = client.post(f"/api/v1/reports/{report_id}/pdf")
        assert first.status_code == 200, first.text
        body = first.json()
        assert body["generated"] is True
        assert body["reused"] is False
        assert body["template_version"] == REPORT_TEMPLATE_VERSION
        assert body["filename"].startswith("Amazon-Listing-Analysis-")
        assert body["filename"].endswith(".pdf")

        download = client.get(f"/api/v1/reports/{report_id}/pdf")
        assert download.status_code == 200
        assert download.headers["content-type"].startswith("application/pdf")
        payload = download.content
        assert payload.startswith(b"%PDF")
        text = _pdf_text(payload)
        assert "Listing Performance" in text
        assert product.asin in text
        assert "Executive Overview" in text
        assert "Listing Quality" in text
        assert str(analysis.listing_quality_score) in text
        assert "Title Optimization" in text or "TITLE" in text
        assert "Market Signals" in text
        assert "Data Coverage" in text
        assert "What We Found" in text or "Findings" in text
        assert "Report Information" in text or "Report ID" in text
        assert "Disclaimer" in text or "DISCLAIMER" in text
        assert "analysis-report-v2" in text
        assert "Custom Scoring Profile" not in text
        assert "AI Content" not in text
        assert "Image & Media Intelligence" not in text

        second = client.post(f"/api/v1/reports/{report_id}/pdf")
        assert second.status_code == 200
        assert second.json()["reused"] is True
        assert calls["n"] == 1
    finally:
        PdfReportRenderer.render = original  # type: ignore[method-assign]

    with session_scope() as session:
        count = session.scalar(
            select(func.count()).select_from(GeneratedReport).where(
                GeneratedReport.analysis_run_id == report_id
            )
        )
        assert count == 1

    later_rain, later_openai = _provider_counts()
    assert later_rain == rain
    assert later_openai == openai


def test_custom_scoring_pdf_uses_snapshot(client: TestClient) -> None:
    custom = _custom_score()
    _product, _analysis, persist = _persist_report(custom=custom)
    rain, openai = _provider_counts()
    generated = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert generated.status_code == 200
    payload = client.get(f"/api/v1/reports/{persist.report_id}/pdf").content
    text = _pdf_text(payload)
    assert "Custom Scoring Profile" in text
    assert "SEO Emphasis" in text
    assert "84 / 100" in text
    assert "Custom weights change score aggregation only" in text
    later_rain, later_openai = _provider_counts()
    assert later_rain == rain
    assert later_openai == openai


def test_ai_strategy_pdf_includes_suggested_copy(client: TestClient) -> None:
    _product, _analysis, persist = _persist_report(with_ai=True)
    generated = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert generated.status_code == 200
    text = _pdf_text(client.get(f"/api/v1/reports/{persist.report_id}/pdf").content)
    assert "AI Content" in text
    assert "Suggested Listing Copy" in text
    assert "Suggested Title" in text
    assert "Priority Actions" in text
    assert get_usage_ledger().openai_requests == 0


def test_image_intelligence_pdf_has_no_numeric_image_score(client: TestClient) -> None:
    _product, _analysis, persist = _persist_report(with_ai=True, with_image=True)
    generated = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert generated.status_code == 200
    text = _pdf_text(client.get(f"/api/v1/reports/{persist.report_id}/pdf").content)
    assert "Image & Media Intelligence" in text
    assert "Recommended Image Plan" in text
    assert "numeric image-quality score" in text.lower() or "Qualitative visual intelligence" in text
    assert "Image Quality Score" not in text


def test_sparse_and_long_content_pdfs(client: TestClient) -> None:
    sparse = make_product(
        title="Minimal fictional listing title for scoring",
        brand=None,
        bullet_points=[],
        description=None,
        images=[],
        price=None,
        rating=None,
        review_count=None,
        bsr=None,
        seller=None,
        availability=None,
    )
    _product, _analysis, persist = _persist_report(product=sparse)
    generated = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert generated.status_code == 200
    text = _pdf_text(client.get(f"/api/v1/reports/{persist.report_id}/pdf").content)
    assert "Not available" in text

    long_product = make_product(
        asin="B0LONGTITL",
        title=("Very long fictional product title for wrap testing. " * 40).strip(),
        description=("Long description paragraph for pagination. " * 200).strip(),
    )
    long_ai = sample_intelligence_v2(
        executive_assessment=("Long persisted executive assessment. " * 300).strip(),
        rewrite_suggestions={
            "suggested_title": ("Suggested long title phrase " * 40).strip(),
            "suggested_bullets": [("Long suggested bullet " * 40).strip() for _ in range(5)],
            "optional_description_excerpt": ("Long description excerpt " * 80).strip(),
        },
    )
    analysis = ListingAnalysisV2Service().analyze(long_product)
    saved = AnalysisHistoryService().record_listing_v2(
        long_product, analysis, ProductSource.MOCK.value
    )
    AnalysisHistoryService().record_ai_v2(
        long_product,
        analysis,
        long_ai,
        report_id=saved.report_id,
        source="mock",
        provider="openai",
        model="gpt-5.4",
        prompt_version="listing-intelligence-v2",
        usage=None,
        latency_ms=1,
        estimated_cost_usd=None,
    )
    rain, openai = _provider_counts()
    long_pdf = client.post(f"/api/v1/reports/{saved.report_id}/pdf")
    assert long_pdf.status_code == 200, long_pdf.text
    payload = client.get(f"/api/v1/reports/{saved.report_id}/pdf").content
    assert payload.startswith(b"%PDF")
    later_rain, later_openai = _provider_counts()
    assert later_rain == rain
    assert later_openai == openai


def test_storage_failure_does_not_create_metadata_row(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _product, _analysis, persist = _persist_report()

    class BoomStore:
        def put(self, bucket: str, path: str, data: bytes, content_type: str) -> None:
            raise RuntimeError("storage unavailable")

        def get(self, bucket: str, path: str) -> bytes | None:
            return None

    monkeypatch.setattr(
        "app.services.artifact_persistence_service.get_file_store",
        lambda: BoomStore(),
    )
    response = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert response.status_code == 503
    with session_scope() as session:
        count = session.scalar(
            select(func.count()).select_from(GeneratedReport).where(
                GeneratedReport.analysis_run_id == persist.report_id
            )
        )
        assert count == 0


def test_cover_image_failure_still_builds_pdf(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(url: str | None) -> bytes | None:
        raise RuntimeError("image fetch failed")

    monkeypatch.setattr("app.services.client_report_service.load_cover_image_bytes", boom)
    _product, _analysis, persist = _persist_report()
    response = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert response.status_code == 200
    assert client.get(f"/api/v1/reports/{persist.report_id}/pdf").content.startswith(b"%PDF")


def test_get_pdf_before_generate_is_404(client: TestClient) -> None:
    _product, _analysis, persist = _persist_report()
    assert client.get(f"/api/v1/reports/{persist.report_id}/pdf").status_code == 404

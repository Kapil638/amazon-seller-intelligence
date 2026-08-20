from __future__ import annotations

from io import BytesIO

from fastapi.testclient import TestClient
from pypdf import PdfReader
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.product import Image, Price, ProductSource
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import GeneratedReport
from app.persistence.repositories import GeneratedReportRepository
from app.persistence.storage import get_file_store
from app.reports.client_analysis_report import LEGACY_TEMPLATE_VERSION, REPORT_TEMPLATE_VERSION
from app.reports.formatting import display_title, format_price, normalize_text
from app.reports.labels import friendly_label
from app.usage.ledger import get_usage_ledger
from tests.test_listing_analysis import make_product
from tests.test_report_lifecycle import _custom_score, _pdf_text, _persist_report, _provider_counts


def test_v2_template_and_legacy_artifact_are_distinct(client: TestClient) -> None:
    _product, _analysis, persist = _persist_report()
    report_id = persist.report_id
    settings = get_settings()
    dummy = b"%PDF-1.4 v1-placeholder\n%%EOF\n"
    path = f"{current_organization_id()}/analysis-pdf/{report_id}/{LEGACY_TEMPLATE_VERSION}/legacy.pdf"
    get_file_store().put(settings.storage_generated_bucket, path, dummy, "application/pdf")
    with session_scope() as session:
        GeneratedReportRepository(session).create(
            organization_id=current_organization_id(),
            analysis_run_id=report_id,
            report_type="analysis_pdf",
            storage_bucket=settings.storage_generated_bucket,
            storage_path=path,
            filename="legacy.pdf",
            template_version=LEGACY_TEMPLATE_VERSION,
        )
    rain, openai = _provider_counts()
    generated = client.post(f"/api/v1/reports/{report_id}/pdf")
    assert generated.status_code == 200
    body = generated.json()
    assert body["template_version"] == REPORT_TEMPLATE_VERSION == "analysis-report-v2"
    assert body["reused"] is False
    payload = client.get(f"/api/v1/reports/{report_id}/pdf").content
    assert payload.startswith(b"%PDF")
    assert payload != dummy
    text = _pdf_text(payload)
    assert "analysis-report-v2" in text
    assert "analysis-report-v1" not in text
    with session_scope() as session:
        versions = set(
            session.scalars(
                select(GeneratedReport.template_version).where(GeneratedReport.analysis_run_id == report_id)
            )
        )
        assert LEGACY_TEMPLATE_VERSION in versions
        assert REPORT_TEMPLATE_VERSION in versions
        assert session.scalar(select(func.count()).select_from(GeneratedReport).where(
            GeneratedReport.analysis_run_id == report_id
        )) == 2
    later_rain, later_openai = _provider_counts()
    assert later_rain == rain == 0 or later_rain == rain
    assert later_openai == openai
    assert get_usage_ledger().rainforest_product_calls == later_rain
    assert get_usage_ledger().openai_requests == later_openai


def test_currency_and_label_helpers() -> None:
    assert format_price(Price(amount=2599.0, currency="INR"), "amazon.in") in {"₹2,599", "Rs.2,599"}
    assert "2,599" in format_price(Price(amount=2599.0, currency="INR"), "amazon.in")
    assert friendly_label("description_a_plus") == "Description & A+ Content"
    assert friendly_label("content_structure") == "Content Structure & Readability"
    assert friendly_label("media_coverage") == "Media Coverage"
    assert friendly_label("a_plus") == "A+ Content"
    assert friendly_label("bsr_ranks") == "Best Seller Rank"
    assert friendly_label("review_count") == "Review Count"
    assert "Wi-Fi" in normalize_text("Wi\u2011Fi Range Extender")
    product = make_product(
        brand="TP-Link",
        title="TP-Link AC1200 WiFi Range Extender RE305 Dual Band",
    )
    brand, short = display_title(product)
    assert brand == "TP-Link"
    assert "TP-Link" not in short
    assert "AC1200" in short


def test_v2_pdf_has_no_snake_case_or_repeated_ones(client: TestClient) -> None:
    _product, analysis, persist = _persist_report(with_ai=True)
    payload = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
    assert payload.status_code == 200
    text = _pdf_text(client.get(f"/api/v1/reports/{persist.report_id}/pdf").content)
    assert "description_a_plus" not in text
    assert "content_structure:" not in text
    assert "media_coverage:" not in text
    assert "bsr_ranks" not in text
    assert "What to Fix First" in text
    assert "Executive Overview" in text
    assert "Recommended Action Plan" in text
    assert "Suggested Listing Copy" in text
    assert "01" in text and "02" in text
    ones = [line.strip() for line in text.splitlines() if line.strip() == "1"]
    assert len(ones) < 8
    reader = PdfReader(BytesIO(client.get(f"/api/v1/reports/{persist.report_id}/pdf").content))
    assert len(reader.pages) >= 6
    assert str(analysis.listing_quality_score) in text


def test_v2_custom_scoring_and_image_reports(client: TestClient) -> None:
    _product, _analysis, persist = _persist_report(custom=_custom_score(), with_ai=True, with_image=True)
    assert client.post(f"/api/v1/reports/{persist.report_id}/pdf").status_code == 200
    text = _pdf_text(client.get(f"/api/v1/reports/{persist.report_id}/pdf").content)
    assert "Custom Scoring Profile" in text
    assert "SEO Emphasis" in text
    assert "Image & Media Intelligence" in text
    assert "numeric image-quality score" in text.lower() or "Qualitative visual intelligence" in text
    assert get_usage_ledger().openai_requests == 0
    assert get_usage_ledger().rainforest_product_calls == 0


def test_invalid_and_missing_cover_image_still_renders(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("app.services.client_report_service.load_cover_image_bytes", lambda url: None)
    product = make_product(images=[Image(url="https://evil.example/not-allowed.jpg", is_main=True)])
    _p, _a, persist = _persist_report(product=product)
    assert client.post(f"/api/v1/reports/{persist.report_id}/pdf").status_code == 200
    assert client.get(f"/api/v1/reports/{persist.report_id}/pdf").content.startswith(b"%PDF")

    def boom(url):
        raise RuntimeError("blocked")

    monkeypatch.setattr("app.services.client_report_service.load_cover_image_bytes", boom)
    _p2, _a2, persist2 = _persist_report()
    assert client.post(f"/api/v1/reports/{persist2.report_id}/pdf").status_code == 200


def test_v2_artifact_reuse(client: TestClient) -> None:
    _product, _analysis, persist = _persist_report()
    first = client.post(f"/api/v1/reports/{persist.report_id}/pdf").json()
    second = client.post(f"/api/v1/reports/{persist.report_id}/pdf").json()
    assert first["reused"] is False
    assert second["reused"] is True
    assert second["template_version"] == "analysis-report-v2"


def test_visual_qa_fixture_suite(client: TestClient, tmp_path) -> None:
    cases = {
        "deterministic": _persist_report(),
        "ai": _persist_report(with_ai=True),
        "ai_image": _persist_report(with_ai=True, with_image=True),
        "sparse": _persist_report(
            product=make_product(
                title="Sparse fictional listing title",
                brand=None,
                bullet_points=[],
                description=None,
                images=[],
                price=None,
                rating=None,
                review_count=None,
                bsr=None,
                seller=None,
            )
        ),
        "long": _persist_report(
            product=make_product(
                asin="B0QAVERYLONG",
                title=("Very long fictional product title for wrap testing including Wi\u2011Fi. " * 30).strip(),
                description=("Long description paragraph. " * 180).strip(),
            ),
            with_ai=True,
        ),
    }
    for name, (_product, _analysis, persist) in cases.items():
        response = client.post(f"/api/v1/reports/{persist.report_id}/pdf")
        assert response.status_code == 200, name
        payload = client.get(f"/api/v1/reports/{persist.report_id}/pdf").content
        assert payload.startswith(b"%PDF"), name
        reader = PdfReader(BytesIO(payload))
        assert len(reader.pages) >= 5, name
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "Page 1 of" in text or "Historical Analysis" in text
        (tmp_path / f"{name}.pdf").write_bytes(payload)
    assert get_usage_ledger().openai_requests == 0
    assert get_usage_ledger().rainforest_product_calls == 0

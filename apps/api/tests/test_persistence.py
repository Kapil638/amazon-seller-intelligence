from datetime import UTC, datetime
from uuid import uuid4

from fastapi.testclient import TestClient

from app.analytics.listing_rules_v2 import SCORE_VERSION as SCORE_VERSION_V2
from app.core.config import get_settings
from app.models.product import Image, ProductSource
from app.persistence.database import current_organization_id, session_scope
from app.persistence.hashing import sha256_bytes
from app.persistence.models import Organization, ProductSnapshot
from app.persistence.repositories import (
    ProductSnapshotRepository,
    ReportUploadRepository,
    UsageEventRepository,
)
from app.services.analysis_history_service import AnalysisHistoryService
from app.services.artifact_persistence_service import ArtifactPersistenceService
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from app.usage.ledger import get_usage_ledger
from tests.test_ai_image_intelligence import sample_image_intelligence
from tests.test_listing_analysis import make_product


def _persist_listing(product=None):
    product = product or make_product(images=[Image(url="https://placehold.co/800?text=Main", is_main=True)])
    analysis = ListingAnalysisV2Service().analyze(product)
    persist = AnalysisHistoryService().record_listing_v2(product, analysis, ProductSource.MOCK.value)
    assert persist.persisted is True
    assert persist.report_id is not None
    return product, analysis, persist


def test_product_snapshot_is_immutable_for_same_asin() -> None:
    product = make_product(title="First snapshot title that is long enough for scoring")
    first = AnalysisHistoryService().record_listing_v2(
        product, ListingAnalysisV2Service().analyze(product), "mock"
    )
    later = product.model_copy(update={"title": "Second snapshot title that is long enough for scoring"})
    second = AnalysisHistoryService().record_listing_v2(
        later, ListingAnalysisV2Service().analyze(later), "mock"
    )
    assert first.report_id != second.report_id
    with session_scope() as session:
        rows = ProductSnapshotRepository(session).list_for_asin(current_organization_id(), product.asin)
        assert len(rows) == 2
        titles = [row.normalized_product["title"] for row in rows]
        assert titles[0].startswith("First")
        assert titles[1].startswith("Second")


def test_organization_scoping_hides_other_tenant_reports() -> None:
    product, analysis, persist = _persist_listing()
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        snapshot = ProductSnapshot(
            organization_id=other_org,
            asin=product.asin,
            marketplace=product.marketplace,
            source="mock",
            normalized_product=product.model_dump(mode="json"),
            content_hash="abc",
            fetched_at=datetime.now(UTC),
        )
        session.add(snapshot)
        session.flush()
        from app.persistence.models import AnalysisRun, ListingAnalysisResult

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
        other_run_id = run.id
        session.add(
            ListingAnalysisResult(
                analysis_run_id=run.id,
                score_version=SCORE_VERSION_V2,
                listing_quality_score=11,
                payload=analysis.model_dump(mode="json"),
            )
        )
    listed = AnalysisHistoryService().list_reports(asin=product.asin)
    ids = {str(item.report_id) for item in listed.items}
    assert str(persist.report_id) in ids
    assert str(other_run_id) not in ids


def test_partial_report_keeps_deterministic_result() -> None:
    product, analysis, persist = _persist_listing()
    AnalysisHistoryService().record_ai_v2_failure(persist.report_id, "OpenAI unavailable")
    detail = AnalysisHistoryService().get_report(persist.report_id)
    assert detail.analysis.listing_quality_score == analysis.listing_quality_score
    assert detail.ai_intelligence is None
    assert detail.meta.status == "partial"


def test_ai_and_image_results_round_trip() -> None:
    from tests.test_ai_listing_intelligence_v2 import sample_intelligence_v2

    product, analysis, persist = _persist_listing()
    ai = sample_intelligence_v2()
    image = sample_image_intelligence()
    AnalysisHistoryService().record_ai_v2(
        product,
        analysis,
        ai,
        report_id=persist.report_id,
        source="mock",
        provider="openai",
        model="gpt-5.4",
        prompt_version="listing-intelligence-v2",
        usage=None,
        latency_ms=12,
        estimated_cost_usd=None,
    )
    AnalysisHistoryService().record_image_intelligence(
        product,
        analysis,
        image,
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
    other = AnalysisHistoryService()
    detail = other.get_report(persist.report_id)
    assert detail.product.title == product.title
    assert detail.analysis.listing_quality_score == analysis.listing_quality_score
    assert detail.ai_intelligence.executive_assessment == ai.executive_assessment
    assert detail.image_intelligence.executive_assessment == image.executive_assessment


def test_duplicate_upload_hash_is_detected() -> None:
    data = b"asin,title\nB0TEST0001,Demo\n"
    service = ArtifactPersistenceService()
    first = service.save_seller_report_upload(
        filename="one.csv",
        data=data,
        report_type="search_term_report",
        parser_version="v1",
        row_count=1,
        analysis_payload={"ok": True},
    )
    second = service.save_seller_report_upload(
        filename="two.csv",
        data=data,
        report_type="search_term_report",
        parser_version="v1",
        row_count=1,
        analysis_payload={"ok": True},
    )
    assert first["persisted"] is True
    assert second["duplicate"] is True
    assert second["file_hash"] == sha256_bytes(data)
    with session_scope() as session:
        found = ReportUploadRepository(session).find_by_hash(current_organization_id(), sha256_bytes(data))
        assert found is not None


def test_usage_event_persistence() -> None:
    ArtifactPersistenceService().record_usage_event(
        provider="openai",
        workflow="listing_intelligence_v2",
        event_type="call",
        model="gpt-5.4",
        input_tokens=10,
        output_tokens=4,
        total_tokens=14,
        estimated_cost_usd=None,
        cache_hit=False,
    )
    with session_scope() as session:
        rows = UsageEventRepository(session).list_for_org(current_organization_id())
        assert rows
        assert rows[0].provider == "openai"


def test_endpoint_saves_and_reopens_without_provider_calls(client: TestClient) -> None:
    product = make_product(images=[Image(url="https://placehold.co/800?text=Main", is_main=True)])
    analysis = ListingAnalysisV2Service().analyze(product)
    created = client.post(
        "/api/v1/analysis/listing/v2",
        json={"product": product.model_dump(mode="json"), "source": "mock"},
    )
    assert created.status_code == 200
    report_id = created.json()["meta"]["report_id"]
    assert created.json()["meta"]["persisted"] is True
    assert created.json()["analysis"]["listing_quality_score"] == analysis.listing_quality_score

    from tests.test_ai_listing_intelligence_v2 import FakeAIProvider, _result
    from app.api.routes.analysis import get_ai_listing_intelligence_v2_service, get_ai_image_intelligence_service
    from app.main import app
    from app.providers.memory_cache import MemoryTtlValueCache
    from app.services.ai_listing_intelligence_v2_service import AIListingIntelligenceV2Service
    from app.services.ai_image_intelligence_service import AIImageIntelligenceService
    from tests.test_ai_image_intelligence import FakeVisionProvider, _vision_result

    listing_provider = FakeAIProvider(result=_result())
    vision_provider = FakeVisionProvider(result=_vision_result())
    app.dependency_overrides[get_ai_listing_intelligence_v2_service] = lambda: AIListingIntelligenceV2Service(
        provider=listing_provider, cache=MemoryTtlValueCache(60)
    )
    app.dependency_overrides[get_ai_image_intelligence_service] = lambda: AIImageIntelligenceService(
        provider=vision_provider, cache=MemoryTtlValueCache(60)
    )
    try:
        ai = client.post(
            "/api/v1/analysis/listing/v2/ai",
            json={
                "product": product.model_dump(mode="json"),
                "analysis": created.json()["analysis"],
                "source": "mock",
                "report_id": report_id,
            },
        )
        images = client.post(
            "/api/v1/analysis/listing/v2/images/ai",
            json={
                "product": product.model_dump(mode="json"),
                "analysis": created.json()["analysis"],
                "source": "mock",
                "report_id": report_id,
            },
        )
    finally:
        app.dependency_overrides.clear()
    assert ai.status_code == 200
    assert images.status_code == 200

    get_usage_ledger().reset()
    listed = client.get("/api/v1/reports", params={"asin": product.asin})
    assert listed.status_code == 200
    assert listed.json()["total"] >= 1
    detail = client.get(f"/api/v1/reports/{report_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["product"]["title"] == product.title
    assert body["analysis"]["listing_quality_score"] == analysis.listing_quality_score
    assert body["ai_intelligence"]["executive_assessment"]
    assert body["image_intelligence"]["executive_assessment"]
    assert body["meta"]["historical"] is True
    assert get_usage_ledger().rainforest_product_calls == 0
    assert get_usage_ledger().openai_requests == 0


def test_unknown_report_returns_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/reports/{uuid4()}")
    assert response.status_code == 404


def test_list_pagination(client: TestClient) -> None:
    for index in range(3):
        product = make_product(asin=f"B0PAGE{index:04d}")
        client.post(
            "/api/v1/analysis/listing/v2",
            json={"product": product.model_dump(mode="json"), "source": "mock"},
        )
    page = client.get("/api/v1/reports", params={"limit": 2, "offset": 0})
    assert page.status_code == 200
    assert len(page.json()["items"]) == 2
    assert page.json()["total"] >= 3


def test_invalid_report_id_is_rejected(client: TestClient) -> None:
    response = client.get("/api/v1/reports/not-a-uuid")
    assert response.status_code in {400, 422}


def test_bulk_job_and_generated_excel_are_persisted() -> None:
    from app.models.bulk import (
        BulkIngestStats,
        BulkJobOptions,
        BulkJobProgress,
        BulkJobResponse,
        BulkUsageStats,
    )
    from app.persistence.repositories import BulkRepository, GeneratedReportRepository

    job = BulkJobResponse(
        job_id="jobpersist01",
        status="completed",
        options=BulkJobOptions(),
        ingest=BulkIngestStats(filename="asins.csv", unique_asins=1, valid_rows=1, input_rows=1),
        progress=BulkJobProgress(total=1, processed=1, successful=1),
        usage=BulkUsageStats(product_provider="mock"),
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    service = ArtifactPersistenceService()
    service.save_bulk_job(job, b"asin\nB0TEST0001\n", "asins.csv")
    generated_id = service.save_generated_excel(job, b"fake-xlsx-bytes")
    assert generated_id is not None
    loaded = service.load_generated_excel(job.job_id)
    assert loaded is not None
    payload, filename = loaded
    assert payload == b"fake-xlsx-bytes"
    assert filename.endswith(".xlsx")
    with session_scope() as session:
        stored = BulkRepository(session).get_by_external_id(current_organization_id(), job.job_id)
        assert stored is not None
        assert stored.total_items == 1
        generated = GeneratedReportRepository(session).get_for_bulk_job(
            current_organization_id(), stored.id
        )
        assert generated is not None


def test_default_organization_id_is_stable() -> None:
    assert str(get_settings().default_organization_id) == "11111111-1111-4111-8111-111111111111"

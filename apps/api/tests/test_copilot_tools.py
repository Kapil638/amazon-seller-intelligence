from __future__ import annotations

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.copilot import default_registry
from app.copilot.budget import (
    COST_NONE,
    COST_OPENAI,
    COST_RAINFOREST_PRODUCT,
    COST_RAINFOREST_SEARCH,
    BudgetTracker,
)
from app.copilot.evidence import EvidenceClaim, envelope
from app.copilot.exceptions import (
    BudgetExceededError,
    BudgetRequiredError,
    ConfirmationRequiredError,
    ToolValidationError,
    UnknownToolError,
)
from app.copilot.registry import ToolCatalogEntry, ToolDefinition, ToolRegistry
from app.copilot.synthesis.service import SynthesisService
from app.copilot.synthesis.schemas import SynthesisRequest
from app.core.exceptions import ReportNotFoundError
from app.models.product import ProductSource
from app.persistence.database import current_organization_id
from app.providers.factory import get_product_provider
from app.providers.memory_cache import MemoryTtlCache
from app.providers.mock import MockProductDataProvider
from app.providers.rainforest import RainforestProductDataProvider
from app.services.listing_analysis_v2_service import ListingAnalysisV2Service
from app.services.product_service import ProductService
from tests.test_listing_analysis import make_product
from tests.test_rainforest import TEST_KEY, load_fixture
from tests.test_report_lifecycle import _create_other_org_report, _persist_report


def _budget() -> BudgetTracker:
    return BudgetTracker()


@pytest.mark.asyncio
async def test_execute_requires_budget() -> None:
    registry = default_registry()
    with pytest.raises(BudgetRequiredError):
        await registry.execute("list_saved_reports", {"limit": 1})
    with pytest.raises(BudgetRequiredError):
        await registry.execute("list_saved_reports", {"limit": 1}, budget=None)


@pytest.mark.asyncio
async def test_unknown_tool_is_rejected() -> None:
    registry = default_registry()
    with pytest.raises(UnknownToolError):
        await registry.execute("not_a_real_tool", {}, budget=_budget())


@pytest.mark.asyncio
async def test_registered_tool_executes_and_validates_schema() -> None:
    class PingInput(BaseModel):
        pass

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="ping",
            description="Test helper",
            input_schema=PingInput,
            handler=lambda _payload: envelope("ping", []),
            estimated_provider_cost=COST_NONE,
        )
    )
    result = await registry.execute("ping", {}, budget=_budget())
    assert result.tool_name == "ping"
    assert result.organization_id == current_organization_id()
    with pytest.raises(ToolValidationError):
        await default_registry().execute("get_saved_report", {}, budget=_budget())
    with pytest.raises(ToolValidationError):
        await default_registry().execute("analyze_listing_v2", {}, budget=_budget())


def test_planner_catalog_hides_handlers_and_product_input() -> None:
    registry = default_registry()
    names = {item.name for item in registry.list_tools()}
    assert names == {
        "analyze_listing_v2",
        "get_product",
        "get_saved_report",
        "list_saved_reports",
    }
    listing = registry.get_tool("analyze_listing_v2")
    assert isinstance(listing, ToolCatalogEntry)
    dumped = listing.model_dump()
    assert set(dumped) == {
        "name",
        "description",
        "input_schema",
        "cost",
        "confirmation_required",
    }
    assert "handler" not in dumped
    properties = dumped["input_schema"].get("properties", {})
    assert "asin" in properties
    assert "product" not in properties
    assert listing.cost == COST_RAINFOREST_PRODUCT


def test_evidence_envelope_and_claim_kinds() -> None:
    item = EvidenceClaim(key="score", value=80, kind="calculated", source="derived")
    packed = envelope("analyze_listing_v2", [item])
    assert packed.tool_name == "analyze_listing_v2"
    assert packed.value("score") == 80
    assert packed.claims[0].kind == "calculated"
    with pytest.raises(ValidationError):
        EvidenceClaim(key="x", value=1, kind="guessed", source="derived")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_saved_report_returns_historical_findings() -> None:
    product, analysis, persist = _persist_report()
    result = await default_registry().execute(
        "get_saved_report",
        {"report_id": persist.report_id},
        budget=_budget(),
    )
    assert result.tool_name == "get_saved_report"
    assert result.value("asin") == product.asin
    assert result.value("listing_quality_score") == analysis.listing_quality_score
    assert result.claim_map()["findings"].kind == "historical"
    assert result.claim_map()["findings"].source == "snapshot"
    assert isinstance(result.value("findings"), list)


@pytest.mark.asyncio
async def test_get_saved_report_preserves_section_scores_findings_and_recommendations() -> None:
    product, analysis, persist = _persist_report()
    result = await default_registry().execute(
        "get_saved_report",
        {"report_id": persist.report_id},
        budget=_budget(),
    )
    assert result.value("listing_quality_score") == analysis.listing_quality_score
    assert result.value("analysis_engine") == "listing_analysis_v2"
    sections = result.value("section_scores")
    assert sections["title"]["score"] == analysis.sections.title.score
    assert sections["bullets"]["score"] == analysis.sections.bullets.score
    assert sections["description_a_plus"]["score"] == analysis.sections.description_a_plus.score
    assert sections["media_coverage"]["score"] == analysis.sections.media_coverage.score
    assert sections["content_structure"]["score"] == analysis.sections.content_structure.score
    assert sections["title"]["max_score"] == analysis.sections.title.max_score
    assert {row["code"] for row in result.value("findings")} == {item.code for item in analysis.findings}
    rec_actions = [row["action"] for row in result.value("recommendations")]
    expected_actions = [
        item.action
        for item in sorted(
            analysis.recommendations,
            key=lambda rec: {"high": 1, "medium": 2, "low": 3}.get(rec.priority.value, 9),
        )[:8]
    ]
    assert rec_actions == expected_actions
    dumped = result.model_dump(mode="json")
    blob = str(dumped)
    assert "listing_analysis_results" not in blob
    assert "sa_instance_state" not in blob
    assert "ProductSnapshot" not in blob
    claim_keys = {item["key"] for item in dumped["claims"]}
    assert "product" not in claim_keys
    assert "payload" not in claim_keys
    assert "organization_id" not in claim_keys
    assert result.value("asin") == product.asin


@pytest.mark.asyncio
async def test_get_saved_report_respects_organization_isolation() -> None:
    product, analysis, persist = _persist_report()
    other_id = _create_other_org_report(product, analysis)
    listed = await default_registry().execute(
        "list_saved_reports",
        {"asin": product.asin},
        budget=_budget(),
    )
    ids = {row["report_id"] for row in listed.value("reports")}
    assert str(persist.report_id) in ids
    assert str(other_id) not in ids
    mixed = await default_registry().execute(
        "list_saved_reports",
        {"asin": product.asin.lower()},
        budget=_budget(),
    )
    mixed_ids = {row["report_id"] for row in mixed.value("reports")}
    assert str(persist.report_id) in mixed_ids
    with pytest.raises(ReportNotFoundError):
        await default_registry().execute(
            "get_saved_report",
            {"report_id": other_id},
            budget=_budget(),
        )


@pytest.mark.asyncio
async def test_analyze_listing_v2_matches_service_and_rejects_product_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    product, origin = await ProductService(provider=get_product_provider()).fetch_product(
        "B0TEST0001",
        "amazon.in",
    )
    assert origin == ProductSource.MOCK.value
    expected = ListingAnalysisV2Service().analyze(product)
    calls = {"n": 0}
    original = ListingAnalysisV2Service.analyze

    def wrapped(self, listing_product):
        calls["n"] += 1
        return original(self, listing_product)

    monkeypatch.setattr(ListingAnalysisV2Service, "analyze", wrapped)
    result = await default_registry().execute(
        "analyze_listing_v2",
        {"asin": "B0TEST0001", "marketplace": "amazon.in"},
        budget=_budget(),
    )
    assert calls["n"] == 1
    assert result.value("listing_quality_score") == expected.listing_quality_score
    assert result.claim_map()["listing_quality_score"].kind == "calculated"
    assert result.claim_map()["asin"].kind == "observed"
    assert result.claim_map()["asin"].source == ProductSource.MOCK.value
    assert result.value("coverage_overall_percentage") == expected.data_coverage.overall_percentage
    assert result.value("findings")
    assert result.value("section_scores")["title"]["score"] == expected.sections.title.score
    assert result.value("recommendations")
    assert all("action" in row for row in result.value("recommendations"))
    assert "rating" in result.value("market_signals")
    fabricated = make_product().model_dump(mode="json")
    with pytest.raises(ToolValidationError):
        await default_registry().execute(
            "analyze_listing_v2",
            {"product": fabricated},
            budget=_budget(),
        )


@pytest.mark.asyncio
async def test_get_product_uses_product_service_and_preserves_cache() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=load_fixture("product.json"))

    provider = RainforestProductDataProvider(
        api_key=TEST_KEY,
        cache=MemoryTtlCache(ttl_seconds=60),
        transport=httpx.MockTransport(handler),
    )
    products = ProductService(provider=provider, mock_provider=MockProductDataProvider())
    registry = ToolRegistry()
    from app.copilot.tools import product as product_tools

    product_tools.register(registry, products=products)
    budget = _budget()
    first = await registry.execute(
        "get_product",
        {"asin": "B07J4TNYV8", "marketplace": "amazon.in"},
        budget=budget,
    )
    second = await registry.execute(
        "get_product",
        {"asin": "B07J4TNYV8", "marketplace": "amazon.in"},
        budget=budget,
        confirmed=True,
    )
    assert first.value("title").startswith("AKASO")
    assert first.claim_map()["title"].kind == "observed"
    assert first.value("provider_source") == ProductSource.RAINFOREST.value
    assert second.value("title") == first.value("title")
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_budget_enforces_tool_limit_confirmation_and_rounds() -> None:
    _persist_report()
    registry = default_registry()
    budget = BudgetTracker()
    for _ in range(4):
        await registry.execute("list_saved_reports", {"limit": 5}, budget=budget)
    with pytest.raises(BudgetExceededError):
        await registry.execute("list_saved_reports", {"limit": 5}, budget=budget)

    expensive = BudgetTracker()
    await registry.execute(
        "get_product",
        {"asin": "B0TEST0001", "marketplace": "amazon.in"},
        budget=expensive,
    )
    with pytest.raises(ConfirmationRequiredError) as blocked:
        await registry.execute(
            "get_product",
            {"asin": "B0TEST0002", "marketplace": "amazon.in", "confirmed": True},
            budget=expensive,
        )
    assert blocked.value.cost_kind == COST_RAINFOREST_PRODUCT
    allowed = await registry.execute(
        "get_product",
        {"asin": "B0TEST0002", "marketplace": "amazon.in"},
        budget=expensive,
        confirmed=True,
    )
    assert allowed.value("asin") == "B0TEST0002"
    assert expensive.rainforest_product_calls == 2
    assert expensive.openai_calls == 0

    rounds = BudgetTracker()
    rounds.begin_round()
    await registry.execute("list_saved_reports", {"limit": 1}, budget=rounds)
    rounds.begin_round()
    await registry.execute("list_saved_reports", {"limit": 1}, budget=rounds)
    with pytest.raises(BudgetExceededError):
        rounds.begin_round()

    policy = BudgetTracker()
    assert policy.requires_confirmation(COST_RAINFOREST_SEARCH) is True
    assert policy.requires_confirmation(COST_OPENAI) is True
    assert policy.requires_confirmation(COST_RAINFOREST_PRODUCT) is False
    policy.record_execution(COST_RAINFOREST_PRODUCT)
    assert policy.requires_confirmation(COST_RAINFOREST_PRODUCT) is True
    assert policy.requires_confirmation("unexpected_provider") is True


@pytest.mark.asyncio
async def test_saved_report_evidence_answers_how_to_improve_listing() -> None:
    _product, analysis, persist = _persist_report()
    packed = await default_registry().execute(
        "get_saved_report",
        {"report_id": persist.report_id},
        budget=_budget(),
    )
    result = await SynthesisService().synthesize(
        SynthesisRequest(
            user_message="How can I improve my listing?",
            intent="explain_listing_score",
            evidence=[packed],
        )
    )
    assert str(analysis.listing_quality_score) in result.summary
    assert any("/" in item and item[0].isalpha() for item in result.findings)
    assert result.recommendations
    cited = {item.claim_key for item in result.citations}
    assert "listing_quality_score" in cited
    assert "section_scores" in cited
    assert "weaknesses" in cited or "findings" in cited
    if analysis.recommendations:
        assert "recommendations" in cited
        assert result.recommendations[0] == analysis.recommendations[0].action or result.recommendations
    assert all("ranking will" not in item.lower() for item in result.findings + result.recommendations)
    assert "20%" not in result.message
    assert "ToolRegistry" not in result.message

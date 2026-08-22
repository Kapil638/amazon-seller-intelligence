from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.copilot import default_registry
from app.copilot.budget import COST_NONE, BudgetTracker
from app.copilot.conversation.service import ConversationService
from app.copilot.exceptions import ToolValidationError
from app.copilot.planner.service import PlannerService
from app.core.exceptions import ProfitModelNotFoundError
from app.models.profit import PROFIT_FORMULA_VERSION
from app.persistence.database import session_scope
from app.persistence.models import Organization, ProfitModel

MODELS_URL = "/api/v1/profit/models"

PROFIT_INPUTS = {
    "selling_price": "999",
    "cogs": "350",
    "referral_fee_amount": "80",
    "fba_fee_amount": "190",
    "shipping_cost": "0",
    "packaging_cost": "0",
    "other_cost": "0",
}

ADS_INPUTS = {
    "period_start": "2026-08-01",
    "period_end": "2026-08-31",
    "ad_spend": "320",
    "ad_sales": "1000",
    "total_sales": "2000",
    "units_in_period": "10",
}


def _budget() -> BudgetTracker:
    return BudgetTracker()


def _create_profit(client: TestClient, asin: str, *, calculate: bool = True) -> str:
    created = client.post(MODELS_URL, json={"asin": asin, **PROFIT_INPUTS})
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    if calculate:
        calculated = client.post(f"{MODELS_URL}/{model_id}/calculate")
        assert calculated.status_code == 200, calculated.text
    return model_id


@pytest.mark.asyncio
async def test_profit_and_ads_tools_are_registered_with_catalog_contract() -> None:
    registry = default_registry()
    for name in (
        "get_profit_snapshot",
        "analyze_profitability",
        "get_advertising_snapshot",
        "analyze_advertising_impact",
    ):
        entry = registry.get_tool(name)
        dumped = entry.model_dump()
        assert set(dumped) == {"name", "description", "input_schema", "cost", "confirmation_required"}
        assert "handler" not in dumped
        assert entry.cost == COST_NONE
        assert entry.confirmation_required is False
        properties = dumped["input_schema"].get("properties", {})
        assert "organization_id" not in properties
        assert "net_profit_before_ads" not in properties
        assert "acos" not in properties


@pytest.mark.asyncio
async def test_get_profit_snapshot_reads_historical_values(client: TestClient) -> None:
    model_id = _create_profit(client, "B0COP00001")
    first = client.get(f"{MODELS_URL}/{model_id}").json()["latest_snapshot"]
    result = await default_registry().execute(
        "get_profit_snapshot",
        {"profit_model_id": model_id, "net_profit_before_ads": "1", "organization_id": str(uuid4())},
        budget=_budget(),
    )
    assert result.tool_name == "get_profit_snapshot"
    assert result.evidence_id is not None
    assert result.organization_id is not None
    assert result.produced_at is not None
    assert result.value("profit_snapshot_id") == first["id"]
    assert result.value("net_profit_before_ads") == "379.00"
    assert result.value("profit_formula_version") == PROFIT_FORMULA_VERSION
    claims = result.claim_map()
    assert claims["selling_price"].kind == "historical"
    assert claims["selling_price"].source == "snapshot"
    assert claims["net_profit_before_ads"].kind == "historical"
    assert claims["amazon_fees"].kind == "historical"
    assert claims["net_profit_before_ads"].kind != "ai_inference"
    second = await default_registry().execute(
        "get_profit_snapshot",
        {"asin": "B0COP00001"},
        budget=_budget(),
    )
    assert second.value("profit_snapshot_id") == first["id"]


@pytest.mark.asyncio
async def test_analyze_profitability_uses_engine_and_does_not_invent_cogs(
    client: TestClient,
) -> None:
    created = client.post(
        MODELS_URL,
        json={
            "asin": "B0COP00002",
            "selling_price": "999",
            "referral_fee_amount": "80",
            "fba_fee_amount": "190",
            "shipping_cost": "0",
            "packaging_cost": "0",
            "other_cost": "0",
        },
    )
    model_id = created.json()["id"]
    result = await default_registry().execute(
        "analyze_profitability",
        {"profit_model_id": model_id},
        budget=_budget(),
    )
    assert result.tool_name == "analyze_profitability"
    claims = result.claim_map()
    assert claims["cogs"].kind == "unknown"
    assert claims["net_profit_before_ads"].kind == "unknown"
    assert claims["net_profit_before_ads"].value is None
    assert claims["selling_price"].kind == "seller_provided"
    assert claims["amazon_fees"].kind == "calculated"
    assert claims["amazon_fees"].source == PROFIT_FORMULA_VERSION
    fetched = client.get(f"{MODELS_URL}/{model_id}").json()
    assert fetched["latest_snapshot"]["id"] == result.value("profit_snapshot_id")


@pytest.mark.asyncio
async def test_get_advertising_snapshot_and_impact_tools(client: TestClient) -> None:
    model_id = _create_profit(client, "B0COP00003")
    client.patch(f"{MODELS_URL}/{model_id}/advertising", json=ADS_INPUTS)
    calculated = client.post(f"{MODELS_URL}/{model_id}/advertising/calculate").json()
    snapshot_id = calculated["latest_snapshot"]["id"]
    ads = await default_registry().execute(
        "get_advertising_snapshot",
        {"asin": "B0COP00003", "acos": "0.99", "tacos": "0.99", "roas": "9"},
        budget=_budget(),
    )
    assert ads.tool_name == "get_advertising_snapshot"
    assert ads.value("acos") == "0.320000"
    assert ads.value("tacos") == "0.160000"
    assert ads.value("roas") == "3.125000"
    assert ads.value("advertising_snapshot_id") == snapshot_id
    assert ads.value("period_start") == "2026-08-01"
    assert ads.value("period_end") == "2026-08-31"
    assert ads.claim_map()["acos"].kind == "historical"
    assert ads.claim_map()["ad_spend"].kind == "historical"
    impact = await default_registry().execute(
        "analyze_advertising_impact",
        {"profit_model_id": model_id, "net_profit_after_ads": "1"},
        budget=_budget(),
    )
    assert impact.tool_name == "analyze_advertising_impact"
    assert impact.value("net_profit_after_ads") == "347.00"
    assert impact.value("profit_snapshot_id") == calculated["impact"]["profit_snapshot_id"]
    assert impact.claim_map()["net_profit_after_ads"].kind == "calculated"
    assert impact.claim_map()["net_profit_after_ads"].source == "advertising_impact"
    assert impact.claim_map()["net_profit_after_ads"].kind != "ai_inference"
    snapshots = client.get(f"{MODELS_URL}/{model_id}/advertising/snapshots").json()
    assert snapshots["total"] == 1
    assert snapshots["items"][0]["id"] == snapshot_id


@pytest.mark.asyncio
async def test_unknown_tacos_and_missing_snapshot(client: TestClient) -> None:
    model_id = _create_profit(client, "B0COP00004")
    client.patch(
        f"{MODELS_URL}/{model_id}/advertising",
        json={**ADS_INPUTS, "total_sales": None},
    )
    client.post(f"{MODELS_URL}/{model_id}/advertising/calculate")
    ads = await default_registry().execute(
        "get_advertising_snapshot",
        {"profit_model_id": model_id},
        budget=_budget(),
    )
    assert ads.claim_map()["tacos"].kind == "unknown"
    assert ads.claim_map()["tacos"].value is None
    assert ads.value("acos") == "0.320000"

    empty = _create_profit(client, "B0COP00005", calculate=False)
    missing = await default_registry().execute(
        "get_profit_snapshot",
        {"profit_model_id": empty},
        budget=_budget(),
    )
    assert missing.claim_map()["net_profit_before_ads"].kind == "unknown"
    assert missing.value("net_profit_before_ads") is None

    no_ads = await default_registry().execute(
        "get_advertising_snapshot",
        {"profit_model_id": empty},
        budget=_budget(),
    )
    assert no_ads.claim_map()["acos"].kind == "unknown"
    assert no_ads.value("acos") is None
    assert client.get(f"{MODELS_URL}/{empty}/advertising/snapshots").json()["total"] == 0


@pytest.mark.asyncio
async def test_organization_isolation_and_schema(client: TestClient) -> None:
    hidden_id = uuid4()
    other_org = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(
            ProfitModel(
                id=hidden_id,
                organization_id=other_org,
                asin="B0HIDDEN03",
                marketplace="amazon.in",
                currency="INR",
                selling_price_source="seller",
            )
        )
    with pytest.raises(ProfitModelNotFoundError):
        await default_registry().execute(
            "get_profit_snapshot",
            {"profit_model_id": str(hidden_id)},
            budget=_budget(),
        )
    with pytest.raises(ProfitModelNotFoundError):
        await default_registry().execute(
            "get_advertising_snapshot",
            {"profit_model_id": str(hidden_id)},
            budget=_budget(),
        )
    with pytest.raises(ToolValidationError):
        await default_registry().execute("get_profit_snapshot", {}, budget=_budget())


@pytest.mark.asyncio
async def test_planner_routes_profit_and_ads_without_skills() -> None:
    conversations = ConversationService()
    created = conversations.create_conversation()
    service = PlannerService(conversations=conversations, registry=default_registry())
    profit = await service.plan_turn(created.id, "Is B0COP00001 profitable?")
    assert profit.intent == "explain_profit"
    assert [call.name for call in profit.tool_calls] == ["get_profit_snapshot"]
    assert profit.tool_calls[0].arguments["asin"] == "B0COP00001"
    ads = await service.plan_turn(created.id, "What is ACOS for B0COP00001?")
    assert ads.intent == "explain_advertising_impact"
    assert [call.name for call in ads.tool_calls] == ["get_advertising_snapshot"]
    blocked = await service.plan_turn(created.id, "Compare my product with competitors")
    assert blocked.intent == "out_of_scope"
    assert blocked.tool_calls == []


def test_domain_tools_are_not_skills_and_contain_no_formulas() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "copilot" / "tools"
    forbidden = (
        "SkillRegistry",
        "langgraph",
        "crewai",
        "ROUND_HALF_UP",
        "profit-calc-v2",
        "ads-calc-v2",
    )
    for name in ("profit.py", "advertising.py"):
        text = (root / name).read_text()
        for token in forbidden:
            assert token not in text, f"{name} must not contain {token}"
        assert "Skill" not in text

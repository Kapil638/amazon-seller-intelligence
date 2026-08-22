from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.analytics.profit_rules import COGS_MISSING_MESSAGE, calculate_profit
from app.models.profit import PROFIT_FORMULA_VERSION, ProfitInputs
from app.persistence.database import session_scope
from app.persistence.models import Organization, ProfitModel, ProfitSnapshot
from app.profit.evidence import profit_evidence_envelope
from app.services.profit_calculation_service import ProfitCalculationService

MODELS_URL = "/api/v1/profit/models"
PREVIEW_URL = "/api/v1/profit/preview"

COMPLETE_INPUTS = {
    "selling_price": "999",
    "cogs": "350",
    "referral_fee": "80",
    "fba_fee": "190",
    "shipping_cost": "0",
    "packaging_cost": "0",
    "other_cost": "0",
}


def test_profit_calc_v1_golden_case() -> None:
    result = ProfitCalculationService().calculate(ProfitInputs.model_validate(COMPLETE_INPUTS))
    assert result.profit_formula_version == PROFIT_FORMULA_VERSION
    assert result.status == "complete"
    assert result.outputs.amazon_fees == Decimal("270.00")
    assert result.outputs.operating_costs == Decimal("0.00")
    assert result.outputs.landed_cost == Decimal("620.00")
    assert result.outputs.net_profit_before_ads == Decimal("379.00")
    assert result.outputs.margin_before_ads == Decimal("0.379379")
    assert result.outputs.roi_on_cogs == Decimal("1.082857")
    assert result.completeness.unknown == []
    assert isinstance(result.outputs.net_profit_before_ads, Decimal)
    assert not isinstance(result.outputs.net_profit_before_ads, float)


def test_missing_cogs_returns_unknown_profit() -> None:
    result = calculate_profit(
        ProfitInputs.model_validate({**COMPLETE_INPUTS, "cogs": None})
    )
    assert result.status == "partial"
    assert "cogs" in result.completeness.unknown
    assert result.outputs.net_profit_before_ads is None
    assert result.outputs.margin_before_ads is None
    assert result.outputs.roi_on_cogs is None
    assert result.outputs.landed_cost is None
    assert COGS_MISSING_MESSAGE in result.completeness.messages
    envelope = profit_evidence_envelope(result, asin="B0TEST0001", marketplace="amazon.in", currency="INR")
    claims = envelope.claim_map()
    assert claims["cogs"].kind == "unknown"
    assert claims["net_profit_before_ads"].kind == "unknown"
    assert claims["net_profit_before_ads"].value is None
    assert claims["net_profit_before_ads"].notes == COGS_MISSING_MESSAGE
    assert claims["selling_price"].kind == "seller_provided"
    assert claims["selling_price"].source == "seller_input"
    assert claims["amazon_fees"].kind == "calculated"
    assert claims["amazon_fees"].source == PROFIT_FORMULA_VERSION


def test_zero_denominators_are_null_not_zero() -> None:
    result = calculate_profit(
        ProfitInputs.model_validate({**COMPLETE_INPUTS, "selling_price": "0", "cogs": "0"})
    )
    assert result.outputs.net_profit_before_ads == Decimal("-270.00")
    assert result.outputs.margin_before_ads is None
    assert result.outputs.roi_on_cogs is None


def test_missing_cost_line_does_not_invent_zero() -> None:
    result = calculate_profit(
        ProfitInputs.model_validate({**COMPLETE_INPUTS, "shipping_cost": None})
    )
    assert result.status == "partial"
    assert "shipping_cost" in result.completeness.unknown
    assert result.outputs.net_profit_before_ads is None
    assert result.outputs.operating_costs is None


def test_negative_money_rejected() -> None:
    try:
        ProfitInputs.model_validate({**COMPLETE_INPUTS, "cogs": "-1"})
    except Exception as exc:
        assert "negative" in str(exc).lower()
    else:
        raise AssertionError("negative COGS should be rejected")


def test_preview_ignores_client_calculated_values(client: TestClient) -> None:
    response = client.post(
        PREVIEW_URL,
        json={
            **COMPLETE_INPUTS,
            "net_profit": "1",
            "margin": "0.99",
            "roi": "9",
            "net_profit_before_ads": "1",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["outputs"]["net_profit_before_ads"] == "379.00"
    assert body["profit_formula_version"] == PROFIT_FORMULA_VERSION
    assert body["evidence"]["tool_name"] == "profit_calculation"


def test_create_calculate_and_latest_snapshot(client: TestClient) -> None:
    created = client.post(
        MODELS_URL,
        json={"asin": "B0TEST0001", "marketplace": "amazon.in", **{
            "selling_price": COMPLETE_INPUTS["selling_price"],
            "cogs": COMPLETE_INPUTS["cogs"],
            "referral_fee_amount": COMPLETE_INPUTS["referral_fee"],
            "fba_fee_amount": COMPLETE_INPUTS["fba_fee"],
            "shipping_cost": COMPLETE_INPUTS["shipping_cost"],
            "packaging_cost": COMPLETE_INPUTS["packaging_cost"],
            "other_cost": COMPLETE_INPUTS["other_cost"],
        }},
    )
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    calculated = client.post(f"{MODELS_URL}/{model_id}/calculate")
    assert calculated.status_code == 200, calculated.text
    snapshot = calculated.json()["latest_snapshot"]
    assert snapshot["status"] == "complete"
    assert snapshot["outputs"]["net_profit_before_ads"] == "379.00"
    fetched = client.get(f"{MODELS_URL}/{model_id}")
    assert fetched.json()["latest_snapshot"]["id"] == snapshot["id"]
    listed = client.get(MODELS_URL)
    asins = [item["asin"] for item in listed.json()["items"]]
    assert "B0TEST0001" in asins


def test_missing_cogs_calculate_is_partial(client: TestClient) -> None:
    created = client.post(MODELS_URL, json={"asin": "B0TEST0002", "selling_price": "999"})
    model_id = created.json()["id"]
    calculated = client.post(f"{MODELS_URL}/{model_id}/calculate")
    snapshot = calculated.json()["latest_snapshot"]
    assert snapshot["status"] == "partial"
    assert snapshot["outputs"]["net_profit_before_ads"] is None
    assert COGS_MISSING_MESSAGE in snapshot["completeness"]["messages"]
    cogs_claim = next(item for item in snapshot["evidence"]["claims"] if item["key"] == "cogs")
    assert cogs_claim["kind"] == "unknown"


def test_snapshots_are_immutable(client: TestClient) -> None:
    created = client.post(
        MODELS_URL,
        json={
            "asin": "B0TEST0003",
            "selling_price": "999",
            "cogs": "350",
            "referral_fee_amount": "80",
            "fba_fee_amount": "190",
            "shipping_cost": "0",
            "packaging_cost": "0",
            "other_cost": "0",
        },
    )
    model_id = created.json()["id"]
    first = client.post(f"{MODELS_URL}/{model_id}/calculate").json()["latest_snapshot"]
    client.patch(f"{MODELS_URL}/{model_id}", json={"cogs": "400"})
    second = client.post(f"{MODELS_URL}/{model_id}/calculate").json()["latest_snapshot"]
    assert first["id"] != second["id"]
    assert second["outputs"]["net_profit_before_ads"] == "329.00"
    with session_scope() as session:
        original = session.get(ProfitSnapshot, UUID(first["id"]))
        assert original is not None
        assert original.outputs_json["net_profit_before_ads"] == "379.00"


def test_organization_isolation(client: TestClient) -> None:
    visible = client.post(MODELS_URL, json={"asin": "B0TEST0004"}).json()
    other_org = uuid4()
    hidden_id = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(
            ProfitModel(
                id=hidden_id,
                organization_id=other_org,
                asin="B0HIDDEN01",
                marketplace="amazon.in",
                currency="INR",
                selling_price_source="seller",
            )
        )
    listed = client.get(MODELS_URL)
    ids = [item["id"] for item in listed.json()["items"]]
    assert visible["id"] in ids
    assert str(hidden_id) not in ids
    assert client.get(f"{MODELS_URL}/{hidden_id}").status_code == 404
    assert client.patch(f"{MODELS_URL}/{hidden_id}", json={"cogs": "10"}).status_code == 404
    assert client.post(f"{MODELS_URL}/{hidden_id}/calculate").status_code == 404


def test_duplicate_asin_conflicts(client: TestClient) -> None:
    first = client.post(MODELS_URL, json={"asin": "B0TEST0005"})
    assert first.status_code == 201
    second = client.post(MODELS_URL, json={"asin": "b0test0005"})
    assert second.status_code == 409


def test_calculation_layer_has_no_external_or_ai_imports() -> None:
    roots = [
        Path(__file__).resolve().parents[1] / "app" / "analytics" / "profit_rules.py",
        Path(__file__).resolve().parents[1] / "app" / "services" / "profit_calculation_service.py",
    ]
    forbidden = ("openai", "rainforest", "httpx", "app.copilot.planner", "app.ai")
    for path in roots:
        text = path.read_text()
        for token in forbidden:
            assert token not in text, f"{path} must not reference {token}"
        assert "float(" not in text

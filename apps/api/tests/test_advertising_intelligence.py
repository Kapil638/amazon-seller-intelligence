from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from app.analytics.advertising_rules import (
    AD_SALES_MISSING_MESSAGE,
    TOTAL_SALES_MISSING_MESSAGE,
    calculate_advertising,
)
from app.models.advertising import ADS_FORMULA_VERSION, AdvertisingInputs
from app.models.profit import ProfitOutputs
from app.persistence.database import session_scope
from app.persistence.models import AdvertisingSnapshot, Organization, ProfitModel
from app.services.advertising_calculation_service import AdvertisingCalculationService
from app.services.advertising_impact_service import UNITS_MISSING_MESSAGE, AdvertisingImpactService

MODELS_URL = "/api/v1/profit/models"
PREVIEW_URL = "/api/v1/advertising/preview"

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


def _create_profit(client: TestClient, asin: str = "B0ADS00001") -> str:
    created = client.post(MODELS_URL, json={"asin": asin, **PROFIT_INPUTS})
    assert created.status_code == 201, created.text
    model_id = created.json()["id"]
    calculated = client.post(f"{MODELS_URL}/{model_id}/calculate")
    assert calculated.status_code == 200, calculated.text
    return model_id


def test_ads_calc_v1_golden_case() -> None:
    result = AdvertisingCalculationService().calculate(AdvertisingInputs.model_validate(ADS_INPUTS))
    assert result.ads_formula_version == ADS_FORMULA_VERSION
    assert result.status == "complete"
    assert result.outputs.acos == Decimal("0.320000")
    assert result.outputs.tacos == Decimal("0.160000")
    assert result.outputs.roas == Decimal("3.125000")
    assert isinstance(result.outputs.acos, Decimal)
    assert not isinstance(result.outputs.acos, float)


def test_missing_ad_sales_acos_unknown() -> None:
    result = calculate_advertising(AdvertisingInputs.model_validate({**ADS_INPUTS, "ad_sales": None}))
    assert result.outputs.acos is None
    assert result.outputs.tacos == Decimal("0.160000")
    assert AD_SALES_MISSING_MESSAGE in result.completeness.messages
    assert "ad_sales" in result.completeness.unknown


def test_missing_total_sales_tacos_unknown_does_not_copy_acos() -> None:
    result = calculate_advertising(AdvertisingInputs.model_validate({**ADS_INPUTS, "total_sales": None}))
    assert result.outputs.acos == Decimal("0.320000")
    assert result.outputs.tacos is None
    assert TOTAL_SALES_MISSING_MESSAGE in result.completeness.messages


def test_zero_denominators_are_null() -> None:
    result = calculate_advertising(
        AdvertisingInputs.model_validate({**ADS_INPUTS, "ad_sales": "0", "ad_spend": "0", "total_sales": "0"})
    )
    assert result.outputs.acos is None
    assert result.outputs.tacos is None
    assert result.outputs.roas is None


def test_impact_subtracts_spend_per_unit() -> None:
    impact = AdvertisingImpactService().compose(
        profit_outputs=ProfitOutputs(
            net_profit_before_ads=Decimal("379.00"),
            margin_before_ads=Decimal("0.379379"),
        ),
        ads_inputs=AdvertisingInputs.model_validate(ADS_INPUTS),
    )
    assert impact.ad_spend_per_unit == Decimal("32.00")
    assert impact.net_profit_after_ads == Decimal("347.00")
    assert impact.break_even_acos == Decimal("0.379379")


def test_impact_unknown_when_units_missing() -> None:
    impact = AdvertisingImpactService().compose(
        profit_outputs=ProfitOutputs(net_profit_before_ads=Decimal("379.00")),
        ads_inputs=AdvertisingInputs.model_validate({**ADS_INPUTS, "units_in_period": None}),
    )
    assert impact.net_profit_after_ads is None
    assert impact.ad_spend_per_unit is None
    assert UNITS_MISSING_MESSAGE in impact.messages


def test_impact_unknown_when_profit_missing() -> None:
    impact = AdvertisingImpactService().compose(
        profit_outputs=None,
        ads_inputs=AdvertisingInputs.model_validate(ADS_INPUTS),
    )
    assert impact.net_profit_after_ads is None
    assert "net_profit_before_ads" in impact.unknown


def test_impact_unknown_when_ad_spend_missing() -> None:
    impact = AdvertisingImpactService().compose(
        profit_outputs=ProfitOutputs(net_profit_before_ads=Decimal("379.00")),
        ads_inputs=AdvertisingInputs.model_validate({**ADS_INPUTS, "ad_spend": None}),
    )
    assert impact.ad_spend_per_unit is None
    assert impact.net_profit_after_ads is None
    assert "ad_spend" in impact.unknown


def test_zero_units_are_unknown() -> None:
    impact = AdvertisingImpactService().compose(
        profit_outputs=ProfitOutputs(net_profit_before_ads=Decimal("379.00")),
        ads_inputs=AdvertisingInputs.model_validate({**ADS_INPUTS, "units_in_period": "0"}),
    )
    assert impact.net_profit_after_ads is None
    assert impact.ad_spend_per_unit is None


def test_preview_ignores_client_calculated_metrics(client: TestClient) -> None:
    response = client.post(
        PREVIEW_URL,
        json={**ADS_INPUTS, "acos": "0.99", "tacos": "0.99", "roas": "9", "net_profit_after_ads": "1"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["outputs"]["acos"] == "0.320000"
    assert body["outputs"]["tacos"] == "0.160000"
    assert body["ads_formula_version"] == ADS_FORMULA_VERSION


def test_create_calculate_and_history(client: TestClient) -> None:
    model_id = _create_profit(client)
    patched = client.patch(f"{MODELS_URL}/{model_id}/advertising", json=ADS_INPUTS)
    assert patched.status_code == 200, patched.text
    calculated = client.post(f"{MODELS_URL}/{model_id}/advertising/calculate")
    assert calculated.status_code == 200, calculated.text
    body = calculated.json()
    assert body["latest_snapshot"]["outputs"]["acos"] == "0.320000"
    assert body["impact"]["net_profit_after_ads"] == "347.00"
    assert body["impact"]["break_even_acos"] == "0.379379"
    fetched = client.get(f"{MODELS_URL}/{model_id}/advertising")
    assert fetched.json()["latest_snapshot"]["id"] == body["latest_snapshot"]["id"]
    assert body["source"] == "seller_input"
    assert body["profit_snapshot_stale"] is False
    claims = {item["key"]: item for item in body["latest_snapshot"]["evidence"]["claims"]}
    assert claims["ad_spend"]["kind"] == "seller_provided"
    assert claims["ad_spend"]["source"] == "seller_input"
    assert claims["acos"]["kind"] == "calculated"
    assert claims["acos"]["source"] == ADS_FORMULA_VERSION
    assert claims["net_profit_after_ads"]["kind"] == "calculated"
    assert claims["net_profit_after_ads"]["source"] == "advertising_impact"
    assert claims["advertising_snapshot_id"]["value"] == body["latest_snapshot"]["id"]
    assert claims["profit_snapshot_id"]["kind"] == "historical"
    history = client.get(f"{MODELS_URL}/{model_id}/advertising/snapshots")
    assert history.json()["total"] == 1
    assert history.json()["items"][0]["acos"] == "0.320000"


def test_snapshots_are_immutable(client: TestClient) -> None:
    model_id = _create_profit(client, asin="B0ADS00002")
    client.patch(f"{MODELS_URL}/{model_id}/advertising", json=ADS_INPUTS)
    first = client.post(f"{MODELS_URL}/{model_id}/advertising/calculate").json()["latest_snapshot"]
    client.patch(f"{MODELS_URL}/{model_id}/advertising", json={"ad_spend": "400"})
    second = client.post(f"{MODELS_URL}/{model_id}/advertising/calculate").json()["latest_snapshot"]
    assert first["id"] != second["id"]
    assert second["outputs"]["acos"] == "0.400000"
    with session_scope() as session:
        original = session.get(AdvertisingSnapshot, UUID(first["id"]))
        assert original is not None
        assert original.outputs_json["acos"] == "0.320000"


def test_organization_isolation(client: TestClient) -> None:
    model_id = _create_profit(client, asin="B0ADS00003")
    other_org = uuid4()
    hidden_profit = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(
            ProfitModel(
                id=hidden_profit,
                organization_id=other_org,
                asin="B0HIDDEN02",
                marketplace="amazon.in",
                currency="INR",
                selling_price_source="seller",
            )
        )
    assert client.get(f"{MODELS_URL}/{hidden_profit}/advertising").status_code == 404
    assert client.patch(f"{MODELS_URL}/{hidden_profit}/advertising", json=ADS_INPUTS).status_code == 404
    assert client.post(f"{MODELS_URL}/{hidden_profit}/advertising/calculate").status_code == 404
    visible = client.get(f"{MODELS_URL}/{model_id}/advertising")
    assert visible.status_code == 200


def test_profit_snapshot_stale_after_unit_recalculate(client: TestClient) -> None:
    model_id = _create_profit(client, asin="B0ADS00004")
    client.patch(f"{MODELS_URL}/{model_id}/advertising", json=ADS_INPUTS)
    ads = client.post(f"{MODELS_URL}/{model_id}/advertising/calculate").json()
    cited = ads["impact"]["profit_snapshot_id"]
    client.patch(f"{MODELS_URL}/{model_id}", json={"cogs": "360"})
    profit = client.post(f"{MODELS_URL}/{model_id}/calculate").json()
    assert profit["latest_snapshot"]["id"] != cited
    refreshed = client.get(f"{MODELS_URL}/{model_id}/advertising").json()
    assert refreshed["profit_snapshot_stale"] is True
    assert refreshed["impact"]["profit_snapshot_id"] == cited


def test_calculation_layer_has_no_external_or_ai_imports() -> None:
    roots = [
        Path(__file__).resolve().parents[1] / "app" / "analytics" / "advertising_rules.py",
        Path(__file__).resolve().parents[1] / "app" / "services" / "advertising_calculation_service.py",
        Path(__file__).resolve().parents[1] / "app" / "services" / "advertising_impact_service.py",
    ]
    forbidden = ("openai", "rainforest", "httpx", "app.copilot.planner", "app.ai", "PPCAnalytics")
    for path in roots:
        text = path.read_text()
        for token in forbidden:
            assert token not in text, f"{path} must not reference {token}"
        assert "float(" not in text

"""Advertising Copilot tools. Wrap modeling + impact services; do not duplicate formulas."""

from __future__ import annotations

from app.advertising.evidence import advertising_evidence_envelope
from app.copilot.budget import COST_NONE
from app.copilot.evidence import EvidenceClaim, EvidenceEnvelope, claim, envelope
from app.copilot.registry import ToolDefinition, ToolRegistry
from app.copilot.schemas import AnalyzeAdvertisingImpactInput, GetAdvertisingSnapshotInput, ProfitDomainToolInput
from app.copilot.tools.profit import resolve_profit_model
from app.models.advertising import AdvertisingCalculationResult, AdvertisingModelResponse
from app.models.profit import ProfitOutputs
from app.services.advertising_impact_service import AdvertisingImpactService
from app.services.advertising_modeling_service import AdvertisingModelingService
from app.services.profit_modeling_service import ProfitModelingService

_NO_SNAPSHOT_NOTE = (
    "No advertising snapshot exists yet. Open Profit and calculate advertising impact to create one."
)


def register(
    registry: ToolRegistry,
    *,
    advertising: AdvertisingModelingService | None = None,
    profit: ProfitModelingService | None = None,
    impact: AdvertisingImpactService | None = None,
) -> None:
    ads_service = advertising or AdvertisingModelingService()
    profit_service = profit or ProfitModelingService()
    impact_service = impact or AdvertisingImpactService()
    registry.register(
        ToolDefinition(
            name="get_advertising_snapshot",
            description=(
                "Retrieve the latest immutable advertising snapshot for a profit model or ASIN. "
                "Does not recalculate ACOS, TACOS, or ROAS."
            ),
            input_schema=GetAdvertisingSnapshotInput,
            handler=lambda payload: _get_advertising_snapshot(  # type: ignore[arg-type]
                ads_service, profit_service, payload
            ),
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="analyze_advertising_impact",
            description=(
                "Compose after-ads impact from stored advertising and profit snapshots using "
                "AdvertisingImpactService. Does not reimplement ads-calc-v1 or profit-calc-v1."
            ),
            input_schema=AnalyzeAdvertisingImpactInput,
            handler=lambda payload: _analyze_advertising_impact(  # type: ignore[arg-type]
                ads_service, profit_service, impact_service, payload
            ),
            estimated_provider_cost=COST_NONE,
        )
    )


def _get_advertising_snapshot(
    ads_service: AdvertisingModelingService,
    profit_service: ProfitModelingService,
    payload: GetAdvertisingSnapshotInput,
) -> EvidenceEnvelope:
    ads = _load_ads(ads_service, profit_service, payload)
    if ads is None:
        profit = resolve_profit_model(profit_service, payload)
        return _missing_ads_envelope("get_advertising_snapshot", profit.organization_id, profit.id, profit.asin)
    snapshot = ads.latest_snapshot
    if snapshot is None:
        return _missing_ads_envelope("get_advertising_snapshot", ads.organization_id, ads.profit_model_id, ads.asin)
    packed = advertising_evidence_envelope(
        _result_from_snapshot(ads),
        asin=ads.asin,
        marketplace=ads.marketplace,
        currency=ads.currency,
        organization_id=ads.organization_id,
        advertising_snapshot_id=snapshot.id,
    )
    extra = [
        claim("profit_model_id", str(ads.profit_model_id), kind="historical", source="snapshot"),
        claim("advertising_model_id", str(ads.id), kind="historical", source="snapshot"),
        claim(
            "calculated_at",
            snapshot.calculated_at.isoformat(),
            kind="historical",
            source="snapshot",
            as_of=snapshot.calculated_at,
        ),
    ]
    return _relabel(packed, "get_advertising_snapshot", extra, historical=True)


def _analyze_advertising_impact(
    ads_service: AdvertisingModelingService,
    profit_service: ProfitModelingService,
    impact_service: AdvertisingImpactService,
    payload: AnalyzeAdvertisingImpactInput,
) -> EvidenceEnvelope:
    ads = _load_ads(ads_service, profit_service, payload)
    if ads is None or ads.latest_snapshot is None:
        profit = resolve_profit_model(profit_service, payload)
        return _missing_ads_envelope(
            "analyze_advertising_impact", profit.organization_id, profit.id, profit.asin
        )
    snapshot = ads.latest_snapshot
    result = _result_from_snapshot(ads)
    cited_id = None
    if snapshot.impact is not None:
        cited_id = snapshot.impact.profit_snapshot_id
    profit_outputs = _cited_profit_outputs(profit_service, cited_id)
    impact = impact_service.compose(
        profit_outputs=profit_outputs,
        ads_inputs=result.inputs,
        profit_snapshot_id=cited_id,
    )
    packed = advertising_evidence_envelope(
        result,
        asin=ads.asin,
        marketplace=ads.marketplace,
        currency=ads.currency,
        impact=impact,
        organization_id=ads.organization_id,
        advertising_snapshot_id=snapshot.id,
    )
    extra = [
        claim("profit_model_id", str(ads.profit_model_id), kind="historical", source="snapshot"),
        claim("advertising_model_id", str(ads.id), kind="historical", source="snapshot"),
    ]
    return _relabel(packed, "analyze_advertising_impact", extra, historical=False)


def _load_ads(
    ads_service: AdvertisingModelingService,
    profit_service: ProfitModelingService,
    payload: ProfitDomainToolInput,
) -> AdvertisingModelResponse | None:
    profit = resolve_profit_model(profit_service, payload)
    return ads_service.get_existing_for_profit_model(profit.id)


def _result_from_snapshot(ads: AdvertisingModelResponse) -> AdvertisingCalculationResult:
    snapshot = ads.latest_snapshot
    assert snapshot is not None
    return AdvertisingCalculationResult(
        ads_formula_version=snapshot.ads_formula_version,
        status=snapshot.status,  # type: ignore[arg-type]
        inputs=snapshot.inputs,
        outputs=snapshot.outputs,
        completeness=snapshot.completeness,
    )


def _cited_profit_outputs(profit_service: ProfitModelingService, snapshot_id) -> ProfitOutputs | None:
    if snapshot_id is None:
        return None
    snapshot = profit_service.get_snapshot(snapshot_id)
    if snapshot is None:
        return None
    return snapshot.outputs


def _missing_ads_envelope(tool_name: str, organization_id, profit_model_id, asin: str) -> EvidenceEnvelope:
    return envelope(
        tool_name,
        [
            claim("profit_model_id", str(profit_model_id), kind="historical", source="snapshot"),
            claim("asin", asin, kind="seller_provided", source="seller_input"),
            claim("acos", None, kind="unknown", source="snapshot", confidence="none", notes=_NO_SNAPSHOT_NOTE),
            claim("tacos", None, kind="unknown", source="snapshot", confidence="none", notes=_NO_SNAPSHOT_NOTE),
            claim("roas", None, kind="unknown", source="snapshot", confidence="none", notes=_NO_SNAPSHOT_NOTE),
            claim(
                "net_profit_after_ads",
                None,
                kind="unknown",
                source="advertising_impact",
                confidence="none",
                notes=_NO_SNAPSHOT_NOTE,
            ),
        ],
        organization_id=organization_id,
    )


def _relabel(
    packed: EvidenceEnvelope,
    tool_name: str,
    extra: list[EvidenceClaim],
    *,
    historical: bool,
) -> EvidenceEnvelope:
    claims: list[EvidenceClaim] = list(extra)
    for item in packed.claims:
        kind = item.kind
        source = item.source
        if historical and kind in {"calculated", "seller_provided"}:
            kind = "historical"
            source = "snapshot"
        claims.append(item.model_copy(update={"kind": kind, "source": source}))
    return envelope(tool_name, claims, organization_id=packed.organization_id)

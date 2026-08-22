"""Profit Copilot tools. Wrap ProfitModelingService; do not calculate in the tool."""

from __future__ import annotations

from app.copilot.budget import COST_NONE
from app.copilot.evidence import EvidenceClaim, EvidenceEnvelope, claim, envelope
from app.copilot.exceptions import ToolValidationError
from app.copilot.registry import ToolDefinition, ToolRegistry
from app.copilot.schemas import AnalyzeProfitabilityInput, GetProfitSnapshotInput, ProfitDomainToolInput
from app.core.exceptions import ProfitModelNotFoundError, ProfitValidationError
from app.core.config import get_settings
from app.models.profit import ProfitCalculationResult, ProfitModelResponse
from app.profit.evidence import profit_evidence_envelope
from app.services.profit_modeling_service import ProfitModelingService

_NO_SNAPSHOT_NOTE = "No profit snapshot exists yet. Open Profit and calculate to create one."


def register(registry: ToolRegistry, modeling: ProfitModelingService | None = None) -> None:
    service = modeling or ProfitModelingService()
    registry.register(
        ToolDefinition(
            name="get_profit_snapshot",
            description=(
                "Retrieve the latest immutable profit snapshot for a profit model or ASIN. "
                "Does not recalculate. Returns unknown when a snapshot is missing."
            ),
            input_schema=GetProfitSnapshotInput,
            handler=lambda payload: _get_profit_snapshot(service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )
    registry.register(
        ToolDefinition(
            name="analyze_profitability",
            description=(
                "Run profit-calc-v1 through ProfitModelingService for an existing worksheet. "
                "The tool does not calculate money itself."
            ),
            input_schema=AnalyzeProfitabilityInput,
            handler=lambda payload: _analyze_profitability(service, payload),  # type: ignore[arg-type]
            estimated_provider_cost=COST_NONE,
        )
    )


def resolve_profit_model(service: ProfitModelingService, payload: ProfitDomainToolInput) -> ProfitModelResponse:
    try:
        if payload.profit_model_id is not None:
            return service.get_model(payload.profit_model_id)
        marketplace = payload.marketplace or get_settings().default_marketplace
        listed = service.list_models(asin=payload.asin)
        match = next((item for item in listed.items if item.marketplace == marketplace), None)
        if match is None and listed.items:
            match = listed.items[0]
        if match is None:
            raise ProfitModelNotFoundError(payload.asin or "")
        return service.get_model(match.id)
    except ProfitValidationError as exc:
        raise ToolValidationError("profit_domain_tool", str(exc)) from exc


def _get_profit_snapshot(service: ProfitModelingService, payload: GetProfitSnapshotInput) -> EvidenceEnvelope:
    model = resolve_profit_model(service, payload)
    snapshot = model.latest_snapshot
    if snapshot is None:
        return _missing_snapshot_envelope("get_profit_snapshot", model)
    result = ProfitCalculationResult(
        profit_formula_version=snapshot.profit_formula_version,
        status=snapshot.status,  # type: ignore[arg-type]
        inputs=snapshot.inputs,
        outputs=snapshot.outputs,
        completeness=snapshot.completeness,
    )
    packed = profit_evidence_envelope(
        result,
        asin=model.asin,
        marketplace=model.marketplace,
        currency=model.currency,
        organization_id=model.organization_id,
    )
    extra = [
        claim("profit_model_id", str(model.id), kind="historical", source="snapshot", as_of=snapshot.calculated_at),
        claim(
            "profit_snapshot_id",
            str(snapshot.id),
            kind="historical",
            source="snapshot",
            as_of=snapshot.calculated_at,
        ),
        claim(
            "calculated_at",
            snapshot.calculated_at.isoformat(),
            kind="historical",
            source="snapshot",
            as_of=snapshot.calculated_at,
        ),
    ]
    return _relabel(packed, "get_profit_snapshot", extra, historical=True, as_of=snapshot.calculated_at)


def _analyze_profitability(service: ProfitModelingService, payload: AnalyzeProfitabilityInput) -> EvidenceEnvelope:
    model = resolve_profit_model(service, payload)
    calculated = service.calculate(model.id)
    snapshot = calculated.latest_snapshot
    if snapshot is None:
        return _missing_snapshot_envelope("analyze_profitability", calculated)
    result = ProfitCalculationResult(
        profit_formula_version=snapshot.profit_formula_version,
        status=snapshot.status,  # type: ignore[arg-type]
        inputs=snapshot.inputs,
        outputs=snapshot.outputs,
        completeness=snapshot.completeness,
    )
    packed = profit_evidence_envelope(
        result,
        asin=calculated.asin,
        marketplace=calculated.marketplace,
        currency=calculated.currency,
        organization_id=calculated.organization_id,
    )
    extra = [
        claim("profit_model_id", str(calculated.id), kind="historical", source="snapshot", as_of=snapshot.calculated_at),
        claim(
            "profit_snapshot_id",
            str(snapshot.id),
            kind="historical",
            source="snapshot",
            as_of=snapshot.calculated_at,
        ),
    ]
    return _relabel(packed, "analyze_profitability", extra, historical=False, as_of=snapshot.calculated_at)


def _missing_snapshot_envelope(tool_name: str, model: ProfitModelResponse) -> EvidenceEnvelope:
    return envelope(
        tool_name,
        [
            claim("profit_model_id", str(model.id), kind="historical", source="snapshot"),
            claim("asin", model.asin, kind="seller_provided", source="seller_input"),
            claim("marketplace", model.marketplace, kind="seller_provided", source="seller_input"),
            claim(
                "net_profit_before_ads",
                None,
                kind="unknown",
                source="snapshot",
                confidence="none",
                notes=_NO_SNAPSHOT_NOTE,
            ),
            claim(
                "margin_before_ads",
                None,
                kind="unknown",
                source="snapshot",
                confidence="none",
                notes=_NO_SNAPSHOT_NOTE,
            ),
            claim(
                "roi_on_cogs",
                None,
                kind="unknown",
                source="snapshot",
                confidence="none",
                notes=_NO_SNAPSHOT_NOTE,
            ),
        ],
        organization_id=model.organization_id,
    )


def _relabel(
    packed: EvidenceEnvelope,
    tool_name: str,
    extra: list[EvidenceClaim],
    *,
    historical: bool,
    as_of,
) -> EvidenceEnvelope:
    claims: list[EvidenceClaim] = list(extra)
    for item in packed.claims:
        kind = item.kind
        source = item.source
        if historical and kind in {"calculated", "seller_provided"}:
            kind = "historical"
            source = "snapshot"
        claims.append(
            item.model_copy(
                update={
                    "kind": kind,
                    "source": source,
                    "as_of": item.as_of or as_of,
                }
            )
        )
    return envelope(tool_name, claims, organization_id=packed.organization_id)

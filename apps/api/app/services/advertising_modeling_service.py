"""Org-scoped advertising worksheets and immutable snapshots.

Does not contain ACOS/TACOS/ROAS formulas or profit formulas.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from app.advertising.evidence import advertising_evidence_envelope
from app.core.exceptions import PersistenceNotConfiguredError, ProfitModelNotFoundError
from app.models.advertising import (
    ADS_FORMULA_VERSION,
    AdvertisingCalculationResult,
    AdvertisingCompleteness,
    AdvertisingImpact,
    AdvertisingInputs,
    AdvertisingModelResponse,
    AdvertisingOutputs,
    AdvertisingPreviewRequest,
    AdvertisingSnapshotListResponse,
    AdvertisingSnapshotResponse,
    AdvertisingSnapshotSummary,
    AdvertisingUpdate,
    DEFAULT_SOURCE,
)
from app.models.profit import ProfitEvidenceClaimView, ProfitEvidenceView, ProfitOutputs
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AdvertisingModel, AdvertisingSnapshot, ProfitModel, ProfitSnapshot
from app.persistence.repositories import AdvertisingModelRepository, ProfitModelRepository
from app.services.advertising_calculation_service import AdvertisingCalculationService
from app.services.advertising_impact_service import AdvertisingImpactService

_UPDATE_FIELDS = (
    "period_start",
    "period_end",
    "ad_spend",
    "ad_sales",
    "total_sales",
    "units_in_period",
)


class AdvertisingModelingService:
    def __init__(
        self,
        calculator: AdvertisingCalculationService | None = None,
        impact: AdvertisingImpactService | None = None,
    ) -> None:
        self._calculator = calculator or AdvertisingCalculationService()
        self._impact = impact or AdvertisingImpactService()

    def get_for_profit_model(self, profit_model_id: UUID) -> AdvertisingModelResponse:
        self._require_persistence()
        with session_scope() as session:
            org_id = current_organization_id()
            profit = self._require_profit(session, org_id, profit_model_id)
            ads = self._get_or_create(session, org_id, profit)
            return self._to_response(session, org_id, ads)

    def get_existing_for_profit_model(self, profit_model_id: UUID) -> AdvertisingModelResponse | None:
        """Read-only. Does not create an advertising worksheet."""
        self._require_persistence()
        with session_scope() as session:
            org_id = current_organization_id()
            profit = self._require_profit(session, org_id, profit_model_id)
            ads = AdvertisingModelRepository(session).get_for_profit_model(org_id, profit.id)
            if ads is None:
                return None
            return self._to_response(session, org_id, ads)

    def update(self, profit_model_id: UUID, payload: AdvertisingUpdate) -> AdvertisingModelResponse:
        self._require_persistence()
        with session_scope() as session:
            org_id = current_organization_id()
            profit = self._require_profit(session, org_id, profit_model_id)
            ads = self._get_or_create(session, org_id, profit)
            for field in _UPDATE_FIELDS:
                if field in payload.model_fields_set:
                    setattr(ads, field, getattr(payload, field))
            ads.updated_at = datetime.now(UTC)
            session.flush()
            return self._to_response(session, org_id, ads)

    def calculate(self, profit_model_id: UUID) -> AdvertisingModelResponse:
        self._require_persistence()
        with session_scope() as session:
            org_id = current_organization_id()
            profit = self._require_profit(session, org_id, profit_model_id)
            ads = self._get_or_create(session, org_id, profit)
            result = self._calculator.calculate(_inputs_from_row(ads))
            profit_snap = ProfitModelRepository(session).latest_snapshot(org_id, profit.id)
            profit_outputs = _profit_outputs(profit_snap)
            impact = self._impact.compose(
                profit_outputs=profit_outputs,
                ads_inputs=result.inputs,
                profit_snapshot_id=profit_snap.id if profit_snap else None,
            )
            snapshot = AdvertisingSnapshot(
                organization_id=org_id,
                advertising_model_id=ads.id,
                profit_model_id=profit.id,
                status=result.status,
                ads_formula_version=ADS_FORMULA_VERSION,
                inputs_json=result.inputs.model_dump(mode="json"),
                outputs_json=result.outputs.model_dump(mode="json"),
                completeness=result.completeness.model_dump(mode="json"),
                impact_json=impact.model_dump(mode="json"),
                profit_snapshot_id=impact.profit_snapshot_id,
                calculated_at=datetime.now(UTC),
            )
            AdvertisingModelRepository(session).add_snapshot(snapshot)
            ads.updated_at = snapshot.calculated_at
            session.flush()
            return self._to_response(
                session,
                org_id,
                ads,
                result=result,
                snapshot=snapshot,
                impact=impact,
            )

    def list_snapshots(self, profit_model_id: UUID) -> AdvertisingSnapshotListResponse:
        self._require_persistence()
        with session_scope() as session:
            org_id = current_organization_id()
            profit = self._require_profit(session, org_id, profit_model_id)
            ads = AdvertisingModelRepository(session).get_for_profit_model(org_id, profit.id)
            if ads is None:
                return AdvertisingSnapshotListResponse()
            rows = AdvertisingModelRepository(session).list_snapshots(org_id, ads.id)
            items = [_summary(row) for row in rows]
            return AdvertisingSnapshotListResponse(items=items, total=len(items))

    def preview(self, payload: AdvertisingPreviewRequest) -> AdvertisingSnapshotResponse:
        result = self._calculator.calculate(payload.to_inputs())
        profit_outputs = None
        if payload.net_profit_before_ads is not None or payload.margin_before_ads is not None:
            profit_outputs = ProfitOutputs(
                net_profit_before_ads=payload.net_profit_before_ads,
                margin_before_ads=payload.margin_before_ads,
            )
        impact = self._impact.compose(
            profit_outputs=profit_outputs,
            ads_inputs=result.inputs,
        )
        evidence = advertising_evidence_envelope(
            result,
            asin=payload.asin,
            marketplace=payload.marketplace,
            currency=payload.currency,
            impact=impact,
            organization_id=current_organization_id(),
        )
        now = datetime.now(UTC)
        nil = UUID("00000000-0000-4000-8000-000000000000")
        return AdvertisingSnapshotResponse(
            id=nil,
            organization_id=current_organization_id(),
            advertising_model_id=nil,
            profit_model_id=nil,
            status=result.status,
            ads_formula_version=result.ads_formula_version,
            inputs=result.inputs,
            outputs=result.outputs,
            completeness=result.completeness,
            impact=impact,
            evidence=_evidence_view(evidence),
            calculated_at=now,
        )

    def _get_or_create(self, session, org_id: UUID, profit: ProfitModel) -> AdvertisingModel:
        repo = AdvertisingModelRepository(session)
        existing = repo.get_for_profit_model(org_id, profit.id)
        if existing is not None:
            return existing
        row = AdvertisingModel(
            organization_id=org_id,
            profit_model_id=profit.id,
            asin=profit.asin,
            marketplace=profit.marketplace,
            currency=profit.currency,
            source=DEFAULT_SOURCE,
        )
        return repo.create(row)

    def _require_profit(self, session, org_id: UUID, profit_model_id: UUID) -> ProfitModel:
        row = ProfitModelRepository(session).get(org_id, profit_model_id)
        if row is None:
            raise ProfitModelNotFoundError(str(profit_model_id))
        return row

    def _to_response(
        self,
        session,
        org_id: UUID,
        ads: AdvertisingModel,
        *,
        result: AdvertisingCalculationResult | None = None,
        snapshot: AdvertisingSnapshot | None = None,
        impact: AdvertisingImpact | None = None,
    ) -> AdvertisingModelResponse:
        repo = AdvertisingModelRepository(session)
        latest = snapshot or repo.latest_snapshot(org_id, ads.id)
        latest_result = result
        latest_impact = impact
        if latest is not None and latest_result is None:
            latest_result = AdvertisingCalculationResult(
                ads_formula_version=latest.ads_formula_version,
                status=latest.status,  # type: ignore[arg-type]
                inputs=AdvertisingInputs.model_validate(latest.inputs_json),
                outputs=AdvertisingOutputs.model_validate(latest.outputs_json),
                completeness=AdvertisingCompleteness.model_validate(latest.completeness),
            )
        if latest is not None and latest_impact is None and latest.impact_json:
            latest_impact = AdvertisingImpact.model_validate(latest.impact_json)
        snapshot_response = None
        if latest is not None and latest_result is not None:
            evidence = advertising_evidence_envelope(
                latest_result,
                asin=ads.asin,
                marketplace=ads.marketplace,
                currency=ads.currency,
                impact=latest_impact,
                organization_id=org_id,
                advertising_snapshot_id=latest.id,
            )
            snapshot_response = AdvertisingSnapshotResponse(
                id=latest.id,
                organization_id=latest.organization_id,
                advertising_model_id=latest.advertising_model_id,
                profit_model_id=latest.profit_model_id,
                status=latest.status,
                ads_formula_version=latest.ads_formula_version,
                inputs=latest_result.inputs,
                outputs=latest_result.outputs,
                completeness=latest_result.completeness,
                impact=latest_impact,
                evidence=_evidence_view(evidence),
                calculated_at=latest.calculated_at,
            )
        return AdvertisingModelResponse(
            id=ads.id,
            organization_id=ads.organization_id,
            profit_model_id=ads.profit_model_id,
            asin=ads.asin,
            marketplace=ads.marketplace,
            currency=ads.currency,
            source=ads.source,
            period_start=ads.period_start,
            period_end=ads.period_end,
            ad_spend=_as_decimal(ads.ad_spend),
            ad_sales=_as_decimal(ads.ad_sales),
            total_sales=_as_decimal(ads.total_sales),
            units_in_period=_as_decimal(ads.units_in_period),
            latest_snapshot=snapshot_response,
            impact=latest_impact,
            profit_snapshot_stale=_profit_snapshot_stale(
                session, org_id, ads.profit_model_id, latest_impact
            ),
            created_at=ads.created_at,
            updated_at=ads.updated_at,
        )

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Advertising Intelligence is not configured.")


def get_advertising_modeling_service() -> AdvertisingModelingService:
    return AdvertisingModelingService()


def _inputs_from_row(row: AdvertisingModel) -> AdvertisingInputs:
    return AdvertisingInputs(
        ad_spend=_as_decimal(row.ad_spend),
        ad_sales=_as_decimal(row.ad_sales),
        total_sales=_as_decimal(row.total_sales),
        units_in_period=_as_decimal(row.units_in_period),
        period_start=row.period_start,
        period_end=row.period_end,
    )


def _profit_outputs(snapshot: ProfitSnapshot | None) -> ProfitOutputs | None:
    if snapshot is None:
        return None
    return ProfitOutputs.model_validate(snapshot.outputs_json)


def _profit_snapshot_stale(
    session,
    org_id: UUID,
    profit_model_id: UUID,
    impact: AdvertisingImpact | None,
) -> bool:
    if impact is None or impact.profit_snapshot_id is None:
        return False
    latest = ProfitModelRepository(session).latest_snapshot(org_id, profit_model_id)
    if latest is None:
        return False
    return latest.id != impact.profit_snapshot_id


def _as_decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _summary(row: AdvertisingSnapshot) -> AdvertisingSnapshotSummary:
    inputs = row.inputs_json or {}
    outputs = row.outputs_json or {}
    return AdvertisingSnapshotSummary(
        id=row.id,
        status=row.status,
        period_start=_as_date(inputs.get("period_start")),
        period_end=_as_date(inputs.get("period_end")),
        acos=outputs.get("acos"),
        tacos=outputs.get("tacos"),
        calculated_at=row.calculated_at,
    )


def _as_date(value: object) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    return date.fromisoformat(str(value)[:10])


def _evidence_view(item) -> ProfitEvidenceView:
    return ProfitEvidenceView(
        evidence_id=item.evidence_id,
        tool_name=item.tool_name,
        organization_id=item.organization_id,
        produced_at=item.produced_at,
        claims=[
            ProfitEvidenceClaimView(
                key=claim.key,
                value=claim.value,
                kind=claim.kind,
                source=claim.source,
                confidence=claim.confidence,
                notes=claim.notes,
            )
            for claim in item.claims
        ],
    )

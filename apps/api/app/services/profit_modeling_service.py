"""Org-scoped profit worksheets and immutable calculation snapshots.

Does not contain formulas. Delegates math to ProfitCalculationService.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from app.core.config import get_settings
from app.core.exceptions import (
    PersistenceNotConfiguredError,
    ProfitModelConflictError,
    ProfitModelNotFoundError,
    ProfitValidationError,
    UnsupportedMarketplaceError,
)
from app.core.validation import is_valid_asin, normalize_asin
from app.models.profit import (
    DEFAULT_CURRENCY,
    DEFAULT_MARKETPLACE,
    PROFIT_FORMULA_VERSION,
    ProfitCalculationResult,
    ProfitCompleteness,
    ProfitEvidenceClaimView,
    ProfitEvidenceView,
    ProfitInputs,
    ProfitModelCreate,
    ProfitModelListResponse,
    ProfitModelResponse,
    ProfitModelSummary,
    ProfitModelUpdate,
    ProfitOutputs,
    ProfitPreviewRequest,
    ProfitSnapshotResponse,
)
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import ProfitModel, ProfitSnapshot
from app.persistence.repositories import ProfitModelRepository
from app.profit.evidence import profit_evidence_envelope
from app.services.profit_calculation_service import ProfitCalculationService

_UPDATE_FIELDS = (
    "selling_price",
    "selling_price_source",
    "cogs",
    "shipping_cost",
    "packaging_cost",
    "other_cost",
    "referral_fee_amount",
    "fba_fee_amount",
    "fee_category_key",
)


class ProfitModelingService:
    def __init__(self, calculator: ProfitCalculationService | None = None) -> None:
        self._calculator = calculator or ProfitCalculationService()

    def list_models(self, *, asin: str | None = None) -> ProfitModelListResponse:
        self._require_persistence()
        normalized = _optional_asin(asin)
        with session_scope() as session:
            repo = ProfitModelRepository(session)
            org_id = current_organization_id()
            rows = repo.list_for_org(org_id, asin=normalized)
            items: list[ProfitModelSummary] = []
            for row in rows:
                latest = repo.latest_snapshot(org_id, row.id)
                completeness = latest.completeness if latest else {}
                items.append(
                    ProfitModelSummary(
                        id=row.id,
                        asin=row.asin,
                        marketplace=row.marketplace,
                        currency=row.currency,
                        latest_status=latest.status if latest else None,
                        unknown=list(completeness.get("unknown") or []),
                        updated_at=row.updated_at,
                    )
                )
            return ProfitModelListResponse(items=items, total=len(items))

    def create_model(self, payload: ProfitModelCreate) -> ProfitModelResponse:
        self._require_persistence()
        marketplace = _marketplace(payload.marketplace)
        currency = (payload.currency or DEFAULT_CURRENCY).strip().upper() or DEFAULT_CURRENCY
        if currency != "INR":
            raise ProfitValidationError("Profit Intelligence V1 supports INR only.")
        with session_scope() as session:
            repo = ProfitModelRepository(session)
            org_id = current_organization_id()
            existing = repo.find_by_asin(org_id, payload.asin, marketplace)
            if existing is not None:
                raise ProfitModelConflictError(
                    f"A profit model already exists for {payload.asin} on {marketplace}."
                )
            row = ProfitModel(
                organization_id=org_id,
                asin=payload.asin,
                marketplace=marketplace,
                currency=currency,
                selling_price=payload.selling_price,
                selling_price_source=payload.selling_price_source or "seller",
                cogs=payload.cogs,
                shipping_cost=payload.shipping_cost,
                packaging_cost=payload.packaging_cost,
                other_cost=payload.other_cost,
                referral_fee_amount=payload.referral_fee_amount,
                fba_fee_amount=payload.fba_fee_amount,
                fee_category_key=payload.fee_category_key,
            )
            repo.create(row)
            session.flush()
            return self._to_response(repo, org_id, row)

    def get_model(self, model_id: UUID) -> ProfitModelResponse:
        self._require_persistence()
        with session_scope() as session:
            repo = ProfitModelRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, model_id)
            if row is None:
                raise ProfitModelNotFoundError(str(model_id))
            return self._to_response(repo, org_id, row)

    def update_model(self, model_id: UUID, payload: ProfitModelUpdate) -> ProfitModelResponse:
        self._require_persistence()
        with session_scope() as session:
            repo = ProfitModelRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, model_id)
            if row is None:
                raise ProfitModelNotFoundError(str(model_id))
            for field in _UPDATE_FIELDS:
                if field in payload.model_fields_set:
                    setattr(row, field, getattr(payload, field))
            if "selling_price" in payload.model_fields_set and "selling_price_source" not in payload.model_fields_set:
                row.selling_price_source = "seller"
            row.updated_at = datetime.now(UTC)
            session.flush()
            return self._to_response(repo, org_id, row)

    def calculate(self, model_id: UUID) -> ProfitModelResponse:
        self._require_persistence()
        with session_scope() as session:
            repo = ProfitModelRepository(session)
            org_id = current_organization_id()
            row = repo.get(org_id, model_id)
            if row is None:
                raise ProfitModelNotFoundError(str(model_id))
            result = self._calculator.calculate(_inputs_from_row(row))
            snapshot = ProfitSnapshot(
                organization_id=org_id,
                profit_model_id=row.id,
                status=result.status,
                profit_formula_version=PROFIT_FORMULA_VERSION,
                inputs_json=result.inputs.model_dump(mode="json"),
                outputs_json=result.outputs.model_dump(mode="json"),
                completeness=result.completeness.model_dump(mode="json"),
                calculated_at=datetime.now(UTC),
            )
            repo.add_snapshot(snapshot)
            row.updated_at = snapshot.calculated_at
            session.flush()
            return self._to_response(repo, org_id, row, result=result, snapshot=snapshot)

    def preview(self, payload: ProfitPreviewRequest) -> ProfitSnapshotResponse:
        result = self._calculator.calculate(payload.to_inputs())
        evidence = profit_evidence_envelope(
            result,
            asin=payload.asin,
            marketplace=payload.marketplace or DEFAULT_MARKETPLACE,
            currency=payload.currency or DEFAULT_CURRENCY,
            organization_id=current_organization_id(),
        )
        now = datetime.now(UTC)
        return ProfitSnapshotResponse(
            id=UUID("00000000-0000-4000-8000-000000000000"),
            organization_id=current_organization_id(),
            profit_model_id=UUID("00000000-0000-4000-8000-000000000000"),
            status=result.status,
            profit_formula_version=result.profit_formula_version,
            inputs=result.inputs,
            outputs=result.outputs,
            completeness=result.completeness,
            evidence=_evidence_view(evidence),
            calculated_at=now,
        )

    def _to_response(
        self,
        repo: ProfitModelRepository,
        org_id: UUID,
        row: ProfitModel,
        *,
        result: ProfitCalculationResult | None = None,
        snapshot: ProfitSnapshot | None = None,
    ) -> ProfitModelResponse:
        latest = snapshot or repo.latest_snapshot(org_id, row.id)
        latest_result = result
        if latest is not None and latest_result is None:
            latest_result = ProfitCalculationResult(
                profit_formula_version=latest.profit_formula_version,
                status=latest.status,  # type: ignore[arg-type]
                inputs=ProfitInputs.model_validate(latest.inputs_json),
                outputs=ProfitOutputs.model_validate(latest.outputs_json),
                completeness=ProfitCompleteness.model_validate(latest.completeness),
            )
        snapshot_response = None
        if latest is not None and latest_result is not None:
            evidence = profit_evidence_envelope(
                latest_result,
                asin=row.asin,
                marketplace=row.marketplace,
                currency=row.currency,
                organization_id=org_id,
            )
            snapshot_response = ProfitSnapshotResponse(
                id=latest.id,
                organization_id=latest.organization_id,
                profit_model_id=latest.profit_model_id,
                status=latest.status,
                profit_formula_version=latest.profit_formula_version,
                inputs=latest_result.inputs,
                outputs=latest_result.outputs,
                completeness=latest_result.completeness,
                evidence=_evidence_view(evidence),
                calculated_at=latest.calculated_at,
            )
        return ProfitModelResponse(
            id=row.id,
            organization_id=row.organization_id,
            asin=row.asin,
            marketplace=row.marketplace,
            currency=row.currency,
            selling_price=_as_decimal(row.selling_price),
            selling_price_source=row.selling_price_source,
            cogs=_as_decimal(row.cogs),
            shipping_cost=_as_decimal(row.shipping_cost),
            packaging_cost=_as_decimal(row.packaging_cost),
            other_cost=_as_decimal(row.other_cost),
            referral_fee_amount=_as_decimal(row.referral_fee_amount),
            fba_fee_amount=_as_decimal(row.fba_fee_amount),
            fee_category_key=row.fee_category_key,
            latest_snapshot=snapshot_response,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    def _require_persistence(self) -> None:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError("Profit Intelligence is not configured.")


def get_profit_modeling_service() -> ProfitModelingService:
    return ProfitModelingService()


def _inputs_from_row(row: ProfitModel) -> ProfitInputs:
    return ProfitInputs(
        selling_price=_as_decimal(row.selling_price),
        cogs=_as_decimal(row.cogs),
        referral_fee=_as_decimal(row.referral_fee_amount),
        fba_fee=_as_decimal(row.fba_fee_amount),
        shipping_cost=_as_decimal(row.shipping_cost),
        packaging_cost=_as_decimal(row.packaging_cost),
        other_cost=_as_decimal(row.other_cost),
    )


def _as_decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _marketplace(value: str | None) -> str:
    marketplace = (value or get_settings().default_marketplace).strip().lower()
    if marketplace not in get_settings().supported_marketplaces:
        raise UnsupportedMarketplaceError(marketplace)
    return marketplace


def _optional_asin(asin: str | None) -> str | None:
    if asin is None or not asin.strip():
        return None
    normalized = normalize_asin(asin)
    if not is_valid_asin(normalized):
        raise ProfitValidationError("ASIN must be 10 letters or numbers.")
    return normalized


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

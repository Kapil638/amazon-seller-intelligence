from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.core.exceptions import PersistenceNotConfiguredError, ReportNotFoundError
from app.models.ai_image_intelligence import AIImageIntelligence
from app.models.ai_listing_intelligence import AITokenUsage
from app.models.ai_listing_intelligence_v2 import AIListingIntelligenceV2
from app.models.listing_analysis_v2 import ListingAnalysisV2
from app.models.product import Product
from app.models.saved_analysis import (
    PersistMeta,
    SavedAnalysisDeleteResponse,
    SavedAnalysisDetail,
    SavedAnalysisListResponse,
    SavedAnalysisMetadata,
    SavedAnalysisSummary,
)
from app.models.scoring_profile import CustomScoreResult, ScoringProfileSnapshot
from app.persistence.database import current_organization_id, persistence_enabled, session_scope
from app.persistence.models import AnalysisRun
from app.persistence.repositories import AnalysisRunRepository, ProductSnapshotRepository

logger = logging.getLogger("app.persistence.history")

UNAVAILABLE = "Analysis succeeded but could not be saved."
NOT_CONFIGURED = "Report history is not configured. The live analysis was not saved."


class AnalysisHistoryService:
    """Persist and reconstruct listing reports. Does not recalculate scores or call providers."""

    def record_listing_v2(
        self,
        product: Product,
        analysis: ListingAnalysisV2,
        source: str | None,
        custom_score: CustomScoreResult | None = None,
    ) -> PersistMeta:
        if not persistence_enabled():
            return PersistMeta(persisted=False, persistence_warning=NOT_CONFIGURED)
        try:
            with session_scope() as session:
                org_id = current_organization_id()
                snapshots = ProductSnapshotRepository(session)
                runs = AnalysisRunRepository(session)
                payload = product.model_dump(mode="json")
                snapshot = snapshots.create(
                    organization_id=org_id,
                    asin=product.asin,
                    marketplace=product.marketplace,
                    source=source,
                    product_payload=payload,
                    fetched_at=product.last_fetched_at,
                )
                title = (product.title or "").strip() or product.asin
                analyzed = datetime.now(UTC).strftime("%d %b %Y")
                run = runs.create(
                    organization_id=org_id,
                    snapshot=snapshot,
                    status="complete",
                    listing_score_version=analysis.score_version,
                    product_source=source,
                    display_name=f"{title} · {analyzed}",
                )
                runs.save_listing_result(
                    run,
                    score_version=analysis.score_version,
                    listing_quality_score=analysis.listing_quality_score,
                    payload=analysis.model_dump(mode="json"),
                    custom_listing_quality_score=(
                        custom_score.custom_listing_quality_score if custom_score else None
                    ),
                    scoring_profile_snapshot=_snapshot_payload(custom_score),
                )
                return PersistMeta(report_id=run.id, persisted=True)
        except Exception:
            logger.exception("Failed to persist listing analysis v2")
            return PersistMeta(persisted=False, persistence_warning=UNAVAILABLE)

    def record_ai_v2(
        self,
        product: Product,
        analysis: ListingAnalysisV2,
        intelligence: AIListingIntelligenceV2,
        *,
        report_id: UUID | None,
        source: str | None,
        provider: str,
        model: str,
        prompt_version: str,
        usage: AITokenUsage | None,
        latency_ms: int | None,
        estimated_cost_usd: float | None,
    ) -> PersistMeta:
        if not persistence_enabled():
            return PersistMeta(report_id=report_id, persisted=False, persistence_warning=NOT_CONFIGURED)
        try:
            with session_scope() as session:
                org_id = current_organization_id()
                runs = AnalysisRunRepository(session)
                run = self._ensure_run(session, runs, org_id, report_id, product, analysis, source)
                runs.save_ai_result(
                    run,
                    provider=provider,
                    model=model,
                    prompt_version=prompt_version,
                    payload=intelligence.model_dump(mode="json"),
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                    estimated_cost_usd=estimated_cost_usd,
                    latency_ms=latency_ms,
                )
                runs.mark_complete(run)
                return PersistMeta(report_id=run.id, persisted=True)
        except Exception:
            logger.exception("Failed to persist AI listing intelligence v2")
            return PersistMeta(report_id=report_id, persisted=False, persistence_warning=UNAVAILABLE)

    def record_ai_v2_failure(self, report_id: UUID | None, note: str) -> None:
        if not persistence_enabled() or report_id is None:
            return
        try:
            with session_scope() as session:
                runs = AnalysisRunRepository(session)
                run = runs.get(current_organization_id(), report_id)
                if run is not None:
                    runs.mark_partial(run, note)
        except Exception:
            logger.exception("Failed to mark analysis run partial after AI failure")

    def record_image_intelligence(
        self,
        product: Product,
        analysis: ListingAnalysisV2,
        intelligence: AIImageIntelligence,
        *,
        report_id: UUID | None,
        source: str | None,
        provider: str,
        model: str,
        prompt_version: str,
        images_available: int,
        images_selected: int,
        images_skipped: int,
        usage: AITokenUsage | None,
        latency_ms: int | None,
        estimated_cost_usd: float | None,
    ) -> PersistMeta:
        if not persistence_enabled():
            return PersistMeta(report_id=report_id, persisted=False, persistence_warning=NOT_CONFIGURED)
        try:
            with session_scope() as session:
                org_id = current_organization_id()
                runs = AnalysisRunRepository(session)
                run = self._ensure_run(session, runs, org_id, report_id, product, analysis, source)
                runs.save_image_result(
                    run,
                    provider=provider,
                    model=model,
                    prompt_version=prompt_version,
                    payload=intelligence.model_dump(mode="json"),
                    images_available=images_available,
                    images_selected=images_selected,
                    images_skipped=images_skipped,
                    input_tokens=usage.input_tokens if usage else None,
                    output_tokens=usage.output_tokens if usage else None,
                    total_tokens=usage.total_tokens if usage else None,
                    estimated_cost_usd=estimated_cost_usd,
                    latency_ms=latency_ms,
                )
                runs.mark_complete(run)
                return PersistMeta(report_id=run.id, persisted=True)
        except Exception:
            logger.exception("Failed to persist image intelligence")
            return PersistMeta(report_id=report_id, persisted=False, persistence_warning=UNAVAILABLE)

    def record_image_failure(self, report_id: UUID | None, note: str) -> None:
        if not persistence_enabled() or report_id is None:
            return
        try:
            with session_scope() as session:
                runs = AnalysisRunRepository(session)
                run = runs.get(current_organization_id(), report_id)
                if run is not None:
                    runs.mark_partial(run, note)
        except Exception:
            logger.exception("Failed to mark analysis run partial after image failure")

    def list_reports(
        self,
        *,
        asin: str | None = None,
        marketplace: str | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> SavedAnalysisListResponse:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError()
        limit = min(max(limit, 1), 100)
        offset = max(offset, 0)
        if asin:
            asin = asin.strip().upper()
        with session_scope() as session:
            runs, total = AnalysisRunRepository(session).list_page(
                current_organization_id(),
                asin=asin,
                marketplace=marketplace,
                status=status,
                created_from=created_from,
                created_to=created_to,
                offset=offset,
                limit=limit,
            )
            items = [_summary(run) for run in runs]
        return SavedAnalysisListResponse(items=items, total=total, offset=offset, limit=limit)

    def latest_complete_report_id(self, asin: str) -> UUID | None:
        """Latest complete/partial listing analysis for this org and ASIN. Does not fetch Amazon."""
        if not persistence_enabled():
            return None
        normalized = (asin or "").strip().upper()
        if not normalized:
            return None
        with session_scope() as session:
            run = AnalysisRunRepository(session).latest_complete_for_asin(
                current_organization_id(),
                normalized,
            )
            if run is None or run.listing_result is None:
                return None
            return run.id

    def get_report(self, report_id: UUID) -> SavedAnalysisDetail:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError()
        with session_scope() as session:
            run = AnalysisRunRepository(session).get(current_organization_id(), report_id)
            if run is None or run.listing_result is None:
                raise ReportNotFoundError(str(report_id))
            product = Product.model_validate(run.snapshot.normalized_product)
            analysis = ListingAnalysisV2.model_validate(run.listing_result.payload)
            ai = (
                AIListingIntelligenceV2.model_validate(run.ai_result.payload)
                if run.ai_result is not None
                else None
            )
            image = (
                AIImageIntelligence.model_validate(run.image_result.payload)
                if run.image_result is not None
                else None
            )
            return SavedAnalysisDetail(
                report_id=run.id,
                display_name=run.display_name,
                product=product,
                analysis=analysis,
                custom_score=_custom_from_result(run.listing_result),
                ai_intelligence=ai,
                image_intelligence=image,
                meta=SavedAnalysisMetadata(
                    historical=True,
                    analyzed_at=run.created_at,
                    product_fetched_at=run.snapshot.fetched_at,
                    product_source=run.product_source,
                    listing_score_version=run.listing_score_version,
                    ai_prompt_version=run.ai_prompt_version,
                    image_prompt_version=run.image_prompt_version,
                    ai_provider=run.ai_result.provider if run.ai_result else None,
                    ai_model=run.ai_result.model if run.ai_result else None,
                    image_provider=run.image_result.provider if run.image_result else None,
                    image_model=run.image_result.model if run.image_result else None,
                    images_available=run.image_result.images_available if run.image_result else None,
                    images_selected=run.image_result.images_selected if run.image_result else None,
                    images_skipped=run.image_result.images_skipped if run.image_result else None,
                    status=run.status,
                ),
            )

    def _ensure_run(
        self,
        session,
        runs: AnalysisRunRepository,
        org_id: UUID,
        report_id: UUID | None,
        product: Product,
        analysis: ListingAnalysisV2,
        source: str | None,
    ) -> AnalysisRun:
        if report_id is not None:
            existing = runs.get(org_id, report_id)
            if existing is not None:
                return existing
        snapshots = ProductSnapshotRepository(session)
        snapshot = snapshots.create(
            organization_id=org_id,
            asin=product.asin,
            marketplace=product.marketplace,
            source=source,
            product_payload=product.model_dump(mode="json"),
            fetched_at=product.last_fetched_at,
        )
        title = (product.title or "").strip() or product.asin
        run = runs.create(
            organization_id=org_id,
            snapshot=snapshot,
            status="complete",
            listing_score_version=analysis.score_version,
            product_source=source,
            display_name=f"{title} · {datetime.now(UTC).strftime('%d %b %Y')}",
        )
        runs.save_listing_result(
            run,
            score_version=analysis.score_version,
            listing_quality_score=analysis.listing_quality_score,
            payload=analysis.model_dump(mode="json"),
        )
        return run

    def update_custom_score(self, report_id: UUID, custom_score: CustomScoreResult | None) -> bool:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError()
        with session_scope() as session:
            run = AnalysisRunRepository(session).get(current_organization_id(), report_id)
            if run is None or run.listing_result is None:
                raise ReportNotFoundError(str(report_id))
            AnalysisRunRepository(session).save_custom_score(
                run,
                custom_listing_quality_score=(
                    custom_score.custom_listing_quality_score if custom_score else None
                ),
                scoring_profile_snapshot=_snapshot_payload(custom_score),
            )
            return True

    def soft_delete(self, report_id: UUID) -> SavedAnalysisDeleteResponse:
        if not persistence_enabled():
            raise PersistenceNotConfiguredError()
        with session_scope() as session:
            runs = AnalysisRunRepository(session)
            run = runs.get(current_organization_id(), report_id)
            if run is None or run.listing_result is None:
                raise ReportNotFoundError(str(report_id))
            runs.soft_delete(run)
            return SavedAnalysisDeleteResponse(report_id=run.id, deleted=True)


def _summary(run: AnalysisRun) -> SavedAnalysisSummary:
    product = run.snapshot.normalized_product if run.snapshot else {}
    return SavedAnalysisSummary(
        report_id=run.id,
        asin=run.asin,
        product_title=product.get("title") if isinstance(product, dict) else None,
        brand=product.get("brand") if isinstance(product, dict) else None,
        marketplace=run.marketplace,
        listing_quality_score=run.listing_result.listing_quality_score if run.listing_result else None,
        custom_listing_quality_score=(
            run.listing_result.custom_listing_quality_score if run.listing_result else None
        ),
        scoring_profile_name=_profile_name(run),
        source=run.product_source,
        has_ai_strategy=run.ai_result is not None,
        has_image_intelligence=run.image_result is not None,
        created_at=run.created_at,
        completed_at=run.completed_at,
        status=run.status,
        display_name=run.display_name,
    )


def _custom_from_result(row) -> CustomScoreResult | None:
    if row is None or row.custom_listing_quality_score is None or not row.scoring_profile_snapshot:
        return None
    return CustomScoreResult(
        custom_listing_quality_score=row.custom_listing_quality_score,
        profile=ScoringProfileSnapshot.model_validate(row.scoring_profile_snapshot),
    )


def _snapshot_payload(custom_score: CustomScoreResult | None) -> dict | None:
    if custom_score is None:
        return None
    payload = custom_score.profile.model_dump(mode="json")
    payload["custom_listing_quality_score"] = custom_score.custom_listing_quality_score
    return payload


def _profile_name(run: AnalysisRun) -> str | None:
    snapshot = run.listing_result.scoring_profile_snapshot if run.listing_result else None
    if isinstance(snapshot, dict):
        name = snapshot.get("profile_name")
        if isinstance(name, str) and name.strip():
            return name
    return None


def get_analysis_history_service() -> AnalysisHistoryService:
    return AnalysisHistoryService()

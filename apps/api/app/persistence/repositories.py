from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import Select, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.amazon.secrets import InvalidSecretReferenceError, parse_asi_amazon_secret_reference
from app.core.config import get_settings
from app.persistence.hashing import product_content_hash, sha256_bytes
from app.persistence.models import (
    AIListingResult,
    AnalysisRun,
    BulkJob,
    BulkJobItem,
    CopilotConversation,
    CopilotMessage,
    CopilotPendingConfirmation,
    GeneratedReport,
    ImageIntelligenceResult,
    ListingAnalysisResult,
    ProductSnapshot,
    ReportUpload,
    ScoringProfile,
    UsageEvent,
    ProfitModel,
    ProfitSnapshot,
    AdvertisingModel,
    AdvertisingSnapshot,
    AmazonConnection,
    AmazonIngestionRun,
    AmazonMarketplaceParticipation,
    AmazonOAuthState,
    AmazonSellerAccount,
)


def ensure_organization_id(session: Session) -> UUID:
    return get_settings().default_organization_id


class ProductSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        organization_id: UUID,
        asin: str,
        marketplace: str,
        source: str | None,
        product_payload: dict[str, Any],
        fetched_at: datetime | None = None,
    ) -> ProductSnapshot:
        snapshot = ProductSnapshot(
            organization_id=organization_id,
            asin=asin,
            marketplace=marketplace,
            source=source,
            normalized_product=product_payload,
            content_hash=product_content_hash(product_payload),
            fetched_at=fetched_at or datetime.now(UTC),
        )
        self.session.add(snapshot)
        self.session.flush()
        return snapshot

    def list_for_asin(self, organization_id: UUID, asin: str) -> list[ProductSnapshot]:
        statement: Select[tuple[ProductSnapshot]] = (
            select(ProductSnapshot)
            .where(
                ProductSnapshot.organization_id == organization_id,
                ProductSnapshot.asin == asin,
            )
            .order_by(ProductSnapshot.fetched_at.asc(), ProductSnapshot.created_at.asc())
        )
        return list(self.session.scalars(statement).all())


class AnalysisRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        organization_id: UUID,
        snapshot: ProductSnapshot,
        status: str,
        listing_score_version: str | None,
        product_source: str | None,
        display_name: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> AnalysisRun:
        now = datetime.now(UTC)
        run = AnalysisRun(
            organization_id=organization_id,
            product_snapshot_id=snapshot.id,
            asin=snapshot.asin,
            marketplace=snapshot.marketplace,
            status=status,
            listing_score_version=listing_score_version,
            product_source=product_source,
            display_name=display_name,
            extra_metadata=metadata,
            started_at=now,
            completed_at=now if status in {"complete", "partial"} else None,
        )
        self.session.add(run)
        self.session.flush()
        return run

    def get(self, organization_id: UUID, run_id: UUID, *, include_deleted: bool = False) -> AnalysisRun | None:
        filters = [AnalysisRun.organization_id == organization_id, AnalysisRun.id == run_id]
        if not include_deleted:
            filters.append(AnalysisRun.deleted_at.is_(None))
        statement = (
            select(AnalysisRun)
            .options(
                selectinload(AnalysisRun.snapshot),
                selectinload(AnalysisRun.listing_result),
                selectinload(AnalysisRun.ai_result),
                selectinload(AnalysisRun.image_result),
            )
            .where(*filters)
        )
        return self.session.scalars(statement).first()

    def latest_for_asin(self, organization_id: UUID, asin: str) -> AnalysisRun | None:
        statement = (
            select(AnalysisRun)
            .options(
                selectinload(AnalysisRun.snapshot),
                selectinload(AnalysisRun.listing_result),
                selectinload(AnalysisRun.ai_result),
                selectinload(AnalysisRun.image_result),
            )
            .where(AnalysisRun.organization_id == organization_id, AnalysisRun.asin == asin)
            .where(AnalysisRun.deleted_at.is_(None))
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(statement).first()

    def latest_complete_for_asin(self, organization_id: UUID, asin: str) -> AnalysisRun | None:
        normalized = asin.strip().upper()
        statement = (
            select(AnalysisRun)
            .join(AnalysisRun.listing_result)
            .options(
                selectinload(AnalysisRun.snapshot),
                selectinload(AnalysisRun.listing_result),
                selectinload(AnalysisRun.ai_result),
                selectinload(AnalysisRun.image_result),
            )
            .where(
                AnalysisRun.organization_id == organization_id,
                func.upper(AnalysisRun.asin) == normalized,
                AnalysisRun.deleted_at.is_(None),
                AnalysisRun.status.in_(("complete", "partial")),
            )
            .order_by(AnalysisRun.created_at.desc())
            .limit(1)
        )
        return self.session.scalars(statement).first()

    def list_page(
        self,
        organization_id: UUID,
        *,
        asin: str | None = None,
        marketplace: str | None = None,
        status: str | None = None,
        created_from: datetime | None = None,
        created_to: datetime | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AnalysisRun], int]:
        filters = [AnalysisRun.organization_id == organization_id, AnalysisRun.deleted_at.is_(None)]
        if asin:
            filters.append(func.upper(AnalysisRun.asin) == asin.strip().upper())
        if marketplace:
            filters.append(AnalysisRun.marketplace == marketplace)
        if status:
            filters.append(AnalysisRun.status == status)
        if created_from is not None:
            filters.append(AnalysisRun.created_at >= created_from)
        if created_to is not None:
            filters.append(AnalysisRun.created_at <= created_to)
        count = self.session.scalar(select(func.count()).select_from(AnalysisRun).where(*filters)) or 0
        statement = (
            select(AnalysisRun)
            .options(
                selectinload(AnalysisRun.snapshot),
                selectinload(AnalysisRun.listing_result),
                selectinload(AnalysisRun.ai_result),
                selectinload(AnalysisRun.image_result),
            )
            .where(*filters)
            .order_by(AnalysisRun.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all()), int(count)

    def soft_delete(self, run: AnalysisRun) -> AnalysisRun:
        run.deleted_at = datetime.now(UTC)
        self.session.flush()
        return run

    def save_listing_result(
        self,
        run: AnalysisRun,
        *,
        score_version: str,
        listing_quality_score: int,
        payload: dict[str, Any],
        custom_listing_quality_score: int | None = None,
        scoring_profile_snapshot: dict[str, Any] | None = None,
    ) -> ListingAnalysisResult:
        existing = run.listing_result or self.session.scalars(
            select(ListingAnalysisResult).where(ListingAnalysisResult.analysis_run_id == run.id)
        ).first()
        if existing is not None:
            existing.score_version = score_version
            existing.listing_quality_score = listing_quality_score
            existing.payload = payload
            if custom_listing_quality_score is not None or scoring_profile_snapshot is not None:
                existing.custom_listing_quality_score = custom_listing_quality_score
                existing.scoring_profile_snapshot = scoring_profile_snapshot
            run.listing_result = existing
            run.listing_score_version = score_version
            self.session.flush()
            return existing
        row = ListingAnalysisResult(
            analysis_run_id=run.id,
            score_version=score_version,
            listing_quality_score=listing_quality_score,
            custom_listing_quality_score=custom_listing_quality_score,
            scoring_profile_snapshot=scoring_profile_snapshot,
            payload=payload,
        )
        self.session.add(row)
        run.listing_result = row
        run.listing_score_version = score_version
        self.session.flush()
        return row

    def save_custom_score(
        self,
        run: AnalysisRun,
        *,
        custom_listing_quality_score: int | None,
        scoring_profile_snapshot: dict[str, Any] | None,
    ) -> ListingAnalysisResult | None:
        existing = run.listing_result
        if existing is None:
            return None
        existing.custom_listing_quality_score = custom_listing_quality_score
        existing.scoring_profile_snapshot = scoring_profile_snapshot
        self.session.flush()
        return existing

    def save_ai_result(
        self,
        run: AnalysisRun,
        *,
        provider: str,
        model: str,
        prompt_version: str,
        payload: dict[str, Any],
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        estimated_cost_usd: float | None,
        latency_ms: int | None,
    ) -> AIListingResult:
        existing = run.ai_result or self.session.scalars(
            select(AIListingResult).where(AIListingResult.analysis_run_id == run.id)
        ).first()
        if existing is not None:
            existing.provider = provider
            existing.model = model
            existing.prompt_version = prompt_version
            existing.payload = payload
            existing.input_tokens = input_tokens
            existing.output_tokens = output_tokens
            existing.total_tokens = total_tokens
            existing.estimated_cost_usd = estimated_cost_usd
            existing.latency_ms = latency_ms
            run.ai_result = existing
            run.ai_prompt_version = prompt_version
            self.session.flush()
            return existing
        row = AIListingResult(
            analysis_run_id=run.id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            payload=payload,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            latency_ms=latency_ms,
        )
        self.session.add(row)
        run.ai_result = row
        run.ai_prompt_version = prompt_version
        self.session.flush()
        return row

    def save_image_result(
        self,
        run: AnalysisRun,
        *,
        provider: str,
        model: str,
        prompt_version: str,
        payload: dict[str, Any],
        images_available: int,
        images_selected: int,
        images_skipped: int,
        input_tokens: int | None,
        output_tokens: int | None,
        total_tokens: int | None,
        estimated_cost_usd: float | None,
        latency_ms: int | None,
    ) -> ImageIntelligenceResult:
        existing = run.image_result or self.session.scalars(
            select(ImageIntelligenceResult).where(ImageIntelligenceResult.analysis_run_id == run.id)
        ).first()
        if existing is not None:
            existing.provider = provider
            existing.model = model
            existing.prompt_version = prompt_version
            existing.payload = payload
            existing.images_available = images_available
            existing.images_selected = images_selected
            existing.images_skipped = images_skipped
            existing.input_tokens = input_tokens
            existing.output_tokens = output_tokens
            existing.total_tokens = total_tokens
            existing.estimated_cost_usd = estimated_cost_usd
            existing.latency_ms = latency_ms
            run.image_result = existing
            run.image_prompt_version = prompt_version
            self.session.flush()
            return existing
        row = ImageIntelligenceResult(
            analysis_run_id=run.id,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            payload=payload,
            images_available=images_available,
            images_selected=images_selected,
            images_skipped=images_skipped,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            estimated_cost_usd=estimated_cost_usd,
            latency_ms=latency_ms,
        )
        self.session.add(row)
        run.image_result = row
        run.image_prompt_version = prompt_version
        self.session.flush()
        return row

    def mark_partial(self, run: AnalysisRun, note: str) -> None:
        run.status = "partial"
        meta = dict(run.extra_metadata or {})
        meta["last_optional_error"] = note
        run.extra_metadata = meta
        run.completed_at = datetime.now(UTC)
        self.session.flush()

    def mark_complete(self, run: AnalysisRun) -> None:
        if run.status != "partial":
            run.status = "complete"
        run.completed_at = datetime.now(UTC)
        self.session.flush()


class ReportUploadRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_hash(self, organization_id: UUID, file_hash: str) -> ReportUpload | None:
        statement = (
            select(ReportUpload)
            .where(
                ReportUpload.organization_id == organization_id,
                ReportUpload.file_hash == file_hash,
            )
            .order_by(ReportUpload.created_at.asc())
            .limit(1)
        )
        return self.session.scalars(statement).first()

    def create(self, **kwargs: Any) -> ReportUpload:
        row = ReportUpload(**kwargs)
        self.session.add(row)
        self.session.flush()
        return row


class BulkRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_job(
        self,
        *,
        organization_id: UUID,
        external_job_id: str,
        status: str,
        total_items: int,
        processed_items: int,
        successful_items: int,
        failed_items: int,
        settings: dict[str, Any] | None,
        completed_at: datetime | None,
        input_file_id: UUID | None = None,
    ) -> BulkJob:
        existing = self.session.scalars(
            select(BulkJob).where(
                BulkJob.organization_id == organization_id,
                BulkJob.external_job_id == external_job_id,
            )
        ).first()
        if existing is None:
            existing = BulkJob(
                organization_id=organization_id,
                external_job_id=external_job_id,
                status=status,
                total_items=total_items,
                processed_items=processed_items,
                successful_items=successful_items,
                failed_items=failed_items,
                settings=settings,
                completed_at=completed_at,
                input_file_id=input_file_id,
            )
            self.session.add(existing)
        else:
            existing.status = status
            existing.total_items = total_items
            existing.processed_items = processed_items
            existing.successful_items = successful_items
            existing.failed_items = failed_items
            existing.settings = settings
            existing.completed_at = completed_at
            if input_file_id is not None:
                existing.input_file_id = input_file_id
        self.session.flush()
        return existing

    def replace_items(self, job: BulkJob, items: list[dict[str, Any]]) -> None:
        for existing in list(job.items):
            self.session.delete(existing)
        self.session.flush()
        for item in items:
            self.session.add(
                BulkJobItem(
                    bulk_job_id=job.id,
                    asin=item["asin"],
                    status=item["status"],
                    product_snapshot_id=item.get("product_snapshot_id"),
                    listing_analysis=item.get("listing_analysis"),
                    error=item.get("error"),
                )
            )
        self.session.flush()

    def get_by_external_id(self, organization_id: UUID, external_job_id: str) -> BulkJob | None:
        return self.session.scalars(
            select(BulkJob).where(
                BulkJob.organization_id == organization_id,
                BulkJob.external_job_id == external_job_id,
            )
        ).first()


class GeneratedReportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **kwargs: Any) -> GeneratedReport:
        row = GeneratedReport(**kwargs)
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, organization_id: UUID, report_id: UUID) -> GeneratedReport | None:
        return self.session.scalars(
            select(GeneratedReport).where(
                GeneratedReport.organization_id == organization_id,
                GeneratedReport.id == report_id,
            )
        ).first()

    def get_for_bulk_job(self, organization_id: UUID, bulk_job_id: UUID) -> GeneratedReport | None:
        return self.session.scalars(
            select(GeneratedReport)
            .where(
                GeneratedReport.organization_id == organization_id,
                GeneratedReport.bulk_job_id == bulk_job_id,
            )
            .order_by(GeneratedReport.created_at.desc())
        ).first()

    def get_analysis_pdf(
        self,
        organization_id: UUID,
        analysis_run_id: UUID,
        *,
        report_type: str,
        template_version: str,
    ) -> GeneratedReport | None:
        return self.session.scalars(
            select(GeneratedReport)
            .where(
                GeneratedReport.organization_id == organization_id,
                GeneratedReport.analysis_run_id == analysis_run_id,
                GeneratedReport.report_type == report_type,
                GeneratedReport.template_version == template_version,
            )
            .order_by(GeneratedReport.created_at.desc())
        ).first()


class UsageEventRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, **kwargs: Any) -> UsageEvent:
        row = UsageEvent(id=kwargs.pop("id", uuid4()), **kwargs)
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_org(self, organization_id: UUID, limit: int = 50) -> list[UsageEvent]:
        return list(
            self.session.scalars(
                select(UsageEvent)
                .where(UsageEvent.organization_id == organization_id)
                .order_by(UsageEvent.created_at.desc())
                .limit(limit)
            ).all()
        )


class ScoringProfileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_org(self, organization_id: UUID, *, include_archived: bool = False) -> list[ScoringProfile]:
        filters = [ScoringProfile.organization_id == organization_id]
        if not include_archived:
            filters.append(ScoringProfile.archived_at.is_(None))
        statement = (
            select(ScoringProfile)
            .where(*filters)
            .order_by(ScoringProfile.created_at.asc(), ScoringProfile.name.asc())
        )
        return list(self.session.scalars(statement).all())

    def get(self, organization_id: UUID, profile_id: UUID) -> ScoringProfile | None:
        return self.session.scalars(
            select(ScoringProfile).where(
                ScoringProfile.organization_id == organization_id,
                ScoringProfile.id == profile_id,
            )
        ).first()

    def get_default(self, organization_id: UUID) -> ScoringProfile | None:
        return self.session.scalars(
            select(ScoringProfile).where(
                ScoringProfile.organization_id == organization_id,
                ScoringProfile.is_default.is_(True),
                ScoringProfile.archived_at.is_(None),
            )
        ).first()

    def find_active_by_name(self, organization_id: UUID, name: str) -> ScoringProfile | None:
        return self.session.scalars(
            select(ScoringProfile).where(
                ScoringProfile.organization_id == organization_id,
                ScoringProfile.archived_at.is_(None),
                func.lower(ScoringProfile.name) == name.strip().lower(),
            )
        ).first()

    def create(self, row: ScoringProfile) -> ScoringProfile:
        self.session.add(row)
        self.session.flush()
        return row

    def clear_defaults(self, organization_id: UUID, *, except_id: UUID | None = None) -> None:
        rows = self.session.scalars(
            select(ScoringProfile).where(
                ScoringProfile.organization_id == organization_id,
                ScoringProfile.is_default.is_(True),
            )
        ).all()
        for row in rows:
            if except_id is not None and row.id == except_id:
                continue
            row.is_default = False


class CopilotConversationRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        organization_id: UUID,
        title: str | None = None,
        status: str = "active",
    ) -> CopilotConversation:
        now = datetime.now(UTC)
        row = CopilotConversation(
            organization_id=organization_id,
            title=title,
            status=status,
            created_at=now,
            updated_at=now,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, organization_id: UUID, conversation_id: UUID) -> CopilotConversation | None:
        return self.session.scalars(
            select(CopilotConversation).where(
                CopilotConversation.organization_id == organization_id,
                CopilotConversation.id == conversation_id,
            )
        ).first()

    def list_page(
        self,
        organization_id: UUID,
        *,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[CopilotConversation], int]:
        filters = [CopilotConversation.organization_id == organization_id]
        count = self.session.scalar(
            select(func.count()).select_from(CopilotConversation).where(*filters)
        ) or 0
        statement = (
            select(CopilotConversation)
            .where(*filters)
            .order_by(CopilotConversation.updated_at.desc(), CopilotConversation.created_at.desc())
            .offset(offset)
            .limit(limit)
        )
        return list(self.session.scalars(statement).all()), int(count)

    def add_message(
        self,
        *,
        organization_id: UUID,
        conversation: CopilotConversation,
        role: str,
        content: str,
        structured_payload: dict[str, Any] | None = None,
    ) -> CopilotMessage:
        now = datetime.now(UTC)
        message = CopilotMessage(
            conversation_id=conversation.id,
            organization_id=organization_id,
            role=role,
            content=content,
            structured_payload=structured_payload,
            created_at=now,
        )
        conversation.updated_at = now
        self.session.add(message)
        self.session.flush()
        return message

    def list_messages(self, organization_id: UUID, conversation_id: UUID) -> list[CopilotMessage]:
        return list(
            self.session.scalars(
                select(CopilotMessage)
                .where(
                    CopilotMessage.organization_id == organization_id,
                    CopilotMessage.conversation_id == conversation_id,
                )
                .order_by(CopilotMessage.created_at.asc())
            ).all()
        )

    def get_active_pending(
        self, organization_id: UUID, conversation_id: UUID
    ) -> CopilotPendingConfirmation | None:
        return self.session.scalars(
            select(CopilotPendingConfirmation)
            .where(
                CopilotPendingConfirmation.organization_id == organization_id,
                CopilotPendingConfirmation.conversation_id == conversation_id,
                CopilotPendingConfirmation.consumed_at.is_(None),
            )
            .order_by(CopilotPendingConfirmation.created_at.desc())
        ).first()

    def get_plan_payload(
        self, organization_id: UUID, conversation_id: UUID, plan_id: UUID
    ) -> dict[str, Any] | None:
        messages = self.list_messages(organization_id, conversation_id)
        target = str(plan_id)
        for message in reversed(messages):
            payload = message.structured_payload or {}
            if payload.get("type") != "copilot_plan":
                continue
            plan = payload.get("plan")
            if isinstance(plan, dict) and str(plan.get("plan_id")) == target:
                return plan
        return None

    def get_pending_by_nonce(
        self, organization_id: UUID, nonce: str
    ) -> CopilotPendingConfirmation | None:
        return self.session.scalars(
            select(CopilotPendingConfirmation).where(
                CopilotPendingConfirmation.organization_id == organization_id,
                CopilotPendingConfirmation.nonce == nonce,
            )
        ).first()

    def create_pending(
        self,
        *,
        organization_id: UUID,
        conversation: CopilotConversation,
        nonce: str,
        plan_id: UUID,
        plan_schema_version: str | None,
        plan_hash: str,
        summary: str | None,
        expires_at: datetime,
    ) -> CopilotPendingConfirmation:
        now = datetime.now(UTC)
        row = CopilotPendingConfirmation(
            conversation_id=conversation.id,
            organization_id=organization_id,
            nonce=nonce,
            plan_id=plan_id,
            plan_schema_version=plan_schema_version,
            plan_hash=plan_hash,
            summary=summary,
            expires_at=expires_at,
            created_at=now,
        )
        conversation.status = "awaiting_confirmation"
        conversation.updated_at = now
        self.session.add(row)
        self.session.flush()
        return row

    def consume_pending(self, pending: CopilotPendingConfirmation) -> None:
        now = datetime.now(UTC)
        pending.consumed_at = now
        conversation = self.get(pending.organization_id, pending.conversation_id)
        if conversation is not None:
            conversation.status = "active"
            conversation.updated_at = now

    def cancel_active_pendings(
        self,
        organization_id: UUID,
        conversation_id: UUID,
        *,
        except_id: UUID | None = None,
    ) -> None:
        statement = select(CopilotPendingConfirmation).where(
            CopilotPendingConfirmation.organization_id == organization_id,
            CopilotPendingConfirmation.conversation_id == conversation_id,
            CopilotPendingConfirmation.consumed_at.is_(None),
        )
        now = datetime.now(UTC)
        for row in self.session.scalars(statement).all():
            if except_id is not None and row.id == except_id:
                continue
            row.consumed_at = now


class ProfitModelRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, row: ProfitModel) -> ProfitModel:
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, organization_id: UUID, model_id: UUID) -> ProfitModel | None:
        return self.session.scalars(
            select(ProfitModel).where(
                ProfitModel.organization_id == organization_id,
                ProfitModel.id == model_id,
            )
        ).first()

    def find_by_asin(
        self,
        organization_id: UUID,
        asin: str,
        marketplace: str,
    ) -> ProfitModel | None:
        return self.session.scalars(
            select(ProfitModel).where(
                ProfitModel.organization_id == organization_id,
                func.lower(ProfitModel.asin) == asin.lower(),
                ProfitModel.marketplace == marketplace,
            )
        ).first()

    def list_for_org(
        self,
        organization_id: UUID,
        *,
        asin: str | None = None,
    ) -> list[ProfitModel]:
        filters = [ProfitModel.organization_id == organization_id]
        if asin:
            filters.append(func.lower(ProfitModel.asin) == asin.lower())
        statement = (
            select(ProfitModel)
            .where(*filters)
            .order_by(ProfitModel.updated_at.desc(), ProfitModel.created_at.desc())
        )
        return list(self.session.scalars(statement).all())

    def latest_snapshot(self, organization_id: UUID, model_id: UUID) -> ProfitSnapshot | None:
        return self.session.scalars(
            select(ProfitSnapshot)
            .where(
                ProfitSnapshot.organization_id == organization_id,
                ProfitSnapshot.profit_model_id == model_id,
            )
            .order_by(ProfitSnapshot.calculated_at.desc())
            .limit(1)
        ).first()

    def get_snapshot(self, organization_id: UUID, snapshot_id: UUID) -> ProfitSnapshot | None:
        return self.session.scalars(
            select(ProfitSnapshot).where(
                ProfitSnapshot.organization_id == organization_id,
                ProfitSnapshot.id == snapshot_id,
            )
        ).first()

    def add_snapshot(self, row: ProfitSnapshot) -> ProfitSnapshot:
        self.session.add(row)
        self.session.flush()
        return row


class AdvertisingModelRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, row: AdvertisingModel) -> AdvertisingModel:
        self.session.add(row)
        self.session.flush()
        return row

    def get(self, organization_id: UUID, model_id: UUID) -> AdvertisingModel | None:
        return self.session.scalars(
            select(AdvertisingModel).where(
                AdvertisingModel.organization_id == organization_id,
                AdvertisingModel.id == model_id,
            )
        ).first()

    def get_for_profit_model(
        self, organization_id: UUID, profit_model_id: UUID
    ) -> AdvertisingModel | None:
        return self.session.scalars(
            select(AdvertisingModel).where(
                AdvertisingModel.organization_id == organization_id,
                AdvertisingModel.profit_model_id == profit_model_id,
            )
        ).first()

    def latest_snapshot(
        self, organization_id: UUID, advertising_model_id: UUID
    ) -> AdvertisingSnapshot | None:
        return self.session.scalars(
            select(AdvertisingSnapshot)
            .where(
                AdvertisingSnapshot.organization_id == organization_id,
                AdvertisingSnapshot.advertising_model_id == advertising_model_id,
            )
            .order_by(AdvertisingSnapshot.calculated_at.desc())
            .limit(1)
        ).first()

    def list_snapshots(
        self, organization_id: UUID, advertising_model_id: UUID
    ) -> list[AdvertisingSnapshot]:
        return list(
            self.session.scalars(
                select(AdvertisingSnapshot)
                .where(
                    AdvertisingSnapshot.organization_id == organization_id,
                    AdvertisingSnapshot.advertising_model_id == advertising_model_id,
                )
                .order_by(AdvertisingSnapshot.calculated_at.desc())
            ).all()
        )

    def get_snapshot(self, organization_id: UUID, snapshot_id: UUID) -> AdvertisingSnapshot | None:
        return self.session.scalars(
            select(AdvertisingSnapshot).where(
                AdvertisingSnapshot.organization_id == organization_id,
                AdvertisingSnapshot.id == snapshot_id,
            )
        ).first()

    def add_snapshot(self, row: AdvertisingSnapshot) -> AdvertisingSnapshot:
        self.session.add(row)
        self.session.flush()
        return row


class AmazonConnectionRepository:
    """Org-scoped Amazon connection metadata. No secrets, no Amazon API calls."""

    _SECRET_FIELDS = frozenset(
        {
            "refresh_token",
            "access_token",
            "client_secret",
            "client_id",
            "token_reference",
            "authorization_code",
            "spapi_oauth_code",
            "oauth_code",
        }
    )
    _LIFECYCLE_FIELDS = frozenset(
        {
            "region",
            "application_id",
            "status",
            "last_successful_validation_at",
            "last_successful_sync_at",
            "last_error_at",
            "last_error_code",
            "authorized_at",
            "selling_partner_id",
        }
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        organization_id: UUID,
        provider: str,
        environment: str,
        region: str,
        status: str = "not_connected",
        selling_partner_id: str | None = None,
        application_id: str | None = None,
    ) -> AmazonConnection:
        row = AmazonConnection(
            organization_id=organization_id,
            provider=provider,
            environment=environment,
            region=region,
            status=status,
            selling_partner_id=selling_partner_id,
            application_id=application_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get(
        self,
        organization_id: UUID,
        *,
        provider: str = "SP_API",
        environment: str = "SANDBOX",
    ) -> AmazonConnection | None:
        return self.session.scalars(
            select(AmazonConnection).where(
                AmazonConnection.organization_id == organization_id,
                AmazonConnection.provider == provider,
                AmazonConnection.environment == environment,
            )
        ).first()

    def get_by_id(self, organization_id: UUID, connection_id: UUID) -> AmazonConnection | None:
        return self.session.scalars(
            select(AmazonConnection).where(
                AmazonConnection.organization_id == organization_id,
                AmazonConnection.id == connection_id,
            )
        ).first()

    def list_for_org(self, organization_id: UUID) -> list[AmazonConnection]:
        statement: Select[tuple[AmazonConnection]] = (
            select(AmazonConnection)
            .where(AmazonConnection.organization_id == organization_id)
            .order_by(AmazonConnection.created_at.asc(), AmazonConnection.id.asc())
        )
        return list(self.session.scalars(statement).all())

    def update(
        self,
        organization_id: UUID,
        connection_id: UUID,
        **fields: Any,
    ) -> AmazonConnection | None:
        if self._SECRET_FIELDS.intersection(fields):
            raise TypeError("Amazon connection repository cannot store secret fields.")
        unknown = set(fields) - self._LIFECYCLE_FIELDS
        if unknown:
            raise TypeError(f"Unsupported Amazon connection update fields: {sorted(unknown)}")
        row = self.get_by_id(organization_id, connection_id)
        if row is None:
            return None
        for name, value in fields.items():
            setattr(row, name, value)
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def bind_token_reference(
        self,
        organization_id: UUID,
        connection_id: UUID,
        token_reference: str,
    ) -> AmazonConnection | None:
        """Persist an opaque ASI pointer only. Never stores token material."""
        try:
            parsed = parse_asi_amazon_secret_reference(token_reference)
        except InvalidSecretReferenceError as exc:
            raise TypeError("Amazon connection repository cannot store secret fields.") from exc
        if parsed.organization_id.lower() != str(organization_id).lower():
            raise TypeError("Amazon token reference organization does not match.")
        if parsed.connection_id.lower() != str(connection_id).lower():
            raise TypeError("Amazon token reference connection does not match.")
        row = self.get_by_id(organization_id, connection_id)
        if row is None:
            return None
        if parsed.provider != row.provider or parsed.environment != row.environment:
            raise TypeError("Amazon token reference does not match this connection.")
        row.token_reference = parsed.value
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def claim_identity_for_authorization(
        self,
        organization_id: UUID,
        connection_id: UUID,
        *,
        selling_partner_id: str | None,
    ) -> bool:
        """Atomically claim this connection for an OAuth callback attempt.

        This is the sole race-closing step for concurrent callbacks against
        the same connection: it must run, and must be the only thing that can
        change `selling_partner_id`, strictly before any SecretProvider call
        for this attempt. Two concurrent callbacks with different identifiers
        can never both succeed here, because a single `UPDATE ... WHERE ...`
        is atomic with respect to concurrent writers of the same row — the
        database serializes conflicting writes and re-evaluates the WHERE
        clause against the live, just-committed state. This is a basic
        guarantee of any ACID-compliant relational database (SQLite and
        PostgreSQL alike); it does not depend on `SELECT ... FOR UPDATE` or
        any other backend-specific locking feature.

        Returns True if this call may proceed to exchange/store a grant.
        Returns False if another identifier is already on record — the
        caller must not touch SecretProvider at all in that case.

        Raises TypeError for a missing/blank `selling_partner_id`. There is
        no successful-authorization case where this should be called without
        one: a missing identifier must fail closed at the caller, before this
        method is ever reached, not be silently tolerated here. Accepting a
        falsy value that "trivially claims" merely because the connection
        exists is exactly the bypass this method exists to prevent.
        """
        if not (selling_partner_id or "").strip():
            raise TypeError("Amazon connection identity claim requires a non-empty selling_partner_id.")
        statement = (
            update(AmazonConnection)
            .where(
                AmazonConnection.organization_id == organization_id,
                AmazonConnection.id == connection_id,
                or_(
                    AmazonConnection.selling_partner_id.is_(None),
                    AmazonConnection.selling_partner_id == selling_partner_id,
                ),
            )
            .values(selling_partner_id=selling_partner_id, updated_at=datetime.now(UTC))
        )
        outcome = self.session.execute(statement)
        self.session.flush()
        return outcome.rowcount == 1

    def clear_token_reference(
        self,
        organization_id: UUID,
        connection_id: UUID,
    ) -> AmazonConnection | None:
        """Clear the opaque pointer only. Never writes token material."""
        row = self.get_by_id(organization_id, connection_id)
        if row is None:
            return None
        row.token_reference = None
        row.updated_at = datetime.now(UTC)
        self.session.flush()
        return row

    def delete(self, organization_id: UUID, connection_id: UUID) -> bool:
        row = self.get_by_id(organization_id, connection_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.flush()
        return True


class AmazonOAuthStateRepository:
    """Org-scoped hashed OAuth state. Never stores raw state or tokens."""

    _SECRET_FIELDS = frozenset(
        {
            "refresh_token",
            "access_token",
            "client_secret",
            "client_id",
            "token_reference",
            "authorization_code",
            "oauth_code",
            "spapi_oauth_code",
            "state",
        }
    )

    def __init__(self, session: Session) -> None:
        self.session = session

    def _reject_secrets(self, fields: dict[str, Any]) -> None:
        if self._SECRET_FIELDS.intersection(fields):
            raise TypeError("Amazon OAuth state repository cannot store secret fields.")

    def create(
        self,
        *,
        organization_id: UUID,
        provider: str,
        environment: str,
        connection_id: UUID,
        state_hash: str,
        expires_at: datetime,
        amazon_state: str | None = None,
    ) -> AmazonOAuthState:
        self._reject_secrets(
            {
                "organization_id": organization_id,
                "provider": provider,
                "environment": environment,
                "connection_id": connection_id,
                "state_hash": state_hash,
                "expires_at": expires_at,
                "amazon_state": amazon_state,
            }
        )
        digest = state_hash.strip()
        if len(digest) != 64:
            raise TypeError("Amazon OAuth state hash is invalid.")
        connection = self.session.get(AmazonConnection, connection_id)
        if connection is None or connection.organization_id != organization_id:
            raise TypeError("Amazon OAuth state cannot bind a connection from another organization.")
        row = AmazonOAuthState(
            organization_id=organization_id,
            provider=provider,
            environment=environment,
            connection_id=connection_id,
            state_hash=digest,
            amazon_state=amazon_state,
            expires_at=expires_at,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def get_by_hash(self, organization_id: UUID, state_hash: str) -> AmazonOAuthState | None:
        return self.session.scalars(
            select(AmazonOAuthState).where(
                AmazonOAuthState.organization_id == organization_id,
                AmazonOAuthState.state_hash == state_hash,
            )
        ).first()

    def get_usable_by_hash(
        self,
        organization_id: UUID,
        state_hash: str,
        *,
        now: datetime | None = None,
    ) -> AmazonOAuthState | None:
        row = self.get_by_hash(organization_id, state_hash)
        if row is None:
            return None
        moment = now or datetime.now(UTC)
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if row.consumed_at is not None or expires <= moment:
            return None
        return row

    def classify(
        self,
        organization_id: UUID,
        state_hash: str,
        *,
        now: datetime | None = None,
    ) -> tuple[AmazonOAuthState | None, str]:
        """Return (row, missing|expired|consumed|usable). Org-scoped; unknown hash is missing."""
        row = self.get_by_hash(organization_id, state_hash)
        if row is None:
            return None, "missing"
        moment = now or datetime.now(UTC)
        expires = row.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=UTC)
        if row.consumed_at is not None:
            return row, "consumed"
        if expires <= moment:
            return row, "expired"
        return row, "usable"

    def consume(
        self,
        organization_id: UUID,
        state_id: UUID,
        *,
        now: datetime | None = None,
    ) -> AmazonOAuthState | None:
        row = self.get_by_id(organization_id, state_id)
        if row is None or row.consumed_at is not None:
            return None
        row.consumed_at = now or datetime.now(UTC)
        self.session.flush()
        return row

    def get_by_id(self, organization_id: UUID, state_id: UUID) -> AmazonOAuthState | None:
        return self.session.scalars(
            select(AmazonOAuthState).where(
                AmazonOAuthState.organization_id == organization_id,
                AmazonOAuthState.id == state_id,
            )
        ).first()

    def list_for_org(self, organization_id: UUID) -> list[AmazonOAuthState]:
        statement: Select[tuple[AmazonOAuthState]] = (
            select(AmazonOAuthState)
            .where(AmazonOAuthState.organization_id == organization_id)
            .order_by(AmazonOAuthState.created_at.asc(), AmazonOAuthState.id.asc())
        )
        return list(self.session.scalars(statement).all())


class SellerAccountOwnershipConflict(Exception):
    """A `selling_partner_id` is already owned by a different organization.

    12B.2A V1 rule: one canonical `selling_partner_id` belongs to exactly one
    organization. Never carries the owning organization's id — callers must
    not disclose it.
    """


class AmazonSellerAccountRepository:
    """Org-scoped canonical Amazon seller accounts. 12B.2A schema foundation.

    One organization may own multiple seller accounts. One `selling_partner_id`
    may be owned by only one organization (V1). Never stores tokens or
    `token_reference`; those remain on `amazon_connections`.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_reconcile(
        self,
        *,
        organization_id: UUID,
        selling_partner_id: str,
        display_store_name: str | None = None,
    ) -> AmazonSellerAccount:
        """Reconcile an existing account for this org, or create a new one.

        Raises SellerAccountOwnershipConflict without disclosing the owning
        organization if `selling_partner_id` already belongs to another org.
        """
        spid = (selling_partner_id or "").strip()
        if not spid:
            raise TypeError("Amazon seller account requires a non-empty selling_partner_id.")
        existing = self.session.scalars(
            select(AmazonSellerAccount).where(AmazonSellerAccount.selling_partner_id == spid)
        ).first()
        if existing is not None:
            if existing.organization_id != organization_id:
                raise SellerAccountOwnershipConflict(
                    "This Amazon seller account is already connected to another organization."
                )
            if display_store_name:
                existing.display_store_name = display_store_name
            existing.last_seen_at = datetime.now(UTC)
            self.session.flush()
            return existing
        row = AmazonSellerAccount(
            organization_id=organization_id,
            selling_partner_id=spid,
            display_store_name=display_store_name,
        )
        self.session.add(row)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise SellerAccountOwnershipConflict(
                "This Amazon seller account is already connected to another organization."
            ) from exc
        return row

    def get_by_id(self, organization_id: UUID, seller_account_id: UUID) -> AmazonSellerAccount | None:
        return self.session.scalars(
            select(AmazonSellerAccount).where(
                AmazonSellerAccount.organization_id == organization_id,
                AmazonSellerAccount.id == seller_account_id,
            )
        ).first()

    def get_by_selling_partner_id(
        self, organization_id: UUID, selling_partner_id: str
    ) -> AmazonSellerAccount | None:
        spid = (selling_partner_id or "").strip()
        if not spid:
            return None
        return self.session.scalars(
            select(AmazonSellerAccount).where(
                AmazonSellerAccount.organization_id == organization_id,
                AmazonSellerAccount.selling_partner_id == spid,
            )
        ).first()

    def list_for_org(self, organization_id: UUID) -> list[AmazonSellerAccount]:
        statement: Select[tuple[AmazonSellerAccount]] = (
            select(AmazonSellerAccount)
            .where(AmazonSellerAccount.organization_id == organization_id)
            .order_by(AmazonSellerAccount.created_at.asc(), AmazonSellerAccount.id.asc())
        )
        return list(self.session.scalars(statement).all())


class AmazonMarketplaceParticipationRepository:
    """Org-scoped marketplace participation rows. Marketplace id is canonical identity.

    Display domain (e.g. `amazon.com`) is never used as identity or uniqueness.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def create_or_reconcile(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_id: str,
        region: str,
        connection_id: UUID | None = None,
        name: str | None = None,
        country_code: str | None = None,
        default_currency_code: str | None = None,
        default_language_code: str | None = None,
        domain_name: str | None = None,
        store_name: str | None = None,
        is_participating: bool = True,
        has_suspended_listings: bool = False,
    ) -> AmazonMarketplaceParticipation:
        seller_account = self.session.get(AmazonSellerAccount, seller_account_id)
        if seller_account is None or seller_account.organization_id != organization_id:
            raise TypeError(
                "Amazon marketplace participation cannot bind a seller account from another organization."
            )
        if connection_id is not None:
            connection = self.session.get(AmazonConnection, connection_id)
            if connection is None or connection.organization_id != organization_id:
                raise TypeError(
                    "Amazon marketplace participation cannot bind a connection from another organization."
                )
        existing = self.session.scalars(
            select(AmazonMarketplaceParticipation).where(
                AmazonMarketplaceParticipation.seller_account_id == seller_account_id,
                AmazonMarketplaceParticipation.marketplace_id == marketplace_id,
            )
        ).first()
        now = datetime.now(UTC)
        if existing is not None:
            existing.name = name or existing.name
            existing.country_code = country_code or existing.country_code
            existing.default_currency_code = default_currency_code or existing.default_currency_code
            existing.default_language_code = default_language_code or existing.default_language_code
            existing.domain_name = domain_name or existing.domain_name
            existing.store_name = store_name or existing.store_name
            existing.region = region
            existing.is_participating = is_participating
            existing.has_suspended_listings = has_suspended_listings
            existing.is_active = True
            existing.last_seen_at = now
            if connection_id is not None:
                existing.connection_id = connection_id
            self.session.flush()
            return existing
        row = AmazonMarketplaceParticipation(
            organization_id=organization_id,
            seller_account_id=seller_account_id,
            connection_id=connection_id,
            marketplace_id=marketplace_id,
            name=name,
            country_code=country_code,
            default_currency_code=default_currency_code,
            default_language_code=default_language_code,
            domain_name=domain_name,
            region=region,
            is_participating=is_participating,
            has_suspended_listings=has_suspended_listings,
            store_name=store_name,
            is_active=True,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_seller_account(
        self, organization_id: UUID, seller_account_id: UUID
    ) -> list[AmazonMarketplaceParticipation]:
        statement: Select[tuple[AmazonMarketplaceParticipation]] = (
            select(AmazonMarketplaceParticipation)
            .where(
                AmazonMarketplaceParticipation.organization_id == organization_id,
                AmazonMarketplaceParticipation.seller_account_id == seller_account_id,
            )
            .order_by(AmazonMarketplaceParticipation.marketplace_id.asc())
        )
        return list(self.session.scalars(statement).all())

    def deactivate_missing(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        seen_marketplace_ids: set[str],
    ) -> int:
        """Mark rows absent from the latest complete snapshot as inactive.

        Only call this after a successful, complete reconciliation pass for
        this seller account — never after a malformed, partial, or failed
        response, since absence there is not evidence the seller actually
        left that marketplace. `is_active` tracks presence in the most
        recent complete snapshot; it is independent of Amazon's own
        `is_participating` flag, which is preserved as-is on every row.
        """
        rows = self.session.scalars(
            select(AmazonMarketplaceParticipation).where(
                AmazonMarketplaceParticipation.organization_id == organization_id,
                AmazonMarketplaceParticipation.seller_account_id == seller_account_id,
                AmazonMarketplaceParticipation.is_active.is_(True),
            )
        ).all()
        now = datetime.now(UTC)
        deactivated = 0
        for row in rows:
            if row.marketplace_id not in seen_marketplace_ids:
                row.is_active = False
                row.last_seen_at = now
                deactivated += 1
        if deactivated:
            self.session.flush()
        return deactivated


class AmazonIngestionRunRepository:
    """Org- and seller-account-scoped ingestion-run lifecycle records.

    Foundation only. Creating rows here does not perform SP-API ingestion.
    """

    _VALID_STATUSES = frozenset({"started", "succeeded", "partial", "failed", "timed_out"})

    def __init__(self, session: Session) -> None:
        self.session = session

    def start(
        self,
        *,
        organization_id: UUID,
        domain: str,
        region: str,
        environment: str,
        connection_id: UUID | None = None,
        seller_account_id: UUID | None = None,
        request_correlation_id: str | None = None,
    ) -> AmazonIngestionRun:
        if seller_account_id is not None:
            seller_account = self.session.get(AmazonSellerAccount, seller_account_id)
            if seller_account is None or seller_account.organization_id != organization_id:
                raise TypeError(
                    "Amazon ingestion run cannot bind a seller account from another organization."
                )
        if connection_id is not None:
            connection = self.session.get(AmazonConnection, connection_id)
            if connection is None or connection.organization_id != organization_id:
                raise TypeError("Amazon ingestion run cannot bind a connection from another organization.")
        row = AmazonIngestionRun(
            organization_id=organization_id,
            connection_id=connection_id,
            seller_account_id=seller_account_id,
            domain=domain,
            region=region,
            environment=environment,
            status="started",
            request_correlation_id=request_correlation_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def complete(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        status: str,
        records_received: int = 0,
        records_accepted: int = 0,
        records_rejected: int = 0,
        retry_count: int = 0,
        failure_class: str | None = None,
        pagination_complete: bool = True,
    ) -> AmazonIngestionRun | None:
        if status not in self._VALID_STATUSES:
            raise TypeError(f"Unsupported Amazon ingestion run status: {status!r}")
        row = self.get_by_id(organization_id, run_id)
        if row is None:
            return None
        row.status = status
        row.completed_at = datetime.now(UTC)
        row.records_received = records_received
        row.records_accepted = records_accepted
        row.records_rejected = records_rejected
        row.retry_count = retry_count
        row.failure_class = failure_class
        row.pagination_complete = pagination_complete
        self.session.flush()
        return row

    def get_by_id(self, organization_id: UUID, run_id: UUID) -> AmazonIngestionRun | None:
        return self.session.scalars(
            select(AmazonIngestionRun).where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.id == run_id,
            )
        ).first()

    def list_for_org(self, organization_id: UUID) -> list[AmazonIngestionRun]:
        statement: Select[tuple[AmazonIngestionRun]] = (
            select(AmazonIngestionRun)
            .where(AmazonIngestionRun.organization_id == organization_id)
            .order_by(AmazonIngestionRun.started_at.asc(), AmazonIngestionRun.id.asc())
        )
        return list(self.session.scalars(statement).all())

    def list_for_connection(
        self, organization_id: UUID, connection_id: UUID
    ) -> list[AmazonIngestionRun]:
        statement: Select[tuple[AmazonIngestionRun]] = (
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.connection_id == connection_id,
            )
            .order_by(AmazonIngestionRun.started_at.desc(), AmazonIngestionRun.id.desc())
        )
        return list(self.session.scalars(statement).all())


def file_sha256(data: bytes) -> str:
    return sha256_bytes(data)

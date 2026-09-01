from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Select, and_, case, func, or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, selectinload

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
    AmazonSellerListing,
)


def ensure_organization_id(session: Session) -> UUID:
    return get_settings().default_organization_id


# 12B.3E — LIKE/ILIKE wildcard escaping for user-entered search terms.
# `%` and `_` are SQL wildcards; a literal search for e.g. "SKU_100" or a
# 10%-off promo SKU containing "%" must not silently widen into a
# catalog-wide match. `_LIKE_ESCAPE_CHAR` must itself be escaped first, or
# an escape character already present in the user's term would be
# misread as introducing an escape sequence instead of matching itself.
_LIKE_ESCAPE_CHAR = "\\"


def _escape_like_term(term: str, escape_char: str = _LIKE_ESCAPE_CHAR) -> str:
    return term.replace(escape_char, escape_char * 2).replace("%", escape_char + "%").replace("_", escape_char + "_")


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
        try:
            # A SAVEPOINT (`begin_nested()`), not a full `session.rollback()`.
            # On PostgreSQL, an uncaught error inside a transaction leaves
            # the *whole* transaction aborted — no further statement can run
            # on it until a rollback. A savepoint isolates that failure to
            # just this attempted INSERT: on IntegrityError, SQLAlchemy
            # issues `ROLLBACK TO SAVEPOINT`, undoing only this insert and
            # leaving the outer transaction (and anything the caller already
            # did on this same session before calling this method) fully
            # intact and usable for the re-read and reconciliation below.
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError as exc:
            # The unique-constraint loser here could be this same
            # organization's own concurrent reconciliation attempt racing
            # itself (benign — the two initial SELECTs above both saw no
            # row yet), not necessarily a different organization. Re-read
            # the now-committed winner and classify by its actual owner
            # rather than assuming every insert failure is a genuine
            # cross-organization conflict.
            winner = self.session.scalars(
                select(AmazonSellerAccount).where(AmazonSellerAccount.selling_partner_id == spid)
            ).first()
            if winner is None or winner.organization_id != organization_id:
                raise SellerAccountOwnershipConflict(
                    "This Amazon seller account is already connected to another organization."
                ) from exc
            if display_store_name:
                winner.display_store_name = display_store_name
            winner.last_seen_at = datetime.now(UTC)
            self.session.flush()
            return winner
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
        try:
            # SAVEPOINT, not a full rollback — see AmazonSellerAccountRepository
            # .create_or_reconcile for why: isolates a failed INSERT without
            # aborting the outer transaction this method's caller (the
            # reconciliation service, upserting many participations in one
            # transaction) is still in the middle of.
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            # Same principle as the seller-account race: the loser of this
            # unique-constraint race (seller_account_id, marketplace_id) is
            # necessarily this same seller account's own concurrent
            # reconciliation attempt — participations are never shared
            # across organizations — so reconcile into the winner instead
            # of surfacing a spurious failure.
            winner = self.session.scalars(
                select(AmazonMarketplaceParticipation).where(
                    AmazonMarketplaceParticipation.seller_account_id == seller_account_id,
                    AmazonMarketplaceParticipation.marketplace_id == marketplace_id,
                )
            ).one()
            winner.name = name or winner.name
            winner.country_code = country_code or winner.country_code
            winner.default_currency_code = default_currency_code or winner.default_currency_code
            winner.default_language_code = default_language_code or winner.default_language_code
            winner.domain_name = domain_name or winner.domain_name
            winner.store_name = store_name or winner.store_name
            winner.region = region
            winner.is_participating = is_participating
            winner.has_suspended_listings = has_suspended_listings
            winner.is_active = True
            winner.last_seen_at = now
            if connection_id is not None:
                winner.connection_id = connection_id
            self.session.flush()
            return winner
        return row

    def get_by_id(
        self, organization_id: UUID, participation_id: UUID
    ) -> AmazonMarketplaceParticipation | None:
        return self.session.scalars(
            select(AmazonMarketplaceParticipation).where(
                AmazonMarketplaceParticipation.organization_id == organization_id,
                AmazonMarketplaceParticipation.id == participation_id,
            )
        ).first()

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


@dataclass(frozen=True)
class ListingsRunClaim:
    """Outcome of an atomic listings-run claim attempt. Never carries a
    lease owner or any identifier beyond what the caller already supplied."""

    claimed: bool
    run_id: UUID | None = None
    reason: str | None = None  # "already_running" when claimed is False


class AmazonIngestionRunRepository:
    """Org- and seller-account-scoped ingestion-run lifecycle records.

    Foundation only. Creating rows here does not perform SP-API ingestion.
    """

    _VALID_STATUSES = frozenset({"started", "succeeded", "partial", "failed", "timed_out"})

    def __init__(self, session: Session) -> None:
        self.session = session

    def _lease_expiry_value(self, lease_duration_seconds: int):
        """The future `lease_expires_at` value written at claim time or at
        heartbeat renewal.

        On PostgreSQL this is computed entirely from the *database's* own
        clock — `func.now(type_=DateTime) + timedelta(...)` compiles to
        `now() + make_interval(secs=>...)` — so the value actually written
        shares the same time authority as every expiry/staleness
        comparison in this class (`func.now()`): lease creation, renewal,
        expiry comparison, and stale detection all read from one coherent
        database clock, with no dependency on the calling application
        worker's own clock. Verified directly: this expression executes
        correctly against PostgreSQL but raises at *execution* time against
        SQLite (confirmed empirically, not assumed — SQLite has no
        portable `DATETIME + INTERVAL` construct for SQLAlchemy to compile
        here).

        SQLite is used only by this project's local/CI unit tests (never
        production), so those tests fall back to an approximate
        Python-computed value. That fallback is test-only scaffolding and
        must never be read as evidence of the production invariant above —
        the disposable-PostgreSQL tests exercise the real database-time
        expression directly.
        """
        if self.session.get_bind().dialect.name == "postgresql":
            return func.now(type_=DateTime) + timedelta(seconds=lease_duration_seconds)
        return datetime.now(UTC) + timedelta(seconds=lease_duration_seconds)

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
            # Org-scoped lookup, not a Python-level equality against a
            # separately-loaded row: the WHERE clause enforces ownership in
            # SQL itself, so this can never be defeated by a stale identity-
            # map entry or by comparing values of differing representations.
            seller_account = AmazonSellerAccountRepository(self.session).get_by_id(
                organization_id, seller_account_id
            )
            if seller_account is None:
                raise TypeError(
                    "Amazon ingestion run cannot bind a seller account from another organization."
                )
        if connection_id is not None:
            connection = AmazonConnectionRepository(self.session).get_by_id(organization_id, connection_id)
            if connection is None:
                raise TypeError("Amazon ingestion run cannot bind a connection from another organization.")
        row = AmazonIngestionRun(
            organization_id=organization_id,
            connection_id=connection_id,
            seller_account_id=seller_account_id,
            domain=domain,
            region=region,
            environment=environment,
            status="started",
            # Explicit since 12B.3G: `started_at` lost its `server_default`
            # when it became nullable for the Listings job lifecycle's
            # `queued` state (which this run_type does not have — a
            # `marketplace_participations` run is always immediately
            # "started", exactly as before).
            started_at=func.now(),
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
        pages_fetched: int = 0,
        reported_total_results: int | None = None,
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
        row.pages_fetched = pages_fetched
        row.reported_total_results = reported_total_results
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

    def get_latest_listings_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        """The single most recent `run_type='listings'` run for this
        organization-scoped participation, regardless of status — 12B.3E
        read-side evidence. Read-only: never claims, heartbeats, or
        completes anything. Deliberately excludes every other `run_type`
        (e.g. `marketplace_participations`) so a caller can never mistake
        an unrelated synchronization for Listings synchronization
        evidence."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
            )
            .order_by(AmazonIngestionRun.started_at.desc(), AmazonIngestionRun.id.desc())
            .limit(1)
        ).first()

    def get_latest_successful_listings_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        """The most recent *succeeded* `run_type='listings'` run — may be
        an earlier run than `get_latest_listings_run` if the most recent
        attempt failed. Used only for "last known-good data" evidence, not
        for the current run's own status."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status == "succeeded",
            )
            .order_by(AmazonIngestionRun.started_at.desc(), AmazonIngestionRun.id.desc())
            .limit(1)
        ).first()

    def claim_listings_run(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
        region: str,
        environment: str,
        connection_id: UUID | None,
        lease_owner: str,
        lease_duration_seconds: int,
    ) -> ListingsRunClaim:
        """Atomically claims the single-writer listings-ingestion scope for
        `(seller_account_id, marketplace_participation_id)`.

        Two statements, one transaction (whatever transaction the caller's
        session is already in — this method never commits):

        1. A conditional `UPDATE` terminalizes any *expired* `'started'`
           listings run for this exact scope (`status='timed_out'`) before
           attempting to claim. This is the stale-recovery step, and it is
           safe under concurrency without any explicit locking: a second,
           concurrent transaction attempting the same `UPDATE` on the same
           row blocks on Postgres's normal row-level write lock until the
           first commits, then re-evaluates its own `WHERE status=
           'started'` against the now-committed data — which no longer
           matches, so only one transaction ever actually performs the
           reclaim.
        2. An `INSERT` of the new `'started'` row, wrapped in a SAVEPOINT
           (`begin_nested()`) so a collision — either an unexpired run
           genuinely still active, or a concurrent claimant that already
           committed a replacement for the scope this same stale row just
           vacated — surfaces as a plain `ListingsRunClaim(claimed=False,
           reason="already_running")`, isolated from the outer transaction
           by the savepoint exactly like `AmazonSellerAccountRepository.
           create_or_reconcile`'s pattern.

        Raises `TypeError` for a missing/cross-organization seller account,
        marketplace participation, participation-does-not-belong-to-this-
        seller-account, or cross-organization connection — the same
        fail-closed, no-foreign-identifier-disclosed pattern already used by
        `start()` and the other repositories in this module.
        """
        seller_account = AmazonSellerAccountRepository(self.session).get_by_id(
            organization_id, seller_account_id
        )
        if seller_account is None:
            raise TypeError(
                "Amazon listings run cannot bind a seller account from another organization."
            )
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon listings run cannot bind a marketplace participation from another organization."
            )
        if participation.seller_account_id != seller_account_id:
            raise TypeError(
                "Amazon listings run marketplace participation does not belong to the given seller account."
            )
        if connection_id is not None:
            connection = AmazonConnectionRepository(self.session).get_by_id(organization_id, connection_id)
            if connection is None:
                raise TypeError("Amazon listings run cannot bind a connection from another organization.")

        # Database time, not `datetime.now(UTC)`, for the staleness
        # comparison: comparing a row's `lease_expires_at` against the
        # DATABASE server's clock means every worker agrees on the same
        # authoritative "now" regardless of its own local clock skew. The
        # NEW row's `lease_expires_at` a few lines below is likewise
        # computed from database time on PostgreSQL via
        # `_lease_expiry_value` — see that method's docstring for why the
        # value written and the value later compared against share one
        # coherent clock authority.
        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at.is_not(None),
                AmazonIngestionRun.lease_expires_at < func.now(),
            )
            .values(
                status="timed_out",
                completed_at=func.now(),
                failure_class="lease_expired",
                lease_owner=None,
                # Truthful terminal record: an abandoned run never actually
                # reached a confirmed natural end of pagination, regardless
                # of what the column's insert-time default says.
                pagination_complete=False,
            )
        )
        self.session.flush()

        row = AmazonIngestionRun(
            organization_id=organization_id,
            connection_id=connection_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=marketplace_participation_id,
            run_type="listings",
            domain="listings_items",
            status="started",
            region=region,
            environment=environment,
            lease_owner=lease_owner,
            lease_expires_at=self._lease_expiry_value(lease_duration_seconds),
            # Explicit since 12B.3G: `started_at` lost its `server_default`
            # for the new `queued` state. This method claims a run that is
            # immediately `started` (the pre-12B.3G synchronous path,
            # still used by `AmazonListingsIngestionService.sync()`), so it
            # always has a real start time, exactly as before.
            started_at=func.now(),
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            return ListingsRunClaim(claimed=False, reason="already_running")
        return ListingsRunClaim(claimed=True, run_id=row.id)

    def heartbeat_listings_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        lease_duration_seconds: int,
        pages_fetched: int,
    ) -> bool:
        """Extends the lease and records progress for an in-flight listings
        run. The `WHERE lease_owner = :lease_owner AND status = 'started'
        AND lease_expires_at > now()` clause makes this an atomic
        compare-and-set: it only succeeds if this exact caller still holds
        the claim, the run is still active, AND the lease has not already
        expired — merely matching on `lease_owner` is not enough, since a
        worker whose lease genuinely expired must fail closed even before
        any replacement worker has reclaimed the scope (nothing else would
        otherwise ever notice the expiry until a *new* claim attempt runs
        the stale-reclaim step in `claim_listings_run`). Uses the
        database's own clock for that comparison, not the calling worker's,
        so this never depends on clock agreement between workers. The
        renewed `lease_expires_at` written below shares that same
        authority on PostgreSQL — see `_lease_expiry_value`. Returns
        False if the lease was stolen OR has simply expired — the caller
        must treat either as a lost claim and stop, never continue writing
        pages under an ownership it no longer holds.
        """
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.lease_owner == lease_owner,
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at > func.now(),
            )
            .values(
                lease_expires_at=self._lease_expiry_value(lease_duration_seconds),
                pages_fetched=pages_fetched,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    def complete_listings_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        status: str,
        records_received: int = 0,
        records_accepted: int = 0,
        records_rejected: int = 0,
        pages_fetched: int = 0,
        reported_total_results: int | None = None,
        pagination_complete: bool = True,
        failure_class: str | None = None,
    ) -> bool:
        """Atomic, lease-owner-gated completion. Like `heartbeat_listings_
        run`, the `WHERE lease_owner = :lease_owner AND status = 'started'
        AND lease_expires_at > now()` clause is a compare-and-set: returns
        False (and writes nothing) if this caller no longer holds the
        claim — because it was stolen, or simply because its lease already
        expired, even if nothing has reclaimed the scope yet — so a caller
        that lost its lease mid-fetch can never overwrite whatever a
        reclaiming process has since recorded, and an expired-but-not-yet-
        reclaimed worker cannot silently finalize a run it no longer has
        exclusive rights to. Uses the database's own clock, not the calling
        worker's. Clears `lease_owner`/`lease_expires_at` on success — the
        scope is released the moment `status` leaves `'started'`, which is
        exactly what the partial unique index already keys on; clearing the
        lease fields is cleanliness for future observability, not a
        separate unlocking step.
        """
        if status not in self._VALID_STATUSES:
            raise TypeError(f"Unsupported Amazon ingestion run status: {status!r}")
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.lease_owner == lease_owner,
                # Without this, a caller whose lease was already stolen and
                # reclaimed by a stale-recovery step (which terminalizes
                # status but historically left lease_owner unchanged on the
                # vacated row) could still match on lease_owner alone and
                # re-complete an already-terminal row. Matching
                # `heartbeat_listings_run`'s same guard closes that gap.
                AmazonIngestionRun.status == "started",
                # And even if nothing has reclaimed it yet, an already-
                # expired lease must not be treated as still valid.
                AmazonIngestionRun.lease_expires_at > func.now(),
            )
            .values(
                status=status,
                completed_at=func.now(),
                records_received=records_received,
                records_accepted=records_accepted,
                records_rejected=records_rejected,
                pages_fetched=pages_fetched,
                reported_total_results=reported_total_results,
                pagination_complete=pagination_complete,
                failure_class=failure_class,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    # --- 12B.3G: durable job lifecycle (queued / waiting_to_retry) ---------

    def enqueue_listings_run(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
        region: str,
        environment: str,
        connection_id: UUID | None,
    ) -> ListingsRunClaim:
        """Creates a durable `status='queued'` Listings job — no lease, no
        `started_at`, no Amazon call. A separate worker process claims it
        later via `claim_next_listings_job`.

        Ownership validation and the stale-`started`-lease reclaim step are
        identical to `claim_listings_run`; only the terminal state of a
        *successful* claim differs (`queued`, not `started`). Protected by
        the same partial unique index (now covering queued/started/
        waiting_to_retry), so a concurrent enqueue/claim for the same scope
        fails the same way: `ListingsRunClaim(claimed=False,
        reason="already_running")`.
        """
        seller_account = AmazonSellerAccountRepository(self.session).get_by_id(
            organization_id, seller_account_id
        )
        if seller_account is None:
            raise TypeError(
                "Amazon listings run cannot bind a seller account from another organization."
            )
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon listings run cannot bind a marketplace participation from another organization."
            )
        if participation.seller_account_id != seller_account_id:
            raise TypeError(
                "Amazon listings run marketplace participation does not belong to the given seller account."
            )
        if connection_id is not None:
            connection = AmazonConnectionRepository(self.session).get_by_id(organization_id, connection_id)
            if connection is None:
                raise TypeError("Amazon listings run cannot bind a connection from another organization.")

        # Same stale-`started`-lease reclaim as claim_listings_run — a
        # `queued`/`waiting_to_retry` row is never "stale" in this sense
        # (neither holds a lease), so this predicate is intentionally
        # unchanged: only an abandoned `started` row is reclaimed here.
        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at.is_not(None),
                AmazonIngestionRun.lease_expires_at < func.now(),
            )
            .values(
                status="timed_out",
                completed_at=func.now(),
                failure_class="lease_expired",
                lease_owner=None,
                pagination_complete=False,
            )
        )
        self.session.flush()

        row = AmazonIngestionRun(
            organization_id=organization_id,
            connection_id=connection_id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=marketplace_participation_id,
            run_type="listings",
            domain="listings_items",
            status="queued",
            region=region,
            environment=environment,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            return ListingsRunClaim(claimed=False, reason="already_running")
        return ListingsRunClaim(claimed=True, run_id=row.id)

    def get_active_listings_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        """The current nonterminal (`queued`/`started`/`waiting_to_retry`)
        Listings run for this organization-owned participation, if any.
        Used to surface sanitized evidence in an `already_running`
        response — never raises for a missing/foreign participation
        (callers that need that check already performed it beforehand)."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status.in_(("queued", "started", "waiting_to_retry")),
            )
            .order_by(AmazonIngestionRun.created_at.desc())
            .limit(1)
        ).first()

    # Reserved solely for `claim_next_listings_job`'s claim-decision
    # critical section below. Must never be reused by any other
    # `pg_advisory_xact_lock` call anywhere in this codebase — a
    # collision would silently and incorrectly serialize two unrelated
    # features against each other.
    _LISTINGS_CLAIM_ADVISORY_LOCK_KEY = 847_539_201_663

    def claim_next_listings_job(
        self,
        *,
        lease_owner: str,
        lease_duration_seconds: int,
        max_global_active: int,
        max_active_per_organization: int,
    ) -> AmazonIngestionRun | None:
        """Worker-side claim: atomically picks at most one eligible
        Listings job (`queued`, or `waiting_to_retry` whose `next_retry_at`
        has passed) across *every* organization, subject to global and
        per-organization concurrency limits, and transitions it to
        `started`.

        Claims exactly one row with a single `UPDATE ... WHERE id = (SELECT
        ... FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *` statement — the
        candidate subquery locks at most the one row it will actually
        claim, never a wider batch. An earlier version selected up to a
        batch of candidates with `FOR UPDATE SKIP LOCKED` and iterated them
        in Python to find one under the concurrency limits; that held row
        locks on *every* candidate it merely inspected, not just the one it
        claimed, so concurrent workers raced past those unnecessarily
        locked rows via `SKIP LOCKED` and could come back with nothing even
        though eligible jobs genuinely existed — proven directly by
        `tests/postgres/test_disposable_postgres_listings_job_lifecycle_
        concurrency.py::test_claim_next_listings_job_does_not_hold_locks_
        on_unclaimed_candidates`, and originally surfaced by five
        concurrent single-shot claims against five eligible jobs returning
        as few as two successes. This version never touches — let alone
        locks — any row besides the one it may claim.

        On PostgreSQL, the whole decision (stale-reclaim + capacity check +
        candidate pick + claim) additionally runs inside a single
        transaction-scoped advisory lock (`pg_advisory_xact_lock`, released
        automatically at commit or rollback — crash-safe, no explicit
        unlock needed, and immune to a worker crashing mid-claim leaving it
        held). Without this, the global/per-organization capacity checks
        below are plain, non-locking `COUNT` reads: two concurrent workers
        could each read the *same* pre-claim count before either commits,
        both pass the same capacity gate, and each then claim a
        *different* eligible row via `SKIP LOCKED` — correct with respect
        to each other (never a double-claim), but together they could
        exceed the configured cap, since neither one's read ever saw the
        other's write. The advisory lock closes that gap by fully
        serializing the decision step itself — never actual job execution,
        heartbeat renewal, or completion, all of which happen in later,
        independent transactions after this one has already committed and
        released the lock. This trades a small amount of decision-time
        throughput (claim *decisions* across different organizations are
        serialized against each other; job *execution* is not) for a
        capacity guarantee that is trivial to prove from a single lock,
        rather than relying on `SERIALIZABLE` isolation's optimistic
        abort-and-retry behavior, whose retry budget is hard to bound
        under genuinely N-way concurrent contention on one small table. A
        single global key is used rather than one key per organization:
        which organization a call will end up claiming for is only known
        *after* the candidate is already picked, so a "peek the
        organization, take its lock, then re-verify" two-phase scheme
        would add real complexity for a decision step whose actual cost is
        a handful of index lookups. Only this one advisory-lock key is
        ever acquired by this method, and never nested with another, so it
        cannot deadlock against itself or anything else —
        `test_many_queued_jobs_are_distributed_one_per_worker_with_no_
        double_claim` and `test_more_workers_than_jobs_yields_one_success_
        per_job_and_none_for_the_rest` both exercise this directly at
        5-way concurrency with a bounded thread-join timeout, which would
        hang instead of completing if any deadlock were possible.
        `pg_advisory_xact_lock` is a no-op on SQLite (no such function
        there, and no concurrent writers to protect against in that
        dialect's own test suite), matching how `FOR UPDATE SKIP LOCKED`
        is already treated below.

        `started_at` is set only on the *first* claim (`COALESCE`-style:
        left untouched if already set from an earlier attempt);
        `retry_count` increments only when reclaiming from
        `waiting_to_retry` (a genuine retry), never on a fresh `queued`
        claim. Stale `started` rows (lease expired, no worker ever
        finished them) are reclaimed to `timed_out` first, exactly like
        `claim_listings_run`'s own stale-reclaim step, so their scope is
        freed before any new candidate is considered.
        """
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": self._LISTINGS_CLAIM_ADVISORY_LOCK_KEY},
            )

        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at.is_not(None),
                AmazonIngestionRun.lease_expires_at < func.now(),
            )
            .values(
                status="timed_out",
                completed_at=func.now(),
                failure_class="lease_expired",
                lease_owner=None,
                pagination_complete=False,
            )
        )
        self.session.flush()

        _Global = aliased(AmazonIngestionRun)
        global_active_count = (
            select(func.count())
            .select_from(_Global)
            .where(_Global.run_type == "listings", _Global.status == "started")
            .scalar_subquery()
        )
        _Org = aliased(AmazonIngestionRun)
        org_active_count = (
            select(func.count())
            .select_from(_Org)
            .where(
                _Org.run_type == "listings",
                _Org.status == "started",
                _Org.organization_id == AmazonIngestionRun.organization_id,
            )
            .scalar_subquery()
        )
        candidate_id = (
            select(AmazonIngestionRun.id)
            .where(
                AmazonIngestionRun.run_type == "listings",
                or_(
                    AmazonIngestionRun.status == "queued",
                    and_(
                        AmazonIngestionRun.status == "waiting_to_retry",
                        AmazonIngestionRun.next_retry_at.is_not(None),
                        AmazonIngestionRun.next_retry_at <= func.now(),
                    ),
                ),
                global_active_count < max_global_active,
                org_active_count < max_active_per_organization,
            )
            .order_by(
                func.coalesce(AmazonIngestionRun.next_retry_at, AmazonIngestionRun.created_at).asc(),
                AmazonIngestionRun.id.asc(),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
            .scalar_subquery()
        )

        claimed = self.session.execute(
            update(AmazonIngestionRun)
            .where(AmazonIngestionRun.id == candidate_id)
            .values(
                status="started",
                lease_owner=lease_owner,
                lease_expires_at=self._lease_expiry_value(lease_duration_seconds),
                last_heartbeat_at=func.now(),
                next_retry_at=None,
                started_at=func.coalesce(AmazonIngestionRun.started_at, func.now()),
                retry_count=case(
                    (AmazonIngestionRun.status == "waiting_to_retry", AmazonIngestionRun.retry_count + 1),
                    else_=AmazonIngestionRun.retry_count,
                ),
            )
            .returning(AmazonIngestionRun)
        ).scalar_one_or_none()
        self.session.flush()
        return claimed

    def count_active_listings_runs_for_organization(self, organization_id: UUID) -> int:
        """Nonterminal (`queued`/`started`/`waiting_to_retry`) Listings run
        count for one organization, across every participation.

        Monitoring/reporting only — see this class's own docstring history:
        an earlier version of 12B.3G used this to gate the *trigger*
        (`listings_sync_max_concurrent_jobs_per_organization`), which was
        wrong — it conflated worker EXECUTION capacity with queue
        ADMISSION, meaning a legitimate new job could be rejected outright
        just because other jobs happened to be queued, not because any
        worker was actually busy. The trigger no longer calls this method.
        Execution capacity is enforced exactly once, at claim time, by
        `claim_next_listings_job`'s own `started`-only count. See
        `count_queued_listings_runs_for_organization` for the trigger's
        actual (queue-backlog-only) admission check.
        """
        count = self.session.scalar(
            select(func.count())
            .select_from(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status.in_(("queued", "started", "waiting_to_retry")),
            )
        )
        return count or 0

    def count_active_listings_runs_global(self) -> int:
        """Nonterminal Listings run count across every organization.

        Monitoring/reporting only — not used for trigger admission or
        claim-time execution limits. See `count_active_listings_runs_
        for_organization`'s docstring for why, and `claim_next_listings_
        job` for the actual (`started`-only) global execution limit.
        """
        count = self.session.scalar(
            select(func.count())
            .select_from(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status.in_(("queued", "started", "waiting_to_retry")),
            )
        )
        return count or 0

    def count_queued_listings_runs_for_organization(self, organization_id: UUID) -> int:
        """Genuine queue-backlog count for one organization: `status =
        'queued'` only — never `started` or `waiting_to_retry`, which
        already each occupy their own participation's single-writer slot
        and are not "backlog" in the sense this exists to bound.

        This is the trigger's *only* admission-time capacity check
        (`listings_sync_max_queued_per_organization`) — a safety valve
        against unbounded queue growth (e.g. a caller triggering many
        distinct marketplace participations in a tight loop), deliberately
        unrelated to worker execution capacity. A legitimate new job is
        never rejected merely because workers are currently busy; it is
        only ever rejected if this organization's *queue* itself has
        grown unreasonably large.
        """
        count = self.session.scalar(
            select(func.count())
            .select_from(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status == "queued",
            )
        )
        return count or 0

    def terminalize_unclaimed_listings_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        failure_class: str = "cancelled_before_start",
    ) -> bool:
        """Operator-only, race-safe compare-and-set: terminalizes a
        Listings run that has **never been claimed** — never a `started`,
        `waiting_to_retry`, or already-terminal run, never a run from
        another organization, and never a `marketplace_participations`
        run. Exists so an operator can dispose of a durable job that
        should never be processed (e.g. one queued in error) without
        risking a race against a worker that might claim it at any
        moment — there is no other safe way to remove a queued job's
        single-writer hold on its scope short of this.

        The WHERE clause requires ALL of: exact `id` + `organization_id`
        match, `run_type = 'listings'`, `status = 'queued'`, and
        `started_at`/`lease_owner`/`lease_expires_at`/`last_heartbeat_at`
        all `IS NULL` — i.e. this run has not been touched by anything
        since `enqueue_listings_run` created it. If a worker claims the
        row (or anything else changes about it) between an operator's
        decision to call this and the call itself, every one of those
        columns changes, the `UPDATE` matches zero rows, and this returns
        `False` — never overwriting a job that has since become active.

        On success, transitions to `status='failed'` with `completed_at`
        set, the given sanitized `failure_class` (default
        `'cancelled_before_start'`), and `pagination_complete=False` —
        truthful: this run never attempted pagination at all. Every
        counter (`records_received`/`records_accepted`/`records_rejected`/
        `pages_fetched`) is left at its existing value, which for a
        genuinely never-claimed run is always the `0` `enqueue_listings_
        run` set them to — never fabricated here. No lease field is
        touched (all already `NULL`, per the WHERE clause). Never calls
        Amazon and never touches `amazon_seller_listings` — this method
        exists entirely within `amazon_ingestion_runs`.
        """
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status == "queued",
                AmazonIngestionRun.started_at.is_(None),
                AmazonIngestionRun.lease_owner.is_(None),
                AmazonIngestionRun.lease_expires_at.is_(None),
                AmazonIngestionRun.last_heartbeat_at.is_(None),
            )
            .values(
                status="failed",
                completed_at=func.now(),
                failure_class=failure_class,
                pagination_complete=False,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    def reschedule_listings_run_for_retry(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        next_retry_at,
        failure_class: str,
        pages_fetched: int = 0,
        records_received: int = 0,
        reported_total_results: int | None = None,
    ) -> bool:
        """Releases the lease and moves a run to `waiting_to_retry` rather
        than a terminal status — the run is *not* completed, it is paused.
        Same lease-owner/status/unexpired-lease compare-and-set guarantee
        as `complete_listings_run`, so a caller that already lost its
        lease can never reschedule a run it no longer owns, and a stale
        worker can never undo a newer owner's progress."""
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.lease_owner == lease_owner,
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at > func.now(),
            )
            .values(
                status="waiting_to_retry",
                next_retry_at=next_retry_at,
                failure_class=failure_class,
                pages_fetched=pages_fetched,
                records_received=records_received,
                reported_total_results=reported_total_results,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        self.session.flush()
        return result.rowcount == 1


@dataclass(frozen=True)
class ListingSummaryCounts:
    """Plain aggregate counts for one marketplace participation's listings.
    12B.3E read API. Never carries an identifier — purely counts."""

    total: int
    active: int
    inactive: int
    buyable: int
    not_buyable: int
    discoverable: int
    not_discoverable: int
    with_issues: int
    without_issues: int
    severity_error: int
    severity_warning: int
    severity_info: int
    with_asin: int
    with_price: int
    with_fulfillment_availability: int


class AmazonSellerListingRepository:
    """Marketplace-participation-scoped canonical listings. 12B.3D.

    `amazon_seller_listings` has no `organization_id` column by design (see
    `AmazonSellerListing`'s docstring in `models.py`) — but that must not
    mean "any caller holding a `marketplace_participation_id` UUID can
    write." Every public read and write on this repository takes
    `organization_id` and validates it against `marketplace_participation_
    id` via `AmazonMarketplaceParticipationRepository.get_by_id` (an
    indexed primary-key lookup) — the same org-scoped-lookup pattern
    already used by `AmazonIngestionRunRepository.claim_listings_run` and
    every other repository in this module. Possession of a participation
    UUID alone is never sufficient.

    Writes go through exactly one path: `reconcile_snapshot()`. It
    validates ownership **once** per call (not once per listing — a
    snapshot can contain hundreds of items, and re-validating per row would
    be exactly the "inefficient per-row ownership query" this design
    avoids), then performs every upsert and the deactivation pass against
    that single already-validated `marketplace_participation_id`. The
    lower-level `_upsert`/`_deactivate_missing` helpers are intentionally
    private and are never called from outside this class — there is no
    public method that can mutate a listing without first passing the
    organization check in `reconcile_snapshot()`.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_participation_in_organization(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> None:
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            # Deliberately generic: missing, cross-organization, and
            # not-this-organization's-participation all fail identically,
            # with no foreign identifier disclosed — the same fail-closed
            # shape already used throughout this module (e.g. `claim_
            # listings_run`, `AmazonListingsIngestionService._validate_and_
            # claim`'s `scope_not_found`).
            raise TypeError(
                "Amazon seller listing access cannot bind a marketplace participation from another organization."
            )

    def get_by_natural_key(
        self, organization_id: UUID, marketplace_participation_id: UUID, seller_sku: str
    ) -> AmazonSellerListing | None:
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        return self._get_by_natural_key_unchecked(marketplace_participation_id, seller_sku)

    def _get_by_natural_key_unchecked(
        self, marketplace_participation_id: UUID, seller_sku: str
    ) -> AmazonSellerListing | None:
        """No organization check — only ever called from inside this class
        (`_upsert`, and the public, already-checked `get_by_natural_key`
        above), after ownership has already been validated exactly once for
        the enclosing call."""
        return self.session.scalars(
            select(AmazonSellerListing).where(
                AmazonSellerListing.marketplace_participation_id == marketplace_participation_id,
                AmazonSellerListing.seller_sku == seller_sku,
            )
        ).first()

    def list_for_participation(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> list[AmazonSellerListing]:
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        statement: Select[tuple[AmazonSellerListing]] = (
            select(AmazonSellerListing)
            .where(AmazonSellerListing.marketplace_participation_id == marketplace_participation_id)
            .order_by(AmazonSellerListing.seller_sku.asc())
        )
        return list(self.session.scalars(statement).all())

    def reconcile_snapshot(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        listings: list,
        last_ingestion_run_id: UUID,
    ) -> tuple[int, int]:
        """The one validated, organization-scoped write boundary for a
        listings snapshot. Validates ownership exactly once, then upserts
        every entry in `listings` (each a `NormalizedListing`-shaped object
        — see `app.amazon.listings_normalization`) and deactivates whatever
        is missing from it. Returns `(upserted_count, deactivated_count)`.

        Callers must only pass a fully validated, authoritative snapshot —
        this method does not itself decide authority (that remains the
        service's job); it only decides *ownership*.
        """
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        seen_skus: set[str] = set()
        for listing in listings:
            self._upsert(
                marketplace_participation_id=marketplace_participation_id,
                seller_sku=listing.seller_sku,
                asin=listing.asin,
                product_type=listing.product_type,
                condition_type=listing.condition_type,
                item_name=listing.item_name,
                main_image_url=listing.main_image_url,
                amazon_created_at=listing.amazon_created_at,
                amazon_last_updated_at=listing.amazon_last_updated_at,
                status=listing.status,
                is_buyable=listing.is_buyable,
                is_discoverable=listing.is_discoverable,
                offers=listing.offers,
                price_amount=listing.price_amount,
                price_currency=listing.price_currency,
                fulfillment_availability=listing.fulfillment_availability,
                issues=listing.issues,
                issue_count=listing.issue_count,
                highest_issue_severity=listing.highest_issue_severity,
                product_types=listing.product_types,
                last_ingestion_run_id=last_ingestion_run_id,
            )
            seen_skus.add(listing.seller_sku)
        deactivated = self._deactivate_missing(
            marketplace_participation_id=marketplace_participation_id, seen_skus=seen_skus
        )
        return len(listings), deactivated

    def _upsert(
        self,
        *,
        marketplace_participation_id: UUID,
        seller_sku: str,
        asin: str | None,
        product_type: str | None,
        condition_type: str | None,
        item_name: str | None,
        main_image_url: str | None,
        amazon_created_at: datetime | None,
        amazon_last_updated_at: datetime | None,
        status: list,
        is_buyable: bool,
        is_discoverable: bool,
        offers: list,
        price_amount,
        price_currency: str | None,
        fulfillment_availability: list,
        issues: list,
        issue_count: int,
        highest_issue_severity: str | None,
        product_types: list,
        last_ingestion_run_id: UUID,
    ) -> AmazonSellerListing:
        """Upsert by `(marketplace_participation_id, seller_sku)`. Preserves
        `first_seen_at`; reactivates a previously-inactive row that
        reappears. `last_ingestion_run_id` must already belong to the same
        `marketplace_participation_id` and carry `run_type='listings'` —
        this is enforced by the database's own composite foreign key
        (`fk_amazon_seller_listings_last_ingestion_run_same_participation`),
        not re-validated here; an `IntegrityError` from a genuine caller bug
        propagates rather than being silently swallowed.
        """
        now = datetime.now(UTC)
        existing = self._get_by_natural_key_unchecked(marketplace_participation_id, seller_sku)
        if existing is not None:
            existing.asin = asin
            existing.product_type = product_type
            existing.condition_type = condition_type
            existing.item_name = item_name
            existing.main_image_url = main_image_url
            existing.amazon_created_at = amazon_created_at
            existing.amazon_last_updated_at = amazon_last_updated_at
            existing.status = status
            existing.is_buyable = is_buyable
            existing.is_discoverable = is_discoverable
            existing.offers = offers
            existing.price_amount = price_amount
            existing.price_currency = price_currency
            existing.fulfillment_availability = fulfillment_availability
            existing.issues = issues
            existing.issue_count = issue_count
            existing.highest_issue_severity = highest_issue_severity
            existing.product_types = product_types
            existing.is_active = True
            existing.last_seen_at = now
            existing.last_successful_sync_at = now
            existing.last_ingestion_run_id = last_ingestion_run_id
            self.session.flush()
            return existing
        row = AmazonSellerListing(
            marketplace_participation_id=marketplace_participation_id,
            seller_sku=seller_sku,
            asin=asin,
            product_type=product_type,
            condition_type=condition_type,
            item_name=item_name,
            main_image_url=main_image_url,
            amazon_created_at=amazon_created_at,
            amazon_last_updated_at=amazon_last_updated_at,
            status=status,
            is_buyable=is_buyable,
            is_discoverable=is_discoverable,
            offers=offers,
            price_amount=price_amount,
            price_currency=price_currency,
            fulfillment_availability=fulfillment_availability,
            issues=issues,
            issue_count=issue_count,
            highest_issue_severity=highest_issue_severity,
            product_types=product_types,
            is_active=True,
            last_successful_sync_at=now,
            last_ingestion_run_id=last_ingestion_run_id,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            # The single-writer lease should make a genuine natural-key race
            # unreachable in practice (only one process reconciles a given
            # scope at a time), but a SAVEPOINT here costs nothing and
            # matches the established convention elsewhere in this module
            # rather than risking an aborted outer transaction on an
            # unexpected race. Not every IntegrityError here is that race,
            # though: a genuine composite-FK violation (last_ingestion_run_id
            # from a different participation, or a non-'listings' run — a
            # real caller bug) also raises IntegrityError but leaves no
            # natural-key winner row to find. Re-raise honestly in that case
            # instead of letting `.one()` mask it as a confusing
            # `NoResultFound`.
            winner = self.session.scalars(
                select(AmazonSellerListing).where(
                    AmazonSellerListing.marketplace_participation_id == marketplace_participation_id,
                    AmazonSellerListing.seller_sku == seller_sku,
                )
            ).first()
            if winner is None:
                raise
            winner.asin = asin
            winner.product_type = product_type
            winner.condition_type = condition_type
            winner.item_name = item_name
            winner.main_image_url = main_image_url
            winner.amazon_created_at = amazon_created_at
            winner.amazon_last_updated_at = amazon_last_updated_at
            winner.status = status
            winner.is_buyable = is_buyable
            winner.is_discoverable = is_discoverable
            winner.offers = offers
            winner.price_amount = price_amount
            winner.price_currency = price_currency
            winner.fulfillment_availability = fulfillment_availability
            winner.issues = issues
            winner.issue_count = issue_count
            winner.highest_issue_severity = highest_issue_severity
            winner.product_types = product_types
            winner.is_active = True
            winner.last_seen_at = now
            winner.last_successful_sync_at = now
            winner.last_ingestion_run_id = last_ingestion_run_id
            self.session.flush()
            return winner
        return row

    def _deactivate_missing(
        self,
        *,
        marketplace_participation_id: UUID,
        seen_skus: set[str],
    ) -> int:
        """Mark rows absent from the latest complete, authoritative snapshot
        as inactive. Only called by `reconcile_snapshot()`, after ownership
        has already been validated once for this call. Mirrors
        `AmazonMarketplaceParticipationRepository.deactivate_missing`.
        """
        rows = self.session.scalars(
            select(AmazonSellerListing).where(
                AmazonSellerListing.marketplace_participation_id == marketplace_participation_id,
                AmazonSellerListing.is_active.is_(True),
            )
        ).all()
        now = datetime.now(UTC)
        deactivated = 0
        for row in rows:
            if row.seller_sku not in seen_skus:
                row.is_active = False
                row.last_seen_at = now
                deactivated += 1
        if deactivated:
            self.session.flush()
        return deactivated

    # --- 12B.3E: read-only, organization-scoped listings access ------------
    #
    # Distinct from `_require_participation_in_organization` (the write
    # boundary above, which raises `TypeError` for a caller bug): a read
    # miss here is an ordinary, expected "not found" outcome — the caller
    # is typically an HTTP request for a resource that may legitimately
    # not exist or not belong to this organization. These methods return
    # `None` instead, and the service layer above turns that into a
    # sanitized domain not-found error. Foreign and nonexistent
    # participation ids are indistinguishable: both simply produce `None`
    # from the same organization-scoped lookup.

    _SORT_COLUMNS: dict[str, Any] = {
        "last_seen_at": AmazonSellerListing.last_seen_at,
        "first_seen_at": AmazonSellerListing.first_seen_at,
        "seller_sku": AmazonSellerListing.seller_sku,
        "asin": AmazonSellerListing.asin,
        "issue_count": AmazonSellerListing.issue_count,
        "price_amount": AmazonSellerListing.price_amount,
    }

    def _get_owned_participation(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonMarketplaceParticipation | None:
        return AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )

    def get_summary_counts(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> "ListingSummaryCounts | None":
        """One aggregate SQL query for every plain-column count, plus one
        lightweight single-column scan for `fulfillment_availability`
        "present" (non-empty JSON array). Returns `None` if the
        participation does not belong to `organization_id`.

        The single-column scan (not a full-row load, not N+1 — one query
        selecting only `fulfillment_availability` for every row in this
        participation) is a deliberate choice over SQL-side JSON-length
        functions: PostgreSQL's `jsonb_array_length` and SQLite's JSON1
        `json_array_length` are not the same callable name, and
        SQLAlchemy has no dialect-agnostic "JSON array is non-empty"
        expression for a column typed `JSON().with_variant(JSONB(),
        "postgresql")` — introducing dialect-specific raw SQL here purely
        for aggregation elegance was judged not worth the fragility.

        Scale bound: 12B.3D's ingestion ceiling
        (`LISTINGS_RESULT_CEILING = 1000` in `listings_ingestion.py`) caps
        every *individual* successful snapshot at <=1000 accepted items —
        `reconcile_snapshot` is only ever called once, atomically, after a
        fully-accepted traversal, so no single sync can ever upsert more
        than that many rows. This does **not** by itself cap this table's
        *cumulative* row count forever: deactivated rows are kept, not
        deleted (see `_deactivate_missing`), so a seller who repeatedly
        lists and delists many distinct SKUs across many sync cycles could
        in principle accumulate more total rows than any one snapshot ever
        contained. The actual practical bound this method relies on is the
        realistic seller-catalog scale already assumed by
        `AmazonSellerListing`'s own schema docstring (12B.3B): "hundreds to
        low thousands" of distinct SKUs per seller over time, not a
        mathematical guarantee from the ingestion ceiling. If a
        participation's row count is ever observed growing far beyond that
        (e.g. from unusually high SKU churn), this scan — and the
        equivalent full-catalog page loaded by `list_page`'s default
        request — should be revisited before it, not after.
        """
        if self._get_owned_participation(organization_id, marketplace_participation_id) is None:
            return None

        row = self.session.execute(
            select(
                func.count().label("total"),
                func.count().filter(AmazonSellerListing.is_active.is_(True)).label("active"),
                func.count().filter(AmazonSellerListing.is_active.is_(False)).label("inactive"),
                func.count().filter(AmazonSellerListing.is_buyable.is_(True)).label("buyable"),
                func.count().filter(AmazonSellerListing.is_buyable.is_(False)).label("not_buyable"),
                func.count().filter(AmazonSellerListing.is_discoverable.is_(True)).label("discoverable"),
                func.count().filter(AmazonSellerListing.is_discoverable.is_(False)).label("not_discoverable"),
                func.count().filter(AmazonSellerListing.issue_count > 0).label("with_issues"),
                func.count().filter(AmazonSellerListing.issue_count == 0).label("without_issues"),
                func.count().filter(AmazonSellerListing.highest_issue_severity == "ERROR").label("severity_error"),
                func.count().filter(AmazonSellerListing.highest_issue_severity == "WARNING").label(
                    "severity_warning"
                ),
                func.count().filter(AmazonSellerListing.highest_issue_severity == "INFO").label("severity_info"),
                func.count().filter(AmazonSellerListing.asin.is_not(None)).label("with_asin"),
                func.count().filter(AmazonSellerListing.price_amount.is_not(None)).label("with_price"),
            ).where(AmazonSellerListing.marketplace_participation_id == marketplace_participation_id)
        ).one()

        fulfillment_present = 0
        for (fulfillment_availability,) in self.session.execute(
            select(AmazonSellerListing.fulfillment_availability).where(
                AmazonSellerListing.marketplace_participation_id == marketplace_participation_id
            )
        ):
            if fulfillment_availability:
                fulfillment_present += 1

        return ListingSummaryCounts(
            total=row.total,
            active=row.active,
            inactive=row.inactive,
            buyable=row.buyable,
            not_buyable=row.not_buyable,
            discoverable=row.discoverable,
            not_discoverable=row.not_discoverable,
            with_issues=row.with_issues,
            without_issues=row.without_issues,
            severity_error=row.severity_error,
            severity_warning=row.severity_warning,
            severity_info=row.severity_info,
            with_asin=row.with_asin,
            with_price=row.with_price,
            with_fulfillment_availability=fulfillment_present,
        )

    def list_page(
        self,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        *,
        search: str | None = None,
        is_active: bool | None = None,
        is_buyable: bool | None = None,
        is_discoverable: bool | None = None,
        has_issues: bool | None = None,
        highest_issue_severity: str | None = None,
        product_type: str | None = None,
        sort_by: str = "last_seen_at",
        sort_dir: str = "desc",
        offset: int = 0,
        limit: int = 25,
    ) -> "tuple[list[AmazonSellerListing], int] | None":
        """Validated, filtered, deterministically-ordered listings page,
        scoped to one organization-owned marketplace participation.
        Returns `None` if the participation does not belong to
        `organization_id`.

        `sort_by`/`sort_dir` are validated against an explicit allowlist
        (`_SORT_COLUMNS`, `{"asc", "desc"}`) and raise `ValueError` if
        not recognized. The API layer's `Literal[...]` request typing is
        the primary rejection point (this project's `RequestValidationError`
        handler returns 400 before this method is ever called — see
        `app/main.py`); this is defense in depth, not the only check.

        Ordering policy (identical on SQLite and PostgreSQL, verified by
        direct compilation): the requested sort column, `NULLS LAST`
        regardless of direction, then `id ASC` as a final, always-present
        tie-breaker. Two nullable sort fields (`asin`, `price_amount`)
        would otherwise sort differently across dialects — PostgreSQL
        defaults to NULLS LAST for ASC / NULLS FIRST for DESC, SQLite
        always puts NULLs first — so the `NULLS LAST` placement is always
        explicit here, never left to either dialect's default. The `id`
        tie-breaker is applied *after* NULLS LAST placement, so multiple
        NULL rows (or multiple rows sharing the same non-null value) still
        sort deterministically among themselves, and identical requests
        return identical pages across repeated calls and across page
        boundaries.

        `search` matches seller SKU or ASIN, case-insensitively, as a
        literal substring: `%`, `_`, and the escape character itself are
        escaped via `_escape_like_term` before being wrapped in wildcards,
        so a search for e.g. `"SKU_100"` or `"10%OFF"` never widens into
        an unintended catalog-wide match — those characters are never
        treated as SQL wildcards. A whitespace-only search is treated
        identically to no search at all.
        """
        if self._get_owned_participation(organization_id, marketplace_participation_id) is None:
            return None
        if sort_by not in self._SORT_COLUMNS:
            raise ValueError(f"Unsupported listings sort field: {sort_by!r}")
        if sort_dir not in ("asc", "desc"):
            raise ValueError(f"Unsupported listings sort direction: {sort_dir!r}")

        filters = [AmazonSellerListing.marketplace_participation_id == marketplace_participation_id]
        search = (search or "").strip()
        if search:
            term = f"%{_escape_like_term(search)}%"
            filters.append(
                or_(
                    AmazonSellerListing.seller_sku.ilike(term, escape=_LIKE_ESCAPE_CHAR),
                    AmazonSellerListing.asin.ilike(term, escape=_LIKE_ESCAPE_CHAR),
                )
            )
        if is_active is not None:
            filters.append(AmazonSellerListing.is_active.is_(is_active))
        if is_buyable is not None:
            filters.append(AmazonSellerListing.is_buyable.is_(is_buyable))
        if is_discoverable is not None:
            filters.append(AmazonSellerListing.is_discoverable.is_(is_discoverable))
        if has_issues is not None:
            filters.append(
                AmazonSellerListing.issue_count > 0 if has_issues else AmazonSellerListing.issue_count == 0
            )
        if highest_issue_severity is not None:
            filters.append(AmazonSellerListing.highest_issue_severity == highest_issue_severity)
        if product_type is not None:
            filters.append(AmazonSellerListing.product_type == product_type)

        total = self.session.scalar(select(func.count()).select_from(AmazonSellerListing).where(*filters)) or 0

        sort_column = self._SORT_COLUMNS[sort_by]
        # Explicit NULLS LAST for both directions: `asin` and `price_amount`
        # are nullable sort fields, and SQLite/PostgreSQL disagree on where
        # NULL sorts by default (PostgreSQL: NULLS LAST for ASC, NULLS
        # FIRST for DESC; SQLite: NULLS FIRST always). `nulls_last()`
        # compiles to the identical `NULLS LAST` clause on both dialects
        # (verified directly), so ordering is deterministic and identical
        # in local SQLite tests and production PostgreSQL regardless of
        # sort direction. `id ASC` remains the final tie-breaker, applied
        # after the NULLS LAST placement, so multiple NULL rows (or
        # multiple rows sharing the same non-null value) still sort
        # deterministically among themselves.
        order = (sort_column.desc() if sort_dir == "desc" else sort_column.asc()).nulls_last()
        statement = (
            select(AmazonSellerListing)
            .where(*filters)
            .order_by(order, AmazonSellerListing.id.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(self.session.scalars(statement).all())
        return rows, int(total)

    def get_detail(
        self, organization_id: UUID, marketplace_participation_id: UUID, listing_id: UUID
    ) -> AmazonSellerListing | None:
        """One listing, scoped through both the organization-owned
        participation and that exact participation's own id. A listing
        cannot be retrieved by id alone, and cannot be retrieved through
        a *different* (even organization-owned) participation than the
        one it actually belongs to — the `marketplace_participation_id`
        equality check below is not redundant with `_get_owned_
        participation`, it is the second half of the same boundary.
        """
        if self._get_owned_participation(organization_id, marketplace_participation_id) is None:
            return None
        return self.session.scalars(
            select(AmazonSellerListing).where(
                AmazonSellerListing.id == listing_id,
                AmazonSellerListing.marketplace_participation_id == marketplace_participation_id,
            )
        ).first()


def file_sha256(data: bytes) -> str:
    return sha256_bytes(data)

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
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
    AmazonIngestionRunMarketplaceParticipation,
    AmazonMarketplaceParticipation,
    AmazonOAuthState,
    AmazonOrdersSyncCheckpoint,
    AmazonSalesAndTrafficDailyFact,
    AmazonSalesAndTrafficProductFact,
    AmazonSalesAndTrafficSyncCheckpoint,
    AmazonSellerAccount,
    AmazonSellerListing,
    AmazonSellerOrder,
    AmazonSellerOrderItem,
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


@dataclass(frozen=True)
class SalesTrafficRunClaim:
    """Outcome of an atomic Sales and Traffic report-run enqueue/claim
    attempt — mirrors `ListingsRunClaim` exactly (this run type shares
    the same single-participation scope shape, never Orders' coarser
    multi-participation one). Never carries a lease owner, report id,
    or any identifier beyond what the caller already supplied."""

    claimed: bool
    run_id: UUID | None = None
    reason: str | None = None  # "already_running" when claimed is False


# Shared by `get_latest_listings_run` and `get_latest_cooldown_relevant_
# listings_run` — see the former's docstring for why the second key
# (a boolean expression, never null on either PostgreSQL or SQLite) is
# required to keep a same-`created_at` `cancelled_before_start` row from
# ever outranking a genuinely newer, actually-started one.
_LATEST_LISTINGS_RUN_ORDER_BY = (
    AmazonIngestionRun.created_at.desc(),
    AmazonIngestionRun.started_at.is_not(None).desc(),
    AmazonIngestionRun.id.desc(),
)


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
        evidence.

        Orders by `created_at` (every row has one — server-generated at
        insert, never null), not `started_at`. An earlier version ordered
        by `started_at DESC, id DESC`: `started_at` is null for any row
        that was never claimed (most notably a `cancelled_before_start`
        administrative cancellation — see `listings_job_admin`), and
        PostgreSQL's default `NULLS FIRST` for `DESC` sorts every such row
        *ahead* of every genuinely more recent, actually-started row,
        forever, however old it is. In production this caused
        `AmazonListingsSyncTriggerService.trigger()`'s cooldown check (a
        direct caller of this method) to read a multi-day-old
        never-started row's `created_at` as "the latest run" long after
        three brand-new successful runs had actually happened, so the
        cooldown's elapsed-time computation was never remotely close to
        triggering — the cooldown was silently inert for that
        participation. The same bug independently made `get_summary`'s
        sync-status evidence show that stale cancelled row as "latest"
        while real, newer synchronizations kept succeeding. Confirmed
        directly against the live database: this method returned a
        `cancelled_before_start` row from days earlier while three newer
        `succeeded` rows already existed. `created_at` ordering has no
        null to worry about and is also the more honest definition of
        "latest" for evidence purposes regardless — the moment this run
        was created, not the moment (if any) a worker got to it.

        Ties broken by `started_at IS NOT NULL` before `id`: `created_at`
        alone is not a *sufficient* tiebreak-free key on every backend —
        SQLite's `CURRENT_TIMESTAMP` (what `created_at`'s server default
        compiles to there) only has second-level precision, so two rows
        genuinely created microseconds apart in rapid succession can
        still tie exactly. Falling through to `id DESC` alone in that
        case would compare random UUIDs, which is stable (the same query
        against the same data always returns the same row) but not
        *correct*: it has no relationship to which row is actually more
        recent, and could just as easily pick a tied `cancelled_before_
        start` row (`started_at IS NULL`) over a genuinely newer one that
        happened to tie it. `AmazonIngestionRun.started_at.is_not(None)`
        is a boolean expression, not a nullable column being sorted
        directly — it evaluates to plain `TRUE`/`FALSE` for every row on
        both PostgreSQL and SQLite, so it carries none of the `NULLS
        FIRST`/`NULLS LAST` divergence a raw `started_at` sort would. This
        guarantees a row that actually started can never be outranked by
        a same-`created_at` row that never did; `id DESC` remains only as
        the final, genuinely-arbitrary tiebreak for two rows that tie on
        both `created_at` and started-ness."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
            )
            .order_by(*_LATEST_LISTINGS_RUN_ORDER_BY)
            .limit(1)
        ).first()

    def get_latest_cooldown_relevant_listings_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        """Like `get_latest_listings_run`, but restricted to **terminal**
        runs (`succeeded`, `partial`, `failed`, `timed_out`) and excluding
        `cancelled_before_start` rows — mirrors `get_latest_cooldown_
        relevant_orders_run`'s same terminal-only semantics (its own
        docstring already says so; this method's status filter had
        drifted from it). An administrative cancellation of a job that
        never started never made an Amazon call and did zero work — the
        entire point of `terminalize-queued` is to unblock a stuck scope
        so a real attempt can proceed, so it must never itself impose a
        fresh cooldown on top of the wait the operator just ended. Every
        other terminal outcome (`succeeded`, `partial`, `timed_out`, and a
        genuine `failed` run that actually made an Amazon call before
        failing) still counts toward the cooldown, because each of those
        represents real, recent Amazon API usage worth pacing against.

        The status restriction is not cosmetic: without it, a `queued` or
        `started` sibling row — including one a *concurrent* trigger call
        just created, an instant before this call's own transaction reads
        it — is misread as "the latest cooldown-relevant run", and its
        `created_at` (a queued row has no `completed_at` yet, so the
        anchor falls back to `created_at`) computes a fresh multi-minute
        cooldown window against a job that has not made any real Amazon
        call at all. Under concurrent triggers for the same scope this
        made a losing caller spuriously resolve to `reason="cooldown"`
        instead of truthfully reporting the winner's job as
        `"already_running"` — a real production race, not a test-only
        concern, reproduced under real PostgreSQL by
        `test_ten_concurrent_triggers_create_at_most_one_job`. Restricting
        to terminal statuses closes it: a still-`queued`/`started`/
        `waiting_to_retry` row can never again be mistaken for a genuine,
        already-completed Amazon attempt.

        Same tie-safe ordering as `get_latest_listings_run` — see its
        docstring."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "listings",
                AmazonIngestionRun.status.in_(("succeeded", "partial", "failed", "timed_out")),
                or_(
                    AmazonIngestionRun.failure_class.is_(None),
                    AmazonIngestionRun.failure_class != "cancelled_before_start",
                ),
            )
            .order_by(*_LATEST_LISTINGS_RUN_ORDER_BY)
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

    # --- 12B.4D: Orders run lifecycle (mirrors the Listings methods above,
    # regrouped by Orders' coarser (seller_account, region, environment)
    # scope instead of (seller_account, marketplace_participation)) -------

    def get_latest_orders_run(
        self, organization_id: UUID, seller_account_id: UUID, region: str, environment: str
    ) -> AmazonIngestionRun | None:
        """Mirrors `get_latest_listings_run`'s tie-safe ordering exactly
        (`_LATEST_LISTINGS_RUN_ORDER_BY` — see that method's docstring for
        the full reasoning): SQLite's `CURRENT_TIMESTAMP`
        (`created_at`'s server default there) only has second-level
        precision, so two rows genuinely created within the same second
        can tie on `created_at` alone, and `id DESC` alone would compare
        unrelated UUIDs with no relationship to actual recency. This
        exact class of bug was already found and fixed once for Listings
        in production — the identical fix is applied here from the
        start, not discovered again the same way."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.region == region,
                AmazonIngestionRun.environment == environment,
                AmazonIngestionRun.run_type == "orders",
            )
            .order_by(*_LATEST_LISTINGS_RUN_ORDER_BY)
            .limit(1)
        ).first()

    def get_latest_successful_orders_run(
        self, organization_id: UUID, seller_account_id: UUID, region: str, environment: str
    ) -> AmazonIngestionRun | None:
        """Mirrors `get_latest_successful_listings_run`'s ordering: every
        row here already has `status='succeeded'`, so `started_at` is
        guaranteed non-null (set at first claim) — no `IS NOT NULL`
        tie-break trick is needed, `id DESC` is a sufficient final
        tiebreak for two runs that tied on `started_at` too."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.region == region,
                AmazonIngestionRun.environment == environment,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status == "succeeded",
            )
            .order_by(AmazonIngestionRun.started_at.desc(), AmazonIngestionRun.id.desc())
            .limit(1)
        ).first()

    def get_latest_cooldown_relevant_orders_run(
        self, organization_id: UUID, seller_account_id: UUID, region: str, environment: str
    ) -> AmazonIngestionRun | None:
        """Mirrors `get_latest_cooldown_relevant_listings_run`. There is no
        `cancelled_before_start` concept for Orders (no operator-cancel
        method exists for this run_type), so this is currently equivalent
        to the latest terminal run — kept as its own method for parity
        with the trigger service and to isolate future divergence. Same
        tie-safe ordering as `get_latest_orders_run`."""
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.region == region,
                AmazonIngestionRun.environment == environment,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status.in_(("succeeded", "partial", "failed", "timed_out")),
            )
            .order_by(*_LATEST_LISTINGS_RUN_ORDER_BY)
            .limit(1)
        ).first()

    def get_active_orders_run(
        self, organization_id: UUID, seller_account_id: UUID, region: str, environment: str
    ) -> AmazonIngestionRun | None:
        return self.session.scalars(
            select(AmazonIngestionRun).where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.region == region,
                AmazonIngestionRun.environment == environment,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status.in_(("queued", "started", "waiting_to_retry")),
            )
        ).first()

    def count_queued_orders_runs_for_organization(self, organization_id: UUID) -> int:
        """Mirrors `count_queued_listings_runs_for_organization`'s own
        queue-backlog-only (never worker-execution-capacity) semantics."""
        count = self.session.scalar(
            select(func.count())
            .select_from(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status == "queued",
            )
        )
        return count or 0

    def heartbeat_orders_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        lease_duration_seconds: int,
        pages_fetched: int,
        orders_received: int,
        orders_accepted: int,
        orders_rejected: int,
        items_received: int,
        items_accepted: int,
        items_rejected: int,
        pagination_next_token: str | None,
    ) -> bool:
        """Compare-and-set heartbeat/progress update: only succeeds while
        this exact `lease_owner` still holds the row in `started`. Mirrors
        `heartbeat_listings_run`, extended with Orders' own counters
        (`orders_*`/`items_*` — see `AmazonIngestionRun`'s docstring for
        why these are additive to, not a reuse of, the generic
        `records_*` columns Listings uses).

        `pages_fetched` is now a run-cumulative count of pages
        successfully committed across every attempt of this run (seeded
        from the durable resume point at the start of a resumed attempt),
        not an attempt-local count reset to zero on every retry — a more
        honest "current page number" for both `AmazonOrdersReadService`'s
        public evidence and this method's own durable-pagination
        bookkeeping.

        `pagination_next_token` (12B.4D remediation) is written on every
        call, including the periodic in-flight keepalive from
        `_renew_lease_while_awaiting` — which always passes back whatever
        value is already current (no new page has been persisted since
        the last write), making that call idempotent for this column.
        The caller that just persisted a new page passes that page's own
        `next_token` (`None` once pagination completes), atomically with
        the order/item upserts already in the same transaction — this is
        what "resume from the saved next token, not page one" is built
        on. See `AmazonIngestionRun.orders_pagination_next_token`'s own
        docstring for the full threat-model reasoning."""
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_owner == lease_owner,
            )
            .values(
                last_heartbeat_at=func.now(),
                lease_expires_at=self._lease_expiry_value(lease_duration_seconds),
                pages_fetched=pages_fetched,
                orders_received=orders_received,
                orders_accepted=orders_accepted,
                orders_rejected=orders_rejected,
                items_received=items_received,
                items_accepted=items_accepted,
                items_rejected=items_rejected,
                orders_pagination_next_token=pagination_next_token,
            )
        )
        self.session.flush()
        return result.rowcount > 0

    def freeze_orders_window_if_needed(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        last_updated_after: datetime,
        captured_at: datetime,
    ) -> tuple[datetime, datetime]:
        """One-time freeze of this run's `lastUpdatedAfter` search-window
        start and "as of" completeness timestamp (12B.4D remediation —
        see `AmazonIngestionRun.orders_window_last_updated_after`'s
        docstring for why this must be frozen, not recomputed, once a
        pagination token might be reused across attempts).

        Idempotent by construction via `IS NULL` in the `WHERE` clause,
        not a lease-owner compare-and-set: by the time any caller reaches
        this, `claim_next_orders_job`'s own claim CAS has already ensured
        exactly one worker holds this run_id as `started`, and no other
        code path ever writes these two columns, so there is no
        concurrent writer to guard against beyond "do not overwrite an
        already-frozen value." Always returns the authoritative frozen
        values — the ones this call just wrote, or the ones an earlier
        attempt of the same run already wrote."""
        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.orders_window_last_updated_after.is_(None),
            )
            .values(orders_window_last_updated_after=last_updated_after, orders_window_captured_at=captured_at)
        )
        self.session.flush()
        frozen = self.session.execute(
            select(
                AmazonIngestionRun.orders_window_last_updated_after,
                AmazonIngestionRun.orders_window_captured_at,
            ).where(AmazonIngestionRun.id == run_id, AmazonIngestionRun.organization_id == organization_id)
        ).one()
        # SQLite (unlike PostgreSQL) does not round-trip `tzinfo` on a
        # `DateTime(timezone=True)` column — a value just read back here
        # is naive but still genuinely UTC (this application never stores
        # any other zone). Normalize before returning so every caller
        # gets a tz-aware value regardless of backend, matching the same
        # pattern already used for checkpoint/lease comparisons elsewhere
        # in this module.
        last_updated_after, captured_at = frozen[0], frozen[1]
        if last_updated_after.tzinfo is None:
            last_updated_after = last_updated_after.replace(tzinfo=UTC)
        if captured_at.tzinfo is None:
            captured_at = captured_at.replace(tzinfo=UTC)
        return last_updated_after, captured_at

    def reschedule_orders_run_for_retry(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        next_retry_at: datetime,
        failure_class: str,
        pages_fetched: int,
        orders_received: int,
        orders_accepted: int,
        orders_rejected: int,
        items_received: int,
        items_accepted: int,
        items_rejected: int,
        pagination_next_token: str | None,
    ) -> bool:
        """Mirrors `reschedule_listings_run_for_retry`: releases the lease
        and moves the row to `waiting_to_retry`. Compare-and-set on
        `lease_owner` + `lease_expires_at > now()` so a lease this caller
        no longer verifiably holds (already reclaimed as `timed_out` by
        someone else) can never be rescheduled by it.

        `pagination_next_token`/`pages_fetched` (12B.4D remediation): the
        ordinary case (throttled/transient/malformed-page) preserves
        whatever durable continuation state the caller already committed
        per-page — pass the same values back unchanged so a retried
        attempt resumes exactly where the last committed page left off.
        The one deliberate exception is a `pagination_token_rejected`
        failure (Amazon rejected a resumed token, most plausibly because
        of its documented 24-hour expiry — see
        `AmazonOrdersIngestionService`'s module docstring): the caller
        passes `pagination_next_token=None` and `pages_fetched=0` in that
        case specifically, an explicit, classified, truthfully-recorded
        fallback to a page-one restart *within the still-frozen window*
        — never a silent one, and never a full watermark/window recompute
        (the frozen `orders_window_last_updated_after` is untouched by
        this method)."""
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.lease_owner == lease_owner,
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at > func.now(),
            )
            .values(
                status="waiting_to_retry",
                next_retry_at=next_retry_at,
                failure_class=failure_class,
                pages_fetched=pages_fetched,
                orders_received=orders_received,
                orders_accepted=orders_accepted,
                orders_rejected=orders_rejected,
                items_received=items_received,
                items_accepted=items_accepted,
                items_rejected=items_rejected,
                orders_pagination_next_token=pagination_next_token,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    def complete_orders_run_as_failed(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        status: str,
        failure_class: str | None,
        pages_fetched: int,
        orders_received: int,
        orders_accepted: int,
        orders_rejected: int,
        items_received: int,
        items_accepted: int,
        items_rejected: int,
        pagination_complete: bool,
    ) -> bool:
        """Terminal, non-successful completion (`failed`/`partial`/
        `timed_out`). Deliberately never accepts `status='succeeded'`:
        that transition, together with checkpoint advancement, is the
        sole responsibility of `AmazonIngestionRunMarketplaceParticipation
        Repository.finalize_successful_orders_run` — see that method's
        own docstring for why success and checkpoint advancement must
        never be split into two separately-callable steps.

        Always clears `orders_pagination_next_token` to `NULL` (12B.4D
        remediation): this run has reached a terminal state, so any
        continuation token it held is dead — a future retry of this exact
        scope creates a brand-new run row with its own fresh pagination
        state, never resumes this one."""
        if status == "succeeded":
            raise ValueError("use finalize_successful_orders_run for a successful completion")
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.lease_owner == lease_owner,
                AmazonIngestionRun.status == "started",
            )
            .values(
                status=status,
                completed_at=func.now(),
                lease_owner=None,
                lease_expires_at=None,
                next_retry_at=None,
                orders_pagination_next_token=None,
                failure_class=failure_class,
                pages_fetched=pages_fetched,
                orders_received=orders_received,
                orders_accepted=orders_accepted,
                orders_rejected=orders_rejected,
                items_received=items_received,
                items_accepted=items_accepted,
                items_rejected=items_rejected,
                pagination_complete=pagination_complete,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    # Advisory-lock key distinct from `_LISTINGS_CLAIM_ADVISORY_LOCK_KEY`
    # (847_539_201_663) — two independent workers (Listings, Orders)
    # claiming concurrently must never serialize against each other's
    # decision step.
    _ORDERS_CLAIM_ADVISORY_LOCK_KEY = 847_539_201_664

    def claim_next_orders_job(
        self,
        *,
        lease_owner: str,
        lease_duration_seconds: int,
        max_global_active: int,
        max_active_per_organization: int,
    ) -> AmazonIngestionRun | None:
        """Worker-side claim: atomically picks at most one eligible Orders
        job (`queued`, or `waiting_to_retry` whose `next_retry_at` has
        passed) across *every* organization/seller_account/region/
        environment scope, subject to global and per-organization
        concurrency limits, and transitions it to `started`.

        Structurally identical to `claim_next_listings_job` — same
        stale-reclaim step, same transaction-scoped advisory lock, same
        single-row `FOR UPDATE SKIP LOCKED` subquery technique, same
        `started_at`/`retry_count` semantics — grouped by `run_type =
        'orders'` rather than `'listings'`. `claim_orders_run` (on
        `AmazonIngestionRunMarketplaceParticipationRepository`) is a
        different, narrower method: it claims for one already-known exact
        scope, which is sufficient for a caller that already knows which
        scope it wants, but cannot discover *any* eligible job across the
        whole system the way a generic worker poll loop needs to — this
        method is that discovery step. See `claim_next_listings_job`'s own
        docstring for the full concurrency-safety reasoning, not repeated
        here beyond what differs.
        """
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": self._ORDERS_CLAIM_ADVISORY_LOCK_KEY},
            )

        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.run_type == "orders",
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
            .where(_Global.run_type == "orders", _Global.status == "started")
            .scalar_subquery()
        )
        _Org = aliased(AmazonIngestionRun)
        org_active_count = (
            select(func.count())
            .select_from(_Org)
            .where(
                _Org.run_type == "orders",
                _Org.status == "started",
                _Org.organization_id == AmazonIngestionRun.organization_id,
            )
            .scalar_subquery()
        )
        candidate_id = (
            select(AmazonIngestionRun.id)
            .where(
                AmazonIngestionRun.run_type == "orders",
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

    # --- 12B.6A: Sales and Traffic report run lifecycle ---------------
    # Scoped exactly like Listings (one participation per run — the
    # pinned Reports API contract allows exactly one marketplaceId per
    # report request, docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md
    # §1), never like Orders. Deliberately new, dedicated methods
    # (never a parametrized reuse of the Listings methods above) —
    # matching this repository's own established discipline of never
    # touching an already-proven run-type's code path to add a new one.

    _SALES_TRAFFIC_CLAIM_ADVISORY_LOCK_KEY = 847_539_201_665

    def enqueue_sales_traffic_run(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
        region: str,
        environment: str,
        connection_id: UUID,
        data_start_time: date,
        data_end_time: date,
        date_granularity: str,
        asin_granularity: str,
    ) -> SalesTrafficRunClaim:
        """Creates a durable `status='queued'` Sales and Traffic report
        job — no lease, no `createReport` call. A separate worker process
        claims it later via `claim_next_sales_traffic_job`. Protected by
        `uq_amazon_ingestion_runs_active_sales_traffic_scope`, so a
        concurrent enqueue/claim for the same participation fails the
        same way every other run type's does:
        `SalesTrafficRunClaim(claimed=False, reason="already_running")`.
        """
        seller_account = AmazonSellerAccountRepository(self.session).get_by_id(organization_id, seller_account_id)
        if seller_account is None:
            raise TypeError("Amazon sales traffic run cannot bind a seller account from another organization.")
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon sales traffic run cannot bind a marketplace participation from another organization."
            )
        if participation.seller_account_id != seller_account_id:
            raise TypeError(
                "Amazon sales traffic run marketplace participation does not belong to the given seller account."
            )
        connection = AmazonConnectionRepository(self.session).get_by_id(organization_id, connection_id)
        if connection is None:
            raise TypeError("Amazon sales traffic run cannot bind a connection from another organization.")
        if date_granularity not in ("DAY", "WEEK", "MONTH"):
            raise TypeError(f"Unsupported dateGranularity: {date_granularity!r}")
        if asin_granularity not in ("PARENT", "CHILD", "SKU"):
            raise TypeError(f"Unsupported asinGranularity: {asin_granularity!r}")
        if data_start_time > data_end_time:
            raise TypeError("Amazon sales traffic run dataStartTime must not be after dataEndTime.")

        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
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
            run_type="sales_and_traffic_report",
            domain="sales_and_traffic_report",
            status="queued",
            region=region,
            environment=environment,
            report_data_start_time=data_start_time,
            report_data_end_time=data_end_time,
            report_date_granularity=date_granularity,
            report_asin_granularity=asin_granularity,
        )
        try:
            with self.session.begin_nested():
                self.session.add(row)
                self.session.flush()
        except IntegrityError:
            return SalesTrafficRunClaim(claimed=False, reason="already_running")
        return SalesTrafficRunClaim(claimed=True, run_id=row.id)

    def get_active_sales_traffic_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
                AmazonIngestionRun.status.in_(("queued", "started", "waiting_to_retry")),
            )
            .order_by(AmazonIngestionRun.created_at.desc())
            .limit(1)
        ).first()

    def get_latest_sales_traffic_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
            )
            .order_by(AmazonIngestionRun.created_at.desc())
            .limit(1)
        ).first()

    def get_latest_successful_sales_traffic_run(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        return self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
                AmazonIngestionRun.status == "succeeded",
            )
            .order_by(AmazonIngestionRun.started_at.desc(), AmazonIngestionRun.id.desc())
            .limit(1)
        ).first()

    def claim_next_sales_traffic_job(
        self,
        *,
        lease_owner: str,
        lease_duration_seconds: int,
        max_global_active: int,
        max_active_per_organization: int,
    ) -> AmazonIngestionRun | None:
        """Worker-side claim — identical shape and safety properties to
        `claim_next_listings_job` (single-row `SKIP LOCKED` candidate,
        PostgreSQL-only transaction-scoped advisory lock serializing the
        decision step, stale-`started`-lease reclaim first), using a
        dedicated advisory-lock key never shared with any other run
        type's claim method."""
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": self._SALES_TRAFFIC_CLAIM_ADVISORY_LOCK_KEY},
            )

        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
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
            .where(_Global.run_type == "sales_and_traffic_report", _Global.status == "started")
            .scalar_subquery()
        )
        _Org = aliased(AmazonIngestionRun)
        org_active_count = (
            select(func.count())
            .select_from(_Org)
            .where(
                _Org.run_type == "sales_and_traffic_report",
                _Org.status == "started",
                _Org.organization_id == AmazonIngestionRun.organization_id,
            )
            .scalar_subquery()
        )
        candidate_id = (
            select(AmazonIngestionRun.id)
            .where(
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
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

    def heartbeat_sales_traffic_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        lease_duration_seconds: int,
        report_id: str | None = None,
        report_document_id: str | None = None,
        report_processing_status: str | None = None,
    ) -> bool:
        """Extends the lease and durably records report-lifecycle progress
        (`report_id`/`report_document_id`/`report_processing_status`) for
        an in-flight run — the exact mechanism that lets a worker
        restarted mid-poll resume the *same* report instead of calling
        `createReport` again. Compare-and-set on `(lease_owner, status=
        'started', lease_expires_at > now())`, identical guarantee to
        `heartbeat_listings_run`. Passing `None` for any of the three
        report fields leaves that column's current value unchanged
        (never overwrites a previously-recorded id with NULL)."""
        values: dict = {
            "lease_expires_at": self._lease_expiry_value(lease_duration_seconds),
            "last_heartbeat_at": func.now(),
        }
        if report_id is not None:
            values["report_id"] = report_id
        if report_document_id is not None:
            values["report_document_id"] = report_document_id
        if report_processing_status is not None:
            values["report_processing_status"] = report_processing_status
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.lease_owner == lease_owner,
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at > func.now(),
            )
            .values(**values)
        )
        self.session.flush()
        return result.rowcount == 1

    def reschedule_sales_traffic_run_for_retry(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        next_retry_at: datetime,
        failure_class: str,
    ) -> bool:
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
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    def complete_sales_traffic_run_as_failed(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        status: str,
        failure_class: str | None,
    ) -> bool:
        """Terminal, non-successful completion (`failed`/`timed_out`).
        Deliberately never accepts `status='succeeded'` — see
        `finalize_successful_sales_traffic_run`'s own docstring for why
        success and checkpoint advancement must never be split into two
        separately-callable steps."""
        if status == "succeeded":
            raise ValueError("use finalize_successful_sales_traffic_run for a successful completion")
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
                status=status,
                completed_at=func.now(),
                failure_class=failure_class,
                lease_owner=None,
                lease_expires_at=None,
            )
        )
        self.session.flush()
        return result.rowcount == 1

    def finalize_successful_sales_traffic_run(
        self,
        organization_id: UUID,
        run_id: UUID,
        *,
        lease_owner: str,
        marketplace_participation_id: UUID,
        seller_account_id: UUID,
        synced_through_date: date | None,
    ) -> bool:
        """Atomically marks a `started` sales-and-traffic run `succeeded`
        and, if `synced_through_date` is given (the daily product-level
        ingestion path only — never for a catalog-wide-only run),
        advances that one participation's checkpoint in the same
        transaction — mirroring `finalize_successful_orders_run`'s own
        "success and checkpoint advancement are never two separately-
        callable steps" discipline, simplified for this run type's
        single-participation scope (no association table to join
        through). Returns False (writing nothing) if this caller no
        longer holds the claim."""
        completed = self.complete_sales_traffic_run_terminal(
            organization_id, run_id, lease_owner=lease_owner, status="succeeded"
        )
        if not completed:
            return False
        if synced_through_date is not None:
            AmazonSalesTrafficSyncCheckpointRepository(self.session)._advance_if_run_succeeded(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=marketplace_participation_id,
                run_id=run_id,
                synced_through_date=synced_through_date,
            )
        return True

    def complete_sales_traffic_run_terminal(
        self, organization_id: UUID, run_id: UUID, *, lease_owner: str, status: str
    ) -> bool:
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.lease_owner == lease_owner,
                AmazonIngestionRun.status == "started",
                AmazonIngestionRun.lease_expires_at > func.now(),
            )
            .values(status=status, completed_at=func.now(), lease_owner=None, lease_expires_at=None)
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


@dataclass(frozen=True)
class OrdersRunClaim:
    """Outcome of an atomic Orders-run enqueue/claim attempt. Never carries
    a lease owner or any identifier beyond what the caller already
    supplied."""

    claimed: bool
    run_id: UUID | None = None
    reason: str | None = None  # "already_running" / "no_eligible_job"


class OrdersRunFinalizationIncomplete(RuntimeError):
    """Raised by `finalize_successful_orders_run` if it cannot advance the
    checkpoint for every requested participation. All-or-nothing: this is
    a hard failure, not a partial-success return value — the caller must
    roll back its transaction on this exception, which undoes the run's
    already-applied `succeeded` status flip together with every checkpoint
    this call already advanced earlier in the same batch. Never carries
    which participation failed — that would be a business identifier in
    exception text, the same standard already enforced for `amazon_order_
    id` and every other Orders identifier."""


@dataclass(frozen=True)
class OrdersRunFinalization:
    """Outcome of `finalize_successful_orders_run` when it does not raise.
    `finalized=True` means *every* requested participation's checkpoint
    was advanced — `advanced_participation_ids` always equals the full set
    of keys in the caller's `participation_watermarks` on success. A
    partial result is never returned as success; see
    `OrdersRunFinalizationIncomplete`."""

    finalized: bool
    advanced_participation_ids: tuple[UUID, ...] = ()
    reason: str | None = None  # e.g. "run_not_started" when finalized is False


class AmazonIngestionRunMarketplaceParticipationRepository:
    """Persistence primitives for 12B.4B's Orders run-scope design.

    No HTTP client, pagination, or orchestration lives here — see
    `docs/AI_HANDOVER/12B4B_ORDERS_SCHEMA.md`. The lifecycle mirrors
    Listings' durable job architecture exactly (`API/service trigger →
    queued job → worker claim → started lease → processing → terminal`):

    - `enqueue_orders_run` creates a `queued` row (no lease, no
      `started_at`) plus one `amazon_ingestion_run_marketplace_
      participations` row per covered participation — the only way an
      Orders run comes into existence. No API/service path creates a
      `started` row directly.
    - `claim_orders_run` is the *only* method that transitions a row to
      `started` — a worker-only, compare-and-set operation (mirrors
      `AmazonIngestionRunRepository.claim_next_listings_job`'s stale-reclaim
      step, simplified: this scope's own partial unique index already
      guarantees at most one non-terminal row per `(seller_account, region,
      environment)`, so claiming by exact scope rather than a global
      cross-organization candidate scan is sufficient here — see that
      method's own docstring for the fuller capacity-limited version this
      deliberately does not duplicate, per 12B.4B's own "do not duplicate
      the entire worker implementation unless essential" instruction).
    - `finalize_successful_orders_run` is the *only* method that marks a
      run `succeeded` — and it advances every included participation's
      checkpoint in the same call, atomically, rather than leaving
      checkpoint advancement as a separately-callable, permissively-gated
      operation. See that method's own docstring for the exact gating
      predicate.

    The active-scope partial unique index
    (`uq_amazon_ingestion_runs_active_orders_scope`) already covers
    `queued`, `started`, and `waiting_to_retry` together, so it is the
    single concurrency control for the whole lifecycle, not just the
    `started` state.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _validate_scope(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        connection_id: UUID,
        marketplace_participation_ids: list[UUID],
        region: str,
        environment: str,
    ) -> tuple[AmazonConnection, list[AmazonMarketplaceParticipation]]:
        seller_account = AmazonSellerAccountRepository(self.session).get_by_id(
            organization_id, seller_account_id
        )
        if seller_account is None:
            raise TypeError("Amazon orders run cannot bind a seller account from another organization.")
        connection = AmazonConnectionRepository(self.session).get_by_id(organization_id, connection_id)
        if connection is None:
            raise TypeError("Amazon orders run cannot bind a connection from another organization.")
        if connection.region != region or connection.environment != environment:
            raise TypeError(
                "Amazon orders run region/environment does not match the given connection's own region/environment."
            )
        if not marketplace_participation_ids:
            raise TypeError("Amazon orders run requires at least one marketplace participation.")
        participation_repo = AmazonMarketplaceParticipationRepository(self.session)
        participations: list[AmazonMarketplaceParticipation] = []
        for participation_id in marketplace_participation_ids:
            participation = participation_repo.get_by_id(organization_id, participation_id)
            if participation is None:
                raise TypeError(
                    "Amazon orders run cannot bind a marketplace participation from another organization."
                )
            if participation.seller_account_id != seller_account_id:
                raise TypeError(
                    "Amazon orders run marketplace participation does not belong to the given seller account."
                )
            if participation.region != region:
                raise TypeError("Amazon orders run marketplace participation does not belong to the given region.")
            if participation.connection_id != connection_id:
                raise TypeError(
                    "Amazon orders run marketplace participation is not backed by the given connection — a "
                    "PRODUCTION run cannot cover a SANDBOX-backed participation or vice versa, and every "
                    "covered participation must share the run's own connection."
                )
            participations.append(participation)
        return connection, participations

    def enqueue_orders_run(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        connection_id: UUID,
        marketplace_participation_ids: list[UUID],
        region: str,
        environment: str,
    ) -> OrdersRunClaim:
        """Creates a `queued` Orders run plus its participation-membership
        rows. No lease, no `started_at` — a worker must separately call
        `claim_orders_run` before any processing may begin. A second
        concurrent enqueue for the same `(seller_account, region,
        environment)` scope — while one is already `queued`, `started`, or
        `waiting_to_retry` — fails on `uq_amazon_ingestion_runs_active_
        orders_scope`, surfaced as `claimed=False`, the same savepoint-
        isolated pattern as `AmazonIngestionRunRepository.claim_listings_
        run`.
        """
        connection, participations = self._validate_scope(
            organization_id=organization_id,
            seller_account_id=seller_account_id,
            connection_id=connection_id,
            marketplace_participation_ids=marketplace_participation_ids,
            region=region,
            environment=environment,
        )
        run = AmazonIngestionRun(
            organization_id=organization_id,
            connection_id=connection.id,
            seller_account_id=seller_account_id,
            marketplace_participation_id=None,
            run_type="orders",
            domain="orders",
            region=region,
            environment=environment,
            status="queued",
            started_at=None,
        )
        try:
            with self.session.begin_nested():
                self.session.add(run)
                self.session.flush()
                for participation in participations:
                    self.session.add(
                        AmazonIngestionRunMarketplaceParticipation(
                            ingestion_run_id=run.id,
                            marketplace_participation_id=participation.id,
                            organization_id=organization_id,
                            seller_account_id=seller_account_id,
                            region=region,
                            connection_id=connection.id,
                        )
                    )
                self.session.flush()
        except IntegrityError:
            return OrdersRunClaim(claimed=False, reason="already_running")
        return OrdersRunClaim(claimed=True, run_id=run.id)

    def claim_orders_run(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        region: str,
        environment: str,
        lease_owner: str,
        lease_duration_seconds: int,
    ) -> OrdersRunClaim:
        """Worker-only transition of the eligible `queued`/`waiting_to_
        retry` Orders run for this exact scope to `started`. This is the
        *only* method in this codebase that writes `status='started'` for
        `run_type='orders'` — no API or service path may start work
        directly. Stale `started` rows (lease expired, no worker ever
        finished them) are reclaimed to `timed_out` first, exactly like
        `AmazonIngestionRunRepository.claim_listings_run`'s own
        stale-reclaim step, so their scope is freed before any new
        candidate is considered.

        Compare-and-set: the final `UPDATE` is conditioned on `id` *and*
        the exact `status` value observed by the preceding candidate
        `SELECT`. If a concurrent claim already won (or a stale-reclaim
        already changed the row) between the two statements, this
        `UPDATE` matches zero rows and `claimed=False` is returned — the
        row is never double-claimed. This does not use `FOR UPDATE SKIP
        LOCKED`/global capacity limiting the way `claim_next_listings_job`
        does: `uq_amazon_ingestion_runs_active_orders_scope` already
        guarantees at most one non-terminal row exists for this exact
        scope at any time, so at most one candidate can ever be found —
        the additional machinery that method needs (to fairly distribute
        *many* simultaneously-eligible candidates across workers) does not
        apply here, per 12B.4B's "do not duplicate the entire worker
        implementation unless essential" instruction.
        """
        seller_account = AmazonSellerAccountRepository(self.session).get_by_id(
            organization_id, seller_account_id
        )
        if seller_account is None:
            raise TypeError("Amazon orders run cannot bind a seller account from another organization.")

        self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.region == region,
                AmazonIngestionRun.environment == environment,
                AmazonIngestionRun.run_type == "orders",
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

        candidate = self.session.scalars(
            select(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.region == region,
                AmazonIngestionRun.environment == environment,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status.in_(("queued", "waiting_to_retry")),
                or_(
                    AmazonIngestionRun.next_retry_at.is_(None),
                    AmazonIngestionRun.next_retry_at <= func.now(),
                ),
            )
            .order_by(AmazonIngestionRun.created_at.asc())
        ).first()
        if candidate is None:
            return OrdersRunClaim(claimed=False, reason="no_eligible_job")

        was_retry = candidate.status == "waiting_to_retry"
        result = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == candidate.id,
                AmazonIngestionRun.status == candidate.status,
            )
            .values(
                status="started",
                started_at=func.coalesce(AmazonIngestionRun.started_at, func.now()),
                lease_owner=lease_owner,
                lease_expires_at=AmazonIngestionRunRepository(self.session)._lease_expiry_value(
                    lease_duration_seconds
                ),
                last_heartbeat_at=func.now(),
                retry_count=(AmazonIngestionRun.retry_count + 1) if was_retry else AmazonIngestionRun.retry_count,
            )
        )
        self.session.flush()
        if result.rowcount == 0:
            return OrdersRunClaim(claimed=False, reason="already_running")
        return OrdersRunClaim(claimed=True, run_id=candidate.id)

    def is_participation_in_run(self, ingestion_run_id: UUID, marketplace_participation_id: UUID) -> bool:
        """Membership check: did this specific run actually cover this
        specific participation? Used by tests and by
        `finalize_successful_orders_run`'s own internal predicate — never
        the sole enforcement mechanism (see that method and
        `AmazonOrdersSyncCheckpointRepository` for the SQL-predicate/
        composite-FK enforcement that holds even if this check is
        bypassed)."""
        return (
            self.session.scalars(
                select(AmazonIngestionRunMarketplaceParticipation).where(
                    AmazonIngestionRunMarketplaceParticipation.ingestion_run_id == ingestion_run_id,
                    AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id
                    == marketplace_participation_id,
                )
            ).first()
            is not None
        )

    def get_latest_orders_run_for_participation(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        """12B.4D read-API support: the most recent Orders run that
        covered this specific participation (any status) — used to build
        per-participation sync evidence even though an Orders run's own
        scope is coarser than one participation. Returns `None` if the
        participation does not belong to `organization_id` or no Orders
        run has ever covered it."""
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return None
        return self.session.scalars(
            select(AmazonIngestionRun)
            .join(
                AmazonIngestionRunMarketplaceParticipation,
                AmazonIngestionRunMarketplaceParticipation.ingestion_run_id == AmazonIngestionRun.id,
            )
            .where(
                AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
            )
            .order_by(*_LATEST_LISTINGS_RUN_ORDER_BY)
            .limit(1)
        ).first()

    def get_latest_successful_orders_run_for_participation(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonIngestionRun | None:
        """Same tie-safe ordering as
        `AmazonIngestionRunRepository.get_latest_successful_orders_run`
        — every matched row already has `status='succeeded'`, so
        `started_at` is guaranteed non-null."""
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return None
        return self.session.scalars(
            select(AmazonIngestionRun)
            .join(
                AmazonIngestionRunMarketplaceParticipation,
                AmazonIngestionRunMarketplaceParticipation.ingestion_run_id == AmazonIngestionRun.id,
            )
            .where(
                AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status == "succeeded",
            )
            .order_by(AmazonIngestionRun.started_at.desc(), AmazonIngestionRun.id.desc())
            .limit(1)
        ).first()

    def finalize_successful_orders_run(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        ingestion_run_id: UUID,
        participation_watermarks: dict[UUID, datetime],
    ) -> OrdersRunFinalization:
        """The one atomic primitive that both completes an Orders run and
        advances every included participation's checkpoint — steps 2-4 of
        12B.4B's required transaction design, in one call, one transaction:

        1. (caller's responsibility, not repeated here) the run's active
           lease and scope were already verified at claim time.
        2. Marks the run `succeeded` with `completed_at=now()` — but ONLY
           if it is currently `started` (a single guarded `UPDATE ... WHERE
           id=:id AND run_type='orders' AND status='started'`; zero rows
           affected means rejection — a SQL predicate, not a Python
           status check, decides this).
        3. Clears `lease_owner`/`lease_expires_at`/`next_retry_at` as part
           of that same `UPDATE`.
        4. For every `(participation_id, watermark)` pair supplied,
           advances that participation's checkpoint — gated by a second
           SQL predicate requiring `amazon_ingestion_runs.status=
           'succeeded'` (now true, from step 2, same transaction, same
           session — no intervening commit) AND `run_type='orders'` AND
           `completed_at IS NOT NULL` AND organization/seller ownership
           AND (via `amazon_ingestion_run_marketplace_participations`,
           itself composite-FK-enforced) that this run actually covered
           this participation.
        5. The caller commits (or rolls back) — this method never calls
           `session.commit()` itself, matching this module's existing
           convention; if the caller's commit fails or is never issued,
           steps 2 and 4 are rolled back together, so the run is never
           left `succeeded` with an unadvanced checkpoint or vice versa.

        Returns `finalized=False` (never raises for an ordinary status
        mismatch, and never touches any checkpoint) if the run does not
        exist, does not belong to this organization/seller, is not
        `run_type='orders'`, or is not currently `started` — covering
        `queued`, `waiting_to_retry`, `failed`, `partial`, `timed_out`, a
        `cancelled_before_start` `failed` row, a `listings` run, and a
        `marketplace_participations` run, uniformly, through the same one
        predicate, not a special case per status value.

        **All-or-nothing for step 4.** If any `(participation_id,
        watermark)` pair in `participation_watermarks` fails its own
        eligibility check (most notably: a participation the run did not
        actually cover — a caller bug, since every participation named
        here should have come from the same run's own membership), this
        method raises `OrdersRunFinalizationIncomplete` instead of
        returning `finalized=True` with only the ones that succeeded.
        This is a deliberate, hard failure: the run-succeeded `UPDATE` from
        step 2 and every checkpoint already advanced earlier in this same
        loop are still uncommitted in this same transaction when the
        exception is raised, so the caller's own rollback (never a
        swallow-and-commit) undoes all of it together — a caller must
        never be able to observe a `succeeded` run with only some of its
        covered participations' checkpoints actually advanced.
        """
        run_completion = self.session.execute(
            update(AmazonIngestionRun)
            .where(
                AmazonIngestionRun.id == ingestion_run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status == "started",
            )
            .values(
                status="succeeded",
                completed_at=func.now(),
                lease_owner=None,
                lease_expires_at=None,
                next_retry_at=None,
                # 12B.4D remediation: this run has reached a terminal
                # state — clear the durable continuation token the same
                # way `complete_orders_run_as_failed` does for the
                # non-successful terminal path. Pagination is already
                # known complete by the time this method is ever called
                # (see `AmazonOrdersIngestionService._finalize`), so this
                # is normally a no-op write, kept explicit rather than
                # assumed.
                orders_pagination_next_token=None,
            )
        )
        self.session.flush()
        if run_completion.rowcount == 0:
            return OrdersRunFinalization(finalized=False, reason="run_not_started")

        checkpoint_repo = AmazonOrdersSyncCheckpointRepository(self.session)
        advanced: list[UUID] = []
        for participation_id, watermark in participation_watermarks.items():
            if not checkpoint_repo._advance_if_run_succeeded(
                organization_id=organization_id,
                seller_account_id=seller_account_id,
                marketplace_participation_id=participation_id,
                ingestion_run_id=ingestion_run_id,
                synced_through_at=watermark,
            ):
                # All-or-nothing: raising here (rather than skipping this
                # one participation and returning finalized=True for the
                # rest) is deliberate. The run-succeeded UPDATE above and
                # every checkpoint write already performed in this loop
                # are still uncommitted, in this same transaction — the
                # caller must roll back on this exception (never catch it
                # and commit anyway), which undoes the run's status flip
                # together with every checkpoint this call already
                # advanced. A caller must never observe `finalized=True`
                # with only some of the requested participations actually
                # advanced; either the whole batch succeeds, or none of it
                # is left committed.
                raise OrdersRunFinalizationIncomplete(
                    "Amazon orders run finalization cannot advance a checkpoint for a participation "
                    "the run did not actually cover — refusing to report partial success. Roll back "
                    "this transaction; the run must not be left succeeded with only some checkpoints "
                    "advanced."
                )
            advanced.append(participation_id)
        return OrdersRunFinalization(finalized=True, advanced_participation_ids=tuple(advanced))


# 12B.4B remediation (round 2) — `NUMERIC(19,4)` on real PostgreSQL does
# NOT reject a value with more than 4 fractional digits; it silently
# *rounds* it at type-coercion time (`numeric_field_overflow` is only
# raised for excess *magnitude* — an integer part needing more than
# precision-scale = 15 digits — never for excess scale). Relying on the
# database to reject excess precision would be relying on behavior
# PostgreSQL does not have. This validation is therefore the actual,
# only enforcement point for "excess scale rejected rather than silently
# rounded" — called before any Orders monetary value is bound into an
# INSERT/UPDATE statement, in both `AmazonSellerOrderRepository.upsert`
# and `AmazonSellerOrderItemRepository.upsert`.
_MONEY_PRECISION = 19
_MONEY_SCALE = 4
_MONEY_MAGNITUDE_LIMIT = Decimal(10) ** (_MONEY_PRECISION - _MONEY_SCALE)  # 10**15


def _validate_orders_money_amount(value: Decimal | None, *, field_name: str) -> None:
    """Repository/DTO write-boundary validation for every `Numeric(19,4)`
    Orders amount column (`order_total_amount`, `unit_price_amount`,
    `item_proceeds_amount`). Raises `TypeError` for a non-`Decimal` value
    (a `float` is explicitly rejected, never silently accepted and
    implicitly converted — Amazon's own wire format is a lossless decimal
    string specifically to avoid float rounding, so admitting a float here
    would reintroduce the exact class of error this column type exists to
    avoid) and `ValueError` for a `Decimal` with more than 4 fractional
    digits or a magnitude PostgreSQL's real `NUMERIC(19,4)` would reject
    with `numeric_field_overflow`. `None` (absence) always passes.
    """
    if value is None:
        return
    if isinstance(value, float):
        raise TypeError(
            f"Amazon Orders monetary field {field_name!r} must be a Decimal, never a float — "
            "float cannot represent Amazon's own lossless decimal wire format exactly."
        )
    if not isinstance(value, Decimal):
        raise TypeError(f"Amazon Orders monetary field {field_name!r} must be a Decimal.")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        # A non-int exponent means a special value (NaN/sNaN/Infinity) —
        # Decimal.as_tuple() returns the strings 'n'/'N'/'F' for those.
        raise ValueError(f"Amazon Orders monetary field {field_name!r} must be a finite Decimal.")
    if exponent < -_MONEY_SCALE:
        raise ValueError(
            f"Amazon Orders monetary field {field_name!r} has more than {_MONEY_SCALE} fractional "
            "digits — PostgreSQL's NUMERIC(19,4) would silently round this rather than reject it, "
            "so it is rejected here instead, before any SQL is executed."
        )
    if abs(value) >= _MONEY_MAGNITUDE_LIMIT:
        raise ValueError(
            f"Amazon Orders monetary field {field_name!r} exceeds NUMERIC(19,4)'s representable "
            "magnitude."
        )


class AmazonSellerOrderRepository:
    """Marketplace-participation-scoped canonical orders. 12B.4B.

    Mirrors `AmazonSellerListingRepository`'s ownership pattern exactly:
    `amazon_seller_orders` has no `organization_id` column by design, so
    every public method takes `organization_id` and validates it against
    `marketplace_participation_id` via `AmazonMarketplaceParticipationRepository.
    get_by_id` before touching any row. Possession of a participation UUID
    alone is never sufficient.

    `upsert` is the one validated write path (idempotent by
    `(marketplace_participation_id, amazon_order_id)`). It intentionally
    accepts only an explicit, named-field parameter list — never a raw
    parsed-response object — so it is structurally impossible to pass
    through an unrecognized/PII field this repository was never told about
    (see docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md's bundled-
    field-redaction section). There is no field on this signature, and
    none on `AmazonSellerOrder`, for buyer/recipient/payment/tax data, a
    gift message, or a cancellation reason.
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
            raise TypeError(
                "Amazon seller order access cannot bind a marketplace participation from another organization."
            )

    def get_by_natural_key(
        self, organization_id: UUID, marketplace_participation_id: UUID, amazon_order_id: str
    ) -> AmazonSellerOrder | None:
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        return self._get_by_natural_key_unchecked(marketplace_participation_id, amazon_order_id)

    def _get_by_natural_key_unchecked(
        self, marketplace_participation_id: UUID, amazon_order_id: str
    ) -> AmazonSellerOrder | None:
        return self.session.scalars(
            select(AmazonSellerOrder).where(
                AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id,
                AmazonSellerOrder.amazon_order_id == amazon_order_id,
            )
        ).first()

    def list_for_participation(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> list[AmazonSellerOrder]:
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        statement: Select[tuple[AmazonSellerOrder]] = (
            select(AmazonSellerOrder)
            .where(AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id)
            .order_by(AmazonSellerOrder.amazon_created_at.desc().nullslast())
        )
        return list(self.session.scalars(statement).all())

    def get_max_last_updated_at_by_participation(
        self, organization_id: UUID, ingestion_run_id: UUID
    ) -> dict[UUID, datetime]:
        """12B.4D remediation: the true highest `amazon_last_updated_at`
        committed by this specific run, grouped by
        `marketplace_participation_id` — computed fresh from the database
        rather than accumulated in memory across a traversal, because a
        resumed attempt only re-fetches pages *after* its saved resume
        point and therefore never re-sees orders a prior, interrupted
        attempt of the *same run* already committed on earlier pages.
        Every page of every attempt of one run is upserted with the same
        `last_ingestion_run_id` (see `AmazonOrdersIngestionService.
        _persist_page`), so this aggregate is correct regardless of how
        many attempts it took to complete. Internal to ingestion
        finalization — takes an `ingestion_run_id` directly rather than a
        caller-supplied participation, so no organization-ownership check
        is needed beyond the `organization_id` filter already present in
        the query (this run's own scope was already validated at claim
        time)."""
        rows = self.session.execute(
            select(
                AmazonSellerOrder.marketplace_participation_id,
                func.max(AmazonSellerOrder.amazon_last_updated_at),
            )
            .join(
                AmazonMarketplaceParticipation,
                AmazonMarketplaceParticipation.id == AmazonSellerOrder.marketplace_participation_id,
            )
            .where(
                AmazonSellerOrder.last_ingestion_run_id == ingestion_run_id,
                AmazonSellerOrder.amazon_last_updated_at.is_not(None),
                AmazonMarketplaceParticipation.organization_id == organization_id,
            )
            .group_by(AmazonSellerOrder.marketplace_participation_id)
        ).all()
        # See `freeze_orders_window_if_needed`'s comment: SQLite does not
        # round-trip `tzinfo` on a `DateTime(timezone=True)` column, so
        # normalize each aggregate result to stay comparable against the
        # (already tz-aware) frozen `orders_window_captured_at` value.
        return {
            participation_id: (max_updated_at if max_updated_at.tzinfo is not None else max_updated_at.replace(tzinfo=UTC))
            for participation_id, max_updated_at in rows
        }

    def upsert(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        amazon_order_id: str,
        fulfillment_status: str | None,
        fulfilled_by: str | None,
        sales_channel_name: str | None,
        sales_channel_marketplace_id: str | None,
        sales_channel_marketplace_name: str | None,
        items_shipped_count: int | None,
        items_unshipped_count: int | None,
        order_total_amount: Decimal | None,
        order_total_currency: str | None,
        is_business_order: bool,
        is_prime: bool,
        was_cancelled: bool,
        amazon_created_at: datetime | None,
        amazon_last_updated_at: datetime | None,
        last_ingestion_run_id: UUID,
    ) -> AmazonSellerOrder:
        """Upsert by `(marketplace_participation_id, amazon_order_id)`.
        Preserves `first_seen_at`. `last_ingestion_run_id` must already
        cover this `marketplace_participation_id` in
        `amazon_ingestion_run_marketplace_participations` — enforced by the
        database's own composite foreign key
        (`fk_amazon_seller_orders_last_run_same_participation`), not
        re-validated here; an `IntegrityError` from a genuine caller bug
        propagates rather than being silently swallowed.
        """
        _validate_orders_money_amount(order_total_amount, field_name="order_total_amount")
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        now = datetime.now(UTC)
        existing = self._get_by_natural_key_unchecked(marketplace_participation_id, amazon_order_id)
        if existing is not None:
            existing.fulfillment_status = fulfillment_status
            existing.fulfilled_by = fulfilled_by
            existing.sales_channel_name = sales_channel_name
            existing.sales_channel_marketplace_id = sales_channel_marketplace_id
            existing.sales_channel_marketplace_name = sales_channel_marketplace_name
            existing.items_shipped_count = items_shipped_count
            existing.items_unshipped_count = items_unshipped_count
            existing.order_total_amount = order_total_amount
            existing.order_total_currency = order_total_currency
            existing.is_business_order = is_business_order
            existing.is_prime = is_prime
            existing.was_cancelled = was_cancelled
            existing.amazon_created_at = amazon_created_at
            existing.amazon_last_updated_at = amazon_last_updated_at
            existing.last_ingestion_run_id = last_ingestion_run_id
            existing.last_seen_at = now
            self.session.flush()
            return existing
        row = AmazonSellerOrder(
            marketplace_participation_id=marketplace_participation_id,
            amazon_order_id=amazon_order_id,
            fulfillment_status=fulfillment_status,
            fulfilled_by=fulfilled_by,
            sales_channel_name=sales_channel_name,
            sales_channel_marketplace_id=sales_channel_marketplace_id,
            sales_channel_marketplace_name=sales_channel_marketplace_name,
            items_shipped_count=items_shipped_count,
            items_unshipped_count=items_unshipped_count,
            order_total_amount=order_total_amount,
            order_total_currency=order_total_currency,
            is_business_order=is_business_order,
            is_prime=is_prime,
            was_cancelled=was_cancelled,
            amazon_created_at=amazon_created_at,
            amazon_last_updated_at=amazon_last_updated_at,
            last_ingestion_run_id=last_ingestion_run_id,
        )
        self.session.add(row)
        self.session.flush()
        return row

    # --- 12B.4D: read API support --------------------------------------

    _ORDERS_SORT_COLUMNS: dict[str, Any] = {
        "amazon_last_updated_at": AmazonSellerOrder.amazon_last_updated_at,
        "amazon_created_at": AmazonSellerOrder.amazon_created_at,
        "order_total_amount": AmazonSellerOrder.order_total_amount,
    }

    def get_summary_counts(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> "OrdersSummaryCounts | None":
        """Mirrors `AmazonSellerListingRepository.get_summary_counts`'
        one-aggregate-query shape."""
        if AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        ) is None:
            return None

        row = self.session.execute(
            select(
                func.count().label("total"),
                func.count().filter(AmazonSellerOrder.was_cancelled.is_(True)).label("cancelled"),
                func.count().filter(AmazonSellerOrder.is_business_order.is_(True)).label("business"),
                func.count().filter(AmazonSellerOrder.is_prime.is_(True)).label("prime"),
                # 12B.4D remediation: excludes any row whose amount is
                # known but currency is not — summing an amount with an
                # unknown currency into a total the caller then labels
                # with *some other* known currency would misrepresent
                # that unknown-currency amount as if it were in that
                # currency. `AmazonOrdersReadService.get_summary`'s own
                # currency-consistency check (a separate query over
                # distinct non-null currencies) reasons about exactly
                # this same excluded set, so the two stay in agreement:
                # a participation with only one known currency plus one
                # amount-with-unknown-currency order correctly reports
                # that one known currency's true total, never a total
                # silently inflated by the unknown-currency amount.
                func.coalesce(
                    func.sum(AmazonSellerOrder.order_total_amount).filter(
                        AmazonSellerOrder.order_total_currency.is_not(None)
                    ),
                    0,
                ).label("order_value_sum"),
            ).where(AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id)
        ).one()

        status_counts = dict(
            self.session.execute(
                select(AmazonSellerOrder.fulfillment_status, func.count())
                .where(AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id)
                .group_by(AmazonSellerOrder.fulfillment_status)
            ).all()
        )

        return OrdersSummaryCounts(
            total=row.total,
            cancelled=row.cancelled,
            business=row.business,
            prime=row.prime,
            order_value_sum=row.order_value_sum,
            status_counts=status_counts,
        )

    def list_page(
        self,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        *,
        search: str | None = None,
        fulfillment_status: str | None = None,
        fulfilled_by: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        sort_by: str = "amazon_last_updated_at",
        sort_dir: str = "desc",
        offset: int = 0,
        limit: int = 25,
    ) -> "tuple[list[AmazonSellerOrder], int] | None":
        """Validated, filtered, deterministically-ordered orders page,
        scoped to one organization-owned marketplace participation.
        Mirrors `AmazonSellerListingRepository.list_page`'s ordering/
        search/validation conventions exactly (NULLS LAST + `id ASC`
        tie-break, `_escape_like_term`-sanitized `ILIKE`). `search`
        matches the Amazon order id directly, or (via an `EXISTS`
        subquery — order items are a separate table) any item's seller
        SKU or ASIN on that order."""
        if AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        ) is None:
            return None
        if sort_by not in self._ORDERS_SORT_COLUMNS:
            raise ValueError(f"Unsupported orders sort field: {sort_by!r}")
        if sort_dir not in ("asc", "desc"):
            raise ValueError(f"Unsupported orders sort direction: {sort_dir!r}")

        filters = [AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id]
        search = (search or "").strip()
        if search:
            term = f"%{_escape_like_term(search)}%"
            item_match = (
                select(AmazonSellerOrderItem.id)
                .where(
                    AmazonSellerOrderItem.order_id == AmazonSellerOrder.id,
                    or_(
                        AmazonSellerOrderItem.seller_sku.ilike(term, escape=_LIKE_ESCAPE_CHAR),
                        AmazonSellerOrderItem.asin.ilike(term, escape=_LIKE_ESCAPE_CHAR),
                    ),
                )
                .exists()
            )
            filters.append(or_(AmazonSellerOrder.amazon_order_id.ilike(term, escape=_LIKE_ESCAPE_CHAR), item_match))
        if fulfillment_status is not None:
            filters.append(AmazonSellerOrder.fulfillment_status == fulfillment_status)
        if fulfilled_by is not None:
            filters.append(AmazonSellerOrder.fulfilled_by == fulfilled_by)
        if created_after is not None:
            filters.append(AmazonSellerOrder.amazon_created_at >= created_after)
        if created_before is not None:
            filters.append(AmazonSellerOrder.amazon_created_at <= created_before)

        total = self.session.scalar(select(func.count()).select_from(AmazonSellerOrder).where(*filters)) or 0

        sort_column = self._ORDERS_SORT_COLUMNS[sort_by]
        order = (sort_column.desc() if sort_dir == "desc" else sort_column.asc()).nulls_last()
        statement = (
            select(AmazonSellerOrder)
            .where(*filters)
            .order_by(order, AmazonSellerOrder.id.asc())
            .offset(offset)
            .limit(limit)
        )
        rows = list(self.session.scalars(statement).all())
        return rows, int(total)

    def get_order_detail(
        self, organization_id: UUID, marketplace_participation_id: UUID, order_id: UUID
    ) -> AmazonSellerOrder | None:
        """Read-API variant: returns `None` (never raises) for a foreign
        or nonexistent participation — unlike `_require_participation_in_
        organization` (used by the ingestion-write paths, where a
        mismatched participation is an internal caller bug), a read
        request against someone else's participation id is an ordinary,
        expected occurrence that must produce the same sanitized
        not-found result as a genuinely nonexistent order, matching
        `AmazonSellerListingRepository.get_detail`'s contract."""
        if AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        ) is None:
            return None
        return self.session.scalars(
            select(AmazonSellerOrder).where(
                AmazonSellerOrder.id == order_id,
                AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id,
            )
        ).first()


@dataclass(frozen=True)
class OrdersSummaryCounts:
    """Plain aggregate counts for one marketplace participation's orders.
    12B.4D read API. Never carries an identifier — purely counts.
    `order_value_sum` is a plain `Decimal` sum of `order_total_amount`
    across every order in scope — deliberately not currency-aware (an
    organization with orders in multiple currencies would sum
    incompatible units); the read service only surfaces this figure when
    every order in scope shares one currency, otherwise it is omitted
    (never silently summed across currencies and presented as one
    number)."""

    total: int
    cancelled: int
    business: int
    prime: int
    order_value_sum: Decimal
    status_counts: dict[str | None, int]


class AmazonSellerOrderItemRepository:
    """Order-scoped canonical order items. 12B.4B.

    Ownership flows through the parent order: every public method takes
    `organization_id` and `marketplace_participation_id` and validates
    ownership the same way `AmazonSellerOrderRepository` does, before ever
    touching an item row. `upsert` is the one validated write path,
    idempotent by `(order_id, amazon_order_item_id)` — the evidenced
    natural key from the pinned contract, never invented from `seller_sku`/
    `asin` alone. Like `AmazonSellerOrderRepository.upsert`, this accepts
    only an explicit, named-field parameter list — there is no field here,
    and none on `AmazonSellerOrderItem`, for a gift message or a
    cancellation reason.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_order_in_organization(
        self, organization_id: UUID, marketplace_participation_id: UUID, order_id: UUID
    ) -> AmazonSellerOrder:
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon seller order item access cannot bind a marketplace participation from another organization."
            )
        order = self.session.scalars(
            select(AmazonSellerOrder).where(
                AmazonSellerOrder.id == order_id,
                AmazonSellerOrder.marketplace_participation_id == marketplace_participation_id,
            )
        ).first()
        if order is None:
            raise TypeError("Amazon seller order item access cannot bind an order from another participation.")
        return order

    def _get_by_natural_key_unchecked(
        self, order_id: UUID, amazon_order_item_id: str
    ) -> AmazonSellerOrderItem | None:
        return self.session.scalars(
            select(AmazonSellerOrderItem).where(
                AmazonSellerOrderItem.order_id == order_id,
                AmazonSellerOrderItem.amazon_order_item_id == amazon_order_item_id,
            )
        ).first()

    def list_for_order(
        self, organization_id: UUID, marketplace_participation_id: UUID, order_id: UUID
    ) -> list[AmazonSellerOrderItem]:
        self._require_order_in_organization(organization_id, marketplace_participation_id, order_id)
        statement: Select[tuple[AmazonSellerOrderItem]] = (
            select(AmazonSellerOrderItem)
            .where(AmazonSellerOrderItem.order_id == order_id)
            .order_by(AmazonSellerOrderItem.amazon_order_item_id.asc())
        )
        return list(self.session.scalars(statement).all())

    def list_items_for_window(
        self,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        *,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 2000,
    ) -> list[tuple[AmazonSellerOrderItem, AmazonSellerOrder]] | None:
        """12B.5A — every order item in this participation whose parent
        order's `amazon_created_at` falls in `[created_after, created_before)`,
        paired with its parent order. Returns `None` (never raises) for a
        foreign or nonexistent participation, matching `AmazonSellerOrder
        Repository.get_summary_counts`'s own contract — a caller-visible
        404, not an internal error.

        Added for the 12B.5A Copilot skills, which need item-level
        aggregation (units/exposure per SKU, fulfillment-status
        distribution) across every order in a window — data `list_orders`
        deliberately does not expose per row (`OrderCollectionItem` carries
        only `item_count`, never the items themselves, to keep that
        endpoint's response bounded). One JOIN query here, ownership-
        validated exactly like every other method in this module, is the
        smallest extension that avoids either an N+1 `get_order()` call
        per order or letting the Copilot layer touch these ORM models
        directly. `limit` is a defensive ceiling only (2000 covers any
        realistic single-window seller volume today) — never raised
        silently past what a caller explicitly asks for.
        """
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return None
        conditions = [AmazonSellerOrderItem.marketplace_participation_id == marketplace_participation_id]
        if created_after is not None:
            conditions.append(AmazonSellerOrder.amazon_created_at >= created_after)
        if created_before is not None:
            conditions.append(AmazonSellerOrder.amazon_created_at < created_before)
        statement = (
            select(AmazonSellerOrderItem, AmazonSellerOrder)
            .join(AmazonSellerOrder, AmazonSellerOrder.id == AmazonSellerOrderItem.order_id)
            .where(*conditions)
            .order_by(AmazonSellerOrder.amazon_created_at.asc())
            .limit(limit)
        )
        return list(self.session.execute(statement).tuples().all())

    def upsert(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        order_id: UUID,
        amazon_order_item_id: str,
        seller_sku: str,
        asin: str | None,
        item_name: str | None,
        condition_type: str | None,
        quantity_ordered: int,
        quantity_fulfilled: int | None,
        quantity_unfulfilled: int | None,
        unit_price_amount: Decimal | None,
        unit_price_currency: str | None,
        item_proceeds_amount: Decimal | None,
        item_proceeds_currency: str | None,
        last_ingestion_run_id: UUID,
    ) -> AmazonSellerOrderItem:
        _validate_orders_money_amount(unit_price_amount, field_name="unit_price_amount")
        _validate_orders_money_amount(item_proceeds_amount, field_name="item_proceeds_amount")
        self._require_order_in_organization(organization_id, marketplace_participation_id, order_id)
        now = datetime.now(UTC)
        existing = self._get_by_natural_key_unchecked(order_id, amazon_order_item_id)
        if existing is not None:
            existing.seller_sku = seller_sku
            existing.asin = asin
            existing.item_name = item_name
            existing.condition_type = condition_type
            existing.quantity_ordered = quantity_ordered
            existing.quantity_fulfilled = quantity_fulfilled
            existing.quantity_unfulfilled = quantity_unfulfilled
            existing.unit_price_amount = unit_price_amount
            existing.unit_price_currency = unit_price_currency
            existing.item_proceeds_amount = item_proceeds_amount
            existing.item_proceeds_currency = item_proceeds_currency
            existing.last_ingestion_run_id = last_ingestion_run_id
            existing.last_seen_at = now
            self.session.flush()
            return existing
        row = AmazonSellerOrderItem(
            order_id=order_id,
            marketplace_participation_id=marketplace_participation_id,
            amazon_order_item_id=amazon_order_item_id,
            seller_sku=seller_sku,
            asin=asin,
            item_name=item_name,
            condition_type=condition_type,
            quantity_ordered=quantity_ordered,
            quantity_fulfilled=quantity_fulfilled,
            quantity_unfulfilled=quantity_unfulfilled,
            unit_price_amount=unit_price_amount,
            unit_price_currency=unit_price_currency,
            item_proceeds_amount=item_proceeds_amount,
            item_proceeds_currency=item_proceeds_currency,
            last_ingestion_run_id=last_ingestion_run_id,
        )
        self.session.add(row)
        self.session.flush()
        return row


class AmazonOrdersSyncCheckpointRepository:
    """Durable, per-participation Orders high-water-mark primitives. 12B.4B
    (remediated).

    No overlap-window policy, pagination, or scheduling lives here — this
    class only ever reads the stored watermark publicly. There is
    deliberately **no public, permissive "advance whenever" method** — the
    only way a checkpoint's watermark ever moves is through
    `AmazonIngestionRunMarketplaceParticipationRepository.
    finalize_successful_orders_run`, which calls this class's private
    `_advance_if_run_succeeded` immediately after (same transaction, same
    session) marking the covering run `succeeded`. This is a deliberate
    remediation of an earlier design that exposed a public `advance()`
    method gated only by "the run covered this participation," not by the
    run's own terminal status — that design could accept a checkpoint
    advance for a merely `started` (or `queued`, `waiting_to_retry`,
    `failed`, `partial`, `timed_out`) run, permanently skipping order
    updates if a caller ever invoked it too early or on the wrong run.

    `_advance_if_run_succeeded`'s eligibility check is one SQL query — not
    a Python-only status check — joining
    `amazon_ingestion_run_marketplace_participations` to
    `amazon_ingestion_runs` and requiring `run_type='orders'`,
    `status='succeeded'`, `completed_at IS NOT NULL`, and matching
    organization/seller — so the guarantee holds even if a future caller
    reaches this method directly instead of through
    `finalize_successful_orders_run` (exactly what
    `tests/test_amazon_seller_orders_schema.py`'s rejection tests exercise
    directly). The checkpoint row's own composite foreign key
    (`fk_amazon_orders_sync_checkpoints_run_same_participation`) enforces
    the run/participation membership fact again at the database level as a
    second, independent layer.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_participation_in_organization(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonMarketplaceParticipation:
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon orders sync checkpoint access cannot bind a marketplace participation from "
                "another organization."
            )
        return participation

    def get(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonOrdersSyncCheckpoint | None:
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        return self.session.get(AmazonOrdersSyncCheckpoint, marketplace_participation_id)

    def _advance_if_run_succeeded(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
        ingestion_run_id: UUID,
        synced_through_at: datetime,
    ) -> bool:
        """Private. Returns `True` if the checkpoint was created/updated,
        `False` if the gating predicate was not satisfied — the run does
        not exist, is not `run_type='orders'`, is not `status='succeeded'`,
        has no `completed_at`, does not belong to the given organization/
        seller, or never covered this participation. Never raises for an
        ordinary ineligibility case; `False` is the expected, correct
        result for every rejection scenario this method is tested against.

        Never moves the watermark backward: if the stored value is already
        strictly after `synced_through_at` (a stale/replayed call with an
        older watermark), the existing value is left untouched. An equal
        watermark is idempotent — rewriting the same value and provenance
        is treated as success, not a backward move.
        """
        eligible = self.session.execute(
            select(AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id)
            .select_from(AmazonIngestionRunMarketplaceParticipation)
            .join(
                AmazonIngestionRun,
                AmazonIngestionRun.id == AmazonIngestionRunMarketplaceParticipation.ingestion_run_id,
            )
            .where(
                AmazonIngestionRunMarketplaceParticipation.ingestion_run_id == ingestion_run_id,
                AmazonIngestionRunMarketplaceParticipation.marketplace_participation_id
                == marketplace_participation_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.run_type == "orders",
                AmazonIngestionRun.status == "succeeded",
                AmazonIngestionRun.completed_at.is_not(None),
            )
        ).first()
        if eligible is None:
            return False

        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return False

        existing = self.session.get(AmazonOrdersSyncCheckpoint, marketplace_participation_id)
        if existing is None:
            self.session.add(
                AmazonOrdersSyncCheckpoint(
                    marketplace_participation_id=marketplace_participation_id,
                    organization_id=organization_id,
                    seller_account_id=participation.seller_account_id,
                    synced_through_at=synced_through_at,
                    last_successful_run_id=ingestion_run_id,
                )
            )
            self.session.flush()
            return True
        # SQLite (unlike PostgreSQL) does not round-trip `tzinfo` on a
        # `DateTime(timezone=True)` column — a value read back is naive but
        # still genuinely UTC (this application never stores any other
        # zone). Normalize before comparing so this comparison behaves
        # identically on both backends, the same pattern already used for
        # lease-expiry comparisons elsewhere in this module.
        stored = existing.synced_through_at
        if stored is not None and stored.tzinfo is None:
            stored = stored.replace(tzinfo=UTC)
        if stored is None or synced_through_at >= stored:
            existing.synced_through_at = synced_through_at
            existing.last_successful_run_id = ingestion_run_id
        self.session.flush()
        return True


class AmazonSalesTrafficSyncCheckpointRepository:
    """Durable, per-participation Sales and Traffic high-water-mark
    primitives, for the product-level daily ingestion path only. 12B.6A.

    Mirrors `AmazonOrdersSyncCheckpointRepository`'s own discipline
    exactly: no public, permissive "advance whenever" method — the only
    way this watermark ever moves is through
    `AmazonIngestionRunRepository.finalize_successful_sales_traffic_run`,
    which calls this class's private `_advance_if_run_succeeded`
    immediately after (same transaction, same session) marking the
    covering run `succeeded`. Simplified relative to Orders' own version
    in one respect only: this run type has no association table to join
    through (§ scope note on `enqueue_sales_traffic_run`), so eligibility
    is a direct `amazon_ingestion_runs` row check, not a join.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_participation_in_organization(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonMarketplaceParticipation:
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon sales traffic sync checkpoint access cannot bind a marketplace participation "
                "from another organization."
            )
        return participation

    def get(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> AmazonSalesAndTrafficSyncCheckpoint | None:
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        return self.session.get(AmazonSalesAndTrafficSyncCheckpoint, marketplace_participation_id)

    def _advance_if_run_succeeded(
        self,
        *,
        organization_id: UUID,
        seller_account_id: UUID,
        marketplace_participation_id: UUID,
        run_id: UUID,
        synced_through_date: date,
    ) -> bool:
        """Private. Returns `True` if the checkpoint was created/updated,
        `False` if the gating predicate was not satisfied. Never moves the
        watermark backward — an equal date is idempotent, a strictly
        earlier one is silently ignored, matching
        `AmazonOrdersSyncCheckpointRepository`'s own semantics."""
        eligible = self.session.execute(
            select(AmazonIngestionRun.id).where(
                AmazonIngestionRun.id == run_id,
                AmazonIngestionRun.organization_id == organization_id,
                AmazonIngestionRun.seller_account_id == seller_account_id,
                AmazonIngestionRun.marketplace_participation_id == marketplace_participation_id,
                AmazonIngestionRun.run_type == "sales_and_traffic_report",
                AmazonIngestionRun.status == "succeeded",
                AmazonIngestionRun.completed_at.is_not(None),
            )
        ).first()
        if eligible is None:
            return False

        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return False

        existing = self.session.get(AmazonSalesAndTrafficSyncCheckpoint, marketplace_participation_id)
        if existing is None:
            self.session.add(
                AmazonSalesAndTrafficSyncCheckpoint(
                    marketplace_participation_id=marketplace_participation_id,
                    organization_id=organization_id,
                    seller_account_id=participation.seller_account_id,
                    synced_through_date=synced_through_date,
                    last_successful_run_id=run_id,
                )
            )
            self.session.flush()
            return True
        if existing.synced_through_date is None or synced_through_date >= existing.synced_through_date:
            existing.synced_through_date = synced_through_date
            existing.last_successful_run_id = run_id
        self.session.flush()
        return True


def _validate_sales_traffic_money_amount(amount: Decimal | None, *, field_name: str) -> None:
    if amount is None:
        return
    if amount != round(amount, 4):
        raise TypeError(f"Amazon sales traffic {field_name} must not carry more than 4 fractional digits.")


class AmazonSalesTrafficDailyFactRepository:
    """Idempotent upsert for catalog-wide, dated Sales and Traffic facts.
    12B.6A. Mirrors `AmazonSellerOrderRepository.upsert`'s own discipline:
    an explicit, named-field-only signature (never a raw parsed-response
    object passed through, so an unapproved field cannot be smuggled in),
    an ownership check on every call, and `first_seen_at`/`last_seen_at`
    preserved-vs-updated distinction is not needed here (this table has
    no such columns — a fact row is either freshly written or replaced
    wholesale on a later, more-authoritative fetch of the identical
    window; there is no notion of "still present" vs. "no longer
    present" the way a Listings snapshot has)."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_participation_in_organization(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> None:
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon sales traffic daily fact cannot bind a marketplace participation from another "
                "organization."
            )

    def upsert(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        report_date: date,
        date_granularity: str,
        last_ingestion_run_id: UUID,
        fields: dict[str, Any],
    ) -> AmazonSalesAndTrafficDailyFact:
        """`fields` must be an already-validated, already-allowlisted
        mapping of this model's own approved column names (built by the
        parser in `app.amazon.sales_traffic_parser` — never a raw Amazon
        response dict) — this method never inspects or trusts key names
        beyond passing them to the ORM constructor/assignment, so a typo'd
        key fails loudly (an `AttributeError`/`TypeError`), never
        silently drops or smuggles a field."""
        for money_field in (
            "ordered_product_sales_amount",
            "ordered_product_sales_amount_b2b",
            "average_sales_per_order_item_amount",
            "average_sales_per_order_item_amount_b2b",
            "average_selling_price_amount",
            "average_selling_price_amount_b2b",
            "claims_amount",
            "shipped_product_sales_amount",
        ):
            _validate_sales_traffic_money_amount(fields.get(money_field), field_name=money_field)
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        existing = self.session.execute(
            select(AmazonSalesAndTrafficDailyFact).where(
                AmazonSalesAndTrafficDailyFact.marketplace_participation_id == marketplace_participation_id,
                AmazonSalesAndTrafficDailyFact.report_date == report_date,
                AmazonSalesAndTrafficDailyFact.date_granularity == date_granularity,
            )
        ).scalar_one_or_none()
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.last_ingestion_run_id = last_ingestion_run_id
            self.session.flush()
            return existing
        row = AmazonSalesAndTrafficDailyFact(
            marketplace_participation_id=marketplace_participation_id,
            report_date=report_date,
            date_granularity=date_granularity,
            last_ingestion_run_id=last_ingestion_run_id,
            **fields,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_range(
        self,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        *,
        start: date,
        end: date,
        date_granularity: str = "DAY",
    ) -> list[AmazonSalesAndTrafficDailyFact] | None:
        """12B.6A read API. Returns `None` for a foreign/nonexistent
        participation (caller must surface this identically to every
        other Sales and Traffic read method's "missing vs. foreign are
        indistinguishable" sanitized behavior) — never an empty list,
        which is reserved for "participation exists, no facts in range
        yet". Ordered by `report_date` ascending — the natural order for
        a trend chart."""
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return None
        return list(
            self.session.scalars(
                select(AmazonSalesAndTrafficDailyFact)
                .where(
                    AmazonSalesAndTrafficDailyFact.marketplace_participation_id == marketplace_participation_id,
                    AmazonSalesAndTrafficDailyFact.date_granularity == date_granularity,
                    AmazonSalesAndTrafficDailyFact.report_date >= start,
                    AmazonSalesAndTrafficDailyFact.report_date <= end,
                )
                .order_by(AmazonSalesAndTrafficDailyFact.report_date.asc())
            ).all()
        )


class AmazonSalesTrafficProductFactRepository:
    """Idempotent upsert for product-level, never-dated Sales and Traffic
    facts. 12B.6A. Same discipline as
    `AmazonSalesTrafficDailyFactRepository.upsert` — see that class's own
    docstring."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _require_participation_in_organization(
        self, organization_id: UUID, marketplace_participation_id: UUID
    ) -> None:
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            raise TypeError(
                "Amazon sales traffic product fact cannot bind a marketplace participation from another "
                "organization."
            )

    def upsert(
        self,
        *,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        request_window_start: date,
        request_window_end: date,
        asin_granularity: str,
        parent_asin: str,
        child_asin: str = "",
        seller_sku: str = "",
        last_ingestion_run_id: UUID,
        fields: dict[str, Any],
    ) -> AmazonSalesAndTrafficProductFact:
        for money_field in ("ordered_product_sales_amount", "ordered_product_sales_amount_b2b"):
            _validate_sales_traffic_money_amount(fields.get(money_field), field_name=money_field)
        self._require_participation_in_organization(organization_id, marketplace_participation_id)
        existing = self.session.execute(
            select(AmazonSalesAndTrafficProductFact).where(
                AmazonSalesAndTrafficProductFact.marketplace_participation_id == marketplace_participation_id,
                AmazonSalesAndTrafficProductFact.request_window_start == request_window_start,
                AmazonSalesAndTrafficProductFact.request_window_end == request_window_end,
                AmazonSalesAndTrafficProductFact.asin_granularity == asin_granularity,
                AmazonSalesAndTrafficProductFact.parent_asin == parent_asin,
                AmazonSalesAndTrafficProductFact.child_asin == child_asin,
                AmazonSalesAndTrafficProductFact.seller_sku == seller_sku,
            )
        ).scalar_one_or_none()
        if existing is not None:
            for key, value in fields.items():
                setattr(existing, key, value)
            existing.last_ingestion_run_id = last_ingestion_run_id
            self.session.flush()
            return existing
        row = AmazonSalesAndTrafficProductFact(
            marketplace_participation_id=marketplace_participation_id,
            request_window_start=request_window_start,
            request_window_end=request_window_end,
            asin_granularity=asin_granularity,
            parent_asin=parent_asin,
            child_asin=child_asin,
            seller_sku=seller_sku,
            last_ingestion_run_id=last_ingestion_run_id,
            **fields,
        )
        self.session.add(row)
        self.session.flush()
        return row

    def list_for_window(
        self,
        organization_id: UUID,
        marketplace_participation_id: UUID,
        *,
        start: date,
        end: date,
    ) -> list[AmazonSalesAndTrafficProductFact] | None:
        """12B.6A read API. Returns every product-fact row whose own
        exact requested window (`request_window_start`/`_end`) falls
        entirely within `[start, end]` — never a row whose window merely
        *overlaps* the query range, which would silently blend a wider
        catalog-wide-style request's aggregate into a narrower period's
        report and misrepresent it as belonging to that narrower window
        (handover doc §1a's grain rule: a product-fact row's numbers are
        an aggregate over its own exact window, never divisible or
        re-attributable to a sub-range). Returns `None` for a foreign/
        nonexistent participation, matching every other Sales and
        Traffic read method."""
        participation = AmazonMarketplaceParticipationRepository(self.session).get_by_id(
            organization_id, marketplace_participation_id
        )
        if participation is None:
            return None
        return list(
            self.session.scalars(
                select(AmazonSalesAndTrafficProductFact).where(
                    AmazonSalesAndTrafficProductFact.marketplace_participation_id == marketplace_participation_id,
                    AmazonSalesAndTrafficProductFact.request_window_start >= start,
                    AmazonSalesAndTrafficProductFact.request_window_end <= end,
                )
            ).all()
        )


def file_sha256(data: bytes) -> str:
    return sha256_bytes(data)

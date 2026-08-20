from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.persistence.types import Guid, JsonPayload


class Base(DeclarativeBase):
    pass


def _uuid() -> UUID:
    return uuid4()


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ScoringProfile(Base):
    __tablename__ = "scoring_profiles"
    __table_args__ = (Index("ix_scoring_profiles_organization_id", "organization_id"),)

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    title_weight: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    bullets_weight: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    description_a_plus_weight: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    media_weight: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    content_structure_weight: Mapped[float] = mapped_column(Numeric(6, 2), nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()


class ProductSnapshot(Base):
    __tablename__ = "product_snapshots"
    __table_args__ = (Index("ix_product_snapshots_org_asin_fetched", "organization_id", "asin", "fetched_at"),)

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    asin: Mapped[str] = mapped_column(String(10), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    normalized_product: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped[Organization] = relationship()


class AnalysisRun(Base):
    __tablename__ = "analysis_runs"
    __table_args__ = (
        Index("ix_analysis_runs_org_asin_created", "organization_id", "asin", "created_at"),
        Index("ix_analysis_runs_org_created", "organization_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    product_snapshot_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("product_snapshots.id"), nullable=False)
    asin: Mapped[str] = mapped_column(String(10), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="complete")
    listing_score_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    image_prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    extra_metadata: Mapped[dict | None] = mapped_column("metadata", JsonPayload, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    snapshot: Mapped[ProductSnapshot] = relationship()
    listing_result: Mapped[ListingAnalysisResult | None] = relationship(back_populates="run", uselist=False)
    ai_result: Mapped[AIListingResult | None] = relationship(back_populates="run", uselist=False)
    image_result: Mapped[ImageIntelligenceResult | None] = relationship(back_populates="run", uselist=False)


class ListingAnalysisResult(Base):
    __tablename__ = "listing_analysis_results"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    analysis_run_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("analysis_runs.id"), nullable=False, unique=True
    )
    score_version: Mapped[str] = mapped_column(String(32), nullable=False)
    listing_quality_score: Mapped[int] = mapped_column(Integer, nullable=False)
    custom_listing_quality_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scoring_profile_snapshot: Mapped[dict | None] = mapped_column(JsonPayload, nullable=True)
    payload: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[AnalysisRun] = relationship(back_populates="listing_result")


class AIListingResult(Base):
    __tablename__ = "ai_listing_results"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    analysis_run_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("analysis_runs.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[AnalysisRun] = relationship(back_populates="ai_result")


class ImageIntelligenceResult(Base):
    __tablename__ = "image_intelligence_results"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    analysis_run_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("analysis_runs.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    images_available: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    images_selected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    images_skipped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run: Mapped[AnalysisRun] = relationship(back_populates="image_result")


class ReportUpload(Base):
    __tablename__ = "report_uploads"
    __table_args__ = (Index("ix_report_uploads_org_hash", "organization_id", "file_hash"),)

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(300), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    parser_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    row_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="complete")
    duplicate_of_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    analysis_payload: Mapped[dict | None] = mapped_column(JsonPayload, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BulkJob(Base):
    __tablename__ = "bulk_jobs"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_file_id: Mapped[UUID | None] = mapped_column(Guid(), ForeignKey("report_uploads.id"), nullable=True)
    external_job_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    total_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    successful_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_items: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    settings: Mapped[dict | None] = mapped_column(JsonPayload, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    items: Mapped[list[BulkJobItem]] = relationship(
        back_populates="job", cascade="all, delete-orphan"
    )


class BulkJobItem(Base):
    __tablename__ = "bulk_job_items"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    bulk_job_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("bulk_jobs.id"), nullable=False)
    asin: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    product_snapshot_id: Mapped[UUID | None] = mapped_column(Guid(), ForeignKey("product_snapshots.id"), nullable=True)
    listing_analysis: Mapped[dict | None] = mapped_column(JsonPayload, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    job: Mapped[BulkJob] = relationship(back_populates="items")


class GeneratedReport(Base):
    __tablename__ = "generated_reports"

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    analysis_run_id: Mapped[UUID | None] = mapped_column(Guid(), ForeignKey("analysis_runs.id"), nullable=True)
    bulk_job_id: Mapped[UUID | None] = mapped_column(Guid(), ForeignKey("bulk_jobs.id"), nullable=True)
    report_type: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_bucket: Mapped[str] = mapped_column(String(100), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(500), nullable=False)
    filename: Mapped[str] = mapped_column(String(300), nullable=False)
    template_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UsageEvent(Base):
    __tablename__ = "usage_events"
    __table_args__ = (Index("ix_usage_events_org_created", "organization_id", "created_at"),)

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    workflow: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    estimated_cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

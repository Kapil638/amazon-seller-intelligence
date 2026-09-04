from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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


class CopilotConversation(Base):
    __tablename__ = "copilot_conversations"
    __table_args__ = (Index("ix_copilot_conversations_org_updated", "organization_id", "updated_at"),)

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    title: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_asin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    last_report_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    previous_intent: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()
    messages: Mapped[list[CopilotMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="CopilotMessage.created_at",
    )
    pending_confirmations: Mapped[list[CopilotPendingConfirmation]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )


class CopilotMessage(Base):
    __tablename__ = "copilot_messages"
    __table_args__ = (
        Index("ix_copilot_messages_org_conversation_created", "organization_id", "conversation_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    conversation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("copilot_conversations.id"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    structured_payload: Mapped[dict | None] = mapped_column(JsonPayload, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[CopilotConversation] = relationship(back_populates="messages")


class CopilotPendingConfirmation(Base):
    __tablename__ = "copilot_pending_confirmations"
    __table_args__ = (
        Index(
            "ix_copilot_pending_confirmations_org_conversation",
            "organization_id",
            "conversation_id",
        ),
        UniqueConstraint("nonce", name="uq_copilot_pending_confirmations_nonce"),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    conversation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("copilot_conversations.id"), nullable=False
    )
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    plan_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plan_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    conversation: Mapped[CopilotConversation] = relationship(back_populates="pending_confirmations")


class ProfitModel(Base):
    __tablename__ = "profit_models"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "asin",
            "marketplace",
            name="uq_profit_models_org_asin_marketplace",
        ),
        Index("ix_profit_models_org_updated", "organization_id", "updated_at"),
        Index("ix_profit_models_org_asin", "organization_id", "asin"),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    asin: Mapped[str] = mapped_column(String(10), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    selling_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    selling_price_source: Mapped[str] = mapped_column(String(32), nullable=False, default="seller")
    cogs: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    packaging_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    other_cost: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    referral_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fba_fee_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    fee_category_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()
    snapshots: Mapped[list[ProfitSnapshot]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="ProfitSnapshot.calculated_at.desc()",
    )


class ProfitSnapshot(Base):
    __tablename__ = "profit_snapshots"
    __table_args__ = (
        Index(
            "ix_profit_snapshots_org_model_calculated",
            "organization_id",
            "profit_model_id",
            "calculated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    profit_model_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("profit_models.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    profit_formula_version: Mapped[str] = mapped_column(String(32), nullable=False, default="profit-calc-v1")
    inputs_json: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    outputs_json: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    completeness: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    organization: Mapped[Organization] = relationship()
    model: Mapped[ProfitModel] = relationship(back_populates="snapshots")


class AdvertisingModel(Base):
    __tablename__ = "advertising_models"
    __table_args__ = (
        UniqueConstraint("profit_model_id", name="uq_advertising_models_profit_model_id"),
        Index("ix_advertising_models_org_profit", "organization_id", "profit_model_id"),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    profit_model_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("profit_models.id"), nullable=False)
    asin: Mapped[str] = mapped_column(String(10), nullable=False)
    marketplace: Mapped[str] = mapped_column(String(32), nullable=False)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="INR")
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    ad_spend: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    ad_sales: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    total_sales: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    units_in_period: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="seller_input")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()
    profit_model: Mapped[ProfitModel] = relationship()
    snapshots: Mapped[list[AdvertisingSnapshot]] = relationship(
        back_populates="model",
        cascade="all, delete-orphan",
        order_by="AdvertisingSnapshot.calculated_at.desc()",
    )


class AdvertisingSnapshot(Base):
    __tablename__ = "advertising_snapshots"
    __table_args__ = (
        Index(
            "ix_advertising_snapshots_org_model_calculated",
            "organization_id",
            "advertising_model_id",
            "calculated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("organizations.id"), nullable=False)
    advertising_model_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("advertising_models.id"), nullable=False
    )
    profit_model_id: Mapped[UUID] = mapped_column(Guid(), ForeignKey("profit_models.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    ads_formula_version: Mapped[str] = mapped_column(String(32), nullable=False, default="ads-calc-v1")
    inputs_json: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    outputs_json: Mapped[dict] = mapped_column(JsonPayload, nullable=False)
    completeness: Mapped[dict] = mapped_column("completeness_json", JsonPayload, nullable=False)
    impact_json: Mapped[dict | None] = mapped_column(JsonPayload, nullable=True)
    profit_snapshot_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    organization: Mapped[Organization] = relationship()
    model: Mapped[AdvertisingModel] = relationship(back_populates="snapshots")


class AmazonConnection(Base):
    """Organization-owned Amazon authorization metadata. Not seller business data.

    Stores connection state only. Never stores refresh tokens, access tokens,
    LWA client secrets, or API credentials. `token_reference` is an opaque
    placeholder for a later SecretProvider; it is not a secret value.
    """

    __tablename__ = "amazon_connections"
    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "provider",
            "environment",
            name="uq_amazon_connections_org_provider_env",
        ),
        # 12B.4B remediation — lets `amazon_ingestion_runs` hold a composite
        # FK pinning `(connection_id, organization_id, region, environment)`
        # to this exact row, making it structurally impossible for a run to
        # claim a region/environment that disagrees with its own
        # `connection_id`'s authoritative values. Safe to add unconditionally
        # (not just for Orders): the only existing code path that sets a
        # run's `region`/`environment` (`AmazonListingsIngestionService.
        # _check_scope`) already always copies them directly from the
        # resolved connection object, so this is a no-op for every row any
        # current code can produce and only rejects a genuinely inconsistent
        # future write.
        UniqueConstraint(
            "id",
            "organization_id",
            "region",
            "environment",
            name="uq_amazon_connections_id_org_region_environment",
        ),
        Index("ix_amazon_connections_org", "organization_id"),
        CheckConstraint(
            "status IN ("
            "'not_connected', 'pending_authorization', 'pending_validation', "
            "'connected', 'degraded', 'revoked', 'error'"
            ")",
            name="ck_amazon_connections_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    region: Mapped[str] = mapped_column(String(8), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="not_connected")
    selling_partner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    application_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    token_reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    authorized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_successful_validation_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()


class AmazonOAuthState(Base):
    """Temporary Amazon authorization transaction. Stores a state hash only.

    Never stores the raw OAuth state, authorization codes, refresh tokens,
    access tokens, or client secrets.
    """

    __tablename__ = "amazon_oauth_states"
    __table_args__ = (
        UniqueConstraint("state_hash", name="uq_amazon_oauth_states_state_hash"),
        Index("ix_amazon_oauth_states_org", "organization_id"),
        Index("ix_amazon_oauth_states_connection_id", "connection_id"),
        Index("ix_amazon_oauth_states_expires_at", "expires_at"),
        CheckConstraint(
            "provider IN ('SP_API')",
            name="ck_amazon_oauth_states_provider",
        ),
        CheckConstraint(
            "environment IN ('SANDBOX', 'PRODUCTION')",
            name="ck_amazon_oauth_states_environment",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    connection_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_connections.id", ondelete="RESTRICT"), nullable=False
    )
    state_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    amazon_state: Mapped[str | None] = mapped_column(String(256), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped[Organization] = relationship()
    connection: Mapped[AmazonConnection] = relationship()


class AmazonSellerAccount(Base):
    """Canonical Amazon selling partner. 12B.2A schema foundation; no ingest.

    V1 product constraint: one `selling_partner_id` is owned by exactly one
    ASI organization (global uniqueness below). One organization may own
    multiple seller accounts. Never stores refresh/access tokens or
    `token_reference`; those remain on `amazon_connections` behind
    SecretProvider.
    """

    __tablename__ = "amazon_seller_accounts"
    __table_args__ = (
        UniqueConstraint(
            "selling_partner_id",
            name="uq_amazon_seller_accounts_selling_partner_id",
        ),
        Index("ix_amazon_seller_accounts_org", "organization_id"),
        CheckConstraint(
            "status IN ('active', 'identity_incomplete', 'disconnected')",
            name="ck_amazon_seller_accounts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    selling_partner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    display_store_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="active")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()


class AmazonMarketplaceParticipation(Base):
    """Seller's Amazon marketplace membership. Marketplace id is canonical identity.

    Display domain (e.g. `amazon.com`) is descriptive metadata only, never a
    uniqueness or identity key. Idempotent upsert target for 12B.2B+.
    """

    __tablename__ = "amazon_marketplace_participations"
    __table_args__ = (
        UniqueConstraint(
            "seller_account_id",
            "marketplace_id",
            name="uq_amazon_marketplace_participations_seller_marketplace",
        ),
        Index("ix_amazon_marketplace_participations_org", "organization_id"),
        Index(
            "ix_amazon_marketplace_participations_seller_active",
            "seller_account_id",
            "is_active",
        ),
        # 12B.4B (remediated) — the participation-side anchor for
        # `amazon_ingestion_run_marketplace_participations`'s composite FK.
        # Pins the entire (organization, seller_account, region,
        # connection_id) tuple — `connection_id` was added in remediation
        # specifically so an association row's FK can force this
        # participation's connection to be the *same row* as the covering
        # Orders run's own connection (see `AmazonIngestionRunMarketplace
        # Participation`'s docstring). `connection_id` is nullable on this
        # table already; a participation with no resolved connection simply
        # cannot satisfy this constraint's non-null half when referenced by
        # a composite FK requiring a specific connection_id value — which is
        # exactly the desired effect: a participation with no known
        # connection is structurally ineligible for any Orders run.
        UniqueConstraint(
            "id",
            "organization_id",
            "seller_account_id",
            "region",
            "connection_id",
            name="uq_amazon_marketplace_participations_id_org_seller_region_conn",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    seller_account_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_seller_accounts.id", ondelete="RESTRICT"), nullable=False
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        Guid(), ForeignKey("amazon_connections.id", ondelete="SET NULL"), nullable=True
    )
    marketplace_id: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    country_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    default_currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    default_language_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    domain_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    region: Mapped[str] = mapped_column(String(8), nullable=False)
    is_participating: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_suspended_listings: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    store_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    organization: Mapped[Organization] = relationship()
    seller_account: Mapped[AmazonSellerAccount] = relationship()


class AmazonIngestionRun(Base):
    """Reusable SP-API ingestion-attempt record. Extended for listings runs in 12B.3B.

    12B.2A creates this table so later ingestion slices have a scoped,
    idempotent run ledger to write to. 12B.3B extends it (`run_type`,
    `marketplace_participation_id`, `pages_fetched`, `reported_total_results`,
    `lease_owner`, `lease_expires_at`) so a future listings-sync slice can
    reuse the same ledger rather than a parallel table. No worker,
    scheduler, client, or live SP-API listings call is authorized by this
    table's existence; the claim/lease *service* (acquire, heartbeat,
    stale-recovery) is explicitly deferred to 12B.3D.

    `reported_total_results` mirrors Amazon's `numberOfResults` from
    `searchListingsItems`. Amazon documents a hard ceiling of 1000 items
    that can actually be paginated through, regardless of how large
    `numberOfResults` reports the true match count to be. The *absence* of
    `nextToken` on the last page is therefore not sufficient on its own to
    prove a listings snapshot is complete: a future reconciliation service
    must also confirm `reported_total_results` did not exceed that ceiling
    (or that `records_received` actually reached it) before treating
    `pagination_complete=True` as license to deactivate missing SKUs.

    `marketplace_participation_id` is `ON DELETE RESTRICT`, not `SET NULL`:
    a `run_type='listings'` row's CHECK constraint
    (`ck_amazon_ingestion_runs_listings_scope_required`) requires that
    column to stay non-null for as long as `run_type='listings'` holds, so
    `SET NULL` could never actually succeed for such a row — it would only
    surface as a confusing CHECK-constraint failure at the moment of
    deletion, with the FK metadata itself claiming a non-blocking action
    that isn't the one that actually happens. `RESTRICT` makes the
    enforced behavior match the declared behavior: a direct, legible
    foreign-key violation instead of an indirect CHECK failure.

    Lease semantics (12B.3B schema, extended 12B.3G for the durable job
    lifecycle): the partial unique index
    `uq_amazon_ingestion_runs_active_listings_scope` treats *every*
    `run_type='listings'` row whose status is in `('queued', 'started',
    'waiting_to_retry')` — every *nonterminal* state — as holding the
    scope, regardless of whether `lease_expires_at` has already passed.
    This is intentional, not an oversight — a partial index predicate is
    evaluated against a row's own column values when that row is written,
    not continuously against wall-clock time, so `lease_expires_at <
    now()` cannot be expressed in the predicate at all (and would not do
    what it looks like even if it compiled). A lease past
    `lease_expires_at` is therefore **not** self-releasing. Recovering it
    requires a transactional, race-safe reclaim (`claim_listings_run`,
    `claim_next_listings_job` in `app.persistence.repositories`) that
    terminalizes or re-queues the stale row *before* any new claim
    attempt, so two recovery attempts can never both believe they
    reclaimed the same row. "The scope is released once the row reaches a
    terminal status, or is legitimately re-queued" is the guarantee this
    schema makes; "an expired lease automatically unlocks the scope" is
    not true and must not be asserted by any test or doc.

    12B.3G durable job lifecycle: a Listings run's status is one of
    `queued` (created by the trigger endpoint, not yet claimed by any
    worker — `lease_owner`/`lease_expires_at` are NULL), `started` (a
    worker holds the lease and is actively fetching/reconciling —
    `started_at` is set at the *first* claim, `last_heartbeat_at` updates
    on every heartbeat), `waiting_to_retry` (Amazon throttled or a
    transient failure occurred; the lease is released — `lease_owner`/
    `lease_expires_at` NULL again — and `next_retry_at` names when a
    worker may reclaim it; `retry_count` increments on each reclaim from
    this state), or one of the pre-existing terminal states `succeeded` /
    `partial` / `failed` / `timed_out`. `queued_at` is the existing
    `created_at` column (the row's insertion moment is already exactly
    "when this job was queued"); no separate column was added for it.
    There is no `cancelled` state: nothing in this codebase ever produces
    a transition into it, and an unreachable state is worse than no state
    (see 12B.3F/12B.3G instructions: do not add states without defined
    transitions).
    """

    __tablename__ = "amazon_ingestion_runs"
    __table_args__ = (
        Index("ix_amazon_ingestion_runs_org", "organization_id"),
        Index("ix_amazon_ingestion_runs_seller_account", "seller_account_id"),
        # Supports the worker's claim query: eligible rows are
        # `run_type='listings' AND status IN ('queued','waiting_to_retry')`,
        # ordered/filtered by `next_retry_at`.
        Index(
            "ix_amazon_ingestion_runs_listings_claimable",
            "run_type",
            "status",
            "next_retry_at",
        ),
        CheckConstraint(
            "status IN ("
            "'queued', 'started', 'waiting_to_retry', "
            "'succeeded', 'partial', 'failed', 'timed_out'"
            ")",
            name="ck_amazon_ingestion_runs_status",
        ),
        CheckConstraint(
            "run_type IN ('marketplace_participations', 'listings', 'orders', 'sales_and_traffic_report')",
            name="ck_amazon_ingestion_runs_run_type",
        ),
        CheckConstraint(
            "run_type <> 'listings' OR "
            "(marketplace_participation_id IS NOT NULL AND seller_account_id IS NOT NULL)",
            name="ck_amazon_ingestion_runs_listings_scope_required",
        ),
        # 12B.6A — a Sales and Traffic report run is scoped exactly like a
        # Listings run (one participation per run), never like an Orders
        # run (which can cover several participations at once): the
        # pinned Reports API contract requires exactly one marketplaceId
        # per report request (Phase 1) — there is no multi-participation
        # request shape to represent for this report type at all, so this
        # reuses the single-participation scope shape rather than the
        # Orders-style association-table indirection.
        CheckConstraint(
            "run_type <> 'sales_and_traffic_report' OR "
            "(marketplace_participation_id IS NOT NULL AND seller_account_id IS NOT NULL)",
            name="ck_amazon_ingestion_runs_sales_traffic_scope_required",
        ),
        # 12B.6A — the report-lifecycle columns below are meaningless
        # outside `run_type='sales_and_traffic_report'` (a Listings/Orders
        # run has no Amazon report id/document id/processing status to
        # track), so structurally forbidding them from ever being set on
        # any other run_type keeps a future bug from reusing this storage
        # for something else — the exact same discipline already applied
        # to the Orders pagination columns below.
        CheckConstraint(
            "run_type = 'sales_and_traffic_report' OR "
            "(report_id IS NULL AND report_document_id IS NULL AND report_processing_status IS NULL "
            "AND report_data_start_time IS NULL AND report_data_end_time IS NULL "
            "AND report_date_granularity IS NULL AND report_asin_granularity IS NULL)",
            name="ck_amazon_ingestion_runs_sales_traffic_fields_scope_required",
        ),
        CheckConstraint(
            "report_processing_status IS NULL OR report_processing_status IN "
            "('IN_QUEUE', 'IN_PROGRESS', 'DONE', 'CANCELLED', 'FATAL')",
            name="ck_amazon_ingestion_runs_report_processing_status",
        ),
        CheckConstraint(
            "report_date_granularity IS NULL OR report_date_granularity IN ('DAY', 'WEEK', 'MONTH')",
            name="ck_amazon_ingestion_runs_report_date_granularity",
        ),
        CheckConstraint(
            "report_asin_granularity IS NULL OR report_asin_granularity IN ('PARENT', 'CHILD', 'SKU')",
            name="ck_amazon_ingestion_runs_report_asin_granularity",
        ),
        # 12B.4B — an Orders run is scoped coarser than a Listings run: one
        # run may cover every active marketplace participation for a given
        # (seller_account, region, environment) in a single searchOrders
        # traversal (Amazon's 0.0056 req/s budget is shared per seller
        # account regardless of participation count, so one run per
        # participation would divide an already-scarce budget needlessly —
        # see docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md's Phase 4
        # point 11). This is why `marketplace_participation_id` is
        # deliberately required to be NULL for `run_type='orders'` — unlike
        # a Listings run, no single participation column can name "the"
        # participation an Orders run covers, because it may cover several.
        # Which participations an Orders run actually covered is instead
        # recorded as one row per participation in
        # `amazon_ingestion_run_marketplace_participations` below — never
        # inferred from this column.
        CheckConstraint(
            "run_type <> 'orders' OR "
            "(marketplace_participation_id IS NULL AND seller_account_id IS NOT NULL "
            "AND region IS NOT NULL AND environment IS NOT NULL AND connection_id IS NOT NULL)",
            name="ck_amazon_ingestion_runs_orders_scope_required",
        ),
        # 12B.4D remediation (0013) — durable pagination continuation.
        # These three columns are meaningless outside `run_type='orders'`
        # (a Listings run's pagination position is never interrupted
        # across a worker restart in a way that needs a persisted resume
        # point — see `listings_ingestion.py`), so structurally forbidding
        # them from ever being set on any other run_type keeps a future
        # bug from accidentally reusing this storage for something else.
        CheckConstraint(
            "run_type = 'orders' OR "
            "(orders_window_last_updated_after IS NULL AND orders_window_captured_at IS NULL "
            "AND orders_pagination_next_token IS NULL)",
            name="ck_amazon_ingestion_runs_orders_pagination_scope_required",
        ),
        # Single-writer guarantee (12B.3 product decision, widened 12B.3G):
        # at most one *nonterminal* listings run may exist per
        # (seller_account_id, marketplace_participation_id) at a time —
        # covering queued, started, and waiting_to_retry. This is the
        # actual claim mechanism — a concurrent second INSERT into this
        # scope fails on this constraint — not a separately-implemented
        # lease service. Terminal statuses (succeeded/partial/failed/
        # timed_out) fall outside the partial predicate, so the scope is
        # never permanently locked: any transition into a terminal status
        # frees it for a brand-new job. See the class docstring: this is a
        # status-based release only, never an automatic, lease-expiry-based
        # one.
        Index(
            "uq_amazon_ingestion_runs_active_listings_scope",
            "seller_account_id",
            "marketplace_participation_id",
            unique=True,
            postgresql_where=text(
                "run_type = 'listings' AND status IN ('queued', 'started', 'waiting_to_retry')"
            ),
            sqlite_where=text(
                "run_type = 'listings' AND status IN ('queued', 'started', 'waiting_to_retry')"
            ),
        ),
        # 12B.4B — the Orders equivalent of the index above, scoped to
        # (seller_account, region, environment) instead of a single
        # participation, matching the coarser Orders run scope. This *is*
        # the concurrency control for Orders jobs (a second concurrent
        # INSERT into the same scope fails on this constraint), exactly the
        # same technique as Listings, just at a different granularity.
        Index(
            "uq_amazon_ingestion_runs_active_orders_scope",
            "seller_account_id",
            "region",
            "environment",
            unique=True,
            postgresql_where=text(
                "run_type = 'orders' AND status IN ('queued', 'started', 'waiting_to_retry')"
            ),
            sqlite_where=text(
                "run_type = 'orders' AND status IN ('queued', 'started', 'waiting_to_retry')"
            ),
        ),
        # 12B.6A — the Sales and Traffic equivalent of the Listings index
        # above (same single-participation scope shape, §ck_..._sales_
        # traffic_scope_required), never the Orders shape: at most one
        # nonterminal report run may exist per (seller_account,
        # marketplace_participation) at a time.
        Index(
            "uq_amazon_ingestion_runs_active_sales_traffic_scope",
            "seller_account_id",
            "marketplace_participation_id",
            unique=True,
            postgresql_where=text(
                "run_type = 'sales_and_traffic_report' AND status IN ('queued', 'started', 'waiting_to_retry')"
            ),
            sqlite_where=text(
                "run_type = 'sales_and_traffic_report' AND status IN ('queued', 'started', 'waiting_to_retry')"
            ),
        ),
        # Widens the PK into a composite unique key so amazon_seller_listings
        # can hold a composite FK guaranteeing a listing's last ingestion
        # run belongs to the same marketplace participation (see
        # AmazonSellerListing's docstring).
        UniqueConstraint(
            "id",
            "marketplace_participation_id",
            name="uq_amazon_ingestion_runs_id_marketplace_participation",
        ),
        # 12B.4B (remediated) — lets `amazon_ingestion_run_marketplace_
        # participations` hold a composite FK back to this run that pins
        # the entire (organization, seller_account, region, connection_id)
        # tuple, not just the run's `id` — so an association row can never
        # be inserted pointing at a run from a different organization/
        # seller/region/connection than the association row's own
        # denormalized copies of those columns claim. `connection_id` was
        # added in remediation: pinning it here, together with the same
        # pin on the participation side (see `AmazonMarketplaceParticipation`),
        # forces a run and every participation it covers to share the
        # *exact same* `amazon_connections` row — which the composite FK
        # below (`uq_amazon_connections_id_org_region_environment`) already
        # proves carries a self-consistent (organization, region,
        # environment). This is what makes a PRODUCTION run pairing with a
        # SANDBOX-backed participation, or an `na` run pairing with an
        # `eu`/`fe` participation, structurally impossible rather than only
        # conventionally avoided.
        UniqueConstraint(
            "id",
            "organization_id",
            "seller_account_id",
            "region",
            "connection_id",
            name="uq_amazon_ingestion_runs_id_org_seller_region_conn",
        ),
        # 12B.4B remediation — proves this run's own `region`/`environment`
        # actually match its own `connection_id`'s authoritative values.
        # `ondelete="RESTRICT"`, not `SET NULL` like the plain single-column
        # FK on `connection_id` below: `organization_id`/`region`/
        # `environment` are all `NOT NULL` on this table, so `SET NULL`
        # cannot be satisfied for a composite FK including them — RESTRICT
        # makes the enforced behavior legible instead of failing as a
        # confusing NOT NULL violation at delete time. No code anywhere in
        # this repository deletes an `amazon_connections` row today, so
        # this changes no currently-exercised behavior.
        ForeignKeyConstraint(
            ["connection_id", "organization_id", "region", "environment"],
            [
                "amazon_connections.id",
                "amazon_connections.organization_id",
                "amazon_connections.region",
                "amazon_connections.environment",
            ],
            name="fk_amazon_ingestion_runs_connection_org_region_env",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    organization_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("organizations.id", ondelete="RESTRICT"), nullable=False
    )
    connection_id: Mapped[UUID | None] = mapped_column(
        Guid(), ForeignKey("amazon_connections.id", ondelete="SET NULL"), nullable=True
    )
    seller_account_id: Mapped[UUID | None] = mapped_column(
        Guid(), ForeignKey("amazon_seller_accounts.id", ondelete="SET NULL"), nullable=True
    )
    marketplace_participation_id: Mapped[UUID | None] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), nullable=True
    )
    run_type: Mapped[str] = mapped_column(String(32), nullable=False, default="marketplace_participations")
    domain: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str] = mapped_column(String(8), nullable=False)
    environment: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    # Nullable + no server_default since 12B.3G: a `queued` row has not
    # been claimed by any worker yet, so it has no start time. Set
    # explicitly at claim time (first claim only — a retry reclaim does
    # not reset it, so it always reflects the *original* first attempt).
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    request_correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    records_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Reused (12B.3G) as the Listings job's attempt counter — already
    # existed, unused by any listings-run code path before now.
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_class: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pagination_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reported_total_results: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 12B.3G additions for the durable job lifecycle.
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 12B.4B — Orders-specific counters. `records_received/accepted/
    # rejected` above were sized for Listings, where one record == one
    # listing/SKU. An Orders page truthfully contains two distinct
    # countable entities (orders and their items), which a single generic
    # counter triple cannot represent without conflating the two — so these
    # are additive, not a reuse of the existing columns. Meaningful only
    # for `run_type='orders'`; always `0` (their deterministic default) for
    # every other run_type, including every pre-12B.4B row after migration.
    orders_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orders_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_received: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_accepted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    items_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # 12B.4D remediation (0013) — durable pagination continuation.
    # `orders_window_last_updated_after`/`orders_window_captured_at` are
    # written exactly once per run, by whichever attempt first reaches
    # `AmazonIngestionRunRepository.freeze_orders_window_if_needed`
    # (idempotent — a later attempt that resumes this run reads the
    # already-frozen value back rather than recomputing a fresh one).
    # Freezing this per-run, rather than recomputing it from the
    # checkpoint on every attempt (12B.4B's original design — see
    # `AmazonOrdersSyncCheckpoint`'s docstring), is what makes reusing a
    # still-valid `orders_pagination_next_token` across attempts safe:
    # Amazon's pinned contract requires "all other parameters [to] be
    # provided with the same values that were provided with the request
    # that generated this token" for the whole life of one paginated
    # traversal (`paginationToken`'s 24-hour documented lifetime) — see
    # `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`'s Pagination
    # section. The *marketplace* half of that same frozen request shape
    # needs no separate column: it is already immutably fixed by this
    # run's own membership rows in
    # `amazon_ingestion_run_marketplace_participations`, which nothing
    # ever adds to or removes from after creation.
    orders_window_last_updated_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    orders_window_captured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # `orders_pagination_next_token` is Amazon's opaque `paginationToken`
    # for resuming exactly where the last successfully committed page
    # left off — never a page-1 restart after an interruption. It is
    # NOT a credential (it grants nothing by itself: redeeming it still
    # requires a valid LWA access token derived from the real refresh
    # token, which lives only in `SecretProvider`, never here), but it is
    # still treated as sensitive-by-convention and kept out of every
    # public surface:
    #
    # Threat model: an actor who can read this column already has raw
    # database access, and therefore can already see every other column
    # on this same row and its child rows (the exact frozen search
    # window, every participation covered, and the already-persisted
    # orders/items this run produced) — the token adds no NEW visibility
    # into ASI's own data beyond what that same access already exposes.
    # Its only incremental capability is being redeemable directly
    # against Amazon's `searchOrders` endpoint for this account's already-
    # visible query shape, and only *if* the actor separately also holds
    # a valid access token — gated entirely by SecretProvider, not by
    # this column. The realistic worst case of this column leaking on
    # its own is a nuisance replay against Amazon's own rate limit for
    # this seller, not a new seller-identity or business-data exposure.
    # This is why a plain, tightly-scoped private column — not
    # SecretProvider (whose one-value-per-connection reference format and
    # narrowly documented "OAuth/token onboarding" purpose this does not
    # fit — see `app/amazon/secrets.py`'s module docstring) and not a new
    # encryption dependency (no key-management infrastructure exists in
    # this codebase to hold such a key safely, so adding one would trade
    # a well-understood, disciplined-access risk for a new, less-
    # understood one) — is judged proportionate for this milestone.
    #
    # Discipline enforced instead: never selected into any read-service
    # projection or Pydantic response model (`AmazonOrdersReadService`
    # never touches this column at all), never interpolated into a log
    # message, and cleared to `NULL` (not merely left stale) the moment
    # this run reaches any terminal status — see
    # `AmazonIngestionRunRepository.finalize_successful_orders_run` and
    # `.complete_orders_run_as_failed`. `Text`, not a bounded `String`:
    # unlike `token_reference` (validated against `MAX_SECRET_REFERENCE_
    # LENGTH` because it is parsed as a structured pointer elsewhere),
    # this value is opaque and never parsed, so there is no protocol
    # reason to risk truncating a real Amazon-issued token against an
    # arbitrary bound.
    orders_pagination_next_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 12B.6A — Sales and Traffic report lifecycle. `report_id`/
    # `report_document_id` are durably stored (narrowly reviewed — see
    # docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md §2) so a worker
    # restarted after `createReport` succeeded can resume polling/
    # downloading the *same* report instead of creating a duplicate one
    # against this report type's scarce rate-limit budget (three
    # createReport calls per five minutes, shared across every use of
    # this report type for the seller). The pre-signed document URL
    # itself is never stored here or anywhere — it expires within 5
    # minutes of being issued (pinned contract), so persisting it would
    # be stale before any restart could use it, independent of the
    # privacy reasoning that also forbids it.
    report_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_document_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    report_processing_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # The exact requested window this run's report covers — never
    # inferred from `created_at`/`completed_at`, which are this *run's*
    # own timestamps, not the report's requested data range. See §1a of
    # the handover doc: `report_data_start_time == report_data_end_time`
    # for a daily product-level backfill request; a catalog-wide trend
    # request may span a much wider window in one report.
    report_data_start_time: Mapped[date | None] = mapped_column(Date(), nullable=True)
    report_data_end_time: Mapped[date | None] = mapped_column(Date(), nullable=True)
    report_date_granularity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    report_asin_granularity: Mapped[str | None] = mapped_column(String(8), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    organization: Mapped[Organization] = relationship()


class AmazonSellerListing(Base):
    """Canonical seller-owned listing (SKU) for one marketplace participation.

    12B.3B schema foundation only — no SP-API client, reconciliation
    service, read API, or UI reads/writes this table yet.

    Ownership integrity: this table deliberately has no `organization_id`
    or `seller_account_id` column. Ownership is derived exclusively through
    `marketplace_participation_id` -> `amazon_marketplace_participations`
    (which itself carries `organization_id` and `seller_account_id`). A
    single source of truth makes contradictory ownership structurally
    impossible, at the cost of requiring a join for seller-account-scoped
    listing pages — an accepted tradeoff given expected per-seller SKU
    volumes (hundreds to low thousands, not millions).

    `status`/`offers`/`fulfillment_availability`/`issues`/`product_types`
    store the shapes documented by the pinned `searchListingsItems`
    contract (see `docs/AI_HANDOVER/12B3A_LISTINGS_ITEMS_API_CONTRACT_REPORT.md`).
    Their internal shape is validated at the application layer (future
    12B.3C parsing), not by a database CHECK constraint, matching how
    `inputs_json`/`outputs_json`/`completeness` already work on
    `ProfitSnapshot`/`AdvertisingSnapshot`.

    Provenance integrity: `last_ingestion_run_id` uses a *composite*
    foreign key — `(last_ingestion_run_id, marketplace_participation_id)`
    -> `amazon_ingestion_runs(id, marketplace_participation_id)` — instead
    of a plain single-column FK to `amazon_ingestion_runs.id`. This
    guarantees the referenced run's `marketplace_participation_id` always
    equals this row's own `marketplace_participation_id`, with no
    duplicated organization/seller-account column needed: the same column
    that already carries this listing's ownership is reused as the second
    half of the composite key (`MATCH SIMPLE` semantics mean the
    constraint is inert whenever `last_ingestion_run_id IS NULL`, which is
    always true before a listing's first successful run). `ON DELETE
    RESTRICT`, not `SET NULL`: nulling only `last_ingestion_run_id` while
    leaving `marketplace_participation_id` alone would violate the
    composite FK's own semantics, and nulling *both* is impossible because
    `marketplace_participation_id` is `NOT NULL` here.

    Deliberately NOT enforced at the database level: that the referenced
    run has `run_type='listings'`. A composite FK can only compare the
    referenced row's columns against *this row's own* column values — it
    cannot pin the referenced side to a literal constant, so expressing
    "must be a listings run" at the DB level would require a `PL/pgSQL`
    trigger (a lookup + raise on every insert/update), which is
    disproportionate schema complexity for this foundation slice. That
    check is deferred to repository/service-layer validation when the
    write path is built (12B.3D) — same-marketplace-participation
    provenance is the invariant the schema protects; run-type provenance
    is an application-layer invariant for now.
    """

    __tablename__ = "amazon_seller_listings"
    __table_args__ = (
        UniqueConstraint(
            "marketplace_participation_id",
            "seller_sku",
            name="uq_amazon_seller_listings_participation_sku",
        ),
        Index("ix_amazon_seller_listings_participation_asin", "marketplace_participation_id", "asin"),
        Index("ix_amazon_seller_listings_participation_active", "marketplace_participation_id", "is_active"),
        Index("ix_amazon_seller_listings_participation_buyable", "marketplace_participation_id", "is_buyable"),
        Index(
            "ix_amazon_seller_listings_participation_discoverable",
            "marketplace_participation_id",
            "is_discoverable",
        ),
        Index(
            "ix_amazon_seller_listings_participation_issue_count",
            "marketplace_participation_id",
            "issue_count",
        ),
        Index(
            "ix_amazon_seller_listings_participation_updated",
            "marketplace_participation_id",
            "amazon_last_updated_at",
        ),
        Index("ix_amazon_seller_listings_last_ingestion_run", "last_ingestion_run_id"),
        CheckConstraint(
            "highest_issue_severity IS NULL OR highest_issue_severity IN ('ERROR', 'WARNING', 'INFO')",
            name="ck_amazon_seller_listings_highest_issue_severity",
        ),
        ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_seller_listings_last_ingestion_run_same_participation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    marketplace_participation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), nullable=False
    )
    seller_sku: Mapped[str] = mapped_column(String(180), nullable=False)
    asin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    product_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    condition_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    main_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    amazon_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amazon_last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[list] = mapped_column(JsonPayload, nullable=False)
    is_buyable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_discoverable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    offers: Mapped[list] = mapped_column(JsonPayload, nullable=False)
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    price_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    fulfillment_availability: Mapped[list] = mapped_column(JsonPayload, nullable=False)
    issues: Mapped[list] = mapped_column(JsonPayload, nullable=False)
    issue_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    highest_issue_severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    product_types: Mapped[list] = mapped_column(JsonPayload, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_successful_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # No inline ForeignKey() here: the actual constraint is the composite
    # ForeignKeyConstraint in __table_args__ (last_ingestion_run_id,
    # marketplace_participation_id) -> amazon_ingestion_runs(id,
    # marketplace_participation_id) — see the class docstring.
    last_ingestion_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    marketplace_participation: Mapped[AmazonMarketplaceParticipation] = relationship()


class AmazonIngestionRunMarketplaceParticipation(Base):
    """Which marketplace participations one Orders ingestion run covered.

    12B.4B. A Listings run covers exactly one participation, named directly
    by `amazon_ingestion_runs.marketplace_participation_id`. An Orders run
    covers a whole `(seller_account, region, environment)` scope in one
    `searchOrders` traversal (see `AmazonIngestionRun.__table_args__`'s
    `ck_amazon_ingestion_runs_orders_scope_required` docstring) — this
    table is the only place that membership is recorded, one row per
    participation actually included in the run.

    Composite primary key `(ingestion_run_id, marketplace_participation_id)`
    gives the exact "no duplicate run/participation pair" uniqueness this
    table exists for, and is also the FK target `amazon_seller_orders`/
    `amazon_seller_order_items`/`amazon_orders_sync_checkpoints` reference
    from `last_ingestion_run_id` — proving, at the database level, that the
    run named there actually included that exact participation. A row
    whose `last_ingestion_run_id` names a run that never covered its own
    `marketplace_participation_id` cannot be inserted; the composite FK
    rejects it.

    Both composite foreign keys below pin the full `(organization_id,
    seller_account_id, region, connection_id)` tuple, not just the bare id
    — so this table cannot associate a run and a participation that
    disagree on organization, seller account, region, *or connection*,
    structurally, not only by convention. Pinning `connection_id` on both
    sides (added in 12B.4B remediation) forces the run and every
    participation it covers to share the exact same `amazon_connections`
    row — and since that row's own `(organization, region, environment)`
    is itself proven self-consistent by
    `uq_amazon_connections_id_org_region_environment` and
    `fk_amazon_ingestion_runs_connection_org_region_env`, this transitively
    makes a PRODUCTION run pairing with a SANDBOX-backed participation, or
    an `na` run pairing with an `eu`/`fe` participation, structurally
    impossible — not merely disallowed by convention. `run_type='orders'`
    on the referenced run is *not* database-enforced here, for the same
    reason `amazon_seller_listings` does not database-enforce its
    referenced run's `run_type='listings'` (see that class's docstring) —
    a composite FK cannot pin the referenced side to a literal constant;
    that check is deferred to repository/service-layer validation,
    consistent with the existing precedent.
    """

    __tablename__ = "amazon_ingestion_run_marketplace_participations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["ingestion_run_id", "organization_id", "seller_account_id", "region", "connection_id"],
            [
                "amazon_ingestion_runs.id",
                "amazon_ingestion_runs.organization_id",
                "amazon_ingestion_runs.seller_account_id",
                "amazon_ingestion_runs.region",
                "amazon_ingestion_runs.connection_id",
            ],
            name="fk_amazon_ingestion_run_parts_run_scope",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["marketplace_participation_id", "organization_id", "seller_account_id", "region", "connection_id"],
            [
                "amazon_marketplace_participations.id",
                "amazon_marketplace_participations.organization_id",
                "amazon_marketplace_participations.seller_account_id",
                "amazon_marketplace_participations.region",
                "amazon_marketplace_participations.connection_id",
            ],
            name="fk_amazon_ingestion_run_parts_participation_scope",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_amazon_ingestion_run_participations_participation",
            "marketplace_participation_id",
        ),
    )

    ingestion_run_id: Mapped[UUID] = mapped_column(Guid(), primary_key=True)
    marketplace_participation_id: Mapped[UUID] = mapped_column(Guid(), primary_key=True)
    organization_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    seller_account_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    region: Mapped[str] = mapped_column(String(8), nullable=False)
    # NOT NULL: see this class's docstring — pinning this alongside the run
    # and participation's own connection_id is the actual environment/
    # region enforcement mechanism for 12B.4B's remediated design.
    connection_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AmazonSellerOrder(Base):
    """Canonical seller-owned order (current-state) for one marketplace
    participation. 12B.4B schema foundation only — no SP-API client,
    ingestion service, read API, or UI reads/writes this table yet.

    Current-state, not event history: this row always reflects Amazon's
    latest known state for the order, upserted in place. There is no
    per-status-transition history table — see
    docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md Phase 5's explicit
    "history/change tracking... deferred" decision, reconfirmed unchanged
    by 12B.4B.

    No customer PII column exists here by design: no buyer name/email,
    no recipient/shipping address, no payment instrument data, no
    tax-registration identifiers, no gift message, no cancellation
    free-text reason, no raw order payload. See
    `tests/test_amazon_seller_orders_schema.py` for the automated assertion
    that these columns stay absent.

    `amazon_order_id` is a confidential business identifier — like
    `amazon_seller_listings.seller_sku`/`asin`, it is safe to store and to
    return through a future authenticated, org-scoped read API, but must
    never appear in logs, exception text, or generic diagnostics (see
    docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md Phase 3, answer 6).

    Monetary precision: `order_total_amount` (and `unit_price_amount`/
    `item_proceeds_amount` on `AmazonSellerOrderItem`) use `Numeric(19,4)`,
    not this repository's more common `Numeric(14,2)` — the pinned
    contract's own `Decimal` type is documented as "a decimal number with
    no loss of precision" and is transmitted as a JSON *string*
    specifically to avoid float rounding, which is direct evidence against
    assuming every supported currency has exactly two fractional digits.
    `Numeric(19,4)` comfortably covers three-decimal currencies (e.g.
    BHD/KWD/OMR) with a margin of safety, while `19` total digits exceeds
    any realistic order amount. This is a deliberately scoped exception
    for the *new* Orders columns only — it does not retrofit
    `amazon_seller_listings.price_amount` or the Profit/Advertising
    models' existing `Numeric(14,2)` columns, which are unrelated to this
    milestone and already proven adequate for their own inputs.

    Ownership integrity: like `amazon_seller_listings`, this table has no
    `organization_id`/`seller_account_id` column — ownership is derived
    exclusively through `marketplace_participation_id`.

    Provenance integrity: `last_ingestion_run_id` uses a composite foreign
    key to `amazon_ingestion_run_marketplace_participations(ingestion_run_
    id, marketplace_participation_id)` rather than directly to
    `amazon_ingestion_runs.id` — this is the key difference from
    `amazon_seller_listings`'s pattern, made necessary because one Orders
    run can cover several participations (unlike one Listings run, which
    covers exactly one). Referencing the association table instead of the
    run directly proves, at the database level, that the referenced run
    actually included *this row's own* participation — not merely that the
    run exists.
    """

    __tablename__ = "amazon_seller_orders"
    __table_args__ = (
        UniqueConstraint(
            "marketplace_participation_id",
            "amazon_order_id",
            name="uq_amazon_seller_orders_participation_order_id",
        ),
        # Widens into a composite unique key so amazon_seller_order_items
        # can hold a composite FK guaranteeing an item's order belongs to
        # the same marketplace participation as the item itself — the same
        # technique amazon_ingestion_runs already uses for
        # amazon_seller_listings.
        UniqueConstraint(
            "id",
            "marketplace_participation_id",
            name="uq_amazon_seller_orders_id_marketplace_participation",
        ),
        CheckConstraint(
            "fulfillment_status IS NULL OR fulfillment_status IN ("
            "'PENDING_AVAILABILITY', 'PENDING', 'UNSHIPPED', 'PARTIALLY_SHIPPED', "
            "'SHIPPED', 'CANCELLED', 'UNFULFILLABLE'"
            ")",
            name="ck_amazon_seller_orders_fulfillment_status",
        ),
        CheckConstraint(
            "fulfilled_by IS NULL OR fulfilled_by IN ('MERCHANT', 'AMAZON')",
            name="ck_amazon_seller_orders_fulfilled_by",
        ),
        Index("ix_amazon_seller_orders_participation_updated", "marketplace_participation_id", "amazon_last_updated_at"),
        Index("ix_amazon_seller_orders_participation_status", "marketplace_participation_id", "fulfillment_status"),
        Index("ix_amazon_seller_orders_last_ingestion_run", "last_ingestion_run_id"),
        ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            [
                "amazon_ingestion_run_marketplace_participations.ingestion_run_id",
                "amazon_ingestion_run_marketplace_participations.marketplace_participation_id",
            ],
            name="fk_amazon_seller_orders_last_run_same_participation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    marketplace_participation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), nullable=False
    )
    amazon_order_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # "Order status" in product language; named after the contract's own
    # `Order.fulfillment.fulfillmentStatus` field for traceability.
    fulfillment_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    fulfilled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)
    sales_channel_name: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sales_channel_marketplace_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sales_channel_marketplace_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    items_shipped_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    items_unshipped_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_total_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    order_total_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    is_business_order: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_prime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    was_cancelled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    amazon_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    amazon_last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # No inline ForeignKey() here: the actual constraint is the composite
    # ForeignKeyConstraint in __table_args__ — see the class docstring.
    last_ingestion_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    marketplace_participation: Mapped[AmazonMarketplaceParticipation] = relationship()


class AmazonSellerOrderItem(Base):
    """One product line within a canonical seller-owned order. 12B.4B.

    Natural key `(order_id, amazon_order_item_id)`: `orderItemId` is
    documented by the pinned Orders API `2026-01-01` contract as "a unique
    identifier for this specific item within the order" — an evidenced
    natural key, not one invented from `seller_sku`/`asin` alone (the same
    SKU legitimately repeats across many orders, and even within one order
    a substitution/associated item could in principle share an ASIN with
    another line — neither is a safe uniqueness boundary on its own).

    No customer PII column exists here by design — see
    `AmazonSellerOrder`'s docstring and
    `tests/test_amazon_seller_orders_schema.py`.
    """

    __tablename__ = "amazon_seller_order_items"
    __table_args__ = (
        UniqueConstraint(
            "order_id",
            "amazon_order_item_id",
            name="uq_amazon_seller_order_items_order_item_id",
        ),
        Index("ix_amazon_seller_order_items_participation_asin", "marketplace_participation_id", "asin"),
        Index("ix_amazon_seller_order_items_participation_sku", "marketplace_participation_id", "seller_sku"),
        Index("ix_amazon_seller_order_items_last_ingestion_run", "last_ingestion_run_id"),
        # Prevents an item from ever pointing at an order in a different
        # marketplace participation than its own `marketplace_participation_
        # id` column — the same cross-marketplace-provenance guard
        # `amazon_seller_listings` gets from amazon_ingestion_runs, applied
        # here one level down, against amazon_seller_orders instead.
        ForeignKeyConstraint(
            ["order_id", "marketplace_participation_id"],
            ["amazon_seller_orders.id", "amazon_seller_orders.marketplace_participation_id"],
            name="fk_amazon_seller_order_items_order_same_participation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            [
                "amazon_ingestion_run_marketplace_participations.ingestion_run_id",
                "amazon_ingestion_run_marketplace_participations.marketplace_participation_id",
            ],
            name="fk_amazon_seller_order_items_last_run_same_participation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    order_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    marketplace_participation_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    amazon_order_item_id: Mapped[str] = mapped_column(String(64), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(180), nullable=False)
    asin: Mapped[str | None] = mapped_column(String(10), nullable=True)
    item_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    condition_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    quantity_ordered: Mapped[int] = mapped_column(Integer, nullable=False)
    quantity_fulfilled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quantity_unfulfilled: Mapped[int | None] = mapped_column(Integer, nullable=True)
    unit_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    unit_price_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    item_proceeds_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    item_proceeds_currency: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # No inline ForeignKey() here: the actual constraints are the composite
    # ForeignKeyConstraints in __table_args__ — see the class docstring.
    last_ingestion_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AmazonOrdersSyncCheckpoint(Base):
    """Durable, per-participation Orders incremental high-water mark. 12B.4B.

    One row per `marketplace_participation_id`; absence of a row (not a
    shared default/zero value) is what makes a newly-active participation
    start with no inherited history — there is nothing to inherit from,
    structurally, until this participation's own first successful run
    creates its own row.

    `synced_through_at` stores the raw watermark only — no overlap window
    is baked into the stored value. The future ingestion service applies a
    configurable overlap window (subtracted from this value) when
    constructing the next `lastUpdatedAfter` request; this table has no
    opinion on how large that window is (see
    docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md Phase 4 point 2).

    `last_successful_run_id`'s composite foreign key to
    `amazon_ingestion_run_marketplace_participations` is the mechanism that
    proves, at the database level, this checkpoint can never reference a
    run that did not actually cover its own `marketplace_participation_id`
    — a service-layer bug that tried to advance a checkpoint using an
    unrelated run's id would fail this FK, not merely fail a code review.

    No `paginationToken`/`nextToken` column exists here, deliberately — see
    docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md Phase 4's default
    recommendation, adopted as-is: a failed/partial traversal restarts from
    the unchanged high-water mark rather than resuming a remembered page,
    relying on idempotent upserts and the overlap window for correctness.
    """

    __tablename__ = "amazon_orders_sync_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["last_successful_run_id", "marketplace_participation_id"],
            [
                "amazon_ingestion_run_marketplace_participations.ingestion_run_id",
                "amazon_ingestion_run_marketplace_participations.marketplace_participation_id",
            ],
            name="fk_amazon_orders_sync_checkpoints_run_same_participation",
            ondelete="RESTRICT",
        ),
    )

    marketplace_participation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    seller_account_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    synced_through_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # No inline ForeignKey() here: the actual constraint is the composite
    # ForeignKeyConstraint in __table_args__ — see the class docstring.
    last_successful_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AmazonSalesAndTrafficDailyFact(Base):
    """12B.6A — catalog-wide, dated Sales and Traffic facts
    (`salesAndTrafficByDate` in the pinned contract). One row per
    `(marketplace_participation_id, report_date, date_granularity)` —
    `date_granularity` is part of the natural key because a `WEEK`- or
    `MONTH`-bucketed row's `report_date` is that period's *start* date,
    which can collide with an unrelated `DAY`-bucketed row's own date
    (e.g. a week starting 2026-09-01 and a single day 2026-09-01 are two
    different facts, never the same row) — see docs/AI_HANDOVER/
    12B6A_SALES_TRAFFIC_REPORTS.md §1a. This milestone's own worker only
    ever requests `DAY` granularity (§7 of that doc); the column and
    constraint still accept `WEEK`/`MONTH` structurally, for an operator-
    triggered custom report, without this table ever conflating the
    three.

    Every money field is `Numeric(19, 4)` (matches the precision this
    repository already established for Orders amounts — see
    `AmazonSellerOrder`'s own docstring) and travels with exactly one
    `currency_code` for the whole row: this report's own contract scopes
    every `Amount` field in one response to a single marketplace, and
    therefore a single currency, so one column rather than one currency
    per money field.

    Every percentage field is `Numeric(7, 4)`, `NULL` meaning "Amazon did
    not return this field" (e.g. every `_b2b` column for a non-B2B
    seller) and never coerced to `0`. Every percentage field *except*
    `unit_session_percentage`/`unit_session_percentage_b2b` carries a
    `CHECK (... BETWEEN 0 AND 100)` — those two are deliberately left
    unbounded above, because the pinned contract's own schema omits an
    upper bound for them (a session can result in more than one unit
    purchased) and its own worked example shows a value of `300.00`; see
    §3 of the handover doc.
    """

    __tablename__ = "amazon_sales_traffic_daily_facts"
    __table_args__ = (
        UniqueConstraint(
            "marketplace_participation_id",
            "report_date",
            "date_granularity",
            name="uq_amazon_sales_traffic_daily_facts_natural_key",
        ),
        CheckConstraint(
            "date_granularity IN ('DAY', 'WEEK', 'MONTH')",
            name="ck_amazon_sales_traffic_daily_facts_date_granularity",
        ),
        CheckConstraint(
            "refund_rate IS NULL OR refund_rate BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_refund_rate",
        ),
        CheckConstraint(
            "buy_box_percentage IS NULL OR buy_box_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_buy_box_pct",
        ),
        CheckConstraint(
            "buy_box_percentage_b2b IS NULL OR buy_box_percentage_b2b BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_buy_box_pct_b2b",
        ),
        CheckConstraint(
            "order_item_session_percentage IS NULL OR order_item_session_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_item_session_pct",
        ),
        CheckConstraint(
            "order_item_session_percentage_b2b IS NULL OR order_item_session_percentage_b2b BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_item_session_pct_b2b",
        ),
        CheckConstraint(
            "received_negative_feedback_rate IS NULL OR received_negative_feedback_rate BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_daily_facts_neg_feedback_rate",
        ),
        ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_sales_traffic_daily_facts_last_run_participation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    marketplace_participation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), nullable=False
    )
    report_date: Mapped[date] = mapped_column(Date(), nullable=False)
    date_granularity: Mapped[str] = mapped_column(String(8), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # --- salesByDate -----------------------------------------------------
    ordered_product_sales_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    ordered_product_sales_amount_b2b: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    units_ordered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_ordered_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_order_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_order_items_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_sales_per_order_item_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    average_sales_per_order_item_amount_b2b: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    average_units_per_order_item: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    average_units_per_order_item_b2b: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    average_selling_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    average_selling_price_amount_b2b: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    units_refunded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refund_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    claims_granted: Mapped[int | None] = mapped_column(Integer, nullable=True)
    claims_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    shipped_product_sales_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    units_shipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orders_shipped: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # --- trafficByDate -----------------------------------------------------
    browser_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_page_views_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_page_views_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_views_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_sessions_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_sessions_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sessions_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    buy_box_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    buy_box_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    order_item_session_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    order_item_session_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    # No upper-bound CHECK — see class docstring.
    unit_session_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    unit_session_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    average_offer_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    average_parent_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    feedback_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    negative_feedback_received: Mapped[int | None] = mapped_column(Integer, nullable=True)
    received_negative_feedback_rate: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    # No inline ForeignKey(): the actual constraint is the composite
    # ForeignKeyConstraint in __table_args__.
    last_ingestion_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AmazonSalesAndTrafficProductFact(Base):
    """12B.6A — product-level Sales and Traffic facts
    (`salesAndTrafficByAsin` in the pinned contract). **Never dated** —
    each row is a single aggregate over the exact
    `(request_window_start, request_window_end)` one report request
    covered, per docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md §1a's
    grain conclusion: the pinned schema's `SalesAndTrafficByAsin`
    definition has no `date` field at all, so no date is ever invented
    for a row here. A "daily" product fact is simply a row whose
    `request_window_start == request_window_end` (this milestone's own
    backfill/incremental shape, §7 of the handover doc) — the table
    itself makes no such assumption and stores whatever window was
    actually requested, including a wider one from a future ad-hoc
    "summarize the whole quarter for this product" report.

    `child_asin`/`seller_sku` are **`NOT NULL` with an empty-string
    default**, not nullable, specifically so the natural-key `UNIQUE`
    constraint below actually enforces uniqueness: SQL treats every
    `NULL` as distinct from every other `NULL` in a unique constraint,
    which would silently let two idempotent-retry upserts of the same
    `PARENT`-granularity row (where `childAsin`/`sku` are genuinely
    absent from Amazon's response, per the pinned contract) both insert
    successfully as "different" rows. Empty string is a concrete,
    equal-to-itself value, so the constraint works correctly; a read
    service must translate `''` back to `None` before this ever reaches
    an API response (§9 of the handover doc) — this is a storage-layer
    sentinel only, never a customer-facing value.
    """

    __tablename__ = "amazon_sales_traffic_product_facts"
    __table_args__ = (
        UniqueConstraint(
            "marketplace_participation_id",
            "request_window_start",
            "request_window_end",
            "asin_granularity",
            "parent_asin",
            "child_asin",
            "seller_sku",
            name="uq_amazon_sales_traffic_product_facts_natural_key",
        ),
        CheckConstraint(
            "asin_granularity IN ('PARENT', 'CHILD', 'SKU')",
            name="ck_amazon_sales_traffic_product_facts_asin_granularity",
        ),
        CheckConstraint(
            "request_window_start <= request_window_end",
            name="ck_amazon_sales_traffic_product_facts_window_order",
        ),
        # Structural proof that the granularity column and the identifier
        # columns actually agree — a PARENT-granularity row can never
        # smuggle a child/sku identifier, and a CHILD/SKU-granularity row
        # can never be missing the identifier its own granularity implies.
        CheckConstraint(
            "(asin_granularity = 'PARENT' AND child_asin = '' AND seller_sku = '') OR "
            "(asin_granularity = 'CHILD' AND child_asin <> '' AND seller_sku = '') OR "
            "(asin_granularity = 'SKU' AND child_asin <> '' AND seller_sku <> '')",
            name="ck_amazon_sales_traffic_product_facts_granularity_ids",
        ),
        CheckConstraint(
            "browser_session_percentage IS NULL OR browser_session_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_browser_session_pct",
        ),
        CheckConstraint(
            "session_percentage IS NULL OR session_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_session_pct",
        ),
        CheckConstraint(
            "page_views_percentage IS NULL OR page_views_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_page_views_pct",
        ),
        CheckConstraint(
            "buy_box_percentage IS NULL OR buy_box_percentage BETWEEN 0 AND 100",
            name="ck_amazon_sales_traffic_product_facts_buy_box_pct",
        ),
        ForeignKeyConstraint(
            ["last_ingestion_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_sales_traffic_product_facts_last_run_participation",
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[UUID] = mapped_column(Guid(), primary_key=True, default=_uuid)
    marketplace_participation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), nullable=False
    )
    request_window_start: Mapped[date] = mapped_column(Date(), nullable=False)
    request_window_end: Mapped[date] = mapped_column(Date(), nullable=False)
    asin_granularity: Mapped[str] = mapped_column(String(8), nullable=False)
    parent_asin: Mapped[str] = mapped_column(String(10), nullable=False)
    child_asin: Mapped[str] = mapped_column(String(10), nullable=False, server_default="")
    seller_sku: Mapped[str] = mapped_column(String(180), nullable=False, server_default="")
    item_name: Mapped[str | None] = mapped_column(String(500), nullable=True)
    currency_code: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # --- salesByAsin -------------------------------------------------------
    units_ordered: Mapped[int | None] = mapped_column(Integer, nullable=True)
    units_ordered_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordered_product_sales_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    ordered_product_sales_amount_b2b: Mapped[Decimal | None] = mapped_column(Numeric(19, 4), nullable=True)
    total_order_items: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_order_items_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # --- trafficByAsin -------------------------------------------------------
    browser_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_sessions_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_sessions_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sessions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sessions_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_session_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    browser_session_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    mobile_app_session_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    mobile_app_session_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    session_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    session_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    browser_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_page_views_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    mobile_app_page_views_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_views: Mapped[int | None] = mapped_column(Integer, nullable=True)
    page_views_b2b: Mapped[int | None] = mapped_column(Integer, nullable=True)
    browser_page_views_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    browser_page_views_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    mobile_app_page_views_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    mobile_app_page_views_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    page_views_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    page_views_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    buy_box_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    buy_box_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    # No upper-bound CHECK — see AmazonSalesAndTrafficDailyFact's docstring.
    unit_session_percentage: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    unit_session_percentage_b2b: Mapped[Decimal | None] = mapped_column(Numeric(7, 4), nullable=True)
    last_ingestion_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AmazonSalesAndTrafficSyncCheckpoint(Base):
    """12B.6A — durable, per-participation Sales and Traffic incremental
    high-water mark, for the *product-level daily* ingestion path only
    (the expensive, one-report-per-day path — §7 of the handover doc).
    Mirrors `AmazonOrdersSyncCheckpoint` exactly: one row per
    `marketplace_participation_id`; absence of a row means "never
    synced," never a shared/zero default. `synced_through_date` is a
    bare calendar `Date`, not a timestamp — this report's own grain is
    whole calendar days, never a moment in time (§1a). No overlap-window
    policy is baked into the stored value; the ingestion service applies
    the documented 1-day bounded overlap (§7) when computing the next
    day to request.
    """

    __tablename__ = "amazon_sales_traffic_sync_checkpoints"
    __table_args__ = (
        ForeignKeyConstraint(
            ["last_successful_run_id", "marketplace_participation_id"],
            ["amazon_ingestion_runs.id", "amazon_ingestion_runs.marketplace_participation_id"],
            name="fk_amazon_sales_traffic_sync_checkpoints_run_participation",
            ondelete="RESTRICT",
        ),
    )

    marketplace_participation_id: Mapped[UUID] = mapped_column(
        Guid(), ForeignKey("amazon_marketplace_participations.id", ondelete="RESTRICT"), primary_key=True
    )
    organization_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    seller_account_id: Mapped[UUID] = mapped_column(Guid(), nullable=False)
    synced_through_date: Mapped[date | None] = mapped_column(Date(), nullable=True)
    last_successful_run_id: Mapped[UUID | None] = mapped_column(Guid(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

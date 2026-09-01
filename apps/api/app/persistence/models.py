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
            "run_type IN ('marketplace_participations', 'listings')",
            name="ck_amazon_ingestion_runs_run_type",
        ),
        CheckConstraint(
            "run_type <> 'listings' OR "
            "(marketplace_participation_id IS NOT NULL AND seller_account_id IS NOT NULL)",
            name="ck_amazon_ingestion_runs_listings_scope_required",
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
        # Widens the PK into a composite unique key so amazon_seller_listings
        # can hold a composite FK guaranteeing a listing's last ingestion
        # run belongs to the same marketplace participation (see
        # AmazonSellerListing's docstring).
        UniqueConstraint(
            "id",
            "marketplace_participation_id",
            name="uq_amazon_ingestion_runs_id_marketplace_participation",
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

"""Amazon Ads API read-only integration foundation: connections, OAuth
state, advertiser profiles, Sponsored Products entities (campaigns, ad
groups, advertised products, keywords, product targets), daily
performance facts, the async-reporting state-machine ledger, and
per-profile sync checkpoints/errors.

Revision ID: 0019_amazon_ads_foundation
Revises: 0018_amazon_encrypted_secrets
Create Date: 2026-09-12

12C read-only foundation — schema only, additive. No Ads OAuth route,
client, ingestion service, or worker is wired to run against a live
Amazon endpoint by this migration's existence; every table here is a
wholly separate family from the existing SP-API tables (no FK into
`amazon_connections`, `amazon_oauth_states`, or `amazon_ingestion_runs`,
and none from those tables into here) — see
`docs/AI_HANDOVER/22_AMAZON_ADS_READONLY_FOUNDATION.md`.

Twelve new tables, in dependency order: `amazon_ads_connections`,
`amazon_ads_oauth_states`, `amazon_ads_profiles`, `amazon_ads_campaigns`,
`amazon_ads_ad_groups`, `amazon_ads_advertised_products`,
`amazon_ads_keywords`, `amazon_ads_product_targets`,
`amazon_ads_report_runs`, `amazon_ads_daily_performance_facts`,
`amazon_ads_sync_checkpoints`, `amazon_ads_sync_errors`.

`downgrade()` refuses (raises) if any row exists in any of the twelve
new tables — matching `0014`'s own precedent — since the pre-0019 schema
has no way to represent this data and downgrading in that state would
either violate a restored constraint or silently discard real evidence.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "0019_amazon_ads_foundation"
down_revision = "0018_amazon_encrypted_secrets"
branch_labels = None
depends_on = None

_ENTITY_STATE_CHECK = "state IN ('ENABLED', 'PAUSED', 'ARCHIVED')"

_NEW_TABLES_IN_DROP_ORDER = (
    "amazon_ads_sync_errors",
    "amazon_ads_sync_checkpoints",
    "amazon_ads_daily_performance_facts",
    "amazon_ads_report_runs",
    "amazon_ads_product_targets",
    "amazon_ads_keywords",
    "amazon_ads_advertised_products",
    "amazon_ads_ad_groups",
    "amazon_ads_campaigns",
    "amazon_ads_profiles",
    "amazon_ads_oauth_states",
    "amazon_ads_connections",
)


def upgrade() -> None:
    # --- amazon_ads_connections ---------------------------------------
    op.create_table(
        "amazon_ads_connections",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "amazon_seller_account_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_seller_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status", sa.String(32), nullable=False, server_default="not_connected"),
        sa.Column("token_reference", sa.String(128), nullable=True),
        sa.Column("authorized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_successful_sync_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("organization_id", name="uq_amazon_ads_connections_org"),
        sa.CheckConstraint(
            "status IN ('not_connected', 'pending_authorization', 'connected', 'revoked', 'error')",
            name="ck_amazon_ads_connections_status",
        ),
    )

    # --- amazon_ads_oauth_states ---------------------------------------
    op.create_table(
        "amazon_ads_oauth_states",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "amazon_seller_account_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_seller_accounts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("initiating_user_identity", sa.String(320), nullable=True),
        sa.Column("return_path", sa.String(128), nullable=False),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.Column("amazon_state", sa.String(256), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("state_hash", name="uq_amazon_ads_oauth_states_state_hash"),
    )
    op.create_index("ix_amazon_ads_oauth_states_org", "amazon_ads_oauth_states", ["organization_id"])
    op.create_index("ix_amazon_ads_oauth_states_connection_id", "amazon_ads_oauth_states", ["connection_id"])
    op.create_index("ix_amazon_ads_oauth_states_expires_at", "amazon_ads_oauth_states", ["expires_at"])

    # --- amazon_ads_profiles --------------------------------------------
    op.create_table(
        "amazon_ads_profiles",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "connection_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_connections.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("account_id", sa.String(64), nullable=True),
        sa.Column("account_type", sa.String(32), nullable=True),
        sa.Column("marketplace_country_code", sa.String(8), nullable=False),
        sa.Column("currency_code", sa.String(8), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("region", sa.String(8), nullable=False),
        sa.Column("display_name", sa.String(256), nullable=True),
        sa.Column("is_selected", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("sync_state", sa.String(32), nullable=False, server_default="not_synced"),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("connection_id", "profile_id", name="uq_amazon_ads_profiles_connection_profile"),
        sa.CheckConstraint("region IN ('NA', 'EU', 'FE')", name="ck_amazon_ads_profiles_region"),
        sa.CheckConstraint(
            "sync_state IN ('not_synced', 'awaiting_first_sync', 'synced', 'delayed', 'failed')",
            name="ck_amazon_ads_profiles_sync_state",
        ),
    )
    op.create_index("ix_amazon_ads_profiles_org", "amazon_ads_profiles", ["organization_id"])

    # --- amazon_ads_campaigns -------------------------------------------
    op.create_table(
        "amazon_ads_campaigns",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_campaign_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("targeting_type", sa.String(32), nullable=True),
        sa.Column("daily_budget", sa.Numeric(19, 4), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("portfolio_id", sa.String(64), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "ads_profile_id", "external_campaign_id", name="uq_amazon_ads_campaigns_profile_external_id"
        ),
        sa.CheckConstraint(_ENTITY_STATE_CHECK, name="ck_amazon_ads_campaigns_state"),
    )
    op.create_index("ix_amazon_ads_campaigns_org", "amazon_ads_campaigns", ["organization_id"])
    op.create_index("ix_amazon_ads_campaigns_profile", "amazon_ads_campaigns", ["ads_profile_id"])

    # --- amazon_ads_ad_groups -------------------------------------------
    op.create_table(
        "amazon_ads_ad_groups",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_campaign_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_ad_group_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("default_bid", sa.Numeric(19, 4), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "ads_profile_id", "external_ad_group_id", name="uq_amazon_ads_ad_groups_profile_external_id"
        ),
        sa.CheckConstraint(_ENTITY_STATE_CHECK, name="ck_amazon_ads_ad_groups_state"),
    )
    op.create_index("ix_amazon_ads_ad_groups_org", "amazon_ads_ad_groups", ["organization_id"])
    op.create_index("ix_amazon_ads_ad_groups_profile", "amazon_ads_ad_groups", ["ads_profile_id"])
    op.create_index("ix_amazon_ads_ad_groups_campaign", "amazon_ads_ad_groups", ["ads_campaign_id"])

    # --- amazon_ads_advertised_products ----------------------------------
    op.create_table(
        "amazon_ads_advertised_products",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_campaign_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_ad_group_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_ad_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_ad_id", sa.String(64), nullable=False),
        sa.Column("asin", sa.String(10), nullable=True),
        sa.Column("sku", sa.String(180), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("ads_profile_id", "external_ad_id", name="uq_amazon_ads_advertised_products_profile_ad"),
        sa.CheckConstraint(_ENTITY_STATE_CHECK, name="ck_amazon_ads_advertised_products_state"),
    )
    op.create_index("ix_amazon_ads_advertised_products_org", "amazon_ads_advertised_products", ["organization_id"])
    op.create_index(
        "ix_amazon_ads_advertised_products_profile", "amazon_ads_advertised_products", ["ads_profile_id"]
    )
    op.create_index(
        "ix_amazon_ads_advertised_products_ad_group", "amazon_ads_advertised_products", ["ads_ad_group_id"]
    )

    # --- amazon_ads_keywords ----------------------------------------------
    op.create_table(
        "amazon_ads_keywords",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_campaign_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_ad_group_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_ad_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_keyword_id", sa.String(64), nullable=False),
        sa.Column("keyword_text", sa.String(512), nullable=False),
        sa.Column("match_type", sa.String(32), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("bid", sa.Numeric(19, 4), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "ads_profile_id", "external_keyword_id", name="uq_amazon_ads_keywords_profile_external_id"
        ),
        sa.CheckConstraint(_ENTITY_STATE_CHECK, name="ck_amazon_ads_keywords_state"),
    )
    op.create_index("ix_amazon_ads_keywords_org", "amazon_ads_keywords", ["organization_id"])
    op.create_index("ix_amazon_ads_keywords_profile", "amazon_ads_keywords", ["ads_profile_id"])
    op.create_index("ix_amazon_ads_keywords_ad_group", "amazon_ads_keywords", ["ads_ad_group_id"])

    # --- amazon_ads_product_targets ---------------------------------------
    op.create_table(
        "amazon_ads_product_targets",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_campaign_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_campaigns.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_ad_group_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_ad_groups.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("external_target_id", sa.String(64), nullable=False),
        sa.Column("expression_type", sa.String(32), nullable=True),
        sa.Column("expression", sa.String(512), nullable=True),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("bid", sa.Numeric(19, 4), nullable=True),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "ads_profile_id", "external_target_id", name="uq_amazon_ads_product_targets_profile_external_id"
        ),
        sa.CheckConstraint(_ENTITY_STATE_CHECK, name="ck_amazon_ads_product_targets_state"),
    )
    op.create_index("ix_amazon_ads_product_targets_org", "amazon_ads_product_targets", ["organization_id"])
    op.create_index("ix_amazon_ads_product_targets_profile", "amazon_ads_product_targets", ["ads_profile_id"])
    op.create_index("ix_amazon_ads_product_targets_ad_group", "amazon_ads_product_targets", ["ads_ad_group_id"])

    # --- amazon_ads_report_runs -------------------------------------------
    op.create_table(
        "amazon_ads_report_runs",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("report_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="queued"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("amazon_report_id", sa.String(64), nullable=True),
        sa.Column("amazon_report_status", sa.String(16), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_class", sa.String(64), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("records_ingested", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'started', 'waiting_to_retry', 'succeeded', 'failed', 'timed_out')",
            name="ck_amazon_ads_report_runs_status",
        ),
    )
    op.create_index("ix_amazon_ads_report_runs_org", "amazon_ads_report_runs", ["organization_id"])
    op.create_index("ix_amazon_ads_report_runs_profile", "amazon_ads_report_runs", ["ads_profile_id"])
    op.create_index("ix_amazon_ads_report_runs_claimable", "amazon_ads_report_runs", ["status", "next_retry_at"])

    # --- amazon_ads_daily_performance_facts --------------------------------
    op.create_table(
        "amazon_ads_daily_performance_facts",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(32), nullable=False),
        sa.Column("entity_external_id", sa.String(64), nullable=False),
        sa.Column(
            "ads_campaign_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_campaigns.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "ads_ad_group_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_ad_groups.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("fact_date", sa.Date(), nullable=False),
        sa.Column("attribution_window", sa.String(8), nullable=False, server_default="14d"),
        sa.Column("currency_code", sa.String(8), nullable=True),
        sa.Column("impressions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clicks", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost", sa.Numeric(19, 4), nullable=False, server_default="0"),
        sa.Column("attributed_sales", sa.Numeric(19, 4), nullable=False, server_default="0"),
        sa.Column("attributed_conversions", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "last_report_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_report_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("source_synced_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "ads_profile_id",
            "entity_type",
            "entity_external_id",
            "fact_date",
            "attribution_window",
            name="uq_amazon_ads_daily_performance_facts_natural_key",
        ),
        sa.CheckConstraint(
            "entity_type IN ('campaign', 'ad_group', 'keyword', 'product_target', 'advertised_product')",
            name="ck_amazon_ads_daily_performance_facts_entity_type",
        ),
    )
    op.create_index(
        "ix_amazon_ads_daily_performance_facts_org", "amazon_ads_daily_performance_facts", ["organization_id"]
    )
    op.create_index(
        "ix_amazon_ads_daily_performance_facts_profile_date",
        "amazon_ads_daily_performance_facts",
        ["ads_profile_id", "fact_date"],
    )
    op.create_index(
        "ix_amazon_ads_daily_performance_facts_entity",
        "amazon_ads_daily_performance_facts",
        ["ads_profile_id", "entity_type", "entity_external_id"],
    )

    # --- amazon_ads_sync_checkpoints ---------------------------------------
    op.create_table(
        "amazon_ads_sync_checkpoints",
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            primary_key=True,
        ),
        sa.Column("organization_id", PGUUID(as_uuid=True), nullable=False),
        sa.Column("synced_through_date", sa.Date(), nullable=True),
        sa.Column(
            "last_successful_report_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_report_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    # --- amazon_ads_sync_errors ---------------------------------------------
    op.create_table(
        "amazon_ads_sync_errors",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "ads_profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_profiles.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "report_run_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("amazon_ads_report_runs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(64), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_amazon_ads_sync_errors_org", "amazon_ads_sync_errors", ["organization_id"])
    op.create_index("ix_amazon_ads_sync_errors_profile", "amazon_ads_sync_errors", ["ads_profile_id"])


def downgrade() -> None:
    conn = op.get_bind()

    def _count(table: str) -> int:
        return conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one()

    populated = {table: n for table in _NEW_TABLES_IN_DROP_ORDER if (n := _count(table))}
    if populated:
        raise RuntimeError(
            "Refusing to downgrade 0019: the pre-12C schema has no way to represent Amazon Ads "
            "data, and downgrading now would either violate a restored constraint or silently "
            f"discard it. Non-empty: {populated}. Remove or migrate this data out-of-band before "
            "downgrading, or accept that this migration cannot be safely reversed while it exists."
        )

    for table in _NEW_TABLES_IN_DROP_ORDER:
        op.drop_table(table)

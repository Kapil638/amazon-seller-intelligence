from functools import lru_cache
from uuid import UUID

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DEVELOPMENT_ORGANIZATION_ID = UUID("11111111-1111-4111-8111-111111111111")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = "Amazon Seller Intelligence API"
    # Canonical marketplace identifiers use Amazon domain form, e.g. amazon.in
    default_marketplace: str = "amazon.in"
    supported_marketplaces: tuple[str, ...] = ("amazon.in",)
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    product_provider: str = "rainforest"
    rainforest_api_key: SecretStr | None = None
    rainforest_base_url: str = "https://api.rainforestapi.com/request"
    rainforest_account_url: str = "https://api.rainforestapi.com/account"
    rainforest_timeout_seconds: float = 60
    rainforest_account_timeout_seconds: float = 20
    rainforest_cache_ttl_seconds: int = 600
    rainforest_search_cache_ttl_seconds: int = 900
    rainforest_account_cache_ttl_seconds: int = 60
    amazon_public_timeout_seconds: float = 12
    amazon_public_cache_ttl_seconds: int = 600
    amazon_public_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
    ai_provider: str = "openai"
    openai_api_key: SecretStr | None = None
    openai_admin_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("OPENAI_ADMIN_API_KEY", "OPENAI_ADMIN_KEY"),
    )
    openai_model: str = "gpt-5.4"
    openai_vision_model: str = ""
    openai_vision_max_images: int = 8
    openai_vision_allowed_hosts: str = ""
    openai_timeout_seconds: float = 60
    openai_max_output_tokens: int = 2500
    openai_budget_usd: float = 100.0
    openai_account_cache_ttl_seconds: int = 300
    openai_account_timeout_seconds: float = 20
    ai_cache_ttl_seconds: int = 2700
    report_max_upload_bytes: int = 26_214_400
    bulk_product_provider: str = "mock"
    bulk_ai_provider: str = "mock"
    bulk_live_provider_calls_enabled: bool = False
    max_bulk_asins: int = 100
    bulk_product_concurrency: int = 3
    product_cache_ttl_seconds: int = 86400
    ai_analysis_cache_ttl_seconds: int = 604800
    bulk_ai_top_n_default: int = 10
    ppc_wasted_spend_min: float = 500
    ppc_high_acos: float = 0.50
    ppc_low_cvr: float = 0.05
    ppc_low_cvr_min_clicks: int = 10
    ppc_strong_min_clicks: int = 10
    ppc_strong_min_cvr: float = 0.10
    business_low_conversion_min_sessions: int = 50
    business_low_conversion: float = 0.05
    business_low_buybox: float = 0.80
    business_low_buybox_min_sessions: int = 50
    database_url: str = ""
    supabase_url: str = ""
    supabase_service_role_key: SecretStr | None = None
    default_organization_id: UUID = DEFAULT_DEVELOPMENT_ORGANIZATION_ID
    default_organization_name: str = "Development"
    storage_uploads_bucket: str = "seller-report-uploads"
    storage_generated_bucket: str = "generated-reports"
    signed_url_ttl_seconds: int = 300
    sp_api_sandbox_enabled: bool = False
    sp_api_lwa_client_id: SecretStr | None = None
    sp_api_lwa_client_secret: SecretStr | None = None
    sp_api_sandbox_refresh_token: SecretStr | None = None
    sp_api_region: str = "na"
    sp_api_sandbox_base_url: str = ""
    sp_api_production_base_url: str = ""
    sp_api_lwa_token_url: str = "https://api.amazon.com/auth/o2/token"
    sp_api_timeout_seconds: float = 30
    sp_api_user_agent: str = "AmazonSellerIntelligence/12A.0 (Language=Python/3.12)"
    sp_api_application_name: str = "EWise"
    # Sandbox app id. Used for Test Connection identity only.
    sp_api_application_id: str = ""
    # Draft / production app id used on the Seller Central consent URL.
    sp_api_production_application_id: str = ""
    sp_api_production_lwa_client_id: SecretStr | None = None
    sp_api_production_lwa_client_secret: SecretStr | None = None
    sp_api_oauth_consent_base_url: str = ""
    sp_api_oauth_redirect_uri: str = ""
    sp_api_oauth_state_ttl_seconds: int = 600
    sp_api_consent_version_beta: bool = True
    amazon_secret_backend: str = Field(
        default="development",
        description=(
            "SecretProvider backend. development is the default live backend. "
            "production is reserved and fails closed until a cloud provider is implemented. "
            "Do not put cloud credentials here."
        ),
    )
    amazon_development_secret_store: str = Field(
        default=".data/amazon-development-secrets.json",
        description=(
            "Local file for DevelopmentSecretProvider seller secrets. "
            "Empty disables file persistence (in-memory only). Never a database path."
        ),
    )

    # 12B.3G — durable Listings synchronization job: retry/backoff and
    # concurrency defaults. Deliberately typed settings, not constants
    # buried in the worker/service, so operators can tune them per
    # environment without a code change.
    listings_sync_max_attempts: int = Field(
        default=5, ge=1, le=20,
        description="Total attempts (first try + retries) before a retryable Listings sync failure becomes the terminal 'rate_limited' outcome.",
    )
    listings_sync_base_backoff_seconds: float = Field(
        default=30.0, gt=0,
        description="Base delay for bounded exponential backoff with jitter when Amazon gives no usable Retry-After signal.",
    )
    listings_sync_max_backoff_seconds: float = Field(
        default=900.0, gt=0,
        description="Hard cap on any single computed retry delay (including a Retry-After value from Amazon), in seconds.",
    )
    listings_sync_max_total_retry_seconds: float = Field(
        default=3600.0, gt=0,
        description="Hard cap on total elapsed time (from first attempt) a Listings job may spend retrying before exhausting to a terminal failure.",
    )
    listings_sync_lease_duration_seconds: int = Field(
        default=300, ge=30, le=3600,
        description="How long a worker's exclusive claim on a Listings job is valid before it is eligible for stale-lease recovery by another worker.",
    )
    listings_sync_heartbeat_interval_pages: int = Field(
        default=1, ge=1,
        description="Renew the worker's lease/heartbeat after this many fetched pages (1 = every page). Progress-reporting cadence only — see listings_sync_heartbeat_time_interval_seconds for the actual lease-safety guarantee.",
    )
    listings_sync_heartbeat_time_interval_seconds: float = Field(
        default=60.0, gt=0,
        description=(
            "Wall-clock cadence (independent of page completion) at which the lease is renewed "
            "WHILE a single page fetch is in flight — this, not listings_sync_heartbeat_interval_pages, "
            "is what actually guarantees a lease cannot expire mid-request no matter how slow one "
            "Amazon call is. Must be kept comfortably below listings_sync_lease_duration_seconds."
        ),
    )
    listings_sync_max_global_concurrent_jobs: int = Field(
        default=4, ge=1, le=100,
        description="Maximum number of Listings jobs any worker fleet may run simultaneously, across all organizations.",
    )
    listings_sync_max_concurrent_jobs_per_organization: int = Field(
        default=1, ge=1, le=20,
        description="Maximum number of Listings jobs one organization may run simultaneously.",
    )
    listings_sync_trigger_cooldown_seconds: int = Field(
        default=300, ge=0, le=3600,
        description=(
            "Minimum time after a Listings job's own creation before the trigger endpoint accepts "
            "another request for the same marketplace participation, independent of the "
            "single-active-job guarantee. Raised from an earlier default of 30 seconds after a "
            "production incident: a manual sync button's own completed job frequently finishes in "
            "well under a minute for a small catalog, so 30 seconds left a wide window in which an "
            "impatient repeat click (or a second browser tab) triggered another genuine, billable "
            "Amazon SP-API call. 300 seconds (5 minutes) is long enough to absorb that normal human "
            "re-click pattern while still letting a seller re-run a sync well within the same working "
            "session after making a real change on Amazon's side. A `cancelled_before_start` "
            "administrative cancellation is deliberately excluded from this cooldown entirely — see "
            "`AmazonIngestionRunRepository.get_latest_cooldown_relevant_listings_run`."
        ),
    )
    listings_sync_max_queued_per_organization: int = Field(
        default=25, ge=1, le=1000,
        description=(
            "Queue-backlog safety valve: maximum number of status='queued' Listings jobs one "
            "organization may have outstanding at once (across distinct participations). "
            "Deliberately NOT a worker-execution-capacity limit — a legitimate new job is never "
            "rejected merely because workers are busy; only an unreasonably large unclaimed backlog "
            "triggers this. Execution capacity is enforced separately, only at claim time, by "
            "listings_sync_max_global_concurrent_jobs / listings_sync_max_concurrent_jobs_per_organization."
        ),
    )

    def consent_application_id(self) -> str:
        """Application id for website authorization. Production/Draft wins over sandbox."""
        production = self.sp_api_production_application_id.strip()
        if production:
            return production
        return self.sp_api_application_id.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()

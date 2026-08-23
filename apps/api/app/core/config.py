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
    sp_api_region: str = "eu"
    sp_api_sandbox_base_url: str = ""
    sp_api_lwa_token_url: str = "https://api.amazon.com/auth/o2/token"
    sp_api_timeout_seconds: float = 30
    sp_api_user_agent: str = "AmazonSellerIntelligence/12A.0 (Language=Python/3.12)"
    sp_api_application_name: str = "EWise"


@lru_cache
def get_settings() -> Settings:
    return Settings()

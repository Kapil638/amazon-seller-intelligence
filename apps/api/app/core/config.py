from functools import lru_cache
from uuid import UUID

from pydantic import AliasChoices, Field, SecretStr, model_validator
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
    # pilot-deployment-ewise — Starlette's TrustedHostMiddleware allowlist.
    # Local dev never sets this (uvicorn is reached directly on loopback,
    # never through a proxy that could forge Host), so the default keeps
    # existing dev/test behavior unchanged. A deployed environment behind
    # Railway's edge + Cloudflare must set this explicitly (e.g.
    # ["api.ewiseintelligence.com"]) — an unset/empty list here means "no
    # TrustedHostMiddleware is registered at all" (see main.py), never
    # "allow every host silently."
    allowed_hosts: list[str] = []
    # pilot-deployment-ewise — per-process SQLAlchemy pool bounds. Left
    # unset (None) by default, which keeps SQLAlchemy's own built-in
    # defaults (pool_size=5, max_overflow=10) for local development and
    # every existing test — those tests already pass against SQLite,
    # which ignores these settings entirely (see get_engine()). A
    # deployed environment sharing one remote Postgres pooler across
    # several separate processes (API + 4 workers) MUST set these
    # explicitly — the shared Supabase session-mode pooler used by this
    # project has been observed directly, this session, refusing new
    # connections (EMAXCONNSESSION) at its own hard pool_size=15 ceiling
    # under far less concurrent load than 5 unbounded per-process pools
    # would produce.
    db_pool_size: int | None = Field(default=None, ge=1, le=50)
    db_max_overflow: int | None = Field(default=None, ge=0, le=50)
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
    # pilot-deployment-ewise, correction 1 — production SecretProvider
    # (app/amazon/production_secrets.py) master key material. A JSON
    # object string mapping key-version-id -> base64(32 raw bytes), e.g.
    # '{"v1":"<base64 32-byte key>"}'. Every value must decode to exactly
    # 32 bytes (AES-256). Sourced only from a Railway encrypted
    # environment variable in any deployed environment — never committed,
    # never placed in a persisted .env file outside local throwaway
    # testing. Left empty by default: AMAZON_SECRET_BACKEND=production
    # with no (or invalid) key configuration fails closed at
    # SecretProviderFactory.create() time — see
    # parse_amazon_secret_encryption_keys's own docstring for the exact
    # validation performed and why it raises rather than warns.
    amazon_secret_encryption_keys: SecretStr = Field(
        default=SecretStr(""),
        description=(
            "JSON object {key_version: base64(32 raw AES-256 key bytes)}. "
            "Production SecretProvider master key material. Railway encrypted "
            "variable only. Never committed, never logged."
        ),
    )
    # Which key_version in amazon_secret_encryption_keys new writes use.
    # Existing rows stay decryptable under whatever key_version they were
    # written with (see AmazonEncryptedSecret's own docstring on
    # rotation) — this only selects the key for the *next* put_secret.
    amazon_secret_active_key_version: str = Field(
        default="",
        description=(
            "Active key_version for new production SecretProvider writes. "
            "Must be a key present in amazon_secret_encryption_keys."
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
    listings_worker_idle_poll_seconds: float = Field(
        default=5.0, gt=0, le=300,
        description=(
            "How long the Listings worker sleeps between claim attempts when no eligible job was "
            "found. Distinct from listings_sync_base_backoff_seconds/listings_sync_max_backoff_seconds "
            "(Amazon-throttling retry delay for a job already in flight) — this is the worker's own "
            "idle-poll cadence, unrelated to any single job."
        ),
    )
    listings_worker_poll_error_base_backoff_seconds: float = Field(
        default=2.0, gt=0,
        description=(
            "12B.3H — base delay for the Listings worker's own bounded exponential backoff after a "
            "recoverable error in the claim/poll step itself (e.g. a database connectivity failure — "
            "distinct from a job-processing failure, which claim_next_listings_job never raises for "
            "and which process_claimed_job already records as a normal terminal/retry outcome). "
            "Doubles on each consecutive poll failure, capped at listings_worker_poll_error_max_"
            "backoff_seconds, and resets to this base the moment a poll succeeds (finds a job or "
            "genuinely finds none) — so a transient outage (e.g. Supabase pausing/resuming) does not "
            "permanently degrade the worker's responsiveness once the database is reachable again."
        ),
    )
    listings_worker_poll_error_max_backoff_seconds: float = Field(
        default=60.0, gt=0,
        description="Hard cap on the Listings worker's own poll-error backoff delay — see listings_worker_poll_error_base_backoff_seconds.",
    )

    # 12B.4D — durable Orders synchronization job: retry/backoff and
    # concurrency defaults. Mirrors the Listings settings above in shape,
    # but Orders' documented searchOrders usage plan (0.0056 req/s, burst
    # 20 — see docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md) is
    # roughly 900x tighter than Listings', so the backoff/retry-budget
    # defaults are deliberately much larger, not copy-pasted.
    orders_sync_max_attempts: int = Field(
        default=8, ge=1, le=50,
        description="Total attempts (first try + retries) before a retryable Orders sync failure becomes the terminal 'rate_limited' outcome. Higher than Listings' default: a single throttle-driven retry cycle can legitimately need many attempts to walk a large backfill.",
    )
    orders_sync_base_backoff_seconds: float = Field(
        default=180.0, gt=0,
        description="Base delay for bounded exponential backoff with jitter when Amazon gives no usable Retry-After signal on a 429. Anchored near the documented ~178.6s sustained searchOrders interval, not a short generic default — see 12B.4A's rate-limit-implications section.",
    )
    orders_sync_max_backoff_seconds: float = Field(
        default=3600.0, gt=0,
        description="Hard cap on any single computed retry delay (including a Retry-After value from Amazon), in seconds. Anchored near the documented ~59.5-minute full-burst-refill time.",
    )
    orders_sync_max_total_retry_seconds: float = Field(
        default=14400.0, gt=0,
        description="Hard cap on total elapsed time (from first attempt) an Orders job may spend retrying before exhausting to a terminal failure. 4 hours — long enough for a multi-hour backfill under sustained throttling, per 12B.4A's own worked example.",
    )
    orders_sync_lease_duration_seconds: int = Field(
        default=300, ge=30, le=3600,
        description="How long a worker's exclusive claim on an Orders job is valid before it is eligible for stale-lease recovery by another worker. Same value as Listings — lease safety is governed by heartbeat renewal during an in-flight request, not by how long the job as a whole may run.",
    )
    orders_sync_heartbeat_time_interval_seconds: float = Field(
        default=60.0, gt=0,
        description="Wall-clock cadence at which the lease is renewed WHILE a single page fetch is in flight — see the identical Listings setting's docstring for why this, not a page-count cadence, is the actual lease-safety guarantee. Must stay comfortably below orders_sync_lease_duration_seconds.",
    )
    orders_sync_max_global_concurrent_jobs: int = Field(
        default=4, ge=1, le=100,
        description="Maximum number of Orders jobs any worker fleet may run simultaneously, across all organizations.",
    )
    orders_sync_max_concurrent_jobs_per_organization: int = Field(
        default=1, ge=1, le=20,
        description="Maximum number of Orders jobs one organization may run simultaneously.",
    )
    orders_sync_trigger_cooldown_seconds: int = Field(
        default=300, ge=0, le=3600,
        description="Minimum time after an Orders job's own creation before the trigger endpoint accepts another request for the same (seller_account, region, environment) scope. Mirrors listings_sync_trigger_cooldown_seconds's own production-incident rationale.",
    )
    orders_sync_max_queued_per_organization: int = Field(
        default=10, ge=1, le=1000,
        description="Queue-backlog safety valve: maximum number of status='queued' Orders jobs one organization may have outstanding at once. Lower than Listings' default since an Orders scope is coarser (one run already covers every included participation).",
    )
    orders_sync_default_lookback_days: int = Field(
        default=30, ge=1, le=730,
        description="Product-default initial-sync lookback window for a participation with no prior checkpoint — deliberately far narrower than the API's own 2-year retrieval ceiling (2016+ for JP/AU/SG). See 12B.4A Phase 4 point 1: a capacity ceiling and a product default are separate concepts, never conflated.",
    )
    orders_sync_checkpoint_overlap_seconds: int = Field(
        default=1800, ge=120, le=86400,
        description="Safety overlap subtracted from a participation's stored watermark before constructing the next lastUpdatedAfter — comfortably larger than the documented mandatory ~2-minute consistency gap, per 12B.4A Phase 4 point 2's 15-30 minute recommendation.",
    )
    orders_worker_idle_poll_seconds: float = Field(
        default=5.0, gt=0, le=300,
        description="How long the Orders worker sleeps between claim attempts when no eligible job was found.",
    )
    orders_worker_poll_error_base_backoff_seconds: float = Field(
        default=2.0, gt=0,
        description="Base delay for the Orders worker's own bounded exponential backoff after a recoverable error in the claim/poll step itself (e.g. a database connectivity failure) — mirrors listings_worker_poll_error_base_backoff_seconds.",
    )
    orders_worker_poll_error_max_backoff_seconds: float = Field(
        default=60.0, gt=0,
        description="Hard cap on the Orders worker's own poll-error backoff delay — see orders_worker_poll_error_base_backoff_seconds.",
    )
    sales_traffic_sync_lease_duration_seconds: int = Field(
        default=300, ge=30, le=3600,
        description="How long a worker's exclusive claim on a Sales and Traffic report job is valid before it is eligible for stale-lease recovery by another worker. Same value as Orders/Listings — see 12B.6A's own durable-polling design (this job releases its claim between polls rather than holding it for the report's full generation time).",
    )
    sales_traffic_sync_max_global_concurrent_jobs: int = Field(
        default=2, ge=1, le=100,
        description="Maximum number of Sales and Traffic report jobs any worker fleet may run simultaneously, across all organizations. Lower default than Orders — this report type's createReport budget (3 per 5 minutes, shared per seller) is far scarcer than Orders' own rate limit.",
    )
    sales_traffic_sync_max_concurrent_jobs_per_organization: int = Field(
        default=1, ge=1, le=20,
        description="Maximum number of Sales and Traffic report jobs one organization may run simultaneously.",
    )
    sales_traffic_sync_trigger_cooldown_seconds: int = Field(
        default=300, ge=0, le=3600,
        description="Minimum time after a Sales and Traffic report job's own creation before the trigger endpoint accepts another request for the same marketplace participation. Deliberately conservative given this report type's own scarce three-per-five-minutes createReport budget (handover doc §1).",
    )
    sales_traffic_worker_idle_poll_seconds: float = Field(
        default=5.0, gt=0, le=300,
        description="How long the Sales and Traffic worker sleeps between claim attempts when no eligible job was found.",
    )
    sales_traffic_worker_poll_error_base_backoff_seconds: float = Field(
        default=2.0, gt=0,
        description="Base delay for the Sales and Traffic worker's own bounded exponential backoff after a recoverable error in the claim/poll step itself (e.g. a database connectivity failure) — mirrors orders_worker_poll_error_base_backoff_seconds.",
    )
    sales_traffic_worker_poll_error_max_backoff_seconds: float = Field(
        default=60.0, gt=0,
        description="Hard cap on the Sales and Traffic worker's own poll-error backoff delay — see sales_traffic_worker_poll_error_base_backoff_seconds.",
    )

    # Worker liveness heartbeat — shared, database-backed availability
    # signal used identically by the Listings, Orders, Sales & Traffic,
    # and Inventory workers/triggers (fix/ingestion-worker-runtime-
    # availability, extended to Inventory once that milestone's own
    # worker was integrated with this shared mechanism). Each worker
    # process writes its own `amazon_worker_heartbeats` row on a fixed
    # cadence, independent of its claim/poll loop (so one long-running
    # job in progress never makes the process look dead); each domain's
    # sync-trigger service reads it before enqueueing a new job, so a
    # Sync click when no matching worker is actually running gets a
    # clear, immediate refusal instead of a job that queues forever. See
    # `app/amazon/worker_heartbeat.py`.
    worker_heartbeat_interval_seconds: float = Field(
        default=10.0, gt=0, le=300,
        description=(
            "How often each worker process (Listings/Orders/Sales & Traffic/Inventory) writes its own "
            "liveness heartbeat row, via a background loop independent of its claim/poll cycle — "
            "so a worker legitimately busy on one long-running job still reports itself alive."
        ),
    )
    worker_heartbeat_stale_after_seconds: float = Field(
        default=45.0, gt=0, le=3600,
        description=(
            "How old a worker's last heartbeat may be before the trigger endpoints treat that "
            "worker type as unavailable and refuse to enqueue a new job. Deliberately larger than "
            "worker_heartbeat_interval_seconds (a few missed intervals' worth of tolerance) so an "
            "isolated slow heartbeat write or brief GC pause never produces a false 'unavailable' "
            "refusal for a worker that is actually fine."
        ),
    )

    # pilot-deployment-ewise — a last-resort, same-process self-watchdog,
    # separate from worker_heartbeat_stale_after_seconds above.
    # `scripts/supervisor.py`'s own hung-worker detection (heartbeat
    # polled *externally*, the stalled process force-restarted) has no
    # equivalent on Railway, where each worker is its own isolated
    # service with nothing else watching it. A genuinely frozen asyncio
    # event loop can never detect itself via another task on that same
    # loop — this must run on a separate OS thread. Deliberately a much
    # larger threshold than worker_heartbeat_stale_after_seconds: that
    # setting only ever gates *new* job acceptance (an operator/Sync
    # button signal) and should stay sensitive; this one's only action
    # is a hard, unrecoverable process exit, so it must never fire on an
    # isolated slow tick or brief GC pause that would already have
    # self-healed well before this threshold — see the cross-field
    # validator below.
    worker_watchdog_stale_after_seconds: float = Field(
        default=600.0, gt=0, le=86400,
        description=(
            "How long a worker's own background heartbeat-write task may go without a single "
            "successful write, checked from a separate OS thread (not the worker's own asyncio "
            "event loop, which a genuinely stuck worker could never use to notice itself), before "
            "that thread calls os._exit(1) — a deliberate, hard process death so a real process "
            "supervisor (Railway's restart-on-exit policy in production) restarts this worker "
            "cleanly, rather than leaving it alive-but-unusable indefinitely."
        ),
    )
    worker_watchdog_check_interval_seconds: float = Field(
        default=30.0, gt=0, le=3600,
        description="How often the watchdog thread wakes up to check elapsed time since the last successful heartbeat write.",
    )

    @model_validator(mode="after")
    def _validate_worker_heartbeat_bounds(self) -> "Settings":
        if self.worker_heartbeat_stale_after_seconds <= self.worker_heartbeat_interval_seconds:
            raise ValueError(
                "worker_heartbeat_stale_after_seconds must exceed worker_heartbeat_interval_seconds "
                "(it must tolerate at least one missed heartbeat, or every worker would appear "
                "unavailable between writes)"
            )
        if self.worker_watchdog_stale_after_seconds <= self.worker_heartbeat_stale_after_seconds:
            raise ValueError(
                "worker_watchdog_stale_after_seconds must exceed worker_heartbeat_stale_after_seconds "
                "(the external-availability signal must always go stale, and have time to be acted "
                "on, well before this process would ever kill itself)"
            )
        return self

    # 12B.6B — FBA Inventory sync settings. Listings-shaped (in-memory
    # accumulate-then-reconcile, no durable pagination token — see
    # inventory_client.py's module docstring for why `nextToken`'s
    # 30-second lifetime rules that out), so this block mirrors the
    # Listings settings above field-for-field, plus two Inventory-specific
    # additions (`inventory_sync_max_pages`, `inventory_worker_min_page_
    # interval_seconds`) that have no Listings equivalent.
    inventory_sync_max_attempts: int = Field(
        default=5, ge=1, le=20,
        description="Maximum claim attempts for one Inventory job before it terminalizes as failed rather than retrying again.",
    )
    inventory_sync_deterministic_failure_max_attempts: int = Field(
        default=2, ge=1, le=20,
        description=(
            "Maximum claim attempts for an Inventory job whose failure is deterministic (a schema-validation "
            "failure that will reject the identical Amazon response on every retry, e.g. malformed_page) — "
            "applied as an independent cap on top of inventory_sync_max_attempts, never a higher one, since "
            "retrying against unchanged unparseable data cannot succeed differently."
        ),
    )
    inventory_sync_base_backoff_seconds: float = Field(
        default=30.0, gt=0,
        description="Base delay for an Inventory job's own retry backoff (used when Amazon's Retry-After is absent).",
    )
    inventory_sync_max_backoff_seconds: float = Field(
        default=900.0, gt=0,
        description="Hard cap on an Inventory job's own retry backoff delay, regardless of a larger Retry-After value.",
    )
    inventory_sync_max_total_retry_seconds: float = Field(
        default=3600.0, gt=0,
        description="Hard cap on the total elapsed wall-clock time (since first claim) an Inventory job may spend retrying before terminalizing as failed.",
    )
    inventory_sync_lease_duration_seconds: int = Field(
        default=300, ge=30, le=3600,
        description="How long a worker's exclusive claim on an Inventory job is valid before it is eligible for stale-lease recovery by another worker.",
    )
    inventory_sync_max_global_concurrent_jobs: int = Field(
        default=4, ge=1, le=100,
        description="Maximum number of Inventory jobs any worker fleet may run simultaneously, across all organizations.",
    )
    inventory_sync_max_concurrent_jobs_per_organization: int = Field(
        default=1, ge=1, le=20,
        description="Maximum number of Inventory jobs one organization may run simultaneously.",
    )
    inventory_sync_trigger_cooldown_seconds: int = Field(
        default=300, ge=0, le=3600,
        description="Minimum time after an Inventory job's own completion before the trigger endpoint accepts another request for the same marketplace participation.",
    )
    inventory_sync_max_queued_per_organization: int = Field(
        default=25, ge=1, le=1000,
        description="Queue-backlog safety valve for Inventory sync requests — never a worker-execution-capacity gate. See count_queued_inventory_runs_for_organization.",
    )
    inventory_sync_max_pages: int = Field(
        default=500, ge=1, le=5000,
        description=(
            "ASI's own configurable safety bound on getInventorySummaries pagination — Amazon documents no hard "
            "result ceiling for this operation the way Listings' searchListingsItems documents 1000 items/50 pages "
            "(12B.6B audit finding: NOT DOCUMENTED). Hitting this bound while Amazon still returns a nextToken is a "
            "terminal, non-retryable pagination_bound_exceeded failure — the visible signal to raise this setting, "
            "never a silently truncated snapshot."
        ),
    )
    inventory_worker_min_page_interval_seconds: float = Field(
        default=0.5, ge=0, le=10,
        description=(
            "Proactive minimum delay between consecutive getInventorySummaries page requests within one traversal, "
            "matching the pinned 2 requests/second usage-plan ceiling. Defense-in-depth only — never the sole "
            "rate-limit mechanism; the actual authority is inventory_client.py's reactive handling of a real 429 "
            "response (honors Amazon's own Retry-After when present)."
        ),
    )
    inventory_worker_idle_poll_seconds: float = Field(
        default=5.0, gt=0, le=300,
        description="How long the Inventory worker sleeps between claim attempts when no eligible job was found.",
    )
    inventory_worker_poll_error_base_backoff_seconds: float = Field(
        default=2.0, gt=0,
        description="Base delay for the Inventory worker's own bounded exponential backoff after a recoverable error in the claim/poll step itself.",
    )
    inventory_worker_poll_error_max_backoff_seconds: float = Field(
        default=60.0, gt=0,
        description="Hard cap on the Inventory worker's own poll-error backoff delay — see inventory_worker_poll_error_base_backoff_seconds.",
    )

    # 12B.6C — Inventory Health thresholds. ASI/EWise product policy, not
    # Amazon guidance — reviewed and approved as the V1 defaults. Kept as
    # typed backend configuration (not an organization-mutable database
    # table) for this milestone; every API evidence response returns the
    # exact values used alongside formula_version, so a future change here
    # is always visible/auditable rather than silently altering history.
    inventory_health_low_coverage_days_threshold: float = Field(
        default=14.0, gt=0,
        description="Below this many fulfillable days of cover, a SKU is classified low_coverage. Exactly this value is healthy_coverage.",
    )
    inventory_health_high_coverage_days_threshold: float = Field(
        default=90.0, gt=0,
        description="Above this many fulfillable days of cover, a SKU is classified high_coverage. Exactly this value is healthy_coverage.",
    )
    inventory_health_min_eligible_window_days: int = Field(
        default=7, ge=1, le=365,
        description="A selected Sales & Traffic product fact covering fewer inclusive days than this is insufficient_window, not eligible.",
    )
    inventory_health_preferred_window_days: int = Field(
        default=30, ge=1, le=365,
        description="The canonical product-fact window length preferred when selecting one fact per SKU among several sharing the same latest end date.",
    )
    inventory_health_max_inventory_age_seconds: float = Field(
        default=48 * 3600, gt=0,
        description="Inventory observation age beyond which freshness_state reports stale_inventory. Inventory sync is on-demand, not scheduled, in this milestone — generous by design.",
    )
    inventory_health_max_sales_age_seconds: float = Field(
        default=48 * 3600, gt=0,
        description="Sales & Traffic ingestion age beyond which freshness_state reports stale_sales.",
    )

    @model_validator(mode="after")
    def _validate_inventory_health_coverage_thresholds(self) -> "Settings":
        if self.inventory_health_low_coverage_days_threshold > self.inventory_health_high_coverage_days_threshold:
            raise ValueError(
                "inventory_health_low_coverage_days_threshold must not exceed inventory_health_high_coverage_days_threshold"
            )
        return self

    @model_validator(mode="after")
    def _validate_inventory_worker_poll_error_backoff_bounds(self) -> "Settings":
        if self.inventory_worker_poll_error_base_backoff_seconds > self.inventory_worker_poll_error_max_backoff_seconds:
            raise ValueError(
                "inventory_worker_poll_error_base_backoff_seconds must not exceed "
                "inventory_worker_poll_error_max_backoff_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_sales_traffic_worker_poll_error_backoff_bounds(self) -> "Settings":
        if (
            self.sales_traffic_worker_poll_error_base_backoff_seconds
            > self.sales_traffic_worker_poll_error_max_backoff_seconds
        ):
            raise ValueError(
                "sales_traffic_worker_poll_error_base_backoff_seconds must not exceed "
                "sales_traffic_worker_poll_error_max_backoff_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_orders_worker_poll_error_backoff_bounds(self) -> "Settings":
        if self.orders_worker_poll_error_base_backoff_seconds > self.orders_worker_poll_error_max_backoff_seconds:
            raise ValueError(
                "orders_worker_poll_error_base_backoff_seconds must not exceed "
                "orders_worker_poll_error_max_backoff_seconds"
            )
        return self

    @model_validator(mode="after")
    def _validate_listings_worker_poll_error_backoff_bounds(self) -> "Settings":
        # `gt=0` on each field already rules out zero/negative individually;
        # this additionally rules out a base that exceeds its own cap — an
        # inverted configuration that would make every single poll failure
        # immediately jump to (and get clamped at) the max delay, silently
        # discarding the intended gradual doubling, rather than raising a
        # clear configuration error at startup.
        if self.listings_worker_poll_error_base_backoff_seconds > self.listings_worker_poll_error_max_backoff_seconds:
            raise ValueError(
                "listings_worker_poll_error_base_backoff_seconds must not exceed "
                "listings_worker_poll_error_max_backoff_seconds"
            )
        return self

    def consent_application_id(self) -> str:
        """Application id for website authorization. Production/Draft wins over sandbox."""
        production = self.sp_api_production_application_id.strip()
        if production:
            return production
        return self.sp_api_application_id.strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()

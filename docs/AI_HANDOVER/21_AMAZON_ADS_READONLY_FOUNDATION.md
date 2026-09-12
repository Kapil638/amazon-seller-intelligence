# 21. Amazon Ads Read-Only Integration Foundation (12C foundation)

Status: **built, complete, inactive.** No live Amazon Ads call has been
made. No Cloudflare, Railway, Supabase production, or Amazon console
change has been made. Nothing here starts, enables, or deploys anything
by its own existence — see §8 (activation plan) for what a human must
still do, in order, before any of it goes live.

This is a foundation pass, not milestone 12C itself — the numbered
12B.2–12B.9 roadmap in the root `CLAUDE.md` is unaffected and unrenamed;
this work sits alongside it at the operator's explicit request.

## 1. Architecture summary

Mirrors the existing SP-API integration's proven patterns (OAuth state
machine, `SecretProvider`-backed token storage, lease/heartbeat job
claiming, idempotent upserts) but as a **wholly separate table and code
family** — no foreign key crosses between `amazon_ads_*` tables and
`amazon_connections` / `amazon_oauth_states` / `amazon_ingestion_runs`,
and Ads credentials are distinct settings (`ads_*`) from SP-API's
(`sp_api_*`). An Amazon Ads profile id is never assumed interchangeable
with an SP-API selling-partner id.

New backend modules (`apps/api/app/amazon/`):

| Module | Purpose |
|---|---|
| `ads_oauth.py` | State generation/hash/expiry, consent-URL builder, closed return-path allowlist |
| `ads_lwa_token.py` | Authorization-code exchange + refresh-token grant (own `Ads*` exceptions, never `SpApi*`) |
| `ads_models.py` | Pydantic DTOs for Ads API responses (profiles, campaigns, ad groups, keywords, targets, reports) |
| `ads_client.py` | `AmazonAdsApiClient` protocol, `MockAmazonAdsApiClient` (used everywhere in tests), `HttpAmazonAdsApiClient` (never invoked by any wired path this pass) |
| `ads_connection.py` | OAuth start/callback, profile listing/selection, connection overview |
| `ads_report_service.py` | Reporting v3 async state machine (create → poll → download → ingest → checkpoint) |
| `ads_read.py` | Read-only, paginated, tenant-scoped API response service |
| `ads_worker.py` | Inert worker entrypoint (`ASI_ADS_WORKER_ENABLED` fail-closed, exit code 3 when unset) — never deployed |

New routes: `apps/api/app/api/routes/amazon_ads_connection.py`,
`apps/api/app/api/routes/amazon_ads.py`.

New migration: `migrations/versions/0019_amazon_ads_foundation.py`
(single Alembic head, additive, 12 new tables — see §3).

## 2. OAuth paths and proposed production URLs

| Purpose | Path |
|---|---|
| Login URI (public, once registered) | `https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/login` |
| Redirect/Callback URI (public, once registered) | `https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/callback` |
| Authenticated start (in-app) | `POST /api/v1/amazon/ads-connection/authorize` |
| Connection status | `GET /api/v1/amazon/ads-connection/status` |
| List profiles | `GET /api/v1/amazon/ads-connection/profiles` |
| Select profile | `POST /api/v1/amazon/ads-connection/profiles/select` |

**Neither URL has been registered with Amazon.** Per §7 of
`20_PILOT_DEPLOYMENT_EWISE.md`, register under an **EWise-controlled**
Amazon Developer account, as a **Partner** application (not Direct
Advertiser) — this is a one-time, unchangeable-after-the-fact choice.

LWA consent URL used by `login`/`authorize`:
`https://www.amazon.com/ap/oa?client_id=...&scope=advertising%3A%3Acampaign_management&response_type=code&redirect_uri=...&state=...`
— confirmed from Amazon's Ads API authorization guide and corroborating
sources this pass (the docs site is a client-rendered SPA that did not
return usable content to this environment's fetch tool; see §9 for what
was and wasn't directly confirmed).

## 3. Data model (migration `0019_amazon_ads_foundation`)

| Table | Purpose |
|---|---|
| `amazon_ads_connections` | One per organization. Status, `token_reference` (opaque `SecretProvider` pointer), best-effort `amazon_seller_account_id` cross-reference |
| `amazon_ads_oauth_states` | Hashed, single-use, TTL'd state bound to organization + connection + best-effort initiating-user identity + a closed return-path allowlist |
| `amazon_ads_profiles` | One row per advertiser profile returned by `GET /v2/profiles`; `is_selected` marks the one profile synchronization is allowed for |
| `amazon_ads_campaigns` / `_ad_groups` / `_advertised_products` / `_keywords` / `_product_targets` | Sponsored Products entities, each unique per `(ads_profile_id, external_*_id)` |
| `amazon_ads_daily_performance_facts` | Decimal-safe (`Numeric(19,4)`), currency-tagged, one row per `(profile, entity_type, entity_external_id, date, attribution_window)` |
| `amazon_ads_report_runs` | Async-report state machine ledger — own lease/heartbeat columns, same proven shape as `AmazonIngestionRun`'s claim design |
| `amazon_ads_sync_checkpoints` | One row per profile, rolling high-water mark |
| `amazon_ads_sync_errors` | Queryable failure history, independent of report-run retention |

Verified this pass: single Alembic head (`0019_amazon_ads_foundation`,
`down_revision = 0018_amazon_encrypted_secrets`); migration SQL (offline
PostgreSQL compile) matches `Base.metadata` table-for-table and
column-for-column (`tests/test_migration_chain_matches_orm_metadata.py`);
`downgrade()` refuses if any of the 12 new tables holds a row, matching
the `0014` precedent. **Not applied to production Supabase.**

A disposable-PostgreSQL upgrade/downgrade round-trip test (the pattern
used by `tests/postgres/test_disposable_postgres_*.py` for earlier
migrations) was **not** added this pass — it needs Docker, and the two
checks that exist (single-head + offline-SQL/ORM match) already prove
what this task asked to verify. Add one before this migration is applied
to a real database, following `test_disposable_postgres_amazon_encrypted_secrets.py`'s
pattern.

## 4. Report lifecycle

1. `create_report_request` — persists a `queued` `AmazonAdsReportRun` row.
2. `process_one_claimed_job` claims exactly one eligible row (PostgreSQL
   advisory lock + `SKIP LOCKED`, per-profile **and** global concurrency
   caps — `ads_sync_max_concurrent_jobs_per_profile` / `..._global_...`).
3. Refreshes the Ads access token (outside any DB transaction).
4. Calls `POST /reporting/reports`; persists `amazon_report_id`
   immediately (never re-created on restart).
5. Polls `GET /reporting/reports/{id}` with a bounded attempt count
   (`ads_report_poll_max_attempts`), heartbeating the lease between
   polls. **No database transaction is held open during a poll wait.**
6. On `COMPLETED`: downloads (size-bounded), validates JSON shape,
   upserts facts idempotently (natural-key upsert — re-ingesting the same
   report never duplicates a row), advances the sync checkpoint, marks
   the run `succeeded` — all in one final transaction.
7. On a terminal Amazon failure or transport error: retries with backoff
   up to the bounded attempt count, then fails; every retry/failure is
   also recorded in `amazon_ads_sync_errors`.
8. A stale lease (worker died mid-job) terminalizes to `timed_out` on the
   next claim attempt — it is **not** silently re-queued (matches
   `AmazonIngestionRun`'s own documented guarantee: recovery means "never
   hangs forever as `started`," not "automatically retries").

Incremental sync: `next_sync_window()` re-requests the last
`ads_sync_lookback_days` days even when already synced, so late Amazon
attribution adjustments are refreshed rather than permanently skipped.

## 5. Tenant boundaries

Every table carries `organization_id`; every entity table also carries
`ads_profile_id`. Every read/write repository method takes both and
filters by them — cross-tenant and cross-profile lookups return "not
found," never another tenant's row (see
`tests/test_amazon_ads_connection_service.py::test_select_profile_rejects_cross_connection_profile`
and `tests/test_amazon_ads_routes.py::test_select_profile_rejects_a_profile_belonging_to_another_organization`).
Per-profile concurrency caps on report jobs mean one advertiser's
throttling/failure can never block another's synchronization.

**Known architectural gap, recorded rather than papered over:** this
codebase has no `users` table yet (`current_organization_id()` resolves
from `Settings.default_organization_id`, not a request-scoped session —
see `app/persistence/database.py`). "Initiating user" binding on the
OAuth state is therefore the verified Cloudflare Access identity
(email/subject) captured at state-creation time — a string, never a
foreign key to a user that doesn't exist. When a real `users` table is
built, `amazon_ads_oauth_states.initiating_user_identity` should be
migrated to a proper foreign key.

## 6. Rate-limit / throttling strategy

- `AdsApiRateLimitedError` carries `retry_after_seconds` only when
  Amazon's response actually included a usable `Retry-After` header
  (never guessed) — mirrors `SpApiRateLimitedError` exactly.
- A 429 during report-status polling sleeps and retries the same poll
  attempt (does not consume a job-level retry).
- A 429/5xx during report creation or download consumes one bounded
  job-level retry via `_retry_or_fail`.
- Per-profile and global concurrency caps (§4, §5) are the primary
  defense against one advertiser's throttling cascading to others —
  simpler and more robust than per-call jittered backoff alone.

## 7. Required future environment variables (names only — no values here)

| Variable | Purpose |
|---|---|
| `ADS_LWA_CLIENT_ID` | Ads Partner application LWA client id |
| `ADS_LWA_CLIENT_SECRET` | Ads Partner application LWA client secret |
| `ADS_OAUTH_REDIRECT_URI` | Must exactly match what's registered with Amazon |
| `ADS_LWA_TOKEN_URL` | Defaults to the shared LWA endpoint; override only if Amazon documents otherwise |
| `ADS_OAUTH_CONSENT_BASE_URL` | Defaults to `https://www.amazon.com/ap/oa` |
| `ADS_OAUTH_SCOPE` | Defaults to `advertising::campaign_management` |
| `ASI_ADS_WORKER_ENABLED` | Must stay unset/false until the activation plan (§8) is fully complete |

All other `ads_*` settings have safe numeric/string defaults (see
`apps/api/app/core/config.py`) and do not need new Railway variables
unless tuning is required.

## 8. Cloudflare — required public paths, not yet added

`api.core.cloudflare_access.PUBLIC_PATHS` (code-level allowlist) now
includes `/api/v1/amazon/ads-connection/login` and `.../callback` —
this application's own independent exemption, mirroring the SP-API
pair. **The matching Cloudflare Access bypass application/policy has
deliberately not been created** (explicit constraint this pass). Until
it is, these two paths remain unreachable through the live deployment
even though the code path is public-safe today. Every other
`/api/v1/amazon/ads*` route requires a verified Cloudflare Access
identity like any other protected route — none were added to any bypass
list.

## 9. Recorded assumptions (docs site did not render for this environment)

Amazon's Ads API documentation site (`advertising.amazon.com/API/docs/...`)
is a client-rendered SPA that returned only a page title to this
environment's fetch tool. What follows was corroborated instead from
Amazon's Ads API authorization guide summaries, Amazon's own
`amzn/ads-advanced-tools-docs` GitHub issue tracker, and long-standing,
widely-consistent third-party integration documentation — treat as
**high confidence, not officially re-verified against a live response
this pass**:

- Regional base URLs: NA `https://advertising-api.amazon.com`, EU
  `https://advertising-api-eu.amazon.com`, FE
  `https://advertising-api-fe.amazon.com`.
- Headers: `Amazon-Advertising-API-ClientId`, `Authorization: Bearer
  <token>`, `Amazon-Advertising-API-Scope: <profileId>`.
- Reporting v3 report object fields: `reportId`, `status`, `url`,
  `urlExpiresAt`, `fileSize`, `failureReason`, `createdAt`, `updatedAt`,
  `startDate`, `endDate`, `configuration`, `name`; format `GZIP_JSON`.
- **Lower confidence, explicitly flagged in code
  (`app/amazon/ads_models.py`):** the exact non-`PENDING` report status
  enum values (`PROCESSING`, `COMPLETED`, `CANCELLED`, `FAILURE`) —
  corroborated but not directly confirmed against one full official
  response. Verify against a real sandbox/production response before
  the reporting state machine is ever pointed at a live Ads account.
- **Not confirmed at all, flagged in `ads_client.py`:** the exact
  request body shape and versioned media-type headers for Sponsored
  Products v3 "list" endpoints (campaigns/ad groups/keywords/targets).
  `HttpAmazonAdsApiClient`'s entity-list methods are a generic,
  documented placeholder — verify against real sandbox responses before
  wiring them to a live call.

## 10. Activation plan (do in this order; each step gates the next)

1. Amazon Ads API Partner access approval completes (already submitted,
   per the operator's own context — awaiting Amazon).
2. Register the Ads application under an **EWise-controlled** Amazon
   Developer account as a **Partner** application, using the exact URLs
   in §2.
3. Set `ADS_LWA_CLIENT_ID`, `ADS_LWA_CLIENT_SECRET`,
   `ADS_OAUTH_REDIRECT_UI` on the `api` Railway service (masked secrets,
   never printed — same pattern as every other credential in this repo).
4. Add the Cloudflare Access bypass for the two paths in §8.
5. Apply migration `0019` to production Supabase (`alembic upgrade
   head`) during a low-traffic window — additive only, no data
   migration needed.
6. Manually complete one real OAuth authorization (as the operator, on
   AJ Duran's own Ads account) and confirm `GET
   /api/v1/amazon/ads-connection/status` shows `connected` with the
   expected profile(s).
7. Verify §9's flagged assumptions against the real profiles/report
   responses now observable; fix `ads_models.py`/`ads_client.py` if any
   assumption was wrong.
8. Select the correct AJ Duran profile via `POST
   .../profiles/select`.
9. Run `AmazonAdsReportService.process_one_claimed_job()` manually
   (still not a deployed worker) against one real, small date range;
   confirm ingested facts look correct.
10. Only after step 9 is verified correct: deploy a dedicated Ads worker
    (see §11) and set `ASI_ADS_WORKER_ENABLED=true` on it alone.

**Rollback at any point:** unset `ASI_ADS_WORKER_ENABLED` (worker exits
immediately, code 3); the OAuth routes fail closed the instant any of
the three required env vars is unset; no other service is affected,
since every Ads table is a separate family with no foreign key into
existing SP-API or business tables.

## 11. Future Ads worker deployment recommendation (not created this pass)

Modeled directly on the 4 existing SP-API workers' own measured
footprint (see `docs/AI_HANDOVER/20_PILOT_DEPLOYMENT_EWISE.md` and this
session's own Railway resize history): each existing worker runs
comfortably at **0.25 vCPU / 0.25 GB**. Recommend the same starting
point for a future `ads-worker` service:

- **Resources:** 0.25 vCPU / 0.25 GB, same restart policy
  (`ON_FAILURE`, 10 retries) as the existing 4.
- **DB connections:** `DB_POOL_SIZE=1`, `DB_MAX_OVERFLOW=1` (max 2,
  matching every existing worker) — this would bring the total
  connection ceiling from the current 9 (api: 1, 4 workers: 2 each) to
  **11**, still under Supabase's pooler hard limit of 15. Re-verify that
  ceiling before deploying if any other service's pool settings have
  changed since this pass.
- **Region:** match `api`'s current region/builder configuration
  directly — do not re-attempt the `asia-southeast1` builder pool
  without first checking whether the `NIXPACKS_UV_VERSION` pin is still
  needed (see this session's own build-failure history for that region).
- **Enable flag:** `ASI_ADS_WORKER_ENABLED=true` only after the full
  activation plan (§10) is complete and manually verified.

## 12. Security and compliance review against the published Privacy Notice

| Commitment (`/privacy`) | How this foundation satisfies it |
|---|---|
| Ads data used only for the authorizing advertiser | Every table/query is organization + profile scoped; no cross-tenant query exists |
| No sale/publication of Amazon data | No such endpoint exists |
| No sharing between unrelated advertisers | Per-profile isolation throughout; no aggregate-across-profiles query exists |
| No behavioral retargeting / consumer re-identification | No consumer-level PII field exists anywhere in the schema (campaign/ad-group/keyword/target/fact tables carry no customer identifiers) |
| No credentials sent to OpenAI | This foundation makes no OpenAI call at all |
| Minimal data access | Fields captured are exactly the listed set in the governing task, nothing broader |
| Secrets encrypted | Ads refresh token stored via the existing `SecretProvider` (AES-256-GCM in production), never a plaintext column |
| Sensitive values redacted from logs | `ads_client.py`'s `redact_headers`; no report body or raw Amazon response is ever logged (see `ads_report_service.py`'s module docstring) |

**Deletion design (documented here; not yet implemented as an
endpoint):** disconnecting should (a) revoke/delete the stored
`SecretProvider` reference, (b) set `amazon_ads_connections.status =
'revoked'`, (c) leave historical `amazon_ads_daily_performance_facts`
rows in place unless the advertiser separately requests data deletion
(matching `/privacy`'s "Retention and deletion" 90-day standard for
detailed imported data), (d) stop any further `AmazonAdsReportRun`
creation for that profile. **This delete/disconnect endpoint itself was
not built this pass** — do not advertise it as available until it
exists; this section is a design commitment for the next slice, not a
completed feature.

## 13. Decisions requiring operator input

- Confirm the exact Amazon Ads report type(s)/columns beyond the
  Sponsored Products daily campaign report this foundation defaults to
  (`spCampaigns`, `groupBy: ["campaign"]`) — ad-group/keyword/target-level
  reports use the same state machine but were not pre-built as separate
  report requests this pass.
- Confirm whether `ads_sync_lookback_days` (default 3) matches Amazon's
  actual attribution-adjustment window for Sponsored Products (Amazon
  does not always document this precisely; 3 is a conservative default,
  not an Amazon-documented number).
- Confirm the deletion/disconnect endpoint's exact behavior (§12) before
  it is built, since it is a public commitment on `/privacy`.

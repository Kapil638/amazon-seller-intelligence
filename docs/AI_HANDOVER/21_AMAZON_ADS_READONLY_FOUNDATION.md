# 21. Amazon Ads Read-Only Integration Foundation (12C foundation)

Status: **built, complete, inactive.** No live Amazon Ads call has been
made. No Cloudflare, Railway, Supabase production, or Amazon console
change has been made. Nothing here starts, enables, or deploys anything
by its own existence — see §10 (activation plan) for what a human must
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
| `ADS_API_BACKEND` | Which Ads REST client is actually constructed — `disabled` (default, fail-closed, no network call possible), `mock` (tests/local dev only), or `http` (production). Never falls back silently between these; an unrecognized value raises. See `app.amazon.ads_client.build_amazon_ads_api_client`. |
| `ADS_LWA_CLIENT_ID` | Ads Partner application LWA client id |
| `ADS_LWA_CLIENT_SECRET` | Ads Partner application LWA client secret |
| `ADS_OAUTH_REDIRECT_URI` | Must exactly match what's registered with Amazon |
| `ADS_LWA_TOKEN_URL` | Defaults to the shared LWA endpoint; override only if Amazon documents otherwise |
| `ADS_OAUTH_CONSENT_BASE_URL` | Defaults to `https://www.amazon.com/ap/oa` |
| `ADS_OAUTH_SCOPE` | Defaults to `advertising::campaign_management` |
| `ASI_ADS_WORKER_ENABLED` | Must stay unset/false until the activation plan (§10) is fully complete. The worker also independently requires `ADS_API_BACKEND=http` — either alone is insufficient to start it for real (see `app.amazon.ads_worker.main`'s two distinct exit codes, `EXIT_DISABLED` and `EXIT_BACKEND_NOT_HTTP`). |

All other `ads_*` settings have safe numeric/string defaults (see
`apps/api/app/core/config.py`) and do not need new Railway variables
unless tuning is required.

**Two independent gates, not one:** `ADS_API_BACKEND` controls which
Ads REST *client* gets constructed (the network layer); `ADS_LWA_CLIENT_ID`/
`_SECRET`/`ADS_OAUTH_REDIRECT_URI` control whether the OAuth *flow* is
considered configured (`AmazonAdsConnectionService.is_configured`).
Both must be satisfied independently before a live call can happen —
setting only one leaves the feature inactive.

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

**Second verification pass (production-client wiring task):** the
official docs site still did not render for this environment's fetch
tool (client-rendered SPA, returns only a page title). Additional
corroboration found this pass, from Amazon's own `amzn/ads-advanced-
tools-docs` repository and third-party Postman-collection references,
without changing any of the above:

- `Accept`/`Content-Type: application/vnd.spCampaign.v3+json` confirmed
  again for the v3 Sponsored Products campaigns list, and the
  `/sp/campaigns/list` POST path confirmed again by name.
- `GET /v2/profiles` and the `Amazon-Advertising-API-Scope` header
  confirmed again.
- One third-party Postman mirror (`dbrent-amazon/Advertising-API-
  Postman-Collection`) shows an **older, GET-based** `/v2/campaigns`,
  `/v2/adGroups`, `/v2/keywords` pattern — this is the deprecated v2
  list shape, not the v3 one this codebase implements; it was not used
  to change anything, flagged here only so a future reader who finds
  that mirror doesn't mistake it for current.
- A `dbrent-amazon` gist describing report creation via `POST /v1/
  keywords/report` with statuses `IN_PROGRESS`/`SUCCESS` is the
  **deprecated v1 Reporting API** (different endpoint family, different
  status vocabulary entirely from v3's `PENDING`/`COMPLETED`/etc.) — also
  not used, flagged for the same reason.
- The v3 report status enum's non-`PENDING` values and the exact v3
  list-endpoint request-body shape remain genuinely unconfirmed against
  one full official response, exactly as flagged in the first pass.
  **This must be verified directly against Amazon's own sandbox once
  approval completes** (see the activation plan's step 7 below) before
  any of this is trusted for a live account.

**Third verification pass (controlled production read-validation task,
2026-09-13): `list_campaigns` verified against a real production
response for the selected Nonin Medical profile — two of the two
remaining unknowns turned out wrong:**

- `Content-Type`/`Accept: application/vnd.spcampaign.v3+json` is
  **required**, confirmed by a live 415 rejection when sent as generic
  `application/json` (Amazon's own error body named the required value
  explicitly). The second-pass corroboration above had the *path* and
  *media-type name* right, but the code was never actually sending it —
  `_request_json` had no way to override the hardcoded generic
  `application/json` header before this pass's fix.
- The response envelope key is **`"campaigns"`**, not the previously
  assumed `"items"`. This was a genuine implementation bug, not just an
  unconfirmed guess — a real response would have silently parsed to zero
  campaigns forever.
- Campaign budget arrives as a **nested `budget: {budget, budgetType}`
  object**, not a flat `dailyBudget` field. `AdsCampaignResponse` and
  `ads_client.py` were corrected accordingly (see the corrective PR
  referenced in the changelog below); `AdsCampaignBudget` is the new
  nested model.
- `list_ad_groups`/`list_product_ads`/`list_keywords`/
  `list_product_targets` remain **exactly as unconfirmed as before** —
  the campaigns fix does not generalize to them; each still sends the
  old generic `application/json` and will very likely also 415 in
  production. Do not wire any of them to a live path until each is
  independently verified the same way.
- Reporting v3 report **creation** was not exercised this pass (the
  controlled read-validation task stopped before Phase 4 once this
  contract mismatch was found) — its request/response shape remains
  exactly as unconfirmed as the second pass left it.

**Fourth verification pass (Reporting v3 diagnosis + fix, 2026-09-13):**
`POST /reporting/reports` was exercised live twice (once via the full
report-service orchestrator, once as an isolated diagnostic call) and
both times Amazon rejected the previously-implemented flat request body
outright with `{"code":"400","detail":"Required fields are invalid or
missing: configuration"}` — a genuine, evidenced implementation bug, not
an unconfirmed assumption:

- Amazon requires `adProduct`/`reportTypeId`/`timeUnit`/`format`/
  `groupBy`/`columns` nested under a top-level **`configuration`**
  object, with only `name`/`startDate`/`endDate` at the top level. This
  matches a community-documented Amazon Ads example found independently
  before the live call, and the live error (naming the missing field
  explicitly) made it conclusive rather than inferred.
- `AdsReportRequestConfiguration` was restructured accordingly (new
  `AdsReportConfigurationBody` nested model); `report_name_for_run()`
  generates a deterministic, non-secret, non-seller-identifying report
  name (`asi-sp-campaigns-<internal-run-id>-<start>-<end>`).
- No override to the generic `Content-Type: application/json` was
  needed for this endpoint — unlike campaign-list, Amazon's rejection
  here was a schema/body-structure error (`400`), not a media-type
  rejection (`415`).
- While fixing this, `HttpAmazonAdsApiClient.download_report` was also
  hardened to require HTTPS and never follow a redirect, mirroring
  `app.amazon.reports_client`'s identical, already-reviewed "honestly
  scoped" download-safety design for SP-API's own presigned report URLs
  (no fixed hostname allowlist is published by Amazon for either API, so
  scheme + no-redirect is the verifiable, conservative guarantee this
  codebase already committed to elsewhere).
- `list_ad_groups`/`list_product_ads`/`list_keywords`/
  `list_product_targets` remain unconfirmed, unchanged by this pass.

**Fifth verification pass (Reporting v3 row-contract diagnosis,
2026-09-13):** with the fourth pass's request-structure fix deployed,
one real report was created, completed, and downloaded end to end for
the first time — but **all 35 returned rows failed `AdsReportRow`
validation**, for a single, uniform reason:

- Amazon's Reporting v3 API returns entity ids (`campaignId` directly
  observed; `adGroupId`/`keywordId`/`targetId`/`adId` inferred by the
  same id-field convention within the same API family, not yet each
  independently confirmed) as **JSON integers**, not strings — the
  opposite of the v3 entity-list endpoints (e.g. `/sp/campaigns/list`),
  which return these same ids as strings (confirmed live in the third
  pass). `AdsReportRow` now normalizes a plain `int` id to its string
  form via a `field_validator(mode="before")`, explicitly excluding
  `bool` (an `int` subclass in Python) and any other non-int shape
  (e.g. a float), which still fail validation visibly rather than being
  silently coerced.
- A genuine second, independent finding from the same pass: the report
  lifecycle marked this run `succeeded` with `records_ingested=0` even
  though every one of 35 rows was rejected — a nonempty report with
  zero accepted rows is now treated as a deterministic row-contract
  failure (`report_row_contract_mismatch`), not a success, and not
  retried (retrying would reprocess identical bytes to an identical
  result). The sync checkpoint no longer advances in this case. A
  report with only *some* rows rejected is unaffected — that remains a
  success, ingesting the valid rows.
- The download host was observed live for the first time during this
  pass: `offline-report-storage-us-east-1-prod.s3.amazonaws.com`
  (recorded here only as a hostname — never the signed path/query).
  `download_report`'s existing HTTPS-only + no-redirect design (fourth
  pass) was not changed; this observation is additive context, not a
  new allowlist in shipped code.

## 10. Activation plan (do in this order; each step gates the next)

1. Amazon Ads API Partner access approval completes (already submitted,
   per the operator's own context — awaiting Amazon).
2. Register the Ads application under an **EWise-controlled** Amazon
   Developer account as a **Partner** application, using the exact URLs
   in §2.
3. Set `ADS_LWA_CLIENT_ID`, `ADS_LWA_CLIENT_SECRET`,
   `ADS_OAUTH_REDIRECT_URI` on the `api` Railway service (masked secrets,
   never printed — same pattern as every other credential in this repo).
   `ADS_API_BACKEND` is still unset/`disabled` at this point — the OAuth
   routes exist and will build a correct consent URL, but any attempt to
   actually exchange a code or discover profiles still refuses clearly.
4. Set `ADS_API_BACKEND=http` on the `api` Railway service. This is the
   moment the app becomes capable of making a live Ads API call — do it
   deliberately, immediately before the manual OAuth test in step 7, not
   earlier. (`mock` is never appropriate on a real Railway service; it
   exists for tests/local dev only.)
5. Add the Cloudflare Access bypass for the two paths in §8.
6. Apply migration `0019` to production Supabase (`alembic upgrade
   head`) during a low-traffic window — additive only, no data
   migration needed.
7. Manually complete one real OAuth authorization (as the operator, on
   AJ Duran's own Ads account) and confirm `GET
   /api/v1/amazon/ads-connection/status` shows `connected` with the
   expected profile(s). This is the first point any live Amazon Ads call
   is made — everything before it is inert by construction.
8. Verify §9's flagged assumptions against the real profiles/report
   responses now observable; fix `ads_models.py`/`ads_client.py` if any
   assumption was wrong.
9. Select the correct AJ Duran profile via `POST .../profiles/select`.
10. Run `AmazonAdsReportService.process_one_claimed_job()` manually
    (still not a deployed worker, still on the `api` service/a local
    shell — `ADS_API_BACKEND=http` there is what makes this a real call)
    against one real, small date range; confirm ingested facts look
    correct.
11. Only after step 10 is verified correct: deploy a dedicated Ads
    worker (see §11) and, on that worker service specifically, set
    **both** `ADS_API_BACKEND=http` and `ASI_ADS_WORKER_ENABLED=true`.
    Neither alone starts it for real — `main()` exits with a distinct
    code for each missing half (`EXIT_DISABLED` if the flag is unset,
    `EXIT_BACKEND_NOT_HTTP` if the backend isn't `http`, even with the
    flag set).

**Rollback at any point:** unsetting `ADS_API_BACKEND` (or setting it
back to `disabled`) on any service immediately makes every Ads call on
that service impossible again, independent of every other setting —
this is the fastest, single-variable kill switch. Unsetting
`ASI_ADS_WORKER_ENABLED` stops the worker specifically (exit code 3);
the OAuth routes on `api` fail closed the instant any of the three
required OAuth env vars is unset. No other service is affected in any
case, since every Ads table is a separate family with no foreign key
into existing SP-API or business tables.

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
- **Enable flags:** `ADS_API_BACKEND=http` **and** `ASI_ADS_WORKER_ENABLED=true`,
  both set on this worker service specifically, only after the full
  activation plan (§10) is complete and manually verified. Either alone
  leaves `main()` refusing to start (see §10's step 11).

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

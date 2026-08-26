# 12B.2 — Canonical Seller Identity + Marketplace Ingestion

**Architecture validation report**  
**Date:** 25 August 2026  
**Role:** Principal Architect / Senior Backend Engineer  
**Status:** Validation complete. Implementation is **not started**.  
**Depends on:** ADRs 0002–0006, Milestone 12B data-backbone architecture, 12B.1A–12B.1D, Production Connect Amazon checkpoint `a6b01bc`

This file lives under `docs/AI_HANDOVER/` because that is the repository’s current architecture-handover convention. There is **no** `docs/architecture/` directory.

**Legend:** **Verified** = observed in code/tests/git. **Recommendation** = proposed 12B.2 design. **Open** = product-owner decision required.

Do not treat this document as permission to ingest listings, orders, inventory, reports, finances, or Ads.

---

## 1. Executive summary

ASI has a **validated Production SP-API seller grant** for a US Professional seller (NA / amazon.com). OAuth callback and Sellers handshake remain separated. Handshake results are **not** persisted as canonical seller or marketplace rows.

12B.2 must turn `GET /sellers/v1/marketplaceParticipations` into organization-isolated identity:

```text
ASI organization
  → Amazon seller account (selling partner id, when Amazon returns it)
  → Amazon application authorization (amazon_connections + token_reference)
  → SP-API region
  → marketplace participation (Amazon marketplace id)
```

**Go / no-go:** **GO for 12B.2A schema design and 12B.2B normalization**, after the product-owner answers the two tenancy questions in §21. Do **not** begin listings (12B.3) or Ads (12C).

---

## 2. Verified repository baseline

| Item | Verified fact |
| --- | --- |
| Branch | `main` tracks `origin/main` |
| Amazon foundation tag | `amazon-seller-connection-foundation-v1` @ `2110bb7` (24 Aug 2026 handover) |
| Production Connect Amazon | `a6b01bc` (25 Aug 2026), pushed |
| Alembic head | `0008_amazon_oauth_states` (single head). Chain `0001` … `0008` |
| Amazon tables | `amazon_connections`, `amazon_oauth_states` only |
| Tenancy | `organization_id`. Not `selling_partner_id` |
| Secrets | Refresh token in SecretProvider; DB stores opaque `token_reference` |
| Handshake | `GET /sellers/v1/marketplaceParticipations` only |
| Ingest | None. `last_successful_sync_at` unused |
| Copilot / engines | Unchanged by the 25 Aug checkpoint |
| `docs/architecture/` | **Does not exist** |

**Documentation drift (do not silently rewrite contracts in this slice):**

- `docs/checkpoints/2026-08-25-production-connect-amazon.md` still says “uncommitted”. Git shows **committed and pushed** (`a6b01bc`).
- `CLAUDE.md` / `docs/AI_HANDOVER/00` still say live seller grant is unproven. **Live local Production Connect + Validate succeeded on 25 Aug 2026.**
- `docs/AI_HANDOVER/08_AMAZON_SP_API_ARCHITECTURE.md` still says local Connect Amazon uses a SANDBOX row. **Code now defaults Connect Amazon to PRODUCTION.**

Listing intelligence still supports **`amazon.in` only** (`supported_marketplaces`). Connection display marketplace `amazon.com` is **not** listing-catalog identity.

---

## 3. Production connection checkpoint status

### 3.1 Git and scope

Working tree at validation start: **clean**, in sync with `origin/main` at `a6b01bc`.

Checkpoint commit files are limited to Production Connect Amazon / US labels / file-backed development secrets / Connection UI / tests / docs. No Copilot, Ads, listing-engine, or migration files.

Follow-up **not in `a6b01bc`:** `apps/api/tests/conftest.py` now pins `DEFAULT_MARKETPLACE=amazon.in` so a local `.env` used for US Connect Amazon cannot fail listing/profit tests. **Uncommitted at report time.**

### 3.2 Secret scan

| Path | Tracked? | Finding |
| --- | --- | --- |
| `apps/api/.env` | No (`.gitignore` `.env`) | Live operator file. Do not commit. |
| `.data/` and `apps/api/.data/*.json` | No (`.gitignore` `.data/`) | **Type:** Amazon LWA **refresh token** in DevelopmentSecretProvider file store. **Area:** local JSON map keyed by ASI secret reference. **Remediation:** keep gitignored; mode `0600`; never copy into docs, fixtures, or chat. Value redacted. |
| Checkpoint commit diff | Tracked source only | No live `Atzr|` / `Atza|` material. Tests use `Atzr\|test-…` / `Atza\|test-…` placeholders and assert they never render. |
| Frontend / API models | Tracked | Public Amazon models `extra="forbid"`; overview excludes `token_reference`. |

**Security result:** no credential found in git history of `a6b01bc`. A **local untracked** development secret file exists and must stay untracked.

### 3.3 Validation commands

| Check | Result |
| --- | --- |
| `cd apps/api && uv run pytest` (after conftest pin) | **624 passed** |
| Same suite before pin (local `.env` `DEFAULT_MARKETPLACE=amazon.com`) | 23 failed, 601 passed — all `Unsupported marketplace: amazon.com` in listing/profit/Copilot product paths. **Caused by local Connect Amazon `.env`, not by Amazon handshake code.** Classified as checkpoint test-isolation gap. Fixed in conftest only. |
| `cd apps/web && npm test` | **35 passed** (3 files) |
| `uv run alembic heads` | `0008_amazon_oauth_states (head)` |
| Repo lint/typecheck | Backend has no ruff/mypy in `pyproject.toml`. Frontend `npm run lint` exists; not required as a gate in `docs/AI_HANDOVER/13`. Not run as a new gate. |

Amazon-focused tests covering callback → `pending_validation`, handshake → `connected`, SecretProvider fail-closed, org isolation, and Connection UI **passed** inside the 624.

### 3.4 Behaviour confirmed in tests/code

```text
OAuth callback → pending_validation (no SP-API)
POST /connection/test with seller token_reference
  → GET /sellers/v1/marketplaceParticipations
  → connected on participating marketplace(s)
```

Also confirmed:

- Callback does not ingest seller business data.
- Sandbox LWA / sandbox refresh token remain Test Connection; Production LWA / application id remain Connect Amazon.
- GET `/connection` prefers PRODUCTION; leftover SANDBOX rows are not the seller card.
- Development secrets persist across process restart when `AMAZON_DEVELOPMENT_SECRET_STORE` is a file path; empty store is in-memory only (tests set empty).
- `AMAZON_SECRET_BACKEND=production` raises fail-closed `SecretAccessError`.
- Tokens are not columns on business tables; `bind_token_reference` rejects token-shaped values.
- Public JSON dump rejects secret field names.
- Sellers client is GET-only. No Amazon write client.
- No Ads API module. No Copilot/ToolRegistry/EvidenceEnvelope contract change in `a6b01bc`.

### 3.5 Checkpoint decision

```text
ALREADY COMMITTED AND PUSHED  (a6b01bc)
FOLLOW-UP NOT READY TO COMMIT until the operator authorizes the conftest isolation patch
```

| | |
| --- | --- |
| Included in `a6b01bc` | Connection Production/NA/amazon.com defaults, file-backed DevelopmentSecretProvider, Validate connection UI, tests, session note |
| Follow-up (uncommitted) | `DEFAULT_MARKETPLACE` pin in `tests/conftest.py` |
| Tag | Optional documentation tag (e.g. `production-connect-amazon-us-v1`) **not created**. Not required for 12B.2. |
| Proposed follow-up message | `test(amazon): isolate listing marketplace from local Connect Amazon env` |

Do not commit, push, or tag from this validation pass unless the user authorizes it.

---

## 4. Current data and service boundaries

```text
Next.js Connection UI
  → FastAPI amazon_connection routes
    → AmazonConnectionService
      → authorize / callback / overview / test
      → AmazonSellerValidationService
        → AmazonConnectionSecretResolver
        → SecretProvider
        → AmazonSpApiSellersClient (GET participations)
      → AmazonConnectionRepository / AmazonOAuthStateRepository
      → amazon_connections + amazon_oauth_states
```

Handshake **return** includes `selling_partner_id` (optional) and `marketplaces[{marketplace_id, country_code}]`. Persistence writes **status**, optional **selling_partner_id**, timestamps, error codes. **Marketplace list is discarded.** `last_successful_sync_at` stays null.

`BulkJob` / `bulk_jobs` is ASIN due-diligence, not an SP-API ingest-run abstraction. Do not reuse it for 12B.2.

---

## 5. Canonical seller-identity model

### 5.1 What Amazon returns (verified)

| Signal | Source | Stability | Notes |
| --- | --- | --- | --- |
| `sellingPartnerId` | Sellers `getMarketplaceParticipations` JSON (alias on DTO) | Selling-partner account id; **not** marketplace-specific | **May be omitted.** Fixture `get_marketplace_participations.sandbox.json` has **no** `sellingPartnerId`. Validation still succeeds on participation. |
| `selling_partner_id` query | Website authorization callback | Same Amazon selling partner id when Amazon includes it | **Verified: currently discarded** (`del selling_partner_id` in `complete_authorization_callback`) so it cannot be used as tenant **or** as identity hint. |
| Marketplace `id` | `payload[].marketplace.id` | Canonical **marketplace** id (e.g. `ATVPDKIKX0DER`) | Not seller identity. |
| `storeName` | participation row | Store display name | Not a unique seller key. |

**Do not invent** ASI seller keys when Amazon returns neither callback SPID nor `sellingPartnerId`.

### 5.2 Recommendation

Canonical seller account key:

```text
organization_id + selling_partner_id
```

`selling_partner_id` is Amazon’s selling partner identifier. It is **global to the selling partner**, not per marketplace. Reauthorization of the **same** partner should match this key and **not** create a second account.

When `sellingPartnerId` is missing:

1. Prefer a previously stored `amazon_connections.selling_partner_id` if set.
2. **Recommendation:** persist callback `selling_partner_id` onto the connection as an identity hint at `pending_validation` (still not a tenant key).
3. If still unknown: create **no** `amazon_seller_accounts` row; keep connection `connected` from participation; surface `seller_identity_incomplete`. Do not synthesize a fake id.

Reconnect: upsert account on `(organization_id, selling_partner_id)`; rotate authorization (`token_reference`) on the connection row.

### 5.3 Open product questions

See §21. Do not encode a global uniqueness rule until approved.

---

## 6. Authorization and secret boundary

Keep **four** different things:

| Concept | Current / proposed owner |
| --- | --- |
| ASI tenant | `organizations.id` |
| Amazon seller account | New `amazon_seller_accounts` |
| Application grant | Existing `amazon_connections` + SecretProvider `token_reference` |
| OAuth transaction | Existing `amazon_oauth_states` (hash only) |
| Marketplace participation | New `amazon_marketplace_participations` |

A refresh token is not identity. A `token_reference` is not identity. An OAuth-state row is not identity. A participation row is not a grant.

**V1 recommendation:** do **not** add `amazon_authorizations` as a second grant table. `amazon_connections` already is the grant. Split later if one seller needs two production grants.

ADR 0006 remains in force.

---

## 7. Marketplace-participation model

Amazon **marketplace id** is the canonical external identifier. Display domains (`amazon.com`, `www.amazon.com`) are attributes, never primary keys.

Persist from Sellers DTO (already parsed):

| Field | Source |
| --- | --- |
| `marketplace_id` | `marketplace.id` |
| `name` | `marketplace.name` |
| `country_code` | `marketplace.countryCode` |
| `default_currency_code` | `marketplace.defaultCurrencyCode` |
| `default_language_code` | `marketplace.defaultLanguageCode` |
| `domain_name` | `marketplace.domainName` |
| `is_participating` | `participation.isParticipating` |
| `has_suspended_listings` | `participation.hasSuspendedListings` |
| `store_name` | `storeName` |
| `region` | connection `region` (NA/EU/FE) at observation time |

Add ASI timestamps: `first_seen_at`, `last_seen_at`, `last_successful_sync_at`, `ingested_at`, `is_active`.

Handshake today only keeps `(marketplace_id, country_code)` for **participating** rows and drops non-participating ones. 12B.2 should persist **all returned** participations and mark `is_participating` / `is_active` rather than dropping inactive rows on sight.

---

## 8. Multi-account and multi-marketplace behavior

```text
one ASI organization
  → one or more Amazon seller accounts     (schema capability)
  → one production SP-API grant            (V1 product constraint)
  → one or more marketplace participations (required)
```

| Layer | Rule |
| --- | --- |
| **Verified V1 constraint** | `amazon_connections` unique `(organization_id, provider, environment)` → one PRODUCTION grant per org |
| **Long-term schema** | `amazon_seller_accounts` unique `(organization_id, selling_partner_id)` allows multiple accounts without rewriting listings later |
| **V1 product** | UI/API continues to expose one Production Connect Amazon grant. Do not build multi-account Connect UI in 12B.2 |

If a second selling partner must be connected later, uniqueness on connections must add `selling_partner_id` (already noted in the 12B backbone doc). Design 12B.2 tables so that change is additive.

Same Amazon seller in **two ASI orgs:** **Open** (§21). Recommendation if forbidden: unique `selling_partner_id` globally on `amazon_seller_accounts`, enforced in the identity service, not in Copilot.

---

## 9. Idempotency and reconciliation rules

| Event | Behaviour |
| --- | --- |
| Repeat participation payload | Upsert participation on `(seller_account_id, marketplace_id)`; bump `last_seen_at` / `last_successful_sync_at`; no duplicate rows |
| New marketplace | Insert participation; `first_seen_at = now` |
| Marketplace absent vs last run | Set `is_active=false`, keep last-known-good attributes; **do not delete** |
| Partial payload | Ingestion run `partial`; do not deactivate marketplaces not listed if run classified incomplete |
| Ambiguous timeout | New ingestion run; upsert is safe; do not assume Amazon applied a write (this API is read-only) |
| Reconnect, new refresh token | Same seller account if SPID matches; `amazon_connections.token_reference` replaced; prior secret deleted after bind succeeds |
| Response order | Upserts keyed by marketplace id; order independent |
| Ingestion-run identity | New `amazon_ingestion_runs.id` per attempt; never reuse a completed run id |

---

## 10. Ingestion-run architecture

**Recommendation:** new `amazon_ingestion_runs`. Do not extend `bulk_jobs`.

Suggested fields (align names with existing snake_case):

- `id`, `organization_id`
- `connection_id`, `seller_account_id` (nullable if identity incomplete)
- `domain` (`sellers_marketplace_participations` for 12B.2)
- `region`, `environment`
- `status` (`started`, `succeeded`, `partial`, `failed`, `timed_out`)
- `started_at`, `completed_at`
- `request_correlation_id` (ASI-generated UUID; not an Amazon secret)
- `records_received`, `records_accepted`, `records_rejected`
- `retry_count`
- `failure_class` (`rate_limited`, `auth`, `unavailable`, `parse`, `secret_access`, `identity_incomplete`, …)
- `pagination_complete` (Sellers v1 participations are typically a single page; keep the column for 12B.3+)

Preserve last-known-good participations on `failed` / `timed_out`.

---

## 11. Provenance and freshness

Every canonical seller/marketplace row should answer ADR 0004:

| Question | Field |
| --- | --- |
| Who owns it? | `organization_id` |
| Which Amazon seller? | `seller_account_id` / `selling_partner_id` |
| Which grant was used? | `connection_id` |
| Which source? | `source = sp_api` |
| Which operation? | `source_operation = getMarketplaceParticipations` |
| Region | `region` |
| Observed / ingested | `source_observed_at`, `ingested_at` |
| First / last seen | `first_seen_at`, `last_seen_at` |
| Last good sync | `last_successful_sync_at` |
| Which run? | `ingestion_run_id` |
| Complete vs partial | ingestion run `status` |
| Superseded? | `is_active` + newer `ingested_at` on the same unique key |

Connection `last_successful_sync_at` should be set when a **successful or partial-accepted** marketplace ingest completes — distinct from `last_successful_validation_at` (handshake only).

---

## 12. Connection lifecycle

Existing statuses (verified check constraint):  
`not_connected | pending_authorization | pending_validation | connected | degraded | revoked | error`

| Event | Connection | Canonical identity |
| --- | --- | --- |
| Validation success | `connected`; set `selling_partner_id` if Amazon returned it | Upsert account + participations (12B.2D) |
| Throttle / 5xx / timeout | `degraded`; keep secret | No delete; run `failed`/`partial` |
| Missing local secret | `pending_validation` + `secret_access_failed` | Unchanged identity |
| Invalid/revoked refresh | `error`; reason `requires_reauth`; delete secret; clear `token_reference` | Keep account/participations as last-known-good; mark grant unusable |
| Marketplace access removed | Stay `connected` if any participation remains; otherwise `degraded` or `connected` with zero active participations (**Open** product copy) | Inactive flag on dropped marketplace |
| Reauth | New callback → `pending_validation` → validate → `connected` | Same account if SPID matches |

Failed sync **must not** delete last-known-good identity.

---

## 13. Rate limiting, retries and errors

**Verified today:** Sellers client maps 429 → `SpApiRateLimitedError`, 401/403 → auth, 5xx → request failed, timeout → request failed. No numeric quota constants.

**Recommendation for 12B.2C:** a small `SpApiRequestPolicy` (endpoint-aware, config-driven) used by Sellers now and Listings later:

- Retry only idempotent GETs
- Exponential backoff with jitter
- Retry ceiling
- Treat 429 as retryable until ceiling, then `degraded`
- Auth failures are not retried as throttles
- Do **not** hardcode Amazon’s published rate tables in architecture; keep replaceable config per operation

---

## 14. Security and organization isolation

**Verified:** repositories load connections by `organization_id`; secret references encode org + connection; resolver rejects org mismatch; overview omits `token_reference`.

**Required 12B.2 tests (threat cases):**

| Threat | Control |
| --- | --- |
| Org A reads Org B seller account | Every query `organization_id = current org`; 404 on miss |
| Org A uses Org B `token_reference` | Resolver + reference path must match org and connection id |
| SPID used as tenant | Forbidden; tests assert tenant is org UUID |
| Tokens in `amazon_seller_accounts` / participations | No token columns; schema tests |
| Tokens in GET connection / new admin APIs | `public_model_dump` / extra-forbid |
| Tokens in logs | Log ids and status only |
| Raw payload as Copilot evidence | Forbidden; tools wrap Python services only |
| Production SecretProvider missing | Fail closed (already) |

---

## 15. Raw-payload policy

**Recommendation for 12B.2:** **do not store full Amazon JSON** on identity tables.

- Persist canonical fields listed in §7.
- Store `payload_sha256` (hex) on the ingestion run for replay debugging.
- Optional isolated `amazon_ingestion_blobs` with short TTL is **out of 12B.2** unless debugging a live contract mismatch requires it (product-owner).
- Never attach raw SP-API JSON to EvidenceEnvelope, Copilot messages, or frontend.

Sellers participation payloads are small and generally non-credentialed; the risk is **schema coupling and accidental PII** (`storeName`), not refresh tokens.

---

## 16. API and service boundaries

| Responsibility | Owner |
| --- | --- |
| SP-API HTTP | `AmazonSpApiSellersClient` (existing) |
| Handshake / connection status | `AmazonConnectionService` + `AmazonSellerValidationService` (existing) |
| Identity upsert | New `AmazonSellerIdentityService` (12B.2B) |
| Participation normalization | Same identity service; DTO → canonical |
| Ingestion runs | New `AmazonIngestionRunService` (12B.2C) |
| Persistence | New repositories; **no** frontend writes |
| Org authorization | Service layer using `current_organization_id()` before any SP-API or identity write |
| Retries | Request policy used by the Sellers client |

**Not owners:** Next.js, Copilot, Skills, `profit-calc-v1`, listing scores, ToolRegistry handlers (they may later **read** projections only).

Minimum new read APIs (12B.2D), extra-forbid, no secrets:

- Extend GET `/connection` with safe identity: selling partner id, marketplaces summary, last sync time, ingest run status.
- Optional `POST /connection/marketplaces/sync` if validation stays handshake-only.

---

## 17. UI scope

**May show:** connected/degraded; selling partner id; marketplace id + country + domain label; participating/suspended; last successful marketplace sync; freshness; `secret_access_failed` / `requires_reauth` actions already present.

**Must not show:** tokens, codes, `token_reference`, raw Amazon JSON, stack traces.

**Sync trigger — recommendation:** run marketplace ingest **synchronously immediately after successful validation** inside the existing `POST /connection/test` path (payload is one GET, typically one page), **and** keep an explicit **Sync marketplaces** control for later refresh without re-OAuth.

Reasoning: operators already click Validate connection; they expect marketplaces when status becomes Connected. A second mandatory click would recreate today’s Pending-validation confusion. Scheduled sync is **not** 12B.2.

---

## 18. Proposed data model

Illustrative names. No migration in this phase.

### Remain

| Table | Role in 12B.2 |
| --- | --- |
| `organizations` | Tenant |
| `amazon_connections` | Grant + lifecycle + `token_reference` |
| `amazon_oauth_states` | Ephemeral OAuth CSRF |

Do **not** duplicate grant columns onto identity tables.

### `amazon_seller_accounts` (new)

- **Purpose:** Canonical Amazon selling partner for an ASI org  
- **Owner:** identity service  
- **Fields:** `id`, `organization_id`, `selling_partner_id`, `display_store_name` (last observed), `first_seen_at`, `last_seen_at`, `last_successful_sync_at`, `status` (`active`, `identity_incomplete`, `disconnected`)  
- **FK:** `organization_id` → `organizations`  
- **Unique:** `(organization_id, selling_partner_id)` where SPID is not null  
- **Indexes:** `(organization_id)`, `(selling_partner_id)`  
- **Lifecycle:** insert on first known SPID; never delete on sync failure  
- **Mutable:** display name, timestamps, status  
- **Immutable:** `id`, `organization_id`, `selling_partner_id`  
- **Sensitivity:** public Amazon account id; not a secret  

### `amazon_marketplace_participations` (new)

- **Purpose:** Seller’s Amazon marketplace membership  
- **Owner:** identity service  
- **Fields:** marketplace attributes in §7, `is_active`, provenance timestamps, `connection_id`, `ingestion_run_id`  
- **FK:** `organization_id`, `seller_account_id`, `connection_id`  
- **Unique:** `(seller_account_id, marketplace_id)`  
- **Indexes:** `(organization_id, marketplace_id)`, `(seller_account_id, is_active)`  
- **Deletion:** soft inactive; no hard delete in 12B.2  
- **Sensitivity:** store name may be business identifying; still not a token  

### `amazon_ingestion_runs` (new)

- **Purpose:** Reusable ingest attempt record  
- **Owner:** ingestion-run service  
- **Unique:** `id` only; correlation id unique per org optional  
- **Retention:** keep; no secret payload  

`amazon_connections.selling_partner_id` remains a **denormalized hint** for the Connection UI until identity rows exist; 12B.2B should keep it in sync with the account row, not as a second source of truth.

---

## 19. Migration impact

- Next migration: **`0009`** (name TBD at implementation). Do **not** edit `0007` / `0008`.
- SQLite pytest `create_all` must stay aligned with SQLAlchemy models.
- No backfill of historical Amazon JSON (none stored).
- Existing `connected` rows can be ingested on next Validate / Sync.

---

## 20. Required tests

### Identity

- First connected handshake with SPID creates one account + N participations  
- Repeat ingest is idempotent  
- Reconnect same SPID does not duplicate account  
- Multi-marketplace payload persists all ids  
- Marketplace PK is Amazon id, not domain  
- Org B cannot read Org A accounts  

### Lifecycle

- Validation success / 429 / 5xx / invalid token / missing secret / empty participation / marketplace removed / reauth  

### Security

- No token columns on new tables  
- No `token_reference` on new public JSON  
- Logs without `Atzr|` / `Atza|`  
- Cross-org 404  

### Ingestion

- Retry after 429  
- Timeout replay upsert  
- Partial run does not deactivate unseen marketplaces  
- Run counts: received / accepted / rejected  

### Regression

- Production Connect Amazon flow  
- Sandbox env-token Test Connection does not persist seller `connected`  
- Copilot, listing, profit, advertising suites  
- Existing org isolation  

Do not add live Amazon calls to CI.

---

## 21. Risks and unresolved questions

| ID | Type | Question | Recommendation if unanswered |
| --- | --- | --- | --- |
| Q1 | **Open** | May one Amazon `selling_partner_id` belong to two ASI organizations? | Delay global unique index; unique per org only |
| Q2 | **Open** | May one ASI org connect two Amazon seller accounts in V1? | Schema allows; UI/API stays one PRODUCTION grant |
| Q3 | **Open** | Persist callback `selling_partner_id` when Sellers omits it? | Yes — identity hint only |
| Q4 | **Open** | If all marketplaces become non-participating, is the connection still `connected`? | Stay `connected` with zero active participations + UI warning |
| Q5 | **Open** | Store raw participation JSON at all? | No for 12B.2; checksum only |
| R1 | Risk | SPID omitted forever for some grants | Incomplete identity; block 12B.3 listings until SPID or explicit exception |
| R2 | Risk | Unique `(org, provider, environment)` blocks two production sellers | Documented; change uniqueness later |
| R3 | Risk | Local secret file on disk | Gitignored; not a production vault |
| R4 | Risk | Process access logs may still contain callback query strings | Existing known limitation |

---

## 22. Explicit non-goals

12B.2 does **not** implement: listings, catalog, orders, inventory, reports, financial events, Ads API, campaign management, Amazon writes, Copilot Amazon business tools, Skill Registry, LangGraph/CrewAI, deterministic formula changes, ToolRegistry/EvidenceEnvelope contract changes.

Mention of 12B.3 is only to keep listing identity as `organization_id + seller_account_id + marketplace_id + seller_sku` (ADR 0005).

---

## 23. Implementation slices

Adjusted from the prompt to match this repository (no separate authorizations table in the first slice; ingest attached to existing Validate path).

```text
12B.2A  Schema: amazon_seller_accounts, amazon_marketplace_participations,
        amazon_ingestion_runs + Alembic 0009. No SP-API behaviour change.

12B.2B  Normalize Sellers DTO → upsert identity/participations (idempotent).
        Optionally persist callback selling_partner_id on the connection.

12B.2C  Ingestion-run records, retry/backoff policy for the existing GET,
        provenance timestamps, last_successful_sync_at.

12B.2D  After successful validation, run 12B.2B in-process. Safe GET overview
        fields. Keep handshake-only callback.

12B.2E  Connection UI: marketplaces, freshness, incomplete-identity warning.
        No secrets.

12B.2F  Security, org isolation, idempotency, listing/profit/Copilot regression.
```

Do not execute these slices in this validation pass.

---

## 24. Architecture recommendation

1. Treat `amazon_connections` as the **grant**, not the seller account.  
2. Introduce **canonical seller account + participation** tables keyed by Amazon ids.  
3. Use **upsert + last-known-good**; never delete identity because Amazon flickered.  
4. Reuse Sellers GET; do not add new Amazon APIs in 12B.2.  
5. Drive first ingest from **Validate connection**, not from Copilot.  
6. Keep Rainforest listing default `amazon.in` isolated from connection marketplace `amazon.com`.  
7. Fail closed on secrets; fail open on missing `sellingPartnerId` only as **incomplete identity**, not as a fabricated key.

This is consistent with ADRs 0002–0006 and the 12B backbone document, updated for the **verified** 12B.1D + Production Connect Amazon code.

---

## 25. Go / no-go for implementation

| Decision | Result |
| --- | --- |
| Production Connect Amazon checkpoint | **Already on GitHub (`a6b01bc`)** |
| 12B.2 implementation | **GO** for 12B.2A after Q1/Q2 answers (or after explicitly accepting the defaults in §21) |
| 12B.3 listings | **NO-GO** until identity rows exist and SPID policy is clear |
| Ads / Copilot Amazon tools / Skills | **NO-GO** |

**Implementation is not safe to begin in this chat** without explicit user authorization to start **12B.2A only**. This report is the required architecture-validation gate from `docs/AI_HANDOVER/17_CLAUDE_START_HERE.md`.

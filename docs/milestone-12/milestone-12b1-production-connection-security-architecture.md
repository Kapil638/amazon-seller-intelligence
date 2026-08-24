# Milestone 12B.1 — Production Connection Metadata + Secure Token Architecture

**Date:** 23 August 2026  
**Role:** Principal Architect  
**Status:** Architecture approved. Connection/secret/OAuth/validation slices **12B.1A–12B.1D are implemented**. This file remains the architecture record. Do not start 12B.2 from this document without a dedicated slice.  
**Depends on:** 12A.0, 12A.1, 12B, ADR 0002–0005  
**Companion:** [Implementation plan](milestone-12b1-implementation-plan.md), [ADR 0006](../adr/0006-amazon-connection-credential-boundary.md)

Current Amazon authorization source of truth used for this design: [Authorize Public Applications](https://developer-docs.amazon.com/sp-api/docs/authorize-public-applications), [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow), [Selling Partner Appstore Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/selling-partner-appstore-authorization-workflow), [Roles in the Selling Partner API](https://developer-docs.amazon.com/sp-api/docs/roles-in-the-selling-partner-api) (retrieved 23 August 2026). Implementers must re-read those pages at coding time; do not invent callback parameters.

---

## 1. Executive Summary

12A proved LWA + sandbox Sellers connectivity with **process `.env` secrets** and **no connection table**. That is acceptable for a developer sandbox. It is not a SaaS authorization model.

12B.1 designs the production boundary:

```text
Organization
  → AmazonConnection (Postgres metadata)
  → token_reference (opaque)
  → SecretProvider
  → seller refresh token
  → LWA (app client_id + client_secret + refresh_token)
  → short-lived access token (memory only)
  → SP-API
```

This milestone does **not** ingest orders, listings, inventory, reports, or finances. After consent, the only Amazon business call in scope is a **minimal Sellers validation**, then handoff to 12B.2.

**V1 uniqueness:** one SP-API connection per organization per environment (`sandbox` vs `production`). Multi-account uniqueness (`selling_partner_id`) is a later migration.

**Primary production consent path:** Amazon’s **website authorization workflow** (Connect Amazon on `/connection`), not a generated Python SDK and not a pasted refresh token.

---

## 2. Baseline

### What exists and can be reused

| Asset | Reuse |
| --- | --- |
| `LwaClient` (`grant_type=refresh_token`) | Runtime token exchange after a seller refresh token exists. Extend later with `authorization_code` grant; do not replace. |
| `AmazonSpApiSandboxClient` + Sellers DTOs | Pattern for a production Sellers client: `x-amz-access-token`, no RDT, no SigV4 for this call. |
| `AmazonConnectionService` + `GET/POST /api/v1/amazon/connection` | Keep sandbox test path. Later overlay persisted status; do not break 12A. |
| `public_model_dump` / `contains_secret_key` | Continue on all connection APIs. **Do not put `token_reference` on public models** (`token` is already a forbidden key fragment). |
| `SecretStr` on LWA DTOs | Keep. Access tokens never JSON-dump as plaintext. |
| Exception types (`SpApiAuthenticationError`, …) | Reuse. |
| Logging style (reason codes, no secrets) | Reuse. |
| `organizations.id` + `current_organization_id()` | Tenancy until real user auth exists. |
| Alembic numbered revisions (`0006_advertising_models` latest) | Next migration `0007_amazon_connections` **only when 12B.1A is started**. |
| Repository pattern (`organization_id` on every query) | Copy for connections. |
| `/connection` UI + `AmazonConnectionError` | Evolve; do not replace with a dashboard. |
| `.env` sandbox vars | Keep for 12A.0 / local sandbox. |

### What must not be reused as the production secret model

- `SP_API_SANDBOX_REFRESH_TOKEN` as the SaaS seller token
- Putting refresh tokens on `amazon_connections`
- Treating 12A.1 `CONNECTED` / `FAILED` **test results** as persisted connection status

### Current tenancy honesty

ASI still uses `Settings.default_organization_id`. 12B.1 must still **scope every connection by `organization_id`**. When real seller login exists, bind OAuth state to that session. Until then, `current_organization_id()` is the org context. Do not take org from Amazon query params.

---

## 3. Goals

1. Persist **connection metadata** without persisting **secret material**.
2. Separate **ASI app LWA credentials** from **seller refresh tokens**.
3. Design a **SecretProvider** that production can implement without rewriting connection rows when rotating the app client secret.
4. Design **website OAuth** with one-time state, org binding, and a failure saga that does not leave half-connected secrets.
5. Keep sandbox `.env` working.
6. Stay compatible with 12B canonical entities (`AmazonSellerAccount` later), Ads API as a **separate** connection row, Copilot/Skills never seeing tokens.

---

## 4. Non-Goals

- Operational ingest (orders, listings, inventory, reports, finances)
- Creating those business tables
- Ads API scopes or Ads OAuth
- Copilot, Skills, ToolRegistry, EvidenceEnvelope changes
- Amazon writes, Feeds, listing writes, bid writes
- RDT / buyer PII
- LangGraph / CrewAI / agents
- Broad audit-log product
- Scheduler / background health jobs
- Real user-account system (document the seam only)
- Implementing migrations or OAuth in this documentation step

---

## 5. Connection Metadata Model

`AmazonConnection` is **authorization**, not Amazon business data.

| Field | Type | Notes |
| --- | --- | --- |
| `id` | UUID PK | ASI id |
| `organization_id` | UUID FK → `organizations.id` | Required |
| `provider` | `SP_API` \| `ADS_API` | Ads rows later; no Ads implementation now |
| `environment` | `SANDBOX` \| `PRODUCTION` | Separate records |
| `region` | `eu` \| `na` \| `fe` | SP-API region. India = `eu` |
| `status` | enum §6 | Not a boolean |
| `selling_partner_id` | string, nullable | Filled after validation / Amazon redirect |
| `application_id` | string, nullable | Amazon app id used for consent URI (app-level copy, not secret) |
| `token_reference` | string, nullable | Opaque; **backend only** |
| `authorized_at` | timestamptz, nullable | First successful token persist + validation |
| `last_successful_validation_at` | timestamptz, nullable | Last Sellers handshake / Test Connection |
| `last_successful_sync_at` | timestamptz, nullable | Reserved for 12B.2+; null in 12B.1 |
| `last_error_at` | timestamptz, nullable | |
| `last_error_code` | string, nullable | `authentication`, `throttled`, `secret_missing`, … |
| `created_at` / `updated_at` | timestamptz | |

No `refresh_token`, `access_token`, `client_secret`, authorization headers, or Amazon payloads.

### Uniqueness (V1)

```text
UNIQUE (organization_id, provider, environment)
```

**Recommendation:** one production SP-API connection per org is enough for V1.

**Multi-account migration path:** add nullable `selling_partner_id`, backfill, then replace uniqueness with `(organization_id, provider, environment, selling_partner_id)` where `selling_partner_id` is NOT NULL. Do not invent a dummy SPID for uniqueness in V1.

Reconnect of the **same** org+provider+environment **updates the existing row** (and replaces the secret at the same or swapped `token_reference`). Do not insert a second live connection.

---

## 6. Status State Machine

Do **not** use `is_connected`. Connection status is **not** listing/order freshness.

| Status | Meaning |
| --- | --- |
| `not_connected` | No usable authorization (no row, or row with no secret) |
| `pending_authorization` | OAuth state issued; seller has not completed consent |
| `pending_validation` | Refresh token stored; Sellers handshake not yet succeeded |
| `connected` | Secret present; last validation succeeded |
| `degraded` | Secret present; last test/validation failed transiently |
| `revoked` | Seller or ASI disconnected; secret deleted |
| `error` | Terminal/unexpected failure needing operator/seller action (includes Amazon permanent invalid_grant / reauth required) |

12A.1 test payload statuses (`CONNECTED` / `FAILED` / `NOT_CONNECTED`) remain **test-result** values on `POST /connection/test`. They must not overwrite the state machine except: success may move `degraded` → `connected`; permanent auth failure may move `connected`/`degraded` → `error`.

### Transitions

```text
not_connected
  → pending_authorization     (Connect Amazon / state created)
pending_authorization
  → pending_validation        (callback + secret stored)
  → not_connected             (state expired, seller cancel)
  → error                     (callback invalid after retries exhausted)
pending_validation
  → connected                 (Sellers API OK)
  → degraded                  (Sellers API transient fail; secret kept)
  → error                     (permanent auth fail; secret deleted)
  → revoked                   (disconnect during handshake)
connected
  → degraded                  (test/validation transient fail)
  → error                     (refresh token permanently invalid)
  → pending_authorization     (Reconnect; existing secret kept until new secret committed)
  → revoked                   (Disconnect)
degraded
  → connected                 (test succeeds)
  → error                     (permanent invalid_grant)
  → revoked
  → pending_authorization     (Reconnect)
error
  → pending_authorization     (Reconnect)
  → revoked
revoked
  → pending_authorization     (Connect again; reuse row)
```

`last_successful_sync_at` stays null until 12B.2+. UI must not say “data is current” because status is `connected`.

---

## 7. Secret Boundary

```text
Postgres amazon_connections.token_reference
        ↓  (server loads connection BY organization_id, never by client-supplied reference)
SecretProvider.get_secret(reference)
        ↓
refresh token (SecretStr, in-process)
        ↓
LwaClient.fetch_access_token()
        ↓
access token (SecretStr, memory / optional tiny TTL cache)
```

| Lives in Postgres | Lives in SecretProvider | Lives in memory only | Never |
| --- | --- | --- | --- |
| metadata, status, SPID | seller refresh token | access token | frontend, Copilot, EvidenceEnvelope, Skills, logs |

App-level `SP_API_LWA_CLIENT_SECRET` stays in **process environment / platform secrets**, not in `amazon_connections`. It is not per-seller.

---

## 8. SecretProvider Design

Narrow interface (conceptual):

```text
put_secret(reference, value: SecretStr) -> None
get_secret(reference) -> SecretStr
delete_secret(reference) -> None
```

`rotate_secret` is `put_secret` at the same reference, or put-new + swap `token_reference` + delete-old. Do not build an enterprise secret platform.

`token_reference` format (ASI-generated, not a public URL):

```text
asi/amazon/{provider}/{environment}/{organization_id}/{connection_id}
```

Vendor-generated ids (AWS ARN, secret UUID) are also allowed if the DB stores only that opaque string.

### Implementations

| Name | Use |
| --- | --- |
| `DevelopmentSecretProvider` | Local/dev/test. Maps sandbox `.env` refresh token for the default org **or** stores encrypted values in a non-production table/file. Never logs values. |
| `EncryptedDatabaseSecretProvider` | **Fallback production.** Ciphertext + `key_id` in a table **`amazon_secret_ciphertexts`** isolated from business tables. Master key `ASI_TOKEN_ENCRYPTION_KEY` (or KMS-wrapped) in platform env. AES-GCM. Not ordinary columns on `amazon_connections`. |
| `AwsSecretsManagerProvider` | **Preferred production** when the host can use IAM. One secret object per `token_reference`. |

**Preferred production:** AWS Secrets Manager (or the equivalent of the eventual cloud: GCP Secret Manager / Azure Key Vault). Refresh tokens must not share undifferentiated Postgres backups with listings and profit snapshots.

**Fallback:** encrypted application-managed ciphertext table + env/KMS master key. Acceptable to ship 12B.1B before a cloud SM is provisioned.

**Not preferred as the only control:** Supabase Vault accessed via the same service role that already reads all business tables — it does not add a real trust boundary. Supabase remains the Postgres/storage host; secrets should still be encrypted or in a dedicated SM.

`get_secret` must **not** accept a reference from HTTP. Only the connection service, after loading the org-scoped row, may resolve `row.token_reference`.

---

## 9. App Credentials vs Seller Tokens

| | App-level (one ASI EWise app) | Seller-level (many orgs) |
| --- | --- | --- |
| Identifiers | LWA `client_id`, Amazon `application_id` | `organization_id`, `connection_id`, `selling_partner_id` |
| Secrets | LWA `client_secret` | Refresh token |
| Storage | Platform env / app secret | SecretProvider via `token_reference` |
| Rotation | Rotate env; all sellers keep working | `put_secret` / reconnect |

LWA runtime:

```text
POST https://api.amazon.com/auth/o2/token
grant_type=refresh_token
refresh_token=<seller>
client_id=<app>
client_secret=<app>
```

This is already how `LwaClient` works. **Do not duplicate client_secret per seller.** Amazon’s public-app model is one LWA app authorizing many selling partners.

Consent uses a different grant once:

```text
grant_type=authorization_code
code=<spapi_oauth_code>
redirect_uri=<registered>
client_id / client_secret=<app>
```

---

## 10. Access Token Lifecycle

- Typical TTL ~3600s (`expires_in`).
- Do not persist access tokens as business data.
- Do not send to Next.js, Copilot messages, or EvidenceEnvelope.
- In-process use: fetch, call SP-API, drop.
- Optional later: memory cache keyed by `connection_id` with TTL `expires_in - skew`. **V1 recommendation: no cache.** Simpler, fewer leak surfaces. Add only if rate/latency requires it after 12B.2.

---

## 11. Seller Authorization Flow

**V1 product path:** [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow). Seller clicks **Connect Amazon** on `/connection`.

Do not invent parameter names. At implementation time, follow Amazon’s current docs. The current documented website flow is:

1. ASI generates high-entropy `state`, stores it server-side bound to `organization_id`, `provider=SP_API`, `environment`, expiry.
2. Browser redirect to Seller Central consent, e.g. India:  
   `https://sellercentral.amazon.in/apps/authorize/consent?application_id={app}&state={state}`  
   Draft apps add `version=beta`.
3. Amazon may hit ASI’s registered **OAuth Login URI** with `amazon_callback_uri`, `amazon_state`, `selling_partner_id` (and optional `version`). ASI must associate the in-progress flow with the **already authenticated ASI org** (or `current_organization_id()` until login exists), then redirect to `amazon_callback_uri` with `amazon_state`, ASI `state`, optional `redirect_uri`.
4. Amazon redirects to registered **OAuth Redirect URI** with `state`, `selling_partner_id`, `spapi_oauth_code`.
5. ASI validates `state` (one-time, unexpired, org match). **Do not trust `selling_partner_id` for tenancy.**
6. Exchange `spapi_oauth_code` within **five minutes** (`grant_type=authorization_code`).
7. Store refresh token in SecretProvider; update `amazon_connections`.
8. Minimal Sellers `getMarketplaceParticipations`.
9. Mark `connected` or `degraded` / `error` per §25.

**Appstore / Login URI** is required for Amazon registration even if V1 UX is website-initiated. Same org-binding rules.

**SaaS UX must not ask the seller to paste a refresh token.** Sandbox `.env` remains a **developer** path only (12A.0).

---

## 12. OAuth State / CSRF

| Rule | Detail |
| --- | --- |
| Entropy | Cryptographic random (≥128 bits), URL-safe |
| Binding | `organization_id`, `provider`, `environment`, `connection_id` if row pre-created, expiry (~10 minutes; Amazon warns the whole flow may break after 10 minutes) |
| Storage | Short-lived server table `amazon_oauth_states` (hashed state at rest) or equivalent. Not a frontend cookie as the only copy. |
| Use | One-time: `consumed_at` set on success; reject replay |
| Login URI | Echo Amazon’s `amazon_state` unchanged; generate/replace ASI `state` per current docs |
| Headers | `Referrer-Policy: no-referrer` on auth pages (Amazon website-flow requirement) |

Do not accept `state` as proof of org. Load stored record by hash(state); if missing/expired/consumed → reject.

Until ASI has real users, bind to `current_organization_id()`. When sessions exist, also bind `session_id` / user id.

---

## 13. Organization Isolation

- Repositories: `WHERE organization_id = :org` always.
- Test Connection uses the org’s row + that row’s `token_reference` only.
- Callback: org from **stored state**, not from Amazon.
- SecretProvider has no `GET /secret/{reference}` API.
- Cross-org connection id in a URL → 404 (same as profit models).

`ON DELETE` for `organization_id`: `RESTRICT` or `CASCADE` only with a documented purge that also deletes secrets first. **Recommendation: RESTRICT** so org deletion is an explicit purge job.

---

## 14. Seller Identity Handoff

12B.1 **does not** create `amazon_seller_accounts`, `amazon_marketplaces`, or `seller_products`.

Handshake only:

```text
authorized
  → getMarketplaceParticipations
  → read selling_partner_id (from redirect and/or payload context)
  → store selling_partner_id + region/marketplace summary on the connection row if already discovered
  → 12B.2 persists identity entities
```

If the Sellers call returns participations, 12B.1 may store a **sanitized summary JSON** on the connection row (`marketplace_ids`, domains) for the Connection page. That is display/validation, not the canonical identity model. Prefer keeping summary optional and thin so 12B.2 remains the system of record for `AmazonMarketplace`.

---

## 15. Region / Marketplace

| V1 | Value |
| --- | --- |
| Marketplace | Amazon.in (`A21TJRUUN4KGV`) |
| Seller Central consent host | `sellercentral.amazon.in` |
| SP-API region | `eu` |
| Production host | `sellingpartnerapi-eu.amazon.com` |
| Sandbox host | `sandbox.sellingpartnerapi-eu.amazon.com` |

Connection stores `region`. Endpoint = f(region, environment). Participation discovery may list more than Amazon.in; UI can show them; V1 product still targets IN. Do not hard-code marketplace as a global singleton in new code.

---

## 16. Sandbox vs Production

| | Sandbox | Production |
| --- | --- | --- |
| Connection row | `environment=SANDBOX` | `environment=PRODUCTION` |
| Refresh token | Dev: `.env` via DevelopmentSecretProvider | SecretProvider |
| Base URL | sandbox host | production host |
| Consent | `version=beta` while app is Draft | no beta flag when Published |

Automated tests: **no live Amazon**, production or sandbox. Inject transports / SecretProvider fakes as 12A already does.

Runtime must not use a sandbox refresh token against production hosts. Guard: `environment` on the row selects base URL; DevelopmentSecretProvider refuses to resolve production references from `SP_API_SANDBOX_REFRESH_TOKEN`.

---

## 17. API Design

Keep prefix `/api/v1/amazon`. Extra=forbid public models; `public_model_dump`.

| Method | Path | Role |
| --- | --- | --- |
| `GET` | `/connection` | Sanitized overview (12A.1 keep; later read DB) |
| `POST` | `/connection/test` | Explicit validation call (12A.1 keep) |
| `POST` | `/connection/authorize` | Create state; return Seller Central URL (JSON `{authorization_url}`) — frontend navigates |
| `GET` | `/connection/login` | Amazon Login URI handler (query params from Amazon) |
| `GET` | `/connection/callback` | Redirect URI; exchange code; then redirect browser to `/connection?amazon=connected\|error` |
| `POST` | `/connection/disconnect` | Revoke secret + status `revoked` |

No endpoint returns secrets, `token_reference`, raw `spapi_oauth_code`, or LWA bodies.

Public GET fields (example): `id`, `organization_id`, `provider`, `environment`, `region`, `status`, `selling_partner_id`, `authorized_at`, `last_successful_validation_at`, `last_successful_sync_at`, `last_error_code`, `application`, `marketplace` (V1 display), `ads_api` placeholder.

12A.1 may remain the response when **no row exists** (sandbox env overview). After 12B.1A, GET prefers the persisted row for that org+SP_API+requested environment.

---

## 18. Frontend UX

`/connection` stays a connection page, not analytics.

Future SP-API card:

- Status label from state machine (Connected / Not connected / Pending / Degraded / Action required / Revoked)
- Environment, region, marketplace(s), seller id (safe), authorized at, last validation
- **Connect Amazon** → `POST /authorize` → redirect to Amazon (never paste token)
- **Reconnect**, **Test Connection**, **Disconnect**
- Ads card remains Not connected

Show `last_error_code` as a human sentence, not Amazon payload dumps.

---

## 19. Disconnect / Revocation

```text
Stop using the token for any future work
  → delete_secret(token_reference)
  → token_reference = null
  → status = revoked
  → last_error_code = disconnected (or amazon_revoked)
```

Keep the metadata row for audit. Do **not** delete profit/listing/ads snapshots. Do **not** create operational Amazon tables here, so no canonical purge in 12B.1. When those tables exist, follow 12B: blobs first, canonical retention is a PO/legal decision.

---

## 20. Reauthorization

Triggers: seller removes the app in Seller Central; `invalid_grant`; Amazon annual reauth policy; role/scope change requiring a new refresh token ([roles docs](https://developer-docs.amazon.com/sp-api/docs/roles-in-the-selling-partner-api): new roles need new authorization).

UX: status `error` (or `degraded` if retryable) + **Reconnect Amazon**. Reconnect **updates the same** `(org, provider, environment)` row. Replace secret only after the new token is stored (see §25).

Do not silently create a second SP-API production connection for the same org.

---

## 21. Role / Permission Strategy

Least privilege. **No write roles. No RDT/PII roles for V1.**

| Later domain | SP-API role family (validate at implementation) | 12B.1 need |
| --- | --- | --- |
| Sellers handshake | **Selling Partner Insights** (`getMarketplaceParticipations`) | **Yes** |
| Catalog / listings | Product Listing (read) | No |
| Orders (non-PII tracking) | Inventory and Order Tracking — **not** Direct-to-Consumer Shipping (Restricted) | No |
| Order PII / tax docs | Tax Remittance / Tax Invoicing / DTC Shipping **Restricted — avoid** | No |
| FBA inventory | Amazon Fulfillment (read reports/ops as needed later) | No |
| Finances | Finance and Accounting | No |
| Ads | Advertising roles | No |

Exact role checkboxes live in Solution Provider Portal / developer profile. 12B.1C must re-read the role mapping for `getMarketplaceParticipations` before go-live. Feeds/write operations are out of scope forever for this architecture freeze.

---

## 22. Configuration Model

**App-level (env / platform):** `SP_API_LWA_CLIENT_ID`, `SP_API_LWA_CLIENT_SECRET`, Amazon `application_id`, registered login URI, registered redirect URI, LWA token URL, region default, sandbox flags.

**Seller-level (DB + SecretProvider):** organization, connection, SPID, marketplaces (later), refresh token reference, status.

Do not put client_secret on the connection row. Do not put seller refresh tokens in `.env` for production orgs.

---

## 23. Local Development

| Mode | Behavior |
| --- | --- |
| 12A.0 sandbox CLI | Unchanged: `.env` refresh token + `python -m app.amazon` |
| 12A.1 Test Connection | Unchanged if no production row; uses env credentials |
| 12B.1A+ with DevelopmentSecretProvider | Default org can resolve sandbox token through a synthetic reference **without** OAuth |
| Production OAuth locally | Optional ngrok/registered localhost redirect; Draft app + `version=beta` |

`.env.example` names only. Developers never commit tokens.

---

## 24. Testing Strategy

No live Amazon in pytest.

| Area | Cases |
| --- | --- |
| Repository | Org scope; other-org 404; unique `(org, provider, environment)`; status transitions |
| SecretProvider | get/put/delete; missing secret; values never in `repr`/JSON; cannot resolve another org’s reference through the service |
| OAuth state | generate; expire; replay; wrong org |
| Callback (mocked LWA) | happy path; bad state; Amazon error; missing code; secret put fails; DB insert fails; rollback deletes orphan secret |
| Token exchange | `authorization_code` and existing `refresh_token`; parse; malformed; never log body |
| APIs | `public_model_dump`; no secret-shaped keys; no `token_reference` |
| Regression | existing 12A tests green |

---

## 25. Failure / Transaction Model

Recommended sequence after redirect:

1. Validate state (consume it).
2. Exchange `spapi_oauth_code` (LWA). Refresh token only in memory (`SecretStr`).
3. Ensure connection row exists (`pending_validation`).
4. `put_secret` → set `token_reference`.
5. Minimal Sellers validation.
6. `status=connected` (or `degraded` if step 5 is transient).

| Failure | Policy |
| --- | --- |
| Step 2 fails | No secret, no connected status; `error` or stay `pending_authorization` |
| Step 4 fails after 3 | No token_reference; status not connected |
| Step 4 succeeds, step 3/row commit fails | **delete_secret** (orphan cleanup) |
| Step 5 transient | Keep secret; `degraded` / `pending_validation`; Test Connection retries |
| Step 5 permanent `invalid_grant` | **delete_secret**; `error`; frontend Reconnect |

Do not mark `connected` if the secret is not stored. Do not leave a stored secret with `not_connected` and no way to delete it (reconcile job later if needed: secrets whose reference is not on any row).

---

## 26. Logging / Audit

**Allowed:** connection id, organization id, provider, environment, status from→to, operation name (`getMarketplaceParticipations`), error **category**.

**Forbidden:** refresh token, access token, client secret, `spapi_oauth_code`, Authorization headers, `x-amz-access-token`, raw callback query strings in info logs.

**V1 audit:** `authorized_at`, `updated_at`, status, `last_error_code` on the connection row. **No new general audit table** in 12B.1.

---

## 27. Secret Rotation

- **App client secret:** change env; seller rows unchanged.
- **Seller refresh token:** `put_secret` same reference, or new reference + update row + delete old. Never log the value.
- No automated rotator in 12B.1.

---

## 28. Connection Health

Health ≠ freshness.

Health: authorization valid, secret resolvable, Sellers validation works.

`GET /connection` reads **stored** status. Do not call Amazon on page load. **Test Connection** and post-OAuth handshake may call Amazon. No scheduler in 12B.1.

---

## 29. Logical Database Design

When 12B.1A starts (not now):

**`amazon_connections`** as §5.  
Indexes: PK `id`; `ix_amazon_connections_org` (`organization_id`); unique `uq_amazon_connections_org_provider_env`.  
FK `organization_id` → `organizations.id` **ON DELETE RESTRICT**.

**`amazon_oauth_states`** (12B.1C): `id`, `organization_id`, `provider`, `environment`, `state_hash`, `amazon_state` nullable, `expires_at`, `consumed_at`, `created_at`. Unique `state_hash`. TTL delete.

**`amazon_secret_ciphertexts`** only if EncryptedDatabaseSecretProvider is used: `token_reference` PK, `ciphertext`, `nonce`, `key_id`, `organization_id` (defense in depth), `created_at`. Not joined into Copilot queries.

No orders/listings/inventory/finance tables.

---

## 30. Secret Reference Security

Attack: guess `asi/amazon/SP_API/PRODUCTION/{other_org}/{uuid}`.

Mitigation: no public secret API; resolve only after `SELECT … WHERE id=:id AND organization_id=:org`; DevelopmentProvider must enforce the same. UUID `connection_id` in the path is not authorization.

---

## 31. Future Data Model Links

```text
amazon_connections
  → amazon_seller_accounts (12B.2)
      → amazon_marketplaces (12B.2)
          → seller_products / orders / … (later)
```

Connection stays authorization. Do not stuff catalog into this table.

---

## 32. Ads API Compatibility

`provider` on the same table is enough. **Separate row, separate `token_reference`, separate status, separate OAuth.** Do not assume SP-API consent grants Ads. Do not implement Ads scopes in 12B.1.

---

## 33. Security Threat Review

| Threat | Severity | Mitigation |
| --- | --- | --- |
| Plaintext refresh token in DB/backups | Critical | SecretProvider; no token columns |
| Frontend / Copilot / Evidence leak | Critical | Public models + `public_model_dump`; never pass SecretStr out |
| Logs | Critical | Reason codes only |
| Cross-org token use | Critical | Org-scoped load before `get_secret` |
| State replay / CSRF / callback spoof | High | One-time hashed state, expiry, Referrer-Policy |
| Stale/revoked refresh token | High | `error` + Reconnect; delete on permanent failure |
| Orphan secret | High | Saga delete; optional reconcile |
| Sandbox/prod mix | High | Environment on row; provider guards |
| Write scopes | High | Role policy; no Feeds writes |
| Secret manager outage | Medium | `degraded`; do not wipe tokens |
| Guessable token_reference | Medium | Opaque UUID; no HTTP resolver |

---

## 34. Product Owner Decisions

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | Multi-seller SaaS? | **Yes.** Isolate now. |
| 2 | Production secret manager? | **Cloud SM (AWS Secrets Manager as reference).** Fallback encrypted ciphertext table. |
| 3 | Amazon.in first? | **Yes.** Region `eu`, Seller Central `.in`. |
| 4 | Read-only SP-API? | **Yes.** 12B.1 only Selling Partner Insights. |
| 5 | One SP-API connection per org V1? | **Yes.** Unique org+provider+environment. |
| 6 | Disconnect retention? | Tokens deleted immediately; metadata kept; business snapshots kept. Canonical Amazon tables N/A until they exist. |
| 7 | First production auth? | **Internal/test selling partner** before public onboarding. |
| 8 | Redirect domains? | Register explicit login + redirect URIs per env (local, staging, prod). Draft + `version=beta` for tests. |

---

## 35. Recommended Implementation Slices

Do not start automatically. After approval:

1. **12B.1A** — `amazon_connections` persistence + sanitized GET overlay + status machine. No OAuth.  
2. **12B.1B** — SecretProvider + development impl + production adapter skeleton.  
3. **12B.1C** — Website OAuth (authorize, login, callback) + secure refresh-token storage.  
4. **12B.1D** — Minimal production Sellers validation; stop.  

Next: **12B.2** identity ingestion.

Details: [implementation plan](milestone-12b1-implementation-plan.md).

---

## 36. Final Recommendation

Approve 12B.1 as the credential and connection-metadata architecture. Keep 12A sandbox. Use Amazon’s website authorization workflow as the production Connect Amazon path. Store only `token_reference` in Postgres. Access tokens stay in memory. Copilot/frontend/Evidence/Skills never see secrets.

### Principles confirmed

- Rainforest untouched; SP-API seller-owned; Ads future and separate  
- Connection metadata is not business data  
- Secrets are not ordinary DB fields; DB stores `token_reference` only  
- Frontend, Copilot, EvidenceEnvelope, Skills never receive secrets  
- App LWA credentials ≠ seller refresh tokens; one app, many sellers  
- Access tokens short-lived, not business rows  
- OAuth state one-time, server-validated  
- Every connection organization-scoped  
- Sandbox ≠ production  
- Connection status ≠ data freshness  
- No Amazon writes; no Orders/Listings/Inventory/Reports/Finance ingest  
- No Ads API, Copilot, Skills, LangGraph/CrewAI/agents  
- 11A–12B remain intact  

---

## Architecture review summary

### Verdict

**APPROVE WITH CHANGES** (changes incorporated here): website OAuth as the documented Amazon flow; `pending_validation` in the saga; cloud SM preferred with encrypted-DB fallback; 12A test statuses remain distinct from persisted connection status.

### Recommended secret architecture

Cloud secret manager in production; encrypted ciphertext fallback; DevelopmentSecretProvider for `.env` sandbox; resolve secrets only from org-scoped connection rows.

### Recommended connection state model

`not_connected` → `pending_authorization` → `pending_validation` → `connected`, plus `degraded` / `error` / `revoked`.

### Recommended production authorization flow

Website authorization workflow from `/connection` (Seller Central consent URI for Amazon.in), Login URI + Redirect URI as registered with Amazon, `authorization_code` then `refresh_token` grants.

### Highest-risk security issues

Plaintext tokens; cross-org resolve; OAuth state replay; logging callback codes; sandbox/prod mix.

### Product Owner decisions required

SaaS, secret manager vendor, Amazon.in, read-only, one connection per org, first test seller, redirect domains.

### Suggested ADR

[ADR 0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md).

### Implementation slice recommendation

**12B.1A — Connection Metadata Persistence**, not Orders, Catalog, Inventory, Reports, Finances, Ads, Copilot, or Skills.

# Milestone 12B.1C — Amazon Seller Authorization Flow Architecture

**Date:** 23 August 2026  
**Status:** Architecture approved. **Implemented through 12B.1C.5** (authorize, callback, LWA exchange). Live Amazon website round-trip remains incomplete. This file is the architecture record.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A** and **12B.1B.1–12B.1B.5**  
**Parent architecture:** [milestone-12b1-production-connection-security-architecture.md](milestone-12b1-production-connection-security-architecture.md)  
**Parent plan:** [milestone-12b1-implementation-plan.md](milestone-12b1-implementation-plan.md)  
**Prior:** [milestone-12b1b5-production-secret-provider-preparation.md](milestone-12b1b5-production-secret-provider-preparation.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This document does not implement OAuth, Login with Amazon, callbacks, token exchange, SecretProvider changes, SP-API client changes, frontend, Copilot, or Skills.

Amazon docs to re-read at coding time: [Authorize Public Applications](https://developer-docs.amazon.com/sp-api/docs/authorize-public-applications), [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow), [Selling Partner Appstore Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/selling-partner-appstore-authorization-workflow). Do not invent callback parameter names.

---

## Frozen principles

1. Application code must never directly access secrets.
2. The database stores `token_reference` and connection metadata only — never refresh tokens, access tokens, or client secrets.
3. SecretProvider owns seller refresh tokens. App LWA `client_id` / `client_secret` stay in platform config.
4. Access tokens are memory-only. No V1 access-token cache.
5. Tenancy is `organization_id`. `selling_partner_id` is not the tenant key.
6. Sandbox Test Connection must never persist `connected`.
7. Copilot, Skills, frontend, and EvidenceEnvelope never see secrets.
8. No live Amazon in pytest.

---

## 1. Current Amazon Integration State

### Current capability

ASI can persist **connection metadata**, hide secrets behind **SecretProvider**, and call **sandbox Sellers** with a developer `.env` refresh token. There is still **no seller authorization**. A sandbox Test Connection success is not a connected Amazon seller.

| Layer | Current capability |
| --- | --- |
| DB | `amazon_connections`: org, provider, environment, region, status machine, optional `selling_partner_id`, nullable `token_reference`. Unique `(organization_id, provider, environment)`. No token columns. |
| Repository | Org-scoped CRUD. **Cannot write** `token_reference` (intentional). |
| Service / API | `GET /api/v1/amazon/connection`, `POST /connection/test`. Display `status` stays `NOT_CONNECTED`. Test result `CONNECTED` is sandbox-only. |
| Frontend | `/connection` shows persisted vs sandbox panels. **No Connect Amazon**. |
| SecretProvider | Protocol + `DevelopmentSecretProvider` + factory. `production` **fails closed**. |
| Resolver | `AmazonConnectionSecretResolver` can map a row to an ASI reference. Unused by the live client. |
| SP-API client | Resolves `development_sandbox_token_reference()` only. LWA is **refresh_token grant only**. |
| Tenancy | `current_organization_id()` (default org until product login). |

### Missing capability

- Connect Amazon / Seller Central consent
- OAuth Login URI and Redirect URI
- `authorization_code` exchange
- `put_secret` + bind `token_reference`
- Disconnect / revoke
- Production host + seller-token Test Connection (**12B.1D**)
- Listings/Orders/Ads ingest, Copilot, Skills

### Required future components

- `AmazonOAuthState` (short-lived, hashed)
- Authorize / login / callback / disconnect routes
- LWA `authorization_code` helper
- Narrow repository bind/clear of `token_reference`
- Wire SP-API client through the resolver when a row has a reference (12B.1D or end of 12B.1C.6)
- Connect / Reconnect / Disconnect UI

---

## 2. Seller Authorization Vision

A seller links **their** Selling Partner account to **this** ASI organization. ASI never asks them to paste a refresh token.

1. Click **Connect Amazon** on `/connection`
2. Authenticate in Seller Central
3. Grant the EWise public application
4. Amazon returns `spapi_oauth_code` to ASI’s registered redirect URI
5. ASI exchanges the code with **app** LWA credentials, stores the **seller** refresh token in SecretProvider, saves only `token_reference` on the row
6. Later SP-API calls resolve that reference (12B.1D+). Copilot/Skills never see secrets.

App credentials (`client_id` / `client_secret` / `application_id`) stay in platform config. One ASI LWA app, many seller refresh tokens (ADR 0006).

**V1 product path:** Website Authorization Workflow. Appstore / Login URI is still required for Amazon app registration even if V1 UX is website-initiated.

---

## 3. Complete Authorization Flow

```text
Seller
  → ASI UI  POST /connection/authorize
  → Seller Central consent  (application_id, state, optional version=beta)
  → Amazon Login URI  GET /connection/login
  → Amazon Redirect URI  GET /connection/callback  (state, selling_partner_id, spapi_oauth_code)
  → LWA authorization_code  (≤ 5 minutes)
  → SecretProvider.put_secret(ASI reference, SecretStr refresh token)
  → amazon_connections.token_reference + pending_validation
  → 12B.1D getMarketplaceParticipations → connected | degraded | error
```

| Step | What happens |
| --- | --- |
| Connect | Authenticated ASI org (today: `current_organization_id()`). Create/ensure SP-API row for the chosen **environment**. Generate high-entropy `state`, store hash bound to org + connection. Return `{ authorization_url }`. Frontend navigates. Never paste tokens. |
| Seller Central | India V1: `sellercentral.amazon.in/apps/authorize/consent`. Draft apps: `version=beta`. Host comes from **region/marketplace config**, not a hardcoded global singleton in new logic. |
| Login URI | Amazon may send `amazon_callback_uri`, `amazon_state`, `selling_partner_id`. Bind to the **already known ASI org from stored state**, not from Amazon. Redirect back with Amazon’s `amazon_state` unchanged + ASI `state`. `Referrer-Policy: no-referrer`. |
| Callback | Validate one-time unexpired state. **Do not use `selling_partner_id` for tenancy.** Exchange `spapi_oauth_code`. On success: `put_secret`, bind `token_reference`, set `pending_validation`, store SPID as identity hint only. Redirect browser to `/connection?amazon=…` with **no code in the ASI URL**. |
| SecretProvider | Only refresh token. Access token never persisted. |
| Activation | Status is **not** `connected` until 12B.1D Sellers handshake. Sandbox `.env` must not set `connected`. |

If `put_secret` succeeds and DB bind fails: `delete_secret` (orphan cleanup). If exchange fails: no secret; status `error` or remain `pending_authorization`.

---

## 4. Connection State Machine

Keep the **existing** CHECK values. Do not introduce a second enum.

| Persisted status | Meaning | Trigger | Allowed actions |
| --- | --- | --- | --- |
| `not_connected` | No seller grant. Default / after cleanup. | Row create; optional post-disconnect purge | Connect Amazon; sandbox test (does **not** promote) |
| `pending_authorization` | Consent started; no usable secret yet | `POST /authorize` | Wait; cancel/disconnect; reject replayed callback |
| `pending_validation` | Refresh token stored; Sellers handshake not done | Callback + `put_secret` + bind | 12B.1D validate; disconnect |
| `connected` | Handshake succeeded; not “data is current” | 12B.1D 200 | Test Connection; reconnect; disconnect. GET still must **not** call Amazon |
| `degraded` | Transient Amazon/network failure; **keep secret** | Timeout / 5xx / rate limit | Retry test; disconnect |
| `error` | Permanent auth failure or exhausted callback | `invalid_grant` / 401 after stored token | Reconnect; disconnect |
| `revoked` | Seller or ASI disconnected; secret deleted | Disconnect; Amazon revocation | Connect again (new grant) |

**Display** `CONNECTED` / `FAILED` on `POST /connection/test` stays a **test-result**, not this machine.

**Do not persist `TOKEN_REFRESH_REQUIRED`.** Access-token refresh is in-process LWA (`expires_in` ~3600s, memory only). If the **refresh token** is dead, use `error` or `revoked`.

**Product “disconnected”** = `revoked` (secret gone). Optional later move to `not_connected` after explicit purge.

```text
not_connected
  → pending_authorization     (Connect Amazon)
  → pending_validation        (callback + secret stored)
  → connected                 (12B.1D handshake)
  → degraded | error
  → revoked                   (disconnect)
  → pending_authorization     (reconnect)
```

Sandbox test must **never** write `connected`.

---

## 5. OAuth Security Design

| Concern | Design |
| --- | --- |
| CSRF | Cryptographic `state` ≥128 bits, URL-safe. Server stores **hash**, not the raw value as the only copy. |
| Binding | `organization_id`, `provider`, `environment`, `connection_id`, `expires_at` (~10 minutes). Later: session/user id. |
| Replay | `consumed_at` on first successful use; second use rejected. |
| Org | Org comes from **stored state**, never from query `selling_partner_id` or a client-supplied reference. |
| Login URI | Echo `amazon_state`; do not treat Amazon’s SPID as tenant key. |
| Code | `spapi_oauth_code` is secret, ≤5 minutes, memory only, never logged, never on GET `/connection`. |
| Headers | `Referrer-Policy: no-referrer` on login/callback. |
| Frontend | No token paste; no `token_reference` in JSON (`public_model_dump` already rejects `token*`). |

**Temporary:** raw state, `spapi_oauth_code`, access token, in-flight refresh token (`SecretStr`).  
**Persisted (non-secret):** connection metadata, hashed OAuth state, opaque `token_reference`, SPID, timestamps, status.  
**Secret store only:** seller refresh token.

---

## 6. Token Lifecycle Design

### Initial grant

```text
Seller consents → spapi_oauth_code
  → grant_type=authorization_code + app client_id/secret + redirect_uri
  → refresh_token (SecretStr)
  → build_asi_secret_reference(SP_API, environment, org, connection_id)
  → put_secret(reference, refresh_token)
  → bind amazon_connections.token_reference
  → drop code and refresh token from process memory
```

### Runtime (after 12B.1D wires the resolver)

```text
Load row WHERE organization_id = current org
  → AmazonConnectionSecretResolver (org + row must match reference)
  → get_secret(token_reference)
  → LwaClient grant_type=refresh_token
  → access token SecretStr in memory → one SP-API call → drop
```

| Token | Storage | Expiry |
| --- | --- | --- |
| Refresh | SecretProvider only | Long-lived until revoke/disconnect |
| Access | Memory only; **no V1 cache** | ~3600s; fetch per call |
| App client_secret | Process/platform env | Rotate env; seller rows unchanged |

**Revocation:** `delete_secret` then clear `token_reference` and set `revoked`.  
**Invalid grant:** delete or isolate secret; status `error`.  
**Transient failures:** keep secret; `degraded`.

`AMAZON_SECRET_BACKEND=production` currently **fails closed** (12B.1B.5). 12B.1C local/CI stays on `development`. Do not use sandbox `.env` as a seller grant.

---

## 7. Database Impact

**No migration in this review.** Proposed for 12B.1C implementation:

### New table `amazon_oauth_states` (required)

| Column | Role |
| --- | --- |
| `id` | UUID |
| `organization_id` | FK, `ON DELETE RESTRICT` |
| `provider`, `environment` | SP_API + SANDBOX / PRODUCTION |
| `connection_id` | optional FK to `amazon_connections` |
| `state_hash` | unique; never log raw state |
| `amazon_state` | nullable, Login URI round-trip |
| `expires_at`, `consumed_at`, `created_at` | TTL + replay |

### `amazon_connections`

No new secret columns. Need a **narrow internal bind/clear** of existing `token_reference` (VARCHAR 128; ASI pointers fit). Optional later: thin `marketplace_ids` JSON for display — prefer **not** in 12B.1C; 12B.2 owns canonical marketplaces.

**Not in 12B.1C:** `amazon_seller_accounts`, listings/orders tables, general audit log. V1 audit = connection timestamps + `last_error_code`.

---

## 8. Multi-Tenant Security Model

```text
Organization A  ──x──  Organization B secrets, rows, callbacks, references
```

- Every connection query: `WHERE organization_id = :org`
- Callback org = OAuth state row, not Amazon
- `get_secret` only after org-scoped load; resolver already rejects org/connection/provider/environment mismatch
- Cross-org connection id → 404
- No `GET /secret/{reference}`
- Unique `(organization_id, provider, environment)`: one production SP-API link per org in V1 (ADR 0005)
- Ads later: **separate row**, separate `token_reference`
- Org delete: **RESTRICT** until an explicit purge deletes secrets first

Until product login exists, bind to `current_organization_id()`. That is a V1 limit, not a license to skip state binding.

---

## 9. Marketplace Strategy

OAuth grain is **selling partner + ASI organization**, not Amazon.in as a singleton.

| Concept | Where it lives |
| --- | --- |
| `organization_id` | Tenant |
| `selling_partner_id` | Amazon merchant; identity hint, not tenant key |
| `region` | Already on the row: `eu` / `na` / `fe` (India = `eu`) |
| `environment` | SANDBOX vs PRODUCTION → host selection |
| `marketplace_id` | Discovered via Sellers (e.g. `A21TJRUUN4KGV` for IN); canonical in **12B.2** |

V1 product target remains Amazon.in (`A21TJRUUN4KGV`, consent host `sellercentral.amazon.in`, SP-API `eu`). Consent base URL and SP-API host should be **maps keyed by region/marketplace**, so NA/EU/UK can be added without rewriting OAuth.

Do not hardcode Amazon.in inside SecretProvider, resolver, or Copilot. Default display marketplace may stay `settings.default_marketplace` until 12B.2.

One production connection can cover **multiple** marketplace participations. Do not require one OAuth per marketplace.

---

## 10. Error Handling

| Case | Behaviour |
| --- | --- |
| Seller denies consent | No code; stay `pending_authorization` or return to `not_connected`; UI: cancelled |
| Callback missing/invalid state | 4xx; no exchange; no secret |
| Expired / replayed state | Reject; no exchange |
| Code older than ~5 minutes | LWA fail; `error`; no `put_secret` |
| `put_secret` then DB fail | `delete_secret`; `error` |
| SecretProvider / `production` backend down | Fail closed; do not write `connected`; do not fall back to sandbox `.env` for a production row |
| Amazon 5xx / timeout on handshake | `degraded`; **keep** secret |
| `invalid_grant` / 401 | `error` or `revoked`; delete secret |
| Seller deletes Amazon account | Same as revocation when Amazon rejects refresh |
| User hits Test Connection during pending | Must not mark `connected` |

Logs: connection id, status, `last_error_code` only. Never log code, tokens, or full callback query strings.

---

## 11. Implementation Roadmap

Do not start coding until this review is approved. **12B.1D** still owns marking `connected` via Sellers.

### 12B.1C.1 — Authorization architecture freeze

**Objective:** Persist this review as the coding contract (Amazon docs re-read).  
**Files affected:** `docs/milestone-12/` only.  
**Dependencies:** none.  
**Risks:** drifting from Amazon’s live query names.  
**Exit criteria:** Approved doc; no OAuth code.

### 12B.1C.2 — Authorize start + OAuth state

**Objective:** `POST /connection/authorize` creates hashed state and returns Seller Central URL. Row → `pending_authorization`.  
**Files affected:** `oauth.py`, `models.py`, migration `0008_amazon_oauth_states`, routes, config (`application_id`, consent base, redirect URIs).  
**Dependencies:** 12B.1C.1.  
**Risks:** leaking raw state; wrong consent host.  
**Exit criteria:** Mocked state create/expiry; no Amazon call; no secrets.

### 12B.1C.3 — Frontend Connect Amazon

**Objective:** Connect button → authorize → navigate to Amazon. Query `amazon=` flash messages. Still no token paste.  
**Files affected:** `amazon-connection.tsx`, `api.ts`, types, UI tests.  
**Dependencies:** 12B.1C.2.  
**Risks:** putting codes in client storage.  
**Exit criteria:** Browser: click Connect, leave ASI; no secrets in DOM.

### 12B.1C.4 — Login URI + callback intake

**Objective:** `GET /login` and `GET /callback` validate state, consume it. Redirect to `/connection?amazon=error` on failure. Exchange may land in 12B.1C.5.  
**Files affected:** routes, oauth consume, Referrer-Policy.  
**Dependencies:** 12B.1C.2.  
**Risks:** CSRF, logging query strings, trusting SPID.  
**Exit criteria:** Replay/expiry/wrong-org tests; no `spapi_oauth_code` on later GET connection.

### 12B.1C.5 — Token exchange

**Objective:** LWA `grant_type=authorization_code`; keep `refresh_token` grant. Mocked HTTP only.  
**Files affected:** `lwa.py`, tests.  
**Dependencies:** 12B.1C.4.  
**Risks:** logging LWA body; mixing app secret vs seller token.  
**Exit criteria:** Success/fail mocks; exceptions contain no code/token.

### 12B.1C.6 — Secret put + `token_reference` bind

**Objective:** `put_secret` → narrow repository bind → `pending_validation`. Rollback `delete_secret` on DB failure.  
**Files affected:** repository bind/clear, connection service, resolver, factory (still development in CI).  
**Dependencies:** 12B.1C.5, 12B.1B.4–5.  
**Risks:** orphan secrets; writing tokens to Postgres; exposing `token_reference` on GET.  
**Exit criteria:** Bind tests; public JSON still omits `token_reference`; production backend still fail-closed.

### 12B.1C.7 — Disconnect + activation handoff

**Objective:** `POST /connection/disconnect` deletes secret, clears reference, `revoked`. Do **not** set `connected`. Handoff to 12B.1D for Sellers handshake.  
**Files affected:** service, routes, frontend Disconnect.  
**Dependencies:** 12B.1C.6.  
**Risks:** leaving secrets after disconnect; sandbox test promoting status.  
**Exit criteria:** Disconnect tests; GET still no Amazon; sandbox test still does not persist `connected`.

**Out of 12B.1C:** Orders, Listings, Inventory, Reports, Ads OAuth, Copilot, Skills, live Amazon in pytest.

---

## 12. Risks and Recommendations

1. **Approve this review, then 12B.1C.2** after the architecture is on disk (this file). Do not start frontend without an authorize API.
2. **Reuse the existing status enum** — do not migrate a parallel INITIAL/PENDING vocabulary.
3. **Repository bind is mandatory in 12B.1C.6** — 12B.1A correctly forbade `token_reference` writes.
4. **Wire the resolver in 12B.1D** (or end of 12B.1C.6 for `pending_validation` only) — today’s client still uses the development sandbox reference.
5. **Re-read Amazon website + Appstore docs at implementation.**
6. **CI stays `AMAZON_SECRET_BACKEND=development`** — `production` fails closed until a real cloud provider exists.
7. **Do not treat sandbox `.env` as seller OAuth.**
8. **No live Amazon in pytest**; manual internal-seller test is out of band (12B.1D).

**Recommendation:** Accept 12B.1C as specified: website authorization, hashed OAuth state, SecretProvider-only refresh tokens, `pending_validation` after bind, `connected` only in 12B.1D.

---

## Explicit non-goals (this checkpoint)

- Write OAuth or LWA `authorization_code` code
- Create migrations
- Store tokens
- Modify SecretProvider implementations
- Modify SP-API client, frontend, Copilot, or Skills

Wait for explicit approval before **12B.1C.2 — Authorize start + OAuth state**.

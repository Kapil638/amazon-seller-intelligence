# Milestone 12B.1 — Implementation Plan

**Date:** 23 August 2026  
**Status:** Historical plan. Slices **12B.1A–12B.1D are implemented** (as-built docs in this folder). Do not treat this file as “not started.”  
**Architecture:** [milestone-12b1-production-connection-security-architecture.md](milestone-12b1-production-connection-security-architecture.md)

Next approved work is **12B.2**, not a continuation of this plan’s unimplemented ingest. Latest Alembic head: `0008_amazon_oauth_states`.

Expected order after architecture approval:

```text
12B.1A → 12B.1B → 12B.1C → 12B.1D → stop → 12B.2
```

Do not skip to Orders, Catalog, Inventory, Reports, Finances, Ads API, Copilot, or Skills.

---

## Shared constraints (every slice)

- Reuse `app/amazon/` isolation. Do not import Copilot, Profit, Advertising engines, or Rainforest into new connection code except existing `current_organization_id()`.
- Do not break 12A.0 (`python -m app.amazon`) or 12A.1 `GET/POST /connection` sandbox behavior until an overlay is tested.
- No live Amazon in pytest. Keep `conftest.py` clearing SP-API env.
- Public responses: extra-forbid + `public_model_dump`. Never return `token_reference` (the sanitizer already treats `token` as a forbidden key fragment).
- Latest Alembic head at plan time: `0006_advertising_models`. As-built head after 12B.1C: `0008_amazon_oauth_states`.

---

## 12B.1A — Connection Metadata Persistence

### Objective

Persist `AmazonConnection` metadata in Postgres (and SQLite tests). Serve sanitized GET from the row when present. Status is a state machine, not a boolean. **No OAuth. No SecretProvider. No Amazon business tables.**

### Files likely added/changed

| Path | Change |
| --- | --- |
| `apps/api/app/persistence/models.py` | `AmazonConnection` SQLAlchemy model |
| `apps/api/migrations/versions/0007_amazon_connections.py` | Table, unique, indexes, FK |
| `apps/api/app/persistence/repositories.py` | Org-scoped get/upsert; other-org miss |
| `apps/api/app/amazon/connection.py` | Overview overlay: DB row if any, else 12A.1 env view |
| `apps/api/app/api/routes/amazon_connection.py` | Same URLs; richer sanitized fields when row exists |
| `apps/api/tests/test_amazon_connection.py` | Org isolation, uniqueness, sanitizer |
| `apps/web/src/lib/types.ts` + `amazon-connection.tsx` | Optional persisted fields; no dashboard |
| `apps/api/.env.example` | No secrets; maybe `SP_API_CONNECTION_ENVIRONMENT` later |

### Data model impact

`amazon_connections` only. Columns per architecture §5 (including nullable `token_reference` unused until 12B.1B). Unique `(organization_id, provider, environment)`. `ON DELETE RESTRICT` to `organizations`.

### APIs

- `GET /api/v1/amazon/connection` — persisted status if row exists
- `POST /api/v1/amazon/connection/test` — still env/sandbox client as today unless a later slice wires SecretProvider

No authorize/callback/disconnect yet.

### Tests

- Insert for org A; org B cannot read
- Unique constraint on org+provider+environment
- Status stored and returned; `token_reference` absent from JSON
- 12A tests still pass (no row → previous env overview)

### Security checks

- No token columns
- Logs: connection id + status only
- GET does not call Amazon

### Explicit non-goals

OAuth, SecretProvider, Sellers ingest, Ads, Copilot, frontend Connect Amazon redirect.

### Exit criteria

- Migration applies on Postgres; SQLite tests create the table via metadata or migration equivalent used by the project
- GET can return `not_connected` from a row without implying freshness
- Unique + org isolation tests green
- 12A regression green

---

## 12B.1B — SecretProvider

### Objective

Introduce a narrow `SecretProvider` used only by Amazon connection code. Development implementation for local/sandbox. Production adapter **skeleton** (AWS Secrets Manager **or** encrypted ciphertext table — follow PO choice). **No seller OAuth. No ingest.**

### Files likely added/changed

| Path | Change |
| --- | --- |
| `apps/api/app/amazon/secrets.py` (or `secrets/`) | Protocol + DevelopmentSecretProvider |
| `apps/api/app/amazon/secrets_aws.py` or `secrets_encrypted.py` | Skeleton behind settings flag; may no-op until credentials exist |
| `apps/api/app/core/config.py` | `amazon_secret_backend=development\|encrypted_db\|aws_secrets_manager` |
| `apps/api/app/amazon/connection.py` | Resolve refresh token for **test** only if row has `token_reference`; else 12A env |
| `apps/api/app/persistence/models.py` | Optional `amazon_secret_ciphertexts` if encrypted_db |
| `apps/api/tests/test_amazon_secrets.py` | put/get/delete; no serialization; missing secret |

### Data model impact

None required if development-only. Encrypted fallback adds ciphertext table, still **not** plaintext on `amazon_connections`.

### APIs

No generic secret HTTP API. Existing test endpoint may use provider when a reference exists.

### Tests

- Development provider returns sandbox env token only for configured default-org synthetic ref
- `repr` / JSON / logs never contain `Atzr|` / client secret
- Delete then get raises a safe configuration/auth error
- Service refuses to `get_secret` a reference that does not belong to the loaded org row

### Security checks

- No `GET /secret/...`
- Settings still `SecretStr` for env material
- Production skeleton does not log AWS/KMS payloads

### Explicit non-goals

OAuth, storing a real seller token from Amazon, connection status `connected` from OAuth, cloud SM provisioning in CI.

### Exit criteria

- Interface + development provider tested
- Production adapter exists as a typed skeleton or encrypted_db path behind a flag
- 12A sandbox test still works without a connection row
- pytest never calls AWS or Amazon

---

## 12B.1C — Production Authorization Flow

### Objective

Website authorization workflow: Connect Amazon → Seller Central consent → login URI → redirect URI → `authorization_code` exchange → SecretProvider put → connection row `pending_validation`. **No Orders/Listings. No Copilot.**

Re-read Amazon website + Appstore authorization docs at implementation time.

### Files likely added/changed

| Path | Change |
| --- | --- |
| `apps/api/app/amazon/lwa.py` | Add `authorization_code` grant helper; keep refresh_token |
| `apps/api/app/amazon/oauth.py` | State create/consume; consent URL builder (`sellercentral.amazon.in`, `application_id`, `state`, optional `version=beta`) |
| `apps/api/app/persistence/models.py` | `AmazonOAuthState` |
| `apps/api/migrations/versions/0008_amazon_oauth_states.py` | Short-lived state table |
| `apps/api/app/api/routes/amazon_connection.py` | `POST /connection/authorize`, `GET /connection/login`, `GET /connection/callback`, `POST /connection/disconnect` |
| `apps/api/app/core/config.py` | `sp_api_application_id`, login/redirect URIs, consent base URL |
| `apps/api/.env.example` | Names only |
| `apps/web/src/components/amazon-connection.tsx` | Connect / Reconnect / Disconnect; no token paste |
| `apps/web/src/lib/api.ts` | authorize + disconnect helpers |
| `apps/api/tests/test_amazon_oauth.py` | Mocked LWA; state expiry/replay; rollback orphan secret |

### Data model impact

`amazon_oauth_states`. Connection row updates: status, `selling_partner_id` from redirect (not for tenancy), `token_reference`, timestamps.

### APIs

As architecture §17. Callback redirects to `/connection?amazon=...` without putting codes in our logs.

### Tests

- State generate/validate/expire/replay/wrong org
- Mock LWA `authorization_code` success and failure
- Secret put failure → no connected
- DB failure after put → `delete_secret`
- Disconnect deletes secret and sets `revoked`
- Callback JSON/HTML never includes `spapi_oauth_code` in subsequent GET connection

### Security checks

- `Referrer-Policy: no-referrer` on login/callback
- Org from stored state only
- `spapi_oauth_code` exchanged within documented 5-minute window (test clock)
- Disconnect immediately deletes secret

### Explicit non-goals

Sellers persistence beyond optional thin display fields, Appstore listing, Ads OAuth, user login product (bind `current_organization_id()`).

### Exit criteria

- Mocked end-to-end authorize→callback→secret→row
- Frontend can start Connect Amazon without exposing secrets
- 12A regression green
- No live Amazon in CI

---

## 12B.1D — Minimal Production Validation

### Objective

After a stored seller refresh token exists, call **only** `getMarketplaceParticipations` (production or sandbox host per `environment`). Update `connected` / `degraded` / `error`. Hand off identity ingest to 12B.2. **Stop.**

### Files likely added/changed

| Path | Change |
| --- | --- |
| `apps/api/app/amazon/sandbox.py` or new `sp_api/sellers_client.py` | Production base URL from region+environment; reuse parse/DTOs |
| `apps/api/app/amazon/connection.py` | Post-OAuth and Test Connection use connection token via SecretProvider + app LWA env |
| `apps/api/tests/test_amazon_connection.py` | Mocked Sellers 200 / 401 / timeout; status transitions |

### Data model impact

Timestamps and `selling_partner_id` / optional sanitized marketplace summary on **connection row only**. No `amazon_seller_accounts` table.

### APIs

- Existing `POST /connection/test` against persisted production/sandbox connection
- Callback path from 12B.1C calls the same validation helper

### Tests

- 200 → `connected` + `last_successful_validation_at`
- 401/invalid_grant → secret deleted or `error` per architecture (permanent vs transient)
- Timeout → `degraded`, secret kept
- Response still has no payload/tokens
- GET page load still does not call Amazon

### Security checks

- Access token memory only
- RDT not used
- No write APIs

### Explicit non-goals

12B.2 identity tables, listings, orders, inventory, reports, finances, Ads, Copilot tools, schedulers.

### Exit criteria

- Handshake documented and tested with mocks
- Manual internal-seller test is **out of band** (not pytest)
- Next milestone is 12B.2, not 12C or Skills

---

## Cross-slice regression

After each slice: `uv run python -m pytest` in `apps/api` and `npm run test` in `apps/web`.

## What this plan will not do

- Create orders, listings, inventory, reports, or finance tables
- Redesign intelligence engines
- Ship LangGraph/agents
- Call production Amazon from automated tests
- Start 12B.1A automatically

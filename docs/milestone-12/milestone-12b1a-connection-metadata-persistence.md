# Milestone 12B.1A — Connection Metadata Persistence

**Date:** 23 August 2026  
**Status:** Architecture review and implementation plan only. **Do not implement until explicitly approved.**  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`)  
**Parent architecture:** [milestone-12b1-production-connection-security-architecture.md](milestone-12b1-production-connection-security-architecture.md)  
**Parent plan:** [milestone-12b1-implementation-plan.md](milestone-12b1-implementation-plan.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This document does not create migrations, OAuth, SecretProvider, or ingest.

---

## Frozen principles

1. Amazon connection metadata is **not** Amazon business data.
2. The database stores connection **state only**.
3. The database must **not** store `refresh_token`, `access_token`, LWA client secret, or API credentials.
4. Secrets remain outside normal application tables.
5. Token reference architecture comes later through SecretProvider (12B.1B).
6. Organization ownership is mandatory.
7. Copilot cannot directly access Amazon connections.
8. Skills cannot directly access Amazon connections.
9. Rainforest remains independent marketplace intelligence.
10. SP-API remains the seller-owned intelligence provider.

---

## 1. Current Amazon Connection Assessment

### Current implementation

12A.1 is env/service-based. `GET /api/v1/amazon/connection` always returns `NOT_CONNECTED` and `last_test_at=null`. `POST /api/v1/amazon/connection/test` talks to sandbox via `.env` LWA + refresh token, then returns a sanitized in-memory result. Nothing is written to Postgres. Organization is `current_organization_id()` (`default_organization_id`). There is no SQLAlchemy `AmazonConnection`.

### Reusable components

- Isolated `app/amazon/` (LWA, sandbox client, DTOs, provenance)
- `AmazonConnectionService.overview()` / `test_sp_api()`
- `public_model_dump` / `contains_secret_key` (rejects any key containing `token`)
- `current_organization_id()`, `session_scope()`, org-scoped repository pattern
- Alembic numbered revisions; SQLite tests via `Base.metadata.create_all`
- `/connection` UI already shows provider, environment, marketplace, last test
- Ads placeholder stays `NOT_CONNECTED`

### Components requiring change

- `apps/api/app/persistence/models.py` — add SQLAlchemy model
- `apps/api/migrations/versions/0007_amazon_connections.py` — new
- `apps/api/app/persistence/repositories.py` — org-scoped repository
- `apps/api/app/amazon/connection.py` — GET overlay from DB; keep env fallback
- Public Pydantic models / `apps/web/src/lib/types.ts` — persisted fields, state-machine status
- `amazon-connection.tsx` — display persisted status without OAuth UI
- Tests — org isolation, uniqueness, sanitizer, 12A regression

### Potential risks

- Colliding 12A.1 test statuses (`CONNECTED` / `FAILED`) with the persisted state machine (`connected` / `error`)
- Putting `token_reference` on a public Pydantic model (`token` is a forbidden key fragment → 500)
- Sandbox Test Connection promoting a row to `connected` (env token is not seller OAuth)
- GET starting to call Amazon
- Copilot/Skills accidentally importing the repository

---

## 2. Proposed Database Design

Table: `amazon_connections`  
This is **authorization metadata**, not listings/orders/fees.

| Column | Type | Null | Why |
| --- | --- | --- | --- |
| `id` | UUID PK | no | ASI identity for the row |
| `organization_id` | UUID FK → `organizations.id` | no | Tenant ownership; all queries must filter this |
| `provider` | `VARCHAR(32)` | no | `SP_API` vs future `ADS_API` on a **separate** row |
| `environment` | `VARCHAR(32)` | no | `SANDBOX` vs `PRODUCTION` must not share a row |
| `region` | `VARCHAR(8)` | no | SP-API region (`eu` / `na` / `fe`; India = `eu`) |
| `status` | `VARCHAR(32)` | no | State machine, not a boolean |
| `selling_partner_id` | `VARCHAR(64)` | yes | Amazon merchant id after validation/redirect; **not** the tenant key |
| `application_id` | `VARCHAR(128)` | yes | Amazon app id used later for consent URI (not a secret) |
| `token_reference` | `VARCHAR(128)` | yes | Opaque pointer for 12B.1B; unused now; **never public** |
| `authorized_at` | timestamptz | yes | First successful token persist + validation (null in 12B.1A) |
| `last_successful_validation_at` | timestamptz | yes | Last Sellers handshake / Test Connection against **this row** |
| `last_successful_sync_at` | timestamptz | yes | Reserved for 12B.2+ ingest freshness; **always null in 12B.1** |
| `last_error_at` | timestamptz | yes | When the last connection error occurred |
| `last_error_code` | `VARCHAR(64)` | yes | Stable code: `authentication`, `throttled`, `secret_missing`, … |
| `created_at` / `updated_at` | timestamptz | no | Audit |

**Do not add:** `refresh_token`, `access_token`, `client_secret`, `client_id`, LWA bodies, Amazon payloads.

### Keys / indexes

- PK: `id`
- FK: `organization_id` → `organizations.id` **ON DELETE RESTRICT** (do not cascade-delete authorization history)
- Unique: `uq_amazon_connections_org_provider_env` on `(organization_id, provider, environment)`
- Index: `ix_amazon_connections_org` on `organization_id`

### V1 uniqueness

One SP-API sandbox row and one SP-API production row per org. Reconnect updates the same row.

Multi-account later: add NOT NULL `selling_partner_id` into the unique key after backfill. Do not invent a dummy SPID in V1.

### 12B.1A lifecycle

Rows may exist as `not_connected` with `token_reference` null. Do not auto-create `connected`. Do not write secrets. Optional later OAuth reuses this row.

---

## 3. Connection State Machine

Status is **not** a boolean and **not** “data is current.” `connected` means authorization validated. `last_successful_sync_at` means catalog/order freshness (unused until 12B.2+).

### Canonical DB values (snake_case)

| Status | Meaning in 12B.1A |
| --- | --- |
| `not_connected` | No usable seller authorization (no row, or row with no secret) |
| `pending_authorization` | Reserved for 12B.1C (OAuth started) |
| `pending_validation` | Reserved for 12B.1C (secret stored, handshake not yet OK) |
| `connected` | Secret present; last validation succeeded — **not written by 12B.1A sandbox test** |
| `degraded` | Secret present; last test failed transiently |
| `revoked` | Disconnected; secret deleted |
| `error` | Permanent failure needing reauth |

Keep 12A.1 **test-result** statuses on `POST /connection/test` only: `CONNECTED` / `NOT_CONNECTED` / `FAILED`. They must not be the persisted enum.

### Allowed transitions

Full machine, for the column CHECK + later slices:

```text
not_connected
  → pending_authorization
pending_authorization
  → pending_validation | not_connected | error
pending_validation
  → connected | degraded | error | revoked
connected
  → degraded | error | pending_authorization | revoked
degraded
  → connected | error | revoked | pending_authorization
error
  → pending_authorization | revoked
revoked
  → pending_authorization
```

**12B.1A actually exercises:** insert/read `not_connected` (and fixture rows in other statuses for tests). Do not implement OAuth-driven transitions yet. A small transition helper can live in the service so later slices do not invent a second enum.

### Why not boolean

`is_connected=true` cannot express pending OAuth, degraded-but-secret-kept, revoked, or error-needs-reauth. It also invites treating connection as listing freshness.

---

## 4. Backend Implementation Plan

**Naming:** SQLAlchemy `AmazonConnection` vs existing Pydantic `AmazonConnectionOverview` / `AmazonConnectionTestResult`. Keep both. Do not reuse the Pydantic name for the table.

### Model

DB representation only. No LWA calls. No Copilot.

### Repository

Org-scoped only:

- `get(organization_id, provider="SP_API", environment="SANDBOX") -> AmazonConnection | None`
- `get_by_id(organization_id, id)` — other-org → `None`
- `upsert(...)` — unique key `(org, provider, environment)`
- never query by `token_reference` from caller input

### Service

Lifecycle + overlay:

- `overview()`: if row exists → sanitized overlay; else current 12A.1 env view (`connection_status=not_connected`)
- `test_sp_api()`: **unchanged sandbox/.env path** in 12B.1A
- must **not** set persisted `status=connected` from sandbox env success
- logs: connection id + status only

### Files to create

- `apps/api/migrations/versions/0007_amazon_connections.py`

### Files to modify

- `apps/api/app/persistence/models.py`
- `apps/api/app/persistence/repositories.py`
- `apps/api/app/amazon/connection.py`
- `apps/api/app/api/routes/amazon_connection.py` (same URLs; richer GET)
- `apps/api/tests/test_amazon_connection.py` (and a focused persistence test module if the file gets large)
- `apps/web/src/lib/types.ts`
- `apps/web/src/components/amazon-connection.tsx`
- `apps/web/src/components/amazon-connection-ui.test.tsx`

### Do not modify

Copilot, Skills, Rainforest, profit/ads engines, LWA, sandbox client behavior, `.env` secrets.

Session pattern: same as profit/ads — `session_scope()` inside the service when `persistence_enabled()`, not FastAPI `Depends` DB injection.

---

## 5. API Design

Keep:

- `GET /api/v1/amazon/connection` — no Amazon call
- `POST /api/v1/amazon/connection/test` — sandbox connectivity as today

No new OAuth/disconnect routes.

### GET response (sanitized, extra-forbid)

Keep 12A.1 fields, add persisted overlay:

```text
{
  status: "CONNECTED" | "NOT_CONNECTED" | "FAILED",
  connection_status: "not_connected" | "pending_authorization" | ...,
  persisted: boolean,
  provider: "SP_API",
  environment: "SANDBOX" | "PRODUCTION",
  region: "eu" | "na" | "fe",
  marketplace: string,
  application: string,
  credentials_configured: boolean,
  selling_partner_id: string | null,
  last_test_at: string | null,
  last_successful_validation_at: string | null,
  last_successful_sync_at: null,
  last_error_code: string | null,
  organization_id: string,
  ads_api: { provider: "ADS_API", status: "NOT_CONNECTED" }
}
```

- `status` is display/compat, not the DB enum.
- `selling_partner_id` is allowed: it is not a secret.
- `last_test_at` maps from `last_successful_validation_at`.
- `last_successful_sync_at` is always null in 12B.1.

When **no row:** `persisted=false`, `connection_status="not_connected"`, `status="NOT_CONNECTED"`, same env overview as 12A.1.

When **row exists:** `persisted=true`, `connection_status` from DB. `status` stays a **display** mapping (for example DB `not_connected`/`revoked` → `NOT_CONNECTED`; do not map DB `connected` until a secret exists in 12B.1D). In 12B.1A, the expected row is `not_connected`.

### POST /test response

Unchanged 12A.1 shape. Still never Amazon payloads.

### Never return

Refresh/access tokens, client secrets, `token_reference`, LWA bodies, `x-amz-access-token`, raw Sellers payload.

`public_model_dump` on every response. Do not put `token_reference` on public models.

---

## 6. Frontend Impact

No Connect Amazon, no consent redirect, no token paste.

Update `/connection` to show persisted metadata when `persisted=true`:

- Provider, environment, region
- `connection_status` (human labels: Not connected, Pending authorization, …)
- Last successful validation (`last_successful_validation_at` / existing “Last connection test”)
- Do **not** show “data is current” or a sync timestamp (`last_successful_sync_at` is null)
- Keep Test Connection as sandbox proof
- Ads panel stays Not connected

Page-local overlay of last POST test can remain; after reload, GET is source of persisted state (which will still be `not_connected` in 12B.1A).

---

## 7. Security Review

| Threat | Risk | Mitigation |
| --- | --- | --- |
| Cross-tenant read/write | High | Every query includes `organization_id`; other-org get returns `None` |
| Token/secret columns | Critical | No token columns; only nullable `token_reference` |
| `token_reference` in JSON | High | Omit from public models; sanitizer rejects `token*` keys |
| Frontend credential leak | High | Same public models; no secret HTTP API |
| Logs printing tokens | High | Log connection id + status only |
| GET calling Amazon | Medium | GET is DB/env overlay only |
| Sandbox env token treated as seller OAuth | High | POST /test does not persist `connected` |
| Production vs sandbox mix-up | High | Unique on `(org, provider, environment)`; separate rows |
| Copilot/Skills loading the row | Medium | No imports from `app.amazon` into Copilot/Skills |
| Org delete cascading connections | Medium | `ON DELETE RESTRICT` |
| Guessable secret via reference | Medium later | Opaque UUID when 12B.1B writes it; never HTTP-resolvable |

---

## 8. Test Plan

### Database

- Org A row is invisible to org B
- Unique `(organization_id, provider, environment)` raises on duplicate
- `token_reference` may be set in DB in tests and is still absent from API JSON
- Status CHECK / allowed values
- No `refresh_token` / `access_token` / `client_secret` columns

### Repository

- `get` scoped by org
- Missing connection → `None` (GET falls back to 12A.1)
- Upsert updates the same row

### API

- GET never calls Amazon
- GET with no row matches 12A.1 behavior (`persisted=false`)
- GET with row returns `connection_status`, never secrets
- POST /test still sandbox-mocked; does not flip DB to `connected`

### Security

- `public_model_dump` rejects a payload that includes `token_reference`
- Response text has no `Atza|` / `Atzr|` / `client_secret`

### Regression

- `test_sp_api_sandbox.py` unchanged
- Existing `test_amazon_connection.py` still pass
- Frontend vitest updated for new optional fields; no OAuth UI assertions

No live Amazon. `conftest` continues to clear SP-API env.

---

## 9. Migration Plan

**Current head:** `0006_advertising_models`  
**Next:** `0007_amazon_connections`  
**Revises:** `0006_advertising_models`

**Additive:** new table + indexes + unique constraint only. No alter of listing, profit, ads, copilot, or organizations columns. No data backfill required.

SQLite pytest: new SQLAlchemy model is created by existing `Base.metadata.create_all`.

**Downgrade:** drop indexes, drop `amazon_connections`.

**Conflict check:** none with 0006. Name `amazon_connections` is unused.

---

## 10. Implementation Sequence

### 12B.1A.1 — Database model + migration

**Objective:** Create `amazon_connections` with the credential boundary.  
**Files:** `models.py`, `0007_amazon_connections.py`  
**Dependencies:** none  
**Exit:** migration upgrades; model has no secret columns; unique + FK exist

### 12B.1A.2 — Repository

**Objective:** Org-scoped get/upsert; other-org miss.  
**Files:** `repositories.py`  
**Dependencies:** 12B.1A.1  
**Exit:** unit tests for isolation and uniqueness

### 12B.1A.3 — Service overlay

**Objective:** GET uses row if present, else 12A.1 env view. POST /test unchanged.  
**Files:** `connection.py`  
**Dependencies:** 12B.1A.2  
**Exit:** no Amazon on GET; sandbox test does not persist `connected`

### 12B.1A.4 — API integration

**Objective:** Same URLs; richer sanitized GET; `public_model_dump`.  
**Files:** `amazon_connection.py`, public Pydantic fields  
**Dependencies:** 12B.1A.3  
**Exit:** no `token_reference` in JSON

### 12B.1A.5 — Frontend connection state

**Objective:** Show persisted status/environment/provider/last validation. No OAuth.  
**Files:** `types.ts`, `amazon-connection.tsx`, UI tests  
**Dependencies:** 12B.1A.4  
**Exit:** UI distinguishes persisted `not_connected` vs in-page test `CONNECTED`

### 12B.1A.6 — Tests and validation

**Objective:** Persistence + sanitizer + 12A regression.  
**Files:** API + web tests  
**Dependencies:** 12B.1A.1–5  
**Exit:** pytest + vitest green; no live Amazon

---

## 11. Risks

1. **Status dual vocabulary** — 12A test result vs DB state machine. Must keep both explicit in API.
2. **Sanitizer vs `token_reference`** — never serialize that field publicly.
3. **False `connected`** — env sandbox success is not seller authorization.
4. **Tenancy still default-org** — repository must still be org-scoped so 12B.1C is safe.
5. **Uncommitted freeze note** — `docs/checkpoints/amazon-api-foundation-v1-git-freeze.md` is unrelated and must not be mixed into 12B.1A implementation.

No architecture blockers. SecretProvider/OAuth are correctly **out of scope**.

---

## 12. Final Recommendation

**Approve 12B.1A as specified.** Implement persistence + sanitized GET overlay only.

Do **not** start 12B.1B (SecretProvider), OAuth, ingest, Copilot, or Skills.

Wait for explicit approval before writing code.

**Recommended first slice after approval:** 12B.1A.1 — Database model + migration `0007_amazon_connections`.

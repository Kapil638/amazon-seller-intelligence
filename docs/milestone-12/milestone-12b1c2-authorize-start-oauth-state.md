# Milestone 12B.1C.2 — Authorize Start + OAuth State

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before **12B.1C.3**.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A**, **12B.1B.1–12B.1B.5**, and **12B.1C.1** architecture  
**Architecture:** [milestone-12b1c-amazon-seller-authorization-architecture.md](milestone-12b1c-amazon-seller-authorization-architecture.md)  
**Prior:** [milestone-12b1b5-production-secret-provider-preparation.md](milestone-12b1b5-production-secret-provider-preparation.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice implements seller authorization **initiation** only: hashed OAuth state and a Seller Central consent URL. No callback, Login with Amazon handling, authorization-code exchange, LWA token calls, SecretProvider writes, `token_reference` bind, seller validation, frontend Connect button, SP-API client, Copilot, or Skills changes.

Amazon docs used at coding time: [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow). Consent query parameters were not invented.

---

## Implementation Summary

`POST /api/v1/amazon/connection/authorize` starts Connect Amazon:

```text
Seller
  → ASI authorize endpoint
  → create/ensure SP-API connection
  → generate OAuth state (raw → hash)
  → persist state_hash bound to org + connection
  → set status pending_authorization
  → return { authorization_url }
  → (frontend navigate — 12B.1C.3)
```

Organization is ASI context (`current_organization_id()`), never Amazon `selling_partner_id`. The endpoint returns JSON; it does not HTTP-redirect.

---

## OAuth State Design

| Rule | Behaviour |
| --- | --- |
| Entropy | `secrets.token_urlsafe(32)` — 256 bits, URL-safe |
| Persist | SHA-256 hex of raw state only (`state_hash`, 64 chars, globally unique) |
| Return | Raw state only as Amazon’s `state` query param on `authorization_url` |
| Binding | `organization_id`, `provider`, `environment`, `connection_id`, `expires_at` |
| TTL | `sp_api_oauth_state_ttl_seconds` (default 600) |
| Replay | `consumed_at` column exists; unused until callback |
| Login URI | nullable `amazon_state`; unused this slice |
| Never stored | raw state, refresh/access tokens, authorization codes, client secrets |

Lookup is org-scoped. The repository refuses to bind another organization’s `connection_id`. Usable lookup (`get_usable_by_hash`) returns nothing when expired or consumed.

---

## Files Changed

**New**

- `apps/api/app/amazon/oauth.py` — state generate/hash, expiry helpers, consent URL builder
- `apps/api/migrations/versions/0008_amazon_oauth_states.py`
- `apps/api/tests/test_amazon_oauth_state.py`
- `apps/api/tests/test_amazon_oauth_authorize.py`
- `docs/milestone-12/milestone-12b1c2-authorize-start-oauth-state.md` — this file

**Updated**

- `apps/api/app/persistence/models.py` — `AmazonOAuthState`
- `apps/api/app/persistence/repositories.py` — `AmazonOAuthStateRepository`
- `apps/api/app/amazon/connection.py` — `start_authorization()`, `AmazonAuthorizationStart`
- `apps/api/app/api/routes/amazon_connection.py` — `POST /connection/authorize`
- `apps/api/app/amazon/common.py` — public JSON allowlist for `authorization_url`
- `apps/api/app/core/config.py` — application id, consent origin, redirect URI, TTL, draft beta flag
- `apps/api/.env.example` — names only
- `docs/milestone-12/README.md` — index this slice

Unchanged: SecretProvider implementations, SP-API client, frontend, Copilot, Skills.

---

## Database Changes

Alembic revision `0008_amazon_oauth_states` revises `0007_amazon_connections`. Additive only. No changes to secret fields on `amazon_connections`.

### `amazon_oauth_states`

| Column | Role |
| --- | --- |
| `id` | UUID PK |
| `organization_id` | FK `organizations.id` `ON DELETE RESTRICT` |
| `provider` | `SP_API` (CHECK) |
| `environment` | `SANDBOX` / `PRODUCTION` (CHECK) |
| `connection_id` | FK `amazon_connections.id` `ON DELETE RESTRICT` (required this slice) |
| `state_hash` | unique SHA-256 hex; never log raw state |
| `amazon_state` | nullable; Login URI round-trip later |
| `expires_at` | timezone-aware TTL |
| `consumed_at` | nullable; replay protection later |
| `created_at` | server default |

Indexes: org, connection, `expires_at`. Unique: `state_hash`.

SQLite tests create the table via `Base.metadata.create_all`.

---

## Authorize Endpoint Behaviour

| Method | Path | Body |
| --- | --- | --- |
| POST | `/api/v1/amazon/connection/authorize` | `{}` or `{ "environment": "SANDBOX" \| "PRODUCTION" }` (`extra=forbid`) |

Organization is **not** accepted from the client. Default environment is `SANDBOX` to match current GET `/connection`.

**Flow**

1. Require persistence. Missing `DATABASE_URL` → 503.
2. Require `sp_api_application_id`. Missing → 503 (`SpApiConfigurationError`). Message does not include state or secrets.
3. Generate raw state + hash; compute `expires_at`.
4. Build consent URL (raw state in query only).
5. Get or create SP-API connection for current org + provider + environment.
6. Set status `pending_authorization`.
7. Persist `state_hash` + binding fields.
8. Log `connection_id` and status only.
9. Return JSON. `public_model_dump` runs before respond.

**Response** (`AmazonAuthorizationStart`)

- `authorization_url`
- `expires_at`
- `connection_status` (`pending_authorization`)
- `provider`, `environment`, `organization_id`

No top-level `state` or `state_hash`. No HTTP redirect.

**Status machine (this slice)**

| Before | After |
| --- | --- |
| `not_connected` (or missing row) | `pending_authorization` |
| existing non-pending row | `pending_authorization` |

Never set `connected` or `pending_validation`. Coarse GET display `status` remains `NOT_CONNECTED`.

---

## Authorization URL Generation

Documented Website Authorization consent URI:

```text
{origin}/apps/authorize/consent?application_id={id}&state={raw}[&version=beta]
```

Origin resolution (not a hardcoded global Seller Central host in new logic):

1. `sp_api_oauth_consent_base_url` if set
2. Marketplace map (`amazon.in` → `https://sellercentral.amazon.in`)
3. Region map (`na` / `eu` / `fe`) if marketplace is unknown
4. Else configuration error

`version=beta` is included when `sp_api_consent_version_beta` is true (default; draft apps).

`sp_api_oauth_redirect_uri` is stored in settings for **12B.1C.4**. It is **not** added to this URI. Amazon documents `redirect_uri` on the later Amazon callback / LWA exchange, not on the website consent URL.

---

## Configuration

| Setting | Default | Meaning |
| --- | --- | --- |
| `sp_api_application_id` | empty | Public application id on the consent URL. Not a secret. Required to authorize. |
| `sp_api_oauth_consent_base_url` | empty | Optional origin override |
| `sp_api_oauth_redirect_uri` | empty | Registered redirect URI; unused on consent URL this slice |
| `sp_api_oauth_state_ttl_seconds` | `600` | State expiry |
| `sp_api_consent_version_beta` | `true` | Add `version=beta` for draft apps |

No LWA client secret, refresh token, or SecretProvider settings are required for authorize start.

---

## Security Validation

- Raw OAuth state is returned only inside `authorization_url`.
- Raw state is never persisted, never logged.
- Stored copy is `state_hash` only.
- No `put_secret`, no LWA, no SP-API HTTP, no authorization codes.
- Errors do not include `client_secret`, credentials, or state values.
- `public_model_dump` still rejects `authorization_code` and other secret-shaped keys. `authorization_url` is an explicit public allowlist exception (the key contains the fragment `authorization`).
- Cross-org connection ids cannot be attached to another org’s OAuth state row.

---

## Tests Added

`apps/api/tests/test_amazon_oauth_state.py`

- Table registration; hash stored, raw absent
- Organization isolation
- Expiry / `get_usable_by_hash`
- No secret columns; migration additive
- Repository rejects raw `state` and token fields
- Unique `state_hash`
- Wrong org cannot bind another org’s connection

`apps/api/tests/test_amazon_oauth_authorize.py`

- Marketplace-driven consent URL contains `state`
- Authorize creates hashed state, not raw
- HTTP endpoint returns `authorization_url`; extra credential fields → 400
- Status is `pending_authorization`, not `connected` / `pending_validation`
- Other-org connection is unused
- No secrets stored; no httpx / SP-API client construction
- Raw state not logged
- Missing application id fails closed without leaking state
- `oauth.py` does not import httpx, LWA, sandbox, or Copilot

---

## Test Results

`uv run python -m pytest` from `apps/api`: **586 passed**.

Amazon connection, SecretProvider, SP-API sandbox, and new OAuth tests all passed. No live Amazon calls.

---

## Concerns

- Unconsumed / expired state rows are not purged yet (callback slice should set `consumed_at`; TTL cleanup can follow).
- Region fallback for unknown marketplace + `eu` is UK Seller Central. India V1 is correct via the `amazon.in` marketplace map.
- Redirect URI is settings-only until 12B.1C.4.
- Frontend still has no Connect Amazon button.

---

## Explicit Confirmation

**Only Milestone 12B.1C.2 Authorize Start + OAuth State implemented. No callback handling, token exchange, SecretProvider writes, seller authorization completion, frontend, SP-API changes, or Copilot changes added.**

---

## Next slice (not started)

**12B.1C.3 — Frontend Connect Amazon**

Connect button → authorize → navigate to Amazon. Query `amazon=` flash messages. Still no token paste.

Wait for explicit approval before implementing.

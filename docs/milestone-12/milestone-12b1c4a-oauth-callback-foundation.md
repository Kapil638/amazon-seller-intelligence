# Milestone 12B.1C.4A — Amazon OAuth Callback Foundation

**Date:** 24 August 2026  
**Status:** Implemented. Waiting for approval before **12B.1C.5**.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus **12B.1A**, **12B.1B.1–5**, **12B.1C.2**, **12B.1C.3**  
**Architecture:** [milestone-12b1c-amazon-seller-authorization-architecture.md](milestone-12b1c-amazon-seller-authorization-architecture.md)  
**Prior:** [milestone-12b1c2-authorize-start-oauth-state.md](milestone-12b1c2-authorize-start-oauth-state.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice receives Amazon’s Redirect URI callback and validates hashed OAuth state. It does **not** exchange `spapi_oauth_code`, call LWA, write SecretProvider, bind `token_reference`, set `connected` or `pending_validation`, call SP-API, or change Copilot, Skills, or the Connect Amazon UI.

Amazon docs used: [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow) redirect-URI parameters `state`, `selling_partner_id`, `spapi_oauth_code`. OAuth `error` / `error_description` and `code` are accepted. Login URI is **not** implemented (remainder of 12B.1C.4).

---

## Implementation Summary

ASI can now **receive** Amazon’s OAuth redirect, **validate hashed state**, **consume it once**, and **redirect the browser** to `/connection?amazon=success|denied|error`.

Internal result on success:

```text
AuthorizationCodeReceived
  connection_id
  organization_id
  authorization_code_present=true
```

The authorization code is wrapped as `SecretStr`, presence is recorded, then the value is discarded. It is never persisted, never logged, and never placed on the frontend Location URL.

Connection status stays `pending_authorization`. Architecture `pending_validation` still means “refresh token stored” (12B.1C.6). This slice does not set `connected`.

---

## Current flow (before this slice)

```text
Seller
  → POST /api/v1/amazon/connection/authorize
  → hashed state persisted; connection pending_authorization
  → browser to Seller Central consent
  → (no ASI intake)
```

## Required callback additions

```text
Amazon Redirect URI
  → GET /api/v1/amazon/connection/callback
  → hash state, org-scoped lookup, connection bind
  → reject missing / expired / consumed / wrong-org
  → access_denied: consume state, stay pending_authorization
  → code present: consume state, AuthorizationCodeReceived, drop code
  → 302 /connection?amazon=success|denied|error
  → (12B.1C.5 exchanges the code in the same request)
```

---

## Callback Flow

```text
POST /connection/authorize  →  pending_authorization + hashed state
        ↓
Seller Central consent
        ↓
GET /api/v1/amazon/connection/callback
  ?state=
  &spapi_oauth_code=   (or code=)
  &error=              (optional)
  &error_description=  (accepted, never logged)
  &selling_partner_id= (ignored for tenancy)
        ↓
Validate + consume state
        ↓
302 {first CORS origin}/connection?amazon=success|denied|error
Referrer-Policy: no-referrer
```

| Parameter | Use |
| --- | --- |
| `state` | Hash and match `amazon_oauth_states.state_hash` |
| `spapi_oauth_code` | Presence only; wrapped as `SecretStr` then discarded |
| `code` | Alias for the LWA token-exchange field name |
| `error` | `access_denied` → denial path |
| `error_description` | Accepted; never logged or redirected |
| `selling_partner_id` | Accepted; **not** used for tenancy; not persisted this slice |

---

## State Validation Design

1. Raw `state` must be present.  
2. SHA-256 hex must match stored `state_hash`.  
3. Lookup is **organization-scoped** (`current_organization_id()`). Another org’s hash is treated as missing / invalid.  
4. Row `connection_id` must belong to that organization.  
5. `expires_at` must be in the future.  
6. `consumed_at` must be null. Success and `access_denied` set `consumed_at` once; replay fails.

Raw state is never stored. Only the hash was persisted in 12B.1C.2.

---

## Error Handling

| Case | Redirect `amazon=` | Connection |
| --- | --- | --- |
| Valid code | `success` | remains `pending_authorization` |
| `error=access_denied` | `denied` | remains `pending_authorization`; `last_error_code=access_denied` |
| Invalid / missing state | `error` | unchanged if no matching row |
| Expired | `error` | `last_error_code=oauth_state_expired` |
| Replay | `error` | `last_error_code=oauth_state_consumed` |

HTTP 302 with `Referrer-Policy: no-referrer`. Location contains no code, raw state, or selling partner id. Persistence-down is still 503 JSON.

---

## Security Validation

| Rule | Behaviour |
| --- | --- |
| Authorization code | Memory-only `SecretStr`; discarded; not in DB, logs, GET `/connection`, or Location |
| Raw OAuth state | Never stored; never logged |
| Tenancy | From hashed state row + ASI org; not from `selling_partner_id` |
| Errors | No client secret, code, refresh token, or access token |
| Public JSON | `authorization_code_present` is a boolean allowlisted flag, not the code |
| Repository | Connection updates reject `authorization_code` / `spapi_oauth_code` |

---

## Files Changed

**New**

- `apps/api/app/amazon/oauth_callback.py` — result model, code wrap, denial helper, frontend return URL
- `apps/api/tests/test_amazon_oauth_callback.py`
- `docs/milestone-12/milestone-12b1c4a-oauth-callback-foundation.md` — this file

**Updated**

- `apps/api/app/amazon/connection.py` — `complete_authorization_callback()`
- `apps/api/app/api/routes/amazon_connection.py` — `GET /connection/callback`
- `apps/api/app/persistence/repositories.py` — `classify`, `consume`; reject code fields on connection updates
- `apps/api/app/amazon/common.py` — allowlist `authorization_code_present`
- `apps/api/app/amazon/oauth.py` — docstring (callback lives in `oauth_callback.py`)
- `apps/api/.env.example` — callback path note
- `docs/milestone-12/README.md` — index this slice

Unchanged: SecretProvider, SP-API client, LWA refresh-token grant, frontend Connect flow, Copilot, Skills. No new Alembic revision (consume uses existing `consumed_at`).

---

## Tests Added

`tests/test_amazon_oauth_callback.py`:

1. Valid callback state succeeds (`spapi_oauth_code`).  
2. Invalid state rejected.  
3. Expired state rejected.  
4. Consumed state rejected (replay).  
5. Wrong organization rejected.  
6. Amazon `access_denied` handled.  
7. Authorization code is not logged.  
8. No token exchange (no HTTP, no `grant_type`, no LWA URL).  
9. No SecretProvider `put_secret`.  
10. HTTP 302 without code on Location; GET `/connection` still `pending_authorization` / display `NOT_CONNECTED`.  

Also covers the `code=` alias.

---

## Test Results

`uv run pytest` in `apps/api` after this slice: **598 passed**.

Includes OAuth authorize/state/callback, Amazon connection, SecretProvider, and SP-API sandbox tests.

---

## Concerns

- Amazon will not complete a live redirect until the **Draft** app’s OAuth Redirect URI in Solution Provider Portal matches this callback **exactly**. Amazon rejects `localhost`. HTTPS (or a public tunnel) is required for a real Seller Central round-trip.  
- Website **Login URI** is still not implemented; some Amazon flows hit Login URI before Redirect URI.  
- Process-level access logs (uvicorn) can still include the full callback query string; application logs do not.  
- The code is **not** kept for 12B.1C.5. Token exchange must happen in the same callback request in that slice, or the ~5-minute Amazon code is gone.

---

## Explicit Confirmation

Only Milestone 12B.1C.4A OAuth Callback Foundation implemented. No token exchange, refresh token handling, SecretProvider writes, seller validation, SP-API changes, or Copilot changes added.

Wait for approval before implementing 12B.1C.5.

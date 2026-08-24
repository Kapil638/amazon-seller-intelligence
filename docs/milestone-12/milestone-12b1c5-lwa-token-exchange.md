# Milestone 12B.1C.5 — LWA Token Exchange + SecretProvider Storage

**Date:** 24 August 2026  
**Status:** Implemented. Waiting for approval before **12B.1D**.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus **12B.1A**, **12B.1B.1–5**, **12B.1C.2**, **12B.1C.3**, **12B.1C.4A**  
**Architecture:** [milestone-12b1c-amazon-seller-authorization-architecture.md](milestone-12b1c-amazon-seller-authorization-architecture.md)  
**Prior:** [milestone-12b1c4a-oauth-callback-foundation.md](milestone-12b1c4a-oauth-callback-foundation.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice exchanges Amazon’s authorization code for LWA tokens in the **same callback request**, stores **only** the refresh token through SecretProvider, binds an opaque `token_reference`, and moves the connection to `pending_validation`. It does **not** call SP-API, validate seller identity, set `connected`, ingest listings/orders/inventory, or change Copilot, Skills, or the frontend.

Amazon docs used: [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow) token exchange (`grant_type=authorization_code`). The existing `LwaClient` refresh-token grant (sandbox Test Connection) is unchanged.

---

## Implementation Summary

After 12B.1C.4A validates hashed OAuth state, the callback now exchanges the short-lived authorization code before it expires:

```text
Amazon Redirect URI
  → GET /api/v1/amazon/connection/callback
  → validate + consume state (12B.1C.4A)
  → AmazonLwaTokenService.exchange_authorization_code(SecretStr)
  → access_token (memory only) + refresh_token (SecretStr)
  → SecretProvider.put_secret(ASI pointer, refresh_token)
  → amazon_connections.token_reference
  → status pending_validation
  → 302 /connection?amazon=success
```

Internal success result:

```text
AuthorizationCodeReceived
  outcome=token_stored
  connection_status=pending_validation
  authorization_code_present=true
```

The HTTP route still returns 302 with `Referrer-Policy: no-referrer`. GET `/connection` display `status` remains `NOT_CONNECTED`. Lifecycle is `pending_validation`, not `connected`. Seller validation is **12B.1D**.

---

## Current flow (before this slice)

```text
Amazon
  → GET /api/v1/amazon/connection/callback
  → validate hashed OAuth state
  → AuthorizationCodeReceived
  → drop authorization code
  → stay pending_authorization
  → (no LWA, no SecretProvider, no token_reference)
```

## Required additions

```text
callback
  → validate state
  → exchange authorization code (same request)
  → store refresh token via SecretProvider.put_secret()
  → create token_reference
  → update connection → pending_validation
  → redirect success
```

Do not duplicate 12B.1C.4A state validation. Denial, expiry, replay, and wrong-org paths still do not call LWA or `put_secret`.

---

## LWA Exchange Flow

```text
authorization_code (SecretStr, request memory)
        ↓
POST {SP_API_LWA_TOKEN_URL}
  grant_type=authorization_code
  code=
  redirect_uri={SP_API_OAUTH_REDIRECT_URI}
  client_id=
  client_secret=
        ↓
LwaAuthorizationGrant
  access_token   SecretStr  (dropped after extract)
  refresh_token  SecretStr  (put_secret only)
  token_type
  expires_in
        ↓
SecretProvider.put_secret(token_reference, refresh_token)
        ↓
amazon_connections.token_reference
        ↓
pending_validation
```

Dedicated service: `AmazonLwaTokenService` in `apps/api/app/amazon/lwa_token.py`.

Application credentials are configuration-driven. Production/Draft `SP_API_PRODUCTION_LWA_CLIENT_ID` / `SP_API_PRODUCTION_LWA_CLIENT_SECRET` win when both are set; otherwise the sandbox LWA pair is used. Token URL and redirect URI come from settings. Client id, client secret, and endpoints are not hardcoded in the exchange service.

`LwaClient` remains refresh-token grant only (sandbox Test Connection).

The authorization code is short-lived (~5 minutes). Exchange happens in the same callback request; the code is not persisted for a later slice.

---

## Token Security Design

| Material | Where it may exist | Where it must not |
| --- | --- | --- |
| Authorization code | Request memory as `SecretStr` until LWA returns | Logs, DB, Location, GET `/connection` |
| Access token | Grant object in memory, then dropped | SecretProvider, DB, logs, API |
| Refresh token | SecretProvider via `put_secret` | DB columns, logs, API, Location |
| Client secret | Process settings; LWA form body | Logs, DB, API |
| `token_reference` | `amazon_connections.token_reference` | API JSON, Location |

If `put_secret` succeeds and DB bind fails, `delete_secret` runs (orphan cleanup). LWA and storage failures do not leave a bound reference. Connection stays `pending_authorization` with a safe `last_error_code`.

Tokens are never returned on the HTTP callback, GET `/connection`, or `AuthorizationCodeReceived`.

---

## SecretProvider Integration

```text
reference = asi/amazon/{provider}/{environment}/{organization_id}/{connection_id}
SecretProvider.put_secret(reference, refresh_token: SecretStr)
```

The pointer is built with `build_asi_secret_reference` (12B.1B.4). It is:

- organization scoped
- connection scoped
- opaque (no secrets embedded)
- max 128 characters

DevelopmentSecretProvider is the live backend. Production backend remains fail-closed.

---

## Database Changes

No new Alembic revision. `amazon_connections.token_reference` (VARCHAR 128) already existed from 12B.1A.

Database stores **only** `token_reference`.

Database must **not** store:

- `refresh_token`
- `access_token`
- `client_secret`
- authorization code

`AmazonConnectionRepository.update()` still **rejects** `token_reference` and token fields. New `bind_token_reference()` is the only writer. It parses the ASI pointer and requires organization, connection, provider, and environment to match.

---

## Connection State Changes

| Event | Lifecycle | Display `status` |
| --- | --- | --- |
| Authorize start | `pending_authorization` | `NOT_CONNECTED` |
| Callback + token stored | `pending_validation` | `NOT_CONNECTED` |
| LWA / secret / bind failure | stays `pending_authorization` | `NOT_CONNECTED` |
| Seller validation | **not this slice** | — |
| `connected` | **12B.1D** | — |

`authorized_at` is set when the refresh token is stored. `selling_partner_id` is still ignored and not persisted.

Do **not** move to `connected`. Seller validation happens separately in 12B.1D.

---

## Error Handling

| Case | Redirect `amazon=` | Connection | `last_error_code` |
| --- | --- | --- | --- |
| Token stored | `success` | `pending_validation` | cleared |
| `error=access_denied` | `denied` | stays `pending_authorization` | `access_denied` |
| Invalid / expired / consumed state | `error` | unchanged or stays `pending_authorization` | existing 12B.1C.4A codes |
| Invalid / expired authorization code | `error` | stays `pending_authorization` | `lwa_authentication` |
| Invalid client credentials | `error` | stays `pending_authorization` | `lwa_authentication` |
| Missing LWA / redirect config | `error` | stays `pending_authorization` | `lwa_configuration` |
| Amazon LWA unavailable / timeout / 5xx | `error` | stays `pending_authorization` | `lwa_unavailable` |
| SecretProvider `put_secret` failure | `error` | stays `pending_authorization` | `secret_storage_failed` |
| DB bind failure after `put_secret` | `error` | stays `pending_authorization`; secret deleted | `token_bind_failed` |

Errors never expose client secret, authorization code, refresh token, or access token.

HTTP 302 with `Referrer-Policy: no-referrer`. Location contains no code, tokens, raw state, selling partner id, or `token_reference`. Persistence-down before intake is still 503 JSON.

---

## Security Validation

| Rule | Behaviour |
| --- | --- |
| Authorization code | Memory-only `SecretStr`; discarded after exchange; not in DB, logs, GET `/connection`, or Location |
| Refresh token | SecretProvider only; `SecretStr` in and out |
| Access token | Memory only; never `put_secret`; never persisted |
| Database | Opaque `token_reference` only |
| Tenancy | From hashed state row + ASI org; not from `selling_partner_id` |
| Errors | Safe codes only; no secrets in messages or logs |
| Public JSON | Still omits `token_reference`; display `status` stays `NOT_CONNECTED` |
| Repository | `update()` rejects token fields; bind validates ASI pointer match |

---

## Files Changed

**New**

- `apps/api/app/amazon/lwa_token.py` — `AmazonLwaTokenService`, config-driven credentials
- `apps/api/tests/test_amazon_lwa_token_exchange.py`
- `docs/milestone-12/milestone-12b1c5-lwa-token-exchange.md` — this file

**Updated**

- `apps/api/app/amazon/connection.py` — exchange + `put_secret` + bind in the same callback request
- `apps/api/app/amazon/lwa.py` — shared LWA status/JSON helpers; refresh-token grant unchanged
- `apps/api/app/amazon/models.py` — `LwaAuthorizationGrant`
- `apps/api/app/amazon/oauth_callback.py` — `token_stored` outcome (still no LWA)
- `apps/api/app/persistence/repositories.py` — `bind_token_reference`
- `apps/api/tests/test_amazon_oauth_callback.py` — success now includes exchange + storage
- `apps/api/tests/test_amazon_connection_repository.py` — bind tests
- `apps/api/.env.example` — redirect URI is used on LWA exchange
- `docs/milestone-12/README.md` — index this slice

Unchanged: SP-API Sellers client, sandbox Test Connection, frontend Connect Amazon UI, Copilot, Skills. No seller identity validation. No new Alembic revision.

---

## Tests Added

`tests/test_amazon_lwa_token_exchange.py`:

1. Successful authorization-code form: `grant_type`, `code`, `redirect_uri`, `client_id`; no SP-API URL.  
2. Tokens absent from repr/JSON.  
3. Invalid code → authentication error without secrets in the message.  
4. LWA 5xx / timeout / malformed handled.  
5. Missing credentials / redirect URI.  
6. Production LWA pair preferred when configured.

`tests/test_amazon_oauth_callback.py` (updated):

1. Successful token exchange flow.  
2. LWA API mocked correctly (`grant_type=authorization_code`).  
3. Refresh token stored through SecretProvider.  
4. `token_reference` persisted (ASI format, no `Atzr|` / `Atza|`).  
5. Refresh token never appears in logs.  
6. Access token never persisted.  
7. Invalid authorization code handled.  
8. Amazon API failure handled.  
9. SecretProvider failure handled; bind failure rolls back with `delete_secret`.  
10. Connection moves `pending_authorization` → `pending_validation`.  
11. Connection does **not** become `connected`.  
12. No SP-API calls (`_BoomChecker` + URL assertions).  
13. Denial / invalid / expired / wrong-org still do not exchange.  
14. HTTP 302 without secrets; GET `/connection` omits `token_reference`.

`tests/test_amazon_connection_repository.py`:

- `bind_token_reference` accepts a matching ASI pointer and still rejects `update(..., token_reference=)`.

---

## Test Results

`uv run pytest` in `apps/api` after this slice: **610 passed**.

Includes OAuth authorize/state/callback, Amazon connection, SecretProvider, LWA authorization-code exchange, and SP-API sandbox tests.

---

## Concerns

- Live Amazon exchange still requires the Draft app Redirect URI in Solution Provider Portal to match `SP_API_OAUTH_REDIRECT_URI` exactly. Amazon rejects `localhost`. HTTPS (or a public tunnel) is required.  
- Website **Login URI** is still not implemented; some Amazon flows hit Login URI before Redirect URI.  
- Process-level access logs (uvicorn) can still include the full callback query string; application logs do not.  
- After a successful consume, a later LWA failure burns OAuth state; the seller must start Connect Amazon again. The authorization code is also single-use at Amazon.  
- Sandbox Test Connection still uses the development sandbox env refresh token, not this seller grant. Wiring `pending_validation` connections into SP-API is **12B.1D**.

---

## Explicit Confirmation

Only Milestone 12B.1C.5 LWA Token Exchange + SecretProvider Storage implemented. No seller validation, no SP-API calls, no ingestion, no Copilot, and no frontend changes added.

Wait for approval before implementing 12B.1D.

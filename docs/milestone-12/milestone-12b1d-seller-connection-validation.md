# Milestone 12B.1D — Seller Connection Validation Using SP-API

**Date:** 24 August 2026  
**Status:** Implemented. Waiting for approval before **data ingestion (12B.2)**.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus **12B.1A**, **12B.1B.1–5**, **12B.1C.2–5**  
**Architecture:** [milestone-12b1c-amazon-seller-authorization-architecture.md](milestone-12b1c-amazon-seller-authorization-architecture.md)  
**Prior:** [milestone-12b1c5-lwa-token-exchange.md](milestone-12b1c5-lwa-token-exchange.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice proves an authorized Amazon seller grant is real and usable. It calls **only** `GET /sellers/v1/marketplaceParticipations`, then moves `pending_validation` → `connected`. It does **not** ingest listings, orders, inventory, reports, or analytics, and does not change Copilot, Skills, or the frontend.

---

## Implementation Summary

After 12B.1C.5 stores a refresh token behind `token_reference`, ASI can handshake the seller:

```text
amazon_connections (pending_validation + token_reference)
        ↓
AmazonConnectionSecretResolver (org + connection + pointer must match)
        ↓
SecretProvider.get_secret()
        ↓
LWA refresh_token grant (access token in memory)
        ↓
GET /sellers/v1/marketplaceParticipations
        ↓
validate participation
        ↓
connected + selling_partner_id + last_successful_validation_at
```

Trigger: existing `POST /api/v1/amazon/connection/test` when the org-scoped row has a stored `token_reference` and status is `pending_validation`, `connected`, or `degraded`.

Sandbox env-token Test Connection (12A.1) is unchanged when there is no seller grant. It still does **not** persist `connected`.

GET `/connection` still does not call Amazon. Display `status` stays `NOT_CONNECTED`; lifecycle `connection_status` becomes `connected`.

---

## Current validation gap

12B.1C.5 stopped at `pending_validation` with a SecretProvider refresh token. The sandbox client still resolved the **development env** token, not the seller grant. No Sellers handshake ran against `token_reference`. `connected` was never written.

## Required implementation

- Dedicated `AmazonSellerValidationService`
- Connection-scoped `AmazonSpApiSellersClient` (injected refresh token, no env bypass)
- Persist handshake outcome on `amazon_connections`
- Keep OAuth callback at token storage only (no SP-API in the redirect request)

---

## Seller Validation Flow

```text
POST /connection/test
        ↓
Load org-scoped amazon_connections row
        ↓
Require token_reference + pending_validation|connected|degraded
        ↓
SecretProvider.get_secret(token_reference)
        ↓
AmazonSpApiSellersClient
        ↓
GET {host}/sellers/v1/marketplaceParticipations
        ↓
SellerValidationResult
  valid
  selling_partner_id
  marketplaces[{marketplace_id, country_code}]
        ↓
Apply status on amazon_connections
```

OAuth callback is not changed: it still ends at `pending_validation`. The seller (or Test Connection) runs the handshake next. No frontend change is required; the existing Test Connection button calls this endpoint.

---

## SP-API Endpoint Used

| Item | Value |
| --- | --- |
| Operation | `getMarketplaceParticipations` |
| Path | `GET /sellers/v1/marketplaceParticipations` |
| Sandbox host | `https://sandbox.sellingpartnerapi-{region}.amazon.com` |
| Production host | `https://sellingpartnerapi-{region}.amazon.com` |

Host follows `amazon_connections.environment` and `region`. Optional overrides: `SP_API_SANDBOX_BASE_URL`, `SP_API_PRODUCTION_BASE_URL`.

Not called: listings, orders, inventory, reports, finances, Ads API.

---

## Credential Resolution Flow

```text
Application LWA client_id / client_secret
        ← Settings (production pair preferred, else sandbox pair)

Seller refresh token
        ← SecretProvider.get_secret(token_reference)
        ← AmazonConnectionSecretResolver
           (organization_id + connection_id + provider + environment)

Access token
        ← LWA grant_type=refresh_token
        ← memory only, dropped after the Sellers call
```

Do **not** read the seller refresh token from `SP_API_SANDBOX_REFRESH_TOKEN` for this handshake. Env fallback is refused when `token_reference` is missing.

Tenancy is `organization_id`. `selling_partner_id` is stored as identity metadata only.

---

## Connection State Changes

| Event | Lifecycle | Display `status` | Secret |
| --- | --- | --- | --- |
| Token stored (12B.1C.5) | `pending_validation` | `NOT_CONNECTED` | kept |
| Sellers 200 + participation | `connected` | `NOT_CONNECTED` | kept |
| SecretProvider failure | stays `pending_validation` | `NOT_CONNECTED` | kept |
| Invalid refresh / SP-API 401 | `error` (`requires_reauth`) | `NOT_CONNECTED` | deleted; pointer cleared |
| Amazon 5xx / timeout | `degraded` | `NOT_CONNECTED` | kept |
| No participating marketplace | stays `pending_validation` | `NOT_CONNECTED` | kept |
| Sandbox env-token test | unchanged | Test result `CONNECTED` | not a seller grant |

`authorized_at` is unchanged here (set at token storage). `last_successful_validation_at` is set on handshake success.

---

## Database Changes

No new table. No ingest models.

On success, `amazon_connections` updates:

- `status` = `connected`
- `selling_partner_id` when Amazon returns it
- `last_successful_validation_at`
- `last_error_code` / `last_error_at` cleared

Still never stored: refresh token, access token, client secret.

`update()` still rejects secret fields. Invalid grant uses `clear_token_reference()` after `delete_secret`.

Marketplace id / country code are returned on `SellerValidationResult` and are not a canonical marketplace table (12B.2).

---

## Security Validation

| Rule | Behaviour |
| --- | --- |
| Refresh token | SecretProvider only; `SecretStr` |
| Access token | Memory only; never persisted |
| `token_reference` | Opaque pointer; not in public JSON |
| Tenancy | `organization_id` + `connection_id` + pointer match; never `selling_partner_id` |
| Cross-org | Org A cannot resolve Org B’s connection or reference |
| Errors | Safe messages; no tokens or client secret |
| GET `/connection` | No Amazon HTTP |
| Ingest APIs | Not called |

---

## Files Changed

**New**

- `apps/api/app/amazon/sellers.py` — connection-scoped Sellers client + host resolver
- `apps/api/app/amazon/seller_validation.py` — `AmazonSellerValidationService`
- `apps/api/tests/test_amazon_seller_validation.py`
- `docs/milestone-12/milestone-12b1d-seller-connection-validation.md` — this file

**Updated**

- `apps/api/app/amazon/connection.py` — handshake on Test Connection; persist `connected`
- `apps/api/app/persistence/repositories.py` — `clear_token_reference`
- `apps/api/app/amazon/models.py` — optional `sellingPartnerId` on Sellers DTO
- `apps/api/app/core/config.py` — optional `SP_API_PRODUCTION_BASE_URL`
- `apps/api/.env.example`
- `apps/api/tests/test_amazon_connection_repository.py`
- `docs/milestone-12/README.md`

Unchanged: OAuth callback (still no SP-API), frontend Connect Amazon UI, Copilot, Skills, listings/orders/inventory/reports ingest.

---

## Tests Added

`tests/test_amazon_seller_validation.py`:

1. Successful validation → `connected` + `selling_partner_id`.  
2. Sellers path mocked; LWA + `GET /sellers/v1/marketplaceParticipations` only.  
3. Refresh token retrieved through SecretProvider.  
4. `pending_validation` → `connected`.  
5. Selling partner metadata stored; public JSON omits tokens / `token_reference`.  
6. Invalid refresh token → `error` / `requires_reauth`; secret deleted.  
7. SP-API 5xx → `degraded`; secret kept.  
8. SecretProvider failure → stays `pending_validation`.  
9. Tokens absent from logs and DB.  
10. No listings/orders/inventory/reports/finances calls.  
11. Org A cannot validate Org B’s token.  
12. Empty participation does not set `connected`.  
13. `POST /connection/test` handshake; GET overview `connection_status=connected`.

Existing OAuth, SecretProvider, Amazon connection, and SP-API sandbox tests remain.

---

## Test Results

`uv run pytest` in `apps/api` after this slice: **620 passed**.

---

## Concerns

- Amazon’s `getMarketplaceParticipations` payload may omit `sellingPartnerId`. Handshake still succeeds when participating marketplaces exist; SPID is stored only when present.  
- Website Login URI is still not implemented.  
- Handshake is not run inside the OAuth callback request (keeps the redirect short; Test Connection performs it).  
- Connect Amazon currently defaults to `SANDBOX` environment, so the handshake uses the sandbox Sellers host. A production-host handshake needs a `PRODUCTION` connection row.  
- Canonical marketplace / seller-account tables remain **12B.2**.

---

## Explicit Confirmation

Only Milestone 12B.1D Seller Connection Validation implemented. No listing ingestion, order ingestion, inventory ingestion, reports ingestion, Copilot, Skills, or frontend changes added.

Wait for approval before implementing data ingestion.

# Milestone 12B.1A.4 — Amazon Connection API Integration

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1A.5.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`)  
**Previous slices:**
- [12B.1A.1 — Connection Metadata Database](milestone-12b1a1-amazon-connection-metadata-database.md)
- [12B.1A.2 — Repository Layer](milestone-12b1a2-amazon-connection-repository.md)
- [12B.1A.3 — Service Overlay](milestone-12b1a3-amazon-connection-service-overlay.md)  
**Architecture:** [milestone-12b1a-connection-metadata-persistence.md](milestone-12b1a-connection-metadata-persistence.md)

This slice exposed the approved service layer through existing HTTP routes. No OAuth, SecretProvider, ingest, frontend, or Copilot changes.

---

## Implementation Summary

Existing `GET /api/v1/amazon/connection` and `POST /api/v1/amazon/connection/test` go through `AmazonConnectionService` only. GET returns additive persisted overlay fields. POST remains a sandbox test and does not persist `connected`. Routes do not touch the database, Amazon clients, or secrets.

Create/update/delete were **not** added as HTTP endpoints. Current API conventions and 12A.1 frontend only use GET + POST test. Those operations stay on the service until a later slice needs them.

---

## API Routes Added/Updated

| Method | Path | Behaviour |
| --- | --- | --- |
| GET | `/api/v1/amazon/connection` | `service.overview()`; `public_model_dump`; 503 if persistence is required and missing |
| POST | `/api/v1/amazon/connection/test` | `service.test_sp_api()`; empty body; extra fields (including credentials) rejected |

---

## Request/Response Changes

GET keeps existing fields and adds:

- `connection_status`
- `persisted`
- `region`
- `selling_partner_id`
- `authorized_at`
- `last_successful_validation_at`
- `last_successful_sync_at`
- `last_error_code`

No existing field was renamed. POST test body must be empty `{}` or omitted.

Never returned: `token_reference`, `refresh_token`, `access_token`, `client_secret`, `client_id`

---

## Security Validation

- Response models and JSON schema have no secret fields.
- A row with `token_reference` still omits it from GET.
- Extra POST fields are rejected (400 via the app’s validation handler).
- Routes import neither the repository nor LWA/SP-API clients.

---

## Organization Isolation Validation

A connection for another organization is not returned by GET. Default-org GET stays `persisted=false` with no other-org `selling_partner_id`.

---

## Tests Added

`apps/api/tests/test_amazon_connection_api.py`

- GET persisted metadata
- GET env fallback
- Fallback does not insert
- POST sandbox result
- POST does not persist `connected`
- Cross-org GET isolation
- Secret fields absent from GET, schema, and extra POST body

---

## Test Results

`54 passed` in 0.43s

- `test_amazon_connection_api.py`
- `test_amazon_connection.py`
- `test_amazon_connection_service.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_persistence.py`
- `test_sp_api_sandbox.py`

---

## Concerns

Frontend types are still 12A.1-shaped; extra GET fields are ignored until 12B.1A.5. Create/update/delete remain service-only, not HTTP.

---

## Explicit Confirmation

**Only Milestone 12B.1A.4 API Integration implemented. No OAuth, SecretProvider, token handling, ingestion, frontend, or Copilot changes added.**

---

## Next slice (not started)

**12B.1A.5 — Frontend connection state**

Show persisted status, environment, provider, and last validation. No OAuth UI. Distinguish persisted `not_connected` from in-page test `CONNECTED`.

Wait for explicit approval before implementing.

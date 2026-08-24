# Milestone 12B.1A.3 — Amazon Connection Service Overlay

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1A.4.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`)  
**Previous slices:**
- [12B.1A.1 — Connection Metadata Database](milestone-12b1a1-amazon-connection-metadata-database.md)
- [12B.1A.2 — Repository Layer](milestone-12b1a2-amazon-connection-repository.md)  
**Architecture:** [milestone-12b1a-connection-metadata-persistence.md](milestone-12b1a-connection-metadata-persistence.md)

This slice added service orchestration only. No OAuth, SecretProvider, ingest, API redesign, or frontend changes.

---

## Implementation Summary

`AmazonConnectionService` now overlays org-scoped persisted metadata on the 12A.1 environment view. GET/overview never calls Amazon and never auto-creates rows. Sandbox `test_sp_api()` is unchanged and does not persist `connected`. Create/update/delete go through the repository and return sanitized views only.

---

## Files Changed

- `apps/api/app/amazon/connection.py` — overlay, lifecycle methods, sanitized view fields
- `apps/api/tests/test_amazon_connection_service.py` — new

No schema, migration, route, frontend, Copilot, or sandbox-client changes.

---

## Service Responsibilities Added

| Method | Purpose |
| --- | --- |
| `overview()` | Persisted SP-API sandbox row if present, else 12A.1 env view |
| `create_connection()` | Metadata only; current organization; no secrets |
| `update_connection()` | Lifecycle fields via repository |
| `delete_connection()` | Org-scoped metadata delete |
| `test_sp_api()` | Existing sandbox check, no DB writes |

---

## Connection Lifecycle Behaviour

- Persisted `connection_status` is the state machine (`not_connected`, `pending_authorization`, `pending_validation`, `connected`, `degraded`, `revoked`, `error`).
- Display `status` stays `NOT_CONNECTED` for this slice so a DB row is not treated as seller authorization.
- Missing rows fall back to the env view without inserting.
- Duplicates and missing rows raise existing `PersistenceError`.

---

## Sandbox Behaviour Validation

Successful sandbox test still returns test-result `CONNECTED`. It does **not** set persisted `status=connected`. After a successful mocked test, the stored row remains `not_connected` with `token_reference` null.

Reason: sandbox credential validity ≠ seller authorization.

---

## Security Validation

- Create has no secret parameters.
- Update rejects `refresh_token`, `access_token`, `client_secret`, `client_id`, and `token_reference`.
- Public views omit `token_reference`.
- Org A cannot see Org B’s row.
- Logs use connection id + status only.

---

## Tests Added

`apps/api/tests/test_amazon_connection_service.py`

- Persisted row returned when present
- Env fallback when no row
- Fallback does not insert
- Sandbox success does not persist `connected`
- Organization isolation
- Secret fields rejected
- Duplicate / not-found mapped to `PersistenceError`
- Lifecycle update + delete

---

## Test Results

`47 passed` in 0.38s

- `test_amazon_connection_service.py`
- `test_amazon_connection.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_persistence.py`
- `test_sp_api_sandbox.py`

---

## Concerns

GET now includes additive view fields (`persisted`, `connection_status`, `region`, …) via the existing overview model. Frontend types are unchanged until 12B.1A.5; extra JSON is ignored. Create/update/delete are service methods only — no new HTTP routes (12B.1A.4).

---

## Explicit Confirmation

**Only Milestone 12B.1A.3 Service Overlay implemented. No OAuth, SecretProvider, token handling, ingestion, API redesign, or frontend changes added.**

---

## Next slice (not started)

**12B.1A.4 — API integration**

Same URLs; richer sanitized GET; `public_model_dump`. No `token_reference` in JSON.

Wait for explicit approval before implementing.

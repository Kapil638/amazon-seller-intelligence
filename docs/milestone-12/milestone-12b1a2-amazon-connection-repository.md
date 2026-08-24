# Milestone 12B.1A.2 — Amazon Connection Repository Layer

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1A.3.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`)  
**Previous slice:** [12B.1A.1 — Connection Metadata Database](milestone-12b1a1-amazon-connection-metadata-database.md)  
**Architecture:** [milestone-12b1a-connection-metadata-persistence.md](milestone-12b1a-connection-metadata-persistence.md)

This slice added org-scoped data access only. No OAuth, SecretProvider, ingest, API, or frontend changes.

---

## Implementation Summary

Added `AmazonConnectionRepository` in the existing persistence module. It creates, reads, lists, updates lifecycle fields, and deletes connection metadata. No Amazon calls and no secret storage.

---

## Files Changed

- `apps/api/app/persistence/repositories.py` — `AmazonConnectionRepository`
- `apps/api/tests/test_amazon_connection_repository.py` — new

---

## Repository Methods Added

| Method | Purpose |
| --- | --- |
| `create(...)` | Org-owned row; no secret parameters |
| `get(organization_id, provider=, environment=)` | Unique lookup; other org → `None` |
| `get_by_id(organization_id, connection_id)` | ID lookup always includes org |
| `list_for_org(organization_id)` | No global list |
| `update(organization_id, connection_id, **lifecycle_fields)` | Status and timestamp/error fields only |
| `delete(organization_id, connection_id)` | Org-scoped metadata delete only |

Missing rows return `None` / `False`. Duplicates raise SQLAlchemy `IntegrityError` (same pattern as other ASI repositories).

Allowed `update` fields:

- `status`
- `last_successful_validation_at`
- `last_successful_sync_at`
- `last_error_at`
- `last_error_code`
- `authorized_at`

Rejected on create/update: `refresh_token`, `access_token`, `client_secret`, `client_id`, `token_reference`.

---

## Organization Isolation Validation

Get, get-by-id, list, update, and delete all filter on `organization_id`. Org A cannot read, update, or delete Org B’s row.

---

## Security Validation

- `create` has no secret parameters
- `update` rejects secret field names
- `token_reference` is not writable through the repository (reserved for 12B.1B)
- No Amazon or LWA access
- No global listing method

---

## Tests Added

`apps/api/tests/test_amazon_connection_repository.py`

- Create succeeds
- Get by organization / provider / environment
- Org A cannot retrieve Org B
- List returns only the current org
- Status/lifecycle update; cross-org update is a miss
- Duplicate unique constraint
- Secret fields rejected on create and update
- Delete is organization-scoped

---

## Test Results

`40 passed` in 0.32s

- `test_amazon_connection_repository.py`
- `test_amazon_connection_persistence.py`
- `test_sp_api_sandbox.py`
- `test_amazon_connection.py`

---

## Concerns

Duplicate create surfaces as `IntegrityError` rather than a domain exception, matching existing ASI repositories. Service-layer wrapping can wait for 12B.1A.3. `token_reference` cannot be set here by design.

---

## Explicit Confirmation

**Only Milestone 12B.1A.2 Repository Layer implemented. No OAuth, SecretProvider, ingestion, API, or frontend changes added.**

---

## Next slice (not started)

**12B.1A.3 — Service overlay**

GET uses a persisted row if present, else the 12A.1 env view. POST `/connection/test` remains the sandbox/.env path and must not persist `connected`.

Wait for explicit approval before implementing.

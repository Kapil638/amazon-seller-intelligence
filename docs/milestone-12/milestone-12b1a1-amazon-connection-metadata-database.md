# Milestone 12B.1A.1 — Amazon Connection Metadata Database

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1A.2.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`)  
**Architecture:** [milestone-12b1a-connection-metadata-persistence.md](milestone-12b1a-connection-metadata-persistence.md)

This slice created persistence only. No OAuth, SecretProvider, ingest, API, or frontend changes.

---

## Implementation Summary

Added organization-owned `amazon_connections` persistence: SQLAlchemy `AmazonConnection`, additive Alembic `0007_amazon_connections`, and database-only tests. Connection state only. No secrets stored.

---

## Files Changed

- `apps/api/app/persistence/models.py` — `AmazonConnection`
- `apps/api/migrations/versions/0007_amazon_connections.py` — new
- `apps/api/tests/test_amazon_connection_persistence.py` — new

---

## Database Schema Created

Table: `amazon_connections`

| Column | Notes |
| --- | --- |
| `id` | UUID primary key |
| `organization_id` | UUID FK → `organizations.id`, **ON DELETE RESTRICT** |
| `provider` | required (`SP_API`) |
| `environment` | required (`SANDBOX` / `PRODUCTION`) |
| `region` | required (`eu` / `na` / `fe`) |
| `status` | required; default `not_connected` |
| `selling_partner_id` | nullable |
| `application_id` | nullable |
| `token_reference` | nullable opaque placeholder; not a secret |
| `authorized_at` | nullable |
| `last_successful_validation_at` | nullable |
| `last_successful_sync_at` | nullable; reserved for 12B.2+ |
| `last_error_at` | nullable |
| `last_error_code` | nullable |
| `created_at` / `updated_at` | timestamps |

**Constraints**

- Unique: `uq_amazon_connections_org_provider_env` (`organization_id`, `provider`, `environment`)
- Index: `ix_amazon_connections_org`
- CHECK: `ck_amazon_connections_status`  
  `not_connected`, `pending_authorization`, `pending_validation`, `connected`, `degraded`, `revoked`, `error`

**Not present:** `refresh_token`, `access_token`, `client_secret`, `client_id`

---

## Migration Created

- Revision: `0007_amazon_connections`
- Revises: `0006_advertising_models`
- Head: `0007_amazon_connections`
- Additive only: create table, unique, check, index
- No existing tables altered

---

## Tests Added

`apps/api/tests/test_amazon_connection_persistence.py`

- Table create + insert
- Organization ownership + RESTRICT FK metadata
- Duplicate org + provider + environment rejected
- Separate orgs can share provider/environment
- Sandbox vs production allowed for the same org
- No secret columns in model or migration
- `token_reference` stores an opaque placeholder, not a token
- Invalid status rejected
- Migration revision chain is additive

---

## Security Validation

Passed. No credentials, env tokens, or LWA secrets copied into the schema. `token_reference` is nullable opaque text only. Nothing logged. No public API models added.

---

## Test Results

`32 passed` in 0.26s

- `test_amazon_connection_persistence.py`
- `test_sp_api_sandbox.py`
- `test_amazon_connection.py`

Alembic: `0006_advertising_models -> 0007_amazon_connections (head)`

---

## Concerns

SQLite pytest does not enforce `ON DELETE RESTRICT` (foreign keys are not PRAGMA-enabled). The constraint is on the model and in the Postgres migration. Unique and CHECK constraints are covered in SQLite tests.

---

## Explicit Confirmation

**Only Milestone 12B.1A.1 implemented. No OAuth, SecretProvider, ingestion, or API changes added.**

---

## Next slice (not started)

**12B.1A.2 — Repository layer**

Wait for explicit approval before implementing.

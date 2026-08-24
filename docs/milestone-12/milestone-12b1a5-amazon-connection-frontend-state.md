# Milestone 12B.1A.5 — Amazon Connection Frontend State

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1B.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`)  
**Previous slices:**
- [12B.1A.1 — Connection Metadata Database](milestone-12b1a1-amazon-connection-metadata-database.md)
- [12B.1A.2 — Repository Layer](milestone-12b1a2-amazon-connection-repository.md)
- [12B.1A.3 — Service Overlay](milestone-12b1a3-amazon-connection-service-overlay.md)
- [12B.1A.4 — API Integration](milestone-12b1a4-amazon-connection-api-integration.md)  
**Architecture:** [milestone-12b1a-connection-metadata-persistence.md](milestone-12b1a-connection-metadata-persistence.md)

This slice updated the existing Connection UI to display backend connection state. No OAuth, SecretProvider, ingest, backend, or Copilot changes.

---

## Implementation Summary

The Connection page now shows **persisted Amazon connection state** and **latest sandbox validation** as separate panels. A successful sandbox test no longer replaces persisted `NOT_CONNECTED` with Connected. No OAuth, Connect Amazon, secrets, or backend changes.

---

## Frontend Files Changed

- `apps/web/src/lib/types.ts`
- `apps/web/src/components/amazon-connection.tsx`
- `apps/web/src/components/amazon-connection-ui.test.tsx`

---

## UI Behaviour Added

Amazon connection panel: provider, environment, region, persisted status (`NOT_CONNECTED`, …), record type (saved vs environment fallback), marketplace, application, and optional seller partner ID / timestamps / last error.

Sandbox panel: separate status (`CONNECTED` / `FAILED` / Not tested), timestamp, Test Connection, and copy that sandbox success is not seller authorization.

---

## Connection State Handling

Persisted UI uses `connection_status`. Fallback (`persisted: false`) shows environment defaults and stays **Not connected**. Load failures still use the existing connection error alert.

---

## Sandbox Test Display Behaviour

Test results stay in the sandbox panel only. After a successful test, persisted status remains **Not connected** / `NOT_CONNECTED` while sandbox shows **CONNECTED**.

---

## Security Validation

- No secret fields in types or UI
- Extra payload keys (`refresh_token`, `token_reference`, …) are not rendered
- No Connect Amazon / credential UI

---

## Tests Added

- Environment fallback view
- Persisted `NOT_CONNECTED` is not shown as connected
- Sandbox result appears separately
- Persisted not-connected remains after sandbox `CONNECTED`
- Secret fields never rendered
- Load error handling kept

---

## Test Results

Frontend: **27 passed** (3 files).

Browser: `/connection` shows **Not connected** and **Latest sandbox validation**. After Test Connection, sandbox became **CONNECTED** with “This is not seller authorization”; persisted stayed **Not connected**.

---

## Concerns

Local Postgres does not yet have `amazon_connections` (`0007` not applied), so GET can 500 until that migration is run. The UI still showed the split panels and handled the error; sandbox POST still worked. The Next.js hydration warning is from `theme-toggle.tsx`, not this page.

---

## Explicit Confirmation

**Only Milestone 12B.1A.5 Frontend Connection State implemented. No OAuth, SecretProvider, token handling, ingestion, backend architecture, or Copilot changes added.**

---

## Next slice (not started)

**12B.1B — SecretProvider**

Do not start until explicitly approved.

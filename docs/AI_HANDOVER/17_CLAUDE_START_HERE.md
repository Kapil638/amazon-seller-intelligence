# 17 — Claude Start Here

You are continuing Amazon Seller Intelligence after a Cursor handover at **Milestone 12B.1D**.

## Read in this order

1. `CLAUDE.md` (repo root) — contract
2. This file
3. `docs/AI_HANDOVER/` (00–16, then 18)
4. `docs/adr/` (especially 0002–0006)
5. `docs/milestone-12/` (slice completion docs; 12B.1D is latest implementation)
6. Then inspect code: `apps/api/app/amazon/`, persistence models, `apps/web/src/components/amazon-connection.tsx`

## Do not implement yet

First produce a **repository understanding / architecture validation report**.

Confirm the next milestone is:

**12B.2 — Canonical Seller Identity + Marketplace Ingestion**

unless actual repository evidence shows a blocker (for example: secret leak, migration branch, failing tests, or identity already implemented).

## Facts you must not drift from

- Latest completed Amazon work: **12B.1D** validation handshake. No ingest.
- Next is **12B.2**, not 12C. Ads API is 12C.
- Rainforest stays. SP-API is additive seller-owned data.
- Copilot does not call providers. Skills are not implemented.
- Tokens are not in Postgres. `token_reference` is opaque and not public.
- Sandbox Test Connection ≠ seller authorization.
- Sandbox credentials ≠ Draft/Production Connect Amazon credentials.
- `connected` only after validation. Callback stops at `pending_validation`.
- Tests: backend 620 passed, frontend 33 passed (24 Aug 2026). No live Amazon in CI tests.
- Alembic head: `0008_amazon_oauth_states`.

## Explicitly forbidden without a new approved slice

- Start listings/orders/inventory/reports/finances ingest
- Start Ads API
- Modify Copilot behaviour or Skills
- Replace Rainforest
- Store tokens in business tables
- Live Amazon in pytest
- LangGraph / CrewAI / autonomous agents

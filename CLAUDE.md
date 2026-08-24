# Amazon Seller Intelligence

Primary onboarding contract for Claude Code. Read this before changing architecture or starting a milestone.

Companion package: `docs/AI_HANDOVER/`. Start with `docs/AI_HANDOVER/17_CLAUDE_START_HERE.md`.

## Product Mission

ASI is an Amazon Seller Intelligence platform. It helps sellers understand marketplace listings, profit, advertising, and (next) their own Amazon operational data.

It is not an autonomous Amazon bot. It does not write to Amazon. It does not invent business metrics.

## Current Stable State

Latest completed Amazon milestone: **12B.1D — Seller Connection Validation Using SP-API**.

Completed and stable:

- Marketplace intelligence via Rainforest (ASIN lookup, competitor search)
- Deterministic listing intelligence (V1 + V2), profit (`profit-calc-v1`), advertising (`ads-calc-v1`)
- Copilot V1: conversations, planner, orchestrator, confirmation gate, synthesis, evidence/citation validation, UI
- ToolRegistry + EvidenceEnvelope (Milestone 11A)
- SP-API sandbox connectivity (12A.0) and Amazon Connection Beta UI (12A.1)
- Connection metadata persistence (12B.1A)
- SecretProvider foundation (12B.1B)
- Seller authorization through LWA token exchange + opaque `token_reference` (12B.1C through 12B.1C.5)
- Seller validation handshake via `GET /sellers/v1/marketplaceParticipations` (12B.1D)

Not started:

- 12B.2 canonical seller identity / marketplace ingestion
- Listings, orders, inventory, reports, finances ingest
- Ads API (that is **12C**, not 12B.2)
- Skills implementation
- Live end-to-end seller grant against a real Amazon consent (code exists; Amazon-side + HTTPS still incomplete)

## Architecture Principles

- Deterministic Python engines own calculations, scores, money math, and measurable facts.
- AI owns language, planning, reasoning, summarization, and explanation.
- AI must not invent unsupported business metrics.
- EvidenceEnvelope is the trust/evidence boundary.
- ToolRegistry is the execution boundary for Copilot.
- Copilot does not call provider APIs directly.
- Skills sit conceptually above Tools. Skills are future orchestration, not provider wrappers. Skill implementation is paused.
- Historical reports/snapshots remain immutable.
- No destructive Amazon writes.
- No autonomous agents / LangGraph / CrewAI at this stage.
- Unknown data remains unknown.
- Seller credentials must never enter normal business tables, frontend payloads, Copilot, Skills, EvidenceEnvelope, or logs.
- Tenancy is `organization_id`. Do not use `selling_partner_id` as the tenant key.

## Provider Responsibilities

Do not merge these:

| Provider | Owns |
| --- | --- |
| Rainforest API | Marketplace / public ASIN intelligence |
| Amazon SP-API | Seller-owned Amazon operational intelligence |
| Amazon Ads API | Future seller-owned advertising data (**12C**) |
| Seller input | COGS and private business costs |

SP-API is not a Rainforest replacement. After seller identity + a listing adapter exist, compare Rainforest marketplace ASIN view vs SP-API seller-owned listing view.

## Deterministic vs AI Responsibility

Python owns:

- Listing scores, profit, ACOS/TACOS/ROAS, completeness, thresholds
- SP-API DTO parsing, connection state machine, secret reference validation

AI owns:

- Copilot planning and synthesis
- Listing/competitive strategy copy
- Optional image intelligence

If a number cannot be calculated from inputs, leave it unknown. Do not ask the LLM to estimate it.

## Evidence / Tool Boundaries

- Copilot calls tools through ToolRegistry only.
- Tools wrap existing Python services. They do not call Rainforest, SP-API, or Ads API directly.
- EvidenceEnvelope carries typed claims (`observed`, `calculated`, `historical`, …).
- Synthesis must cite evidence. Unsupported claims are invalid.
- Do not add Amazon connection secrets or `token_reference` to evidence.

## Amazon Security Rules

- Refresh tokens and access tokens live only in SecretProvider (`DevelopmentSecretProvider` in dev/CI).
- Database stores metadata + opaque `token_reference` only.
- Never expose `token_reference`, tokens, LWA secrets, or authorization codes in API JSON, frontend, Copilot, logs, or exceptions.
- `AMAZON_SECRET_BACKEND=production` must fail closed until a real production backend exists.
- Sandbox LWA + sandbox refresh token are for **Test Connection**.
- Draft/Production application id + LWA client id/secret are for **Connect Amazon**.
- Do not mix those credential sets.
- Sandbox Test Connection is not seller authorization and must not persist `connected` from the env-token path.
- Mark a seller `connected` only after 12B.1D validation succeeds.
- Process-level HTTP/access logs may still include callback query strings unless redacted at server/proxy level. Application logs must not log OAuth codes/tokens.

## Database / Snapshot Rules

- Historical product snapshots, analysis runs, profit snapshots, and advertising snapshots are immutable.
- Soft-delete analysis history; do not destroy underlying rows.
- Amazon connection tables are authorization metadata, not seller business data.
- Do not add refresh/access token columns to business or connection tables.
- Current Alembic head: `0008_amazon_oauth_states` (single head). Chain: `0001` … `0006_advertising_models` → `0007_amazon_connections` → `0008_amazon_oauth_states`.
- Do not invent migrations. Do not create seller-account/marketplace/listing tables until 12B.2+.

## Current Amazon Integration Status

| Slice | Status |
| --- | --- |
| 12A.0 Sandbox connectivity | Completed |
| 12A.1 Connection Beta | Completed |
| 12B Canonical seller-data architecture | Completed / approved (docs + ADRs) |
| 12B.1A Connection metadata persistence | Completed |
| 12B.1B SecretProvider foundation | Completed |
| 12B.1C Seller authorization | Implemented through 12B.1C.5 |
| 12B.1D Seller connection validation | Completed |
| 12B.2 Canonical seller identity | **Next. Not started.** |

Authorization path:

```text
POST /authorize → hashed OAuth state → Seller Central consent
  → GET /callback → LWA authorization-code exchange → put_secret(refresh)
  → bind token_reference → pending_validation
  → POST /connection/test (handshake) → connected | degraded | error/requires_reauth
```

OAuth callback does **not** call SP-API. Handshake is a separate `POST /connection/test`.

Local Connect Amazon currently uses a **SANDBOX** connection row by default. A live seller handshake needs a **PRODUCTION** row and production Sellers host.

## Known Limitations

1. Live seller authorization has not been fully proven end-to-end against a real seller grant.
2. Website OAuth Login URI handling is still incomplete.
3. Amazon Draft app Login URI / Redirect URI must match real HTTPS routes exactly. localhost is not suitable for the live Amazon round-trip.
4. Sandbox and Draft/Production credentials are deliberately separate.
5. Connect Amazon has SANDBOX/default-environment behaviour in local development.
6. ASI listing default marketplace may display `amazon.in` while a test seller may be Amazon.com. Do not confuse ASI marketplace default with connected seller marketplace participation.
7. `getMarketplaceParticipations` may omit `sellingPartnerId`. Validation can still succeed from participation.
8. No canonical seller identity / marketplace tables yet (12B.2).
9. No seller business-data ingestion.
10. No Ads API.
11. Rainforest remains active and must not be removed.
12. Rainforest vs SP-API ASIN comparison is not done (after 12B.3 listing adapter).
13. Uvicorn/access logs may expose callback query strings.
14. Production SecretProvider backend is not implemented.

## Current Test Baseline

Verified 24 August 2026, no live Amazon in tests:

- Backend: `cd apps/api && uv run pytest` → **620 passed**
- Frontend: `cd apps/web && npm test` → **33 passed** (3 files)

Do not add live Amazon calls to automated tests. `conftest.py` clears SP-API env.

## Milestone Naming Rules

Do not rename the next seller ingestion stage to 12C.

Approved continuation:

```text
12B.2 Canonical Seller Identity + Marketplace Ingestion
12B.3 Listings / Seller Product Adapter
12B.4 Orders
12B.5 Inventory
12B.6 Reports / business metrics
12B.7 Financial events
12B.8 Provenance / projection hardening
12B.9 Connect stable seller-data tools to intelligence/Copilot
12C   Ads API integration
```

After 12B.3, add a controlled Rainforest vs SP-API ASIN comparison. Do not skip identity (12B.2) to jump into listings or Ads.

## Next Approved Work

**12B.2 — Canonical Seller Identity + Marketplace Ingestion**

Expected scope:

- seller account identity
- marketplace participation normalization
- canonical marketplace rows
- provenance

Do not start listings ingest, orders, Ads, Copilot Amazon tools, or Skills as 12B.2.

First Claude action: produce a repository understanding / architecture validation report. Do not implement immediately.

## Explicit Do-Not-Change Rules

- Do not replace Rainforest with SP-API.
- Do not let Copilot call provider APIs directly.
- Do not store Amazon refresh/access tokens in business database columns.
- Do not expose `token_reference` publicly.
- Do not treat sandbox Test Connection as seller authorization.
- Do not merge sandbox and Draft/Production app credentials.
- Do not mark a seller connected until validation succeeds.
- Do not ingest seller business data before canonical identity/provenance layers are defined.
- Do not change deterministic calculations into LLM calculations.
- Do not introduce agents/LangGraph/CrewAI without explicit architecture approval.
- Do not perform live Amazon calls in automated tests.
- Do not modify Copilot behaviour, Skills, or intelligence engines unless a later approved milestone says so.
- Do not start 12B.2 until the handover architecture-validation report is done, unless the user explicitly starts that slice.

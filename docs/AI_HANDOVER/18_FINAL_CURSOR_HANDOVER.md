# 18 — Final Cursor Handover

**Date:** 24 August 2026  
**Branch:** `main`  
**GitHub remote:** `https://github.com/Kapil638/amazon-seller-intelligence.git`  
**Checkpoint tag:** `amazon-seller-connection-foundation-v1`

| Ref | Value |
| --- | --- |
| Implementation commit | `fb2704be0b857bdbad09cff2e711f54c46ce026f` — feat(amazon): persist connection metadata, secrets, OAuth, and seller validation |
| Handover docs commit | `8ba636a792fd89d16b6a573be7d062fbe0213d3b` — docs(handover): prepare ASI repository for Claude development |
| Checkpoint tag | `amazon-seller-connection-foundation-v1` (this file’s recording commit) |
| Prior freeze | tag `amazon-api-foundation-v1` at `67f54126c6ac165fe6db81a285c2e4fdad36adf8` |

## Current completed milestone

**12B.1D — Seller Connection Validation Using SP-API**

Connection metadata, SecretProvider (development), seller OAuth through LWA refresh-token storage, and Sellers `getMarketplaceParticipations` handshake. No ingest.

## Current architecture state

- Deterministic Python engines + Copilot ToolRegistry/EvidenceEnvelope unchanged
- Rainforest remains marketplace intelligence
- SP-API isolated under `apps/api/app/amazon/`
- Tokens only in SecretProvider; opaque `token_reference` in Postgres
- Sandbox vs Draft/Production credentials split
- Callback sets `pending_validation`; `connected` only after validation
- Canonical seller identity tables **not** built

## Test results (24 August 2026)

| Suite | Command | Result |
| --- | --- | --- |
| Backend | `cd apps/api && uv run pytest` | **620 passed** |
| Frontend | `cd apps/web && npm test` | **33 passed** (3 files) |

No live Amazon in tests.

## Migration status

| Check | Result |
| --- | --- |
| Head | `0008_amazon_oauth_states` (single head) |
| Current (configured DATABASE_URL) | `0008_amazon_oauth_states` |
| Amazon revisions | `0007_amazon_connections`, `0008_amazon_oauth_states` |

## Secret scan

- `.env` / `.env.*` gitignored except tracked `.env.example`
- Tracked `.env.example` values are empty placeholders
- Tests use fictional `Atzr|test-…` / `Atza|test-…` only
- No production SecretProvider credentials in repo
- Secrets committed: **NO** (verified before push)

## Known live Amazon limitations

See `15_KNOWN_ISSUES_AND_TECH_DEBT.md`. In short: live seller grant not fully proven; Login URI incomplete; HTTPS redirect required; SANDBOX default for Connect Amazon locally; `sellingPartnerId` may be absent; no ingest; access logs may include callback query strings; production SecretProvider fail-closed.

## Exact next milestone

**12B.2 — Canonical Seller Identity + Marketplace Ingestion**

Ads API remains **12C**. Do not rename ingest to 12C.

## Recommended first Claude action

Read:

1. `CLAUDE.md`
2. `docs/AI_HANDOVER/17_CLAUDE_START_HERE.md`
3. `docs/AI_HANDOVER/*`
4. `docs/adr/*`
5. `docs/milestone-12/*`

Then inspect code.

**Do not implement immediately.**

First produce a repository understanding / architecture validation report and confirm the next milestone is 12B.2 unless actual repository evidence reveals a blocker.

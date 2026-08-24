# ASI Amazon Foundation — Final Git Freeze Checkpoint

**Date:** 23 August 2026  
**Tag:** `amazon-api-foundation-v1`  
**HEAD:** `67f54126c6ac165fe6db81a285c2e4fdad36adf8`

This freeze records the completed Amazon API foundation **after** the pre–Amazon checkpoint (`13a91c2`) and **before** Milestone 12B.1A.

No implementation was added as part of this freeze. Existing local work was inspected, validated, committed, pushed, and tagged.

---

## Git Freeze Summary

**Latest GitHub Commit:**  
`67f54126c6ac165fe6db81a285c2e4fdad36adf8` — `docs(amazon): add seller data backbone architecture`

**New Commit Hashes:**

| Hash | Message |
| --- | --- |
| `3eab2ab` | `feat(amazon): add SP-API sandbox connectivity foundation` |
| `c1e6452` | `feat(amazon): add Amazon connection beta experience` |
| `67f5412` | `docs(amazon): add seller data backbone architecture` |

**Amazon Foundation Included:**

- **12A.0** — LWA client, SP-API sandbox client, Sellers `getMarketplaceParticipations`, DTOs, provenance, fixtures, tests, sandbox config
- **12A.1** — connection service, routes, test endpoint, `/connection` page, UI + tests
- **12B docs** — `docs/milestone-12/`, ADR 0002–0006, recovery checkpoint

**Files Excluded:**

- `apps/api/.env` (gitignored)
- `.env.local`, real LWA/refresh/access tokens, node_modules, venvs, build artifacts

**Security Check:**  
Passed. No secrets committed. `.env.example` has empty credential values. Tests use placeholders only.

**Push Status:**  
On `origin/main`. Local `main` matches remote.

**Checkpoint Tag:**  
`amazon-api-foundation-v1` → `67f5412` (on origin)

**Current Stable Baseline:**  
`amazon-api-foundation-v1` (`67f5412`)

**Next Recommended Milestone:**  
12B.1A — Connection Metadata Persistence

---

## Prior baseline

| Ref | Hash | Message |
| --- | --- | --- |
| Pre–Amazon freeze | `13a91c2` | `docs(checkpoint): freeze pre Amazon API data backbone state` |
| Tag | `pre-amazon-api-data-backbone` | Applied at that freeze |

---

## Commit 1 — SP-API sandbox foundation

**Hash:** `3eab2ab`  
**Message:** `feat(amazon): add SP-API sandbox connectivity foundation`

**Files:**

- `apps/api/.env.example`
- `apps/api/app/amazon/__init__.py`
- `apps/api/app/amazon/__main__.py`
- `apps/api/app/amazon/lwa.py`
- `apps/api/app/amazon/models.py`
- `apps/api/app/amazon/sandbox.py`
- `apps/api/app/core/config.py`
- `apps/api/app/core/exceptions.py`
- `apps/api/tests/conftest.py`
- `apps/api/tests/fixtures/sp_api/get_marketplace_participations.sandbox.json`
- `apps/api/tests/test_sp_api_sandbox.py`

---

## Commit 2 — Amazon connection beta

**Hash:** `c1e6452`  
**Message:** `feat(amazon): add Amazon connection beta experience`

**Files:**

- `apps/api/app/amazon/ads_api.py`
- `apps/api/app/amazon/common.py`
- `apps/api/app/amazon/connection.py`
- `apps/api/app/api/routes/__init__.py`
- `apps/api/app/api/routes/amazon_connection.py`
- `apps/api/tests/test_amazon_connection.py`
- `apps/web/src/app/connection/page.tsx`
- `apps/web/src/components/amazon-connection-ui.test.tsx`
- `apps/web/src/components/amazon-connection.tsx`
- `apps/web/src/components/app-shell.tsx`
- `apps/web/src/lib/api.ts`
- `apps/web/src/lib/types.ts`

---

## Commit 3 — Seller data backbone architecture

**Hash:** `67f5412`  
**Message:** `docs(amazon): add seller data backbone architecture`

**Files:**

- `docs/adr/0002-amazon-data-provider-separation.md`
- `docs/adr/0003-canonical-amazon-seller-data-model.md`
- `docs/adr/0004-seller-data-provenance-and-source-precedence.md`
- `docs/adr/0005-amazon-seller-identity-model.md`
- `docs/adr/0006-amazon-connection-credential-boundary.md`
- `docs/checkpoints/asi-project-context-recovery-checkpoint-2026-08-23.md`
- `docs/milestone-12/README.md`
- `docs/milestone-12/milestone-12a0-sp-api-sandbox-connectivity.md`
- `docs/milestone-12/milestone-12a1-amazon-connection-beta.md`
- `docs/milestone-12/milestone-12b-sp-api-data-backbone-architecture.md`
- `docs/milestone-12/milestone-12b1-implementation-plan.md`
- `docs/milestone-12/milestone-12b1-production-connection-security-architecture.md`

---

## What this freeze does not include

Do not treat this tag as starting:

- 12B.1A Connection Metadata Persistence
- SecretProvider
- Production OAuth
- Seller ingestion
- Orders / Listings / Inventory / Reports / Finances
- Copilot or Skills changes

---

## Next step

**12B.1A — Connection Metadata Persistence**

Table + repository + sanitized GET overlay only. No OAuth. No ingest.

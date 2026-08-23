# ASI Project Context Recovery Checkpoint

**Date:** 23 August 2026  
**Purpose:** Recover exact repository context after the demo and determine the next development step.  
**Mode:** Read-only inspection. No code, migrations, commits, or implementation.

This checkpoint sits **after** the published pre–Amazon API freeze (`13a91c2`) and records the **local uncommitted** 12A.0–12B.1 work.

---

## 1. Git checkpoint

**Branch:** `main` (tracks `origin/main`)  
**HEAD:** `13a91c2e20799ccb609c1533408d55d02c5d7660`  
**Latest commit:** `docs(checkpoint): freeze pre Amazon API data backbone state`  
**Remote:** `https://github.com/Kapil638/amazon-seller-intelligence.git` — in sync (`0` ahead / `0` behind `origin/main`)  
**Unpushed commits:** none

**Published tags at HEAD:** `pre-amazon-api-data-backbone`, `v0.11C.2`

### Modified tracked files

- `apps/api/.env.example`
- `apps/api/app/api/routes/__init__.py`
- `apps/api/app/core/config.py`
- `apps/api/app/core/exceptions.py`
- `apps/api/tests/conftest.py`
- `apps/web/src/components/app-shell.tsx`
- `apps/web/src/lib/api.ts`
- `apps/web/src/lib/types.ts`

### Untracked files

- `apps/api/app/amazon/` (full package)
- `apps/api/app/api/routes/amazon_connection.py`
- `apps/api/tests/fixtures/sp_api/`
- `apps/api/tests/test_amazon_connection.py`
- `apps/api/tests/test_sp_api_sandbox.py`
- `apps/web/src/app/connection/`
- `apps/web/src/components/amazon-connection.tsx`
- `apps/web/src/components/amazon-connection-ui.test.tsx`
- `docs/adr/0002` … `0006`
- `docs/milestone-12/` (all 12A / 12B / 12B.1 docs)

**Recovery fact:** GitHub still ends at the pre–Amazon API checkpoint. All 12A.0–12B.1 work exists only in the local working tree.

---

## 2. Milestone timeline

| Milestone | Status | Reference |
| --- | --- | --- |
| MVP foundation | Completed (on `main`) | `7c12db9` |
| Listing Intelligence | Completed (on `main`) | `901fa4b`, `7b5a050`; `docs/listing-intelligence-v2.md` |
| AI recommendations | Completed (on `main`) | listing/image AI in `7b5a050` / `901fa4b` |
| History / reports | Completed (on `main`) | `c0706cb` |
| 11A Copilot tool layer | Completed (on `main`) | `834a79b` |
| 11B Seller Copilot | Completed (on `main`) | `c3b75d4` |
| 11C.1 Profit Intelligence | Completed (on `main`) | `171e668` |
| 11C.2 Advertising Intelligence | Completed (on `main`) | `0a21a88`, ADR 0001 |
| 11D Skill architecture | Completed as architecture only (paused) | `docs/milestone-11d-architecture.md`; no Skill code |
| 11D.1 Copilot domain tools | Completed (on `main`) | `e331182` |
| Pre-Amazon checkpoint | Completed (on `main`) | `13a91c2` + tag `pre-amazon-api-data-backbone` |
| 12A.0 SP-API sandbox | Completed locally, not committed | `apps/api/app/amazon/*`, `test_sp_api_sandbox.py`, `docs/milestone-12/milestone-12a0-*.md` |
| 12A.1 Connection Beta | Completed locally, not committed | connection routes/UI/tests, `docs/milestone-12/milestone-12a1-*.md` |
| 12B Canonical seller data | Completed as architecture only, not committed | `docs/milestone-12/milestone-12b-*.md`, ADR 0003–0005 |
| 12B.1 Production connection | Architecture + plan only, not committed; implementation not started | `milestone-12b1-*.md`, ADR 0006 |

---

## 3. Current system architecture

### Backend status

FastAPI under `apps/api`. Routes: products, analysis, scoring, competitors, reports, usage, bulk, copilot, profit, advertising, plus uncommitted `amazon_connection`. Services cover listing, AI, history, profit, advertising, reports. Repositories in `app/persistence/repositories.py`. Integrations: Rainforest, mock, OpenAI, Supabase storage, sandbox SP-API (uncommitted).

### Frontend status

Next.js `apps/web`. Pages: `/` Analyze, `/copilot`, `/profit`, `/history`, `/reports`, `/bulk`, plus uncommitted `/connection`. Nav includes Connection. No seller dashboard/sync UI.

### Database status

Alembic through `0006_advertising_models`. Models: organizations, scoring, product snapshots, analysis, reports, bulk, usage, copilot, profit, advertising. **No Amazon tables.**

### Architecture concerns

- Large uncommitted 12A–12B.1 surface on top of the published freeze.
- Connection is env-based, not org-persisted.
- Tenancy is still `default_organization_id`.

---

## 4. Amazon integration checkpoint

### SP-API sandbox (12A.0)

**Status:** Implemented locally (uncommitted).

**Implemented components:**

- `LwaClient` (`refresh_token` grant, `SecretStr`, no secret logs)
- `AmazonSpApiSandboxClient`
- Sellers `GET /sellers/v1/marketplaceParticipations`
- Pydantic DTOs
- `SpApiSandboxProvenance`
- CLI `python -m app.amazon`

**Files:**

- `apps/api/app/amazon/{lwa,sandbox,models,__main__,__init__}.py`
- `apps/api/tests/test_sp_api_sandbox.py`
- `apps/api/tests/fixtures/sp_api/get_marketplace_participations.sandbox.json`

**Tests:** LWA parse/auth/timeout/missing creds; sandbox headers/path; HTTP errors; no secret serialization; package does not import Copilot/engines. Mocks only.

### Amazon Connection Beta (12A.1)

**Status:** Implemented locally (uncommitted). Config/service-based, not persisted.

**Implemented components:**

- `GET /api/v1/amazon/connection`
- `POST /api/v1/amazon/connection/test`
- `AmazonConnectionService`
- `/connection` page
- Test Connection UI
- Ads placeholder `NOT_CONNECTED`

**Missing (intentionally, 12A.1):**

- `amazon_connections` table
- OAuth
- SecretProvider
- Production consent
- Connect/Disconnect UX

---

## 5. Milestone 12B architecture status

Documents exist (uncommitted). Decisions recorded:

- Provider separation (ADR 0002)
- Rainforest vs SP-API vs Ads
- Canonical seller model (ADR 0003)
- Provenance + source precedence (ADR 0004)
- Seller identity: org + account + marketplace + SKU (ADR 0005)

| ADR | Status |
| --- | --- |
| 0001 | Accepted **and committed** with 11C.2 |
| 0002 | Accepted (docs only, untracked) |
| 0003 | Accepted (docs only, untracked) |
| 0004 | Accepted (docs only, untracked) |
| 0005 | Accepted (docs only, untracked) |
| 0006 | Accepted as 12B.1 design (docs only, untracked); **not implemented** |

---

## 6. Milestone 12B.1 readiness

Do not implement from this checkpoint. Inspection only.

### Connection metadata

**Missing** as persistence.

There is a Pydantic `AmazonConnectionOverview` / `AmazonConnectionService`, not a SQLAlchemy `AmazonConnection`, no migration `0007`, no repository.

### Secret architecture

**Missing** in code.

`SecretProvider` / `put_secret` / `get_secret` do not exist. `token_reference` exists only in docs/ADR 0006. Runtime secrets are still `.env` (`SP_API_LWA_*`, `SP_API_SANDBOX_REFRESH_TOKEN`).

### Production OAuth

**Missing.**

No authorize/login/callback routes, no OAuth state table, no `authorization_code` grant (LWA is refresh-token only).

---

## 7. Architectural boundary validation

**Boundary status:** Held.

- Rainforest stays in `app/providers/rainforest.py`; no `app.amazon` import.
- SP-API isolated under `app/amazon/`; Copilot/profit/advertising do not import it.
- No SkillRegistry / SP-API in Skills.
- Listing / profit-calc-v1 / ads-calc-v1 not redesigned for SP-API.

**Issues found:** None in coupling. Risk is **unpublished local work**, not a boundary break.

---

## 8. Database checkpoint

**Latest migration:** `0006_advertising_models`  
**Amazon-related tables:** none  
**Pending database changes:** `amazon_connections` (and later oauth/secret tables) planned for 12B.1A+, not applied  
**Potential conflicts:** none with existing schema; 12B.1A is additive

---

## 9. Test checkpoint

**Baseline on published HEAD:** 11D.1 era (docs cited 472 API / 20 web).  
**Local uncommitted add:** `test_sp_api_sandbox.py`, `test_amazon_connection.py`, `amazon-connection-ui.test.tsx`. Prior session reported **495 API / 24 web** after 12A.1; this checkpoint did **not** re-run the suite.

**Amazon tests:** mocked only; conftest clears SP-API env.  
**Regression status:** not re-verified in this pass.

---

## 10. Security check

**Status:** Sound for 12A.

- `.env` / `.env.*` gitignored; `.env.example` tracked with empty SP-API placeholders
- `apps/api/.env` ignored
- LWA uses `SecretStr`; responses run `public_model_dump`
- Frontend types have no credential fields
- No `token_reference` in API responses (and sanitizer would reject a `token*` key)

**Issues:** Production tokens still env-scoped (known 12A limit). Uncommitted tree is the main operational risk (loss of sandbox + docs if the working copy is discarded).

---

## Final summary

1. **Current ASI checkpoint:** Published `main` = `13a91c2` (pre–Amazon API freeze + 11D.1). Local working tree holds **12A.0, 12A.1, 12B architecture, 12B.1 architecture/plan, ADR 0002–0006**.

2. **Latest stable milestone on GitHub:** 11D.1 + checkpoint `13a91c2`.

3. **Current implementation position:** Sandbox LWA + Connection Beta **work locally**. Canonical data model and production token architecture are **docs only**. **12B.1A has not started.**

4. **Risks / blockers:** Entire Amazon track is uncommitted. Demo/recovery depends on this working tree. Secret manager vendor still a PO choice (does not block 12B.1A).

5. **Recommended next single action:**

**D) Other** — **commit (and optionally tag) the local 12A.0–12B.1 work first**, then start **12B.1A**.

Do not start 12B.1A on an unpublished pile, and do not treat 12A as unfinished product work: it is implemented, just not on GitHub. After that freeze, the next **code** slice is **A) 12B.1A — Connection Metadata Persistence** (table + repository + sanitized GET overlay; no OAuth, no ingest).

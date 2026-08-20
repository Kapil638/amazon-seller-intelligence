# MILESTONE 10 — PERSISTENCE REPORT

**Date:** 20 August 2026  
**Status:** Complete  
**Auth / SP-API / Ads / Redis / Celery / Reviews / Offers / image generation / agents:** not started

This is the Milestone 10 completion record. Setup steps live in [persistence-supabase.md](persistence-supabase.md). Schema and ER diagram live in [database-schema.md](database-schema.md).

---

## Database

- technology: PostgreSQL (Supabase); SQLite in-memory for automated tests only
- ORM: SQLAlchemy 2.0 (single ORM)
- migration system: Alembic (`apps/api/migrations/`)
- tables created:
  - `organizations`
  - `product_snapshots`
  - `analysis_runs`
  - `listing_analysis_results`
  - `ai_listing_results`
  - `image_intelligence_results`
  - `report_uploads`
  - `bulk_jobs`
  - `bulk_job_items`
  - `generated_reports`
  - `usage_events`

## Supabase

- PostgreSQL: yes (`DATABASE_URL`, FastAPI only)
- Storage: yes when `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` are set; otherwise an in-process memory file store
- private buckets: `seller-report-uploads`, `generated-reports`

## Tenant foundation

- organization model: `organizations` plus `organization_id` on business tables
- auth implemented: **NO**
- current organization resolution: `DEFAULT_ORGANIZATION_ID` (default `11111111-1111-4111-8111-111111111111`) via `current_organization_id()` — not scattered as a magic ID in application code

Authentication is future. The tenant boundary is already modeled.

## Product snapshots

- immutable history: **yes** (append-only)
- same ASIN multiple snapshots: **yes**

If an ASIN is analyzed on 20 August, 3 September, and 20 September, all three snapshots are retained.

## Analysis persistence

- deterministic V2: saved on `POST /api/v1/analysis/listing/v2`
- AI V2: attached to the same `analysis_runs.id` (`report_id`)
- image intelligence: attached to the same run
- partial reports: optional AI/image failure marks status `partial` and keeps the deterministic result

Save flow:

1. Analyze ASIN → fetch Product
2. Listing V2 → ProductSnapshot + AnalysisRun + listing result
3. Optional AI Strategy → persist onto the same AnalysisRun
4. Optional Image & Media → persist onto the same AnalysisRun

## History API

- list endpoint: `GET /api/v1/reports`
- detail endpoint: `GET /api/v1/reports/{report_id}`
- pagination: `offset` / `limit` (max 100)
- filters: `asin`, `marketplace`, `status`, `created_from`, `created_to`

`GET /api/v1/reports/{report_id}` returns persisted historical data only. It does not refresh Amazon or AI data.

## Frontend

- history route: `/history` and `/history/[id]`
- navigation: Analyze · History · Seller Reports · Bulk Due Diligence
- historical report UI: Historical Analysis banner, product overview, Listing Intelligence V2, optional AI Strategy V2, optional Image & Media Intelligence, plus analyzed date, fetched date, source, and prompt/score versions

`/reports` remains Seller Central uploads. History is saved ASIN analyses.

Empty History copy:

> No saved analyses yet.  
> Analyze an ASIN to create your first report.

## Seller Reports

- original uploads persisted: yes (when the database is configured)
- storage bucket: `seller-report-uploads`
- file hashing: SHA-256; duplicates are identified and stored, not rejected

File bytes are not stored in PostgreSQL.

## Bulk

- jobs persisted: yes
- items persisted: yes
- generated Excel persisted: `generated-reports`
- retrieval: `GET /api/v1/bulk/jobs/{job_id}/report.xlsx` (in-memory job, else Storage via FastAPI)

Processing remains in-process. Redis/Celery were not added.

## Usage

Persistent usage events are dual-written from the in-memory ledger when the database is configured. Persist failures are swallowed so usage tracking cannot break analysis. API credentials are not stored.

## Provider savings

- Rainforest calls opening a saved report: **0**
- OpenAI calls opening a saved report: **0**

Persistence is not cache. Expired memory TTL does not refresh a historical report.

## Security

- database credentials backend only: **yes**
- Supabase service key backend only: **yes** (no `NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY`)
- storage buckets private: **yes** (no permanent public URLs)
- tenant scoping: list/detail queries always filter by the current organization

Row Level Security is **not** enforcing user isolation today because there is no authenticated user context. FastAPI uses a privileged backend database/service connection.

## Persistence failure behavior

- Live Analyze listing quality / AI / images: analysis is still returned. `meta.persisted` is false and `meta.persistence_warning` explains that the live result could not be saved.
- History list/detail: missing `DATABASE_URL` → **503**. Unknown report id → **404**.

## Tests

- new tests: 12 in `apps/api/tests/test_persistence.py`
- total backend tests: **321 passed**
- result: pass
- test database: forced `DATABASE_URL=sqlite://` and empty Supabase credentials (no developer Supabase project)

## Frontend build

- result: **pass** (`next build`, including `/history` and `/history/[id]`)

## Migrations

- current revision: `0001_m10_persistence`
- upgrade: `cd apps/api && uv run alembic upgrade head`
- create: `cd apps/api && uv run alembic revision --autogenerate -m "describe the change"`
- rollback one: `cd apps/api && uv run alembic downgrade -1`

The initial downgrade drops Milestone 10 tables. Do not use it on production data.

## Live provider calls during tests

- Live Rainforest calls during tests: **NO**
- Live OpenAI calls during tests: **NO**

## Known limitations

- Authentication is not implemented.
- RLS does not isolate users.
- Re-analyze current listing is **future** and must create a **new** product snapshot and analysis run. It must not mutate the historical report.
- Report deletion is not implemented.
- Listing-score trend charts are not built; multiple snapshots/reports per ASIN are stored for later.
- V1 listing AI, competitor comparison, and competitive AI are not saved as History reports.
- Tests use SQLite JSON, not PostgreSQL JSONB operators.
- Storage without Supabase keys uses process memory (lost on restart).
- Bulk processing remains in-process.
- Short-term Rainforest/OpenAI caches remain in-process memory.

## Files added

- `apps/api/alembic.ini`
- `apps/api/migrations/env.py`
- `apps/api/migrations/script.py.mako`
- `apps/api/migrations/versions/0001_m10_persistence.py`
- `apps/api/app/persistence/__init__.py`
- `apps/api/app/persistence/database.py`
- `apps/api/app/persistence/models.py`
- `apps/api/app/persistence/repositories.py`
- `apps/api/app/persistence/storage.py`
- `apps/api/app/persistence/types.py`
- `apps/api/app/persistence/hashing.py`
- `apps/api/app/models/saved_analysis.py`
- `apps/api/app/services/analysis_history_service.py`
- `apps/api/app/services/artifact_persistence_service.py`
- `apps/api/tests/test_persistence.py`
- `apps/web/src/app/history/page.tsx`
- `apps/web/src/app/history/[id]/page.tsx`
- `apps/web/src/components/analysis-history.tsx`
- `apps/web/src/components/historical-analysis.tsx`
- `docs/persistence-supabase.md`
- `docs/database-schema.md`
- `docs/persistence-report.md`

## Files modified

- `README.md`
- `docs/changes.md`
- `apps/api/.env.example`
- `apps/api/pyproject.toml`
- `apps/api/uv.lock`
- `apps/api/app/main.py`
- `apps/api/app/core/config.py`
- `apps/api/app/core/exceptions.py`
- `apps/api/app/usage/ledger.py`
- `apps/api/app/api/routes/analysis.py`
- `apps/api/app/api/routes/reports.py`
- `apps/api/app/api/routes/bulk.py`
- `apps/api/app/bulk/jobs.py`
- `apps/api/app/models/listing_analysis_v2.py`
- `apps/api/app/models/ai_listing_intelligence_v2.py`
- `apps/api/app/models/ai_image_intelligence.py`
- `apps/api/tests/conftest.py`
- `apps/api/tests/test_products.py`
- `apps/web/src/components/app-shell.tsx`
- `apps/web/src/components/product-lookup.tsx`
- `apps/web/src/lib/api.ts`
- `apps/web/src/lib/types.ts`

## git diff --stat

Recorded at completion (untracked files listed separately under Files added):

```text
 README.md                                         |  45 +-
 apps/api/.env.example                             |  10 +
 apps/api/app/api/routes/analysis.py               |  74 ++
 apps/api/app/api/routes/bulk.py                   |  18 +-
 apps/api/app/api/routes/reports.py                |  66 +-
 apps/api/app/bulk/jobs.py                         |  18 +
 apps/api/app/core/config.py                       |  11 +
 apps/api/app/core/exceptions.py                   |  25 +-
 apps/api/app/main.py                              |  10 +-
 apps/api/app/models/ai_image_intelligence.py      |   4 +
 apps/api/app/models/ai_listing_intelligence_v2.py |   4 +
 apps/api/app/models/listing_analysis_v2.py        |   3 +
 apps/api/app/usage/ledger.py                      |  32 +-
 apps/api/pyproject.toml                           |   6 +-
 apps/api/tests/conftest.py                        |  16 +
 apps/api/tests/test_products.py                   |   2 +-
 apps/api/uv.lock                                  | 946 ++++++++++++++++++++--
 apps/web/src/components/app-shell.tsx             |   5 +-
 apps/web/src/components/product-lookup.tsx        |  24 +
 apps/web/src/lib/api.ts                           |  85 +-
 apps/web/src/lib/types.ts                         |  57 ++
 docs/changes.md                                   |  28 +-
 22 files changed, 1361 insertions(+), 128 deletions(-)
```

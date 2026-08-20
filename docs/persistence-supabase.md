# Persistence — Supabase setup (Milestone 10)

This app stores historical analyses in PostgreSQL and files in Supabase Storage. FastAPI remains the business backend. Next.js never talks to the database or Storage with a service role key.

**Authentication is future.** Tenant rows already have `organization_id`. Local/dev uses one default organization.

**Row Level Security is not enforcing user isolation today.** FastAPI uses a privileged database/service connection. Prepare organization-aware RLS later; do not enable policies that block backend access now.

## Roles

| Layer | Role |
| --- | --- |
| In-memory TTL cache | Short-term duplicate Rainforest/OpenAI call prevention |
| PostgreSQL | Permanent historical records (snapshots, analysis runs, uploads metadata, bulk jobs, usage events) |
| Supabase Storage | Permanent original Seller Central files and generated Excel reports |

Opening a saved report is **not** a cache lookup. It reads historical rows only. Expired cache TTL must not trigger a provider refresh.

## Persistence failure behavior

- **Live Analyze listing quality / AI / images:** analysis is still returned. `meta.persisted` is false and `meta.persistence_warning` explains that the live result could not be saved.
- **History list/detail:** if `DATABASE_URL` is missing, `GET /api/v1/reports` returns **503**. Missing report id returns **404**.
- History GET endpoints never call Rainforest or OpenAI.

## 1. Create a Supabase project

1. Create a project at [https://supabase.com](https://supabase.com).
2. Use the project’s PostgreSQL connection string and Storage API credentials.
3. Local Docker/Supabase is optional. A cloud development project is enough.

## 2. PostgreSQL connection string

In Supabase: **Project Settings → Database**.

Prefer the **transaction pooler** URI for the app (`port 6543`) or the direct URI if pooler is not available.

Copy into `apps/api/.env` as `DATABASE_URL`. Do not commit the real value.

SQLAlchemy uses `postgresql+psycopg://...`. A dashboard URI that starts with `postgresql://` is accepted; the backend maps it to psycopg3.

If the database password contains `@`, encode it as `%40` so the host is not parsed incorrectly:

```bash
DATABASE_URL=postgresql://postgres:your%40password@db.PROJECT_REF.supabase.co:5432/postgres
```

## 3. Backend environment

Copy `apps/api/.env.example` to `apps/api/.env` if needed. Set:

```bash
DATABASE_URL=
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
DEFAULT_ORGANIZATION_ID=11111111-1111-4111-8111-111111111111
DEFAULT_ORGANIZATION_NAME=Development
```

Leave placeholders blank until you have a project.

**Never** set `NEXT_PUBLIC_SUPABASE_SERVICE_ROLE_KEY`.
**Never** put `SUPABASE_SERVICE_ROLE_KEY` or `DATABASE_URL` in the Next.js app.

`GET /health` stays usable if `DATABASE_URL` is empty (`persistence: disabled`). It does not print credentials.

## 4. Storage credentials

`SUPABASE_URL` is the project URL (`https://xyz.supabase.co`).

`SUPABASE_SERVICE_ROLE_KEY` is the **service role** key. It is backend-only.

If Storage credentials are absent, uploads use an in-process memory file store (lost on restart). Database metadata can still persist when `DATABASE_URL` is set.

## 5. Apply Alembic migrations

From `apps/api`:

```bash
cd apps/api
uv sync
uv run alembic upgrade head
```

Current Milestone 10 revision: `0001_m10_persistence`.

Create a future migration:

```bash
uv run alembic revision --autogenerate -m "describe the change"
```

Review the generated file, then:

```bash
uv run alembic upgrade head
```

Roll back one revision (the initial migration drops Milestone 10 tables — do not use this on production data):

```bash
uv run alembic downgrade -1
```

Do not rely on application startup to create production tables. SQLite tests call `create_all` only for the isolated test database.

## 6. Create private buckets

In Supabase Storage, create **private** buckets (no public/anonymous access):

- `seller-report-uploads`
- `generated-reports`

Do not store API keys, prompts, or database backups in these buckets.

Downloads go through FastAPI (bulk Excel and client analysis PDFs). Signed URLs may be used later; they must stay short-lived. Do not create permanent public URLs.

## 7. Start FastAPI

```bash
cd apps/api
uv run uvicorn app.main:app --reload --port 8000
```

## 8. Verify database

```bash
curl http://localhost:8000/health
```

Expect `"persistence":"configured"` when `DATABASE_URL` is set.

After analyzing an ASIN with Listing Intelligence V2:

```bash
curl "http://localhost:8000/api/v1/reports"
curl "http://localhost:8000/api/v1/reports/{report_id}"
```

`GET /api/v1/reports/{report_id}` must not call Rainforest or OpenAI.

## 9. Run frontend

```bash
cd apps/web
npm install
npm run dev
```

Open **History** (`/history`). Seller Central uploads remain on **Seller Reports** (`/reports`).

## 10. Test saved reports

1. Analyze an ASIN.
2. Run Listing Intelligence V2 (saved automatically when the database is configured).
3. Optionally generate AI Strategy V2 and Image & Media Intelligence (attached to the same report).
4. Restart the API process.
5. Open History and reopen the report.
6. Confirm the snapshot and scores match and that no new Rainforest/OpenAI usage appears.

## Future (not in Milestone 10)

- Authentication / mapping users to organizations
- Organization-aware RLS
- **Re-analyze current listing** — must create a **new** product snapshot and analysis run; never mutate the historical report
- Recycle Bin / Restore Report UI (soft delete is implemented; restore is not)
- Listing-score trend charts (schema already keeps multiple snapshots per ASIN)

## Tests

Automated tests force `DATABASE_URL=sqlite://` and empty Supabase credentials. They do not use a developer’s real Supabase project and do not make live Rainforest or OpenAI calls.

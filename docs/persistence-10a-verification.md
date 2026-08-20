# MILESTONE 10A — Real Supabase integration verification

**Date:** 20 August 2026  
**Project:** existing `mynckadcsgabvspobghv` (no new project created)  
**Status:** Verified against live PostgreSQL + Storage using mock/fictional data only

Secrets were not printed, logged, or committed. `apps/api/.env` remains gitignored.

## Credentials (presence only)

- DATABASE_URL configured: **YES**
- SUPABASE_URL configured: **YES**
- SUPABASE_SERVICE_ROLE_KEY configured: **YES**
- SUPABASE_URL matches project ref: **YES**
- Key model: legacy `service_role` JWT (application not migrated to a newer secret-key model)

## Database

- Alembic upgrade: **PASS** (`0001_m10_persistence`)
- Dialect: PostgreSQL via SQLAlchemy `postgresql+psycopg` (dashboard `postgresql://` URI is accepted)
- Expected tables: **all present** (12 including `alembic_version`)
- Default development organization: **present**

## Storage

Private buckets created/verified:

- `seller-report-uploads` — present, **not public**
- `generated-reports` — present, **not public**

File store used: `SupabaseFileStore` (not the in-process memory store).

Smoke file round-trip: **PASS**. Short-lived signed URL created: **PASS**.

## Persistence smoke (mock data, ASIN `B0M10A0001`)

- Listing Intelligence V2 saved: **YES**
- AI Strategy V2 fixture attached to same report: **YES**
- Image intelligence fixture attached to same report: **YES**
- History list found the report: **YES**
- History detail reconstructed exact title/score + AI + image: **YES**
- Seller-report upload metadata + original bytes in Storage: **YES**
- `GET /health` persistence: `configured`
- `GET /api/v1/reports` and `GET /api/v1/reports/{id}`: **200**

## Provider calls during smoke

- Live Rainforest calls: **NO** (0)
- Live OpenAI calls: **NO** (0)

## Code adjustments required for this project URI

These are connection compatibility fixes, not new product features:

1. Map dashboard `postgresql://` to SQLAlchemy `postgresql+psycopg://`.
2. Escape `%` in Alembic `sqlalchemy.url` so encoded passwords such as `%40` are not treated as ConfigParser interpolation.

If the database password contains `@`, encode it as `%40` in `DATABASE_URL`. There must be exactly one `@` between the password and the host.

## Not done

- Authentication / RLS user isolation
- Password rotation (recommended after the password was pasted in chat earlier)
- No commit of `.env`
- No new Supabase project

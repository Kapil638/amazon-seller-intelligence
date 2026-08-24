# 14 — Local Development Setup

## Prerequisites

- Python 3.12+, uv
- Node.js 20+ (Next 16)
- Optional: Supabase project for persistence (tests do not require it)

## Backend

```bash
cd apps/api
cp .env.example .env   # then fill keys locally; never commit .env
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

`apps/api/.env.example` has **empty** credential placeholders only.

Keep sandbox and production/Draft Amazon credentials as separate variables. See comments in `.env.example`.

Apply migrations when using Postgres:

```bash
cd apps/api
uv run alembic upgrade head
```

## Frontend

```bash
cd apps/web
cp .env.example .env.local   # NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm install
npm run dev
```

## Amazon local notes

- `SP_API_SANDBOX_ENABLED` and sandbox LWA/refresh token enable **Test Connection**.
- Connect Amazon needs production/Draft application id, production LWA client, and `SP_API_OAUTH_REDIRECT_URI` matching Seller Partner Portal **exactly**.
- localhost is typically **not** accepted for the live Amazon OAuth round-trip. A public HTTPS tunnel or deployed callback may be required.
- Default connection row environment in local Connect Amazon is **SANDBOX**. A live seller handshake needs a **PRODUCTION** connection row.
- Website OAuth Login URI is still incomplete.

## Do not

- Commit `.env` / `.env.local`
- Point tests at live Amazon
- Mix sandbox refresh token with Draft application id

# 14 — Local Development Setup

## Prerequisites

- Python 3.12+, uv
- Node.js 20+ (Next 16)
- Optional: Supabase project for persistence (tests do not require it)
- macOS or Linux (see `./scripts/dev.sh` below — not supported on Windows)

## Quick start: frontend + backend + workers together

```bash
./scripts/dev.sh
```

Starts the backend API and frontend. **None of the three sync workers
(Listings, Orders, Sales & Traffic) start by default** —
`./scripts/dev.sh` alone gives you the frontend and API only. To also
start one or more workers (so a triggered sync actually gets claimed
and processed), set the corresponding flag(s) — any subset may be
combined:

```bash
ASI_LISTINGS_WORKER_ENABLED=true ./scripts/dev.sh
ASI_ORDERS_WORKER_ENABLED=true ./scripts/dev.sh
ASI_SALES_TRAFFIC_WORKER_ENABLED=true ./scripts/dev.sh

# or all three together:
ASI_LISTINGS_WORKER_ENABLED=true ASI_ORDERS_WORKER_ENABLED=true ASI_SALES_TRAFFIC_WORKER_ENABLED=true ./scripts/dev.sh
```

This is deliberate, not an oversight: this repository's local `.env`
points `DATABASE_URL` at a real, live Supabase project, not a disposable
one. A worker that started automatically the moment you ran a
convenience script would begin claiming and processing *real* jobs —
real Amazon SP-API calls — the instant one existed, with no explicit
action from you. Each `ASI_*_WORKER_ENABLED=true` flag is the one
explicit signal that authorizes that worker. Every worker module
enforces this same check itself (fail-closed) even if you run it
directly, so there is no way to start a live worker by accident through
either path. See `app/amazon/listings_worker.py`, `orders_worker.py`,
and `sales_traffic_worker.py`'s own module docstrings, and `docs/
AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md`, for the full design.

Each process's output is prefixed (`[backend]`, `[frontend]`, `[worker]`,
`[orders-worker]`, `[sales-traffic-worker]` — the latter three only when
enabled). A single Ctrl-C stops everything that was started cleanly.
Detects if a port is already in use or a worker of a given type is
already running (never starts a duplicate of that type) before starting
anything. This does not replace the individual commands below — both
remain fully supported; use whichever fits what you're doing. See
`scripts/test_dev_sh.sh` for this script's own test suite.

**Without a job type's worker enabled (via either path above), a
triggered sync of that type will sit `queued` forever** — every worker
is a separate process from the API and is never started implicitly, by
design. Existing previously-synced data for that job type is never
affected either way.

## Backend

```bash
cd apps/api
cp .env.example .env   # then fill keys locally; never commit .env
uv sync
ASI_DB_RUNTIME_CONTEXT=api uv run uvicorn app.main:app --reload --port 8000
```

`ASI_DB_RUNTIME_CONTEXT=api` authorizes this process to open a
non-loopback (real remote Postgres/Supabase) database connection — see
`app/persistence/database.py`'s own module docstring for the full
production-database guard design. Omitting it is only safe if
`DATABASE_URL` is SQLite or a local loopback Postgres; against this
repository's real Supabase `DATABASE_URL`, the API will refuse to open
a connection without it. `./scripts/dev.sh` already sets this for you.

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

## Listings worker (standalone)

```bash
cd apps/api
ASI_LISTINGS_WORKER_ENABLED=true uv run python -m app.amazon.listings_worker
```

**`ASI_LISTINGS_WORKER_ENABLED=true` is required** — without it, the
process logs why and exits immediately (exit code 3) rather than
starting. This is a deliberate, fail-closed safety gate (see above): it
prevents ever claiming and processing real jobs against a real database
just because this command was run. A separate, long-running process —
never started implicitly by the API or by `./scripts/dev.sh`. Claims and
processes durable Listings synchronization jobs; without it running (or
enabled), a triggered sync stays `queued` indefinitely (harmlessly —
existing listing data is never affected). Graceful shutdown via Ctrl-C
or `SIGTERM`. See `docs/AI_HANDOVER/12B3H_LISTINGS_WORKER_OPERATIONS.md`.

## Orders worker (standalone)

```bash
cd apps/api
ASI_ORDERS_WORKER_ENABLED=true uv run python -m app.amazon.orders_worker
```

Identical shape and safety gate to the Listings worker above
(`ASI_ORDERS_WORKER_ENABLED=true` required, independent of the other two
flags). A separate, long-running process — never started implicitly by
the API or by `./scripts/dev.sh`. Claims and processes durable Orders
synchronization jobs; without it running (or enabled), a triggered sync
stays `queued` indefinitely (harmlessly — existing orders data is never
affected). Graceful shutdown via Ctrl-C or `SIGTERM`. See `app/amazon/
orders_worker.py`'s own module docstring and `docs/AI_HANDOVER/
12B4D_ORDERS_INGESTION_AND_UI.md`.

## Sales and Traffic worker (standalone)

```bash
cd apps/api
ASI_SALES_TRAFFIC_WORKER_ENABLED=true uv run python -m app.amazon.sales_traffic_worker
```

Identical shape and safety gate to the Listings worker above
(`ASI_SALES_TRAFFIC_WORKER_ENABLED=true` required, independent of the
other two flags). A separate, long-running process — never started
implicitly by the API or by `./scripts/dev.sh`. Claims and processes
durable Sales and Traffic report synchronization jobs; without it
running (or enabled), a triggered sync stays `queued` indefinitely
(harmlessly — existing sales/traffic data is never affected). Graceful
shutdown via Ctrl-C or `SIGTERM`. See `app/amazon/sales_traffic_worker.py`'s
own module docstring and `docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md`.

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

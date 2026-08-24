# 02 — Current Architecture

## Runtime shape

```text
Browser (Next.js, apps/web)
    ↓ HTTP JSON
FastAPI (apps/api)
    ├── Product / listing / competitor / reports / profit / ads engines
    ├── Copilot (planner → ToolRegistry → synthesizer)
    ├── Persistence (SQLAlchemy → PostgreSQL/Supabase; SQLite in tests)
    └── app/amazon/  SP-API isolation (connection, OAuth, secrets, Sellers)
```

Next.js never uses `DATABASE_URL`, Supabase service role, Rainforest keys, OpenAI keys, or Amazon LWA secrets.

## Frozen boundaries

| Boundary | Owner |
| --- | --- |
| Scores, money math, completeness | Python engines |
| Language / planning / explanation | OpenAI via AIProvider, Copilot synthesizer |
| Trust/evidence | EvidenceEnvelope |
| Copilot execution | ToolRegistry |
| Amazon credentials | SecretProvider (`DevelopmentSecretProvider` now) |
| Amazon connection metadata | `amazon_connections` (no tokens) |
| Marketplace public catalog | Rainforest `ProductDataProvider` |
| Seller-owned Amazon ops | SP-API (handshake only as of 12B.1D) |

## Amazon isolation

All SP-API code lives under `apps/api/app/amazon/`. Copilot, Profit, Advertising, and Rainforest must not import Amazon token material. Connection routes may use `current_organization_id()`.

Public Amazon HTTP:

- `GET /api/v1/amazon/connection`
- `POST /api/v1/amazon/connection/test`
- `POST /api/v1/amazon/connection/authorize`
- `GET /api/v1/amazon/connection/callback`

`GET /connection` never calls Amazon. Display `status` can remain `NOT_CONNECTED` while lifecycle `connection_status` is `connected`.

## Persistence

When `DATABASE_URL` is set: PostgreSQL (Supabase). Tests use SQLite `create_all`, not Alembic apply.

Single Alembic head: `0008_amazon_oauth_states`.

## What is not in the architecture yet

- Canonical `amazon_seller_accounts` / `amazon_marketplaces`
- Seller listings/orders/inventory/reports/finances tables
- Production SecretProvider
- Ads API client
- AuthN / user memberships / RLS isolation
- Skills runtime

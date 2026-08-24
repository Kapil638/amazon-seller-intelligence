# 04 — Database and Migrations

Authentication is not implemented. Every business table is scoped by `organization_id`. Local/dev uses `DEFAULT_ORGANIZATION_ID`.

## Alembic chain (verified 24 August 2026)

Single head. No branch.

```text
0001_m10_persistence
0002_scoring_profiles
0003_report_lifecycle
0004_copilot_conversations
0005_profit_models
0006_advertising_models
0007_amazon_connections
0008_amazon_oauth_states     ← HEAD
```

Verified:

```text
alembic heads    → 0008_amazon_oauth_states (head)
alembic current  → 0008_amazon_oauth_states (head)   # against configured DATABASE_URL
```

Do not invent migrations. Next Amazon identity tables belong to **12B.2**.

## Amazon tables (authorization metadata only)

### `amazon_connections` (0007)

Organization-owned connection row. Unique on `(organization_id, provider, environment)`.

Columns include: `provider`, `environment` (`SANDBOX` | `PRODUCTION`), `region`, `status`, optional `selling_partner_id`, `application_id`, opaque `token_reference`, timestamps (`authorized_at`, `last_successful_validation_at`, `last_successful_sync_at`, error fields).

**Never stores** refresh tokens, access tokens, LWA secrets, or authorization codes.

Status check constraint:

`not_connected | pending_authorization | pending_validation | connected | degraded | revoked | error`

`connected` means authorization validated. `last_successful_sync_at` is reserved for ingest freshness (always unused until 12B.2+).

### `amazon_oauth_states` (0008)

Temporary hashed OAuth state. Unique `state_hash`. FK to `amazon_connections`.

Stores `state_hash`, optional `amazon_state` (Amazon-returned state echo — not a token), `expires_at`, `consumed_at`.

**Never stores** raw ASI state secret (hash only), authorization codes, or tokens.

## Business tables (unchanged by 12B.1)

See `docs/database-schema.md` for product snapshots, analysis runs, scoring profiles, reports, bulk jobs, usage, profit/advertising models, Copilot conversations.

Those tables remain seller-intelligence history. Do not put Amazon credentials on them.

## Tests vs Alembic

Pytest uses SQLite `Base.metadata.create_all`. It does not apply Alembic to Supabase. Always keep SQLAlchemy models and migrations aligned.

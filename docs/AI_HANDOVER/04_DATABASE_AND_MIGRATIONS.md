# 04 — Database and Migrations

Authentication is not implemented. Every business table is scoped by `organization_id`. Local/dev uses `DEFAULT_ORGANIZATION_ID`.

## Alembic chain (verified 26 August 2026)

Single head. No branch.

```text
0001_m10_persistence
0002_scoring_profiles
0003_report_lifecycle
0004_copilot_conversations
0005_profit_models
0006_advertising_models
0007_amazon_connections
0008_amazon_oauth_states
0009_amazon_seller_identity  ← HEAD
```

Verified:

```text
alembic heads    → 0009_amazon_seller_identity (head)
alembic current  → 0009_amazon_seller_identity (head)   # against configured DATABASE_URL
```

`0009_amazon_seller_identity` (12B.2A) adds three schema-foundation tables — see below. It does not ingest data, run automatically, or wire into any live SP-API path.

### Known blocker: `alembic upgrade head` fails from a genuinely empty database

**This is a release/deployment blocker for any environment that must bootstrap from zero** (a fresh install, a new CI database, a disaster-recovery restore) — it is pre-existing, not introduced by `0009`, and is documented here rather than fixed, per 12B.2A's scope.

Root cause: `0001_m10_persistence.py`'s `upgrade()` calls `Base.metadata.create_all(bind)`, which creates every table *currently* defined on the live SQLAlchemy model metadata — not just the tables that existed when `0001` was written. Because `Base.metadata` has grown with every subsequent migration (including `0009`'s three new tables), running `alembic upgrade head` against a blank database makes `0001` silently create everything, and then `0002_scoring_profiles.py` fails with `table scoring_profiles already exists` when it tries to `op.create_table` something `0001` already created. Pinned by `tests/test_amazon_seller_identity_schema.py::test_full_migration_chain_from_empty_database_currently_fails_at_0002`, which asserts this exact failure so a future fix is forced to update that test rather than the regression going unnoticed.

This has apparently never been exercised in practice: every real database (dev, and presumably any deployed environment) was bootstrapped some other way — most plausibly `Base.metadata.create_all()` run directly at whatever point `0001` was current, followed by `alembic stamp head` — and has only ever been migrated forward one revision at a time since. That path never hits the collision.

**Proposed remediation (not implemented — needs explicit approval before any migration file is touched):**

| Option | Description | Trade-off |
| --- | --- | --- |
| **A. Baseline migration (recommended)** | Add a new `0010_baseline` migration whose `upgrade()` does nothing (or a no-op `pass`) and whose purpose is purely to become the new "zero" for fresh installs; ship a separate one-time bootstrap script (`Base.metadata.create_all()` + `alembic stamp head`) for fresh/CI databases instead of relying on `upgrade head` from empty. | Simplest, safest, touches no historical file. Fresh-install tooling must know to stamp instead of upgrade-from-zero. |
| **B. Repair `0001` in place** | Change `0001_m10_persistence.py` to only create the tables that existed at that historical point (an explicit list), instead of the live `Base.metadata`. | Makes `upgrade head` from empty actually work end-to-end. Violates the project's migration-immutability expectation (CLAUDE.md: "Do not invent migrations" implies past migrations are also not casually rewritten) and is riskier for any already-migrated database if the explicit table list is wrong. |
| **C. Leave as-is, document only** | Keep the current behavior; require every environment to bootstrap via `create_all()` + `stamp head`, never `upgrade head` from empty. | Zero code risk. Permanently forecloses a genuine "just run migrations" fresh-install story, which is normally expected of an Alembic-managed schema. |

Considerations for whichever option is chosen: existing deployed databases (already past `0001`/`0002`, unaffected either way), fresh installations (need a working zero-to-head or zero-to-stamp path), CI databases (currently use SQLite `create_all`, not Alembic — unaffected today, but would be affected if CI ever starts testing the real migration chain), downgrade behavior (Option B would need a correspondingly scoped `downgrade()`), and SQLite vs. PostgreSQL (the failure reproduces identically on both — confirmed against the real configured Postgres instance before being reverted, see the 12B.2A remediation report).

Do not invent further migrations without an approved slice, and do not rewrite `0001` or any other historical migration without explicit approval.

### `amazon_seller_accounts`, `amazon_marketplace_participations`, `amazon_ingestion_runs` (0009, 12B.2A)

Canonical Amazon seller identity schema foundation. `amazon_seller_accounts.selling_partner_id` is globally unique (V1: one selling partner belongs to one organization). `amazon_marketplace_participations` is unique on `(seller_account_id, marketplace_id)` — marketplace id is canonical identity, never the display domain. `amazon_ingestion_runs` is a reusable, currently-unused ingestion-attempt ledger. None of the three tables store tokens, refresh tokens, or `token_reference`. Nothing yet writes to them from a live path — `AmazonSellerAccountRepository`, `AmazonMarketplaceParticipationRepository`, and `AmazonIngestionRunRepository` exist and are unit-tested, but are not called from the OAuth callback, validation handshake, or any route.

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

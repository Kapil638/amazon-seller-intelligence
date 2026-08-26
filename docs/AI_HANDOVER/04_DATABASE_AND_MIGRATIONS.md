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

### Resolved (12B.2A.1): `alembic upgrade head` from a genuinely empty database

**Historical migration `0001_m10_persistence.py` has been repaired.** It previously called `Base.metadata.create_all(bind)`, which created every table *currently* defined on the live SQLAlchemy model metadata — not just the tables that existed when `0001` was written. Because `Base.metadata` grew with every subsequent migration, a genuine `alembic upgrade head` from a blank database made `0001` silently create everything, and `0002_scoring_profiles.py` then failed with `table scoring_profiles already exists`. Git archaeology (see `docs/AI_HANDOVER/19_DATABASE_DEPLOYMENT_HARDENING_ARCHITECTURE_REVIEW.md` for the full investigation) showed this bug was present from the very first commit that introduced Alembic — no database has ever obtained its schema via a genuine zero-to-head run; every real environment was bootstrapped some other way and stamped.

`0001` now creates, via deterministic `op.create_table`/`op.create_index` calls, exactly the eleven tables it historically owned (`organizations`, `product_snapshots`, `analysis_runs`, `listing_analysis_results`, `ai_listing_results`, `image_intelligence_results`, `report_uploads`, `bulk_jobs`, `bulk_job_items`, `generated_reports`, `usage_events`), reconstructed from the commit that introduced it — deliberately excluding `scoring_profiles` (owned entirely by `0002`), `listing_analysis_results.custom_listing_quality_score` / `.scoring_profile_snapshot` (added by `0002`), `analysis_runs.deleted_at` (added by `0003`), and `generated_reports.template_version` (added by `0003`). `0002`–`0009` are unchanged and remain the sole owners of everything they already added. `0001`'s default-organization bootstrap is preserved, now via a lightweight `sa.table` proxy and a plain existence check instead of importing the live ORM `Organization` model.

**This is safe for every database already stamped past `0001`:** Alembic never re-executes an already-applied revision's `upgrade()`, so the rewrite has zero effect on any database at `0001` or later — including the configured development database (`0008` at time of writing).

**Supported deployment paths, now both real:**

```text
empty PostgreSQL database
  → alembic upgrade head
  → complete schema at 0009_amazon_seller_identity
  → no collisions, no model/migration drift
```

```text
existing database at 0008
  → alembic upgrade 0009_amazon_seller_identity
  → existing rows preserved (0009 only adds new, empty tables)
  → amazon_seller_accounts / amazon_marketplace_participations / amazon_ingestion_runs created
```

There is no longer a supported "bootstrap via `Base.metadata.create_all()` + `alembic stamp head`" path — that was a workaround for the bug above, not a design choice, and is superseded now that `alembic upgrade head` works directly.

**Verification performed (12B.2A.1):**

- `tests/test_migration_chain_matches_orm_metadata.py` — a dependency-free static check using Alembic's offline `--sql` mode (compiles every migration for the PostgreSQL dialect with no real connection): confirms the full chain produces exactly 25 tables (24 application tables + `alembic_version`) with no collisions, and that the cumulative table/column set produced by `0001`–`0009` matches `Base.metadata` exactly, table-for-table and column-for-column. This runs in the normal test suite, everywhere, always.
- `apps/api/tests/postgres/test_disposable_postgres_deployment.py` — the real, runtime proof against an actual disposable PostgreSQL instance: empty-to-head, existing-database upgrade with data preservation, schema/constraint/FK inspection, drift check via live reflection, downgrade behavior, and the OAuth identity-concurrency invariant under real Postgres write serialization. **Opt-in only** (`ASI_ALLOW_DISPOSABLE_POSTGRES=1` + `POSTGRES_DISPOSABLE_TEST_URL`, refuses to run against anything resembling the configured application database) — see `apps/api/tests/postgres/_guard.py`. Could not be executed in the environment this repair was authored in (no Docker, no local PostgreSQL binary available); the guard logic itself was unit-tested directly and confirmed to correctly refuse a URL matching the real configured database. **Whoever next has Docker/CI access should treat that first run as the actual proof**, not this file's existence.
- `.github/workflows/backend-database-ci.yml` — new (this repository had no CI before): four jobs — fresh-install zero-to-head, existing-database (0008→0009) upgrade, the disposable-Postgres identity-concurrency suite, and the backend test suite plus the static drift check. All database jobs use an ephemeral, job-scoped PostgreSQL service container; none can reach the configured application database, which is not present in CI at all. Not yet run in CI (the workflow file itself has not been pushed, per this task's scope).

**Remaining limitation:** SQLite still cannot run these migrations at all — `0001`/`0002`/`0004`/`0005`/`0006` use `sqlalchemy.dialects.postgresql.JSONB` directly, which has no SQLite rendering (unlike `postgresql.UUID`, which SQLAlchemy degrades gracefully). This is expected and pre-existing: these migrations have only ever targeted PostgreSQL. Pinned by `tests/test_amazon_seller_identity_schema.py::test_full_migration_chain_from_empty_sqlite_fails_only_on_postgres_only_types`, which asserts the chain now fails *only* for this reason (not the old collision).

Do not invent further migrations without an approved slice, and do not rewrite `0002`–`0009` without explicit approval — they were not touched by this repair and remain the historical owners of everything they already created.

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

Most pytest coverage still uses SQLite `Base.metadata.create_all` and does not apply Alembic to Supabase — unchanged. As of 12B.2A.1, `tests/test_migration_chain_matches_orm_metadata.py` is the exception: it runs the real migration chain in Alembic's offline (`--sql`) mode (PostgreSQL dialect, no live connection) specifically to keep SQLAlchemy models and migrations aligned automatically, rather than by convention alone. The disposable-PostgreSQL suite under `apps/api/tests/postgres/` goes further with live reflection against a real instance, but is opt-in only (see above).

## Backup, rollback, and verification before applying a migration anywhere important

1. **Backup** — a verified, restorable snapshot taken immediately before the upgrade. A backup that has never been restored is not a verified backup.
2. **Dry run** — apply the migration to a disposable Postgres instance first (see `apps/api/tests/postgres/`), never to the target directly.
3. **Row-count and constraint verification** — `0009` adds no columns to existing tables, only new empty ones, so `amazon_connections`/`amazon_oauth_states` row counts must be identical before and after.
4. **Rollback** — `alembic downgrade 0008_amazon_oauth_states` is proven safe in isolation and drops only the three new, still-empty tables. Downgrading below `0001` is not a supported operation for any database bootstrapped before this repair (there is no clean single-step undo for the old `create_all()`-based bootstrap on such a database) — treat `0001` as a forward-only baseline in practice, even though it is now itself a deterministic, ordinary migration.
5. **Canary order** — a shared/staging environment first if one exists; otherwise the configured development database is an appropriately low-consequence first real target, since nothing yet writes to 12B.2A's new tables.

`0009` has not been applied to the configured development database as of this writing — that remains a separate authorization from writing or repairing the migration files.

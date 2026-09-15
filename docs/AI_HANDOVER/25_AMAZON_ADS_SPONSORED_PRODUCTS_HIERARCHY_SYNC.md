# 25. Amazon Ads Sponsored Products Hierarchy Synchronization (PR B2)

Status: **synchronization and persistence orchestration implemented, fixture-tested. No live Amazon call made. `ADS_API_BACKEND` remains disabled; no production deployment or migration applied.**

Contract authority: `docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md` (verified unchanged before and after this PR: 1,263 lines, 123,564 bytes, SHA-256 `ec987b13b14412e2a859d43d7e2bab642edd0811d82cf48037d5a53b3bbfc482`). Supporting context: `docs/AI_HANDOVER/24_AMAZON_ADS_SPONSORED_PRODUCTS_HIERARCHY_CONTRACTS.md` (PR B1's implementation record). Neither document's conclusions are modified by this PR. Document 22 does not exist and is not used.

## 1. Architecture and data flow

```
enqueue_sync_request(org, profile, entity_type)
        │
        ▼
amazon_ads_entity_sync_runs row, status='queued'
        │
        ▼  claim_next_sync_run() — fenced CAS claim, PostgreSQL advisory lock
   status='started', lease_owner=<worker>, lease_expires_at=now()+lease_duration
        │
        ▼  per-entity-type confidence gate check (before any HTTP call)
   disabled → fail_permanently('entity_sync_disabled_for_entity_type')
        │ enabled
        ▼
   resolve profile → connection → token_reference → SecretProvider
        │
        ▼  fenced lease renewal, then LWA refresh (no DB transaction open)
        ▼
   _fetch_all_pages(): bounded pagination loop, in-memory accumulation
     - fenced lease renewal before EVERY page fetch
     - cyclic nextToken detection (in-memory set, never logged)
     - max-page enforcement (ads_entity_sync_max_pages)
     - aggregate contract-mismatch check (observed>0, accepted==0)
        │
        ▼  (only on a fully successful, complete fetch)
   _persist_snapshot(): ONE atomic transaction
     - resolve each item's parent(s) via existing local rows
     - idempotent upsert per item (natural-key upsert, pre-existing repos)
     - reconciliation: count local rows this run's own upserts did NOT touch
     - fenced mark_succeeded (CAS on lease_owner/status/lease_expires_at)
     - checkpoint advance (same transaction)
        │
        ▼
   EntitySyncOutcome(succeeded | failed | retrying | lease_lost | no_job)
```

No page's items are ever persisted individually — the fetch phase holds no open database transaction across HTTP calls (mirrors `ads_report_service.py`'s own "never hold a transaction across an external wait" rule), and the persist phase never makes an HTTP call. This is the same fetch-then-atomically-persist shape PR A2 established for Reporting v3's final ingest step, applied here to an entire paginated snapshot rather than one report's rows.

## 2. New code

- `app/amazon/ads_entity_sync_service.py` (new) — `AmazonAdsEntitySyncService`, `_fetch_all_pages`, `_persist_snapshot`, `_LeaseLost`/`_CyclicPaginationToken`/`_ContractMismatch`/`_PageLimitExceeded` (internal-only signals), `_ENTITY_CONFIG` (per-entity-type dispatch table: list method, gate setting, repository class, parent-resolution kind, persistence field mapping).
- `app/persistence/repositories.py` — `AmazonAdsEntitySyncRunRepository` (enqueue, claim_next_sync_run, heartbeat, mark_succeeded, mark_retry, mark_failed — all fenced CAS) and `AmazonAdsEntitySyncCheckpointRepository` (get, advance).
- `app/persistence/models.py` — `AmazonAdsEntitySyncRun`, `AmazonAdsEntitySyncCheckpoint` ORM classes.
- `migrations/versions/0021_ads_entity_sync_runs.py` — the two new tables (see §3).
- `app/core/config.py` — `ads_entity_sync_max_pages`, `ads_entity_sync_lease_duration_seconds`, `ads_entity_sync_max_attempts`, `ads_entity_sync_retry_base_seconds`/`_retry_max_seconds`, `ads_entity_sync_max_global_concurrent_runs`, and five `ads_entity_sync_<type>_enabled` booleans (§7). A model validator (`_validate_ads_entity_sync_timeout_within_lease_duration`) mirrors the existing Reporting v3 lease/timeout margin check.
- `tests/test_amazon_ads_entity_sync_service.py` (new, 11 tests) and `tests/postgres/test_disposable_postgres_ads_entity_sync_migration.py` (new, opt-in, 6 tests).
- `tests/test_migration_chain_matches_orm_metadata.py` and `tests/test_amazon_seller_identity_schema.py` — updated table count / head-revision assertions for the new migration.
- `.github/workflows/backend-database-ci.yml` — new `existing-database-upgrade-0021` job; updated head-revision assertions in the fresh-install job.

Nothing else was modified. No Ads worker, scheduler, OAuth flow, frontend code, or `ADS_API_BACKEND` gate was touched.

## 3. Migration 0021 — justification

The existing schema (through migration 0020) has no way to represent an entity-hierarchy sync run's lifecycle, lease ownership, progress, or checkpoint — `amazon_ads_report_runs`/`amazon_ads_sync_checkpoints` are Reporting v3's own ledger, shaped around an async create/poll/download lifecycle with an `amazon_report_id` and a `start_date`/`end_date` request window, neither of which apply to a bounded-page GET snapshot fetch. Two new tables were added rather than reusing or widening the existing ones:

- **`amazon_ads_entity_sync_runs`** — one row per (organization, profile, entity_type) claim attempt. Lease columns (`lease_owner`, `lease_expires_at`, `status`, `attempt_count`, `next_retry_at`) mirror `amazon_ads_report_runs` exactly, so the claim/heartbeat/fenced-completion query shapes in `AmazonAdsEntitySyncRunRepository` reuse the same proven CAS pattern. Additional observability columns: `pages_processed`, `items_observed`, `items_accepted`, `items_schema_rejected`, `items_unsupported_state`, `items_missing_parent`, `reconciliation_stale_count`.
- **`amazon_ads_entity_sync_checkpoints`** — one row per (profile, entity_type), recording only `last_successful_sync_at` and `last_successful_run_id`. No date-range semantics (unlike Reporting v3's `synced_through_date`) — entity-list sync is a snapshot, not a windowed pull.

No new column was added to the five existing entity tables. `downgrade()` refuses (raising `RuntimeError`) if either new table is populated, matching the established pattern from migrations 0019/0020 — there is no way to represent run/checkpoint history in the pre-0021 schema, so downgrading with data present would silently discard it.

## 4. Transaction and lease design

- **Claim**: `claim_next_sync_run` takes a PostgreSQL advisory lock (`pg_advisory_xact_lock`, key `991_004_005` — distinct from every other claim lock in this codebase) to serialize the claim decision, then a single fenced `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`. A `max_global_active` cap bounds total concurrent runs across all organizations/profiles/entity types; there is no per-profile cap (unlike Reporting v3) since PR B2 did not receive a requirement for one and profile-level starvation is not a concern this run model exhibits — filing this as a candidate follow-up, not a gap in what shipped.
- **Stale-lease recovery — the one deliberate simplification from Reporting v3**: every stale `started` row (lease expired) terminalizes to `timed_out`, unconditionally. There is no resumable branch, because restarting a paginated entity-list fetch from page 1 is always safe and idempotent — unlike Reporting v3, there is no `amazon_report_id`-shaped side effect on Amazon's side whose duplication must be avoided.
- **Pre-call fencing**: `_renew_lease_or_raise` is called immediately before every external call (LWA refresh, every single page fetch), raising `_LeaseLost` — making zero further calls — if this worker no longer owns the lease. This is the same pre-call gate PR A2 added for Reporting v3's `create_report` after the review found stale workers could otherwise still issue a duplicate external call.
- **Persist-phase atomicity**: `_persist_snapshot` is the only database transaction this service performs per run. Every accepted item's upsert, the reconciliation count, the fenced `mark_succeeded`, and the checkpoint advance all happen inside one `session_scope()` block. If `mark_succeeded`'s CAS affects zero rows (lease lost), `_LeaseLost` is raised and the entire transaction rolls back — including the upserts staged earlier in that same transaction, which never committed. Proven at both the SQLite/unit level (`test_lease_loss_before_completion_rolls_back_the_whole_persist_transaction`) and against real PostgreSQL (`test_persist_and_checkpoint_advance_are_atomic_with_the_ownership_check`).

## 5. Pagination safeguards

- `page_size` is passed explicitly on every call from `settings.ads_entity_list_page_size` (PR B1's setting; PR B2 is its first actual reader).
- `nextToken` is followed exactly per PR B1's established truth table (absent/null → complete; non-empty string → followed exactly; blank/wrong-type → raises, already enforced inside `parse_entity_list_envelope`).
- **Cyclic-token detection**: an in-memory `set()` of every token seen this run. If a page's `nextToken` repeats a value already seen, the run fails immediately (`entity_sync_cyclic_pagination_token`) rather than looping. The token value itself is never included in the failure detail or logged anywhere — proven by `test_cyclic_pagination_token_fails_without_looping_forever_or_leaking_the_token`, which asserts a distinctive synthetic token string does not appear in the persisted `failure_detail`.
- **Bounded page count**: `ads_entity_sync_max_pages` (default 200) caps the fetch loop. Exceeding it fails visibly (`entity_sync_page_limit_exceeded`) rather than looping forever or persisting a partial snapshot — proven that zero rows are persisted and the checkpoint never advances when this fires.
- **Aggregate contract mismatch**: if the fully-fetched run observed items across all pages but accepted none of them, the run fails (`entity_sync_contract_mismatch`) rather than succeeding with an empty-looking snapshot — the entity-list analogue of `ads_report_service.py`'s `report_row_contract_mismatch` guard. A partial rejection (some accepted, some not) is not treated as failure; the rejected/unsupported-state counts are simply recorded on the completed run row.

## 6. Hierarchy resolution and tenant isolation

Every parent reference is resolved by looking up the parent's locally-persisted row via `get_by_external_id(ads_profile_id, external_id)` on the campaign/ad-group repositories — never fabricated, never borrowed from another profile (the lookup itself is scoped to `ads_profile_id`, and every profile belongs to exactly one organization, so this is also organization-scoped by construction). Ad groups resolve a campaign parent; product ads, keywords, and product targets resolve both a campaign and an ad-group parent. A missing or unresolvable parent causes that single item to be skipped and counted in `items_missing_parent` (and recorded via `AmazonAdsSyncErrorRepository`) — it does not fail the run, since this is the expected, ordinary state the first time a profile's hierarchy is synced before its parent entity type has been synced yet.

Reconciliation isolation was specifically tested (`test_reconciliation_counts_only_rows_untouched_by_this_run_within_the_same_profile`): a second profile's untouched row is never counted in the first profile's `reconciliation_stale_count`.

## 7. Snapshot reconciliation — mechanism

No new column was added to the five entity tables. Reconciliation is a pure **counting** step: within the same transaction as the upserts, the service tracks the set of local row ids its own upserts touched this run, then counts existing rows for this `(ads_profile_id, entity table)` whose id is **not** in that touched set. This never mutates `state` or deletes a row — it is observability only, recorded as `reconciliation_stale_count` on the completed run.

This deliberately does **not** compare `last_seen_at`/`first_seen_at` timestamps (though those columns are still updated by every upsert, unchanged, and remain useful for manual/observability queries). An id-membership check needs no shared "before this run" clock reference between the Python process and the database server, and — concretely found during this PR's own test development — no reconciliation of formatting differences between a Python-side `datetime.now(UTC)` reading and a `func.now()`-populated column across dialects (SQLite's `CURRENT_TIMESTAMP` text representation does not compare correctly against a tz-aware Python literal bound as a query parameter). The id-membership approach is exact by construction and was verified identically under SQLite (unit tests) and real PostgreSQL (disposable-Postgres tests).

Reconciliation only ever runs as part of a fully successful, complete persist transaction — by construction, `_persist_snapshot` is only ever reached after `_fetch_all_pages` returns without raising, which happens only when pagination reached a genuine end (`nextToken` absent/null) without hitting the page limit, a cyclic token, or a contract mismatch. There is no partial/incomplete path that reaches reconciliation.

## 8. Endpoint confidence / activation matrix

| Entity type | Blueprint confidence (§0.1, per doc 23/24) | `ads_entity_sync_<type>_enabled` default |
|---|---|---|
| campaign | Officially documented, live-confirmed | `True` |
| ad_group | Officially documented, not production-confirmed | `False` |
| product_ad | Media type documented; envelope key UNCONFIRMED (this codebase's own inference) | `False` |
| keyword | Media type documented; envelope key AND field shape UNCONFIRMED | `False` |
| product_target | Media type documented; envelope key AND field shape UNCONFIRMED | `False` |

Each gate is checked immediately after a run is claimed, before any HTTP call for that entity type — a disabled type fails permanently (`entity_sync_disabled_for_entity_type`) and issues zero calls, proven by `test_disabled_entity_type_gate_makes_zero_http_calls` (asserts `client.calls == []`). This is independent of, and in addition to, the pre-existing `ADS_API_BACKEND` gate, which remains disabled and was not touched by this PR.

## 9. Observability and failure classes

Persisted per run (never a raw response body, token, or entity-shaped string): `status`, `attempt_count`, `pages_processed`, `items_observed`, `items_accepted`, `items_schema_rejected`, `items_unsupported_state`, `items_missing_parent`, `reconciliation_stale_count`, `failure_class`, `failure_detail` (always `str()` of one of this module's own typed internal exceptions or an `app.core.exceptions.Ads*` exception — none of which are ever constructed with secret- or entity-shaped text, an invariant already established and tested for `ads_report_service.py`).

Failure classes introduced by this PR: `entity_sync_disabled_for_entity_type`, `profile_missing`, `connection_missing`, `secret_missing`, `token_refresh_failed`, `entity_sync_page_limit_exceeded`, `entity_sync_cyclic_pagination_token`, `entity_sync_contract_mismatch`, `entity_sync_envelope_contract_mismatch`, `entity_sync_authentication_failed`, `entity_sync_invalid_request`, `entity_sync_rate_limited`, `entity_sync_request_failed`.

Never logged or persisted anywhere: access tokens, refresh tokens, pagination tokens (proven explicitly for the cyclic-token failure path), report/download URLs (not applicable to this service), raw response bodies, campaign/ad-group/keyword names or targeting expressions/search terms, or seller identifiers.

## 10. Tests and CI results

- `tests/test_amazon_ads_entity_sync_service.py` — 11 tests: multipage ingestion and checkpoint advance, idempotent replay, cyclic-token failure (with token-leak assertion), page-limit exhaustion (zero persistence), contract mismatch (zero persistence), ad-group parent resolution with missing-parent counting, disabled-gate zero-HTTP-calls, lease-loss transactional rollback, reconciliation profile isolation, repository-level stale-lease-always-terminal, repository-level enqueue idempotency.
- `tests/postgres/test_disposable_postgres_ads_entity_sync_migration.py` — 6 opt-in tests: table/constraint shape after upgrade, downgrade refusal when populated, downgrade success when empty, stale-lease-always-terminal under real concurrency, fenced-mutation hijack rejection, persist/checkpoint atomicity under real PostgreSQL.
- `tests/test_migration_chain_matches_orm_metadata.py` and `tests/test_amazon_seller_identity_schema.py` updated for the new migration/table count; both pass with no drift between the migration chain and live ORM metadata.
- Full backend suite: **2237 passed, 111 skipped**, 0 failed (skips are the disposable-PostgreSQL suites, which require a local Postgres instance not available in this environment — expected and unchanged in kind from every prior PR in this series).

## 11. Remaining uncertainties

- Ad-group, product-ad, keyword, and product-target contracts remain exactly as UNCONFIRMED as PR B1 left them (see doc 24 §9) — this PR adds orchestration around those contracts but performs no live call against any of them, so it cannot and does not advance their confidence level. The per-entity-type gate defaults reflect this unchanged state.
- `ads_entity_sync_max_global_concurrent_runs` has no per-profile equivalent (see §4) — not requested for this PR, flagged as a candidate follow-up if multi-profile fairness becomes a concern once this is live.
- The five-endpoint evidence matrix's own unresolved items (client-ID header-name conflict, `Amazon-Ads-AccountId` requirement, response-schema gaps for three endpoints) are unchanged by this PR — they are PR B1/blueprint-level uncertainties, not something this orchestration layer could resolve without a live call.

## 12. Proposed live-validation sequence (not performed by this PR)

1. Apply migration 0021 to a non-production database first; confirm `alembic current` and run the disposable-Postgres suite against it.
2. With `ADS_API_BACKEND` still disabled, deploy this code with no behavioral change (dead code path).
3. In a controlled environment with real Ads credentials, enable `ADS_API_BACKEND` and `ads_entity_sync_campaigns_enabled` only; enqueue a single campaign sync for one profile; inspect the resulting run row's counts before persisting more broadly.
4. Only after campaign sync is validated against a real response, consider independently validating one sibling entity type at a time (ad groups first, as the next-most-documented), enabling its gate only after a real response confirms its envelope key and field shape — never batch-enable multiple UNCONFIRMED endpoints at once.

## 13. Confirmation

No production deployment, migration application, Railway change, Ads worker/scheduler, OAuth flow, live Amazon or SP-API call, or OpenAI call was made or modified by this PR. `ADS_API_BACKEND` remains disabled. No frontend code was touched. No Amazon mutation-shaped operation exists anywhere in the new code (`AmazonAdsEntitySyncService` and the new repositories expose only enqueue/read/list-shaped and fenced-completion methods — no create/update/delete against Amazon).

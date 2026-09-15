# 25. Amazon Ads Sponsored Products Hierarchy Synchronization (PR B2)

Status: **synchronization and persistence orchestration implemented, fixture-tested, revised after final review. No live Amazon call made. `ADS_API_BACKEND` remains disabled; no production deployment or migration applied.**

Contract authority: `docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md` (verified unchanged before and after this PR: 1,263 lines, 123,564 bytes, SHA-256 `ec987b13b14412e2a859d43d7e2bab642edd0811d82cf48037d5a53b3bbfc482`). Supporting context: `docs/AI_HANDOVER/24_AMAZON_ADS_SPONSORED_PRODUCTS_HIERARCHY_CONTRACTS.md` (PR B1's implementation record). Neither document's conclusions are modified by this PR. Document 22 does not exist and is not used.

## 0. Final-review correction summary

Four blockers were found in the PR's first pass and are all addressed here:

1. **Incoherent parent resolution** — a product ad/keyword/target's campaign and ad group were resolved independently, never checking the ad group actually belonged to that campaign. Fixed in `_resolve_parents` (§6).
2. **Partial rejection marked a clean success** — a run with schema-rejected, unsupported-state, missing-parent, or mismatched-parent items still became `'succeeded'` and advanced the checkpoint. Fixed with a new `'partial'` terminal status (§4, §6).
3. **"Reconciliation" was observability only** — it counted untouched rows but never actually deactivated or reactivated anything. Fixed with reversible `is_active`/`last_seen_entity_sync_run_id` tracking on all five entity tables (§7).
4. **Run/checkpoint scope omitted the Ads connection; checkpoint `advance()` trusted its caller** — fixed by adding `ads_connection_id` to both new tables and replacing `advance()` with a guarded, SQL-verified finalization path (§8).

Also fixed: a broad `except Exception` around LWA token refresh no longer persists `str(exc)` (§9), and every "GET-based" characterization of the five B1 list endpoints (which are POST requests, read-only in effect) was corrected (§1).

## 1. Architecture and data flow

```
enqueue_sync_request(org, profile, entity_type)
        │  resolves + validates the profile's own connection_id
        ▼
amazon_ads_entity_sync_runs row, status='queued'
        │  scope = (organization_id, ads_connection_id, ads_profile_id, entity_type)
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
   _fetch_all_pages(): bounded pagination loop against the POST-based,
   read-only list endpoint, in-memory accumulation
     - fenced lease renewal before EVERY page fetch
     - cyclic nextToken detection (in-memory set, never logged)
     - max-page enforcement (ads_entity_sync_max_pages)
     - aggregate contract-mismatch check (observed>0, accepted==0)
        │
        ▼  (only on a fully successful, complete fetch)
   _persist_snapshot(): ONE atomic transaction
     - _resolve_parents(): campaign AND ad-group coherence check per item
       ("missing" | "mismatched" | "ok")
     - idempotent upsert per coherently-parented item
     - is_clean = zero schema-rejected/unsupported-state/missing/mismatched
     - is_clean  → reconciliation (deactivate/reactivate) + mark_succeeded
                   + guarded checkpoint advance
     - !is_clean → mark_partial — NO reconciliation, NO checkpoint advance
        │
        ▼
   EntitySyncOutcome(succeeded | partial | failed | retrying | lease_lost | no_job)
```

No page's items are ever persisted individually — the fetch phase holds no open database transaction across HTTP calls, and the persist phase never makes an HTTP call. This is the same fetch-then-atomically-persist shape PR A2 established for Reporting v3's final ingest step.

## 2. New/changed code (this revision)

- `app/amazon/ads_entity_sync_service.py` — `_resolve_parents` (coherence check, new), `_upsert_with_parents` (new), `_persist_snapshot` rewritten for the clean/partial split and reconciliation, broad-exception sanitization in `_process_claimed`.
- `app/persistence/repositories.py` — `AmazonAdsEntitySyncRunRepository.enqueue`/`claim_next_sync_run` now carry `ads_connection_id`; `mark_succeeded` gained `items_mismatched_parent`/`items_deactivated`/`items_reactivated` (replacing `reconciliation_stale_count`); new `mark_partial`; `AmazonAdsEntitySyncCheckpointRepository.advance` rewritten as a guarded, SQL-verified finalization.
- `app/persistence/models.py` — `AmazonAdsEntitySyncRun`/`AmazonAdsEntitySyncCheckpoint` gained `ads_connection_id`; run gained `items_mismatched_parent`/`items_deactivated`/`items_reactivated` (replacing `reconciliation_stale_count`) and the `'partial'` status value; the five entity ORM classes (`AmazonAdsCampaign`, `AmazonAdsAdGroup`, `AmazonAdsAdvertisedProduct`, `AmazonAdsKeyword`, `AmazonAdsProductTarget`) gained `is_active`/`last_seen_entity_sync_run_id`.
- `migrations/versions/0021_ads_entity_sync_runs.py` — revised in place (still unmerged) rather than a follow-up migration: adds `ads_connection_id` to both new tables, widens the `status` check to include `'partial'`, adds `items_mismatched_parent`/`items_deactivated`/`items_reactivated`, and adds `is_active`/`last_seen_entity_sync_run_id` (+ an index) to the five existing entity tables.
- `tests/test_amazon_ads_entity_sync_service.py` — rewritten, 22 tests (was 11).
- `tests/postgres/test_disposable_postgres_ads_entity_sync_migration.py` — rewritten, 8 opt-in tests (was 6).

Unchanged from the first pass: `app/core/config.py`'s new settings, the CI workflow job, the overall architecture in §1. Nothing outside this PR's own file set was touched.

## 3. Migration 0021 — revised schema

- **`amazon_ads_entity_sync_runs`** — one row per (organization, connection, profile, entity_type) claim attempt. Scope columns: `organization_id`, `ads_connection_id` (new), `ads_profile_id`, `entity_type`. `status` check constraint: `'queued', 'started', 'waiting_to_retry', 'succeeded', 'partial', 'failed', 'timed_out'` (`'partial'` new). Accounting columns: `pages_processed`, `items_observed`, `items_accepted`, `items_schema_rejected`, `items_unsupported_state`, `items_missing_parent`, `items_mismatched_parent` (new), `items_deactivated` (new, nullable, populated only on a clean `'succeeded'` run), `items_reactivated` (new, same).
- **`amazon_ads_entity_sync_checkpoints`** — one row per (profile, entity_type). Scope columns: `organization_id`, `ads_connection_id` (new), `ads_profile_id`, `entity_type`.
- **Five existing entity tables** (`amazon_ads_campaigns`, `amazon_ads_ad_groups`, `amazon_ads_advertised_products`, `amazon_ads_keywords`, `amazon_ads_product_targets`) each gain `is_active BOOLEAN NOT NULL DEFAULT true` and `last_seen_entity_sync_run_id UUID NULL REFERENCES amazon_ads_entity_sync_runs(id) ON DELETE SET NULL`, plus an index on `(ads_profile_id, is_active)`. Added only after `amazon_ads_entity_sync_runs` exists (ordering matters for the FK). Existing rows default to `is_active=true` on upgrade.

`downgrade()` still refuses (`RuntimeError`) if either new table is populated — unchanged rule, same rationale (no way to represent run/checkpoint history in the pre-0021 schema). It additionally drops the two new columns (and their index) from all five entity tables. Proven by `test_downgrade_refuses_when_either_new_table_is_populated` and `test_downgrade_succeeds_and_removes_entity_table_columns_when_empty`.

Revising migration 0021 in place (rather than adding 0022) was correct here since it remains unmerged — no real deployment has ever applied the first-pass schema.

## 4. Clean / partial / failed state table

| Terminal status | Meaning | Persists accepted items? | Advances checkpoint? | Runs reconciliation? |
|---|---|---|---|---|
| `succeeded` | Every observed item was schema-valid, in a supported state, and had a fully coherent parent chain | Yes (all of them) | Yes | Yes (deactivate/reactivate) |
| `partial` | At least one item was schema-rejected, unsupported-state, missing a parent, or had a mismatched parent — but at least one item *was* accepted at the parse level (`total_accepted > 0`) | Yes, for whichever items resolved coherently | **No** | **No** |
| `failed` (`entity_sync_contract_mismatch`) | The fetch observed items but accepted none of them at the parse level (`total_accepted == 0`, nonempty) | No | No | No |
| `failed` (other classes) | Auth/invalid-request/parse/page-limit/cyclic-token/disabled-gate/profile-or-connection-missing | No | No | No |
| `waiting_to_retry` → eventually `failed` | Transient (rate-limited, transport failure, LWA refresh failure) | No | No | No |
| `timed_out` | Stale lease reclaimed — always terminal, never auto-resumed | No | No | No |

`is_clean` (the exact boolean deciding `succeeded` vs. `partial`) is `items_schema_rejected == 0 and items_unsupported_state == 0 and items_missing_parent == 0 and items_mismatched_parent == 0`. An empty page set (`total_observed == 0`) is clean by definition — proven by `test_empty_clean_snapshot_deactivates_all_existing_active_rows`.

## 5. Transaction and lease design (unchanged from first pass)

- **Claim**: PostgreSQL advisory lock (`991_004_005`) + fenced `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED)`. `max_global_active` caps total concurrent runs; no per-profile cap (not requested, flagged in §11).
- **Stale-lease recovery**: always terminalizes to `timed_out`, unconditionally — restarting a paginated fetch from page 1 is always safe.
- **Pre-call fencing**: `_renew_lease_or_raise` before every external call.
- **Persist-phase atomicity**: `_persist_snapshot` is the only transaction per run — upserts, the clean/partial decision, reconciliation (when clean), the fenced `mark_succeeded`/`mark_partial`, and the guarded checkpoint advance all happen inside one `session_scope()` block. A lost lease at `mark_succeeded`/`mark_partial` rolls back everything staged earlier in that same transaction. Proven at the SQLite/unit level (`test_lease_loss_before_completion_rolls_back_the_whole_persist_transaction`) and against real PostgreSQL (`test_persist_and_checkpoint_advance_are_atomic_with_the_ownership_check`).

## 6. Exact parent-resolution rules

`_resolve_parents(session, organization_id, ads_profile_id, config, dto)` returns one of `"ok"`, `"missing"`, `"mismatched"` — it persists nothing itself.

- Entities with no parent (`campaign`): always `"ok"`.
- Entities with one parent (`ad_group`, parent = campaign): looks up the campaign via `get_by_external_id(ads_profile_id, dto.campaign_id)`. Not found (or, defensively, a resolved row whose `organization_id`/`ads_profile_id`/`external_campaign_id` don't match exactly what was asked for) → `"missing"`. No mismatch concept applies — there is only one parent to resolve.
- Entities with two parents (`product_ad`, `keyword`, `product_target`; parents = campaign AND ad group):
  1. Resolve the campaign via `get_by_external_id(ads_profile_id, dto.campaign_id)`. Not found → `"missing"`.
  2. Resolve the ad group via `get_by_external_id(ads_profile_id, dto.ad_group_id)`. Not found → `"missing"`.
  3. **The check the first pass omitted**: `ad_group_row.ads_campaign_id == campaign_row.id`. If the resolved ad group's actual parent campaign is not the resolved campaign — both exist, both were found independently, but they are not the same hierarchy branch — → `"mismatched"`.
  4. Otherwise → `"ok"`.

Every lookup is scoped by `ads_profile_id`, which is itself scoped to exactly one organization by its own foreign key — so a same-named external id under a different profile or organization never resolves at all (proven by `test_same_external_ids_under_a_different_profile_never_resolve`). `"missing"` and `"mismatched"` are counted separately (`items_missing_parent` vs. `items_mismatched_parent`) and recorded via `AmazonAdsSyncErrorRepository` under distinct error codes (`entity_sync_missing_parent` / `entity_sync_mismatched_parent`); neither case ever persists that item.

Tests: `test_valid_coherent_campaign_ad_group_pair_persists_a_keyword_cleanly` (ok), `test_ad_group_belonging_to_a_different_campaign_branch_is_mismatched_not_persisted` (mismatched, real second branch, zero persistence), `test_same_external_ids_under_a_different_profile_never_resolve` (missing, cross-profile isolation), `test_ad_group_with_missing_parent_produces_partial_not_succeeded` (missing, single-parent case).

## 7. Deactivation/reactivation semantics

No `last_seen_at` timestamp comparison is used (the first pass's `reconciliation_stale_count` did use one; final review replaced the whole mechanism, not just its output). Instead, only inside the clean-success (`is_clean`) branch:

1. `touched_ids` — the set of local row ids this run's own upserts touched (already tracked for the parent-resolution loop).
2. **Reactivate**: `UPDATE <entity table> SET is_active=true, last_seen_entity_sync_run_id=<run_id> WHERE id IN (touched_ids) AND is_active=false` — the `rowcount` of this statement is `items_reactivated` by construction (it only affects rows that were inactive and are now active).
3. Every touched row (whether just reactivated or already active) gets `last_seen_entity_sync_run_id` stamped with this run's id via a second, unconditional update over `touched_ids`.
4. **Deactivate**: `UPDATE <entity table> SET is_active=false WHERE ads_profile_id=<profile> AND is_active=true AND id NOT IN (touched_ids)` — `rowcount` is `items_deactivated`.

Never mutates or deletes Amazon's own `state` column; `is_active` is a wholly separate, application-owned signal. Scoped by `ads_profile_id` only — profile already fully implies organization and connection via its own foreign keys, so no additional scope column was needed on the five entity tables themselves. An empty snapshot (0 items) deactivates every currently-active row in scope, by design (proven by `test_empty_clean_snapshot_deactivates_all_existing_active_rows`). A previously-inactive row reappearing in a later clean snapshot is reactivated (`test_reactivation_of_a_previously_inactive_row_on_a_later_clean_snapshot`). Deactivation never crosses profiles (`test_deactivation_never_leaks_across_profiles`). An incomplete or failed run (page-limit, cyclic token, contract mismatch, disabled gate, lease loss, retry) never reaches this code at all — proven directly by `test_incomplete_run_never_deactivates_anything` (a pre-existing active row survives a page-limit failure untouched) and by the `is_clean`-gated control flow itself (reconciliation is physically inside the `if is_clean:` branch of `_persist_snapshot`, not merely skipped by a separate condition).

## 8. Checkpoint authorization rules

`AmazonAdsEntitySyncCheckpointRepository.advance(organization_id, ads_connection_id, ads_profile_id, *, entity_type, synced_at, run_id)` no longer trusts its caller. It first runs a `SELECT` whose `WHERE` clause requires, all at once, inside the same transaction as the caller's `mark_succeeded`:

- `AmazonAdsEntitySyncRun.id == run_id`
- `AmazonAdsEntitySyncRun.organization_id == organization_id`
- `AmazonAdsEntitySyncRun.ads_connection_id == ads_connection_id`
- `AmazonAdsEntitySyncRun.ads_profile_id == ads_profile_id`
- `AmazonAdsEntitySyncRun.entity_type == entity_type`
- `AmazonAdsEntitySyncRun.status == "succeeded"`

If no row matches, it raises `ValueError` — never a silent no-op, never an advance anyway. A `'partial'`, `'failed'`, `'timed_out'`, `'queued'`, `'started'`, or `'waiting_to_retry'` run can never advance a checkpoint through this method, and neither can a real `'succeeded'` run id used with the wrong organization/connection/profile/entity_type. Proven directly — including under real PostgreSQL — by `test_checkpoint_advance_rejects_a_run_that_is_not_cleanly_succeeded` / `test_checkpoint_advance_rejects_a_foreign_profile_scope` (SQLite) and `test_checkpoint_advance_rejects_a_non_succeeded_or_foreign_scope_run` (PostgreSQL).

`enqueue_sync_request` independently validates that the requested profile belongs to the requesting organization (via `AmazonAdsProfileRepository.get_owned`) before creating anything, and resolves the profile's own `connection_id` itself — a run's scope is established at creation time, never taken on faith from a caller.

## 9. Additional hardening — exception sanitization

The one broad `except Exception` in this module (around the LWA token refresh call, which wraps an underlying HTTP client whose exception text is not this codebase's own sanitized type) no longer persists `str(exc)`. It now persists a fixed diagnostic string (`"Amazon Ads LWA token refresh failed (see run_id for correlation)."`) — the real exception is never written to `failure_detail`, `amazon_ads_sync_errors`, or any log line. Every other failure path in this module already used this module's own typed internal exceptions or `app.core.exceptions`'s `Ads*` exceptions, both already-established as sanitized. Proven by `test_secret_shaped_exception_never_reaches_failure_detail_or_sync_errors`, which injects a synthetic secret-shaped string into a raised exception and asserts it appears in neither the run's `failure_detail` nor any recorded `amazon_ads_sync_errors` row.

## 10. Pagination safeguards (unchanged from first pass)

- `page_size` passed explicitly from `settings.ads_entity_list_page_size`.
- `nextToken` followed exactly per PR B1's truth table.
- Cyclic-token detection via an in-memory set; the token itself is never logged or persisted (`test_cyclic_pagination_token_fails_without_looping_forever_or_leaking_the_token`).
- Bounded page count (`ads_entity_sync_max_pages`); exceeding it fails visibly, zero rows persisted.
- Aggregate contract mismatch (`total_accepted == 0`, nonempty) fails permanently before reaching `_persist_snapshot` at all.

## 11. Endpoint confidence / activation matrix (unchanged from first pass)

| Entity type | Blueprint confidence (§0.1, per doc 23/24) | `ads_entity_sync_<type>_enabled` default |
|---|---|---|
| campaign | Officially documented, live-confirmed | `True` |
| ad_group | Officially documented, not production-confirmed | `False` |
| product_ad | Media type documented; envelope key UNCONFIRMED | `False` |
| keyword | Media type documented; envelope key AND field shape UNCONFIRMED | `False` |
| product_target | Media type documented; envelope key AND field shape UNCONFIRMED | `False` |

## 12. Observability and failure classes

Persisted per run: `status` (now including `'partial'`), `attempt_count`, `pages_processed`, `items_observed`, `items_accepted`, `items_schema_rejected`, `items_unsupported_state`, `items_missing_parent`, `items_mismatched_parent`, `items_deactivated`/`items_reactivated` (only on a clean `'succeeded'` run), `failure_class`, `failure_detail`.

Failure classes: `entity_sync_disabled_for_entity_type`, `profile_missing`, `connection_missing`, `secret_missing`, `token_refresh_failed`, `entity_sync_page_limit_exceeded`, `entity_sync_cyclic_pagination_token`, `entity_sync_contract_mismatch`, `entity_sync_envelope_contract_mismatch`, `entity_sync_authentication_failed`, `entity_sync_invalid_request`, `entity_sync_rate_limited`, `entity_sync_request_failed`. `'partial'` runs carry no `failure_class` — it is a distinct terminal status, not a failure.

Never logged or persisted anywhere: access tokens, refresh tokens, pagination tokens, raw response bodies, campaign/ad-group/keyword names or targeting expressions/search terms, seller identifiers, or an unsanitized broad-exception message.

## 13. Tests and CI results

- `tests/test_amazon_ads_entity_sync_service.py` — **22 tests** (was 11): the original 6 structural tests (multipage, idempotent replay, cyclic token, page limit, contract mismatch, disabled gate) plus 16 new/revised — coherent-parent success, mismatched-parent (real second branch), cross-profile non-resolution, missing-parent partial status, schema-rejected partial, unsupported-state partial, lease-loss rollback, empty-snapshot full deactivation, reactivation, cross-profile deactivation isolation, incomplete-run-never-deactivates, repository-level stale-lease-always-terminal (with connection scope), repository-level enqueue idempotency (with connection scope), guarded-checkpoint-advance-rejects-partial, guarded-checkpoint-advance-rejects-foreign-scope, secret-shaped-exception sanitization.
- `tests/postgres/test_disposable_postgres_ads_entity_sync_migration.py` — **8 opt-in tests** (was 6): schema/column shape (including the new columns and `'partial'` status), existing-rows-default-active, downgrade refusal when populated, downgrade success + column removal when empty, stale-lease-always-terminal under real concurrency, guarded-checkpoint-advance rejection under real concurrency, deactivate/reactivate with tenant isolation under real PostgreSQL, persist/checkpoint atomicity under real PostgreSQL.
- `tests/test_migration_chain_matches_orm_metadata.py` / `tests/test_amazon_seller_identity_schema.py`: unchanged from first pass, still pass with no drift (the migration/ORM edits in this revision keep both in lockstep — verified directly after each schema edit).
- Full backend suite: **2248 passed, 113 skipped**, 0 failed.

## 14. Remaining contract uncertainties

- Ad-group, product-ad, keyword, and product-target contracts remain exactly as UNCONFIRMED as PR B1 left them — this revision adds coherence/reconciliation/scope correctness around those contracts but performs no live call against any of them.
- `ads_entity_sync_max_global_concurrent_runs` still has no per-profile equivalent — not requested, flagged as a candidate follow-up.
- The five-endpoint evidence matrix's own unresolved items (client-ID header-name conflict, `Amazon-Ads-AccountId` requirement, response-schema gaps for three endpoints) are unchanged — PR B1/blueprint-level uncertainties, not something this orchestration layer resolves.
- `is_active`'s scope is `ads_profile_id` only (no `ads_connection_id` column was added to the five entity tables) — correct today since a profile belongs to exactly one connection via its own foreign key, but worth flagging if that invariant ever changes.

## 15. Proposed live-validation sequence (not performed by this PR)

1. Apply migration 0021 to a non-production database first; confirm `alembic current` and run the disposable-Postgres suite against it.
2. With `ADS_API_BACKEND` still disabled, deploy this code with no behavioral change (dead code path).
3. In a controlled environment with real Ads credentials, enable `ADS_API_BACKEND` and `ads_entity_sync_campaigns_enabled` only; enqueue a single campaign sync for one profile; inspect the resulting run row's counts (including `items_deactivated`/`items_reactivated`) before persisting more broadly.
4. Only after campaign sync is validated against a real response, consider independently validating one sibling entity type at a time, enabling its gate only after a real response confirms its envelope key and field shape.

## 16. Confirmation

No production deployment, migration application, Railway change, Ads worker/scheduler, OAuth flow, live Amazon or SP-API call, or OpenAI call was made or modified by this PR. `ADS_API_BACKEND` remains disabled. No frontend code was touched. No Amazon mutation-shaped operation exists anywhere in the new/revised code.

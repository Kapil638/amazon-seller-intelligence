# 12B.6B — FBA Inventory Ingestion

Durable record of the 12B.6B implementation pass. Branch:
`milestone-12b6b-fba-inventory`, created in an isolated worktree from
verified `origin/main` at `e71f5cb`, deliberately kept separate from the
session's dirty primary working tree (unrelated, untouched OPS.1/NVIDIA-
NIM proposal docs and `README.md`/`docs/adr/README.md` modifications —
never staged, edited, or committed by this milestone). Implement/test/
document/commit only — no push, no live Amazon call, no worker start
against the connected seller, no backfill, no Supabase/shared-database
migration, no production-data mutation, no live role probe, no
unattended scheduling, and no OPS.1 implementation were authorized or
performed while producing this milestone.

## 0. Governing correction: snapshot grain

An earlier draft proposed one snapshot per SKU per UTC calendar day,
written with `ON CONFLICT DO NOTHING` — silently discarding every later
same-day successful observation after the first. **Rejected and
corrected** before implementation began: immutable observations are
keyed by `(ingestion_run_id, marketplace_participation_id, seller_sku,
condition)`, never by calendar day. Every successfully reconciled run
records its own observation set; no later successful run's observations
are ever overwritten or discarded by an earlier one the same day.
Retention/rollups are deliberately deferred to a later milestone — this
one simply never loses data. See §4 and §8.

## 1. Official contract, pinned

**Source of truth:** `amzn/selling-partner-api-models`, commit
`cca8d338b6dc56afd3f9fdb822d2125a80ae8090`, file
`models/fba-inventory-api-model/fbaInventory.json`, fetched directly
(not inferred from Seller Central, community answers, or memory).
**SHA-256 of the exact fetched file:**
`7c14bcdb22de8ca2df45e5a40f2a422cff344d45985a68b9515b2e800edcc5ab`.
Recorded in `app/amazon/inventory_models.py`'s and
`app/amazon/inventory_client.py`'s own module docstrings, which every
specific claim below was re-verified against directly (a Swagger 2.0
document).

**Operation:** `GET /fba/inventory/v1/summaries` (`getInventorySummaries`),
FBA Inventory API v1.

**Required vs. optional, verified directly against the pinned model:**
`InventorySummary` itself has **no `required` list at all** — `asin`,
`fnSku`, `sellerSku`, `condition`, `productName`, `totalQuantity`,
`lastUpdatedTime`, `stores`, and `inventoryDetails` are every one
independently optional. The same is true of `InventoryDetails`,
`ReservedQuantity`, and `UnfulfillableQuantity` — none of their
properties are required. The one nested object that *does* declare
`required` is `ResearchingQuantityEntry` (`name`, `quantity`, both
required when an entry is present at all), and the top-level
`GetInventorySummariesResult` (`granularity`, `inventorySummaries`, both
required, though `inventorySummaries` may be an empty list).

**Nullability:** Swagger 2.0 has no `nullable`/`x-nullable` keyword
anywhere in this file — "optional" means "the key may be absent," never
"the key may be present with an explicit JSON `null`." Every optional
field is modeled with this repository's existing `optional_not_null()`
helper (`listings_models.py`), reused rather than duplicated — an
explicit `null` is rejected even though a missing key is fine.

**`condition`:** no enum constraint in the pinned contract — plain
`"type": "string"`, modeled as `str`, never a closed `Literal`.

**Quantities:** no field documents a `minimum`/`maximum` — every
quantity is plain `"type": "integer"`. No `ge=0` is added at the Pydantic
layer for that reason (a stricter bound than Amazon's own contract would
risk rejecting a value Amazon is fully entitled to send); non-negativity
is instead an *application-layer* policy (§3).

**`researchingQuantityBreakdown`:** the pinned model's confirmed bucket
names, verbatim: `researchingQuantityInShortTerm`,
`researchingQuantityInMidTerm`, `researchingQuantityInLongTerm`.

**Pagination:** `nextToken` is documented as expiring **30 seconds after
being created** — both the model file's own parameter description and
the official reference page agree. This is the direct evidentiary basis
for the entire ingestion shape decided in §5: a durable, cross-attempt
pagination token would be architecturally unsound for this operation
specifically (contrast Orders, which durably persists its own token
because Orders' token has no such short expiry).

**Marketplace scope:** `marketplaceIds` carries `maxItems: 1` in the
pinned contract — `getInventorySummaries` is called per marketplace,
never coarser. This governs the ingestion run's own scope shape (§4):
Inventory is scoped like Listings/Sales-and-Traffic
(`marketplace_participation_id` + `seller_account_id` both required),
never like Orders' coarser multi-participation association shape.

**`details=true` query parameter:** controls whether `inventoryDetails`
(the reserved/inbound/unfulfillable/researching breakdown) is populated
at all on the response. This client always requests `details=true` — the
milestone's stated goal (a genuine reserved/inbound/unfulfillable
breakdown, not just a bare total) requires it.

## 2. Inventory identity — required correction

An earlier draft proposed finalizing `(seller_account_id,
marketplace_participation_id, seller_sku, condition)` as the natural key
before verifying field optionality against the pinned model. Per §1,
neither `sellerSku` nor `condition` is required on every response row —
finalizing that identity without checking would have risked either
inventing a synthetic identifier for a row Amazon did not identify, or
silently colliding distinct rows onto the same key.

**Corrected identity policy** (`inventory_normalization.py`): a row is
identifiable **if and only if both `sellerSku` and `condition` are
present and non-blank**. `sellerSku` is the seller's own catalog key —
fundamentally how a seller's inventory is addressed at all, even though
this specific response schema does not mark it required. `condition`
distinguishes multiple condition-scoped listings of the same SKU, which
the pinned schema's own `InventorySummary.condition` field exists to
represent. A row missing either is **rejected — never persisted, never
assigned a fabricated key** — and counted via `records_rejected`,
exactly matching how `listings_normalization.py` rejects a whole item on
a genuine data anomaly rather than silently dropping or guessing.
`asin`/`fnSku` are preserved as source *attributes* only, never part of
identity — both may legitimately be absent for a row this module still
accepts.

Final natural key, on both `amazon_seller_inventory` (current state) and
`amazon_seller_inventory_observations` (immutable history):
`(marketplace_participation_id, seller_sku, condition)` — stable across
syncs, never collapses distinct summaries, works when `asin`/`fnSku` are
absent, and avoids PostgreSQL's nullable-column-uniqueness loophole
because `seller_sku`/`condition` are the two columns the application
layer has already guaranteed non-null before a row ever reaches this
table (see §4's `NOT NULL` reasoning).

## 3. Non-negativity — required correction

The pinned schema places no `minimum` on any quantity field — a
database `CHECK (... >= 0)` would be stricter than Amazon's own contract
unless the application deliberately rejects a row that violates it
first. `inventory_normalization.py` is that deliberate rejection point:
any quantity field present with a negative value fails the whole row
(never silently clamped to zero, never silently dropped from a subset of
fields), counted as rejected. The database `CHECK` (§4) is then a
legitimate defense-in-depth backstop a correctly-functioning ingestion
should never actually trip — proven directly under real PostgreSQL by
`tests/postgres/test_disposable_postgres_inventory_migration.py::
test_negative_quantity_rejected_by_real_postgres`, which bypasses the
application layer with raw SQL specifically to exercise it.

## 4. Schema and migration (implemented)

Migration `0016_inventory_foundation`, revises
`0015_worker_heartbeats` — single Alembic head, additive only. Originally
authored as `0015_inventory_foundation` (revising
`0014_sales_traffic_foundation`); renumbered after the independently
developed `fix/ingestion-worker-runtime-availability` cross-cutting fix
merged first and claimed revision `0015` for its own
`amazon_worker_heartbeats` migration — see that migration file's own
docstring, and `0016_inventory_foundation.py`'s own renumbering note.

**Extends `amazon_ingestion_runs`**, exactly as 12B.3D/12B.6A did:

- Widens `ck_amazon_ingestion_runs_run_type` to add `'inventory'`.
- `ck_amazon_ingestion_runs_inventory_scope_required`: an `'inventory'`
  row is scoped like Listings/Sales-and-Traffic
  (`marketplace_participation_id`/`seller_account_id` both `NOT NULL`),
  never like Orders — per §1's `marketplaceIds` `maxItems: 1` finding.
- `uq_amazon_ingestion_runs_active_inventory_scope`: the Inventory
  equivalent of the existing single-writer partial unique index, scoped
  to `(seller_account_id, marketplace_participation_id)`, covering
  `queued`/`started`/`waiting_to_retry` together.

**No new columns are added to `amazon_ingestion_runs`.** Inventory
reuses the fully generic `pages_fetched`/`reported_total_results`/
`pagination_complete`/`records_received`/`records_accepted`/
`records_rejected` columns that already exist — this ingestion is
Listings-shaped (in-memory accumulate-then-reconcile, no durable
cross-attempt pagination token), never Orders-shaped or
Sales-Traffic-report-shaped, so none of those domains' own dedicated
columns apply.

**New tables, in dependency order:**

1. `amazon_seller_inventory` — canonical current state, one row per
   `(marketplace_participation_id, seller_sku, condition)`. No
   `organization_id`/`seller_account_id` column, matching
   `amazon_seller_listings`'s own documented design — ownership is
   derived solely through `marketplace_participation_id`. `seller_sku`/
   `condition` are `NOT NULL` even though Amazon's own pinned schema
   does not require either on every response row: `inventory_
   normalization.py` rejects and counts any row missing either *before*
   persistence (§2), so this `NOT NULL` only proves an invariant already
   enforced above the database — it never rejects Amazon-valid data this
   table would otherwise accept. Every quantity column is nullable with
   a combined non-negative `CHECK` (§3). `is_active` is the within-FBA
   absence flag (§5). No currency column exists (unit counts, not
   money).
2. `amazon_seller_inventory_observations` — immutable per-run history.
   **The corrected design from §0**: natural key includes
   `ingestion_run_id`, not a calendar date.

`downgrade()` refuses (raises) if any row exists in either new table, or
any `amazon_ingestion_runs` row has `run_type='inventory'` — `0014`'s
schema has no way to represent inventory data, and downgrading in that
state would either violate a restored constraint or silently discard
real ingestion evidence.

ORM models: `AmazonSellerInventory`, `AmazonSellerInventoryObservation`
(`app/persistence/models.py`). Repository:
`AmazonSellerInventoryRepository` (`reconcile_snapshot`, the one
validated write boundary), plus Inventory-specific methods on
`AmazonIngestionRunRepository` (`enqueue_inventory_run`,
`claim_next_inventory_job`, `heartbeat_inventory_run`,
`complete_inventory_run`, `reschedule_inventory_run_for_retry`, and
read-only `get_active_inventory_run`/`get_latest_inventory_run`/
`get_latest_cooldown_relevant_inventory_run`/
`get_latest_successful_inventory_run`/
`count_queued_inventory_runs_for_organization`)
(`app/persistence/repositories.py`).

Verified: `tests/test_migration_chain_matches_orm_metadata.py` (36-table
drift parity, offline `--sql` compilation against a real PostgreSQL
dialect with no live database), `tests/test_amazon_seller_identity_
schema.py` (single-head assertion updated to `0016`), `tests/test_amazon_
ingestion_run_inventory_claim.py` (15 tests — enqueue/claim/lease/
heartbeat/completion, stale-lease reclaim via enqueue, claim-time sweep
with nothing to hand out, lease-theft compare-and-set, global and
per-organization concurrency gates, `waiting_to_retry` timing).
Postgres-guarded: `tests/postgres/test_disposable_postgres_inventory_
migration.py` (12 tests — existing-database upgrade preserving data,
expected schema shape, downgrade-clean, downgrade-refuse ×3 across all
three data classes, active-scope uniqueness and claim path under real
concurrency, non-negative `CHECK` enforcement, composite-FK provenance,
the corrected same-day-multiple-observations grain under a real unique
constraint; skip locally — no Docker/PostgreSQL binary available in this
authoring environment — exercised by CI's `postgres-identity-
concurrency` job on the next push). CI: new
`existing-database-upgrade-0016` job added to `backend-database-ci.yml`;
the fresh-install job's expected-head assertion updated to
`0016_inventory_foundation`.

## 5. Full-sweep-only deactivation and pagination timing — required
corrections

**Absence policy** (`AmazonSellerInventory.is_active`): a row absent
from a later complete `getInventorySummaries` sweep is **never treated
as zero stock**. Amazon's contract does not state that a zero-quantity
SKU continues to appear, nor that disappearance implies zero — genuinely
undocumented either way. A row absent from the latest complete traversal
is marked `is_active=False` with every quantity column left at its
last-known value, never zeroed. This flag may only flip after a run
satisfies every one of:

1. Every page was fetched successfully.
2. Traversal ended naturally — no further `nextToken`.
3. Pagination stayed within the configured safety bound
   (`inventory_sync_max_pages`, default 500 — ASI-invented, since Amazon
   documents no ceiling; configurable, not an arbitrarily low limit that
   would make large catalogs impossible).
4. The final reconcile transaction committed successfully.

A timeout, token expiry, malformed page, max-page breach, or partial
traversal at any point leaves current-state activation **unchanged** —
`inventory_ingestion.py`'s `_traverse()` accumulates entirely in memory
and only calls `_reconcile()` (which calls
`AmazonSellerInventoryRepository.reconcile_snapshot`, the sole path to
deactivation) when `traversal.failure_class is None`. Proven directly:
`tests/test_amazon_inventory_ingestion.py::
test_missing_sku_on_later_full_sweep_is_deactivated_not_zeroed`,
`test_max_page_breach_is_terminal_and_writes_nothing`, and every
retryable-failure test asserting zero repository writes.

**Pagination timing:** per §1's confirmed 30-second `nextToken`
lifetime, the ingestion service consumes each token **immediately,
sequentially** — no delay is ever inserted between receiving a token and
using it beyond the defense-in-depth proactive throttle (§6), which is
bounded well under 30 seconds by configuration
(`inventory_worker_min_page_interval_seconds`, default 0.5s). On token
expiration or any mid-traversal retryable failure (`throttled`,
`transient_request_failed`, `malformed_page`), the in-memory partial
traversal is discarded entirely — no current inventory row, no
observation row, nothing is written — and the run is rescheduled
(`reschedule_inventory_run_for_retry`) to restart from page one on the
next attempt. `pagination_bound_exceeded` (the max-page-guard breach) is
deliberately **excluded** from `RETRYABLE_INVENTORY_FAILURE_CLASSES` — a
terminal, non-retryable failure with a clear message, since retrying an
identical bounded traversal against a catalog that genuinely exceeds the
configured page ceiling would fail identically forever without an
operator raising the bound.

## 6. Rate limiting — required correction

The shared SP-API client machinery (`inventory_client.py`) was inspected
directly before assuming correct behavior, mirroring
`listings_client.py`'s already-established pattern rather than inventing
a new one: bounded 429/5xx/transport retry with `Retry-After` honored
when Amazon returns it (read defensively; no documented `Retry-After`
guarantee either way was found specifically for this operation during
the audit, so absence yields `None` and falls back to the configured
base backoff, exactly like every other client in this codebase);
`x-amzn-RateLimit-Limit`/`x-amzn-RequestId` response headers captured
into sanitized provenance (never logged raw, never a token or payload);
401/403 fold into `SpApiAuthenticationError` (never retried — a missing
role or invalid grant cannot be fixed by retrying). The proactive
per-page sleep (`inventory_worker_min_page_interval_seconds`) is
**defense-in-depth only, never the sole rate-limit mechanism** — the
actual authority is `inventory_client.py`'s reactive handling of a real
429/`Retry-After`, exactly as `app/core/config.py`'s own comment on this
setting states. Never a hard-coded sleep pattern as the sole mechanism.
Operation/seller-specific usage-plan variation remains compatible: the
client reacts to whatever Amazon actually returns rather than assuming a
fixed, hard-coded budget.

## 7. Atomic finalization and status semantics — required corrections

`AmazonSellerInventoryRepository.reconcile_snapshot` and the run's
terminal `succeeded` transition happen inside **one transaction**,
called once by `AmazonInventoryIngestionService._reconcile()` after a
genuinely complete traversal: upsert the complete current-state
inventory set, reactivate reappearing rows, deactivate rows missing from
the complete sweep, insert immutable per-run observations, update run
counters, mark pagination complete, terminalize the run as `succeeded`.
If any part fails (proven with a real database-constraint violation, not
a mocked failure), none of it commits — the run's lease simply expires
and becomes reclaimable.

**`complete_inventory_run` raises `TypeError` if ever called with
`status="partial"`** — accumulate-then-reconcile means a traversal
either reconciles completely (producing a full, internally-consistent
new inventory state) or produces no new inventory state at all; there is
no genuine partial-success outcome for this domain to represent.
`InventorySyncStatus` (the read-API/UI-facing enum, `inventory_read.py`
and `apps/web/src/lib/types.ts`) has no `"partial"` value at all —
verified by grep across both layers, not merely by convention.

## 8. Corrected observation grain, proven under real concurrency

Per §0: `amazon_seller_inventory_observations`' natural key is
`(ingestion_run_id, marketplace_participation_id, seller_sku,
condition)`. Two distinct successful runs the same calendar day each
produce their own observation row for the identical
`(participation, SKU, condition)` — neither is discarded. A retried
reconcile attempt for the exact same run is a silent no-op (a `SAVEPOINT`
around the insert catches the real unique-constraint violation), never a
duplicate row or a crash. Proven at the SQLite level
(`test_amazon_inventory_ingestion.py`) and, more importantly, under
**real PostgreSQL** —
`tests/postgres/test_disposable_postgres_inventory_migration.py::
test_same_day_multiple_successful_observations_are_never_collapsed_
on_real_postgres` inserts two independently-completed runs the same day,
confirms two distinct observation rows with the correct per-run values,
then retries the first run's reconcile and confirms the row count stays
at one for that run — the real unique constraint enforcing it, not
merely SQLite's equivalent-but-not-identical evaluation.

## 9. Amazon FBA Inventory client (implemented)

`app/amazon/inventory_client.py` — `AmazonSpApiInventoryClient`,
`InventoryPageRequest`. Fetches and parses exactly one official page per
call; never traverses pages itself (no loop over `nextToken` inside this
file); never acquires an ingestion-run lease, creates an ingestion run,
writes an inventory row, deactivates anything, or decides observation
authority; never accesses a repository or database session; exposes no
HTTP route. **No seller ID in the URL** — unlike Listings Items,
`getInventorySummaries` is a pure query-string request
(`marketplaceIds`, `granularityType`, `granularityId`, `details`,
`nextToken`), so there is no path segment requiring the seller-ID
log-redaction filter Listings needs. Mirrors `listings_client.py`'s
retry/backoff/`_parse_retry_after` machinery exactly.

Verified: `tests/test_amazon_inventory_client.py` (11 tests).

## 10. Durable lifecycle and worker (implemented)

`app/amazon/inventory_ingestion.py` —
`AmazonInventoryIngestionService.process_claimed_job`, the single entry
point the worker calls, one claimed run per invocation. `_traverse()`
accumulates every page's normalized observations in memory (§2/§3 reject
individual rows without failing the whole page), heartbeats between
pages (with lease renewal during a slow in-flight request), and only
calls `_reconcile()` (§7) once traversal ends with `failure_class is
None`. `RETRYABLE_INVENTORY_FAILURE_CLASSES = frozenset({"throttled",
"transient_request_failed", "malformed_page"})` — deliberately excludes
`pagination_bound_exceeded` (§5) and any authentication/invalid-request
class (never transient).

`app/amazon/inventory_worker.py` — `InventoryWorker`, a dedicated
process (never merged with `listings_worker.py`/`orders_worker.py`/
`sales_traffic_worker.py`), gated by `ASI_INVENTORY_WORKER_ENABLED`
(independent of every other worker's own gate), `main()` sets
`ASI_DB_RUNTIME_CONTEXT = "inventory_worker"` only *after* the enable
check passes — added to `_RECOGNIZED_DB_RUNTIME_CONTEXTS`
(`app/persistence/database.py`) so this worker fails closed against any
non-loopback `DATABASE_URL` exactly like every other worker. Never
started in this session — `ASI_INVENTORY_WORKER_ENABLED` was never set,
no worker process was ever run, no live Amazon call was made.

`scripts/dev.sh` — new `SKIP_INVENTORY_WORKER` gate, duplicate-process
detection, `DEV_SH_INVENTORY_WORKER_CMD` override for deterministic
testing, `start_child "inventory-worker" ...`.

Verified: `tests/test_amazon_inventory_ingestion.py` (14 tests — happy
path single/multi-page, per-row identity/negative-quantity rejection,
full-sweep-only deactivation preserving quantities, max-page breach
writes nothing, every retryable/terminal failure class, retry-budget
exhaustion, proactive-throttle invocation), `tests/test_amazon_inventory_
worker.py` (26 tests — claim/process loop, per-organization concurrency
limit, throttled reschedule, unexpected-exception resilience, graceful
shutdown, the enable-gate, `ASI_DB_RUNTIME_CONTEXT` declaration
ordering, and log sanitization — neither the enable-flag env var value
nor any organization/seller/connection identifier or SKU ever appears in
a log record), `scripts/test_dev_sh.sh` (updated six-children
orchestration test plus a dedicated enable-only-inventory-worker test).

## 11. Read APIs and sync trigger (implemented); UI (implemented)

**Read API** — `app/amazon/inventory_read.py`
(`AmazonInventoryReadService`) + `app/api/routes/amazon_inventory.py`,
strictly read-only, organization-scoped via `current_organization_id()`.
`InventorySyncStatus` never includes `"partial"` (§7). Summary/list/
detail endpoints; `_inbound_total()` sums the three-way inbound split
(`working`/`shipped`/`receiving`) as a display convenience only, never
recomputing or overwriting the stored `total_quantity`.

**Sync trigger** — `app/amazon/inventory_sync.py`
(`AmazonInventorySyncTriggerService`) + `app/api/routes/
amazon_inventory_sync.py`, mirroring `listings_sync.py`'s shape:
`POST .../inventory/sync` enqueues or reports the caller's existing job
(`already_running`/`cooldown`/`scope_not_found`/`scope_inactive`/
`connection_unresolvable`); `GET .../inventory/sync/{run_id}` reports
sanitized progress. Neither route calls Amazon or resolves a secret —
only `inventory_worker.py` does that, out of band. New
`inventory_sync_trigger_cooldown_seconds` setting (default 300s).

**UI** — `apps/web/src/components/seller-inventory.tsx` + `apps/web/
src/app/seller/inventory/page.tsx`, adapted from `seller-sales-
traffic.tsx`'s adaptive-backoff-polling pattern. A **persistent,
always-visible** "FBA-fulfilled inventory only" disclosure banner — this
table has no visibility into merchant-fulfilled (MFN) stock, so a SKU's
absence here may simply mean it is merchant-fulfilled, never that it has
zero stock (distinct from, and in addition to, the within-FBA `is_active`
absence policy in §5). Local nav tab labeled **"FBA Inventory"**, never
bare "Inventory," for the same reason — added to `seller-local-nav.tsx`
as a page-local tab (never a new global header tab, matching the
existing Orders/Sales-and-Traffic precedent). An inactive row is shown
dimmed with a "Not confirmed" badge and tooltip, quantities preserved at
their last-known value, never zeroed.

Verified: `tests/test_amazon_inventory_sync_trigger.py` (9 tests),
`tests/test_amazon_inventory_read_service.py` (7 tests), `tests/
test_amazon_inventory_sync_api.py` (14 tests), `tests/test_amazon_
inventory_api.py` (11 tests), `apps/web/src/components/seller-local-
nav.test.tsx` (FBA Inventory tab assertions),
`apps/web/src/components/seller-inventory-ui.test.tsx` (11 tests — FBA-
only disclosure, every sync state, inactive-row-preserves-quantities,
trigger success/rejection, search, no-marketplace empty state).

## 12. Worker/CI/production authorization gates (explicit)

No migration was applied to Supabase or any shared database. No live
Amazon call, seller reconnection, role change, or backfill was
performed or authorized by this pass. `ASI_INVENTORY_WORKER_ENABLED` was
never set; no worker process was started. The new
`existing-database-upgrade-0016` CI job and the Postgres-guarded
migration/concurrency test file are both written and reasoned through,
but — like every prior milestone's own Postgres-guarded tests — have not
been executed against a real disposable PostgreSQL instance in this
authoring environment (no Docker/PostgreSQL binary available); they skip
locally and are exercised by CI's `postgres-identity-concurrency` job on
the next push.

## 13. Known limitations, honestly stated

1. No automatic scheduler reads a participation's own state and submits
   the next sync on its own — the sync-trigger service is caller-
   supplied-window only (a manual/ad-hoc resync, or a future scheduled
   job calling the same trigger service). Not built in this pass.
2. No historical retention/rollup policy exists for
   `amazon_seller_inventory_observations` yet — every successful run's
   observations accumulate indefinitely (§0). Deliberately deferred, per
   the audit approval's own instruction, to a later milestone.
3. Amazon's contract does not state whether a zero-quantity SKU
   continues to appear in `getInventorySummaries`, nor whether
   disappearance implies zero stock — genuinely undocumented either way
   (§5). This repository's own policy (never treat absence as zero) is a
   documented, conservative choice, not a fact sourced from the
   contract.
4. This table has no visibility into merchant-fulfilled (MFN) inventory
   at all — surfaced prominently in the UI (§11), never silently
   implied.
5. `inventory_sync_max_pages` (default 500) is an ASI-invented safety
   bound, not an Amazon-documented ceiling — a catalog that genuinely
   exceeds it produces a clear terminal failure requiring an operator to
   raise the configured bound, never a silent truncation.

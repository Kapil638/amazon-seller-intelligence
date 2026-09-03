# 12B.4D — End-to-End Orders Ingestion, Read API, Seller Hub UI Foundation, and Copilot Skill Matrix

Durable record of the 12B.4D implementation pass. Branch:
`milestone-12b4d-orders-ingestion-ui`, created from verified `main`
(`46cdd378b842cd4f12c3cd3ef0e5398a16a7f77f`, the genuine two-parent merge
of PR #13 / `milestone-12b4c-orders-client`). No live Amazon call, no
Supabase mutation, no live worker deployment, no migration, and no
commit/push occurred while producing this milestone.

## 1. Phase 0 — verified merge and preserved baseline

- `46cdd378b842cd4f12c3cd3ef0e5398a16a7f77f` confirmed as a genuine
  two-parent merge commit (`git log -1 --format="%H %P"` → parents
  `064c2862...` and `f18ddff4...`, the exact 12B.4C branch tip).
- `f18ddff4...` confirmed a real ancestor of `origin/main`
  (`git merge-base --is-ancestor`).
- All 7 required CI checks passed on the merged tip (Backend database CI,
  fresh-install migration, 4× existing-PostgreSQL-upgrade jobs, OAuth
  identity concurrency validation, backend test suite + migration/model
  drift check) — verified via the GitHub REST API's check-runs endpoint.
- Local `main` fast-forwarded to `origin/main`; both confirmed equal.
- 12B.4C's Orders client (`app/amazon/orders_client.py`) and the literal
  `getOrder` fixture confirmed present; Alembic confirmed at single head
  `0012_orders_foundation`; live Supabase confirmed at the same revision;
  no Listings/Orders worker process running; no active Orders ingestion
  job in the database.
- The seven Log Analyzer paths (`docs/adr/README.md` modified;
  `docs/adr/0007-...md`, `docs/adr/0008-...md`,
  `docs/operations/OPS1_*.md` × 4 untracked) were checksummed before this
  milestone and re-verified byte-identical afterward. None were staged,
  modified, moved, or included in any commit.

## 2. Durable pagination and recovery design (Phase 2 decision)

**Verified against the actual `AmazonIngestionRun`/
`AmazonIngestionRunMarketplaceParticipation`/`AmazonOrdersSyncCheckpoint`
models:**

Already durable in 0012, reused as-is (no migration needed):

- Requested marketplaces → one row per participation in
  `amazon_ingestion_run_marketplace_participations`.
- Retry eligibility time → `next_retry_at`.
- Pages completed / pagination-complete flag → `pages_fetched` /
  `pagination_complete` (both generic columns already on
  `AmazonIngestionRun`, not restricted to `run_type='listings'` by any
  CHECK constraint).
- Orders/items received/accepted/rejected → the 12B.4B-added
  `orders_*`/`items_*` counters.
- Per-participation checkpoint candidates → not a column;
  `finalize_successful_orders_run` already takes
  `participation_watermarks: dict[UUID, datetime]`, so candidates only
  need to live in the ingestion service's in-memory working state for one
  attempt.
- Final checkpoint advancement only after complete success → already the
  entire contract of `finalize_successful_orders_run`.

**Confirmed gap:** no column exists for a mid-traversal pagination
position or a secure reference to the pagination token.
`AmazonOrdersSyncCheckpoint`'s own docstring is explicit and deliberate
about this: "a failed/partial traversal restarts from the unchanged
high-water mark rather than resuming a remembered page, relying on
idempotent upserts and the overlap window for correctness" — adopted
as-is from 12B.4A Phase 4 point 3, which itself requires the raw
pagination token never be persisted, logged, or exposed.

### Decision: keep the restart-from-watermark model; no new migration in 12B.4D

Two options were weighed:

- **Chosen — restart-from-watermark.** On worker restart,
  `_compute_window_start` re-derives `lastUpdatedAfter = min(covered
  participations' checkpoints) - overlap_window` — the *same* value every
  attempt, since the run's exclusive scope lock guarantees the checkpoint
  cannot move while a run is in flight — and re-walks pages from page 1
  of that window. Already-committed orders/items from a prior partial
  attempt are cheaply re-upserted as idempotent no-ops. No data loss, no
  duplicate rows, no schema change. Matches the twice-reviewed 12B.4A →
  12B.4B design intent exactly.
  - Cost: a crash late in a long backfill re-walks earlier pages again on
    restart, burning rate-limit budget before making forward progress —
    an efficiency cost, never a correctness problem.
- **Designed, deferred — persist an opaque pagination-token reference.**
  Add `amazon_ingestion_runs.pagination_token_reference` (nullable
  String), storing an opaque `SecretProvider`-backed reference (mirroring
  the existing `token_reference` pattern for refresh tokens), not the raw
  token. Deferred because: (1) not needed for correctness, only
  rate-limit efficiency on the expected-to-be-rare crash-mid-backfill
  case; (2) adds a new secret-reference lifecycle for a currently
  speculative benefit; (3) purely additive — can be added later with zero
  impact on anything built in this milestone; (4) any new migration is
  gated behind separate live-authorization regardless, so building it now
  would not let it ship sooner in practice.

If operational experience ever shows restart frequency/cost makes Option
B worthwhile, its exact schema is specified above, ready to implement as
its own reviewed increment.

## 3. Persistence and checkpoint atomicity

`AmazonOrdersIngestionService._traverse`/`_persist_page`
(`app/amazon/orders_ingestion.py`) upserts every order/item on **one
page** in a single short transaction, immediately after that page is
fetched — never accumulated in memory across pages the way Listings'
full-catalog-resync model does. This is what makes "previously committed
pages remain available after interruption" true by construction.

- **Multi-marketplace attribution:** one shared `searchOrders` call
  covers every included participation's marketplace at once (12B.4A
  Phase 4 point 11's efficiency argument). Each order is attributed via
  `Order.sales_channel.marketplace_id` — except when the run covers
  exactly one participation, where attribution is unambiguous regardless
  of whether that optional field is present. An order that cannot be
  safely attributed (multi-participation run, missing/unmatched
  marketplace id) is **rejected and counted**, never guessed, never
  failing the whole page.
- **Missing seller SKU:** `ItemProduct.seller_sku` is documented optional
  on the pinned contract, but `amazon_seller_order_items.seller_sku` is
  `NOT NULL`. An item with no SKU is rejected and counted
  (`items_rejected`); its order is still persisted with whatever fields
  are available — never a fabricated SKU, never a whole-order/whole-page
  failure for it.
- **Checkpoint candidate watermark:** per covered participation, the
  highest `amazon_last_updated_at` actually committed — or, for a
  participation that received zero orders in a fully-completed sweep, the
  request's own start time (12B.4A Phase 4 point 9: a completed sweep
  proves nothing was missed up to when it started, not only up to the
  last order actually seen).
- **Finalization:** `_finalize` calls
  `finalize_successful_orders_run` exactly once, only when
  `pagination_complete=True`. On `OrdersRunFinalizationIncomplete`, the
  transaction is never committed — the run's `succeeded` flip and every
  checkpoint already advanced in the same call are rolled back together,
  and the run is then recorded as a sanitized `failed` outcome
  (`finalization_incomplete`) in a fresh, separate transaction.

Proven directly by `tests/test_amazon_orders_ingestion_service.py`
(12 tests): multi-page persistence + checkpoint advancement, multiple
embedded items, idempotent re-upsert + field update on repeat, throttled
→ `waiting_to_retry` honoring `Retry-After`, retry-budget exhaustion →
terminal `rate_limited`, non-retryable failure → immediate terminal,
malformed page → retryable (never a hard crash, never partial
persistence), partial-page rejection counters for an unattributable
order, missing-SKU item rejection with the order still persisted,
multi-marketplace routing to the correct participation, `not_claimed`
fails closed without calling Amazon, and `scope_ambiguous` for
participations spanning inconsistent connections/regions.

## 4. Rate-limit-aware scheduling

`RETRYABLE_ORDERS_FAILURE_CLASSES = {"throttled",
"transient_request_failed", "malformed_page"}` — mirrors
`RETRYABLE_LISTINGS_FAILURE_CLASSES`'s reasoning; Orders has no
"record_count_inconsistent" concept (no documented total-count field on
`SearchOrdersResponse`).

- On `SpApiRateLimitedError`, the traversal ends immediately — **never**
  an in-process sleep for the documented ~178.6s sustained interval. The
  run is moved to `waiting_to_retry` with a durably-stored
  `next_retry_at`, and the lease is released
  (`reschedule_orders_run_for_retry`).
- `_compute_retry_delay` honors a valid `Retry-After` exactly; otherwise
  bounded exponential backoff with full jitter anchored at
  `orders_sync_base_backoff_seconds` (default 180.0s, near the documented
  sustained interval — not a short generic default), capped at
  `orders_sync_max_backoff_seconds` (default 3600.0s, near the documented
  full-burst-refill time).
- New `orders_sync_*`/`orders_worker_*` settings added to
  `app/core/config.py`, mirroring the Listings settings in shape but with
  deliberately larger defaults given Orders' ~900x tighter budget:
  `orders_sync_max_attempts=8`, `orders_sync_max_total_retry_seconds=14400`
  (4 hours), `orders_sync_default_lookback_days=30`,
  `orders_sync_checkpoint_overlap_seconds=1800` (30 minutes, within
  12B.4A's recommended 15–30 minute range).
- Heartbeat renewal (`_renew_lease_while_awaiting`) runs concurrently with
  every single in-flight `search_orders()` call, on a fixed wall-clock
  cadence (`orders_sync_heartbeat_time_interval_seconds`, default 60s) —
  independent of page completion, so the lease cannot expire mid-request
  no matter how long one Amazon call takes. Per-page persistence
  (`_persist_page`) also calls `heartbeat_orders_run` on every page
  transition, exactly matching 12B.4A's explicit requirement.
- Duplicate-trigger/duplicate-run prevention is structural, not
  application logic: `uq_amazon_ingestion_runs_active_orders_scope`
  (already in 0012) makes a second concurrent `queued`/`started`/
  `waiting_to_retry` row for the same `(seller_account, region,
  environment)` scope impossible at the database level.
- A checkpoint is never advanced because a page merely arrived — only
  `finalize_successful_orders_run`, called once per run, ever moves it.

## 5. Worker architecture (Phase 5 design decision)

**Dedicated `OrdersWorker` (`app/amazon/orders_worker.py`), not a single
worker claiming both run types.** `claim_next_listings_job` is a
heavily-reviewed, concurrency-proof piece of SQL (advisory lock +
`FOR UPDATE SKIP LOCKED` single-row claim) whose predicate shape is
specific to Listings' `(seller_account, marketplace_participation)`
scope. Genuinely unifying it with Orders' coarser `(seller_account,
region, environment)` scope into one query would mean modifying
`claim_next_listings_job` itself — risking the explicit "existing
Listings behavior must remain unchanged" requirement. A dedicated worker:

- Keeps `listings_worker.py` (and every one of its existing tests)
  completely untouched — zero lines changed.
- Trivially satisfies "Orders failures must not crash or corrupt
  Listings processing" — different processes, no shared state beyond the
  database.
- Achieves "no starvation between run types" by construction (each type
  has its own dedicated worker capacity) rather than by query
  interleaving; "no starvation between organizations" is enforced within
  each worker's own claim query exactly as it already is for Listings
  (`max_active_per_organization`).

New repository method `claim_next_orders_job` (on
`AmazonIngestionRunRepository`) is structurally identical to
`claim_next_listings_job` — same stale-reclaim step, same
transaction-scoped advisory lock (a distinct key,
`847_539_201_664`, so the two workers' claim decisions never serialize
against each other), same single-row `FOR UPDATE SKIP LOCKED` subquery,
same `started_at`/`retry_count` semantics — grouped by `run_type =
'orders'`. This is a different, broader method than the pre-existing
`claim_orders_run` (which claims for one already-known exact scope,
sufficient for the trigger service but unable to discover *any* eligible
job across the whole system the way a generic worker poll loop needs to).

`ASI_ORDERS_WORKER_ENABLED` is an independent, fail-closed environment
gate from `ASI_LISTINGS_WORKER_ENABLED` — either may be enabled without
the other. SIGTERM/SIGINT handling, poll-error backoff scope
(`OperationalError`/`OSError` only), and configuration-error fail-closed
behavior are identical in shape to `listings_worker.py`.

Lifecycle states: `queued` → `started` (claim) → `succeeded`
(`finalize_successful_orders_run`, the only path to this state) |
`waiting_to_retry` (retryable failure, releases lease, durable
`next_retry_at`) | `failed` (non-retryable failure, retry-budget
exhaustion, or finalization-incomplete) | `timed_out` (stale-lease
reclaim by a later claim attempt). No `partial` status is currently
produced by any code path in this milestone (matching Listings'
documented "no unreachable states" discipline) — reserved in the CHECK
constraint for a future increment that might need it.

Proven by `tests/test_amazon_orders_worker.py` (21 tests): claim +
complete a queued job, process one job at a time across calls, throttled
reschedule honoring `Retry-After`, reclaim + eventual exhaustion to
`rate_limited`, non-retryable immediate terminal, per-organization
concurrency limit, unexpected exception during processing never crashes
the poll loop, graceful `run_forever` stop, SIGINT/SIGTERM both request
graceful stop, the enabled-gate's fail-closed values, `ASI_ORDERS_WORKER_
ENABLED` confirmed independent of the Listings variable, and poll-error
backoff configuration validation. Guarded PostgreSQL `SKIP LOCKED`
concurrency for `claim_next_orders_job` (mirroring
`test_disposable_postgres_listings_job_lifecycle_concurrency.py`) is
**not** included in this pass — no live-Postgres/live-migration action is
authorized in this milestone, and the claim SQL is structurally identical
to the already-proven Listings version, differing only in scope columns.
Recommended as a follow-up guarded test before Orders worker deployment.

## 6. Read API contracts

Mirrors `amazon_listings.py`/`listings_read.py` conventions exactly.
Organization is always `current_organization_id()`, never a request
parameter; every route is scoped by `marketplace_participation_id`,
re-validated on every call; a foreign or nonexistent participation/order
produces the identical sanitized 404.

- `GET /api/v1/amazon/marketplace-participations/{id}/orders/summary` →
  `OrdersSummary` — totals, cancelled/business/prime counts,
  `status_counts`, currency-safe `order_value_sum`/`order_value_currency`
  (both `null` whenever orders in scope span more than one currency —
  never silently summed), and `OrdersSyncEvidence` (restricted to
  `run_type='orders'` rows only, proven by
  `test_sync_evidence_restricted_to_run_type_orders`; `last_successful_
  synchronized_at` remains independently available after a later failed
  run, proven by
  `test_latest_successful_sync_remains_available_after_a_later_failed_run`).
- `GET .../orders` → `OrderCollectionResponse` — explicit sort allowlist
  (`amazon_last_updated_at`/`amazon_created_at`/`order_total_amount`),
  `NULLS LAST` + `id ASC` stable tie-break (identical technique to
  Listings), max page size 100, filters for `fulfillment_status`/
  `fulfilled_by`/date range, and `search` matching the Amazon order id
  directly or (via an `EXISTS` subquery against the separate items table)
  any item's seller SKU or ASIN.
- `GET .../orders/{order_id}` → `OrderDetail` with sanitized `items`.
- `POST /api/v1/amazon/orders/sync` → enqueues only, returns `202`
  immediately (`OrdersSyncTriggerResponse`); `GET
  /api/v1/amazon/orders/sync/{run_id}` reports status. Never blocks on
  ingestion — the trigger's own code path never imports or calls the
  Orders client.

One repository fix made along the way: `AmazonSellerOrderRepository.
get_order_detail` originally reused `_require_participation_in_
organization` (designed for internal ingestion-write paths, where a
mismatched participation is a caller bug and should raise `TypeError`).
For the read API, a foreign/nonexistent participation supplied by an
ordinary HTTP caller is an expected occurrence, not a bug — `get_order_
detail` now returns `None` gracefully in that case, matching
`AmazonSellerListingRepository.get_detail`'s established contract
exactly.

Proven by `tests/test_amazon_orders_read_service.py` (12 tests) and
`tests/test_amazon_orders_routes.py` (15 tests): ownership/tenancy,
mixed-currency sum omission, sync-evidence isolation and independence,
search across order-id/SKU/ASIN, status filtering, stable pagination,
oversized-limit clamping, sanitized order detail (no gift-message/
cancel-reason substring anywhere in the dumped response), foreign/
nonexistent-order identical errors, and full HTTP status-code mapping
(200/404/503/400/409/429) with a check that the trigger response never
contains `lease_owner`/`token_reference`/`pagination_token`/
`connection_id`.

## 7. Final navigation and Seller Hub design

Global navigation reduced from the prior flat, horizontally-scrolling
eight-item bar (`app-shell.tsx`) to exactly five primary destinations:

| Destination | Route | Absorbs |
|---|---|---|
| Analyze | `/` | ASIN analysis (primary) + Bulk Due Diligence (`/bulk`, secondary link shown only while on Analyze) |
| Copilot | `/copilot` | unchanged |
| Seller | `/seller` | new Seller Hub — Overview/Listings/Orders via page-local nav |
| Analytics | `/profit` | Profit (primary) + Seller Reports (`/reports`, secondary link shown only while on Analytics) |
| Activity | `/history` | unchanged |

Connection moved entirely out of primary navigation into a compact
account/settings menu (`AccountMenu` in `app-shell.tsx`), with a live
`ConnectionHealthDot` (self-fetching, fails silently to a neutral dot on
any error, never displays a token/seller id — only the already-public
`connection_status` enum). Usage and theme remain separate, compact
utility controls on desktop.

**Mobile:** a fixed bottom nav (`aria-label="Primary mobile"`) with
Analyze/Copilot/Seller/Analytics/More — Activity, Connection, Usage, and
theme all live inside the "More" sheet (`MobileMoreSheet`), exactly per
the brief. Neither nav ever scrolls horizontally at any breakpoint (both
verified structurally — see §8).

**Seller Hub page-local navigation** (`SellerLocalNav`,
`components/seller-local-nav.tsx`): Overview/Listings/Orders tabs,
preserving the selected `?participation=` query parameter across tabs so
switching from Listings to Orders keeps the same marketplace in view.
Orders is deliberately **not** a global header tab, per the brief.

**Routes and compatibility:**

- `/seller` (new) — `SellerOverview`: real-data cards (Listings total,
  needs-attention count, Orders total, cancelled count, order value,
  Prime orders), per-dataset sync cards (status + last successful sync,
  clicking through to the relevant Hub section), and a deterministic
  "Attention" list built only from data the two summary endpoints
  actually return (listings issue count, orders cancelled count, "never
  synchronized" flags) — no invented cross-dataset metric requiring
  row-level joins beyond what these two endpoints provide (see §14 for
  the explicit scope note).
- `/seller/listings` (moved) — existing `SellerListings` component,
  content and behavior completely unchanged, now wrapped with
  `SellerLocalNav`.
- `/seller/orders` (new) — `SellerOrders`: marketplace selector, sync
  button, truthful progress strip, summary metric cards, server-paginated
  table (click-through to detail), and a detail drawer with sanitized
  item rows.
- `/seller-listings` (old route) — now a thin server component that
  awaits `searchParams` and calls `redirect()` to `/seller/listings`,
  preserving every query parameter (including repeated values) exactly.
  Verified end-to-end against the live dev server:
  `curl .../seller-listings?participation=abc123&listing=def456` → `307`
  → `location: /seller/listings?participation=abc123&listing=def456`.

## 8. Responsive visual verification

**Tooling limitation, stated plainly:** this environment has no browser-
automation/screenshot tool available (checked; only Figma/Weave-specific
MCP tools exist, neither applicable to a local Next.js dev server) — true
pixel-level screenshots at 1440px/1024px/390px were not possible. What
was actually done instead, against the real, already-running local dev
server (frontend on `:3000`, backend on `:8000`, hot-reloaded on top of
every change made in this milestone, not a fresh/separate instance):

- Production build (`npm run build`) succeeded cleanly, including
  TypeScript, and listed all expected routes (`/seller`, `/seller/
  listings`, `/seller/orders` static; `/seller-listings` dynamic for the
  redirect).
- Live HTTP checks confirmed `/seller`, `/seller/listings`, `/seller/
  orders` all return `200`, and the redirect preserves query parameters
  exactly (above).
- The server-rendered HTML for `/seller` was inspected directly: both
  `aria-label="Primary"` (desktop) and `aria-label="Primary mobile"`
  (mobile bottom nav) markup are present with exactly the five
  destinations plus "More" — Tailwind's `md:flex`/`md:hidden` govern
  which is *visible* at a given viewport width via CSS media queries, not
  conditional rendering, so both are genuinely present in one HTML
  response and switch visibility purely by breakpoint.
- Confirmed `/` renders "Bulk Due Diligence" as a secondary link and
  `/profit` renders "Seller Reports", while neither appears as a primary
  tab; confirmed the old "Seller Data" primary-tab label is gone
  entirely; confirmed no `href="/connection"` link exists in the
  always-visible primary chrome (it only exists inside the closed-by-
  default account menu / mobile "More" sheet, correctly absent from
  initial markup).
- Component-level tests (`app-shell-ui.test.tsx`, 9 tests) assert: all
  five destinations render, `aria-current` marks exactly the active one,
  Seller links to `/seller` not the old route, active-link styling,
  every nav link is a real focusable `<a>`, the desktop primary nav's
  class list no longer contains `overflow-x-auto` (the old design's
  horizontal-scroll mechanism — five items fit without it), a mobile
  "More" trigger exists, and Connection is absent from primary nav while
  the account-menu trigger exists.

This is real, verified structural/functional evidence from a live
running build, not merely unit tests — but it is not a substitute for an
actual visual/pixel check at each breakpoint. If a browser-automation
tool becomes available, that check should be run before this UI ships
customer-facing.

## 9. Copilot skill-matrix summary

`docs/AI_HANDOVER/LISTINGS_ORDERS_COPILOT_SKILL_MATRIX.md` — **23
scenarios** (6 listing, 8 order, 9 cross-dataset), each with all 11
required fields (business question, example prompts, required data,
deterministic calculations, evidence/freshness requirements, proposed
tool, response structure, suggested action, limitations, UI deep link,
evaluation fixture, priority). Priority split: **12 Launch**, **7 Next**,
**4 Later**. Closes with an explicit "Explicitly unavailable conclusions"
section (profit without fees/COGS, inventory stockout, advertising
attribution, returns analysis, PII-based buyer segmentation), each tied
to a concrete absent field/table, not just asserted. No LLM Skill
implementation exists anywhere in this document — planning only, gated
behind 12B.9 per `CLAUDE.md`'s approved milestone roadmap.

**Corrected scheduling (12B.4D remediation, review item 4):** "gated
behind 12B.9" must not be read as an indefinitely deferred, unscheduled
someday — the next immediate sequence, in order, is:

1. Complete and merge 12B.4D (this remediation).
2. Deploy the required `0013_orders_durable_pagination` migration (see
   §12/§13 below for the exact authorization gate — not authorized in
   this turn).
3. Perform one controlled, explicitly authorized live Orders
   synchronization.
4. Verify the Orders UI against that live data.
5. Immediately implement the first five Listings + Orders Copilot
   skills, as 12B.9, from the already-written matrix — not a vague
   "eventually":
   - **Listing Health Prioritizer**
   - **Non-buyable Listing Investigator**
   - **Order and Sales Trend Analyst**
   - **Cancellation/Operational Anomaly Detector**
   - **Listing Risk by Order Exposure**

   Mapped onto the matrix's own numbered scenarios: Section 1 #1 (Issue
   Prioritization → Listing Health Prioritizer), Section 1 #2
   (Buyability → Non-buyable Listing Investigator), Section 2 #7
   (Order/Unit Trends → Order and Sales Trend Analyst), Section 2 #12
   (Cancellations → Cancellation/Operational Anomaly Detector), and
   Section 3 #17 (Revenue/Order Exposure Associated With Critical
   Listing Issues → Listing Risk by Order Exposure). Four of the five
   (#1, #2, #7, #12) are already the matrix's own **Launch** tier;
   **#17 is the matrix's own priority is Next, not Launch** — flagged
   here truthfully rather than silently relabeled, because this
   sequence names it as one of the first five to build. Building it
   first is a deliberate, explicit elevation the user made for this
   sequence, not evidence the matrix's own priority judgment was wrong;
   whoever implements 12B.9 should re-confirm that elevation still holds
   at that time rather than assume it's still current.

## 10. Exact files changed

**Backend (new):**

- `apps/api/app/amazon/orders_ingestion.py`
- `apps/api/app/amazon/orders_sync.py`
- `apps/api/app/amazon/orders_worker.py`
- `apps/api/app/amazon/orders_read.py`
- `apps/api/app/api/routes/amazon_orders.py`
- `apps/api/app/api/routes/amazon_orders_sync.py`
- `apps/api/tests/test_amazon_orders_ingestion_service.py` (12 tests)
- `apps/api/tests/test_amazon_orders_worker.py` (21 tests)
- `apps/api/tests/test_amazon_orders_sync_trigger.py` (15 tests)
- `apps/api/tests/test_amazon_orders_read_service.py` (12 tests)
- `apps/api/tests/test_amazon_orders_routes.py` (15 tests)

**Backend (modified):**

- `apps/api/app/core/config.py` — added `orders_sync_*`/`orders_worker_*`
  settings.
- `apps/api/app/persistence/repositories.py` — added Orders lifecycle
  methods on `AmazonIngestionRunRepository` (`get_latest_orders_run`,
  `get_latest_successful_orders_run`, `get_latest_cooldown_relevant_
  orders_run`, `get_active_orders_run`, `count_queued_orders_runs_for_
  organization`, `heartbeat_orders_run`, `reschedule_orders_run_for_
  retry`, `complete_orders_run_as_failed`, `claim_next_orders_job`);
  added two read-lookup methods on `AmazonIngestionRunMarketplaceParticipationRepository`
  (`get_latest_orders_run_for_participation`, `get_latest_successful_
  orders_run_for_participation`); added read-API methods on
  `AmazonSellerOrderRepository` (`get_summary_counts`, `list_page`,
  `get_order_detail`) plus the new `OrdersSummaryCounts` dataclass. No
  existing method's signature or behavior changed.
- `apps/api/app/api/routes/__init__.py` — registered the two new routers.

No migration, ORM model, or CI workflow file changed.

**Frontend (new):**

- `apps/web/src/components/connection-health-dot.tsx`
- `apps/web/src/components/seller-local-nav.tsx` (+ `.test.tsx`)
- `apps/web/src/components/seller-overview.tsx`
- `apps/web/src/components/seller-orders.tsx`
- `apps/web/src/lib/seller-orders-view.ts` (+ `.test.ts`)
- `apps/web/src/app/seller/page.tsx`
- `apps/web/src/app/seller/listings/page.tsx`
- `apps/web/src/app/seller/orders/page.tsx`
- `apps/web/src/app/seller-listings/redirect.test.tsx`

**Frontend (modified):**

- `apps/web/src/components/app-shell.tsx` — full navigation redesign
  (five destinations, mobile bottom nav + More sheet, account menu).
- `apps/web/src/components/app-shell-ui.test.tsx` — rewritten for the new
  nav design (the old scroll-into-view/eight-item assertions tested
  removed behavior).
- `apps/web/src/components/seller-listings-ui.test.tsx` — one navigation
  test updated (`Seller Data` → `Seller`, `/seller-listings` →
  `/seller`); all other tests unchanged.
- `apps/web/src/app/seller-listings/page.tsx` — replaced with the
  redirect (old content moved to `/seller/listings/page.tsx` verbatim).
- `apps/web/src/app/page.tsx`, `connection/page.tsx`, `profit/page.tsx`,
  `profit/[id]/page.tsx`, `reports/page.tsx`, `history/page.tsx`,
  `history/[id]/page.tsx`, `bulk/page.tsx` — updated `current=` prop to
  the new five-value union (`connection` maps to `"seller"`, matching the
  brief's "seller-setup menu" framing for where Connection conceptually
  lives now).
- `apps/web/src/lib/types.ts` — appended the Orders type block.
- `apps/web/src/lib/api.ts` — appended `OrdersApiError`, `ordersRequest`,
  `fetchOrdersSummary`, `fetchOrders`, `fetchOrderDetail`,
  `OrdersSyncError`, `triggerOrdersSync`, `fetchOrdersSyncStatus`.

**Documentation (new):**

- `docs/AI_HANDOVER/12B4D_ORDERS_INGESTION_AND_UI.md` (this file)
- `docs/AI_HANDOVER/LISTINGS_ORDERS_COPILOT_SKILL_MATRIX.md`

### Files changed across both remediation rounds (§10b, §16)

**Round 1 (durable pagination + currency):**

- New: `apps/api/migrations/versions/0013_orders_durable_pagination.py`.
- Modified: `apps/api/app/persistence/models.py` (3 new
  `AmazonIngestionRun` columns + 1 CHECK constraint),
  `apps/api/app/persistence/repositories.py` (`freeze_orders_window_
  if_needed`, `get_max_last_updated_at_by_participation`, extended
  `heartbeat_orders_run`/`reschedule_orders_run_for_retry` signatures,
  token-clearing in `complete_orders_run_as_failed`/`finalize_
  successful_orders_run`, currency-safe `get_summary_counts` SQL),
  `apps/api/app/amazon/orders_ingestion.py` (durable resume wiring,
  `pagination_token_rejected` classification, DB-derived final
  watermark), `apps/api/tests/test_amazon_orders_ingestion_service.py`
  (+9 tests), `apps/api/tests/test_amazon_orders_read_service.py` (+1
  currency test), `apps/api/tests/test_amazon_seller_identity_schema.py`
  (head bump), `apps/api/tests/postgres/test_disposable_postgres_
  orders_migration.py` (+1 guarded downgrade test).

**Round 2 (this remediation — nav fix, API investigation, bounded
recovery proof):**

- Modified: `apps/web/src/components/app-shell.tsx` (compact-header
  fix — see §16 item 1), `apps/web/src/components/app-shell-ui.test.tsx`
  (+2 regression tests), `apps/api/tests/test_amazon_orders_ingestion_
  service.py` (+1 bounded-token-rejection proof test).
- No application/test code changed for the API-failure investigation
  (§16 item 2) — only the local, gitignored, non-committed `apps/api/
  .env` (`DATABASE_URL` pointed at local SQLite instead of the
  unreachable Supabase host, original value preserved in a comment) and
  a throwaway seeding script left in the scratchpad directory, not part
  of the repository.

## 10b. Second remediation round: compact-laptop nav, live-browser API failure, bounded token-rejection proof

**1. Compact-laptop (1024px) navigation fix.** User-reported measurements
(`clientWidth=419px`, `scrollWidth=457px`, "Activity" visually colliding
with "Usage") confirmed the desktop primary nav's icon+full-label items
did not fit at 1024px, even though the same markup fit at 1440px. Root
cause: `PRIMARY_LINKS` rendered icon+label unconditionally from `md:`
(768px) upward, with no intermediate compact treatment for the
768–1279px range. Fix (`app-shell.tsx`): the label text is now wrapped
in `<span className="hidden xl:inline">`, hidden below the `xl` (1280px)
breakpoint and shown at `xl:` and up — the icon always renders, and each
link keeps a real `aria-label`/`title` so it stays identifiable and
reachable while compact, never a text size reduced to unreadable. Text
is never shrunk, only shown/hidden at a breakpoint — a bare CSS-class
change, no new dependency. `SecondaryLinks` (the Analyze/Analytics
sub-items) was moved from `md:flex` to `xl:flex` for the same reason —
otherwise it could reintroduce the identical overflow on the Analyze/
Analytics pages specifically. Account/settings (`AccountMenu`,
`UsagePanel`, `ThemeToggle`) were already icon-only and untouched.

Two new regression tests in `app-shell-ui.test.tsx` assert the compact
mode's actual DOM shape (`hidden xl:inline` on every primary label span,
`hidden ... xl:flex` — not `md:flex` — on the secondary-links container)
rather than a pixel measurement, since jsdom does not lay out real CSS —
the closest testable proxy for "which navigation mode is active."
1440px/1024px/390px were re-verified structurally (route reachability,
redirect query-param preservation) after the fix; see §17 for why the
390px mobile bottom nav was never affected (it uses a completely
separate `<nav aria-label="Primary mobile">` block, untouched by this
fix) and for the still-open pixel-level human-verification gate.

**2. Live-browser API failure — root cause and fix.** Investigation
found the browser-visible `"Amazon Connection could not reach the
server"` was **not** a CORS or stale-frontend issue: `CORS_ORIGINS`
already includes `http://localhost:3000`, and a real CORS preflight
against the running backend returned correct
`access-control-allow-origin` headers. The actual, independently
reproduced root cause: `apps/api/.env`'s `DATABASE_URL` points at a
real Supabase PostgreSQL host, and **this sandboxed environment's Python
process cannot resolve that hostname** — `socket.getaddrinfo` fails
reproducibly (`nodename nor servname provided, or not known`) even
though shell-level `host`/`curl` resolve the same hostname and reach the
public internet fine — an environment/sandbox networking asymmetry
between Python's resolver and shell tools, not an application code
defect. This made every DB-touching endpoint (`/api/v1/amazon/
connection`, listings/orders summaries, etc.) intermittently or
consistently fail with a 500 depending on exactly when a request landed
relative to uvicorn's connection attempts — plausibly compounding with
ordinary `--reload` restart windows (this session was actively editing
backend files throughout) to produce the exact fetch-level network
error the browser reported.

**Fix applied (local-only, reversible, no Supabase mutation):**
`apps/api/.env`'s `DATABASE_URL` line was commented out (preserved
verbatim in a comment immediately above, with instructions to restore
it) and replaced with a local SQLite file
(`sqlite:///./.data/dev-ui-verification.sqlite3`), then the local
uvicorn dev server was restarted to pick up the change.
`get_engine()`'s existing SQLite path (`app/persistence/database.py`)
creates the full current schema directly from ORM metadata — the same
mechanism the test suite already relies on — so no Alembic migration
was applied to any tracked database by this. A one-off, throwaway
seeding script (not part of the application, left in the scratchpad
directory, not committed) populated a connection/seller account/two
marketplace participations/18 synthetic orders with items (including
one multi-item order for drawer verification) directly through the
existing repository layer — no live Amazon call, no worker. Verified via
direct HTTP calls (matching exactly what the browser's `fetch()` calls
send, including an `Origin: http://localhost:3000` header) that
`/api/v1/amazon/connection`, the Orders summary/list/detail endpoints,
and the Listings summary endpoint (deliberately left unseeded, to prove
its empty state degrades to `200`, not `500`) all now return real,
well-formed data. Backend health and the connection endpoint were
re-checked stable across multiple calls after the restart.

This is an environment/config finding, not a code change — nothing in
`app/`, `migrations/`, or `tests/` was touched for this item. The
original Supabase `DATABASE_URL` is preserved in the `.env` comment and
should be restored once Supabase connectivity is available again; the
local SQLite file and the seeding script are throwaway and not part of
the repository.

**3. Bounded rejected-token recovery — proof, not a new mechanism.**
Traced the durable retry-budget machinery already reviewed for
throttled/transient/malformed_page failures
(`AmazonOrdersIngestionService._handle_worker_failure`) and confirmed it
already, structurally, bounds `pagination_token_rejected` too: `
pagination_token_rejected` is a plain member of `RETRYABLE_ORDERS_
FAILURE_CLASSES`, so every rejected-token attempt is gated by the same
`attempt_number >= cfg.orders_sync_max_attempts` /
`elapsed_seconds >= cfg.orders_sync_max_total_retry_seconds` check as
any other retryable failure — both `attempt_number`
(`run_row.retry_count + 1`) and the elapsed-time base
(`run_row.started_at`) are re-read fresh from the database on every
attempt, never held in memory, so the budget provably survives any
number of process restarts between attempts.

**Correction (third remediation round):** the initial proof asserted the
exhausted run's terminal reason was the shared sanitized name
`rate_limited` — factually wrong, since Amazon rejected the
continuation token here; it never throttled the request. Fixed
`_handle_worker_failure` to look up the exhaustion reason via a small
`_EXHAUSTION_REASON_BY_FAILURE_CLASS` mapping: throttled/transient/
malformed-page exhaustion still reports `rate_limited` (an accurate
description for those classes), while `pagination_token_rejected`
exhaustion now reports its own truthful name,
`pagination_token_retry_exhausted`. This is a real code change, not
merely a corrected assertion — the previous behavior would have made a
repeatedly-rejected-token failure indistinguishable from genuine
Amazon-side throttling in the run's own recorded `failure_class`,
undermining exactly the kind of root-cause diagnosis this remediation's
own §16 item 2 needed.

Added `test_repeated_pagination_token_rejection_is_bounded_and_
terminalizes_safely` (`test_amazon_orders_ingestion_service.py`),
modeling the actual pathological shape (page one keeps succeeding,
minting a fresh token each time; the next page is rejected every time) —
not a degenerate single-shot case — across
`orders_sync_max_attempts=3` attempts, using a fresh, stateless
`AmazonOrdersIngestionService` instance per attempt to simulate process
restarts. Confirms: the run terminalizes on the exact configured attempt
(never a 4th), with the truthful sanitized terminal reason
`pagination_token_retry_exhausted` (never the misleading `rate_limited`,
and never the raw `pagination_token_rejected` per-attempt class leaking
out as the run's final state), the durable token cleared, the checkpoint
never advanced, the scope's exhausted run never reclaimable again, and
the very first order committed (before any rejection ever happened)
still present and untouched by the eventual
exhaustion. This also incidentally proved a related, correctly-designed
edge case: an `invalid_request` with **no** token in play (a page-one
request after an earlier fallback) is *not* reclassified as
`pagination_token_rejected` — it is immediately, correctly terminal,
since it cannot be a token-expiry symptom by definition — meaning a
"reject → page one → reject again with no new token minted" sequence
cannot loop even once, let alone forever; only a "page one succeeds,
mints a new token, that gets rejected" sequence can repeat, and that
shape is exactly what the new test bounds.

## 11. Test/build results (updated for this remediation pass)

- Backend: `cd apps/api && uv run pytest -q` → **1237 passed, 61
  skipped**, stable across 3 consecutive full-suite runs after this
  remediation round. The 1 skip is the guarded disposable-Postgres
  downgrade test for `0013` (`test_downgrade_0013_to_0012_refuses_when_
  pagination_token_in_flight`), collected but not executed in this
  environment (no local PostgreSQL/Docker available) — see §12.
  - `uv run alembic heads` → `0013_orders_durable_pagination` — single
    head.
  - New tests across both remediation rounds: 9 durable-pagination
    tests + 1 currency-safety test + 1 guarded disposable-Postgres
    downgrade test (round 1) + 1 bounded-token-rejection proof test
    (round 2) = **12 new backend tests**, all passing (or, for the one
    Postgres-gated test, collecting and parsing cleanly). Original
    12B.4D delivery's 75 new backend tests remain unchanged and passing.
    `test_amazon_seller_identity_schema.py`'s hardcoded single-head
    assertion was updated from `0012` to `0013` (an unavoidable,
    mechanical consequence of adding a migration, not a design change).
- Frontend: 2 new regression tests this round (`app-shell-ui.test.tsx`,
  §16 item 1). `cd apps/web && npm test` → **140 passed**, stable across
  3 consecutive runs after this round's nav fix (the single transient
  `seller-listings-ui.test.tsx` timing flake noted after round 1 did not
  recur in this round's 3 runs; still unrelated to anything this
  remediation touches).
  - `npx tsc --noEmit` → clean, no errors.
  - `npm run build` (production, Turbopack) → succeeded; all 14 routes
    listed, including the Seller Hub routes and the dynamic redirect
    route.
- Diff/secret/PII scan: the 7 preserved Log Analyzer/ADR files remain
  byte-identical (SHA-256 re-verified) and unstaged; no real
  credential-shaped string in any new file (only the existing
  `Atza|test-...`/`Atzr|test-...` synthetic convention already used
  throughout this codebase's Amazon test suite); the new
  `orders_pagination_next_token` column and its test fixtures use only
  synthetic placeholder strings (`"TOKEN-PAGE-2"`,
  `"SECRET-CONTINUATION-TOKEN-MUST-NEVER-BE-LOGGED"`, etc.), never a
  credential-shaped value.

### Discovered-and-fixed: SQLite same-second `created_at` tie in the new "latest Orders run" queries

The final full-suite gate caught a genuine, self-discovered flakiness
(~30–50% failure rate over repeated full-suite runs, 100% pass in
isolation) in
`test_amazon_orders_read_service.py::test_latest_successful_sync_remains_available_after_a_later_failed_run`.
Root cause: five new `AmazonIngestionRunRepository` /
`AmazonIngestionRunMarketplaceParticipationRepository` methods
(`get_latest_orders_run`, `get_latest_successful_orders_run`,
`get_latest_cooldown_relevant_orders_run`,
`get_latest_orders_run_for_participation`,
`get_latest_successful_orders_run_for_participation`) ordered by a bare
`created_at.desc()` (or `completed_at.desc()`). SQLite's
`CURRENT_TIMESTAMP` — what `created_at`'s server default compiles to
there — only has second-level precision, so two rows created within the
same wall-clock second can tie, and the result is then arbitrary. This
is the exact same bug class already found and fixed once in production
for Listings (see `get_latest_listings_run`'s docstring in
`repositories.py`, and `_LATEST_LISTINGS_RUN_ORDER_BY`). Fix applied:
all five Orders methods now use the identical proven tie-break patterns
— `(created_at.desc(), started_at.is_not(None).desc(), id.desc())` for
"latest regardless of status", `(started_at.desc(), id.desc())` for
"latest successful" (where `started_at` is guaranteed non-null).

That query-side fix alone was **not sufficient** to make the specific
test deterministic: with `id` a random UUID, `id DESC` is a *stable*
but not *correct* final tiebreak for two rows that also tie on
`started_at`-is-set — which is exactly what the test's own back-to-back
`_seed_run` calls produced. The actual, established fix for the test
itself (already the convention in
`test_amazon_listings_read_service.py`, which stages equivalent
same-second scenarios via explicit `created_at`/`started_at` overrides
rather than relying on two real `datetime.now(UTC)` calls microseconds
apart) was to give the Orders test's `_seed_run` helper the same
explicit `created_at` override and stagger the two seeded runs by ten
minutes. Re-ran the full backend suite 4 consecutive times and the
previously-flaky file in isolation 3 times after the fix — all green,
`1226 passed, 60 skipped` every time.

## 12. PostgreSQL checks awaiting CI

**Migration `0013_orders_durable_pagination` (added in this remediation
pass) has NOT been applied to any real database** — it exists only as a
migration file plus the SQLite-based test suite (`test_migration_chain_
matches_orm_metadata.py`'s three checks, all passing against the full
chain through `0013`) and one guarded disposable-PostgreSQL test
(`test_downgrade_0013_to_0012_refuses_when_pagination_token_in_flight` in
`tests/postgres/test_disposable_postgres_orders_migration.py`) that could
not be executed in this authoring environment (no local PostgreSQL/Docker
available — the same stated limitation `test_disposable_postgres_orders_
migration.py`'s own module docstring already carries for its 0012
coverage). Whoever runs this against a real disposable Postgres instance
should treat that first run as the actual proof of the new `ADD COLUMN`/
`CHECK CONSTRAINT` DDL and the downgrade-refusal logic, not this file's
existence.

One further follow-up remains recommended, not required, before Orders
worker deployment: a guarded disposable-PostgreSQL concurrency test for
`claim_next_orders_job`'s `FOR UPDATE SKIP LOCKED` behavior, mirroring
`test_disposable_postgres_listings_job_lifecycle_concurrency.py` — the
claim SQL is structurally identical to the already-proven Listings
version (same technique, different scope columns), so this is a
confidence-building addition, not evidence of a suspected gap.

## 13. Required migration or live-authorization gate

**`0013_orders_durable_pagination` is the next authorization gate.** It
must be deployed (`alembic upgrade head` against the real database, after
a fresh backup — the same backup-before-first-run discipline `0012`
required) before any live Orders synchronization is attempted, since the
durable-pagination code path this remediation adds now depends on the
three new `amazon_ingestion_runs` columns existing. This migration was
**not applied to Supabase or any other real database in this turn** — it
was authored, tested against SQLite and (where executable) reasoned
through against PostgreSQL syntax, and left for explicit, separate
deployment authorization.

The deferred Option B pagination-token-*reference* design (persisting an
opaque `SecretProvider`-backed pointer instead of the plain private
column this remediation actually implements — see §2) remains fully
specified but intentionally not built; it would only be needed if a
future increment decides the private-column threat model documented on
`AmazonIngestionRun.orders_pagination_next_token` is no longer
proportionate, and would require its own separate migration and live-
authorization gate exactly like this one.

The first live Orders synchronization (an actual `POST /orders/sync`
against a real Amazon connection, followed by a running `OrdersWorker`
process) is **not** authorized by this milestone and was not attempted.
Per `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md`'s own
unresolved point: production role possession for `searchOrders`/
`getOrder` remains unverified (must be confirmed in Seller Central's
Developer Console before that first live call, independent of anything
built here). Per §9's corrected scheduling, the order is: merge this
remediation → deploy `0013` (with backup) → one controlled live Orders
sync → verify the Orders UI against real data → begin the first five
Copilot skills.

## 14. Remaining risks and deliberate scope decisions

1. **Role possession still unverified** (carried from 12B.4A/12B.4C,
   unchanged) — not a blocker for this milestone, but the first live
   Orders call must not assume either way.
2. **Seller Overview's "Attention" list is intentionally shallow.** It
   surfaces only single-dataset facts (listings issue count, orders
   cancelled count, never-synchronized flags) computed directly from the
   two summary endpoints — it does **not** attempt a true row-level
   cross-dataset join (e.g. "listings with issues that also have
   orders"), which the Copilot skill matrix's Section 3 scenarios (15–23)
   correctly identify as needing joined data neither summary endpoint
   exposes. Building that view is future Seller Hub work, tracked by the
   skill matrix, not silently approximated here.
3. **No guarded PostgreSQL concurrency test for `claim_next_orders_job`
   yet** (§12) — recommended before worker deployment, not before this
   PR.
4. **No true pixel-level responsive screenshot verification** (§8) — this
   environment has no browser-automation tool, still true after this
   remediation pass. Structural/functional verification against a live
   running build was performed instead (route reachability, the
   `/seller-listings` redirect's query-param preservation, both dev
   servers up with no worker process active); the actual 1440px/1024px/
   390px visual/keyboard/focus verification the review requested is an
   **explicit human-verification gate, not yet closed** — see the final
   report's dedicated section for the exact URLs and checklist handed to
   the user.
5. **`SellerOrders`'s default-marketplace selection is deliberately
   simpler than Listings'.** Listings' `pickDefaultMarketplace` fetches
   every marketplace's summary just to prefer the most-recently-synced
   one; the new Orders/Overview pages use URL param → canonical
   marketplace → first available, skipping that optimization. This only
   affects which marketplace is pre-selected on first load, never
   correctness or safety — a reasonable simplification for a foundation
   milestone, revisitable later.
6. **`OrdersWorker`'s claim query has no dedicated guarded-Postgres test
   in this pass** (see item 3) — the SQL is a direct structural mirror of
   `claim_next_listings_job`, which *is* proven under real concurrency;
   this is a coverage gap in test *breadth*, not a reason to doubt the
   claim logic's correctness.
7. **Seller Overview and Seller Orders components do not have the same
   density of dedicated component-level UI tests that `seller-listings-
   ui.test.tsx` has for Listings** (~700 lines covering many interaction
   paths). Given this milestone's scope and time budget, verification for
   these two new components relied on: full TypeScript type-checking,
   a real production build, live HTTP-level checks against a running dev
   server, and the extensive backend test suite proving every API
   contract they consume. Recommend adding dedicated component tests
   (mirroring `seller-listings-ui.test.tsx`'s patterns) as a fast,
   low-risk follow-up before or shortly after this ships.
8. **The `pagination_token_rejected` heuristic (12B.4D remediation §1) is
   a deliberate judgment call, not a documented Amazon guarantee.** Amazon
   does not publish a distinguishing error code for "this paginationToken
   has expired" versus any other invalid-request response, so this module
   infers it from "was a continuation token in play on this specific
   request" alone. A false positive (a genuinely unrelated invalid-request
   error that happens to occur while resuming) costs one harmless extra
   page-one restart within the still-frozen window; a false negative is
   structurally impossible under this heuristic (any invalid-request
   response while presenting a token is always caught by it). If Amazon
   is later found to return a distinguishing signal (e.g. a specific
   `code` value), narrowing this heuristic to key off that signal instead
   would be a strict improvement, not a required fix.
9. **The currency-safety audit (12B.4D remediation §2) found and fixed
   one narrow gap**: `AmazonSellerOrderRepository.get_summary_counts`'s
   SQL sum previously included any order with a known amount but an
   unknown (`NULL`) currency, which could have been silently folded into
   another, differently-labeled known currency's total. Fixed by
   excluding currency-`NULL` amounts from the SQL sum, matching the
   currency-consistency check `AmazonOrdersReadService.get_summary`
   already performs separately. No cross-marketplace monetary
   aggregation exists anywhere in this codebase to audit further —
   `SellerOverview` never combines money across participations at all
   (confirmed by inspection), so "group by currency or omit" is already
   satisfied by omission, not by a new grouping feature this remediation
   had to build.

## 15. Confirmation: no unauthorized action occurred

- No live Amazon call was made anywhere in this milestone (every backend
  test uses a fully faked `AmazonSpApiOrdersClient`; the live dev server
  used for frontend verification never triggered a sync).
- No live Orders worker was started or run (`ASI_ORDERS_WORKER_ENABLED`
  was never set to a truthy value; `OrdersWorker.run_forever`/`main` were
  only exercised inside the test suite, against a fake ingestion
  service).
- No Supabase mutation occurred — the live backend process used for
  frontend HTTP verification only served pre-existing read routes
  (`GET /seller`, etc.) and the new Orders routes were hit with no
  request bodies that would create a row (no `POST /orders/sync` was
  ever actually invoked against the live database).
- No migration was applied; Alembic remains at `0012_orders_foundation`.
- No role, configuration, or credential was changed.
- No commit, push, tag, or branch merge occurred — all work sits
  uncommitted on `milestone-12b4d-orders-ingestion-ui`, exactly as
  instructed.

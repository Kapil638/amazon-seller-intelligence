# 12B.4B — Orders Schema, Migration & PostgreSQL Integrity Foundation

Durable record of the 12B.4B implementation pass. Schema, ORM models,
migration `0012_orders_foundation`, persistence-primitive repositories, and
integrity tests only. No SP-API client, ingestion service, worker, API
route, UI, or Copilot integration exists yet. No live Amazon call, no
Supabase mutation, no `.env` change, no application-role change.

Branch: `milestone-12b4b-orders-schema`, created from verified `main`
(`2dcdc19cb8f68114522b568bba0bac219485cb56`).

## Remediation (same revision, not a second migration)

A schema review found four blocking gaps in the original draft, all fixed
before this branch was committed or pushed — `0012_orders_foundation`
remains one revision, never applied to any real database:

1. **Checkpoint advancement is now success-gated at the SQL level**, not
   caller responsibility. There is no public `advance()` method anymore.
   `AmazonIngestionRunMarketplaceParticipationRepository.
   finalize_successful_orders_run` is the one atomic primitive that marks
   a run `succeeded` and advances every included participation's
   checkpoint, gated by a SQL predicate requiring `run_type='orders' AND
   status='succeeded' AND completed_at IS NOT NULL`.
2. **Environment/connection consistency is now structural.**
   `amazon_connections` gained `uq_amazon_connections_id_org_region_
   environment`; `amazon_ingestion_runs` gained a composite FK to it
   (`fk_amazon_ingestion_runs_connection_org_region_env`) and now requires
   `connection_id IS NOT NULL` for `run_type='orders'`;
   `amazon_marketplace_participations` and `amazon_ingestion_run_
   marketplace_participations` both gained `connection_id` in their
   composite anchors/FKs — forcing a run and every participation it
   covers to share the *same* connection row.
3. **The lifecycle is queued-then-claimed**, mirroring Listings: no repository
   method creates a run directly `started`. `enqueue_orders_run` creates
   `queued`; `claim_orders_run` (worker-only, compare-and-set) is the only
   transition to `started`; `finalize_successful_orders_run` is the only
   transition to `succeeded`.
4. **Monetary columns use `Numeric(19,4)`**, not `Numeric(14,2)` — the
   pinned contract's `Decimal` type is documented as lossless and
   transmitted as a string specifically to avoid float rounding.

Sections below describe the remediated design directly; see git history
for the original draft if needed.

## Pre-implementation resolution of 12B.4A's open questions

Read in full before writing DDL:
`docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md` and every fixture
under `apps/api/tests/fixtures/sp_api/orders/`. Orders API `2026-01-01`
only — no v0 assumptions (no separate `getOrderItems` call, no RDT).

| 12B.4A open question | Resolution in 12B.4B |
|---|---|
| Run-scoping vs. rate-limit batching | Resolved explicitly — see below. Not deferred further. |
| Role possession (does ASI's app hold an Orders-authorizing role?) | Still unverified — this is an operational fact, not a schema question. No DDL depends on it. Restated as a 12B.4D precondition. |
| Per-seller vs. per-app rate-limit bucket | Unchanged operational assumption, no schema impact. |
| `EXPENSE`/`PROMOTION` deferral | Kept deferred — no columns, no `includedData` request for either in this or any future slice authorized so far. |
| History/change tracking | Kept deferred — current-state only (Phase 9, below). |

## Run scope and batching decision (Phase 3 — the primary architecture gate)

**Chosen design: option 3 — a coarser seller-region run, plus a
run-participation association table.** Compared against the other three
candidates:

1. *One Orders run per participation* (reuse Listings' model as-is) —
   rejected. `searchOrders`' 0.0056 req/s budget is shared per seller
   account regardless of participation count; dividing it into N
   independent per-participation jobs for an N-marketplace seller wastes
   an already-scarce, slow-to-refill budget (full burst recovery is
   ~59.5 minutes) for no benefit.
2. *One Orders run per seller account + region, with no participation
   record at all* — rejected. This would make it structurally impossible
   to prove which specific participations a run actually covered, which
   both `amazon_seller_orders.last_ingestion_run_id`'s provenance
   guarantee and `amazon_orders_sync_checkpoints`' correctness depend on.
3. **Seller-region run plus `amazon_ingestion_run_marketplace_participations`
   — chosen.** Gives the batching efficiency of (2) without giving up the
   provenance guarantee: exactly which participations a run covered is a
   real, queryable, FK-checked fact.
4. *Duplicate seller-account ownership columns directly on Orders rows*
   (skip the association table, denormalize seller/region onto every
   order row instead) — rejected. This would prove ownership but not
   *which run* covered *which participation*, which is the actual fact
   Phase 3 asks to make provable; it also does nothing to solve the
   provenance-chain problem for `amazon_orders_sync_checkpoints`.

**Verdict on the "stop and report if disproportionate" clause: not
triggered.** The existing `amazon_ingestion_runs` table already carries
`organization_id`, `seller_account_id`, `region`, and `environment` as
scalar columns (added in earlier milestones for a different purpose, but
directly reusable here) — the coarser Orders scope needed no new scalar
columns on that table at all, only: a widened `run_type` CHECK, one new
CHECK constraint (`marketplace_participation_id IS NULL` for
`run_type='orders'`), one new partial unique index at the coarser
granularity, one new composite unique constraint as an FK anchor, and six
additive counter columns (Phase 8). This is additive, proportionate
schema evolution, not a redesign.

## Provenance design (remediated)

`amazon_ingestion_run_marketplace_participations` is the single source of
truth for "which participations did this Orders run cover." Composite
primary key `(ingestion_run_id, marketplace_participation_id)` gives
exact no-duplicate-pair uniqueness. Both its foreign keys now pin the
*entire* `(organization_id, seller_account_id, region, connection_id)`
tuple — not just a bare id, and now including `connection_id`, added in
remediation — via composite unique constraints on both parent tables:

- `uq_amazon_ingestion_runs_id_org_seller_region_conn` on `amazon_ingestion_runs`
- `uq_amazon_marketplace_participations_id_org_seller_region_conn` on `amazon_marketplace_participations`

This makes cross-organization, cross-seller, cross-region, **and
cross-connection** association rows **structurally impossible to
insert** — an `IntegrityError`, not a code-review expectation. Pinning
`connection_id` on both sides is what closes the original environment gap
(see below): it forces a run and every participation it covers to share
the exact same `amazon_connections` row.

**Environment/connection consistency (Blocker 2, resolved).** The
original draft left environment unenforced because
`amazon_marketplace_participations` carries no `environment` column of
its own — only `amazon_connections` does. Rather than duplicating
`environment` onto participations (a second, independently-mutable copy
that could drift), the fix uses the *existing* `connection_id` link as
the single source of truth:

1. `amazon_connections` gained `uq_amazon_connections_id_org_region_environment`.
2. `amazon_ingestion_runs` gained a composite FK,
   `fk_amazon_ingestion_runs_connection_org_region_env`, proving its own
   `(connection_id, organization_id, region, environment)` values are
   exactly the connection's own — confirmed safe to add unconditionally
   (not just for new Orders rows) because the only existing code path
   that ever sets a run's `region`/`environment`
   (`AmazonListingsIngestionService._check_scope`) already always copies
   them directly from the resolved connection object, so this is a no-op
   for every row any current code can produce.
3. `amazon_ingestion_runs`'s orders-scope CHECK now also requires
   `connection_id IS NOT NULL`.
4. The association table's two composite FKs (above) pin `connection_id`
   on both the run and participation sides.

The transitive result: a PRODUCTION run's `connection_id` can never equal
a SANDBOX participation's `connection_id` (they are different
`amazon_connections` rows with different `environment` values, and the
association table's FKs require them to be the *same* row) — structurally
impossible, not merely disallowed by convention. Region is pinned the
same way. A participation with `connection_id IS NULL` (no resolved
connection) cannot be associated with any Orders run at all — it simply
cannot satisfy either composite FK's non-null half.

Every Orders/items/checkpoint row's `last_ingestion_run_id` still
references the association table's composite key — never
`amazon_ingestion_runs.id` directly — so the database itself proves "the
run named here actually included this row's own participation."
`run_type='orders'` on the referenced run remains **not**
database-enforced (a composite FK cannot pin the referenced side to a
literal constant) — the same accepted limitation `amazon_seller_listings`
already has for `run_type='listings'`; deferred to repository/service
predicates (see Checkpoint design).

## Checkpoint design (remediated — Blocker 1, resolved)

`amazon_orders_sync_checkpoints`, one row per `marketplace_participation_id`:

- `synced_through_at` — the raw UTC high-water mark only. No overlap
  window is baked into the stored value.
- `last_successful_run_id` — composite FK to the association table.
- Absence of a row (not a shared default) is what makes a newly-active
  participation start with no inherited history.
- No `paginationToken`/`nextToken` column.

**There is no public `advance()` method anymore.** The original design's
`advance()` validated only that the run *covered* the participation, not
that the run had actually *succeeded* — a caller invoking it too early
(against a `queued`/`started`/`waiting_to_retry` run) could permanently
skip order updates. The remediated design has exactly one way a
checkpoint's watermark ever moves:

`AmazonIngestionRunMarketplaceParticipationRepository.
finalize_successful_orders_run(*, organization_id, seller_account_id,
ingestion_run_id, participation_watermarks: dict[UUID, datetime])` — the
one atomic finalization primitive, in one transaction:

1. A single guarded `UPDATE ... WHERE id=:id AND organization_id=:org AND
   seller_account_id=:seller AND run_type='orders' AND status='started'`
   marks the run `succeeded`, sets `completed_at=now()`, and clears
   `lease_owner`/`lease_expires_at`/`next_retry_at` — all in one
   statement. Zero rows affected (`rowcount == 0`) means rejection —
   returned as `OrdersRunFinalization(finalized=False, reason=
   "run_not_started")`, never an exception for this ordinary case. This
   single SQL predicate uniformly rejects `queued`, `waiting_to_retry`,
   `failed`, `partial`, `timed_out`, a `cancelled_before_start` `failed`
   row (that's a `failure_class`, not a separate status — see Deferred/
   lifecycle notes below), a `listings` run, and a
   `marketplace_participations` run — no per-status special-casing.
2. If and only if step 1 affected exactly one row, for every
   `(participation_id, watermark)` pair supplied, a private helper —
   `AmazonOrdersSyncCheckpointRepository._advance_if_run_succeeded` —
   attempts the checkpoint write. Its own eligibility check is a single
   SQL query joining the association table to `amazon_ingestion_runs` and
   requiring `run_type='orders' AND status='succeeded' AND completed_at
   IS NOT NULL AND organization_id=:org AND seller_account_id=:seller` —
   re-checked independently, in the same transaction, immediately after
   step 1 (same session, no intervening commit, so it reliably observes
   step 1's just-written status).
3. **All-or-nothing across the whole batch, enforced by raising, not by a
   partial-success return value.** If step 2 fails for *any*
   `(participation_id, watermark)` pair in the caller's
   `participation_watermarks` — most notably, a participation the run
   did not actually cover — this method raises
   `OrdersRunFinalizationIncomplete` instead of returning `finalized=True`
   with only the ones that succeeded so far. The run-succeeded `UPDATE`
   from step 1 and every checkpoint already advanced earlier in the same
   loop are still uncommitted, in the same transaction, when the
   exception is raised — so the caller's own rollback (never a
   swallow-and-commit) undoes all of it together. Proven with a
   deterministic *mid-batch* failure, not only a rejection before any
   write begins: `test_finalize_mid_batch_failure_rolls_back_run_and_all_
   earlier_checkpoint_writes` enqueues a run covering one real
   participation, then calls `finalize_successful_orders_run` with that
   valid participation *and* a second, foreign one in the same dict —
   dict insertion order means the valid participation's checkpoint is
   genuinely written first, then the foreign one triggers the raise, and
   the test confirms that after rollback the run is still `started` (not
   left `succeeded`) and *neither* participation has a checkpoint row —
   including the one that individually would have succeeded.
4. When no exception is raised, the caller commits both effects
   together — a rollback after a *successful* return still leaves the
   run's status and every checkpoint write undone, never one without the
   other, since nothing was ever committed by this method itself.

This is a genuine SQL-predicate-driven design, not a Python-only status
check: the checkpoint write's eligibility is a `JOIN ... WHERE` clause on
live column values, re-evaluated at write time, and calling
`_advance_if_run_succeeded` directly (bypassing `finalize_successful_
orders_run` entirely) against a merely-`started` run is exactly what
`test_checkpoint_advance_rejected_for_started_status_called_directly`
proves fails. Never moves the watermark backward: a call with a
`synced_through_at` strictly earlier than what is already stored leaves
the existing value untouched; an equal watermark is treated as an
idempotent success (rewrites the same value and refreshes provenance,
never an error).

## Schema inventory

### `amazon_seller_orders`

`id` (PK) · `marketplace_participation_id` (FK → participations,
`RESTRICT`) · `amazon_order_id` · `fulfillment_status` (CHECK, 7 enum
values) · `fulfilled_by` (CHECK, `MERCHANT`/`AMAZON`) ·
`sales_channel_name`/`_marketplace_id`/`_marketplace_name` ·
`items_shipped_count`/`items_unshipped_count` · `order_total_amount`
(**`Numeric(19,4)`, remediated from `Numeric(14,2)`** — see Monetary
representation, below) / `order_total_currency` · `is_business_order` ·
`is_prime` · `was_cancelled` · `amazon_created_at` ·
`amazon_last_updated_at` · `last_ingestion_run_id` (composite FK to the
association table) · `first_seen_at` · `last_seen_at` · `created_at` ·
`updated_at`.

Natural key: `UniqueConstraint(marketplace_participation_id,
amazon_order_id)` — the pinned contract does not prove Amazon order IDs
are globally unique across marketplaces, so uniqueness stays scoped per
participation, per 12B.4A. Also carries
`UniqueConstraint(id, marketplace_participation_id)`, the same
composite-unique-widening technique `amazon_ingestion_runs` uses, so
`amazon_seller_order_items` can hold its own same-participation
provenance FK against orders.

`amazon_order_id` is a confidential business identifier: safe to store
and to return through a future authenticated, org-scoped read API, but
must never appear in logs, exception text, or generic diagnostics — the
same standard already enforced for `seller_sku`/`asin` throughout 12B.3.

### `amazon_seller_order_items`

`id` (PK) · `order_id` + `marketplace_participation_id` (composite FK →
`amazon_seller_orders`, `RESTRICT`) · `amazon_order_item_id` ·
`seller_sku` · `asin` · `item_name` · `condition_type` ·
`quantity_ordered` · `quantity_fulfilled` · `quantity_unfulfilled` ·
`unit_price_amount`/`unit_price_currency` (**`Numeric(19,4)`**) ·
`item_proceeds_amount`/`item_proceeds_currency` (**`Numeric(19,4)`**) ·
`last_ingestion_run_id` (composite FK to the
association table) · `first_seen_at` · `last_seen_at` · `created_at` ·
`updated_at`.

Natural key: `UniqueConstraint(order_id, amazon_order_item_id)` —
`orderItemId` is documented by the pinned contract as "a unique
identifier for this specific item within the order," an evidenced key,
never invented from `seller_sku`/`asin` alone (the same SKU legitimately
repeats across orders; even within one order two lines could in principle
share an ASIN).

### `amazon_ingestion_run_marketplace_participations`

Composite PK `(ingestion_run_id, marketplace_participation_id)` ·
`organization_id` · `seller_account_id` · `region` · **`connection_id`
(NOT NULL, added in remediation)** · `created_at`. Two composite FKs, each
now a 5-column pin including `connection_id` (see Provenance design,
above).

### `amazon_orders_sync_checkpoints`

`marketplace_participation_id` (PK, FK → participations, `RESTRICT`) ·
`organization_id` · `seller_account_id` · `synced_through_at` ·
`last_successful_run_id` (composite FK to the association table) ·
`created_at` · `updated_at`. Unchanged by remediation — the checkpoint
table's own columns were already correct; it was the *repository method*
that wrote to them (`advance()`) that was replaced.

### `amazon_ingestion_runs` extensions

- `ck_amazon_ingestion_runs_run_type` widened to add `'orders'`.
- New `ck_amazon_ingestion_runs_orders_scope_required`: an `'orders'` row
  must have `marketplace_participation_id IS NULL` and `seller_account_id`/
  `region`/`environment`/**`connection_id`** all `NOT NULL` — `connection_id`
  added in remediation (Blocker 2).
- New partial unique index `uq_amazon_ingestion_runs_active_orders_scope`
  on `(seller_account_id, region, environment)`, filtered to
  `run_type='orders' AND status IN ('queued','started','waiting_to_retry')`
  — the Orders concurrency control for the whole enqueue-then-claim
  lifecycle (Blocker 3), exactly the same technique as Listings'
  equivalent index, at a coarser granularity.
- New `uq_amazon_ingestion_runs_id_org_seller_region_conn` (FK anchor,
  now including `connection_id`, see Provenance design) and
  `fk_amazon_ingestion_runs_connection_org_region_env` (proves the run's
  own region/environment match its own connection's).
- Six new counters: `orders_received/accepted/rejected`,
  `items_received/accepted/rejected` — all `NOT NULL DEFAULT 0`. Not a
  reuse of the pre-existing `records_received/accepted/rejected` (sized
  for Listings, where one record == one listing/SKU): an Orders page
  truthfully contains two distinct countable entities, which a single
  counter triple cannot represent without conflating orders and items.
  Meaningful only for `run_type='orders'`; always `0` for every other row,
  including every pre-12B.4B row after migration.
- Existing Listings constraints/indexes/columns are completely untouched
  — every 12B.4B change to this table is additive.

### `amazon_connections` / `amazon_marketplace_participations` extensions (new, remediation)

- `amazon_connections` gained `uq_amazon_connections_id_org_region_environment`.
- `amazon_marketplace_participations` gained
  `uq_amazon_marketplace_participations_id_org_seller_region_conn`
  (replacing the original draft's region-only anchor with one that also
  pins `connection_id`).

### Durable lifecycle (Blocker 3, resolved) — no new DDL, repository-layer only

`enqueue_orders_run` (creates `queued`, no lease) → `claim_orders_run`
(worker-only compare-and-set transition to `started`, sets `started_at`/
`lease_owner`/`lease_expires_at`/`last_heartbeat_at`, reclaims expired
`started` leases to `timed_out` first) → `finalize_successful_orders_run`
(the only transition to `succeeded`, atomically advancing checkpoints).
The removed `start_orders_run` method (created a run directly `started`,
bypassing any worker claim) no longer exists at all —
`test_direct_start_method_does_not_exist` asserts this by introspection,
not just by its absence from the docs.

## Monetary representation (Phase 6, precision remediated — Blocker 4, resolved)

No `PROCEEDS`/`EXPENSE`/`PROMOTION`/`PAYMENT`/`TAX` object is stored as
raw JSON anywhere. `EXPENSE`/`PROMOTION` remain deferred per 12B.4A (not
requested, no columns). For the monetary facts this slice *does* store —
order/item totals — the schema uses **normalized `Numeric(19,4)` +
`String(8)` currency-code column pairs exclusively**
(`order_total_amount`/`order_total_currency`,
`unit_price_amount`/`unit_price_currency`,
`item_proceeds_amount`/`item_proceeds_currency`). No JSON/JSONB column
exists on any of the four new tables at all — verified by an automated
test (`test_no_generic_json_column_exists_on_any_new_orders_table`), not
merely asserted. This is a deliberate narrowing versus 12B.4A's original
proposal (which sketched a per-category `proceeds_breakdown` JSON array):
Phase 5's required-concept list for 12B.4B asks only for "order total
amount and currency where available," not per-category breakdowns —
choosing the smallest design that satisfies what was actually asked, and
avoiding a JSON column entirely trivially satisfies Phase 6's "no generic
raw response JSON" and Phase 7's "no JSON column unless narrow and
documented" by having no such column at all. Per-category breakdown
storage (`ITEM`/`SHIPPING`/`TAX`/etc. subtotals) is deferred to a later,
separately-reviewed increment if a concrete analytics need for it
emerges — see Deferred fields, below.

**Precision: `Numeric(19,4)`, not this repository's more common
`Numeric(14,2)`.** Evidence: the pinned Orders API `2026-01-01` model's
own `Decimal` type definition (`Money.amount`) reads *"A decimal number
with no loss of precision. Follows RFC 7159 for number representation"*
and is transmitted as a JSON **string**, not a native number — deliberate
avoidance of float rounding, and direct evidence against assuming every
supported currency has exactly two fractional digits. `Numeric(19,4)`
comfortably covers three-decimal currencies (BHD/KWD/OMR) with a margin
of safety; `19` total digits exceeds any realistic order amount.

**Correction from an earlier draft of this section:** real PostgreSQL's
`NUMERIC(19,4)` does **not** reject a value with more than 4 fractional
digits — it silently **rounds** it at type-coercion time (confirmed:
PostgreSQL's `numeric` type only raises `numeric_field_overflow` for
excess *magnitude*, an integer part needing more digits than
precision-scale=15 allows; it never raises for excess *scale*). An
earlier version of this document and its guarded test incorrectly
asserted the opposite. The actual, only enforcement point for "excess
scale rejected rather than silently rounded" is therefore the
**repository/DTO write boundary**, not the database:
`app.persistence.repositories._validate_orders_money_amount`, called
before any Orders monetary value is bound into an `INSERT`/`UPDATE`, in
both `AmazonSellerOrderRepository.upsert` and
`AmazonSellerOrderItemRepository.upsert`. It rejects (`ValueError`) a
`Decimal` with more than 4 fractional digits or a magnitude PostgreSQL
would reject anyway, and rejects (`TypeError`) a `float` outright rather
than silently accepting and implicitly converting it. This runs
identically on SQLite and PostgreSQL, since the check happens in pure
Python before any SQL is constructed —
`tests/test_amazon_seller_orders_schema.py`'s
`test_excess_scale_rejected_before_sql_execution_*` and
`test_excess_magnitude_rejected_before_sql_execution_*` prove it directly.
`test_excess_scale_is_rounded_not_rejected_by_raw_postgres` in the guarded
suite documents PostgreSQL's real rounding behavior via a raw SQL insert
that deliberately bypasses the repository, so the distinction between
"what the database does on its own" and "what this codebase's write
boundary enforces" stays explicit rather than conflated.
`test_excessive_magnitude_rejected_by_real_postgres` remains correct
unchanged — magnitude overflow genuinely is a database-level guarantee.

An unconstrained `NUMERIC` column plus an explicit `CHECK (scale(amount)
<= 4)` constraint *would* let PostgreSQL itself reject excess scale
(the check would see the value before any column-typmod rounding
occurs) — evaluated and **not adopted**: SQLite has no `scale()`
function or equivalent typmod concept, so this would not be portable to
this repository's SQLite-based test suite without a separate,
harder-to-maintain SQLite-specific `CHECK`; it would also duplicate a
validated-Python-write-boundary pattern this codebase already relies on
for every other Orders invariant (ownership, PII exclusion) rather than
adding a second, database-specific enforcement path for this one field
type. The application-layer validation is simpler, portable, and
consistent with existing precedent.

SQLite's own `Numeric` type has no native arbitrary-precision backing and
silently rounds through a float intermediate on read-back for extreme
values (confirmed empirically) — irrelevant to the rejection guarantee
above (which happens in Python, before SQLite is ever involved), but
still the reason the *exact-round-trip* proof for a legitimately large,
valid value only runs against real PostgreSQL
(`test_boundary_magnitude_round_trips_exactly_on_real_postgres`).

This is a **deliberately scoped exception for the three new Orders amount
columns only** — `order_total_amount`, `unit_price_amount`,
`item_proceeds_amount`. It does not retrofit
`amazon_seller_listings.price_amount` or the Profit/Advertising models'
existing `Numeric(14,2)` columns, which are unrelated to this milestone
and already proven adequate for their own inputs (fixed-precision seller-
entered costs and Rainforest-derived listing prices, not Amazon's own
lossless `Decimal` wire format).

`Decimal` throughout — no float type anywhere in the new schema, and no
`float(...)` call anywhere in the repository `upsert` methods (asserted
by source inspection, `test_no_float_conversion_in_repository_upsert_source`).
Absence is distinct from zero: every monetary column is nullable, and a
`None`/`NULL` value means "not available from this response" — never
coerced to `0`.

## Privacy proof (Phase 7)

No column, on any of the four new tables, for: buyer name/email/phone,
recipient name, shipping/billing address, any geographic address
component, gift message, customized-product text/image, cancellation
reason text, tax-registration identifiers, payment instrument data, or a
raw buyer/recipient/order/item payload. Proven by four kinds of
automated test, not by inspection alone:

1. **Column-name substring scan** (`test_amazon_seller_orders_schema.py`,
   `_assert_no_forbidden_columns`) — asserts none of `buyer`, `recipient`,
   `address`, `email`, `phone`, `gift`, `payment`, `tax_registration`,
   `customiz`, `cancel_reason`, `raw_payload`, `raw_order`, `raw_item`
   appear as a substring of any column name on any of the four new tables.
2. **No JSON column at all** (above) — there is no generic blob any of
   these fields could ride into even accidentally.
3. **Repository signature inspection**
   (`test_repository_upsert_signatures_have_no_gift_message_or_cancel_reason_parameter`)
   — proves a future caller cannot even *attempt* to pass a gift message
   or cancellation reason through `AmazonSellerOrderRepository.upsert`/
   `AmazonSellerOrderItemRepository.upsert`, not merely that no column
   would receive it.
4. **Explicit parser/persistence-boundary rule, documented for 12B.4C**
   (below): no future DTO/parser may call a broad `model_dump()`/`.dict()`
   or pass a raw parsed-response object into these `upsert()` methods.
   Every field, scalar or otherwise, must be assigned by explicit,
   named-field mapping. This is not yet enforceable by a test (there is no
   parser yet), so it is recorded here as a binding requirement for
   12B.4C's implementation and review.

## Migration behavior (`0012_orders_foundation`)

- Revises `0011_listings_job_lifecycle`; single deterministic head
  confirmed (`test_alembic_has_a_single_head`, updated).
- Revision id `0012_orders_foundation` (22 characters), well within
  `alembic_version.version_num`'s default `VARCHAR(32)`
  (`test_every_revision_id_fits_the_alembic_version_column`, generic,
  unmodified, passes automatically).
- Every new constraint name kept ≤ 63 characters (PostgreSQL's identifier
  limit) — several names were shortened during authoring after the
  offline compile check caught them (`fk_amazon_ingestion_run_parts_*_scope`,
  `fk_amazon_seller_order*_last_run_same_participation`,
  `uq_amazon_marketplace_participations_id_org_seller_region_conn` at 62
  characters); see Test results.
- Upgrade: additive only. No existing column is dropped, renamed, or made
  more restrictive for any row this repository's code can currently
  produce. Six new `amazon_ingestion_runs` counters get a deterministic
  `server_default='0'`, applied by PostgreSQL to every existing row with
  no separate `UPDATE` statement. The new composite FK from
  `amazon_ingestion_runs` to `amazon_connections`
  (`fk_amazon_ingestion_runs_connection_org_region_env`) is genuinely new
  enforcement, added in remediation — confirmed safe against every
  existing row because the only code path that ever sets a run's region/
  environment already always copies them from the resolved connection.
- Downgrade: refuses (raises `RuntimeError`) if any row exists in any of
  the four new tables, or any `amazon_ingestion_runs` row has
  `run_type='orders'` — `0011`'s schema has no way to represent that data,
  so downgrading would either violate a restored constraint or silently
  discard real evidence. Orders rows are never reinterpreted as Listings
  rows. When safe (no Orders data exists), downgrade drops the four new
  tables and constraints in dependency order (checkpoints → items →
  orders → association table → the new `amazon_ingestion_runs`/
  `amazon_marketplace_participations`/`amazon_connections` constraints)
  before restoring the original `run_type` CHECK.

## Current-state vs. history boundary (Phase 9)

Both `amazon_seller_orders` and `amazon_seller_order_items` are
current-state, idempotent-upsert tables — one row per order/item,
reflecting Amazon's latest known state. There is **no** order-event or
status-history table in this migration. 12B.4A's own Phase 5 explicitly
deferred history/change tracking as not required by any allowed-slice
analytics goal, and 12B.4B reconfirms that unchanged: nothing here would
let ASI reconstruct "when did this order first become `SHIPPED`," only
"what is the order's state as of the last successful sync." `first_seen_at`/
`last_seen_at`/`amazon_last_updated_at` preserve enough provenance to know
an order changed and when ASI last observed it, without claiming to have
captured every intermediate state. An order is never deleted or
deactivated merely because it is absent from an incremental result — no
such logic exists anywhere in this schema or these repositories.

## Deferred fields (explicitly out of scope for 12B.4B)

- `EXPENSE`/`PROMOTION` monetary categories (12B.4A decision, reconfirmed).
- Per-category `proceeds`/`breakdowns` (`ITEM`/`SHIPPING`/`TAX`/etc.
  subtotals) at either order or item level — only the grand total and
  item total are stored; a later increment can add these as normalized
  columns or an allowlisted child table if a concrete analytics need
  emerges, per Phase 6's own comparison framework.
- `order_aliases`/`associatedOrders` (seller-defined order numbers,
  replacement/exchange linkage) — not in Phase 5's required-concept list;
  no `is_replacement` flag exists in this slice.
- Item-level cancellation flags (`cancel_requester`/`cancelled_by`) — the
  order-level `was_cancelled` boolean covers the stated cancellation-
  monitoring analytics goal for this slice; item-level granularity is
  deferred, not required by Phase 5.B's explicit concept list.
- `packages`/`fulfillmentOrders` (shipment tracking, EasyShip) — not
  requested in this slice.
- `product.serialNumbers` — deliberately not persisted (12B.4A decision).
- History/event tracking (see above).
- Resumable `paginationToken` persistence — deliberately not implemented
  (see Checkpoint design).

## 12B.4C client handoff requirements

Binding constraints on the next milestone (typed Orders SP-API client +
parser), derived directly from this schema:

1. **No broad `model_dump()`/`.dict()` persistence, no raw-object
   persistence.** Every call into `AmazonSellerOrderRepository.upsert`/
   `AmazonSellerOrderItemRepository.upsert` must pass explicit, named
   fields — the signatures make it structurally impossible to do
   otherwise, but the parser layer upstream of those calls must not
   construct a "dump everything" intermediate object either.
2. **Explicit field-level redaction for the two bundled-field hazards**
   (`giftOption.giftMessage`, `cancelReason`) — both live inside otherwise-
   wanted objects (`ItemFulfillment` via `FULFILLMENT`; `ItemCancellation`
   via `CANCELLATION`) and cannot be excluded by omitting an `includedData`
   flag. The parser must read `quantityFulfilled`/`quantityUnfulfilled`
   and `requester`/`cancelledBy` from those objects while never persisting
   or logging the free-text fields alongside them.
3. **Request only `includedData=PROCEEDS,FULFILLMENT,CANCELLATION,
   PACKAGES`** (per 12B.4A's recommended set) — never `BUYER`, `RECIPIENT`,
   `PAYMENT`, or `TAX`.
4. **Required test, not optional**: a fixture-16-style test
   (`apps/api/tests/fixtures/sp_api/orders/16_restricted_pii_fields_present.json`)
   proving that even if Amazon ever returned `buyer`/`recipient`/`payment`/
   `tax`/`product.customization`/`fulfillment.packing.giftOption` fields
   unexpectedly, none of them reach a parsed DTO, a persisted row, or a
   log line.
5. **Use the natural keys exactly as designed**: upsert orders on
   `(marketplace_participation_id, amazon_order_id)`, items on
   `(order_id, amazon_order_item_id)` — never invent uniqueness from
   `seller_sku`/`asin`.
6. **`last_ingestion_run_id` must come from a run/association pairing
   that genuinely exists** — the composite FK will reject anything else;
   the ingestion service (12B.4D) must call `enqueue_orders_run` then
   `claim_orders_run` (both on
   `AmazonIngestionRunMarketplaceParticipationRepository`) before
   attempting any order/item upsert, and must call
   `finalize_successful_orders_run` — never write to
   `amazon_orders_sync_checkpoints` any other way — to mark the run
   complete and advance checkpoints together.
7. **Never persist a `paginationToken`/`nextToken` value** anywhere,
   including transient logging — consistent with the checkpoint's own
   design.
8. **One worker, no concurrency** against a given seller account's Orders
   budget — the same invariant already operationally proven for Listings
   (12B.3H/12B.3I), and more costly to violate here given the ~59.5-minute
   burst-refill time.

## Remaining risks / unresolved items

Four of the original five items are now resolved by this remediation
(environment/connection consistency, the direct-`started` lifecycle
shortcut, and the permissive checkpoint `advance()` no longer exist).
What remains:

1. **Run-scope batching *policy*** (as opposed to the *schema*, which is
   settled) — the ingestion service's actual choice of how many
   participations to batch into one `searchOrders` call, and how to pick
   the combined `lastUpdatedAfter` when participations have different
   watermarks (recommendation carried over from 12B.4A: the minimum
   across the batch), is a 12B.4D decision this schema supports but does
   not make.
2. **Role possession remains unverified** — 12B.4D must not proceed to a
   live call without the user confirming, out-of-band in Seller Central's
   Developer Console, that the production app holds at least one of the
   twelve Orders-authorizing roles.
3. **`claim_orders_run`'s simplified concurrency model** (compare-and-set
   by exact scope, no `FOR UPDATE SKIP LOCKED`/global capacity limiting)
   is correct for this schema's guarantee (at most one non-terminal run
   per scope) but deliberately does not replicate
   `claim_next_listings_job`'s cross-organization fairness/capacity
   machinery — reasonable for this slice's proof, per 12B.4B's own "do
   not duplicate the entire worker implementation unless essential"
   instruction, but 12B.4D should revisit if Orders ever needs a shared
   global worker pool the way Listings does.
4. **Guarded PostgreSQL tests are written but unexecuted** in this
   environment (no Docker/local Postgres available) — see Test results.
   They must be run for real against a disposable instance before this
   milestone is considered fully proven, exactly as 12B.3B's equivalent
   file was treated. This now includes the two precision/magnitude
   boundary tests, which are *only* meaningful against real PostgreSQL.

## Test results

See the chat-turn report for exact commands and counts. Summary across
both remediation rounds: **1107 backend tests passed**
(`tests/test_amazon_seller_orders_schema.py` grew from the original 28 →
60 (round 1) → 67 (round 2: added Python-level excess-scale/magnitude/
float rejection tests and the mid-batch finalize-atomicity test) plus the
pre-existing 1040), **60 skipped** (guarded — the Orders Postgres file
grew from 10 → 13 (round 1) → 15 (round 2: corrected the excess-scale
test to document rounding rather than assert a nonexistent rejection, and
added a real-Postgres exact-boundary round-trip test plus a real-Postgres
mid-batch atomicity test) plus the pre-existing 45; no disposable
PostgreSQL target in this environment), **0 failed**. Offline PostgreSQL
upgrade SQL compiles cleanly for the full chain through `0012`; offline
downgrade SQL begins compiling correctly and fails at the same
live-connection guard point `0011`'s downgrade already fails at in `--sql`
mode (a pre-existing, identical characteristic of the guarded-downgrade
pattern, not a regression). Full backend suite, migration/model drift
check, and Alembic single-head check all green after both rounds.

`12B.4B ORDERS SCHEMA REMEDIATED — READY FOR FINAL REVIEW`

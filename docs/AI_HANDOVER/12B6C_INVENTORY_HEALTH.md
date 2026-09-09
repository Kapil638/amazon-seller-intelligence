# 12B.6C — Inventory Health and Replenishment Insights

Durable record of the 12B.6C implementation pass. Branch:
`milestone-12b6c-inventory-health`, created from post-merge `origin/main`
(commit `e2e5e93`, PR #26) in the session's normal working tree.
Compute-on-read only — no new ingestion path, no new worker, no new
source table, no migration, no live Amazon call, no live AI call. This
milestone reads exclusively from data already persisted by 12B.6A
(Sales & Traffic) and 12B.6B (Inventory).

## 0. Governing corrections (review pass before implementation)

A first-draft proposal for this milestone was reviewed and returned
with three material corrections, applied before any code was written:

1. **Velocity formula.** The rejected formula was
   `sum(units_ordered) / count(distinct report windows)`. A Sales &
   Traffic product fact (`amazon_sales_traffic_product_facts`) is
   already an aggregate over its own exact
   `(request_window_start, request_window_end)` window — a single
   30-day fact is *one row*, not thirty independent daily counts. The
   corrected formula divides one selected fact's `units_ordered` by
   that fact's own **inclusive** day count
   (`(end - start).days + 1`), and overlapping 7-/30-/90-day facts
   covering the same underlying days are never summed — exactly one
   fact is selected per SKU (see §2).
2. **Zero vs. missing demand.** Amazon's pinned Sales & Traffic
   contract (`docs/AI_HANDOVER/12B6A_SALES_TRAFFIC_REPORTS.md`)
   documents no guarantee that a complete `SKU`-granularity report
   enumerates every seller SKU. Absence of a product fact for a SKU is
   therefore `no_eligible_sales_traffic_fact` ("no reliable evidence"),
   never `no_recent_demand` ("proven zero"). Only an eligible fact that
   explicitly reports `units_ordered == 0` is real evidence of zero
   demand.
3. **Inventory freshness source.** `amazon_seller_inventory.
   amazon_last_updated_time` is Amazon's own, optional, sometimes-empty
   field — it must never be the primary freshness clock. Freshness now
   comes from ASI's own provenance: the row's `last_ingestion_run_id`
   joined to that run's `completed_at` (falling back to the row's own
   `last_seen_at`, which is set in the identical successful-reconcile
   transaction as `last_ingestion_run_id` — see
   `AmazonSellerInventoryRepository._upsert`). `amazon_last_updated_time`
   is still exposed in every response, separately labeled, but never
   drives `freshness_state`.

## 1. User story

> As an Amazon FBA seller, I want to see my current inventory position
> alongside recent sales velocity so I can identify products at risk of
> stocking out or holding excess inventory and decide what to
> replenish first.

## 2. Join and selection strategy

Safest join key: **`(marketplace_participation_id, seller_sku)`** — per
ADR-0005, never ASIN (a seller SKU is the seller-owned-operations
identity; ASIN can legitimately repeat across SKUs/marketplaces/
conditions).

`AmazonSalesTrafficProductFactRepository.get_eligible_sku_facts`
(`repositories.py`) returns every `asin_granularity == "SKU"` product
fact whose originating run's `status == 'succeeded'`, grouped by
`seller_sku` — a `PARENT`/`CHILD`-granularity row (`seller_sku == ''`)
or a fact from a non-succeeded run is structurally excluded by the
query itself, never filtered after the fact.

`inventory_health_formulas.select_canonical_product_fact` then picks
**exactly one** fact per SKU from that eligible candidate set (pure
function, no I/O):

1. Latest `request_window_end`.
2. Among facts sharing that end date, prefer the one whose inclusive
   length exactly equals the configured preferred window (30 days).
3. If none matches exactly, use the longest window at that end date —
   its *actual* start/end/day-count are always reported, never coerced
   to look like 30 days.
4. Final deterministic tie-break (practically unreachable given the
   source table's own natural key): earliest `request_window_start`,
   then `id`.

Never combines two facts — a real 7-day and a real 30-day fact ending on
the same date describe overlapping periods, not additive ones.

## 3. Condition handling

Verified against this seller's own **live, real** FBA Inventory sync
(2026-09-09, PR #26): all 19 real summary rows reported
`condition="NewItem"` verbatim — not assumed from memory or a test
fixture. The pinned Inventory contract places no enum constraint on
`condition` at all (`inventory_models.py`'s own module docstring), so
`SUPPORTED_CONDITION = "NewItem"` is ASI/EWise V1 policy, not an
Amazon-documented closed set.

A Sales & Traffic product fact carries **no condition dimension**. To
prevent two condition-rows for the same SKU from both silently claiming
the same demand evidence, `demand_eligibility_for_condition` short-
circuits any non-`NewItem` row to `"unsupported_condition"` *before*
any product-fact lookup — an unsupported-condition row never consumes a
SKU-level demand fact a supported-condition row for the same SKU would
also be entitled to.

## 4. Metric formulas

```
covered_days(start, end) = (end - start).days + 1

units_per_covered_day = fact.units_ordered / covered_days(fact.start, fact.end)

fulfillable_days_of_cover =
    fulfillable_quantity / units_per_covered_day
    (None if either input is None, or if units_per_covered_day == 0 —
     zero demand is "no recent demand," never "infinite runway")

potential_units =
    fulfillable_quantity + inbound_working + inbound_shipped + inbound_receiving
    (None if fulfillable_quantity is None; a missing individual inbound
     component is excluded from the sum, never treated as a confirmed
     zero — incomplete_inputs=True flags this even when a numeric value
     is still returned)
```

`fulfillable_days_of_cover` never includes reserved, inbound,
unfulfillable, or researching quantities. `potential_units` is a
**separate** metric, always labeled "Potential coverage including
inbound inventory" — inbound units are not yet fulfillable and may be
delayed or rejected by Amazon.

## 5. Classification (factual state, eligibility, freshness, overlays — kept separate)

```
inventory_state:      inactive | out_of_stock | low_coverage |
                       healthy_coverage | high_coverage | unclassified
demand_eligibility:   eligible | no_eligible_sales_traffic_fact |
                       insufficient_window | unsupported_condition |
                       missing_identity
freshness_state:       fresh | stale_inventory | stale_sales | stale_both
overlays[]:            demand_with_no_fulfillable_stock | inbound_present |
                        unfulfillable_present | researching_present |
                        no_recent_demand
```

`inventory_state` precedence: `inactive` first, then a factual
`fulfillable_quantity == 0` → `out_of_stock` (true regardless of
whether any demand evidence exists), then the coverage-threshold
classification. `fulfillable_quantity is None` never triggers
`out_of_stock` (Amazon-unknown is not a real zero).

Boundary behavior (inclusive both ends of "healthy"): exactly the low
threshold is `healthy_coverage`; exactly the high threshold is
`healthy_coverage`; strictly below low is `low_coverage`; strictly
above high is `high_coverage`.

## 6. Threshold policy

EWise/ASI product policy, not Amazon guidance — kept as typed backend
configuration (`Settings.inventory_health_*`, `app/core/config.py`),
not an organization-mutable database table, for this V1:

```
low_coverage_days_threshold  = 14.0
high_coverage_days_threshold = 90.0
min_eligible_window_days     = 7
preferred_window_days        = 30
max_inventory_age_seconds    = 172800  (48h)
max_sales_age_seconds        = 172800  (48h)
```

Every evidence-bearing API response returns the exact thresholds used
alongside `formula_version` (`inventory_health_formulas.FORMULA_VERSION`),
so a later policy change is always visible, never silently rewriting
the meaning of a previously-seen classification. Organization-
configurable thresholds are explicitly deferred to a later milestone
after pilot feedback.

## 7. Persistence decision

Compute-on-read for V1, approved — no metric snapshot table. The
documented, small expected catalog size (hundreds to low thousands of
SKUs, per `AmazonSellerListing`'s own precedent) keeps this a bounded,
small, constant number of queries per request
(`AmazonInventoryHealthReadService._compute_rows` batches the eligible-
facts lookup and the ingestion-run lookup once per request, never once
per row — see the query-count regression test in
`test_amazon_inventory_health_read_service.py`).

## 8. API

```
GET  /api/v1/amazon/marketplace-participations/{id}/inventory-health/summary
GET  /api/v1/amazon/marketplace-participations/{id}/inventory-health
GET  /api/v1/amazon/marketplace-participations/{id}/inventory-health/{inventory_id}/evidence
```

The evidence endpoint takes the Inventory row's own UUID, never a raw
seller SKU, in the path. No threshold `PUT` route in V1. Every response
distinguishes a real `0` from `null` (unavailable/ineligible) — never
one collapsed into the other.

## 9. UI

`SellerInventoryHealth` (`apps/web/src/components/seller-inventory-
health.tsx`) is mounted as an additional section inside the existing
FBA Inventory page (`seller-inventory.tsx`), never a new top-level nav
tab — consistent with this codebase's `SellerLocalNav` precedent.
Summary state counts (including genuine `0` counts, never omitted),
search, per-state filter chips, an urgency-biased default sort
(ascending `fulfillable_quantity`), and a per-row evidence panel
showing every formula input, both freshness clocks independently, and
the exact backing run ids.

## 10. Explicitly deferred

Supplier lead time, safety stock, reorder points, purchase orders,
seasonality, service-level optimization, automatic ordering, Copilot
exposure (deterministic read service ships first; a future read-only
skill would cite exact formula inputs/version, never generate a
quantity itself), organization-configurable thresholds.

## 11. Known limitations, honestly stated

- Demand eligibility depends on the Sales & Traffic ingestion having
  actually requested `asin_granularity="SKU"` for a given window — a
  historical run requested at `PARENT`/`CHILD` granularity leaves that
  window's SKUs ineligible, correctly reported as
  `no_eligible_sales_traffic_fact`, not silently backfilled.
- `SUPPORTED_CONDITION = "NewItem"` is this seller's own observed
  value, not a verified-exhaustive Amazon enum — a seller whose catalog
  uses a different primary condition string would see every row report
  `unsupported_condition` until this policy is revisited.
- No organization-level threshold override yet (§6) — every
  organization on this codebase shares one global policy for now.

# Listings + Orders Copilot Scenario Matrix

Status: **Planning document only.** No code, no Copilot tools, and no Skill implementation exist yet for anything described below. This document is the candidate backlog for wiring already-ingested Listings and Orders data into Copilot via `ToolRegistry` + `EvidenceEnvelope`, once that work is explicitly approved (per `CLAUDE.md`'s milestone roadmap, that is **12B.9 — Connect stable seller-data tools to intelligence/Copilot**, not yet started).

## Scope and non-negotiable constraints

Every scenario below is written to respect ASI's fixed architecture:

- Deterministic Python engines own all calculations, scores, and money math. AI (Copilot synthesis) owns language and explanation only, and must never invent or estimate a number that cannot be computed from stored data.
- Copilot calls tools only through `ToolRegistry`; a tool wraps an existing Python service and never calls a provider API directly.
- `EvidenceEnvelope` carries typed claims (`observed`, `calculated`, `historical`, …) with freshness/provenance. Synthesis must cite evidence; unsupported claims are invalid.
- Tenancy is `organization_id`; every row is additionally scoped by `marketplace_participation_id`.
- Two seller-owned datasets exist today and are the only source material for this document:
  - **Listings** (`amazon_seller_listings`) — one row per SKU per marketplace participation, refreshed by full-catalog resync (never incremental).
  - **Orders** (`amazon_seller_orders` + `amazon_seller_order_items`) — current-state per order (not a change history), refreshed incrementally against a per-participation `synced_through_at` watermark, bounded by a 30-day default lookback and a 2-year API ceiling.
- A separate, already-implemented profit feature owns ACOS/TACOS/ROAS/margin from seller-supplied COGS + advertising figures. Scenarios below may reference it as a combinable source but never re-derive or duplicate its math.
- Fields not confirmed present on the actual implemented schema are never invented. Where a plausible-sounding field is not confirmed to exist, the scenario is scoped to only the fields that are confirmed.

## How to read each scenario

Every scenario uses the same 11-field template:

- **Business question** — the seller's actual question.
- **Example user prompts** — realistic Copilot phrasings.
- **Required data** — exact stored fields/tables.
- **Deterministic calculations** — what Python computes (or "None" if it's a pure lookup/filter).
- **Evidence/freshness requirements** — claim type(s) and staleness disclosure.
- **Proposed Copilot tool** — plausible `ToolRegistry` tool name + one-line description.
- **Response structure** — shape of the synthesized answer, not prose.
- **Suggested action** — informational next step (Copilot never writes to Amazon).
- **Limitations** — what this scenario cannot tell the seller.
- **UI deep link** — plausible Seller Hub route.
- **Evaluation fixture** — what a test fixture needs to contain.
- **Priority** — `Launch` / `Next` / `Later` with justification.

---

## Section 1 — Listing scenarios

### 1. Issue Prioritization

- **Business question:** Which of my listings need attention first, and how bad is each problem?
- **Example user prompts:**
  - "What listing issues should I fix first?"
  - "Show me my most critical listing problems."
  - "Rank my SKUs by how broken they are."
- **Required data:** `amazon_seller_listings.sku`, `.asin`, `.item_name`, `.issues[].code`, `.issues[].severity`, `.issues[].category`, `.issues[].attribute_names`, `.is_active`, `.last_seen_at`.
- **Deterministic calculations:** Count issues per listing by severity tier (`ERROR` vs `WARNING`); sort listings by (ERROR count desc, WARNING count desc); build a frequency table of issue codes/categories across the catalog. Pure counting and sorting.
- **Evidence/freshness requirements:** `observed` claim for raw issue records from the latest sync; `calculated` claim for the severity ranking. Must state "as of last successful listings sync" with the run timestamp, and flag if the run is incomplete, failed, or still in progress.
- **Proposed Copilot tool:** `get_listings_by_issue_severity` — wraps a severity-ranked scan of current listing issues.
- **Response structure:** headline ERROR-listing count vs WARNING-listing count; top-N table (sku, asin, item_name, error_count, warning_count, top issue codes); freshness line.
- **Suggested action:** review/edit the flagged listing content or attributes in Seller Central, starting with the top of the ranked list.
- **Limitations:** cannot explain why Amazon raised an issue beyond its own code/message; cannot show issue trend without comparing multiple syncs (see Scenario 6); cannot estimate revenue impact without joining Orders (see Scenario 17).
- **UI deep link:** `/seller/listings?sort=severity&issue_severity=ERROR`
- **Evaluation fixture:** an org with 5 listings — 2 with `ERROR` issues (one with 3 errors, one with 1 error + 2 warnings), 1 with `WARNING` only, 2 with none — asserting the ranked order and per-severity counts.
- **Priority:** Launch — issues are already ingested; the calculation is a direct count/sort with no upstream dependency.

### 2. Buyability

- **Business question:** Which of my SKUs are not currently buyable, and why?
- **Example user prompts:**
  - "Which of my products can't customers buy right now?"
  - "Show me listings that lost buyability."
  - "Why isn't SKU ABC-123 purchasable?"
- **Required data:** `amazon_seller_listings.sku`, `.asin`, `.item_name`, `.status` (presence/absence of `BUYABLE`), `.issues`, `.is_active`.
- **Deterministic calculations:** Filter listings where `BUYABLE` is absent from `status`; match against that same listing's `issues` list for context (a join, not an inference). No estimation of cause beyond what Amazon's own issue record states.
- **Evidence/freshness requirements:** `observed` claim from the latest sync's `status` field. Must disclose "as of last successful sync" and flag an incomplete run.
- **Proposed Copilot tool:** `get_non_buyable_listings` — wraps a filter over current listing status.
- **Response structure:** count of non-buyable active listings; table (sku, asin, item_name, missing status flags, associated issue codes if present); freshness note.
- **Suggested action:** open the flagged SKU in Seller Central and review the associated issues.
- **Limitations:** cannot state the root cause beyond what `status`/`issues` already report; no buy-box/price-competition data exists in this schema; cannot confirm a fix until the next sync.
- **UI deep link:** `/seller/listings?status=not_buyable`
- **Evaluation fixture:** 4 listings — 2 with `BUYABLE` present, 2 without (one of the two paired with a matching `ERROR` issue, one with no issues at all) — asserting correct filtering and pairing.
- **Priority:** Launch — direct filter, high seller value, uses only already-ingested fields.

### 3. Discoverability

- **Business question:** Which of my products are not discoverable in search/browse right now?
- **Example user prompts:**
  - "Which listings aren't showing up in search?"
  - "Show me products missing discoverable status."
  - "Is my new SKU visible to shoppers yet?"
- **Required data:** `amazon_seller_listings.sku`, `.asin`, `.item_name`, `.status` (presence/absence of `DISCOVERABLE`), `.issues`, `.first_seen_at`.
- **Deterministic calculations:** Filter listings missing `DISCOVERABLE`; bucket by age using `first_seen_at` (e.g., recently added vs. established) via a simple date comparison. No modeling of actual search ranking.
- **Evidence/freshness requirements:** `observed` claim from the latest sync. Same freshness/staleness disclosure as Scenario 2.
- **Proposed Copilot tool:** `get_non_discoverable_listings` — wraps a filter over current status plus a `first_seen_at` age bucket.
- **Response structure:** count; table (sku, item_name, days since first_seen_at, associated issues); split "recently added — may still be indexing" vs. "established — may need review."
- **Suggested action:** review content/attributes for established SKUs; monitor the next sync for very new ones.
- **Limitations:** no search ranking, impressions, or click-through data exists in this schema (that is a separate, unimplemented search-analytics domain); only the binary discoverable flag is known.
- **UI deep link:** `/seller/listings?status=not_discoverable`
- **Evaluation fixture:** 3 listings — 1 new (`first_seen_at` 2 days ago) missing `DISCOVERABLE`, 1 established (200 days) missing `DISCOVERABLE` with an `ERROR` issue, 1 healthy with `DISCOVERABLE` present — asserting correct bucketing.
- **Priority:** Launch — same low-lift filter category as buyability.

### 4. Attribute/Content Gaps

- **Business question:** Which listings have content or attribute problems I should fix?
- **Example user prompts:**
  - "Which listings are missing required attributes?"
  - "Show me content-related issues across my catalog."
  - "What attributes does Amazon say are wrong on SKU ABC-123?"
- **Required data:** `amazon_seller_listings.sku`, `.asin`, `.item_name`, `.issues[].category`, `.issues[].attribute_names`, `.issues[].message`, `.product_types`.
- **Deterministic calculations:** Filter issues whose `category` (as Amazon reports it, not inferred) indicates a content/attribute problem; aggregate distinct `attribute_names` across the catalog into a frequency table. Pure aggregation.
- **Evidence/freshness requirements:** `observed` claims (raw attribute_names/messages from the latest sync) plus a `calculated` frequency count on top.
- **Proposed Copilot tool:** `get_attribute_gap_report` — wraps an aggregation of `issues[].attribute_names`/`.category` across current listings.
- **Response structure:** top-N flagged attributes by frequency; per-SKU table (sku, item_name, flagged attribute_names, message); freshness line.
- **Suggested action:** update the flagged attributes in Seller Central/inventory file per Amazon's exact message text.
- **Limitations:** cannot suggest the correct attribute value (that would be invented content); no historical issue trend without a prior sync snapshot (see Scenario 6).
- **UI deep link:** `/seller/listings?issue_category=content`
- **Evaluation fixture:** 4 listings with issue `attribute_names` of `["bullet_point"]`, `["bullet_point","item_weight"]`, `[]`, `["size"]` — asserting frequency counts `bullet_point=2, item_weight=1, size=1`.
- **Priority:** Next — valuable, but depends on `issues[].category` being reliably populated across product types; more aggregation logic than the direct filters above.

### 5. Product-Type Corrections

- **Business question:** Are any of my products classified under the wrong product type / category?
- **Example user prompts:**
  - "Which SKUs might be miscategorized?"
  - "Show me product-type mismatches or category issues."
  - "Did any of my listings get moved to a different category?"
- **Required data:** `amazon_seller_listings.sku`, `.asin`, `.item_name`, `.product_types`, `.issues` (filtered to classification-related codes), plus a prior stored snapshot of `.product_types` from an earlier ingestion run for the same SKU where available.
- **Deterministic calculations:** Filter issues whose category/code Amazon marks as classification-related, joined with the current `product_types`; if a prior snapshot for the same SKU recorded a different `product_types` value, report a "changed since last observation" flag via a simple not-equal comparison between two stored snapshots. Never suggests the "correct" category.
- **Evidence/freshness requirements:** `observed` claim per sync; a `calculated` "changed since prior sync" flag only where two runs both captured the SKU.
- **Proposed Copilot tool:** `get_product_type_flags` — wraps a filter over classification-related issues plus a `product_types` diff across the two most recent syncs.
- **Response structure:** table (sku, item_name, current product_types, classification issue codes/messages, changed-since-prior-sync yes/no).
- **Suggested action:** review Amazon's classification guidance and request reclassification through Seller Central if warranted.
- **Limitations:** cannot recommend the correct category (an invented business judgment); "changed since prior sync" cannot reconstruct a change that happened before ASI began syncing.
- **UI deep link:** `/seller/listings?issue_category=classification`
- **Evaluation fixture:** SKU A unchanged with a classification `ERROR`; SKU B where run 1 recorded `product_types=["SHOES"]` and run 2 recorded `["APPAREL"]` with no issue; SKU C with no classification activity — asserting correct flags per SKU.
- **Priority:** Later — needs multi-run history and well-populated classification-issue codes; lower frequency need than issue/buyability triage.

### 6. Listing-Health Changes Over Time

- **Business question:** Is my catalog's overall health improving or getting worse?
- **Example user prompts:**
  - "How has my listing health changed over the last month?"
  - "Are more of my SKUs failing than before?"
  - "Show me a trend of buyable vs. non-buyable listings over time."
- **Required data:** `amazon_seller_listings` snapshots across multiple `amazon_ingestion_runs` (`.is_active`, `.status`, `.issues`) per `marketplace_participation_id`, tied to each run's completion time.
- **Deterministic calculations:** Per historical run, compute counts (active listings, missing-BUYABLE, missing-DISCOVERABLE, ≥1 ERROR issue, ≥1 WARNING issue); compute the delta between the earliest and latest run in the requested window. Counting and subtraction only — no forecasting.
- **Evidence/freshness requirements:** `historical` claim per past run plus a `calculated` delta claim. Must disclose which runs the trend is built from and flag any failed/skipped run that leaves a gap in the series.
- **Proposed Copilot tool:** `get_listing_health_trend` — wraps a time series of per-run listing-health counts for a marketplace participation.
- **Response structure:** table (run date, active count, non-buyable count, non-discoverable count, error-issue count); net change over the window; freshness/completeness note.
- **Suggested action:** investigate the run with the largest negative delta first.
- **Limitations:** only as granular as sync cadence (full-catalog resync, not real-time) — cannot show intra-day changes; because a resync is the only way `is_active` flips false, a disappearance is detected only on the *next* resync, not the moment it happened.
- **UI deep link:** `/seller/listings/health-trend?range=30d`
- **Evaluation fixture:** 3 stored ingestion runs 10 days apart with counts that worsen then improve, plus a 4th run marked failed — asserting correct deltas/direction and that the failed run is excluded from the trend but flagged as a gap.
- **Priority:** Next — high strategic value but requires reliable multi-run querying, not zero-lift like the direct filters.

---

## Section 2 — Order scenarios

### 7. Order/Unit Trends

- **Business question:** How are my order volume and units sold trending?
- **Example user prompts:**
  - "How many orders did I get last week vs. the week before?"
  - "Show me my order trend for the last 30 days."
  - "Are my unit sales going up or down?"
- **Required data:** `amazon_seller_orders.amazon_order_id`, `.amazon_created_at`, `.order_total_amount`/`.currency`, `.was_cancelled`; `amazon_seller_order_items.quantity_ordered` per order.
- **Deterministic calculations:** Bucket orders by day/week using `amazon_created_at`; sum order counts, `quantity_ordered`, and `order_total_amount` per currency per bucket; compute percentage change between two equal-length windows. Aggregation and arithmetic only.
- **Evidence/freshness requirements:** `calculated` claim with an explicit statement of the synced window used — the trend must not claim coverage beyond what `synced_through_at` and the initial lookback actually cover.
- **Proposed Copilot tool:** `get_order_volume_trend` — wraps day/week bucketed order and unit aggregation for a marketplace participation.
- **Response structure:** current-period totals (orders, units, revenue by currency), prior-period totals, % change; note on earliest date covered by synced data.
- **Suggested action:** cross-reference a downturn/upswing against a specific listing-health or marketplace-participation event before acting.
- **Limitations:** reflects `order_total_amount`, not profit (no COGS/fees here — see the profit feature); cannot show sales before the lookback ceiling; currencies are never summed together.
- **UI deep link:** `/seller/orders/trend?range=30d`
- **Evaluation fixture:** 20 orders across two 15-day windows with known counts/quantities/totals in a single currency, plus a `synced_through_at` watermark 45 days back — asserting correct bucketing, % change, and a matching "earliest covered date" note.
- **Priority:** Launch — core, high-frequency question; straightforward aggregation over already-ingested data.

### 8. SKU/ASIN Performance

- **Business question:** Which of my products are selling best/worst?
- **Example user prompts:**
  - "What are my top-selling SKUs this month?"
  - "Rank my ASINs by units sold."
  - "Which products barely sold anything in the last 30 days?"
- **Required data:** `amazon_seller_order_items.seller_sku`, `.asin`, `.item_name`, `.quantity_ordered`, `.item_proceeds_amount`/`.currency`, `.was_cancelled`; joined to `amazon_seller_orders.amazon_created_at` for date filtering.
- **Deterministic calculations:** Group order items by `seller_sku`/`asin` within a window; sum `quantity_ordered` and `item_proceeds_amount` (flagging cancelled items separately rather than silently including or excluding them); sort ascending/descending. Grouping, summation, sort only.
- **Evidence/freshness requirements:** `calculated` claim scoped to the requested window and the actual synced range, with freshness note.
- **Proposed Copilot tool:** `get_sku_performance_ranking` — wraps grouped-by-SKU order-item aggregation with sort direction/limit.
- **Response structure:** ranked table (sku, asin, item_name, units, proceeds by currency, cancelled-unit count called out separately); top/bottom N per request.
- **Suggested action:** compare a top performer against its listing health, or a bottom performer against its discoverability status (see Section 3).
- **Limitations:** "best selling" means units/proceeds, not margin (needs seller COGS via the profit feature); no Rainforest competitor context is mixed in.
- **UI deep link:** `/seller/orders/sku-performance?range=30d&sort=units_desc`
- **Evaluation fixture:** order items for 4 SKUs with distinct quantities/proceeds, one SKU with 2 of its units cancelled — asserting cancelled units are reported separately and don't silently distort the ranking.
- **Priority:** Launch — one of the most common seller questions; uses already-ingested item-level fields only.

### 9. Marketplace Comparison

- **Business question:** How is each Amazon marketplace performing for me?
- **Example user prompts:**
  - "Compare my sales across amazon.com and my other marketplaces."
  - "Which marketplace is my best performer?"
  - "Break down my orders by country."
- **Required data:** `amazon_seller_orders.sales_channel_marketplace_id`, `.order_total_amount`/`.currency`, `.amazon_created_at`, `.fulfillment_status`; scoped across the org's `amazon_marketplace_participations`.
- **Deterministic calculations:** Group orders by `sales_channel_marketplace_id`; sum counts and `order_total_amount` per currency within a window; compute share-of-total percentages. No cross-currency conversion is performed.
- **Evidence/freshness requirements:** `calculated` claim per marketplace, each carrying its own currency and its own participation's `synced_through_at` freshness (participations can have different watermarks).
- **Proposed Copilot tool:** `get_marketplace_order_comparison` — wraps per-marketplace order aggregation across a seller's connected participations.
- **Response structure:** table (marketplace_id, order count, revenue in native currency, share of total orders, freshness/watermark per marketplace); explicit note that currencies are not combined.
- **Suggested action:** consider where to focus inventory/listing effort based on relative order volume (informational only).
- **Limitations:** no single blended revenue number across currencies without a conversion step this schema does not perform; only reflects marketplaces with a connected, synced participation.
- **UI deep link:** `/seller/orders/by-marketplace?range=30d`
- **Evaluation fixture:** two participations (amazon.com in USD, amazon.co.uk in GBP) with distinct order sets and different `synced_through_at` watermarks — asserting native-currency totals and per-marketplace freshness notes.
- **Priority:** Next — valuable specifically for multi-marketplace sellers, a minority case at current adoption.

### 10. Fulfillment Mix

- **Business question:** How much of my business is FBA vs. self-fulfilled, and how well is each doing?
- **Example user prompts:**
  - "What percentage of my orders are Fulfilled by Amazon vs. by me?"
  - "Show me my FBA vs. FBM split."
  - "Are my self-fulfilled orders shipping worse than FBA?"
- **Required data:** `amazon_seller_orders.fulfilled_by` (`MERCHANT`/`AMAZON`), `.fulfillment_status`, `.amazon_created_at`, `.order_total_amount`.
- **Deterministic calculations:** Group by `fulfilled_by`; count orders, sum `order_total_amount`, and compute the `fulfillment_status` distribution within each group. Grouping and counting only.
- **Evidence/freshness requirements:** `calculated` claim per fulfillment channel, with freshness note.
- **Proposed Copilot tool:** `get_fulfillment_mix` — wraps grouped aggregation of orders by `fulfilled_by` and `fulfillment_status`.
- **Response structure:** two-row comparison (MERCHANT vs. AMAZON): order count, revenue, % share, status-distribution mini-breakdown.
- **Suggested action:** evaluate whether shifting SKUs between fulfillment channels matches the observed status-distribution pattern (informational only).
- **Limitations:** no shipping-speed/on-time-delivery metric exists (only the coarse `fulfillment_status` enum); no fee data exists to compare cost between channels.
- **UI deep link:** `/seller/orders/fulfillment-mix?range=30d`
- **Evaluation fixture:** 10 MERCHANT orders (7 SHIPPED, 2 UNSHIPPED, 1 CANCELLED) and 15 AMAZON orders (14 SHIPPED, 1 PARTIALLY_SHIPPED) — asserting correct per-channel counts and percentages.
- **Priority:** Next — useful operational view, secondary to raw volume/SKU performance for most sellers.

### 11. Status Distribution

- **Business question:** What's the current state of all my open and recent orders?
- **Example user prompts:**
  - "How many orders are still unshipped?"
  - "Give me a breakdown of my order statuses right now."
  - "Do I have any orders stuck in a weird state?"
- **Required data:** `amazon_seller_orders.fulfillment_status`, `.amazon_order_id`, `.amazon_created_at`, `.amazon_last_updated_at`.
- **Deterministic calculations:** Group currently stored orders by `fulfillment_status`; count and compute percentage share. A snapshot, not a trend — pure counting.
- **Evidence/freshness requirements:** `observed` claim, explicitly framed as current-state (not a change history) — a status reflects the last synced update, not necessarily Amazon's live state at query time.
- **Proposed Copilot tool:** `get_order_status_distribution` — wraps a group-by-count over `fulfillment_status`.
- **Response structure:** count table (status enum, count, % of total); freshness/watermark note prominently placed given the current-state caveat.
- **Suggested action:** open Seller Central for any status bucket that looks unexpectedly large (e.g., UNSHIPPED).
- **Limitations:** no status *history* (e.g., how long an order sat in PENDING) since only current state is stored; a shown status may be out of date if Amazon changed it after the last sync.
- **UI deep link:** `/seller/orders?groupBy=status`
- **Evaluation fixture:** 12 orders spanning all 6 `fulfillment_status` values with uneven counts, one with `amazon_last_updated_at` older than `synced_through_at` — asserting correct counts/percentages and that the stale record still appears with the freshness caveat triggered.
- **Priority:** Launch — simple, high-utility operational snapshot using data already stored.

### 12. Cancellations

- **Business question:** How many of my orders/items are getting cancelled, and by whom?
- **Example user prompts:**
  - "What's my cancellation rate this month?"
  - "Show me cancelled orders and who cancelled them."
  - "Are cancellations going up?"
- **Required data:** `amazon_seller_orders.was_cancelled`, `.amazon_order_id`, `.amazon_created_at`, `.order_total_amount`; `amazon_seller_order_items.was_cancelled`, `.cancel_requester`/`.cancelled_by` (enum only), `.quantity_ordered`, `.item_proceeds_amount`.
- **Deterministic calculations:** Compute the count/percentage of `was_cancelled=true` orders and items within a window relative to totals; group cancelled items by `cancel_requester`/`cancelled_by`; sum exposed `item_proceeds_amount`/`order_total_amount`. Counting/summation only — no reason-coding, since no free-text reason is stored.
- **Evidence/freshness requirements:** `calculated` claim, explicitly noting the enum-only nature of the requester field ("who cancelled" is a coarse category, not a reason).
- **Proposed Copilot tool:** `get_cancellation_summary` — wraps counts/rates of cancelled orders/items grouped by `cancel_requester`/`cancelled_by`.
- **Response structure:** headline cancellation rate (orders and items separately); breakdown by requester enum; revenue exposed to cancellation; freshness note.
- **Suggested action:** investigate SKUs/orders with a concentration of seller-initiated cancellations (informational only).
- **Limitations:** cannot explain *why* an order/item was cancelled — no free-text reason is stored, only the requester enum; cannot distinguish partial vs. full cancellation reasoning beyond the item-level flag.
- **UI deep link:** `/seller/orders?status=cancelled`
- **Evaluation fixture:** 20 orders, 3 fully cancelled with `cancel_requester=CUSTOMER`, 2 orders with one cancelled item each with `cancelled_by=SELLER`, rest uncancelled — asserting the rate and requester grouping match exactly.
- **Priority:** Launch — a top seller pain point, directly available without needing history-of-changes.

### 13. Velocity Changes

- **Business question:** Has my sales velocity for a specific product changed recently?
- **Example user prompts:**
  - "Is SKU ABC-123 selling faster or slower than last month?"
  - "Which of my products had the biggest jump or drop in order velocity?"
  - "Show me velocity changes across my catalog."
- **Required data:** `amazon_seller_order_items.seller_sku`, `.quantity_ordered`, joined to `amazon_seller_orders.amazon_created_at`, scoped to a `marketplace_participation_id` and the actual synced date range.
- **Deterministic calculations:** For each SKU, compute units-per-day in a recent window vs. a prior equal-length window (both fully within synced data); compute absolute/percentage change; rank by magnitude. Rate/ratio arithmetic only — no forecasting.
- **Evidence/freshness requirements:** `calculated` claim; both comparison windows must be checked against `synced_through_at`'s lookback — if the prior window predates it, that SKU's comparison must be marked incomplete/unavailable, never silently computed on partial data.
- **Proposed Copilot tool:** `get_sku_velocity_change` — wraps a per-SKU two-window units-per-day comparison.
- **Response structure:** ranked table (sku, recent velocity, prior velocity, % change); explicit exclusion list of SKUs whose prior window isn't fully covered.
- **Suggested action:** investigate a large drop against that SKU's current listing status (buyability/discoverability — see Section 3).
- **Limitations:** cannot compute velocity before the org's orders sync began; no seasonality or demand modeling — a raw rate comparison only.
- **UI deep link:** `/seller/orders/velocity?sku=...&compare=30d_vs_prior30d`
- **Evaluation fixture:** SKU A with 10 units in days 1–15 and 30 units in days 16–30 (both windows covered); SKU B whose prior window falls before the stored `synced_through_at` watermark (must be excluded) — asserting correct % change for A and correct exclusion for B.
- **Priority:** Next — valuable diagnostic, but needs sufficient accumulated sync history to be meaningful for new connections.

### 14. Stale Order States

- **Business question:** Do I have any orders that look stuck and haven't updated in a long time?
- **Example user prompts:**
  - "Are any of my orders stuck in unshipped for too long?"
  - "Show me orders that haven't changed status in weeks."
  - "Find orders that might need manual attention."
- **Required data:** `amazon_seller_orders.amazon_order_id`, `.fulfillment_status`, `.amazon_last_updated_at`, `.amazon_created_at`, `.fulfilled_by`.
- **Deterministic calculations:** Filter orders whose `fulfillment_status` is non-terminal (e.g., `PENDING_AVAILABILITY`, `PENDING`, `UNSHIPPED`, `PARTIALLY_SHIPPED`) and whose `amazon_last_updated_at` is older than a configurable threshold; sort by age. A date-diff/filter — the threshold is a parameter, not an invented rule presented as fact.
- **Evidence/freshness requirements:** `observed` claim, explicitly framed as "as of last successful sync" — an order may already have updated on Amazon's side since then.
- **Proposed Copilot tool:** `get_stale_orders` — wraps a filter of non-terminal-status orders older than a threshold since `amazon_last_updated_at`.
- **Response structure:** table (amazon_order_id, fulfillment_status, days since last update, fulfilled_by); freshness caveat stated prominently.
- **Suggested action:** check these specific orders directly in Seller Central; Copilot cannot confirm real-time status or write to Amazon.
- **Limitations:** cannot confirm current real-time Amazon status; cannot tell a genuine fulfillment problem from a normal slow shipping lane (no carrier/tracking data exists here).
- **UI deep link:** `/seller/orders?status=unshipped&stale=true`
- **Evaluation fixture:** 5 orders in non-terminal statuses aged 2–45 days, plus 2 terminal-status (SHIPPED/CANCELLED) orders that are old but must be excluded — asserting only non-terminal + over-threshold orders appear, oldest-first.
- **Priority:** Launch — directly actionable, prevents fulfillment SLA problems, computable from already-stored fields.

---

## Section 3 — Cross-dataset scenarios

### 15. Active Listings With No Orders

- **Business question:** Which of my live listings haven't sold anything?
- **Example user prompts:**
  - "Which active SKUs have zero orders?"
  - "Show me listings I'm maintaining that aren't selling at all."
  - "What's live in my catalog but never sold?"
- **Required data:** `amazon_seller_listings` (`is_active=true`, `sku`, `asin`, `item_name`, `status`) left-joined against `amazon_seller_order_items.seller_sku` (existence within the synced order window) for the same `marketplace_participation_id`.
- **Deterministic calculations:** Set difference — active SKUs with zero matching order items in the queried/synced window; context count of days active from `first_seen_at`. A filter/join, not a scoring model.
- **Evidence/freshness requirements:** Combined `observed` claims (listings snapshot + order-items existence check), each disclosing its own freshness — a SKU may simply be too new for orders to have synced yet.
- **Proposed Copilot tool:** `get_zero_order_active_listings` — wraps a listings/orders anti-join scoped to a lookback window.
- **Response structure:** table (sku, item_name, status flags, days since first_seen_at, orders-window checked); established zero-sellers separated from SKUs too new to judge.
- **Suggested action:** review pricing/content/discoverability for established zero-sellers; treat very new SKUs as "too early to tell."
- **Limitations:** cannot say *why* a listing isn't selling (traffic, price competitiveness, demand data are outside this schema); "no orders" is bounded by the orders lookback, not true all-time history.
- **UI deep link:** `/seller/listings?filter=no_orders&range=30d`
- **Evaluation fixture:** SKU A active 200 days with zero order items in a 30-day window (true zero-seller); SKU B active 3 days with zero order items (too new); SKU C active with 2 order items (excluded) — asserting the three-way split.
- **Priority:** Launch — a natural, simple-join combination of two already-launched datasets.

### 16. High-Selling Products With Listing Errors

- **Business question:** Are any of my best sellers currently showing listing errors that could hurt them?
- **Example user prompts:**
  - "Do any of my top-selling SKUs have listing issues right now?"
  - "Which high performers are at risk from listing errors?"
  - "Cross-check my top sellers against listing health."
- **Required data:** `amazon_seller_order_items.seller_sku`, `.quantity_ordered`/`.item_proceeds_amount` (ranking, as in Scenario 8) joined with `amazon_seller_listings.sku`, `.issues[].severity`/`.code`.
- **Deterministic calculations:** Rank SKUs by units/proceeds over a window, then filter to SKUs whose current listing carries ≥1 `ERROR`-severity issue. Rank + filter only — no weighting formula.
- **Evidence/freshness requirements:** `calculated` claim (ranking) combined with `observed` claim (current issues); both independently-timed freshness windows (orders window + listings sync) disclosed together.
- **Proposed Copilot tool:** `get_top_sellers_with_listing_errors` — wraps a SKU performance ranking filtered by current ERROR-severity issues.
- **Response structure:** table (sku, item_name, units/proceeds in window, error issue codes/messages); explicit "top sellers ∩ current errors" framing, not a combined risk score.
- **Suggested action:** prioritize fixing these SKUs first — they carry the most revenue at stake.
- **Limitations:** cannot quantify future revenue actually at risk (would assume the issue causes a decline, which is not observed, only correlated); no COGS/margin, so "high-selling" is revenue/units, not profit.
- **UI deep link:** `/seller/listings?filter=top_sellers_with_errors&range=30d`
- **Evaluation fixture:** SKU A (top by units, 1 ERROR issue), SKU B (top by units, no issues), SKU C (low units, 2 ERROR issues) — asserting only SKU A appears in the intersection.
- **Priority:** Launch — combines two fully-implemented datasets to surface the highest-value fix-it-first list.

### 17. Revenue/Order Exposure Associated With Critical Listing Issues

- **Business question:** How much of my recent revenue is tied to SKUs that currently have critical listing issues?
- **Example user prompts:**
  - "How much revenue is at risk from listing errors?"
  - "What dollar amount is tied to SKUs with critical issues?"
  - "Show me my error-exposed revenue for the last 30 days."
- **Required data:** `amazon_seller_listings.sku`, `.issues[].severity` joined with `amazon_seller_order_items.seller_sku`, `.item_proceeds_amount`/`.currency`, `.quantity_ordered`, scoped to `amazon_seller_orders.amazon_created_at`.
- **Deterministic calculations:** Sum `item_proceeds_amount`/`quantity_ordered` for order items whose SKU currently has ≥1 `ERROR`-severity issue, within the window, grouped by currency; express as a % of total window proceeds. Filtered sum + ratio only.
- **Evidence/freshness requirements:** `calculated` claim, explicitly stated as retrospective "proceeds associated with the SKU, observed in the order window" — **not** a prediction of future lost revenue, since the sale already happened despite the issue existing.
- **Proposed Copilot tool:** `get_revenue_exposed_to_critical_issues` — wraps the filtered-sum join described above.
- **Response structure:** headline dollar amount(s) by currency and % of total window revenue; supporting table of contributing SKUs; explicit "historical exposure, not a loss forecast" disclaimer.
- **Suggested action:** treat these SKUs as top priority fixes since they carry proven revenue.
- **Limitations:** does not mean revenue will be lost if unfixed, nor that revenue was already lost because of the issue — no causal or predictive claim is possible; multi-currency totals are never combined.
- **UI deep link:** `/seller/orders?filter=revenue_exposed_critical_issues&range=30d`
- **Evaluation fixture:** 3 SKUs with ERROR issues generating $500/$200/$0 in a 30-day window, plus 2 healthy SKUs generating $1000 combined — asserting exposed total $700, correct % of $1700, and no forward-looking language in the expected response shape.
- **Priority:** Next — high value but requires careful wording to avoid implying a forecast.

### 18. Non-Buyable Products With Historical Orders

- **Business question:** Did any product that used to sell go non-buyable?
- **Example user prompts:**
  - "Which of my previously-selling SKUs are now not buyable?"
  - "Show me products with order history that I can't currently sell."
  - "What used to work but is broken now?"
- **Required data:** `amazon_seller_listings.sku`, `.status` (absence of `BUYABLE`), `.is_active` joined with `amazon_seller_order_items.seller_sku` having ≥1 historical row within the synced order history.
- **Deterministic calculations:** Filter listings currently missing `BUYABLE` whose SKU appears in at least one synced order item; report the most recent `amazon_created_at` among that SKU's orders for context. Filter/join + max-date lookup only.
- **Evidence/freshness requirements:** `calculated` claim combining current `observed` listings state with historical `observed` orders state; both coverage windows disclosed — "historical" means "since ASI started syncing," not Amazon's full order history.
- **Proposed Copilot tool:** `get_non_buyable_with_order_history` — wraps the filter/join described above.
- **Response structure:** table (sku, item_name, current status flags, issue codes, most recent order date within synced history, total historical units); freshness/coverage caveat on both sides.
- **Suggested action:** treat these as regression cases — highest priority since they have proven demand and are now blocked from selling.
- **Limitations:** "historical orders" only covers the synced lookback (bounded by the 2-year ceiling), not the SKU's full selling lifetime; cannot assert causation without also running the velocity-change scenario.
- **UI deep link:** `/seller/listings?filter=non_buyable_with_history`
- **Evaluation fixture:** SKU A currently non-buyable with 5 historical order items; SKU B currently non-buyable with 0 historical order items (excluded); SKU C currently buyable with historical orders (excluded) — asserting only A appears.
- **Priority:** Launch — a clear regression signal from a simple join of two implemented datasets.

### 19. Discoverability Loss Associated With Declining Activity

- **Business question:** Did losing discoverability line up with a drop in orders for that product?
- **Example user prompts:**
  - "Is my sales drop for SKU ABC-123 related to it losing discoverability?"
  - "Show me SKUs that lost search visibility and also slowed down."
  - "Did discoverability issues correlate with fewer orders?"
- **Required data:** `amazon_seller_listings.sku`, `.status` across multiple ingestion runs (to detect when `DISCOVERABLE` was lost), `.first_seen_at`/`.last_seen_at`, joined with `amazon_seller_order_items.seller_sku`/`.quantity_ordered` and `amazon_seller_orders.amazon_created_at` (reusing Scenario 13's before/after method).
- **Deterministic calculations:** Identify the run at which a SKU's status first stopped including `DISCOVERABLE` (requires listings history, per Scenario 6); compute units-per-day immediately before vs. after that run's date (per Scenario 13); present both facts side by side. A temporal correlation of two independently observed facts — never presented as causal.
- **Evidence/freshness requirements:** `historical`/`calculated` claims placed side by side, each with its own coverage caveat; synthesis must state "correlation only, not causation."
- **Proposed Copilot tool:** `get_discoverability_loss_correlation` — wraps the paired listings-history lookup + before/after velocity computation for a SKU.
- **Response structure:** timeline (date discoverability lost, prior velocity, subsequent velocity, % change); explicit correlation-not-causation disclaimer.
- **Suggested action:** review listing content/attributes around the time discoverability was lost (see Scenario 4).
- **Limitations:** requires ≥2 listings syncs bracketing the loss event and sufficient order history on both sides — unavailable for newly connected accounts; can never prove causation.
- **UI deep link:** `/seller/listings/discoverability-correlation?sku=...`
- **Evaluation fixture:** a SKU with `DISCOVERABLE` present in run 1, absent in run 2 (day 20), with 40 units in days 1–19 vs. 5 units in days 21–40 — asserting the correct loss date and velocity delta with no causal language in the expected output.
- **Priority:** Later — requires multi-run listings history and order history on both sides of an event, unlikely for most orgs at launch.

### 20. Healthy Listings With Unexpectedly Weak Sales

- **Business question:** Why isn't this listing selling if nothing looks wrong with it?
- **Example user prompts:**
  - "Show me listings with no issues that still aren't selling well."
  - "Which healthy SKUs are underperforming compared to my catalog average?"
  - "Find listings that look fine but have low sales."
- **Required data:** `amazon_seller_listings.sku`, `.status` (`BUYABLE`+`DISCOVERABLE` both present), `.issues` (empty or `WARNING`-only) joined with per-SKU aggregated units/proceeds from `amazon_seller_order_items` over a window, compared against a median computed from the same org's own order items.
- **Deterministic calculations:** Compute units-per-SKU for the window across active SKUs; compute the org's own median (or another simple percentile); filter to "healthy" SKUs (no ERROR issues, BUYABLE+DISCOVERABLE present) that fall below that self-computed threshold. A within-catalog relative comparison only — no external Rainforest benchmark mixed in.
- **Evidence/freshness requirements:** `calculated` claim, explicitly scoped as relative to this seller's own catalog in this window, not an external/category benchmark.
- **Proposed Copilot tool:** `get_healthy_low_performers` — wraps the healthy-listing filter + within-catalog percentile comparison.
- **Response structure:** table (sku, item_name, units in window, org median units, % below median); explicit statement that no external market benchmark is used.
- **Suggested action:** consider pricing, imagery, or reviews (levers outside this schema), or manually compare the listing to competitors.
- **Limitations:** cannot explain the actual cause — price competitiveness, reviews, images, and competitor activity are not in this schema (Rainforest holds that separately and must not be blended into this seller-only claim); a small catalog yields a statistically weak median.
- **UI deep link:** `/seller/listings?filter=healthy_low_performers&range=30d`
- **Evaluation fixture:** 6 healthy active SKUs with window units `[50, 45, 40, 5, 3, 48]` — asserting the two low SKUs (5 and 3 units) are flagged and the rest are not.
- **Priority:** Later — useful but statistically thin for small catalogs; easy to over-read as diagnostic when it is only a within-catalog ranking.

### 21. Ordered SKUs Absent From The Current Listings Snapshot

- **Business question:** Did I sell something that's no longer in my active catalog?
- **Example user prompts:**
  - "Are there SKUs I've sold that don't show up in my listings anymore?"
  - "Show me orders for products that seem to have disappeared from my catalog."
  - "What happened to the listing for a SKU I know I sold?"
- **Required data:** `amazon_seller_order_items.seller_sku` (distinct set within a window) left-joined against `amazon_seller_listings.sku` (current `is_active=true` rows) for the same `marketplace_participation_id`.
- **Deterministic calculations:** Set difference — `seller_sku` values present in recent order items but absent from (or `is_active=false` in) the current listings snapshot. Anti-join only — no inference of cause.
- **Evidence/freshness requirements:** Combined `observed` claims; both freshness windows disclosed (listings sync date, orders window), flagging that "absent from listings" could mean genuine deactivation *or* simply that the next full-catalog resync hasn't re-confirmed it yet.
- **Proposed Copilot tool:** `get_ordered_skus_missing_from_listings` — wraps the order-items-to-listings anti-join.
- **Response structure:** table (sku, asin/item_name from the order-item record, last order date, order count in window); explicit ambiguity note (deactivated vs. sync-timing gap).
- **Suggested action:** check Seller Central directly for that SKU's current status — this scenario can only flag the mismatch, not resolve it.
- **Limitations:** cannot distinguish "Amazon deactivated it" from "the last resync just hasn't re-confirmed it yet" — both look identical here; `item_name`/`asin` come from the order record and may be stale relative to the live catalog.
- **UI deep link:** `/seller/orders?filter=sku_missing_from_listings`
- **Evaluation fixture:** SKU A with 3 recent order items and no current active listings row; SKU B with order items and an `is_active=false` row (also flagged); SKU C with order items and an active current row (excluded) — asserting A and B appear, C does not.
- **Priority:** Next — a genuinely useful data-integrity check, lower frequency need than the top-line Launch scenarios.

### 22. Marketplace Expansion Opportunities

- **Business question:** Which of my proven products aren't listed yet in my other connected marketplaces?
- **Example user prompts:**
  - "Which of my best sellers aren't listed on my other marketplace?"
  - "Where could I expand a product that's already working well?"
  - "Show me gaps between my marketplaces for my top SKUs."
- **Required data:** `amazon_seller_order_items.asin`/`.seller_sku` with per-participation aggregated performance (per Scenario 8), joined with `amazon_seller_listings.asin` per participation, to find ASINs performing well in one connected participation with no current listings row in another connected participation for the same organization.
- **Deterministic calculations:** For each ASIN with proven order volume in participation X, check for a matching active listings row (by `asin`) in participation Y under the same `organization_id`; report the gap. Filter/join only — no invented opportunity-value estimate for a marketplace ASI has no seller data in.
- **Evidence/freshness requirements:** `calculated` claim combining two participations' `observed` listings snapshots and one participation's `observed` order aggregate; explicitly scoped to already-connected participations only — it says nothing about marketplaces ASI has never connected through.
- **Proposed Copilot tool:** `get_marketplace_expansion_gaps` — wraps the per-ASIN cross-participation listings gap check, gated to already-connected participations.
- **Response structure:** table (asin, item_name, proven performance in source marketplace, present/absent in each other connected marketplace); explicit "connected marketplaces only" scope note.
- **Suggested action:** consider creating a listing for that ASIN in the gap marketplace via Seller Central (informational, no automated listing creation).
- **Limitations:** says nothing about marketplaces the seller has never connected through ASI; cannot estimate expected demand/revenue in the target marketplace (no order data exists there by definition); no eligibility/compliance/catalog-restriction data.
- **UI deep link:** `/seller/expansion-gaps`
- **Evaluation fixture:** an org with 2 connected participations (US, CA); ASIN X has strong US order volume and an active US listing but no CA listings row; ASIN Y has an active listing in both — asserting only ASIN X appears as a gap.
- **Priority:** Later — strategically valuable but requires ≥2 connected marketplace participations, a minority case at current adoption.

### 23. Data-Quality and Freshness Anomalies

- **Business question:** Can I trust the numbers Copilot is giving me right now, or is something out of date/broken?
- **Example user prompts:**
  - "Is my data up to date?"
  - "Why does this answer look stale?"
  - "Did my last sync actually finish?"
- **Required data:** `amazon_ingestion_runs` (status, run type, timestamps) per `marketplace_participation_id`; listings/orders freshness fields (`last_seen_at`, `synced_through_at` watermark) compared against current time and against each other.
- **Deterministic calculations:** Per participation, compute time-since-last-successful-run per dataset (listings, orders); flag any run recorded failed/incomplete/in-progress; flag a large gap between the two datasets' freshness (e.g., listings synced today but orders watermark weeks old) as an inconsistency. Comparison/flagging only — no imputation of missing data.
- **Evidence/freshness requirements:** This scenario *is* the freshness/provenance layer itself — every claim it returns is a provenance fact, not a business metric, and is the thing every other scenario's freshness disclosure ultimately points back to.
- **Proposed Copilot tool:** `get_data_freshness_report` — wraps a per-participation summary of ingestion-run status and dataset watermarks.
- **Response structure:** per-participation table (dataset, last successful run time, run status, time since, stale/incomplete flag); overall "safe to trust" vs. "stale/incomplete — treat other answers with caution" banner.
- **Suggested action:** wait for the next sync, or re-trigger connection validation through existing connection-management flows if a run shows persistent failure (no new write path).
- **Limitations:** cannot fix the sync itself or diagnose *why* a run failed beyond what the ingestion run record captures; cannot backfill a coverage gap except by a future successful run.
- **UI deep link:** `/seller/data-health`
- **Evaluation fixture:** participation A with listings synced 1 hour ago and orders synced 40 days ago (large-gap flag); participation B with an orders run currently in-progress and no completed run yet (incomplete/no-safe-data flag); participation C with both datasets synced within the last day (no flags) — asserting correct flags per participation.
- **Priority:** Launch — the trust backbone every other scenario's freshness disclosure depends on.

---

## Explicitly unavailable conclusions

**Profit without fees/COGS.** `amazon_seller_orders`/`amazon_seller_order_items` expose `order_total_amount`, `item_proceeds_amount`, and `proceeds_breakdown` (categories: ITEM, SHIPPING, TAX, DISCOUNT) — gross revenue/proceeds figures, not profit. No COGS field exists on any Listings/Orders table, and no fee category (referral fee, FBA fee, etc.) is listed among `proceeds_breakdown`'s categories. COGS is exclusively seller-input data owned by the separate profit feature. Net margin therefore cannot be computed from Listings/Orders alone; Listings/Orders scenarios in this document stop at revenue/proceeds claims and explicitly defer margin questions to the existing profit feature.

**Inventory stockout without Inventory data.** `amazon_seller_listings.fulfillment_availability` gives only channel + quantity as observed at the last full-catalog resync — it is not a stockout event log, not a real-time inventory feed, and not backed by any dedicated Inventory ingestion (explicitly listed as a future, unimplemented integration). No scenario here can attribute a sales change to "went out of stock" — at most, Scenario 19's velocity-correlation pattern could be reused if Inventory data existed, but it does not, so stockout causation is never claimed.

**Advertising attribution.** No Ads API integration exists yet (a distinct, future 12C-scoped effort). No advertising spend, campaign, keyword, or click/attribution field exists anywhere in `amazon_seller_listings` or `amazon_seller_orders`/`amazon_seller_order_items`. ACOS/TACOS/ROAS are computed entirely inside the separate profit/ads feature from seller-input and (future) ads data — not from Listings/Orders. No scenario in this document can say an order was ad-driven or compute an ad-attributed revenue share.

**Returns analysis.** No returns table or dataset exists in the current schema (explicitly listed absent: "returns data (not implemented)"). `was_cancelled` on `amazon_seller_orders`/`amazon_seller_order_items` captures pre-fulfillment cancellation only — a different Amazon lifecycle event from a post-fulfillment return/refund/RMA. No return rate, return reason, or refund amount can be reported.

**Repeat-customer or buyer segmentation using prohibited PII.** No buyer/recipient name, email, address, phone, or any other buyer identifier exists in `amazon_seller_orders` or `amazon_seller_order_items` — buyer PII is explicitly and permanently excluded from ASI's data model, not merely unimplemented. Without a stable buyer identifier there is no way to determine that two orders belong to the same customer, so "repeat customer rate," "new vs. returning buyer," or any buyer segmentation is structurally impossible under this architecture — a future data source could not add it without violating the no-PII rule itself.

---

## Summary

- **Total scenarios:** 23 (6 listing, 8 order, 9 cross-dataset).
- **Launch:** 12 — Scenarios 1, 2, 3, 7, 8, 11, 12, 14, 15, 16, 18, 23.
- **Next:** 7 — Scenarios 4, 6, 9, 10, 13, 17, 21.
- **Later:** 4 — Scenarios 5, 19, 20, 22.

This document is the authoritative next-implementation backlog for surfacing Listings and Orders data through Copilot, and every scenario above respects the existing deterministic-Python/AI-synthesis split, the `ToolRegistry` execution boundary, and `EvidenceEnvelope` provenance requirements. No LLM Skill implementation exists yet anywhere in this backlog — this is planning only, pending explicit approval to begin 12B.9 (Connect stable seller-data tools to intelligence/Copilot) or any earlier milestone that precedes it on the approved roadmap.

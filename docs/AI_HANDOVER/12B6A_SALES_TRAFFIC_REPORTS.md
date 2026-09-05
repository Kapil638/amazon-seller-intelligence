# 12B.6A — Sales and Traffic Business Reports Ingestion

Durable record of the 12B.6A implementation pass. Branch:
`milestone-12b6a-sales-traffic-reports`, created from verified `main` at
`af2a762` (the genuine two-parent merge of PR #16 /
`milestone-12b5b-copilot-intelligence-cache`). Implement/test/review
only — no commit, push, PR, migration, live Amazon call, reconnection,
role change, production worker start, backfill, or production mutation
was authorized or performed while producing this milestone.

## 0. Phase 0 — verified base

- `git fetch origin` → `18316dd..af2a762 main -> origin/main` (5 new
  commits: the 12B.5B PR #16 merge and its 4 constituent commits).
- `git log -1 --format="%H %P" af2a762` → parents `18316dd` (previous
  `main` tip) and `171ccfb` (12B.5B branch tip) — confirmed genuine
  two-parent merge.
- `git merge-base --is-ancestor 171ccfb origin/main` and the same for
  `c83fded` — both confirmed ancestors.
- Local `main` fast-forwarded `18316dd..af2a762`; `git rev-parse main
  origin/main` confirmed equal.
- Working tree confirmed to contain only the seven pre-existing,
  unrelated Log Analyzer/ADR paths (`docs/adr/README.md`,
  `docs/adr/0007-...md`, `docs/adr/0008-...md`, `docs/operations/
  OPS1_*.md` × 4) — checksummed (SHA-256) and preserved byte-for-byte;
  never staged. Checksums recorded in this session's own record; the
  set and content are unchanged from every prior milestone's own check
  of the same paths.
- Alembic head confirmed as `0013_orders_durable_pagination` (the
  task's own prompt text contained a garbled fragment —
  "0013_orders_durable_plir adds? Tape" — treated as noise per the
  prompt's own "ignore any malformed text" instruction; the actual
  repository value matches every prior handover and is unchanged).
- Live Supabase revision independently confirmed identical
  (`SELECT version_num FROM alembic_version` via the app's own
  `get_engine()`, using the narrow `ASI_ALLOW_PRODUCTION_DB_ACCESS=1`
  override for this one reviewed read-only diagnostic call — read-only,
  no write, no context manager left running).
- Sanitized production evidence counts recorded (aggregate only, no
  identifier), matching the 12B.5B checkpoint exactly (no drift since
  that report — confirming no live Amazon call or worker run happened
  during the 12B.5B remediation work): 10 Listings rows, 153 Orders
  rows, 154 order-item rows, 6 marketplace participations, latest
  successful Listings run `succeeded` (2026-09-01), latest successful
  Orders run `succeeded` (2026-09-03), 6 Orders sync checkpoints, 1
  active Listings job and 1 active Orders job outstanding (pre-existing
  queued state, unchanged), no worker process running.

## 1. Phase 1 — official contract, pinned

**Source of truth:** `amzn/selling-partner-api-models`, `main` branch,
fetched directly (not inferred from Seller Central or community
answers):
- `schemas/reports/sellerSalesAndTrafficReport.json` — the report
  document schema itself, fetched and reproduced in full during this
  session (raw JSON, JSON Schema draft-07). **SHA-256 of the fetched
  file:** `912c0d01073c4770f8bbd1e24f5553afa197920ac12b37d2b59726118c21105d`
  (1765 lines), fetched from `main` at the raw GitHub URL
  `https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/schemas/reports/sellerSalesAndTrafficReport.json`
  during the final pre-commit audit and re-verified line-by-line against
  every specific claim this document makes: `SalesAndTrafficByAsin`'s
  `required`/`properties` (no `date` field — confirmed), `buyBoxPercentage`'s
  `minimum: 0, maximum: 100` (confirmed), `unitSessionPercentage`'s
  `minimum: 0` with no `maximum` plus its own `300.00` worked example at
  three separate lines (confirmed), and `reportType: "GET_SALES_AND_
  TRAFFIC_REPORT"` (confirmed). This checksum was not recorded when this
  document was first written despite claiming the file was "pinned" — a
  documentation gap the final pre-commit audit found and closed by
  re-fetching the live file rather than fabricating a value. `main`
  is a moving branch, not an immutable tag; this checksum identifies the
  exact bytes this implementation was actually built and verified
  against, not a promise that the file will never change upstream.
- `models/reports-api-model/reports_2021-06-30.json` — the Reports API
  v2021-06-30 operation/model definitions (`createReport`, `getReport`,
  `getReportDocument`, `getReports`, `Report`, `ReportDocument`,
  `CreateReportSpecification`).
- `developer-docs.amazon/sp-api/docs/report-type-values-analytics` —
  the official report-type reference page (role, scheduling,
  throttling note specific to this report type).

**Reports API version:** `2021-06-30`.

**Report type:** `GET_SALES_AND_TRAFFIC_REPORT` (fixed enum value on
`reportSpecification.reportType`; no other value is valid for this
schema).

**Required role:** **Brand Analytics.** Confirmed directly from the
official report-type reference page: *"SP-API Role Required: Brand
Analytics"*. The same page explicitly states *"No Brand Registry
registration is required. The report is available to all sellers with
the Brand Analytics role."* — this corrects an earlier, less precise
search-summary finding that suggested Brand Registry enrollment was
required; the directly-fetched official page is treated as
authoritative over a search-engine synopsis. The role is selected in
Amazon Developer Central for the application, then the seller must
re-authorize (re-run the Website Authorization Workflow) for the
newly-added role's consent to attach to their existing authorization —
an existing seller connection authorized *before* this role was added
to the app registration does not retroactively gain it.

**Seller and marketplace availability:** available to "Sellers"
(not vendors — see the schema-family split below). No further
seller-eligibility gate documented beyond holding the Brand Analytics
role.

**Requested and/or scheduled:** the official reference page states the
report "can be requested or scheduled" — both on-demand (`createReport`)
and Amazon's own report-schedule mechanism (`createReportSchedule`) are
supported by the report type. This milestone implements on-demand
`createReport` only; report scheduling is not used (matches "Polling is
acceptable for this milestone," §6 below, and avoids taking on a second,
Amazon-driven trigger surface for a v1).

**Request date constraints** (from the pinned schema's own field
descriptions on `dataStartTime`):
- "If the start date of the report is more than two years ago, the
  report will be cancelled" — **2-year maximum lookback**, enforced by
  Amazon itself (a report requested outside this window reaches
  `CANCELLED`, not an immediate request-time rejection).
- For `WEEK`/`MONTH` `dateGranularity`, Amazon **silently expands** the
  requested start/end dates outward to the natural period boundary
  (week starts Sunday, month starts on the 1st) before generating the
  report — the actual data returned can cover a wider window than
  requested. This milestone requests `DAY` granularity exclusively
  (§ grain conclusion below), which has no such expansion behavior.

**Supported `dateGranularity` values:** `DAY`, `WEEK`, `MONTH`.
Defaults to `DAY` if omitted. Governs **only** the bucketing of
`salesAndTrafficByDate` rows — see the grain conclusion below.

**Supported `asinGranularity` values:** `PARENT`, `CHILD`, `SKU`.
Defaults to `PARENT` if omitted. Governs the identifier fields present
on each `salesAndTrafficByAsin` row (`childAsin` only present for
`CHILD`/`SKU`; `sku` only present for `SKU`) — it does **not** change
how many rows are returned per distinct product beyond the
identifier-key granularity itself (one row per distinct
parent/child/SKU key in the response).

**Document format:** JSON (this schema). No CSV/flat-file/XML variant
is defined for this report type in the pinned schema.

**Compression:** `ReportDocument.compressionAlgorithm` has exactly one
enum value: `GZIP`. Absence of this field means the document is
uncompressed. No other compression algorithm is ever valid for this
API — any other value is a contract violation and must be rejected,
never guessed-and-decompressed.

**Processing states** (`Report.processingStatus` enum, from the pinned
Reports API model): `IN_QUEUE`, `IN_PROGRESS`, `DONE`, `CANCELLED`,
`FATAL`. `IN_QUEUE`/`IN_PROGRESS` are non-terminal (keep polling);
`DONE`/`CANCELLED`/`FATAL` are terminal. `DONE` is the only state with
a populated `reportDocumentId` to retrieve. `CANCELLED` covers both an
operator-driven cancellation and Amazon's own automatic cancellation
(e.g. the 2-year-lookback violation above) — this schema gives no way
to distinguish those two cancellation causes from the `Report` object
alone.

**Rate limits and relevant headers** (from the pinned Reports API
model's per-operation rate-limit annotations, plus the report-type-
specific throttling note):
- `createReport`: generic Reports API bucket is 0.0167 requests/second
  (≈ 1 per 60s), burst 15. **This specific report type additionally
  documents a tighter, report-type-level restriction: "You can request
  this report up to three times every five minutes"** (≈ 1 per 100s
  sustained) — the more specific, more conservative figure, and the one
  this milestone's worker/backfill scheduler is designed against (§7).
  The same page recommends starting a retry backoff at one minute and
  doubling on each subsequent 429.
- `getReport`: 2 requests/second, burst 15 — cheap to poll relative to
  `createReport`.
- `getReportDocument`: 0.0167 requests/second, burst 15 — call this
  exactly once per completed report (immediately followed by the
  actual document download, never a second time for the same report),
  never in a poll loop.
- `getReports` (list): 0.0222 requests/second, burst 10 — not used by
  this milestone's worker (it tracks its own report/document ids
  durably; it never needs to re-list Amazon's report history to
  recover state).
- Amazon's `Retry-After` header, when present on a 429, is honored
  exactly as this repository's existing SP-API clients already do
  (Listings/Orders clients' own bounded-retry convention, reused
  verbatim — see §5).

**Duplicate-report/request restrictions:** no explicit "duplicate
report rejected" error is documented in the pinned model; the
*de facto* restriction is the rate limit itself (three requests per
five minutes for this report type) — requesting the same window twice
in quick succession consumes rate-limit budget and produces two
independent, billably-identical report generations, not a rejection.
This milestone's own durable-run design (§6) prevents this at the
application level: one active job per protected scope, and no
duplicate `createReport` call after a worker restart if a `reportId`
already exists for the current attempt.

**Retention limits for report metadata/documents:** the pinned
`ReportDocument.url` field description states plainly: **"This URL
expires after 5 minutes."** No separate retention period is documented
for the `Report`/`reportDocumentId` metadata itself (Amazon's own
`getReports`/`getReport` history is understood, from this repository's
existing Listings/Orders experience with the same Reports API family,
to remain queryable for a materially longer window than 5 minutes, but
the presigned document URL specifically is single-use-window only).
This is the direct evidentiary basis for "never persist the pre-signed
URL" (§2) — it would be stale within minutes of being stored regardless
of any privacy concern.

**Marketplace IDs — one or multiple:** the pinned
`reportSpecification.marketplaceIds` field description states: **"This
report type supports only one marketplaceId per report. Specifying
multiple marketplaces will result in failure to generate the report."**
Confirmed **one marketplace ID per report request**, structurally
different from the generic `CreateReportSpecification.marketplaceIds`
schema (which allows 1–25 items generically, for report types that do
support multi-marketplace requests) — this report type overrides that
generic allowance down to exactly one.

## 1a. Critical grain question — resolved from the pinned schema

**Question:** does `dateGranularity=DAY` make `salesAndTrafficByAsin`
daily, or is the ASIN section aggregated across the entire requested
range without a date field?

**Answer, proven directly from the pinned schema's own `required`/
`properties` for the `SalesAndTrafficByAsin` definition:**

```json
"SalesAndTrafficByAsin": {
  "required": ["parentAsin", "salesByAsin", "trafficByAsin"],
  "properties": {
    "parentAsin": { "type": "string" },
    "childAsin": { "type": "string" },
    "sku": { "type": "string" },
    "salesByAsin": { "$ref": "#/definitions/SalesByAsin" },
    "trafficByAsin": { "$ref": "#/definitions/TrafficByAsin" }
  }
}
```

**`salesAndTrafficByAsin` has no `date` field anywhere in its schema.**
`dateGranularity` has **zero effect** on the ASIN section — it governs
*only* `salesAndTrafficByDate`'s bucketing. Every `salesAndTrafficByAsin`
row is a single aggregate over the *entire* requested
`dataStartTime`–`dataEndTime` window, for exactly one report request,
regardless of what `dateGranularity` was specified. The pinned schema's
three worked examples confirm this directly: a `DAY`-granularity
4-day-window example, a `WEEK`-granularity 4-day-window example, and a
`MONTH`-granularity 4-day-window example all produce ASIN rows with
*identical* per-ASIN values (e.g. `parentAsin: "B123456789"` always
shows `unitsOrdered: 1`, `orderedProductSales.amount: 16.79`) across
all three — because in every case the underlying requested window
(`2021-06-11` to `2021-06-14`) was the same 4 days; only the
`salesAndTrafficByDate` bucketing differed between the three examples.

**Consequence, stated explicitly per the task's own instruction:**
product-level (ASIN/SKU) *daily* facts require **one report request
per marketplace per day** (`dataStartTime == dataEndTime`, a one-day
window). A single larger-window report (e.g. 30 days, `DAY`
granularity) gives 30 dated *catalog-wide* rows in
`salesAndTrafficByDate`, but only **one** aggregate-over-30-days row
per ASIN/SKU in `salesAndTrafficByAsin` — never 30 per-ASIN-per-day
rows. This governs the entire backfill design in §7 and the schema
design in §4: the catalog-wide and product-level tables are
structurally different grains and must never be forced into one table.

**Exact grains, documented independently:**
- **Catalog/date grain:** `salesAndTrafficByDate[]` — one row per
  `date` bucket (day/week/month start, per the requested
  `dateGranularity`), aggregated across the seller's *entire catalog*
  for that marketplace. Always dated. Never product-specific.
- **Product/request-window grain:** `salesAndTrafficByAsin[]` — one row
  per distinct ASIN/SKU key, aggregated across the *entire* requested
  `dataStartTime`–`dataEndTime` window for that one report request.
  Never dated (no `date` field exists on this row shape at all). A
  "daily" product fact is only obtainable by requesting a 1-day window
  report and treating its ASIN rows as that day's facts — the *table*
  storing them must record the exact requested window
  (`dataStartTime`/`dataEndTime`) it came from, never infer or invent a
  single "date" for it.
- **Marketplace grain:** exactly one `marketplaceId` per report
  request/response (contract-enforced, §1) — both sections of a given
  report response share that same single marketplace scope.
- **Parent/child/SKU grain:** governed by `asinGranularity`
  (`PARENT`/`CHILD`/`SKU`) — determines which identifier fields
  (`parentAsin` always; `childAsin` added at `CHILD`/`SKU`; `sku` added
  only at `SKU`) are present on each `salesAndTrafficByAsin` row, and
  therefore what the natural key of that row actually is. A `PARENT`-
  granularity report's natural key is `parentAsin` alone (one row per
  parent, no per-variation breakdown); a `SKU`-granularity report's
  natural key is `(parentAsin, childAsin, sku)`.

This milestone requests **`dateGranularity=DAY`, `asinGranularity=SKU`,
one-day windows** as its default backfill/incremental shape — see §7 —
specifically because SKU is this project's existing natural-key
convention for product-level facts (matches Listings'/Orders' own
`seller_sku` keying) and because daily-shaped product facts are what
the milestone's own stated goals (traffic-vs-conversion trend, before/
after change detection) actually need.

## 2. Phase 2 — authorization and privacy boundary

**Is the Brand Analytics role expected to be included in the
application registration?** Not by default. This is a distinct,
separately-selectable role in Amazon Developer Central, added to the
app's own role list and then re-consented by the seller. Nothing in
this repository's `.env.example`, `app/core/config.py`, or the OAuth
scope-request code (`app/amazon/oauth.py`) currently references or
requires it — confirmed by direct inspection: no `role` or `scope`
column or constant anywhere mentions Brand Analytics, or any SP-API
role, today.

**Is role possession currently recorded anywhere?** **No.** Direct
inspection of `AmazonConnection` (`app/persistence/models.py`) confirms
no `role`/`scope`/`granted_roles` column exists on that table or any
other. This repository has no mechanism today to know, ahead of a live
API call, whether a given seller's authorization actually carries the
Brand Analytics role. This is a genuine, honestly-stated gap — not
fixed in this milestone (adding such a column is schema-adjacent scope
creep beyond "implement Sales and Traffic ingestion," and doing it
without a live authorization probe to populate it would only add an
unverifiable column).

**Do existing sellers need to reconnect/reauthorize?** **Yes, almost
certainly.** The one live Production grant this repository has ever
proven (`docs/checkpoints/2026-08-25-production-connect-amazon.md`)
predates this milestone and predates any Brand Analytics role
selection in this app's Developer Central registration. Per §1, a role
added to the app registration *after* a seller's existing consent does
not retroactively attach — that seller's existing `token_reference`
almost certainly does not carry Brand Analytics scope, and a live
`createReport` call against it would be expected to fail with a
permission error (see below) until that seller re-runs the Website
Authorization Workflow after the role is added to the app.

**How does a missing role appear operationally?** SP-API returns an
HTTP 403 (Forbidden) for an operation the current authorization does
not carry the role for. This repository's existing convention (see
`app/amazon/orders_client.py`, confirmed by direct inspection: `if
status in {401, 403}: raise SpApiAuthenticationError(...)`) already
folds both 401 (bad/expired credentials) and 403 (valid credentials,
insufficient role/scope) into the same `SpApiAuthenticationError`. The
new Reports client (§5) follows this exact, already-established
convention rather than inventing a new exception type — a 403 for a
missing Brand Analytics role is indistinguishable, at the exception-
type level, from any other SP-API authentication/authorization
failure this codebase already knows how to terminalize.

**Why must authorization failure terminalize, never retry forever?**
A missing role or invalid/expired grant is not a transient condition —
retrying the identical request with the identical credentials will
fail identically every time, because the seller must take an out-of-
band action (an operator adding the role in Developer Central, and the
seller re-consenting) that no amount of automated retrying can trigger.
Retrying forever would only waste this report type's already-scarce
rate-limit budget (three requests per five minutes, shared with every
other legitimate use of this report type for that seller) and worker
attempts, for a call that can never succeed until a human acts. This
mirrors the exact reasoning this repository's Listings/Orders workers
already apply to their own `SpApiAuthenticationError` handling.

**No live authorization probe was made in this task**, per the
explicit instruction — this section is written entirely from the
pinned contract and this repository's own existing code/config, never
from an actual `createReport` call.

### Field classification

Every field in the pinned schema, classified:

| Category | Fields |
|---|---|
| **Approved business metric** | Every field under `SalesByDate`, `TrafficByDate`, `SalesByAsin`, `TrafficByAsin` (§3 has the full metric dictionary) — all are aggregate, already-anonymized seller-performance metrics, not individual buyer/order records. |
| **Identifier/provenance** | `reportSpecification` (reportType, reportOptions, dataStartTime, dataEndTime, marketplaceIds — request provenance, not seller-secret), `parentAsin`/`childAsin`/`sku` (product identifiers, the same class of "safe to store, never log" identifier this repository already treats `amazon_order_id`/`seller_sku`/`asin` as). |
| **Unnecessary** | None identified — every field in this schema is either an approved metric or provenance/identifier needed to scope one. |
| **Sensitive** | None — this report contains no buyer, order, or payment data of any kind; it is exclusively seller-aggregate performance data. |
| **Prohibited** | `Report.reportDocumentId`/`reportId` **as durably stored identifiers require deliberate justification** (allowed, narrowly, for restart-recovery only — see §6); `ReportDocument.url` (the pre-signed download URL) is **prohibited outright** — never persisted, per the 5-minute-expiry evidence above; raw report JSON bytes are **prohibited outright** — never persisted as a blob; any field not in this pinned schema (a genuinely unknown future field) is **prohibited from silent pass-through** — parsed permissively (tolerate-unknown, per Phase 5) but never written to a durable column that doesn't already exist for a *named*, reviewed field. |

**Persisted, explicitly:** every approved business metric (§3), the
report's own request provenance (organization/connection/seller
account/marketplace participation/window/granularity — §4), and,
narrowly, `reportId`/`reportDocumentId` on the durable run row only
(needed to resume `getReport`/`getReportDocument` after a worker
restart without creating a duplicate report — §6).

**Never persisted:** the pre-signed document URL, any access
token/credential, the raw report document bytes, any unrestricted/
generic JSON blob column, any buyer/order PII (none exists in this
report's contract at all), and any field this pinned schema does not
name.

## 3. Phase 3 — metric semantics

All percentage fields in this schema are supplied by Amazon as
**already-computed values on a 0–100 scale** (confirmed directly from
the pinned schema: every percentage field's JSON Schema constraint is
`"minimum": 0` with, for most, `"maximum": 100` — e.g.
`buyBoxPercentage`, `orderItemSessionPercentage`, `refundRate`,
`receivedNegativeFeedbackRate`, `browserSessionPercentage`,
`pageViewsPercentage`). **This milestone never recomputes any
Amazon-supplied percentage** — every stored percentage column is the
value Amazon returned, verbatim, converted only from JSON `number` to
`Decimal` (never through a Python `float` round-trip) for storage
precision. `NULL` means the field was absent from Amazon's response
(e.g. every `...B2B` field for a non-B2B seller); `0`/`0.00` means
Amazon explicitly reported a zero value. These are never conflated:
absence and true zero are stored as `NULL` and `0` respectively, with
no default-to-zero coercion anywhere in the parser.

**One documented exception to the 0–100 cap:** `unitSessionPercentage`
(both `TrafficByDate.unitSessionPercentage` and
`TrafficByAsin.unitSessionPercentage`) has **`"minimum": 0` only — no
`"maximum"` is declared in the pinned schema.** The schema's own
worked examples confirm this is not an oversight: one ASIN-level
example shows `"unitSessionPercentage": 300.00` (three units purchased
per session is a legitimate, un-capped ratio — e.g. a multi-unit
bundle purchase in a single session). Storage/validation for this one
field must **never** clamp to 100; every other percentage field in this
schema does carry an explicit 0–100 contract bound and storage may
assert that bound as a defensive check constraint.

**Safe precision:** money uses `Numeric(19,4)`, the same precision this
repository already established for Orders (`docs/AI_HANDOVER/
12B4B_ORDERS_SCHEMA.md`'s own reasoning, reused verbatim — see §4).
Percentages use `Numeric(7,4)` (covers 0.0000–100.0000 with headroom,
and covers `unitSessionPercentage`'s uncapped values well past 100 —
`Numeric(7,4)` allows up to 999.9999, which is a safe, generous ceiling
for a per-session-multi-unit ratio without being unbounded). Plain
counts (`unitsOrdered`, `sessions`, `pageViews`, ...) are `Integer`,
matching the schema's own `"type": "integer", "minimum": 0` contract.

**Denominator/interpretation, per metric family:**
- `refundRate` = unitsRefunded ÷ **unitsOrdered** (not the reverse —
  the schema's own description text has the numerator/denominator
  worded ambiguously ["calculated by dividing unitsOrdered by
  unitsRefunded"], which would produce a value that can exceed 100 and
  contradicts the field's own `maximum: 100` constraint; the
  *constraint itself* is treated as authoritative over the prose, since
  a 0–100-bounded rate can only be `refunded ÷ ordered`, never the
  reverse — documented here as a known schema-prose inconsistency,
  resolved in favor of the type constraint, never silently "fixed" by
  recomputing Amazon's own returned value).
- `orderItemSessionPercentage`/`unitSessionPercentage` (date-level):
  conversion rate — order items (or units) generated relative to
  sessions, for the *whole catalog* that day/week/month.
- `buyBoxPercentage`: share of page views where this seller held the
  Buy Box — the direct Buy-Box-exposure signal the milestone's
  objective calls for.
- ASIN-level `sessionPercentage`/`pageViewsPercentage` (and their
  browser/mobile-app splits): this ASIN's share of the *seller's own
  total* sessions/page views across all products for the report
  window — never a share of Amazon's whole-marketplace traffic.
- `unitSessionPercentage` (ASIN-level): this ASIN's own
  units-ordered ÷ this ASIN's own sessions — a per-product conversion
  rate, uncapped (see above).

**Ordered product sales is never labeled revenue, proceeds, or
profit** anywhere in this milestone's schema, client, services, read
APIs, or UI copy — it is stored and surfaced exactly as `ordered
product sales`, mirroring the exact same discipline Order and Sales
Trend Analyst (12B.5A) already applies to `order_total_amount`.

**Why Orders API totals and Business Report totals may differ,
documented explicitly (surfaced in the UI's reconciliation view, §8):**
- **Source/timing:** Orders API reflects individual order records as
  ASI's own Orders ingestion has captured them, current-state, as of
  each order's own `last_seen_at`; the Business Report is a Amazon-
  computed aggregate snapshot for the exact requested window, computed
  server-side at report-generation time.
- **Cancellation/refund treatment:** `orderedProductSales` in the
  Business Report reflects orders *as placed* (gross ordered value for
  the window) with `unitsRefunded`/`refundRate` reported as a
  *separate* metric alongside it — not netted out of
  `orderedProductSales` itself. ASI's own Orders-derived `order_count`/
  `order_value_by_currency` (12B.5A's Order and Sales Trend Analyst)
  reflects Orders API `order_total_amount` as currently stored, which
  already excludes fully-cancelled orders (`was_cancelled=True`
  filtering) — a structurally different treatment of the same
  underlying event.
- **Reporting window boundary semantics:** Amazon's report-window
  boundaries are calendar-day-aligned in the *seller's* reporting
  timezone (not necessarily UTC, and not necessarily the marketplace's
  own local timezone for every marketplace) — a detail this schema
  itself does not specify precisely (no timezone field is present on
  `SalesAndTrafficByDate.date`, which is a bare `format: date` string
  with no offset). ASI's own Orders ingestion stores `amazon_created_at`
  as a UTC timestamp. A day boundary computed in UTC and a day boundary
  computed in the report's own (undocumented, but presumed
  seller/marketplace-local) timezone can disagree on which orders fall
  into "today" near midnight — flagged as an open, honestly-stated
  reconciliation caveat, not resolved by inventing a timezone
  assumption the contract does not state.
- **Shipped vs. ordered:** the Business Report separately reports
  `shippedProductSales`/`unitsShipped`/`ordersShipped` alongside
  `orderedProductSales`/`unitsOrdered` — an order can be *ordered* in
  one window and *shipped* in a later one, so the two metric families
  are expected to disagree by design, not by error.

Given these structural differences, **reconciliation output never
declares "corruption" merely because the two sources differ** — see
§8's reconciliation semantics.

## 4. Phase 4 — schema and migration (implemented)

Migration `0014_sales_traffic_foundation`, revises
`0013_orders_durable_pagination` — single Alembic head, additive only.

**Extends `amazon_ingestion_runs`** (rather than a new job-ledger table),
exactly as 12B.4B did for Orders:

- Widens `ck_amazon_ingestion_runs_run_type` to add
  `'sales_and_traffic_report'`.
- `ck_amazon_ingestion_runs_sales_traffic_scope_required`: a
  `'sales_and_traffic_report'` row is scoped **like Listings**
  (`marketplace_participation_id` and `seller_account_id` both
  `NOT NULL`), never like Orders' coarser multi-participation
  association table — the pinned contract allows exactly one
  `marketplaceId` per report request (§1), so there is no
  multi-participation shape to represent.
- `ck_amazon_ingestion_runs_sales_traffic_fields_scope_required`: the
  seven new report-lifecycle columns must be `NULL` for every other
  `run_type`.
- Three enum `CHECK`s matching the pinned contract's own enums exactly:
  `report_processing_status` (`IN_QUEUE, IN_PROGRESS, DONE, CANCELLED,
  FATAL`), `report_date_granularity` (`DAY, WEEK, MONTH`),
  `report_asin_granularity` (`PARENT, CHILD, SKU`).
- `uq_amazon_ingestion_runs_active_sales_traffic_scope`: single-writer
  partial unique index on `(seller_account_id,
  marketplace_participation_id)`, covering `queued`/`started`/
  `waiting_to_retry` together — the Sales-and-Traffic equivalent of the
  existing Listings index.
- Seven new nullable columns: `report_id`, `report_document_id`,
  `report_processing_status`, `report_data_start_time`,
  `report_data_end_time`, `report_date_granularity`,
  `report_asin_granularity`. `report_id`/`report_document_id` are
  durably stored (narrowly reviewed, §2) so a worker restarted after
  `createReport` succeeded resumes the *same* report rather than
  creating a duplicate against this report type's scarce three-per-
  five-minutes budget.

**New tables:**

1. `amazon_sales_traffic_daily_facts` — catalog-wide, dated
   (`salesAndTrafficByDate`). Natural key `(marketplace_participation_id,
   report_date, date_granularity)`. Money `Numeric(19,4)`; percentages
   `Numeric(7,4)` with an explicit `CHECK (... BETWEEN 0 AND 100)` on
   every percentage **except** `unit_session_percentage`/
   `unit_session_percentage_b2b`, which the pinned contract itself leaves
   unbounded above (§3).
2. `amazon_sales_traffic_product_facts` — product-level, **never-dated**
   (`salesAndTrafficByAsin`), keyed on `(marketplace_participation_id,
   request_window_start, request_window_end, asin_granularity,
   parent_asin, child_asin, seller_sku)`. `child_asin`/`seller_sku` are
   `NOT NULL` with an empty-string default (never nullable) specifically
   so the natural-key `UNIQUE` constraint enforces uniqueness — SQL
   treats every `NULL` as distinct, which would let two idempotent
   retries of the same `PARENT`-granularity row insert as "different"
   rows. A `CHECK` proves the granularity column and identifier columns
   always agree.
3. `amazon_sales_traffic_sync_checkpoints` — one row per participation,
   a raw `synced_through_date` high-water mark plus provenance, for the
   product-level daily-ingestion path only — mirrors
   `amazon_orders_sync_checkpoints` exactly.

`downgrade()` refuses (raises) if any row exists in any of the three new
tables, or any `amazon_ingestion_runs` row has
`run_type='sales_and_traffic_report'`.

ORM models: `AmazonSalesAndTrafficDailyFact`,
`AmazonSalesAndTrafficProductFact`, `AmazonSalesAndTrafficSyncCheckpoint`
(`app/persistence/models.py`). Repositories:
`AmazonSalesTrafficDailyFactRepository`,
`AmazonSalesTrafficProductFactRepository`,
`AmazonSalesTrafficSyncCheckpointRepository`, and Sales-and-Traffic-
specific methods on `AmazonIngestionRunRepository` (`enqueue_sales_
traffic_run`, `claim_next_sales_traffic_job`, `heartbeat_sales_traffic_
run`, `reschedule_sales_traffic_run_for_retry`,
`complete_sales_traffic_run_as_failed`,
`finalize_successful_sales_traffic_run`) (`app/persistence/
repositories.py`).

Verified: `tests/test_amazon_sales_traffic_schema.py` (18 tests: enqueue/
claim/heartbeat/finalize lifecycle, both fact tables' idempotent upsert,
grain-collision avoidance, percentage-bound/unbounded-exception
enforcement, cross-participation isolation), `tests/test_migration_chain_
matches_orm_metadata.py` (33-table drift parity), `tests/test_amazon_
seller_identity_schema.py` (single-head assertion updated to `0014`).
Postgres-guarded: `tests/postgres/test_disposable_postgres_sales_traffic_
migration.py` (8 tests — upgrade preserving data, expected schema,
downgrade-clean, downgrade-refuse ×2, active-scope uniqueness under real
concurrency-relevant constraints, excessive-magnitude rejection,
unbounded-`unit_session_percentage` acceptance; skip locally, exercised
by CI's `postgres-identity-concurrency` job). CI: new
`existing-database-upgrade-0014` job added to `backend-database-ci.yml`;
the fresh-install job's expected-head assertion updated to `0014_sales_
traffic_foundation`.

## 5. Phase 5 — Reports client (implemented)

`app/amazon/reports_client.py` — `AmazonSpApiReportsClient`, scoped to
`GET_SALES_AND_TRAFFIC_REPORT`. Mirrors `orders_client.py`'s constructor,
bounded-retry loop, and httpx-log-redaction shape.

- `create_report`/`get_report`/`get_report_document` against the normal
  SP-API host; a separate `download_report_document` step fetches the
  actual document from the **presigned URL** `getReportDocument`
  returns — a different host, never retried (a presigned URL expires
  after 5 minutes total; retrying would burn most of that window).
- Bounded 429/5xx/transport retry with `Retry-After` honored; 401/403
  fold into the existing `SpApiAuthenticationError` (never retried);
  other non-2xx statuses become `SpApiInvalidRequestError`.
- Download safety: scheme must be `https`, redirects are never followed,
  size is capped at 64 MiB, `GZIP` is the only accepted compression
  algorithm (absence means uncompressed; any other value is a contract
  violation, rejected rather than guessed), malformed JSON and a
  contract-shape mismatch both raise `SpApiParseFailedError`.
- `CreateSalesAndTrafficReportRequest` validates single-marketplace,
  valid granularities, and `start <= end` at construction — before any
  network call, never leaving a wasted rate-limit-budget request to
  Amazon's own rejection.
- Never persists the presigned URL, an access token, or raw document
  bytes anywhere in this module.

Verified: `tests/test_amazon_reports_client.py` (23 tests — every
processing status, retry/backoff/`Retry-After` honoring, auth/parse/
compression/redirect/oversize/malformed-JSON rejection, unknown-future-
field tolerance, URL-never-logged).

## 6. Phase 6 — durable lifecycle and worker (implemented)

`app/amazon/sales_traffic_ingestion.py` —
`AmazonSalesTrafficIngestionService.process_claimed_job`, the single
entry point the worker calls, one claimed run per invocation:

```text
short DB read of the claimed run's frozen request/report state
  -> if no report_id yet: createReport, then a short DB heartbeat durably
     records report_id before anything else — once that heartbeat has
     committed, a restarted attempt never re-creates the report. This
     does not close the crash window itself: the Reports API and
     PostgreSQL cannot share one atomic transaction, so a crash between
     createReport succeeding and this heartbeat committing can still
     leave an orphaned, untracked Amazon report — an accepted
     at-least-once request boundary, never claimed as exactly-once. See
     §11's own explicit statement of this window.
  -> one getReport call (never an in-process poll loop)
  -> non-terminal status: release the claim to waiting_to_retry (a short
     next_retry_at) — never holds a worker slot for the report's
     generation time
  -> CANCELLED/FATAL: complete_sales_traffic_run_as_failed, distinct
     failure_class per cause
  -> DONE: getReportDocument + download_report_document, then one DB
     transaction persists every fact row AND calls
     finalize_successful_sales_traffic_run together (never split across
     two commits)
```

Authorization failures (`SpApiAuthenticationError`) terminalize
immediately at every network call — a missing Brand Analytics role or
invalid/expired grant is never transient (§2). `SpApiInvalidRequestError`
and `SpApiParseFailedError` also terminalize (`invalid_request`/
`malformed_report`). `SpApiRateLimitedError`/`SpApiRequestFailedError`
reschedule (`throttled_or_transient`). A genuine persistence-layer
exception (e.g. a database constraint violation) is **re-raised, never
terminalized** — "programming errors remain visible" — leaving the run's
lease to expire and become reclaimable, exactly like the worker's own
handling of any unexpected exception.

**Checkpoint advancement is scoped to the product-level daily path
only**: `finalize_successful_sales_traffic_run` only receives a
`synced_through_date` when `report_data_start_time ==
report_data_end_time` (a genuine single calendar day) — a wider
catalog-wide-trend request still persists its facts and still succeeds,
but never moves the incremental daily checkpoint.

`app/amazon/sales_traffic_worker.py` — `SalesTrafficWorker`, a dedicated
process (never merged with `orders_worker.py`/`listings_worker.py`),
gated by `ASI_SALES_TRAFFIC_WORKER_ENABLED` (independent of the other two
workers' own gates), with the same claim-one/process-to-terminal-or-
waiting_to_retry loop, poll-error backoff, and SIGINT/SIGTERM cooperative
shutdown shape as `orders_worker.py`. Run as:

```text
cd apps/api && uv run python -m app.amazon.sales_traffic_worker
```

Not started in this session — no `ASI_SALES_TRAFFIC_WORKER_ENABLED` was
ever set, no worker process was ever run, no live Amazon call was made.

Verified: `tests/test_amazon_sales_traffic_ingestion.py` (15 tests —
durable `report_id` recording, skip-`createReport`-on-restart, non-
terminal-status release-not-poll, `CANCELLED`/`FATAL` terminalization,
every authorization/invalid-request/parse/rate-limit outcome, secret-
resolution failure, single-day checkpoint advancement, wider-window
checkpoint non-advancement, and — the one genuine production defect this
pass found and fixed — atomic rollback on a persistence-layer failure).
`tests/test_amazon_sales_traffic_worker.py` (25 tests — claim/process
loop, per-organization concurrency limit, throttled reschedule,
unexpected-exception resilience, graceful shutdown, the enable-gate,
and the `ASI_DB_RUNTIME_CONTEXT` declaration ordering).

**Production defect found and fixed during this recovery pass:**
`_amount_fields` in `sales_traffic_ingestion.py` built every Amount
field's destination column name as `f"{prefix}_amount"` — correct for a
non-B2B field (`ordered_product_sales` -> `ordered_product_sales_
amount`, which matches the real column), but wrong for every `_b2b`
field: the real column is `ordered_product_sales_amount_b2b` (B2B suffix
trailing the *whole* column name), not `ordered_product_sales_b2b_
amount`. Because the helper always emits a key (even `None` for an
absent value), this produced a nonexistent-column `TypeError` on
**every** persisted report, B2B or not — the ingestion service had never
successfully persisted a single report before this fix. Caught by this
pass's own first attempt at `test_done_persists_facts_and_advances_
checkpoint_for_a_single_day_window`; fixed by making `_amount_fields`
take the exact destination column name directly rather than templating
a suffix, and updating every call site. Regression-tested by the three
persistence tests above, which now pass.

## 7a. Phase 7a — worker/CI/production authorization gates (explicit)

No migration was applied to Supabase. No live Amazon call, seller
reconnection, role change, or backfill was performed or authorized by
this pass. `ASI_SALES_TRAFFIC_WORKER_ENABLED` was never set; no worker
process was started. The new `existing-database-upgrade-0014` CI job and
the Postgres-guarded migration test file are both written and reasoned
through, but — like every prior milestone's own Postgres-guarded tests —
have not been executed against a real disposable PostgreSQL instance in
this authoring environment (no Docker/Postgres binary available); they
skip locally and are exercised by CI's `postgres-identity-concurrency`
job on the next push.

## 7. Phase 7 — historical backfill and incremental strategy (designed,
not executed)

**Maximum supported lookback:** 2 years from today, per the pinned
schema's own `dataStartTime` description (§1) — Amazon cancels a report
requesting further back than that, it does not reject the request
up front.

**Two structurally different backfill costs, per the grain conclusion
(§1a):**
- **Catalog-wide trend** (`salesAndTrafficByDate`): cheap. One report
  per marketplace covers an arbitrarily long window (up to the 2-year
  cap) in a single request, with `dateGranularity=DAY` returning one
  dated row per day within it.
- **Product-level daily facts** (`salesAndTrafficByAsin`): expensive.
  Exactly **one report per marketplace per day** is required, because
  the ASIN section carries no date field at all (§1a) — there is no
  request shape that returns multiple days of per-SKU facts from one
  report.

**Proposed initial history window:** **30 days**, product-level daily,
per currently-authorized marketplace participation — long enough to
support a "last 30 days" trend view (matching this project's existing
`DEFAULT_PERIOD_DAYS = 30` convention across every 12B.5A Copilot
skill) without committing to a multi-day backfill operation before this
slice's real-world request-rate behavior has been observed. Catalog-
wide trend history can be backfilled far more generously (e.g. the
full 2-year cap) in a single low-cost report per marketplace, since it
does not share the same per-day request cost.

**Exact request-count calculations for the expensive (product-level,
one-report-per-day) case:**

| Window | 1 marketplace | All 6 current participations |
|---|---|---|
| 30 days | 30 reports | 180 reports |
| 90 days | 90 reports | 540 reports |
| 180 days | 180 reports | 1,080 reports |
| 365 days | 365 reports | 2,190 reports |

**Expected duration**, bound by the report-type-specific throttle of 3
requests per 5 minutes (≈ 1 request per 100 seconds sustained,
submission-cadence only — not counting each report's own Amazon-side
generation time, which runs concurrently with submitting the next
request since `createReport`/`getReport`/`getReportDocument` are
separate rate-limit buckets and this design polls durably rather than
blocking a worker slot per in-flight report, §6):

| Window | 1 marketplace | All 6 participations |
|---|---|---|
| 30 days | ~0.8 hours | ~5.0 hours |
| 90 days | ~2.5 hours | ~15.0 hours |
| 180 days | ~5.0 hours | ~30.0 hours |
| 365 days | ~10.1 hours | ~60.8 hours (~2.5 days) |

These are **floors**, not estimates of real elapsed time — they assume
zero 429s, zero transient failures, and back-to-back submission at
exactly the documented limit, none of which a real run should assume.
A production backfill should budget meaningfully more wall-clock time
than this table shows, and should not be scheduled as a single
uninterrupted operation for the larger windows.

**Not every marketplace will authorize or return data** — the 6
current participations are a *baseline count*, not a guarantee; a
backfill scheduler must treat each participation's own report request
independently (its own attempt/retry/terminal state, §6), never assume
all 6 will succeed, and never block one participation's backfill on
another's failure.

**Rate-limit implications:** the 3-per-5-minute createReport budget is
shared across **every** legitimate use of this report type for the
app+seller — the initial backfill, the ongoing daily incremental job,
and any future ad-hoc customer-triggered "resync this marketplace"
request all draw from the same budget. The worker (§6) must treat this
as one shared, durable rate-limit domain, not a per-job allowance.

**Report deduplication restrictions:** none documented beyond the rate
limit itself (§1) — the application-level durable-run design (§6) is
what actually prevents duplicate requests for the same window, not any
Amazon-side dedup guarantee.

**Retry schedule:** matches the report-type-specific guidance directly
— start at 1 minute, double on each subsequent 429, capped at a
bounded maximum attempt count (mirrors this repository's existing
Listings/Orders worker retry-budget convention, reused rather than
inventing a new one — §6).

**Daily versus larger request windows:** product-level ingestion always
requests exactly one calendar day per report (the only shape that
produces genuinely daily SKU facts, §1a). Catalog-wide trend ingestion
requests a larger rolling window (e.g. the trailing 30/90 days in one
report) periodically, since `salesAndTrafficByDate` scales with
`dateGranularity` cheaply.

**Overlap strategy and incomplete-day handling:** Amazon's own
processing/settlement lag means the most recent 1–2 days of a report
window can under-report true final values if requested too soon after
the day ends (the same class of "recent day not yet fully settled"
concern this repository already reasons about for Orders). This
milestone defines a day as **sufficiently settled** once it is **at
least 2 full calendar days old** in UTC (a conservative, documented
choice — not sourced from the contract, since the contract does not
state a settlement SLA) — the incremental job re-requests the
trailing **3 days** on every run (a **1-day bounded overlap** past the
2-day settlement floor) so a day that settles slightly late is picked
up on the next run without re-requesting the entire history. This
overlap is a deliberate, small, re-request — not a gap in coverage —
and each re-request's persistence is idempotent (§8), so re-fetching an
already-settled day changes nothing.

**Seller timezone and marketplace timezone behavior:** not resolved by
the contract (§3's reconciliation note applies identically here) — this
milestone stores `dataStartTime`/`dataEndTime` exactly as sent (UTC
calendar dates, ASI's own convention) and documents, rather than
silently assumes, that Amazon's own day-boundary semantics for this
report may not align exactly with a UTC calendar day for every
marketplace. Not fixed in this milestone; flagged as a known
limitation (see the doc's own Known Limitations section).

**Incremental cadence avoiding repeated requests for settled history:**
once a day is confirmed settled (≥ 2 days old) and successfully
persisted, the per-participation checkpoint (§4, §6) advances past it —
the daily incremental job only ever requests the trailing 3-day window
described above, never the full history again, exactly mirroring how
Orders' own `synced_through_at` checkpoint already avoids re-requesting
settled Orders history.

**Not built in this pass, honestly deferred:** an automatic scheduler
that reads a participation's checkpoint and computes/submits the next
window on its own. §6/§9's trigger is caller-supplied-window only (a
manual/ad-hoc resync, or a future scheduled job calling the same trigger
service) — the checkpoint exists and is read by `get_freshness`/
`SalesTrafficSyncEvidence.synced_through_date`, but nothing in this
milestone drives it forward automatically. Natural 12B.6B-or-later
follow-up, not invented here without an explicit approved slice.

## 8. Phase 8 — persistence and reconciliation (implemented)

**Idempotency:** both fact repositories (`AmazonSalesTrafficDailyFact
Repository.upsert`/`AmazonSalesTrafficProductFactRepository.upsert`)
look up the existing row by natural key first and replace its fields in
place, never insert-then-fail-then-retry — a duplicate delivery of the
same report (a worker restart re-processing the same `DONE` report, or a
genuine re-request of an already-settled day per §7's overlap strategy)
overwrites the same row with the same or newer values, never duplicates
it. Proven directly: `test_daily_fact_upsert_is_idempotent`, `test_
product_fact_upsert_is_idempotent_for_parent_granularity_no_child_or_
sku`.

**Atomicity:** persistence of every fact row for one report AND the
run's `succeeded` transition AND (when applicable) the checkpoint
advance all happen inside one `session_scope()` transaction
(`AmazonSalesTrafficIngestionService._persist_and_finalize`) — a failure
partway through (proven with a real database-constraint violation, not a
mocked failure) rolls back every row this attempt would have written,
including any daily-fact rows already flushed earlier in the same
transaction, and leaves the run `started` (never `succeeded`) so its
lease simply expires and becomes reclaimable. Proven directly:
`test_persistence_failure_rolls_back_every_row_and_leaves_the_run_
claimable`.

**Malformed reports write nothing:** a report that fails the client's
own contract-shape validation (`SpApiParseFailedError`, e.g. an
unrecognized `compressionAlgorithm`, invalid JSON, or a schema mismatch)
never reaches the ingestion service's persistence step at all — parsing
happens entirely inside `download_report_document`, before any database
write.

**Null vs. zero, percentage scale, and money precision** are preserved
end to end from the pinned contract through to storage — see §2/§3's own
reasoning; the schema's `CHECK` constraints (§4) enforce the 0–100 bound
on every percentage field except the one the contract itself leaves
unbounded, and money columns are `Numeric(19,4)`, validated in Python
(`_validate_sales_traffic_money_amount`) to reject more than 4 fractional
digits before any SQL is issued.

**Marketplaces/currencies never mixed, incompatible grains never
summed:** every fact row is scoped to exactly one
`marketplace_participation_id` (one marketplace, one currency, per §1's
own contract restriction), and the read layer (§9) never sums a
catalog-wide daily total together with a product-level window total, and
recomputes every aggregated percentage from its own summed numerator/
denominator rather than averaging pre-computed percentages (see §9's own
`_weighted_percentage` reasoning) — never a naive `mean()` that would
silently give a low-traffic day the same weight as a high-traffic one.

**Amazon-catalog vs. locally-comparable totals reported separately, not
auto-reconciled:** this milestone does not attempt to reconcile Sales and
Traffic Business Report totals against Orders API totals — see §3's
explicit reasoning for why they are expected to differ structurally
(source/timing, cancellation/refund treatment, reporting-window boundary
semantics, shipped-vs-ordered). No automated "discrepancy" classifier
exists in this pass; the two sources are simply never blended into one
number anywhere in the schema, service, or read layer.

## 9. Phase 9 — read APIs and sync trigger (implemented); UI (see below)

**Read API** — `app/amazon/sales_traffic_read.py`
(`AmazonSalesTrafficReadService`) + `app/api/routes/amazon_sales_
traffic.py`, strictly read-only, organization-scoped via `current_
organization_id()` exactly like `AmazonOrdersReadService`:

- `GET .../sales-traffic/summary?start=&end=` — catalog-wide aggregate
  over an explicit date range: summed money/counts (only when every day
  in range shares one currency), and *recomputed* (never averaged)
  `buy_box_percentage`/`unit_session_percentage`, plus `SalesTrafficSync
  Evidence` (status/failure/timestamps/`synced_through_date`).
- `GET .../sales-traffic/daily-trend?start=&end=` — unaggregated
  per-day points, in `report_date` order, for a trend chart.
- `GET .../sales-traffic/products?start=&end=&q=&sort_by=&sort_dir=&
  offset=&limit=` — product performance, aggregated across every
  product-fact window that falls **entirely inside** `[start, end]`
  (never a partially-overlapping window, which would misattribute a
  wider aggregate to a narrower period — proven by `test_product_
  performance_never_returns_a_window_outside_the_query_range`); carries
  both traffic and conversion fields in one row so the UI's traffic-vs-
  conversion view is a client-side sort/filter of this same response,
  not a second endpoint.
- `GET .../sales-traffic/freshness` — coverage/staleness evidence
  independent of the numeric summary: earliest/latest daily-fact date on
  file plus the same `SalesTrafficSyncEvidence`.

**Sync trigger** — `app/amazon/sales_traffic_sync.py`
(`AmazonSalesTrafficSyncTriggerService`) + `app/api/routes/amazon_sales_
traffic_sync.py`, mirroring `orders_sync.py`'s shape exactly,
simplified for this report type's single-participation scope: `POST
/sales-traffic/sync` (seller account + participation + explicit
requested window/granularities) enqueues or reports the caller's
existing job (`already_running`/`cooldown`/`scope_not_found`/
`scope_inactive`/`connection_unresolvable`/`invalid_request`); `GET
/sales-traffic/sync/{run_id}` reports sanitized progress. Neither route
calls Amazon or resolves a secret — only `sales_traffic_worker.py` does
that, out of band. A new `sales_traffic_sync_trigger_cooldown_seconds`
setting (default 300s) throttles repeated manual triggers, deliberately
conservative given this report type's own three-per-five-minutes
`createReport` budget.

Verified: `tests/test_amazon_sales_traffic_read_service.py` (11 tests —
ownership scoping on all four read methods, never-synchronized baseline,
weighted-vs-naive-mean percentage aggregation proven with an explicit
adversarial fixture, unaggregated daily-trend ordering, product-window
containment, freshness bounds/checkpoint). `tests/test_amazon_sales_
traffic_sync_trigger.py` (11 tests — scope/ownership validation, request
validation, first-trigger/duplicate/cooldown, `get_status` foreign-run
and wrong-run-type rejection). Both new routers verified registered via
`TestClient(app).get("/openapi.json")` (6 new paths under `/api/v1/
amazon/...sales-traffic...`) and the full backend suite green alongside
them (1526 passed, 70 appropriately-skipped Postgres-guarded tests).

**UI** — `/seller/sales-traffic`, a fourth `SellerLocalNav` tab
(`src/components/seller-local-nav.tsx`), sibling to Listings/Orders —
never a new global `AppShell` top-level tab, per this milestone's own
requirement. `src/components/seller-sales-traffic.tsx` mirrors
`seller-orders.tsx`'s conventions exactly (Tailwind-only styling, plain
`useState`/`useEffect` data fetching driven by URL query params, adaptive
-backoff polling rediscovered purely from server-returned sync evidence,
never a client-stored run id).

One deliberate deviation from Orders' own spinner rule, made because of
this repository's own known limitation that no worker is deployed
anywhere in production yet: `salesTrafficSyncShowsActiveSpinner`
(`seller-sales-traffic-view.ts`) shows the spinner only for `running`,
never for `queued` — a queued job with no deployed worker could sit
unclaimed indefinitely, and Orders' "spin for queued too" convention
would then be a literal, indefinite false-progress signal rather than a
momentary one. A queued job instead shows a static "Waiting for a worker
to pick this up" line.

A missing Brand Analytics role has no dedicated tracked signal anywhere
in this system (§2's own honestly-stated gap) — the UI's only way to
surface it is `failure_class === "authentication_failed"`
(`salesTrafficIsLikelyMissingRoleFailure`), mapped to an explicit
"may be missing the Brand Analytics permission" message rather than a
generic failure string. Traffic-vs-conversion is a client-side badge
(`isHighTrafficLowConversion`, documented conservative thresholds) over
the same product-performance response — never a second endpoint.

Verified: TypeScript (`npx tsc --noEmit`) clean; production build
(`npm run build`) succeeds and lists `/seller/sales-traffic` as its own
static route; full frontend suite green (162 passed, up from 161 — the
one new/updated `seller-local-nav.test.tsx` assertion); `next dev` was
started locally and the route returned `200` with the expected
"Sales & Traffic" nav label and loading-state markup server-rendered
(client-side data fetching against a live backend/database was not
exercised — no backend server or seeded dev database was started for
this check, consistent with this pass's no-live-data authorization
boundary).

**Deliberately out of scope for the UI in this pass:** no per-product
detail drawer (Orders' own drawer pattern was not replicated — the
product table's existing row already carries every field a drawer would
add); no rendered chart library for the daily trend (a plain table
satisfies "daily trend" functionally without taking on a new dependency
in an already-large milestone). Both are straightforward follow-ups, not
missing plumbing.

## 10. Copilot evidence/cache boundary (mechanism-ready only)

No new Copilot skill is built in this milestone (explicitly out of
scope). What's wired instead, following the exact existing Orders/
Listings pattern one-for-one (`app/copilot/skills/contracts.py` has no
central "evidence source" registry — each domain is one mirrored
function plus one optional `SkillEvidence` field, added by hand):

- `sales_traffic_evidence_version(sync: SalesTrafficSyncEvidence | None)
  -> str` — unlike the Orders/Listings versions (a single
  `last_successful_synchronized_at` timestamp), this one is a composite
  `"{last_successful_synchronized_at}|{synced_through_date}"` string,
  because Sales and Traffic genuinely has two independent freshness
  signals (any successful report run vs. the product-level *daily*
  checkpoint) and either one advancing must invalidate a cached
  evidence entry.
- `SkillEvidence.sales_traffic_freshness: SalesTrafficSyncEvidence | None
  = None` — a new optional field, unused by any launch skill today.
- `evidence_cache_key(...)` and `app/copilot/tools/skills.py`'s
  `_evidence_versions`/`_cached_evidence` gained a third,
  default-`None`/default-`False` parameter each
  (`sales_traffic_evidence_version`, `needs_sales_traffic`) — every
  existing Listings/Orders call site is provably unaffected (`test_
  evidence_cache_key_defaults_sales_traffic_version_to_none_unaffecting_
  existing_callers`).

Never sends whole report documents or tables to the LLM — the version
itself is always a compact string (never a hash of content, a row, or a
count), matching the same restraint the existing pattern already
applies; the Sales and Traffic read layer (§9) independently reinforces
this by only ever exposing pre-aggregated summary/trend/product rows,
never a raw fact-table dump.

Verified: `tests/test_copilot_sales_traffic_evidence_version.py` (6
tests — `None`/never-synchronized sentinel, each freshness signal
changing its own half of the composite string independently, stability
for identical evidence), plus 2 new tests in `tests/test_copilot_skill_
cache.py` proving the new cache-key parameter is backward-compatible and
sensitive to change.

## 11. Final pre-commit safety review (this pass)

A dedicated review pass, before any commit, specifically targeting
external-report failure boundaries the earlier implementation report had
not yet proven. Six genuine defects were found and fixed; every fix has
a regression test. No commit, push, migration apply, live Amazon call,
role probe, reconnection, worker start, or backfill was performed.

**Corrections made:**

1. **Persistence bug (found in the previous pass, restated for
   completeness):** `_amount_fields` built every B2B money column's name
   wrong — already fixed and tested before this review pass began.
2. **Unbounded decompression (decompression-bomb vulnerability).**
   `download_report_document` called `gzip.decompress(raw)` directly —
   a compressed payload's decompressed size is attacker/corruption-
   controlled, not bounded by its own (already-capped) compressed size.
   Fixed with `_bounded_gzip_decompress` (`reports_client.py`): streaming
   `zlib` decompression in 1 MiB chunks with a hard `MAX_DECOMPRESSED_
   BYTES` (256 MiB) ceiling, raising the moment it's exceeded. A
   truncated mid-transfer stream is also handled explicitly (breaks out
   rather than looping forever; partial bytes fail at the JSON-parse
   step, never accepted as complete). Tested: a 5 MiB highly-compressible
   payload is rejected against a 1 MiB test ceiling; a truncated GZIP
   stream is rejected; the pre-existing gzip round-trip test still
   passes.
3. **Fragile `DONE`/`reportDocumentId` handling.** The ingestion service
   relied on a bare `assert status.report_document_id is not None` — an
   assert strips under `-O`, and is the wrong layer for a data-
   validation invariant anyway. Fixed by validating it in `get_report()`
   itself: a `DONE` response missing `reportDocumentId` now raises
   `SpApiParseFailedError` at the client boundary, before the ingestion
   service ever sees it. The ingestion service's `assert` is now a
   genuinely-guaranteed type-narrowing statement, not a validation
   boundary — documented as such.
4. **Unbounded retry/elapsed-time budget.** `AmazonSalesTrafficIngestion
   Service` accepted `max_retry_attempts` as a constructor parameter but
   never checked it anywhere — a run could reschedule (`waiting_to_
   retry`) forever. Fixed: `process_claimed_job` now checks both
   `retry_count >= max_retry_attempts` (6) and elapsed wall-clock time
   since the run's own `created_at` (`>= max_total_retry_seconds`, 6
   hours) *before* any network call, terminalizing as `retry_budget_
   exhausted` if either is exceeded. Tested: budget-exhausted
   terminalizes without any client call; an in-budget run is unaffected
   (no off-by-one).
5. **No lease renewal around the download step.** The one potentially
   long-running network operation (`getReportDocument` + `download_
   report_document`, up to 64 MiB download + 256 MiB decompression) had
   no heartbeat to guard the lease. Fixed: a heartbeat renewal is issued
   immediately before the download starts. (Bounded risk even before
   this fix — `DOWNLOAD_TIMEOUT_SECONDS=60` sits comfortably under the
   `300`s default lease duration — but this closes the gap rather than
   relying on that margin implicitly.)
6. **Product-window double-counting (the most significant finding).**
   `list_product_performance` summed *every* product-fact row whose
   window fell inside the query range, with no check for whether two
   windows for the same product actually overlapped each other. Nothing
   in this system prevents an operator from independently triggering
   both 30 daily one-day windows *and* a single 30-day wide window at
   SKU granularity for the same period — before this fix, both would
   have been summed together, silently double-counting every overlapping
   day. Fixed with `_select_non_overlapping_windows`: sorts windows
   shortest-first and greedily keeps only those that don't overlap an
   already-kept (necessarily finer-or-equal) window — the wider window is
   dropped entirely rather than partially blended (blending a non-
   divisible aggregate across a sub-range isn't representable in this
   contract). Proven with an adversarial fixture (30 daily windows +
   one fully-overlapping 30-day window → only the 30 daily windows
   count) and a normal-case fixture (two genuinely non-overlapping
   windows still sum together correctly).
7. **Exception-chain URL leak (hardening, not an active leak today).**
   `download_report_document`'s transport-failure branch chained `from
   exc`, where `exc` (`httpx.HTTPError`) embeds the request URL — for
   this one call, the presigned document URL — in its own string
   representation. No code path today actually logs that chain, but
   `from exc` left it *reachable* by any future `logger.exception()`
   call anywhere upstream. Changed to `from None` (matching the sibling
   timeout branch, which already used it), structurally removing that
   possibility rather than relying on no caller ever adding one. Proven
   directly: a simulated transport failure's fully-formatted traceback
   (`traceback.format_exception`) contains neither the URL path nor its
   query string.

**1. Grain proof.** Traced end to end: `SalesAndTrafficByAsin` has no
`date` field anywhere in the DTOs (`sales_traffic_models.py`); the
ingestion service tags every product fact with the run's own exact
`(data_start_time, data_end_time)`, never a derived/fabricated date
(`_persist_and_finalize`); the product-facts table's natural key
includes that window; the read service's `list_for_window` only returns
rows whose window falls *entirely* inside the query range (never a
partial overlap); and — this pass's own finding — overlapping windows
for the same product are now deduplicated rather than summed (§ fix 6).
The UI labels the relevant section "Product performance" (never "daily
product performance") and separately discloses `window_count`. Catalog-
wide (`amazon_sales_traffic_daily_facts`) and product-window
(`amazon_sales_traffic_product_facts`) data live in physically separate
tables with separate read-service methods and separate response models
end to end — never blended in one query or one response shape.
Marketplace/currency boundaries: every fact row is scoped to exactly one
`marketplace_participation_id` (one marketplace, one currency per the
pinned contract's own one-marketplace-per-request restriction, §1); the
read service never combines rows across participations.

**2. Percentage aggregation decisions.** See `_weighted_percentage`'s
own docstring (`sales_traffic_read.py`), now containing a full worked
proof that `buy_box_percentage`'s weighting basis (`page_views`) is
exactly the field's own documented denominator — not merely "a"
plausible weight. `unit_session_percentage` is recomputed from summed
raw numerator/denominator (`units_ordered`/`sessions`) directly, never
from a supplied-percentage weighted average — a stronger guarantee than
weighting, since it never compounds Amazon's own per-day rounding.
Fields this module does not expose on any response model (browser/
mobile session-percentage splits, page-view percentages, every B2B
percentage) are never aggregated at all — silence, not a fabricated
weighting, for a field whose compatible denominator isn't currently
surfaced. Tested: zero denominator, null denominator, mixed null/zero
across rows, unequal-denominator weighting (hand-computed expected
value), fractional-precision preservation, and an end-to-end proof that
the weighted summary figure can legitimately differ from — and does
differ from — a naive per-day average, while the daily-trend endpoint
separately proves it preserves Amazon's own supplied per-day value
verbatim.

**3. External `createReport` crash-window behavior — stated honestly.**
The Reports API and PostgreSQL cannot share one atomic transaction.
**Exactly-once Amazon report creation is not, and cannot be, guaranteed
by this design.** If the process crashes after `createReport` succeeds
but before the heartbeat commits `report_id` locally, that Amazon report
becomes a durably-orphaned object: never polled, never downloaded,
consuming one of the report type's three-per-five-minutes budget, and
the local run eventually times out (an unclaimed `started` row's lease
expires into terminal `timed_out` — this reclaim-to-terminal shape is
identical to every other run type in this codebase, not unique to Sales
and Traffic, and is not changed by this pass). No idempotency key is
invented for this — the Reports API contract does not support one, and
Amazon's own `getReports` listing (not used by this design, §5's own
reasoning) is the only documented reconciliation surface, and using it
opportunistically here was judged out of scope for this review pass.
What *is* proven: once `report_id` is durably committed, a restart never
calls `createReport` again (`test_process_claimed_job_skips_create_
report_when_report_id_already_recorded`); duplicate delivery of an
already-`DONE` report cannot duplicate facts (idempotent upsert, proven
directly); only the lease-holding run can finalize (`test_finalize_
rejects_the_wrong_lease_owner_and_changes_nothing`, plus an ingestion-
service-level race reproduction, `test_a_stale_worker_that_lost_its_
lease_cannot_finalize_or_persist`); and the retry/elapsed-time budget
(§ fix 4) now bounds how long a run may sit unresolved before
terminalizing truthfully, rather than retrying forever.

**4. Waiting/capacity behavior.** `IN_QUEUE`/`IN_PROGRESS` release the
claim to `waiting_to_retry` with a `next_retry_at` (never a busy-poll
loop) — proven by `test_non_terminal_status_reschedules_and_releases_
the_claim`. `claim_next_sales_traffic_job`'s own candidate predicate
(`next_retry_at <= now()`) is what actually prevents early reclaim —
proven directly by the newly-added `test_waiting_to_retry_job_is_not_
reclaimed_before_its_next_retry_at`. Retry/elapsed-time budgets: § fix
4. Lease guard during download: § fix 5. `DONE`/`CANCELLED`/`FATAL`/
unknown-status/missing-document-id/403/429/5xx/malformed-report are each
individually tested (client and ingestion-service layers). **`DONE_NO_
DATA` does not exist anywhere in the pinned Reports API v2021-06-30
`Report.processingStatus` enum** — confirmed directly against the pinned
model; a `DONE` response with empty `salesAndTrafficByDate`/
`salesAndTrafficByAsin` arrays is simply `DONE` with zero rows, handled
by § fix/proof below (zero-row `DONE`), not a distinct status this
system needs to represent.

**5. Document-download security.** Presigned URL: never persisted, never
logged (centralized `httpx` log redaction plus a dedicated test
asserting it never appears in `caplog`). HTTPS-only, enforced before any
request. Redirects: never followed (`follow_redirects=False`); any 3xx
is a hard failure, never a followed hop — deliberately no hardcoded host
allowlist, since the pinned contract documents no fixed hostname set for
presigned URLs (overfitting to one observed hostname would be a false
guarantee, not a real one). Credentials: the download call is a bare,
unauthenticated `httpx.AsyncClient` request — no `x-amz-access-token` or
any other header from the SP-API calls is ever attached. Timeouts:
`DOWNLOAD_TIMEOUT_SECONDS=60` (httpx's single-float form covers connect/
read/write/pool). Compressed size: `MAX_DOCUMENT_BYTES=64 MiB`, checked
incrementally during streaming. Decompressed size: `MAX_DECOMPRESSED_
BYTES=256 MiB`, § fix 2. Unsupported/absent compression: an explicit
allowlist (`GZIP` only); anything else fails at `getReportDocument`
already. Checksum/content validation: **not available** — the pinned
`ReportDocument` schema exposes no checksum field for this report type;
stated honestly rather than fabricated. Malformed JSON/encoding: writes
nothing (parsing happens entirely before any persistence call).
Temporary content: none written to disk anywhere in this module (the
downloaded bytes live only in an in-memory list for the duration of one
call) — nothing to clean up. Exception URL leakage: § fix 7, proven
directly.

**6. Persistence/checkpoint atomicity.** Every fact row for one report
plus the run's `succeeded` transition plus (when applicable) the
checkpoint advance happen inside one `session_scope()` transaction — a
genuine database-constraint failure partway through rolls back
everything already written in that same transaction (proven with a real
constraint violation, not a mock). Retrying an already-committed
document is idempotent (natural-key upsert, proven for both fact
tables). Checkpoint and finalization are the same atomic call
(`finalize_successful_sales_traffic_run`), never two separate
commits. Stale-lease rejection: § crash-window section above and the two
new tests. Cross-participation/cross-organization FK enforcement:
proven at the application layer (existing `test_facts_never_leak_
across_marketplace_participations`) and now also at the real-PostgreSQL
composite-FK layer (`test_daily_fact_provenance_must_reference_a_run_
scoped_to_its_own_participation_on_real_postgres`, new this pass,
mirrors the Orders migration file's own equivalent proof). **Zero-row
`DONE` reports genuinely advance the checkpoint without inventing any
fact row** — proven directly by the new `test_zero_row_done_report_
advances_checkpoint_without_inventing_facts`: an empty-but-successfully-
processed day persists zero rows, marks the run `succeeded`, and still
advances `synced_through_date`, since re-requesting an already-checked
empty day forever would never converge.

**7. Trigger/UI truthfulness.** Concurrent triggers for the same
participation: enforced by the DB partial unique index, proven
sequentially at the application layer and — new this pass — under
genuine concurrent threads against real PostgreSQL (`test_concurrent_
enqueue_for_the_same_participation_has_exactly_one_winner_on_real_
postgres`, mirroring the Listings/Orders claim-concurrency convention).
Cooldown is anchored to `completed_at` (terminal completion), never
`created_at`/queue time, and is scoped per-participation regardless of
the requested window — bounding a manual trigger to at most one new
report every `sales_traffic_sync_trigger_cooldown_seconds` (300s
default) per participation, well inside the report type's own three-
per-five-minutes budget; combined with the active-run check, a manual
trigger cannot generate a request storm. UI fixes made this pass: (a)
auto-polling now gives up after `MAX_QUEUED_POLL_TICKS` (8) while a job
never advances past `queued`, replacing indefinite silent polling with
an explicit "Refresh status" button — a `queued` job never shows an
active spinner either (pre-existing, this milestone's own deliberate
deviation from Orders); (b) a `succeeded` sync with zero data now shows
an explicit "no sales or traffic recorded for this period" line,
distinguishing it from `never_synchronized`'s own distinct empty state;
(c) every `load*` fetch now captures its own `participationId` and
discards its result if the currently-selected participation has since
changed — a rapid marketplace switch can no longer let a stale response
overwrite the newly-selected marketplace's state. Role-missing vs.
worker-unavailable remain distinguishable (`authentication_failed` ->
an explicit Brand-Analytics-permission message; `queued` -> "waiting for
a worker" — two different, never-conflated strings). No legacy route
exists for this brand-new page (not applicable).

**8. Migration/PostgreSQL readiness.** Unchanged from the previous
report except for the two new guarded tests this pass adds (cross-
participation FK rejection, genuine concurrent-thread enqueue racing) —
10 Postgres-guarded tests total now, still correctly skipping locally
(no disposable Postgres instance in this environment) and not claimed as
passed. Revision-length, single-head, fresh-zero-to-head, exact
`0013->0014`, existing-data preservation, downgrade-safe/downgrade-
refuse, and money/percentage-precision boundaries are all unchanged and
still verified exactly as the previous report described.

**9. Backfill plan completeness.** Unchanged from §7 — the exact
30/90/180/365-day, 1-marketplace/6-marketplace tables and rate-limit/
duration estimates (explicitly marked as floors/assumptions, not
guarantees) were already present and are not revised by this review. No
automatic scheduler was built to satisfy this review (correctly out of
scope); the manual trigger's own cooldown + active-run-uniqueness design
(§7 above) already bounds request volume without one.

**10. Verification — this pass.** Backend: **1555 passed, 72 skipped**
(up from 1526/70 before this review; the delta is entirely this pass's
own new/fixed tests). Frontend: **162 passed**, `tsc --noEmit` clean,
production build succeeds. The full sales-traffic-specific test files
(127 tests) and the full backend suite were each run multiple times
(5x and 2x respectively) with byte-identical pass counts every time — no
flakes observed; the genuinely concurrency-sensitive tests (real-
thread races) live in the Postgres-guarded file and cannot execute
locally at all (skip, not pass) pending a real disposable-Postgres CI
run. The seven unrelated Log Analyzer/ADR paths were re-diffed against
this pass's own start and remain byte-identical and unstaged.

**Remaining gates, restated:** no live Amazon call, Brand Analytics role
probe, seller reconnection, production worker start, backfill execution,
or Supabase migration apply has been performed or is authorized by this
review. `ASI_SALES_TRAFFIC_WORKER_ENABLED` was never set. Nothing is
committed, staged, or pushed.

## 12. Window-coverage redesign and test-inventory reconciliation (this pass)

**Window-coverage policy, replacing §11's drop-entirely approach.**
`_select_product_windows` (`sales_traffic_read.py`) now returns explicit
coverage metadata rather than silently returning partial evidence under
a response shape that looked complete:

1. Deduplicate exact-identical windows; drop any row whose
   `asin_granularity` conflicts with the majority grain (SKU preferred
   over CHILD over PARENT — structurally shouldn't happen given the
   schema's own CHECK constraint, but never trusted blindly).
2. Select the finest-grain mutually non-overlapping union. If its
   covered ranges exactly equal `[(requested_start, requested_end)]`,
   use it — the common case (e.g. daily windows fully covering a 30-day
   query) and finer wins when both finer and a coarser alternative would
   each independently achieve complete coverage.
3. Otherwise, fall back to a single available window whose own span
   exactly equals the full requested range, if one exists — a complete
   coarser answer beats a partial finer one.
4. Otherwise, return the finer selection **explicitly labeled partial**:
   `coverage_complete=False`, `covered_ranges` naming exactly what
   is/isn't covered, and a human-readable `partial_coverage_reason` —
   never silently presented as if it answered the full requested period.

Never proportionally splits a window (no operation divides a row's own
values); never combines incompatible granularities (step 1); never
sums money across disagreeing currencies (fixed in both `get_summary`
and `list_product_performance` — money nulls out together with
`currency_code` when selected rows disagree, mirroring `OrdersSummary`'s
existing "both null together" convention, which this module's own
earlier version had NOT actually applied to the amount field itself —
a second real defect this pass found and fixed). `ProductPerformanceRow`
gained `coverage_complete`, `covered_ranges: list[CoverageRange]`,
`partial_coverage_reason`, `excluded_overlapping_window_count`, and
`excluded_conflicting_granularity_window_count` — surfaced through the
API response model unchanged, and through the UI as a per-row
"Full"/"Partial" coverage badge (hover reveals the exact covered dates
and reason) plus a page-level banner when any visible row is partial.

Verified with all nine scenarios the review named: complete daily vs.
complete broad (finer wins), incomplete daily vs. complete broad
(falls back to the complete broad window), incomplete daily with no
complete alternative (explicit partial), adjacent non-overlapping
windows, nested windows (falls back to the complete outer window),
duplicate identical windows (deduplicated, never double-counted),
conflicting granularities (deterministically excluded), currency
separation (money nulls out, non-monetary fields unaffected), and
order-independence (three different input orderings of the same ten
rows produce byte-identical selection results). 28 tests total in
`test_amazon_sales_traffic_read_service.py` now (up from 21).

**`createReport` guarantee language corrected.** The ingestion module's
own docstring and the handover doc's §6 previously read as if "a crash
immediately after `createReport` succeeds still lets the next attempt
skip re-creating it" — true only once the heartbeat has actually
committed, not for the narrower gap between the network call succeeding
and that commit. Both are now explicit that this is an accepted
at-least-once request boundary, never an exactly-once guarantee, with a
cross-reference to §11's own full statement of the crash window. A
misleadingly-broad test docstring (`test_process_claimed_job_skips_
create_report_when_report_id_already_recorded`) was similarly tightened
to say exactly what it proves (report_id already durably recorded) and
what it does not (the crash gap before that commit).

**Test-count reconciliation.** The prior report's "up from 1526/70"
was a **wrong baseline citation, not a lost or hidden test**. The
actual state at the end of the implementation pass (before the first
safety review began) was **1534 passed, 70 skipped** — 1526 was an
intermediate count from partway through that same earlier pass (after
adding read APIs, before the Copilot evidence-cache wiring's own 8
tests landed), never the correct "before this review" comparison point.
Reconciled exactly, file by file, for every file touched in the
subsequent two review passes:

| File | Before final gate | Added (safety review) | Added (this gate) | Now |
|---|---|---|---|---|
| `test_amazon_reports_client.py` | 26 | +4 | +0 | 30 |
| `test_amazon_sales_traffic_schema.py` | 18 | +2 | +0 | 20 |
| `test_amazon_sales_traffic_ingestion.py` | 15 | +5 | +0 | 20 |
| `test_amazon_sales_traffic_worker.py` | 25 | +0 | +0 | 25 |
| `test_amazon_sales_traffic_read_service.py` | 11 | +10 | +7 | 28 |
| `test_amazon_sales_traffic_sync_trigger.py` | 11 | +0 | +0 | 11 |
| `test_copilot_sales_traffic_evidence_version.py` | 6 | +0 | +0 | 6 |
| `test_copilot_skill_cache.py` (existing file) | +2 (this pass's own additions) | +0 | — | — |
| `tests/postgres/..._sales_traffic_migration.py` (skipped) | 8 | +2 | +0 | 10 |

1534 (end of implementation) + 21 passing + 2 skipped (safety review) =
**1555 passed, 72 skipped** (confirmed by that pass's own fresh full-
suite run). 1555 + 7 passing (this gate's window-coverage tests, all in
`test_amazon_sales_traffic_read_service.py`) = **1562 passed, 72
skipped** (confirmed by this pass's own fresh full-suite run — skipped
count unchanged, since this gate added no new Postgres-guarded tests).
Every number above was re-derived from a fresh `pytest --collect-only`
count per file, not from memory. Confirmed: no `pytest.mark.skip`, no
`xfail`, no `.only`, and no ad-hoc `pytestmark` anywhere in any Sales
and Traffic test file except the one standard disposable-Postgres
skip-guard every such file in this repository already carries.

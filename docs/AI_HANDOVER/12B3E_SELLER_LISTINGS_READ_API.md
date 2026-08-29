# 12B.3E — Seller Listings Read API

Read-only HTTP API over `amazon_seller_listings`, the table 12B.3D's
ingestion service populates. This milestone adds no ingestion, no
migration, no UI. It exists so 12B.3F can build the visible Listings page
against a stable, authorized contract instead of the ORM directly.

## Endpoints

All under `/api/v1/amazon`, tag `amazon-listings`:

| Method | Path | Purpose |
|---|---|---|
| GET | `/marketplace-participations/{marketplace_participation_id}/listings/summary` | Aggregate counts + synchronization evidence for one participation |
| GET | `/marketplace-participations/{marketplace_participation_id}/listings` | Paginated, searchable, filterable, sorted listing collection |
| GET | `/marketplace-participations/{marketplace_participation_id}/listings/{listing_id}` | One listing's approved detail fields |

Route registration order matters: `/listings/summary` is registered before
`/listings/{listing_id}` in `app/api/routes/amazon_listings.py` so the
literal `summary` segment is never captured by the `listing_id` path
parameter.

## Authorization path

No route accepts `organization_id` from the request. `AmazonListingsReadService`
derives it internally via `current_organization_id()` — the same trusted-context
call every other Amazon service in this codebase uses (`AmazonConnectionService`,
`AmazonListingsIngestionService`). There is no real multi-tenant auth
middleware yet; this is the existing single-tenant convention, not something
new introduced here.

Ownership chain enforced on every call, at the repository layer
(`AmazonSellerListingRepository` in `app/persistence/repositories.py`):

```
organization -> AmazonMarketplaceParticipationRepository.get_by_id(organization_id, participation_id)
             -> (participation found and owned) -> listing query scoped to that participation_id
```

Three new read methods do this, each independently re-validating ownership
(no method trusts a caller who already "checked" a participation earlier —
every call re-validates for itself):

- `get_summary_counts(organization_id, marketplace_participation_id)` — `None` if not owned.
- `list_page(organization_id, marketplace_participation_id, ...)` — `None` if not owned.
- `get_detail(organization_id, marketplace_participation_id, listing_id)` — `None` if not owned, **or** if the listing exists but belongs to a *different* participation than the one supplied (checked via `AmazonSellerListing.marketplace_participation_id == marketplace_participation_id`, not just listing id).

These are read-only counterparts to 12B.3D's write boundary
(`reconcile_snapshot`/`_require_participation_in_organization`), deliberately
built differently: the write boundary raises `TypeError` (a caller-bug
signal, since ingestion scope always comes from already-validated internal
state). A read miss is an ordinary, expected outcome for an HTTP request, so
these methods return `None` instead, and the service layer raises
`AmazonListingsParticipationNotFoundError` / `AmazonSellerListingNotFoundError`
(`app/core/exceptions.py`), which the router maps to a sanitized 404 —
identical whether the participation/listing is missing, malformed-but-valid,
or belongs to another organization. Neither exception message contains
anything beyond the identifier the caller already supplied.

No architectural one-seller-per-organization assumption was introduced:
every lookup is scoped by `marketplace_participation_id`, never by
"the organization's seller account" as if there were only one. Multiple
seller accounts and participations per organization work correctly and are
covered by tests. The connection table (`amazon_connections`) is never
consulted for listing ownership — only the
organization -> participation -> listing chain is authoritative, matching
`amazon_seller_listings`'s existing no-`organization_id`-column design from
12B.3B.

## Summary contract

`ListingsSummary` (`app/amazon/listings_read.py`): total/active/inactive,
buyable/not-buyable, discoverable/not-discoverable, with/without issues,
ERROR/WARNING/INFO severity counts, with-ASIN, with-consumer-price,
with-fulfillment-availability, and a nested `sync: ListingsSyncEvidence`.

All counts are computed by one aggregate SQL query using
`func.count().filter(...)` (a single `SELECT` with multiple `FILTER (WHERE
...)` clauses — supported by both SQLite 3.25+ and PostgreSQL, verified
empirically against this project's SQLite), except
`with_fulfillment_availability_count`: SQLite's `JSON` and PostgreSQL's
`JSONB` (this project's `JsonPayload` variant type) have no common portable
SQL expression for "JSON array is non-empty," so that one count is computed
by pulling just the `fulfillment_availability` column for the participation
and counting non-empty arrays in Python. This is a deliberate, documented
tradeoff for the expected scale (hundreds to low thousands of SKUs per
seller, per the 12B.3B schema docstring) — see Known Limitations.

`active`, `buyable`, and `discoverable` are never conflated: each has its
own independent count, and a listing can be any combination of the three
(a listing can be active-but-not-buyable, buyable-but-not-discoverable,
etc.) — this is tested explicitly.

## Collection contract

`ListingCollectionResponse { items, total, offset, limit }` — same shape
as the existing `SavedAnalysisListResponse` convention
(`app/models/saved_analysis.py`).

- **Pagination**: `offset`/`limit` query params, `limit` clamped to
  `[1, 100]` (`MAX_PAGE_SIZE = 100` in `listings_read.py`), default 25.
  Values outside range are rejected by FastAPI's own `Query(ge=..., le=...)`
  validation (this app's existing `RequestValidationError` handler in
  `app/main.py` returns **400**, not FastAPI's raw 422 default — tests
  assert 400 to match this project's actual behavior).
- **Search**: `q` matches seller SKU or ASIN via case-insensitive, **literal**
  substring search — `%`, `_`, and the escape character itself are escaped
  (`_escape_like_term` in `repositories.py`) before being wrapped in `%...%`
  and passed to `.ilike(term, escape="\\")`. A search for `"10%OFF"` or
  `"SKU_100"` matches only that literal text; it can never accidentally
  widen into a wildcard match. A whitespace-only search is treated as no
  search at all. The search term is never logged (there is no logging
  statement anywhere in `listings_read.py` or `amazon_listings.py`).
- **Filters**: `is_active`, `is_buyable`, `is_discoverable`, `has_issues`,
  `highest_issue_severity` (`Literal["ERROR","WARNING","INFO"]`),
  `product_type` (exact match). Any combination composes as `AND`.
- **Sort**: `sort_by` is a `Literal` allowlist (`last_seen_at`,
  `first_seen_at`, `seller_sku`, `asin`, `issue_count`, `price_amount`),
  `sort_dir` is `Literal["asc","desc"]`. An unsupported value never reaches
  the repository — FastAPI rejects it (400) before the request is routed.
  The repository's `_SORT_COLUMNS` allowlist and explicit `ValueError` on an
  unrecognized value is defense in depth, not the primary check.
  Every query orders by the requested column with an explicit
  `NULLS LAST` (via SQLAlchemy's `.nulls_last()`, verified to compile to the
  identical `NULLS LAST` clause on both SQLite and PostgreSQL) regardless of
  direction, then appends `AmazonSellerListing.id ASC` as a final,
  always-present tie-breaker. Both are necessary for the two nullable sort
  fields (`asin`, `price_amount`): SQLite always places NULLs first by
  default while PostgreSQL's default flips between ASC and DESC, so leaving
  NULL placement to either dialect's default would have made local test
  ordering diverge from production; the `id` tie-breaker separately handles
  every row sharing an identical sort value (e.g. everything touched by one
  ingestion run shares one `last_seen_at`, and every NULL is "equal" to
  every other NULL for ordering purposes).
- No user input is ever interpolated into raw SQL; every filter/search/sort
  value is a parameterized SQLAlchemy expression, and the search escape
  transformation only ever produces additional literal characters, never
  SQL syntax.

`ListingCollectionItem` fields: `id`, `seller_sku`, `asin`, `product_type`,
`is_active`, `is_buyable`, `is_discoverable`, `price_amount`,
`price_currency`, `issue_count`, `highest_issue_severity`, `first_seen_at`,
`last_seen_at`, `last_successful_sync_at`. No organization/seller-account/
connection id, no secret reference, no lease owner, no page token, no raw
`offers`/`issues` JSON (that level of detail is reserved for the detail
endpoint).

## Detail contract

`ListingDetail`: `id`, `seller_sku`, `asin`, `item_name`, `product_type`,
`is_active`, `is_buyable`, `is_discoverable`, `price_amount`,
`price_currency`, `status`, `offers`, `fulfillment_availability`, `issues`,
`product_types`, `issue_count`, `highest_issue_severity`, `first_seen_at`,
`last_seen_at`, `last_successful_sync_at`.

`offers`/`issues`/`fulfillment_availability`/`product_types`/`status` are
returned as-is from the stored JSONB — these are already the approved,
normalized shapes 12B.3D's `listings_normalization.py` produces (only
`summaries`/`issues`/`offers`/`fulfillmentAvailability`/`productTypes` are
ever parsed from Amazon at ingestion time; `attributes`, `relationships`,
and `procurement` are never modeled anywhere upstream, so there is
structurally nothing here that could leak them).

Every response DTO uses `ConfigDict(extra="forbid")` and is built field-by-
field from the ORM row in `_collection_item()`/`_detail()` — no
`from_orm`/`model_validate(row)` shortcut that could accidentally pass
through an unlisted column. Routes additionally call the existing
`public_model_dump()` helper (`app/amazon/common.py`) before returning,
which recursively rejects any credential-shaped key as defense in depth.

## Synchronization evidence

`ListingsSyncEvidence.status` is one of `never_synchronized`, `running`,
`succeeded`, `failed`, `partial`, `timed_out` — `never_synchronized` is
synthetic (no DB status value), returned when no `run_type='listings'` row
exists at all for the participation; the other five map 1:1 from
`AmazonIngestionRun.status` (`started` -> `running`).

Built from exactly two repository queries
(`AmazonIngestionRunRepository.get_latest_listings_run` /
`get_latest_successful_listings_run`), both filtered on `organization_id`
(a real column on `amazon_ingestion_runs`, unlike listings) **and**
`run_type == 'listings'` **and** the selected `marketplace_participation_id`
— a `run_type='marketplace_participations'` row (12B.1D's seller-validation
handshake) can never be mistaken for Listings synchronization evidence,
and a successful *connection* validation is never treated as proof that
Listings synchronization succeeded (tested explicitly).

`status`/`completed_at`/etc. describe the **latest attempt**, which may have
failed; `last_successful_synchronized_at` is independently sourced from the
latest **succeeded** run, which may be an earlier attempt. These
deliberately do not always agree — a listing set can be visibly stale
(`last_successful_synchronized_at` from an hour ago) even while the summary
correctly reports the latest attempt as `failed`.

## Database behavior

Strictly read-only. No migration (Alembic head unchanged at
`0010_amazon_seller_listings`), no Amazon call, no secret resolution, no
ingestion trigger, no manual write, no refresh-on-read, no deactivation or
reconciliation performed by anything in this milestone.

## Fields intentionally withheld

`organization_id`, `seller_account_id`, `connection_id`,
`marketplace_participation_id` (except echoed back in the summary response,
since the caller already supplied it in the URL), `token_reference`,
`last_ingestion_run_id`, `lease_owner`, `lease_expires_at`, any page/next
token, `attributes`, `relationships`, `procurement`, raw Amazon response
bodies, `condition_type`, `main_image_url`, `amazon_created_at`,
`amazon_last_updated_at`, `created_at`/`updated_at` audit columns. The last
four were left out of the detail contract as reasonable but non-required
extensions beyond the milestone's explicit field list — easy to add later
without a breaking change if 12B.3F's diagnostic UI needs them.

## Known limitations

1. `with_fulfillment_availability_count` is computed via a Python scan of
   one JSON column per participation, not a single SQL aggregate —
   deliberately, since PostgreSQL's `jsonb_array_length` and SQLite's
   `json_array_length` are not the same callable and there is no
   dialect-agnostic SQLAlchemy expression for this project's
   `JSON().with_variant(JSONB(), "postgresql")` column type; introducing
   dialect-specific raw SQL was judged more fragile than the scan. 12B.3D's
   1,000-item ingestion ceiling caps the size of any *one* successful
   snapshot, but does not by itself cap this table's cumulative row count
   over a seller's full sync history (deactivated rows are kept, not
   deleted). The scan's practical justification is the realistic
   seller-catalog scale already assumed by `AmazonSellerListing`'s own
   schema docstring (12B.3B) — "hundreds to low thousands" of distinct
   SKUs — not a mathematical guarantee from the ingestion ceiling. Revisit
   (e.g. a materialized boolean column, a schema change out of this
   milestone's scope) if a participation's row count is ever observed
   growing far beyond that.
2. No dedicated index on `amazon_ingestion_runs(marketplace_participation_id,
   run_type, status, started_at)` — the two new sync-evidence queries do a
   filtered scan of a table that is low-volume (one row per ingestion
   attempt, not per listing), so this is not expected to matter at current
   scale, but would be the first thing to add if ingestion-run volume grows.
3. No real multi-tenant authentication exists yet in this codebase —
   "organization from trusted context" currently means
   `settings.default_organization_id`, exactly like every other Amazon
   service. This milestone does not change or weaken that; it is an
   existing, pre-dating condition.
4. `search` (`q`) does a substring `ILIKE` scan across SKU/ASIN with no
   dedicated index — acceptable at documented per-seller scale, would need
   revisiting for a much larger catalog.
5. `nulls_last()` and `.ilike(term, escape="\\")` were verified by direct
   SQLAlchemy dialect compilation (identical `NULLS LAST` / `ILIKE ...
   ESCAPE '\'` SQL text for both `sqlite` and `postgresql` dialects) and by
   live execution against SQLite, but not by live execution against a real
   PostgreSQL server — no disposable PostgreSQL instance is available in
   this development environment, and this milestone adds no schema change,
   so no new PostgreSQL-specific CI job exists to prove it there either.
   Both are standard, long-established PostgreSQL SQL features, so the risk
   is low, but this is not yet runtime-proven on PostgreSQL.

## 12B.3F expectations

The frontend Listings page can be built directly against these three
endpoints: summary for the header/stat cards, collection for the paginated
table (respecting the sort/filter allowlists above), detail for a row's
expanded/diagnostic view. `ListingsSyncEvidence.status` is the field to
drive "last synced" / "never synced" / "sync failed" UI states — do not
infer freshness from connection status. No sync-trigger button or endpoint
exists yet; adding one is out of scope until an explicitly approved
ingestion-scheduling milestone.

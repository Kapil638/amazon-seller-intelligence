# 12B.3F — Seller Listings UI

Frontend surface over the 12B.3E Seller Listings Read API, plus (post
real-browser review) one deliberate, explicitly-scoped write action: a
"Sync listings" button that triggers the existing
`AmazonListingsIngestionService.sync()` for the selected marketplace. This
milestone still adds no migration, no direct browser-to-Amazon call, and
no new ingestion *logic* — the sync button is a thin, ownership-checked
trigger around 12B.3D's already-existing, already-tested service. It
exists so a human can actually see the seller's own catalog and
Amazon-reported listing health that 12B.3D/12B.3E made queryable, and
initiate a fresh sync without leaving the page.

## Post-review additions (this pass)

Three changes made after a completed real-browser desktop + 390px review:

1. **Human-readable product types** — `formatProductType()`
   (`lib/seller-listings-view.ts`) generically converts Amazon's
   `SCREAMING_SNAKE_CASE` product type strings to title case for display
   only (`BLOOD_OXYGEN_MONITOR` → "Blood Oxygen Monitor"). No lookup
   table — any current or future Amazon product type renders readably.
   Applied in the table, the detail drawer's "Product type" field, and its
   "Product types" collection list. Stored values, the read API's query
   parameters, and the product-type filter's exact-match behavior are
   completely unchanged — only display.
2. **Mobile nav active-link visibility** — `AppShell` (`app-shell.tsx`) is
   now a client component that `scrollIntoView({ block: "nearest", inline:
   "nearest" })`s the active `<Link>` on mount and on every `current`
   change, scrolling only the nav's own `overflow-x-auto` container (never
   the page). Generic for every destination — keyed off whichever link's
   `id` matches `current`, not hardcoded to Seller Data. Manual horizontal
   scrolling, keyboard focusability, and active-link styling are all
   unchanged; `aria-current="page"` was added to the active link as a
   small accessibility improvement in the same spot.
3. **"Sync listings" action** — see below.

## Synchronization action

### Backend

No listings-sync HTTP endpoint existed before this pass (confirmed by
inspecting every route file). Added:

- `app/amazon/listings_sync.py` — `AmazonListingsSyncTriggerService`.
  Resolves `seller_account_id` from the caller's organization-owned
  `marketplace_participation_id` (one repository lookup), then calls the
  existing `AmazonListingsIngestionService.sync()` exactly once. No
  ingestion, pagination, normalization, reconciliation, or lease logic is
  duplicated — all of that remains solely in `listings_ingestion.py`,
  unchanged. `organization_id` always comes from `current_organization_id()`,
  never the request.
- `app/api/routes/amazon_listings_sync.py` — `POST /api/v1/amazon/
  marketplace-participations/{marketplace_participation_id}/listings/sync`.
  Deliberately its own file/router, not added to `amazon_listings.py`, so
  that file's "strictly read-only" docstring claim stays literally true.

Every outcome `sync()` can produce is mapped to one of six sanitized HTTP
statuses:

| Category | Status | Reasons |
|---|---|---|
| Accepted and succeeded | 200 | `succeeded=true` |
| Already running | 409 | `already_running` |
| Scope not found/inaccessible | 404 | participation missing or foreign (own lookup, or `sync()`'s own `scope_not_found`) |
| Configuration/authorization failure | 503 | `scope_inactive`, `identity_missing`, `connection_unresolvable`, `secret_unresolvable`, `configuration_error`, or a raised `SpApiConfigurationError` |
| Amazon throttling/temporary failure | 502 | `throttled`, `authentication_failed`, `invalid_request`, `malformed_page`, `transient_request_failed`, `result_ceiling_exceeded`, `record_count_inconsistent`, `cyclic_pagination_token`, `duplicate_sku`, `ambiguous_marketplace_summary`, `malformed_offer_price`, `lease_lost` |
| Sanitized database/internal failure | 500 | `reconciliation_failed`, `unexpected_error`, or any genuinely unexpected raised exception |

The response body (`ListingsSyncTriggerResult`) never carries a seller ID,
marketplace ID, token, lease owner, page token, or raw Amazon payload —
only `succeeded`, a `reason` code, a pre-sanitized `message`, the
`ingestion_run_id` (an opaque internal UUID, already organization-scoped —
same precedent as `report_id`/`model_id`/`job_id`), and truthful counters.
A genuinely unexpected exception's own text is never included in any
response; the route logs only a generic warning with no exception content.

Ownership, active-state, identity, connection resolution, secret
resolution, and the single-writer lease are all still enforced *inside*
`sync()` itself, exactly as 12B.3D built and tested them — this endpoint
adds no new concurrency mechanism and cannot weaken the existing one. A
double-click or two concurrent requests for the same participation can
never both win: proven by an `asyncio.gather` concurrency test asserting
exactly one winner and one `already_running` loser.

### Frontend

`triggerListingsSync()` (`lib/api.ts`) POSTs to the new route and throws a
typed `ListingsSyncError` (`reason`, `kind`) on any non-2xx response,
parsed from the route's structured `detail` object rather than reusing the
plain-string `formatDetail` helper. The "Sync listings" button lives in
`SellerListingsSyncStrip` (the compact sync row), owned by the
`seller-listings.tsx` orchestrator:

- Disabled whenever there is no valid marketplace selection or a sync is
  already in flight (both server-side double-click prevention via the
  lease, and client-side via a `syncing` state flag).
- Shows "Synchronizing…" immediately on click.
- Never clears already-loaded summary/listings/detail while running.
- On success: bumps a `refreshToken` counter that both the summary and
  listings fetch effects (and the detail drawer, via a `refreshToken`
  prop) depend on, forcing a safe background refetch; also resets `page`
  in the URL so refreshed results start from a sane position. Shows a
  concise "N accepted[, M rejected]" message.
- On an `already_running` response: shown as an informational status
  line, not an error — the page remains fully usable.
- On any other failure: shows the pre-sanitized `message` from the
  backend only; never a raw exception or backend detail string.
- The transient status line is `role="status" aria-live="polite"` so
  assistive technology announces it without needing focus.
- Does not navigate away from the page on any outcome.

## Navigation and page flow

New nav entry **"Seller Data"** (`app-shell.tsx`, between Connection and
History) → `/seller-listings`. Labeled and described distinctly from
**Analyze** (ASIN Analyzer): the page header explicitly states it shows
"your own Amazon catalog and Amazon-reported listing health... different
from ASIN Analyzer, which evaluates public marketplace products."

Page flow: `app/seller-listings/page.tsx` is a thin wrapper
(`AppShell` + `Suspense`, matching every other page in this app) around
`components/seller-listings.tsx`, the orchestrator. The `Suspense`
boundary is required because the orchestrator uses `useSearchParams()`,
which Next.js requires to be wrapped for static builds.

## Marketplace context

Marketplace participations come from the **existing Connection API**
(`GET /api/v1/amazon/connection`, `AmazonConnectionOverview.marketplaces`)
— never hardcoded, never a separate lookup. That response previously
exposed only Amazon's own `marketplace_id` string; it did not expose the
internal `marketplace_participation_id` UUID the 12B.3E Listings API
requires as its path parameter, which would have made the Listings API
unreachable from any UI. **This was a genuine blocking backend gap**,
found and stopped on before writing UI code, per this milestone's own
instructions. Fixed minimally and additively: `SellerMarketplaceRead`
(`app/amazon/connection.py`) now also returns `id` (the participation's
own primary key) — the same pattern this app already uses for `report_id`,
profit `model_id`, and bulk `job_id`. No other backend contract changed.

Selection algorithm (`components/seller-listings.tsx`):

1. A valid `?participation=` in the URL (matching a marketplace the
   Connection API actually returned) wins immediately — no extra calls.
2. Otherwise, the summary endpoint is probed for every marketplace in
   parallel; the one with the most recent `sync.status === "succeeded"`
   wins.
3. Otherwise, the canonical standard storefront
   (`marketplace_id === "ATVPDKIKX0DER"`, `www.amazon.com` — the same id
   every backend test in this project already treats as canonical) wins.
4. Otherwise, the first marketplace in the list wins.

Step 2 never treats connection/authorization status as proof Listings
data exists — it reads `sync.status` from the 12B.3E summary endpoint
specifically, which is itself scoped to `run_type='listings'` only.

The selection is written back into the URL (`router.replace`, shallow,
`scroll: false`) so a refresh with a valid `?participation=` reproduces
the same selection without re-running the probe. Changing marketplace via
the selector resets `page` and closes any open `?listing=` detail, but
deliberately preserves filters/search/sort — only what the milestone
brief specified.

**Marketplace-name safety**: `sim1.stores.amazon.com`,
`sidevo.stores.amazon.mx`, and `invoicing-shadow-marketplace.amazon.com`
(present in this seller's real real participation list) are rendered
identically to any other marketplace — name, domain, country, and their
own synchronization state — with no blocklist or special-casing. The
current contracts (Connection API, Listings API) have no field that
reliably distinguishes an "internal-looking" marketplace from a genuine
small storefront, and inventing a name-pattern heuristic client-side would
be exactly the kind of fragile guess this milestone was told not to build.
See Known Limitations.

## API usage

`lib/api.ts` additions: `fetchListingsSummary`, `fetchListings` (accepts a
typed `ListingsQuery`), `fetchListingDetail`, and `ListingsApiError`
(`not_found | unavailable | unknown`) — same shape and `/api/v1/amazon`
base-path convention as the existing `amazonConnectionRequest` helper. No
new fetch pattern was invented.

`lib/types.ts` additions mirror the 12B.3E backend DTOs field-for-field,
including that `price_amount` is a **decimal string**, not a number
(confirmed against a live response) — `formatPrice`
(`lib/seller-listings-view.ts`) is the only place that parses it for
display.

## Component architecture

- `seller-listings.tsx` — orchestrator: connection load, marketplace
  resolution, URL state (participation/page/filters/sort/listing) via
  `useSearchParams`/`useRouter`/`usePathname`, and wiring summary/collection/
  detail fetches to the components below.
- `seller-listings-marketplace-selector.tsx` — the `<select>`.
- `seller-listings-sync-strip.tsx` — compact one-row sync status + last
  successful sync + latest accepted/rejected, with a restrained inline
  warning (not a full alert block) when the latest attempt failed but
  earlier data still exists.
- `seller-listings-summary.tsx` — the 6 KPIs in one `Panel` grid, not six
  separate cards.
- `seller-listings-filters.tsx` — search (debounced 300ms internally),
  all filters, sort, Reset.
- `seller-listings-table.tsx` — the columns table + server pagination
  footer.
- `seller-listings-detail.tsx` — the right-side drawer; fetches its own
  detail data given a listing id, independent of the table.
- `lib/seller-listings-view.ts` — pure formatting/labeling helpers
  (`formatPrice`, `formatDate(Time)`, `SYNC_STATUS_LABEL`,
  `highestSeverityFirst`, `CANONICAL_MARKETPLACE_ID`), matching the
  existing `lib/profit-view.ts` / `lib/copilot-view.ts` convention.

No new state-management or UI library was added. Existing primitives only:
`Panel`, `Kpi`, `PageHeader`, `EmptyState` (`ui/layout.tsx`), `Badge`,
`Button`, `Input`, `Alert`.

## Loading, empty, and error states

Distinct loading flags: connection load, marketplace-default resolution,
summary load, listings load — each renders its own inline
spinner/message, never a single opaque "loading" screen. Distinct empty
states: no seller account, no marketplaces, never-synchronized (shows
"Not synchronized yet" verbatim, sourced from `sync.status`, never
inferred from connection/authorization state), zero listings after a real
sync, and zero listings from filters (worded differently — "no listings
match these filters" vs. "no listings yet"). API failures render a
sanitized one-line message (`"Listings could not be loaded. Please try
again."` etc.) — the raw `ListingsApiError` message is never interpolated
into these strings, so a backend exception detail can never reach the
screen. A failed latest sync with a prior successful one keeps the table
showing the last-known-good listings, with a small amber warning line
above stating the data's actual freshness — it never hides the table.

## Security and withheld fields

No route or component ever sends `organization_id`; it is derived
server-side from trusted context, exactly as the 12B.3E backend already
requires — including the new sync trigger. The UI never renders
`organization_id`, `seller_account_id`, `connection_id`,
`last_ingestion_run_id`, lease fields, secret references, tokens, or
page/next tokens — verified both by DTO shape (the backend never sends
them) and by explicit tests asserting these values never appear in
rendered table/detail output. `offers`/`issues`/`fulfillment_availability`/
`product_types` are rendered as readable rows, never dumped as raw JSON.

The sync action never calls Amazon from the browser — it only ever calls
the ASI backend's own `POST .../listings/sync`, which internally resolves
the stored token reference and calls Amazon server-side, exactly like
every other Amazon-facing operation in this app. The browser never sees a
token, secret reference, or Amazon URL at any point in this flow.

## Known limitations

1. **No reliable way to flag "internal-looking" marketplaces.** See
   above — displayed conservatively with real sync state instead of a
   guessed blocklist.
2. **Default-marketplace resolution cost.** Step 2 of the selection
   algorithm calls the summary endpoint once per marketplace in parallel
   when no valid `?participation=` is in the URL. Bounded by how many
   marketplaces a seller has (single digits in every case observed so
   far); would need revisiting if that ever became large.
3. **The three post-review additions (product-type formatting, mobile nav
   scroll, sync action) have not yet had their own real-browser desktop +
   390px pass** — the real-browser review that validated the original page
   predates them. No browser/screenshot tool is available in this
   environment, so verification for these three relied on: 99 passing
   jsdom/testing-library interaction tests (including a dedicated
   `scrollIntoView` assertion for the nav fix, since jsdom has no real
   layout engine to observe visually), a clean production `next build`, a
   clean `tsc --noEmit`, and one live HTTP smoke check against the running
   dev server. A human should confirm all three at real mobile width
   before considering this final.
4. **Product-type filter is a free-text exact match**, not a dropdown of
   known values — the backend has no product-type enumeration endpoint,
   and inventing one was out of this milestone's scope.
5. **Synchronous HTTP request for the sync trigger.** `POST .../listings/
   sync` runs the entire ingestion pass (up to 50 pages) inside one HTTP
   request/response cycle — there is no background job/polling model yet.
   A large catalog or a slow Amazon response could make this a genuinely
   long-lived request; acceptable at the seller-catalog scale observed so
   far, but worth revisiting (background job + polling, or a webhook-style
   completion signal) if sync durations become a real problem.
6. **No client-side rate limiting on the button** beyond disabling it
   while a request is in flight — Amazon-side throttling (502) is surfaced
   to the user, but nothing here prevents a user from retrying rapidly
   after each attempt completes. The server-side lease still guarantees
   correctness either way.

## Expected next milestone

The sync trigger added here is deliberately minimal: one button, one
existing service, no background job, no scheduling, no retry policy
beyond what the user does manually. Natural next steps: a background-job/
polling model for long-running syncs, a visible sync-history view (the
`ingestion_run_id` this endpoint already returns is a natural anchor for
that), or extending Seller Data to a second SP-API domain (orders/
inventory) once that read API exists — none of these are started here.

# 12B.4A — Official Orders API Contract, Privacy Boundary & Ingestion Plan

Durable record of the 12B.4A research/contract/privacy-boundary pass.
Planning, sanitized fixtures, and architecture design only. No production
code, ORM model, Alembic migration, API route, UI, worker, or live Amazon
call was made while producing this report. Branch:
`milestone-12b4a-orders-contract`, created from verified `main`
(`89fa20bbe61f2b21a795b26ed41846f59e6bae91`).

## Phase 2 — Pinned authoritative sources

| Source | URL | Pinned commit | Retrieved |
|---|---|---|---|
| Current Orders API model (authoritative target for 12B.4) | `https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/orders-api-model/orders_2026-01-01.json` | `96d516badc8d69a566a4160e3c7b315600e043a7` | 2026-09-01/02, this session |
| Deprecated Orders API v0 model (reference only, not the implementation target) | `https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/orders-api-model/ordersV0.json` | `e584dcb641d9da22728673317ebb73d3cd2dd852` | 2026-09-01/02, this session |
| Orders API v0 human-readable reference | `https://developer-docs.amazon.com/sp-api/docs/orders-api-v0-reference` (states v0 is deprecated; migrate to 2026-01-01) | n/a (doc page) | same session |
| Orders API Migration Guide (v0 → 2026-01-01) | `https://developer-docs.amazon.com/sp-api/docs/orders-api-migration-guide` | n/a (doc page) | 2026-09-02, this session |
| Get order information (use-case guide — authoritative role list for `searchOrders`/`getOrder`) | `https://developer-docs.amazon.com/sp-api/docs/get-order-information` | n/a (doc page) | 2026-09-02, this session |
| Roles in the Selling Partner API | `https://developer-docs.amazon.com/sp-api/docs/roles-in-the-selling-partner-api` | n/a (doc page) | same session |
| Role Mappings for SP-API Operations (fuller reference; page too large to retrieve past the Orders API section in this session's fetch attempts — cited for completeness, not relied on for any specific claim below) | `https://developer-docs.amazon.com/sp-api/docs/role-mappings` | n/a (doc page) | attempted 2026-09-02, incomplete retrieval |
| Access Orders PII | `https://developer-docs.amazon.com/sp-api/docs/access-orders-pii` | n/a (doc page) | same session |
| Usage Plans and Rate Limits in the SP-API | `https://developer-docs.amazon.com/sp-api/docs/usage-plans-and-rate-limits-in-the-sp-api` | n/a (doc page) | same session |

Downloaded and parsed directly as JSON via `curl` + Python (primary-source
parse, not an AI-summarized fetch of a documentation page) — same method
used successfully for 12B.3A. SHA-256 of the two pinned model files as
retrieved this session (recorded here for integrity; the files themselves
are not committed to the repository, matching the 12B.3A convention of
citing URL + commit + checksum rather than vendoring the raw spec):

- `orders_2026-01-01.json`: `4bd47126b466b94ccb04d822e3b94edee1fa7977a8b376a8c329852ef61be431`
- `ordersV0.json`: `027ac6f5c97126647c6925db9be09f78c7c741cd1d8727a5367374a1846bedc5`

**Critical scope correction to the assignment's own investigation
checklist:** the current (non-deprecated) Orders API is not v0. Amazon
replaced ten v0 operations (`getOrders`, `getOrder`, `getOrderItems`,
`getOrderItemsBuyerInfo`, `getOrderBuyerInfo`, `getOrderAddress`,
`updateShipmentStatus`, `confirmShipment`, `getOrderRegulatedInfo`,
`updateVerificationStatusForOrder`) with exactly **two** operations in
`2026-01-01`: `searchOrders` (`GET /orders/2026-01-01/orders`) and
`getOrder` (`GET /orders/2026-01-01/orders/{orderId}`). There is no
separate `getOrderItems` call in the current contract — `orderItems[]` is
embedded directly in the `Order` object, controlled by the same
`includedData` query parameter that also controls `buyer`/`recipient`/
`proceeds`/etc. 12B.4 targets `2026-01-01` exclusively; v0 is investigated
only as historical/reference context, never as an implementation target.

### `searchOrders` — `GET /orders/2026-01-01/orders`

Parameters (all query, none required — see mutual-exclusivity rule below):

| Name | Type | Notes |
|---|---|---|
| `createdAfter` | date-time | Exactly one of `createdAfter`/`lastUpdatedAfter` **must** be provided. |
| `createdBefore` | date-time | Only valid alongside `createdAfter`. Must be ≥ `createdAfter` and **at least two minutes before the time of the request** (documented eventual-consistency safety margin). |
| `lastUpdatedAfter` | date-time | Exactly one of `createdAfter`/`lastUpdatedAfter` **must** be provided; providing this forbids `createdAfter`/`createdBefore`. |
| `lastUpdatedBefore` | date-time | Only valid alongside `lastUpdatedAfter`. Same "≥ after date, ≥2 minutes before now" rule. |
| `fulfillmentStatuses` | array\<enum\> | `PENDING_AVAILABILITY, PENDING, UNSHIPPED, PARTIALLY_SHIPPED, SHIPPED, CANCELLED, UNFULFILLABLE`. |
| `marketplaceIds` | array\<string\>, max 50 | |
| `fulfilledBy` | array\<enum\> | `MERCHANT, AMAZON`. |
| `maxResultsPerPage` | integer 1–100 | Default 100. |
| `paginationToken` | string | Request-side pagination parameter name. |
| `includedData` | array\<enum\> | `BUYER, RECIPIENT, PROCEEDS, EXPENSE, PROMOTION, CANCELLATION, FULFILLMENT, PACKAGES, TAX, PAYMENT, FULFILLMENT_ORDERS`. |

**This is a hard, documented constraint, not a style choice:** you must
supply exactly one of `createdAfter`/`lastUpdatedAfter`, and the two
families (`created*` vs `lastUpdated*`) are mutually exclusive on the same
request. An ingestion client cannot mix "new orders since X" and "orders
touched since Y" in one call.

Usage plan (from the model itself, not a third party): **0.0056
requests/second, burst 20.** Translated precisely: `1 / 0.0056 ≈ 178.6
seconds` between sustained requests once burst is exhausted — **not**
"roughly 3 minutes" loosely, but a specific ~178.6-second sustained
interval. A fully-drained bucket takes `20 / 0.0056 ≈ 3571 seconds ≈ 59.5
minutes` to refill to full burst capacity from empty. **Burst 20 is a
one-time allowance, not a sustained rate** — a client that fires 20
requests back-to-back, then assumes it can keep going at that pace, will
be throttled for the next ~178.6 seconds per request thereafter. This is
dramatically tighter than Listings' `searchListingsItems` (5 req/s, burst
5) and is the dominant constraint on every part of 12B.4's ingestion
design (Phase 4's dedicated rate-limit-implications subsection below).

### `getOrder` — `GET /orders/2026-01-01/orders/{orderId}`

Parameters: `orderId` (path, required), `includedData` (query, same enum as
above). Usage plan: **0.5 requests/second, burst 30** — over 89x more
headroom than `searchOrders`. Not part of 12B.4's incremental-sync loop;
useful later for a "resync one order on demand" affordance.

### Response shape — what's unconditional vs. `includedData`-gated

`Order` required fields: `orderId, createdTime, lastUpdatedTime,
salesChannel, orderItems`. `OrderItem` required fields: `orderItemId,
quantityOrdered, product`. Everything else on `Order` (`buyer`, `recipient`,
`proceeds`, `payment`, `tax`, `fulfillment`, `packages`,
`fulfillmentOrders`) and on `OrderItem` (`proceeds`, `expense`,
`promotion`, `cancellation`, `fulfillment`, `tax`) maps **one-to-one** by
name to an `includedData` enum value — confirmed by cross-referencing each
`includedData` value's description ("...for the order and order items")
against the definitions that reference it. **`product` (ASIN, seller SKU,
title, condition, price, serial numbers, customization) is not gated by
any `includedData` value — it is always present when the item exists.**
This is the single most important fact for Phase 3: SKU/ASIN-level
identification and pricing require zero opt-in flags and zero restricted
role, at all.

`packages` (`OrderPackage`) is documented as "Only available for
merchant-fulfilled (FBM) orders"; `fulfillmentOrders` is "Only available
for EasyShip orders at present" — both niche/conditional, not universal.

### Pagination

- Response field: `SearchOrdersResponse.pagination.nextToken` (nested
  under a `pagination` object, absent entirely once there are no more
  pages — same "absent key, not empty string" signal used by Listings).
- Request field: `paginationToken` (**different name from the response
  field** — this asymmetry must be encoded explicitly in the client, not
  assumed to mirror Listings' `pageToken`/`pageToken` symmetry).
- Documented token lifetime: **24 hours.**
- Documented: "All other parameters must be provided with the same values
  that were provided with the request that generated this token, with the
  exception of `maxResultsPerPage` and `includedData`, which can be
  modified between calls." I.e. the date-range/status/marketplace filters
  are locked for the life of a paginated traversal; only page size and
  requested-data-shape may vary mid-traversal.

### Errors

Both operations return `400, 403, 404, 413, 415, 429, 500, 503`, all
shaped as `ErrorList = { errors: Error[] }`, `Error = { code, message,
details? }` (`code`/`message` required) — identical error envelope shape
to Listings.

### Rate limits / retry (general SP-API guidance, `usage-plans-and-rate-limits` doc)

- Token-bucket model: tokens refill continuously at the documented
  rate/second up to the burst ceiling; each call consumes one token; an
  empty bucket throttles with `429`.
- `x-amzn-RateLimit-Limit` response header reports the *actual* applied
  rate when present, but the doc explicitly warns **"you must not depend
  on this header being present."** When it *is* present, the future
  client must treat it as **authoritative over the static documented
  default** (`0.0056`/burst `20`) — Amazon explicitly notes some sellers
  are granted higher throughput than the documented default, so a client
  that only ever paces against the hardcoded default would under-utilize
  a seller-specific higher grant, and one that ignores the header when a
  *lower* grant is in effect would under-throttle and generate needless
  `429`s. Fall back to the documented default only when the header is
  absent.
- **No `Retry-After` header is documented on `429` for either Orders
  operation** (matches the Listings finding) — backoff timing must be
  ASI's own client-side policy, not server-supplied.
- Amazon's own guidance: "A 429 is a retry-able status code... repeated
  throttled requests require a back-off strategy," with no specific
  algorithm mandated (exponential vs. linear is ASI's choice — 12B.3's
  existing `listings_worker_poll_error_base_backoff_seconds`/`..._max_...`
  pattern is directly reusable, with a distinct, longer base/max pair for
  Orders given the ~178.6s sustained interval computed above).
- **This budget must never be worked around with concurrency.** Two
  simultaneous requests against the same seller-account's `searchOrders`
  budget do not double the effective rate — they draw from the same
  bucket and simply produce more `429`s and wasted burst tokens sooner.
  The same "exactly one worker" invariant already proven operationally for
  Listings (12B.3H/12B.3I) applies identically, and more critically, here:
  Orders' bucket is roughly 900x tighter than Listings', so any accidental
  duplicate worker or duplicate concurrent job would be far more costly to
  recover from (see the burst-refill math above — burning the shared
  20-request burst on a wasted duplicate costs ~an hour of full-rate
  capacity to recover).

### Retention and eventual consistency (primary-source, from the model itself)

- Retention, quoted verbatim from the model's own `info.description`:
  **"This API does not display order data that is more than two years
  old, except in the JP, AU, and SG marketplaces, for which data from 2016
  and after is available."** This is an authoritative API-level ceiling,
  not a product default — Phase 4 distinguishes the two.
- Consistency lag, quoted verbatim from the `createdBefore`/
  `lastUpdatedBefore` parameter descriptions: any `*Before` bound "must be
  ... at least two minutes before the time of the request." This is
  Amazon's own primary-source statement of the ingestion-relevant
  eventual-consistency window for `searchOrders`/`getOrder` (independent,
  corroborating secondary-source community commentary — describing the
  older, deprecated v0 `getOrders` operation's own visibility delay —
  reports the same ~2-minute figure; cited here only as corroboration,
  never as the source of the requirement, and never as this milestone's
  implementation target).

### Authorization roles and Restricted Data Token — CORRECTED

An earlier draft of this report conflated two separate concepts: *what
authorizes calling `searchOrders`/`getOrder` at all* and *what authorizes
receiving PII once you're calling them*. It incorrectly implied
`Direct-to-Consumer Shipping (Restricted)` was **the** (sole, required)
gate for the endpoints themselves. Primary-source re-verification this
session — the official **"Get order information"** use-case guide
(`developer-docs.amazon.com/sp-api/docs/get-order-information`) — corrects
this. The three concepts below are now kept explicitly separate.

**1. Endpoint authorization (calling `searchOrders`/`getOrder` at all).**
Quoted structure from the primary source: *"At least one of the following
roles assigned to your developer profile and selected in the app
registration page"* is sufficient to call these operations — this is an
**"any one of," not "this specific one,"** requirement, and the accepted
list is broad and mostly non-restricted:

`Amazon Fulfillment`, `Buyer Communication`, `Buyer Solicitation`,
`Finance and Accounting`, `Inventory and Order Tracking`, `Pricing`,
`Product Listing`, `Selling Partner Insights`, `Professional Services
(Restricted)`, `Direct-to-Consumer Shipping (Restricted)`, `Tax Invoicing
(Restricted)`, `Tax Remittance (Restricted)`.

`Direct-to-Consumer Shipping (Restricted)` is **one of twelve** sufficient
roles, not a special or sole requirement — eight of the twelve
(`Amazon Fulfillment` through `Selling Partner Insights` above) are
**ordinary, non-restricted roles**. Notably, `Product Listing` and
`Inventory and Order Tracking` are on this list — roles an app already
using SP-API for Listings (12B.3) plausibly already holds. This report
does not claim ASI's app holds any specific one of the twelve (see point 3
below), only that the *shape* of the requirement is "any one of a broad
list," not "one specific restricted role."

**2. PII authorization (receiving `buyer`/`recipient` data specifically).**
This is where restricted roles come in, and this part of the original
report was accurate: access to protected PII categories requires the
*appropriate restricted role* —
`Direct-to-Consumer Shipping (Restricted)` for `recipient`
(shipping-address) data, and `Tax Invoicing (Restricted)` /
`Tax Remittance (Restricted)` (with additional geographic restrictions,
US/JP/SG called out specifically) as alternate paths to `buyer` data.
Confirmed unchanged from Phase 2's original finding: **the Orders API
`2026-01-01` does not use Restricted Data Tokens (RDTs) for this at all**
— quoted directly from Amazon's own "Access Orders PII" page: *"Accessing
PII with the Orders API v2026-01-01 does not require a Restricted Data
Token (RDT),"* and reconfirmed by the Migration Guide: *"Role-based
permissions replace Restricted Data Token (RDT) generation."* A restricted
role may be needed for protected buyer/recipient information — but 12B.4
will not request that information in the initial slice, so this
requirement is inert for this milestone regardless of which restricted
roles the app does or doesn't hold.

**3. ASI's initial non-PII slice — what this milestone will and will not
request.** Unchanged and reconfirmed: 12B.4's first slice omits `BUYER`
and `RECIPIENT` from every request, and therefore avoids customer names,
email addresses, telephone numbers, shipping addresses, billing addresses,
gift messages, customized-product personal content, tax-registration
identifiers, and any other customer PII — and never requests or mints an
RDT (moot under `2026-01-01` regardless, per point 2, but stated as an
explicit, permanent design constraint independent of that fact).

**The production authorization gate, stated precisely:**

> Before the first live Orders call, confirm that the ASI production
> application has **at least one** Amazon-accepted role for
> `searchOrders`/`getOrder` (any one of the twelve listed in point 1). The
> repository does not currently prove which role(s) Amazon granted this
> application — this is **not** evidence the role is absent, only that it
> is unverified from code/docs alone.

Why this repository cannot answer it either way: grep across
`docs/AI_HANDOVER/`, `docs/checkpoints/`, `apps/api/app/amazon/*.py`, and
`apps/api/.env.example` for role/RDT/restricted-operation language turned
up nothing — no prior milestone recorded which roles were selected when
the Draft/Production app was registered. The OAuth `authorize` URL builder
(`app/amazon/connection.py`) also carries no `scope` parameter to inspect
— SP-API self-authorization roles are fixed at app-registration time in
Amazon's own Developer Console, not negotiated per authorization request,
so no code path in this repository could ever reveal the answer either
way. **Role possession is classified UNKNOWN and UNVERIFIED, never
ABSENT.** Confirming it requires checking the app's role list directly in
Seller Central's Developer Console (out of this report's scope) or a live
call (forbidden by this milestone's boundaries). Because 12B.4's design
never requests PII anyway, this gap does not block 12B.4A, 12B.4B, or
12B.4C — but 12B.4D (first live ingestion) must not proceed on any
assumption about role status either way; the user must confirm it
out-of-band before any live call is authorized.

### Sandbox behavior

Three official static sandbox scenarios are embedded in the model itself
(`x-amzn-api-sandbox` extension on the `searchOrders` `200` response),
keyed by exact request parameters — this is Amazon's own canned-response
sandbox mechanism, not a generic test environment:

1. `createdAfter=2024-12-25T00:00:00Z`, `marketplaceIds=[A1VC38T7YXB528]` (JP), full `includedData` — single order, no further pages.
2. `createdAfter=2024-12-23T00:00:00Z`, `marketplaceIds=[A1F83G8C2ARO7P]` (UK), full `includedData` — includes a `pagination.nextToken`, demonstrating a real multi-page scenario.
3. `createdAfter=2024-12-20T00:00:00Z`, `marketplaceIds=[A2Q3Y263D00KWC]` (BR), `includedData` including `TAX`/`PAYMENT` — demonstrates `taxRegistrationNumber` and `paymentExecutions` shapes.

All three scenarios use Amazon's own synthetic buyer names/addresses (e.g.
"John Smith", "Sarah Thompson", "Carlos Oliveira") — confirmed
already-fake, but not reused verbatim in this repository's fixtures
(Phase 7) in favor of even more obviously-synthetic `FIXTURE-*`/`*.invalid`
placeholders, per this report's own sanitization standard.

## Phase 3 — Privacy and Restricted Data boundary

**Default decision confirmed: 12B.4 will not ingest customer PII and will
not request or mint a Restricted Data Token.** The contract proves the
non-PII analytics goal is achievable without either — this is not a
compromise, it falls out directly from the schema: `product`
(ASIN/SKU/price/title) and `proceeds`/`fulfillment`/`cancellation`
(minus specific sub-fields, below) are available with zero PII exposure.

### Allowed initial slice — confirmed available without PII/RDT

| Field (as named in this report) | Amazon path | Gate |
|---|---|---|
| Amazon order identifier | `Order.orderId` | none (required, unconditional) |
| Created/purchased timestamp | `Order.createdTime` | none (required) |
| Updated timestamp | `Order.lastUpdatedTime` | none (required) |
| Order status | `Order.fulfillment.fulfillmentStatus` | `includedData=FULFILLMENT` |
| Fulfillment channel | `Order.fulfillment.fulfilledBy` | `includedData=FULFILLMENT` |
| Sales channel / marketplace | `Order.salesChannel` | none (required) |
| Shipped/unshipped item counts | `OrderItem.fulfillment.quantityFulfilled/quantityUnfulfilled` | `includedData=FULFILLMENT` |
| Order currency and totals | `Order.proceeds.grandTotal`, `.breakdowns[]` | `includedData=PROCEEDS` |
| Seller SKU | `OrderItem.product.sellerSku` | none |
| ASIN | `OrderItem.product.asin` | none |
| Ordered/shipped quantities | `OrderItem.quantityOrdered`, `.fulfillment.quantityFulfilled` | quantityOrdered: none; fulfilled: `FULFILLMENT` |
| Item price/tax/discount aggregates | `OrderItem.product.price`, `.proceeds.breakdowns[]` | price: none; proceeds breakdowns: `PROCEEDS` |
| Business/Prime flags | `Order.programs[]` (contains `PRIME`, `AMAZON_BUSINESS`, etc.) | none |
| Cancellation indicator (status only) | `Order.fulfillment.fulfillmentStatus == CANCELLED`; `OrderItem.cancellation.cancellationRequest.requester` / `.cancellationExecution.cancelledBy` (enum fields only) | `includedData=CANCELLATION` for item-level requester/cancelledBy |
| Replacement indicator | `Order.associatedOrders[].associationType` (e.g. `REPLACEMENT_ORIGINAL_ID`) | none |
| Shipment state / carrier / tracking | `Order.packages[]` (FBM only) | `includedData=PACKAGES` |

**Recommended `includedData` set for 12B.4: `PROCEEDS, FULFILLMENT,
CANCELLATION, PACKAGES`.** Deliberately excludes `BUYER`, `RECIPIENT`,
`PAYMENT`, `TAX` entirely (see below), and also excludes `EXPENSE`,
`PROMOTION`, `FULFILLMENT_ORDERS` from the *first* slice as unneeded for
the stated analytics goals (promotion/expense data can be added later as
its own reviewed increment; `FULFILLMENT_ORDERS` is EasyShip-only and
low-value).

### Excluded initial slice — confirmed PII-bearing or PII-adjacent

| Field | Amazon path | Why excluded |
|---|---|---|
| Buyer name, email, company, PO number | `Order.buyer.*` | Direct PII; gated by `includedData=BUYER`, which 12B.4 never requests. |
| Shipping/billing address, recipient name, delivery instructions, precise location | `Order.recipient.*` | Direct PII; gated by `includedData=RECIPIENT`, never requested. |
| Payment instrument details | `Order.payment.paymentExecutions[]` (`paymentMethod`, `cardBrand`, `authorizationCode`, `acquirerId`) | Gated by `includedData=PAYMENT`, never requested. |
| Tax-registration identifiers | `Order.tax.taxRegistrations[].taxRegistrationNumber`/`.legalName` | Gated by `includedData=TAX`, never requested. **Note:** tax *amount* totals do not require this flag — they live under `proceeds.breakdowns[type=TAX]`, which 12B.4 does request. `TAX` is needed only for registration identifiers, which 12B.4 has no use for. |
| Gift message (free text) | `OrderItem.fulfillment.packing.giftOption.giftMessage` | **Cannot be excluded by omitting an `includedData` flag** — it is nested inside `ItemFulfillment`, the same object that carries the wanted `quantityFulfilled`/`quantityUnfulfilled` fields, both gated by the single `FULFILLMENT` flag. Requires **application-layer field-level redaction**: the future 12B.4C parser must read `quantityFulfilled`/`quantityUnfulfilled`/`shipping.*` from this object and explicitly never persist or log `packing.giftOption` at all, even though the API will return it whenever `FULFILLMENT` is requested. Fixture `16_restricted_pii_fields_present.json` encodes this exact case. |
| Cancellation free-text reason | `ItemCancellationRequest.cancelReason`, `ItemCancellationExecution.cancelReason` | Buyer/merchant-authored free text, same redaction pattern as gift message — persist only the enum `requester`/`cancelledBy` fields from the same `CANCELLATION`-gated object, never `cancelReason`. |
| Customized product URL | `OrderItem.product.customization.customizedUrl` | **Not gated by any `includedData` flag at all** — part of the always-present `product` object. Points to Amazon Custom personalization data (potentially customer-submitted text/images at that URL). Must be explicitly dropped at the parsing layer regardless of which `includedData` values are requested. |
| Product serial numbers | `OrderItem.product.serialNumbers[]` | Also unconditional/always-present on `product`. Not customer PII, but not needed for any stated analytics goal and adds supply-chain-sensitive surface for no benefit in this slice — deferred, not persisted initially. |
| Merchant ship-from address | `OrderPackage.shipFromAddress` (`MerchantAddress`) | Not customer PII (it is the *seller's own* warehouse/address), but out of scope for the initial slice — `PACKAGES` will be requested for `packageStatus`/`carrier`/`trackingNumber`/timestamps only; `shipFromAddress` should not be persisted either, to keep the "no address data at all" boundary simple and audit-friendly rather than partially exceptioned. |

### Answers to Phase 3's six explicit questions

1. **Which endpoints/fields require an RDT?** None, for the `2026-01-01`
   Orders API — RDT is not the gating mechanism for this API version at
   all (see Phase 2). This does not mean PII is free to request; it means
   the gate is application-role possession, checked by Amazon at request
   time, not a token ASI mints.
2. **Can basic `searchOrders`/`getOrder` analytics operate without
   restricted PII?** **Yes, proven by the schema itself** — `product`
   (SKU/ASIN/price/title) is unconditional, and `PROCEEDS`/`FULFILLMENT`/
   `CANCELLATION`/`PACKAGES` supply every other field in the allowed-slice
   table above without touching `BUYER`/`RECIPIENT`/`PAYMENT`/`TAX`. The
   "stop and report" fallback in this phase's instructions is **not**
   triggered.
3. **Which Amazon application roles are required?** **Any one of twelve**
   documented roles authorizes calling `searchOrders`/`getOrder` at all —
   `Amazon Fulfillment`, `Buyer Communication`, `Buyer Solicitation`,
   `Finance and Accounting`, `Inventory and Order Tracking`, `Pricing`,
   `Product Listing`, `Selling Partner Insights`, `Professional Services
   (Restricted)`, `Direct-to-Consumer Shipping (Restricted)`, `Tax
   Invoicing (Restricted)`, `Tax Remittance (Restricted)` — not a single
   mandatory role. Separately, `Direct-to-Consumer Shipping (Restricted)`
   / `Tax Invoicing (Restricted)` / `Tax Remittance (Restricted)` are
   required only if PII (`buyer`/`recipient`) is ever requested, which it
   will not be in 12B.4. See the corrected "Authorization roles" subsection
   above for the full endpoint-vs-PII distinction.
4. **Does the current production authorization appear to possess a
   required role?** **Unknown/unverified — not absent.** See the
   "Authorization roles" subsection above. No code or documentation
   inspection can answer this either way; it must be confirmed out-of-band
   in Seller Central before any live call in a future slice. Note that
   ASI's existing production app already integrates Listings (12B.3),
   which plausibly already required a role such as `Product Listing` —
   itself one of the twelve endpoint-authorizing roles for Orders — but
   this report does not assert that as confirmed, only as a plausible,
   unverified reason for optimism.
5. **Data-retention and deletion obligations?** The API itself won't
   surface data older than 2 years (2016+ for JP/AU/SG) — that is a
   read-side ceiling on Amazon's side, not a deletion obligation on ASI's
   side. Because 12B.4 stores no customer PII, ASI's own retention/deletion
   obligations for this data are materially lighter than they would be for
   a PII-bearing design; this report does not need to design a
   PII-deletion workflow. Standard organization-data lifecycle rules
   (already governing Listings) apply: soft-delete/archival, never
   destructive purge of historical rows, per this repository's existing
   database rules.
6. **Should order IDs be treated as confidential business identifiers in
   logs/APIs?** **Yes**, by the same standard already applied to
   participation/listing/job IDs throughout 12B.3: `orderId` and
   `orderItemId` are business identifiers, not secrets, but are excluded
   from worker/application logs on the same "unknown data remains unknown
   to logs, identifiers stay out of logs" posture already enforced for
   Listings. They are safe to store in the database and return through a
   future authenticated read API, exactly like `listing.id`/`participation.id`
   today.

### Required log-redaction rules (Phase 3, point 7)

- Never log `giftOption.giftMessage`, `cancelReason` (either), `customization.customizedUrl`, or any `buyer`/`recipient`/`payment`/`tax` field — these must never be requested in the first place, but the rule must exist independently of that, as defense in depth (fixture 16 tests exactly this).
- Never log full order/item JSON payloads at INFO level (mirrors the existing Listings worker's one-line "claimed a job run_id=..." / "finished a job ... succeeded=..." pattern — no business-object bodies in logs today, and Orders must not regress that).
- `orderId`/`orderItemId`/`marketplace_participation_id`/`amazon_ingestion_runs.id` follow the same "never printed in aggregate verification reports, fine in the database and an authenticated API" rule already in force for Listings identifiers throughout 12B.3's operational verification passes.

### Bundled-field redaction — architectural enforcement (not just a rule of thumb)

`giftOption.giftMessage` and `cancelReason` (both request/execution
variants) are the two confirmed hazards: both live *inside* objects
(`ItemFulfillment` via `FULFILLMENT`; `ItemCancellation` via
`CANCELLATION`) that 12B.4 does request, because those same objects also
carry wanted, non-PII fields (`quantityFulfilled`/`quantityUnfulfilled`;
`requester`/`cancelledBy`). Amazon does not offer a finer-grained flag to
separate them. This cannot be solved by *choosing not to request* a flag —
it must be solved architecturally, in the future 12B.4C parser:

- **No broad `model_dump()`/`.dict()` persistence of a parsed response
  object.** The future DTO layer must never write "the whole parsed order"
  or "the whole parsed item" into a database column or a JSON field in one
  call. Every persisted field — scalar column or JSON column alike — must
  be assigned from an explicit, named source field.
- **No raw-object persistence.** The raw HTTP response body (or the raw
  parsed dict before field-level mapping) must never be stored verbatim
  anywhere, including as a JSON/JSONB "audit" column — a common pattern
  elsewhere that must **not** be reused for Orders, precisely because the
  raw object is the one shape that can carry `giftOption`/`cancelReason`/
  `buyer`/`recipient` end to end into storage.
- **No logging of discarded content.** Redaction must be silent — the
  parser drops these fields without logging what it dropped (logging "here
  is the gift message we discarded" would itself be the leak).
- **No generic JSON blob capable of carrying excluded PII.** Every JSON
  column proposed in Phase 5 (`order_aliases`, `programs`,
  `associated_orders`, `proceeds_breakdown` at both levels) is scoped to a
  **specific, already-vetted, non-PII sub-shape** (alias id/type pairs;
  enum program names; associated-order id/association-type pairs;
  category-labeled money amounts) — never a passthrough of an arbitrary
  API object. The future parser must construct each of these JSON values
  from an explicit field allowlist, so an unrecognized or future field
  from Amazon (fixture `15_unknown_additive_fields.json`) can never
  silently ride into a stored JSON blob alongside the fields that do
  belong there. This is the same principle stated as three separate rules
  above, applied specifically to the JSON columns rather than only the
  scalar ones.
- **Fixture coverage proving this, already delivered:**
  `16_restricted_pii_fields_present.json` populates every excluded field
  at once (`buyer`, `recipient`, `payment`, `tax`, `product.customization`,
  `fulfillment.packing.giftOption`) and `06_cancelled_order.json`
  populates both `cancelReason` variants — both exist specifically so a
  future 12B.4C test can assert none of these values reach a parsed DTO,
  a persisted row, or a log line. This is called out in Phase 8 as a
  **required**, not optional, CI gate for 12B.4C.

## Phase 4 — Incremental ingestion semantics

1. **Initial historical lookback.** API capability ceiling: 2 years (2016+
   JP/AU/SG). Recommended **product default: 30 days** on first sync per
   marketplace participation — conservative, keeps the very first job's
   page count bounded given the 0.0056 req/s ceiling, and matches the
   spirit of "start narrow, widen deliberately" already used for Listings.
   The two must be tracked as separate concepts in code/config (a capacity
   ceiling vs. a product default), never conflated.
2. **Incremental cursor.** Use `lastUpdatedAfter` exclusively for every
   run after the first (never `createdAfter` — `lastUpdatedAfter` is the
   only field that surfaces status/total/item mutations on
   already-known orders, per the contract's own framing: "An update is
   any change made by Amazon or the seller, including changes to order
   status"). Maintain a durable **high-water-mark cursor** per
   `marketplace_participation_id` (see Phase 5 — dedicated checkpoint
   table). Before each run, subtract a **safety overlap window** from the
   stored watermark — recommend **15–30 minutes**, comfortably larger than
   the documented mandatory 2-minute consistency gap, to absorb
   worst-case delayed visibility without under-covering. Overlap
   guarantees convergence (Phase 4 point 12) because ingestion is a pure
   upsert.
3. **Pagination.** One page per client call, exactly as instructed. The
   *durable job* (not the client) owns looping across pages until
   `pagination` is absent or a bounded per-job page cap is hit (necessary
   given the 20-request burst ceiling — a single job must never attempt to
   drain an unbounded number of pages in one burst). `paginationToken`
   values are held only in the worker's in-memory loop state for the
   duration of one job attempt — **never persisted to the database, never
   logged, never exposed via any public API**, consistent with the token's
   own 24-hour expiry and Phase 4's explicit instruction.
4. **Idempotency.** Upsert `amazon_seller_orders` on
   `(marketplace_participation_id, amazon_order_id)`. Upsert
   `amazon_seller_order_items` on `(order_id, amazon_order_item_id)` —
   `orderItemId` is documented as "a unique identifier for this specific
   item within the order," an evidenced natural key exactly as required.
5. **Updates.** Every upsert overwrites all mutable fields (status,
   totals, quantities, item-level proceeds/fulfillment) to the
   newly-fetched values and refreshes `amazon_last_updated_at`/
   `last_seen_at`/`last_ingestion_run_id`. `first_seen_at` is set once on
   insert and never touched again.
6. **Cancellation.** A cancellation is just another `fulfillmentStatus`
   value (`CANCELLED`) delivered through the normal upsert path — never a
   deletion, never inferred from absence.
7. **Completeness.** An incremental `lastUpdatedAfter` window is never
   treated as a complete snapshot of all orders. There is **no**
   deactivate-missing / mark-stale pass for Orders at all, at any point —
   this is a deliberate, permanent divergence from the Listings
   full-resync-then-deactivate model (Phase 5 restates this).
8. **Time.** All Amazon timestamps (`createdTime`, `lastUpdatedTime`,
   window bounds, ship/deliver windows) are already ISO-8601 UTC and are
   stored verbatim as `DateTime(timezone=True)`, unmodified. `first_seen_at`/
   `last_seen_at`/lease/lifecycle fields use `func.now()` (database server
   time), exactly matching the existing `amazon_ingestion_runs`/
   `amazon_seller_listings` convention — never derived from Amazon's clock.
9. **Failure.** Each page's upserts commit before the next page is
   fetched (small, frequent transactions, not one giant transaction for
   an entire multi-page job) so a mid-pagination failure preserves every
   already-committed order/item row. The job's own `status` moves to
   `failed`/`waiting_to_retry` per the existing state machine, and —
   critically — **the durable watermark cursor only advances to the
   highest `lastUpdatedTime` actually committed**, never to "now" and
   never to the requested window's upper bound. This makes the next run's
   overlap-adjusted `lastUpdatedAfter` naturally re-cover exactly the
   uncommitted tail, with no separate resume/replay logic required. Prior
   committed Orders rows are never rolled back or hidden because a later
   page failed.
10. **Resume/retry.** No new retry engine: reuse `amazon_ingestion_runs`'
    existing `retry_count`/`next_retry_at`/`lease_owner`/
    `lease_expires_at`/`last_heartbeat_at` machinery verbatim, scoped by a
    new `run_type='orders'` value. `NextToken`/`paginationToken` is never
    part of that persisted state (point 3) — retrying an orders job means
    re-running the whole bounded page-loop from the watermark-derived
    `lastUpdatedAfter`, not resuming a specific page.
11. **Marketplace.** Every stored *row*'s ownership is still scoped through
    `marketplace_participation_id`, exactly like `amazon_seller_listings`
    — no seller-account-level or organization-level assumption of a single
    marketplace. **Open tension, flagged rather than silently resolved:**
    `searchOrders` accepts up to 50 `marketplaceIds` per call and the
    0.0056 req/s budget is shared per seller-account regardless of how
    many participations that account has (see the rate-limit implications
    subsection immediately below), which argues for fetching *multiple*
    participations' orders in one combined call rather than repeating
    Listings' one-job-per-participation pattern N times. But this milestone's
    proposed `amazon_ingestion_runs` provenance pattern (Phase 5) —
    composite FK `(last_ingestion_run_id, marketplace_participation_id)`,
    copied directly from `amazon_seller_listings` — assumes a run row is
    scoped to exactly **one** participation, the same way every existing
    Listings run is. A run that legitimately spans several participations
    in one HTTP call cannot satisfy that composite FK as designed without
    further schema work. This report does **not** resolve that tension —
    it is listed explicitly in "Unresolved questions" below and must be
    settled during 12B.4B (schema stage), not assumed away here. The two
    candidate resolutions are (a) keep one run per participation as today,
    accepting some rate-limit inefficiency for multi-participation seller
    accounts, or (b) allow one orders run to own multiple participations'
    worth of rows, which requires redesigning the composite-FK provenance
    pattern for `amazon_seller_orders`/`amazon_seller_order_items`.
12. **Deduplication.** A direct consequence of point 4's upsert keys:
    overlapping `lastUpdatedAfter` windows (from the mandated overlap
    margin, or from a retried job re-covering a failed tail) can never
    produce duplicate rows — the unique constraint makes re-processing
    the same order/item a no-op update, not a new row.
13. **Rate limits.** Bounded retry lives inside the single durable job
    (point 10) with the existing backoff-bounds validator
    (`_validate_listings_worker_poll_error_backoff_bounds`'s pattern,
    generalized) applied to a new orders-specific base/max backoff pair.
    The existing partial-unique-index duplicate-trigger-prevention
    technique (`uq_amazon_ingestion_runs_active_listings_scope`)
    generalizes directly to an equivalent orders-scoped unique partial
    index, so a duplicate-trigger click during an in-flight orders job is
    rejected at the database level exactly as it already is for Listings.

### Rate-limit implications (detailed, per Amazon's 0.0056 req/s / burst 20)

- **Initial historical sync.** At ~178.6s/request sustained after a burst
  of 20, a 30-day backfill with, say, 40 pages of results (100/page) needs
  20 pages "free" from burst, then 20 more pages × 178.6s ≈ 59.5 minutes.
  A higher-volume seller or a wider lookback could take multiple hours.
  This must be designed and communicated as a **long-running background
  process**, not a quick synchronous sync — explicitly unlike Listings,
  where the entire 10-row test sync completed in seconds. 12B.4F (UI)
  must not reuse Listings' "sync completes almost immediately" UX
  assumption for Orders.
- **Multiple marketplaces.** See point 11 above — the budget is per
  seller-account, not per participation, so N participations sharing one
  combined request (where the schema allows it) is more budget-efficient
  than N independent per-participation jobs; this is a real efficiency
  argument, not yet a finalized schema decision (open tension, above).
- **Large order volumes.** A single job must cap the number of pages it
  attempts per execution (a bounded "max pages per attempt," not an
  unbounded drain-until-`pagination`-absent loop) — otherwise one job
  could legitimately need to hold its lease for hours, which is
  operationally different from anything Listings has ever needed to do
  and should be an explicit, reviewed config value, not an accident of
  "however long pagination happens to take."
- **Worker leases and heartbeat renewal during rate-limit waits.** An
  inter-page wait of ~178.6 seconds is far longer than anything the
  Listings worker's cadence has needed to model. The future orders worker
  must update `last_heartbeat_at` on every page transition (not just at
  job start/end), so operational monitoring (in the style of 12B.3I's
  "worker idle and sleeping, not busy-looping/not stuck" verification) can
  correctly distinguish "slowly, correctly progressing because Amazon's
  own bucket is empty" from "genuinely hung." This repository's current
  concurrency control is status-based (the partial unique index), not
  lease-expiry-based reclaim (per the existing documented design decision
  for Listings) — so a slow-but-healthy orders job is not at risk of being
  wrongly reclaimed today, but if a future milestone ever adds
  lease-expiry-based reclaim, its expiry window would have to be
  comfortably longer than 178.6s or it would misfire during ordinary,
  correct operation.
- **Bounded retry.** `429` (bucket exhausted — expected, not an error
  condition to alarm on) and `5xx` (transient server-side failure) should
  use **different backoff curves**: a `429` backoff should assume the
  bucket needs real time to refill (start near the ~178.6s sustained
  interval, not a short generic default), while a transient `5xx` can
  reasonably retry sooner. Both still bounded by `retry_count`/
  `next_retry_at` exactly as Listings already does.
- **Scheduling/fairness.** SP-API usage-plan buckets are per
  (application, authorized-selling-partner) pair, not pooled globally
  across every organization ASI serves — consistent with how this
  repository's existing Listings design already assumes no cross-tenant
  rate-limit pooling. The scarce resource is therefore per seller account,
  not shared across ASI's whole customer base; the practical scheduling
  concern is entirely the intra-account one already covered above (one
  worker, no concurrency, batch participations where the schema allows
  it), not a cross-tenant fairness algorithm.
- **Avoiding duplicate pagination requests.** The existing duplicate-
  trigger-prevention technique (a partial unique index rejecting a second
  concurrent active run for the same scope) directly prevents two
  independent orders jobs from both paginating the same window at the
  same time and wasting the one shared bucket on redundant page-1 fetches.
  A resumed/retried job re-requests from the safe watermark (below), not
  from a specific remembered page — so it may benignly re-fetch a few
  already-seen orders inside the overlap window, which is idempotent and
  cheap, never a duplicate *row*.
- **Checkpoint advancement only after successful traversal.** This was
  already the rule in point 9 above (the durable watermark only advances
  to the highest `lastUpdatedTime` actually committed), but the rate-limit
  numbers make the cost of getting this wrong concrete: a burst-refill
  takes ~59.5 minutes from empty, so a bug that skipped orders by
  advancing the checkpoint prematurely would not just be a correctness
  bug — recovering from it (a full re-scan to catch the skipped orders)
  would itself cost roughly an hour of the account's entire rate-limit
  budget to safely re-earn enough burst/sustained capacity, on top of the
  time to actually re-paginate. This makes watermark correctness
  materially higher-stakes for Orders than it already is for Listings.

## Phase 5 — Proposed schema (no migration, no ORM code)

**Status: PROPOSED / UNIMPLEMENTED.** Nothing in this section exists as an
ORM model, an Alembic migration, or a database table. No table, column,
constraint, or index described below has been created. This is a design
proposal for review, to be implemented (and very possibly revised) in
12B.4B — not a description of current state.

### `amazon_seller_orders` (proposed)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `marketplace_participation_id` | uuid, FK → `amazon_marketplace_participations.id`, `ON DELETE RESTRICT` | Sole ownership path — no `organization_id`/`seller_account_id` column, identical reasoning to `amazon_seller_listings`. |
| `amazon_order_id` | string | Amazon's `orderId`. Immutable once written. |
| `order_aliases` | JSON | `Alias[]` — genuinely variable-length/variable-type list; not worth normalizing. |
| `fulfillment_status` | string | From `Order.fulfillment.fulfillmentStatus`. `CHECK` constraint against the 7 documented enum values. |
| `fulfilled_by` | string, nullable | `MERCHANT`/`AMAZON`. |
| `fulfillment_service_level` | string, nullable | |
| `sales_channel_name` | string | `AMAZON`/`NON_AMAZON`. |
| `sales_channel_marketplace_id` | string, nullable | |
| `sales_channel_marketplace_name` | string, nullable | |
| `programs` | JSON | Variable-length string list (`PRIME`, `AMAZON_BUSINESS`, …) — same `JsonPayload` convention already used for `amazon_seller_listings.status`/`.offers`. |
| `associated_orders` | JSON | `AssociatedOrder[]` — variable, low-volume, not worth normalizing into a child table for the initial slice. |
| `order_total_amount` | `Numeric(14,2)`, nullable | From `proceeds.grandTotal.amount` — normalized because it is the single most frequently queried scalar (dashboards, revenue trend). |
| `order_total_currency` | string(8), nullable | |
| `proceeds_breakdown` | JSON | `OrderProceedsBreakdown[]` (up to 8 documented categories, not every order has every category) — genuinely variable-shape, kept as JSON rather than 8 sparse nullable columns or a child table, matching Phase 5's "JSON only where genuinely variable" guidance. |
| `items_shipped_count` | integer | Denormalized aggregate over `orderItems[].fulfillment.quantityFulfilled`, computed at write time. |
| `items_unshipped_count` | integer | Same, over `quantityUnfulfilled`. |
| `is_business_order` | boolean | Derived from `programs` containing `AMAZON_BUSINESS`. |
| `is_prime` | boolean | Derived from `programs` containing `PRIME`. |
| `is_replacement` | boolean | Derived from `associated_orders[].associationType == REPLACEMENT_ORIGINAL_ID` (or equivalent). |
| `was_cancelled` | boolean | `fulfillment_status == 'CANCELLED'`, denormalized for fast filtering. |
| `amazon_created_at` | `DateTime(timezone=True)` | From `createdTime`. Immutable once written. |
| `amazon_last_updated_at` | `DateTime(timezone=True)` | From `lastUpdatedTime`. Mutable — this *is* the incremental cursor source field. |
| `last_ingestion_run_id` | uuid | **Composite FK** `(last_ingestion_run_id, marketplace_participation_id)` → `amazon_ingestion_runs(id, marketplace_participation_id)`, `ON DELETE RESTRICT` — identical cross-marketplace-safety pattern to `amazon_seller_listings.last_ingestion_run_id`. |
| `first_seen_at` | `DateTime(timezone=True)`, `server_default=func.now()` | Never updated after insert. |
| `last_seen_at` | `DateTime(timezone=True)` | Updated on every successful touch, even a no-op upsert. |

Constraints: `UniqueConstraint(marketplace_participation_id, amazon_order_id)`
(idempotency key); `UniqueConstraint(id, marketplace_participation_id)`
(widens the PK so `amazon_seller_order_items` can hold the same
composite-FK provenance pattern `amazon_seller_listings` already uses
against `amazon_ingestion_runs`); `CheckConstraint` on `fulfillment_status`
enum; indexes on `(marketplace_participation_id, amazon_last_updated_at)`
(cursor queries), `(marketplace_participation_id, fulfillment_status)`,
`(marketplace_participation_id, amazon_order_id)` (redundant with the
unique constraint's implicit index, called out for clarity), and
`last_ingestion_run_id`. **No customer PII column exists anywhere in this
table**, by design, matching Phase 3.

### `amazon_seller_order_items` (proposed)

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `order_id` | uuid | Composite FK `(order_id, marketplace_participation_id)` → `amazon_seller_orders(id, marketplace_participation_id)`, `ON DELETE RESTRICT` — prevents an item ever pointing at an order in a different marketplace participation than its own `marketplace_participation_id` column, same reasoning as the orders→ingestion-runs FK. |
| `marketplace_participation_id` | uuid | Denormalized copy carried for the composite FK and for direct item-level queries without a join; still ultimately owned by `amazon_seller_orders`. |
| `amazon_order_item_id` | string | Amazon's `orderItemId`. |
| `seller_sku` | string(180) | Unconditional field. |
| `asin` | string(10), nullable | Unconditional field, nullable exactly like `amazon_seller_listings.asin`. |
| `item_name` | string(500), nullable | `product.title`. |
| `condition_type` | string(32), nullable | `product.condition.conditionType`. |
| `quantity_ordered` | integer | Required by the contract. |
| `quantity_fulfilled` | integer, nullable | From `FULFILLMENT`. |
| `quantity_unfulfilled` | integer, nullable | From `FULFILLMENT`. |
| `unit_price_amount` | `Numeric(14,2)`, nullable | `product.price.unitPrice.amount`. |
| `unit_price_currency` | string(8), nullable | |
| `item_proceeds_amount` | `Numeric(14,2)`, nullable | `proceeds.proceedsTotal.amount`. |
| `item_proceeds_currency` | string(8), nullable | |
| `proceeds_breakdown` | JSON | `ItemProceedsBreakdown[]` — same variable-category reasoning as the order-level breakdown. |
| `was_cancelled` | boolean | Derived, not free text. |
| `cancel_requester` | string, nullable | `cancellationRequest.requester` (enum: `BUYER`) — **never `cancelReason`**. |
| `cancelled_by` | string, nullable | `cancellationExecution.cancelledBy` (enum: `BUYER`/`MERCHANT`/`AMAZON`) — **never `cancelReason`**. |
| `last_ingestion_run_id` | uuid | Composite FK, identical pattern. |
| `first_seen_at` | `DateTime(timezone=True)` | |
| `last_seen_at` | `DateTime(timezone=True)` | |

Constraints: `UniqueConstraint(order_id, amazon_order_item_id)`
(idempotency key); indexes on `(marketplace_participation_id, asin)`,
`(marketplace_participation_id, seller_sku)`,
`(marketplace_participation_id, was_cancelled)`, `last_ingestion_run_id`.
**No `gift_message`, `cancel_reason`, `customization`, or `serial_numbers`
column exists anywhere in this table**, by design.

### `amazon_ingestion_runs` extension

Add `run_type='orders'` as a third accepted value alongside
`'marketplace_participations'`/`'listings'` in the existing `CHECK`
constraint. **This subsection assumes, provisionally, resolution (a) of
the Phase 4 point 11 open tension** (one orders run per participation,
same as Listings) — under that assumption, add an orders-scoped
equivalent of `uq_amazon_ingestion_runs_active_listings_scope` (same
`(seller_account_id, marketplace_participation_id)` partial-unique-index
shape, filtered to `run_type='orders' AND status IN ('queued','started',
'waiting_to_retry')`) for duplicate-trigger protection, and no other
column changes are needed — `records_received/accepted/rejected`,
`retry_count`, `lease_owner/lease_expires_at`, `next_retry_at`,
`last_heartbeat_at`, `pagination_complete`, `pages_fetched` all already
fit an Orders run without modification. **If 12B.4B instead chooses
resolution (b)** (one run may span multiple participations), this
uniqueness scope would need to drop `marketplace_participation_id` and key
on `seller_account_id` alone, `amazon_ingestion_runs.marketplace_participation_id`
would need to become nullable for `run_type='orders'` (contradicting the
existing `ck_amazon_ingestion_runs_listings_scope_required`-style
constraint's assumption that a scoped run always has one), and the
composite-FK provenance pattern on `amazon_seller_orders`/
`amazon_seller_order_items` (below) would need redesigning. That decision
is deferred to 12B.4B, not made here.

### New: `amazon_orders_sync_cursor` (proposed, dedicated checkpoint table)

Phase 5 explicitly asks whether a dedicated cursor table is necessary —
**yes**, and this is a deliberate divergence from "just reuse
`amazon_ingestion_runs`." The reason: `amazon_ingestion_runs` rows are
one-shot, bounded job-execution records; Orders needs a durable,
continuously-advancing high-water mark that must survive independently of
any single run's success/failure/retry lifecycle, and must only ever
advance to a value *provably* safe (Phase 4 point 9) — overloading a job
table with "the persistent truth of how far we've safely gotten" mixes two
different lifecycles and makes the overlap-window math implicit instead of
explicit.

| Column | Type | Notes |
|---|---|---|
| `marketplace_participation_id` | uuid PK, FK → `amazon_marketplace_participations.id`, `ON DELETE RESTRICT` | One row per participation. |
| `synced_through_at` | `DateTime(timezone=True)`, nullable | The high-water mark; null before the first successful run. |
| `last_successful_run_id` | uuid, nullable | Composite FK → `amazon_ingestion_runs(id, marketplace_participation_id)` for provenance/audit. |
| `updated_at` | `DateTime(timezone=True)` | `func.now()` on every write. |

This table's one-row-per-participation shape is compatible with the
batching idea raised in Phase 4 point 11 even before that tension is
resolved: if a future combined fetch spans multiple participations in one
`searchOrders` call, the orchestrator would request using the **minimum**
`synced_through_at` among the participations being combined (the most
conservative safe starting point for all of them), then update each
participation's own row afterward based only on what was actually
observed for its own orders in the response. The *run-provenance* FK
tension (point 11) is independent of this and remains unresolved.

### Explicit comparisons requested by Phase 5

- **Order totals only vs. item-level amounts:** both — `order_total_amount`
  (fast dashboard queries) *and* `item_proceeds_amount` per item (SKU-level
  revenue analytics, explicitly a Phase 6 goal). Storing only the order
  total would foreclose SKU velocity/revenue-concentration analysis;
  storing only item amounts would make simple order-count/revenue-trend
  queries require a join+sum on every request. Both, normalized at their
  natural level.
- **Normalized monetary columns vs. JSON:** normalized for the two
  single-scalar totals (`order_total_amount`, `item_proceeds_amount` and
  their `unit_price_amount`) that are always exactly one value per
  row and are the actual query targets; JSON for the *breakdown* arrays
  (variable category set, not queried by category individually in the
  initial slice).
- **One current-state row vs. immutable event/history tables:** **one
  current-state row per order/item**, not an event history table, for
  12B.4. Orders are mutable and Amazon's contract gives no changelog/diff
  API — building true history would mean ASI inventing synthetic "change
  events" from successive polls, which is speculative and explicitly out
  of scope. `first_seen_at`/`last_seen_at`/`amazon_last_updated_at`
  preserve enough provenance to know an order changed and when ASI last
  observed it, without claiming to have captured every intermediate
  state.
- **Is history/change tracking required now or deferred?** **Deferred.**
  Nothing in Phase 3's allowed-slice or Phase 6's Copilot-relevance list
  requires reconstructing an order's full status history; if a future
  milestone needs it (e.g. "average time from order to ship"), that is a
  new, separately-reviewed schema increment, not implied by 12B.4.
- **Explicitly not reusing the Listings "snapshot deactivation" model:**
  confirmed throughout Phase 4 point 7 above — there is no
  reconcile-and-deactivate-missing pass for Orders, ever.

## Phase 6 — Copilot/skills relevance (mapping only, no implementation)

No ToolRegistry entry, EvidenceEnvelope claim type, prompt, RAG, embedding,
or Copilot tool is added in 12B.4A. This is a capability map for future
milestones only.

| Capability | Directly from Orders | Needs Listings join | Needs future Inventory | Needs future Finances/fees | Needs Ads (excluded) |
|---|---|---|---|---|---|
| Sales trend analysis (order count/revenue over time) | ✔ (`amazon_created_at`, `proceeds`) | | | | |
| SKU velocity (units/orders per SKU per period) | ✔ (`order_items.seller_sku`, `quantity_ordered`) | | | | |
| Marketplace comparison | ✔ (`marketplace_participation_id` grouping) | | | | |
| Cancellation monitoring | ✔ (`was_cancelled`, `fulfillment_status`) | | | | |
| Fulfillment-channel performance (AMAZON vs MERCHANT, on-time rate) | ✔ (`fulfilled_by`, ship/deliver windows) | | | | |
| Revenue concentration (top SKUs by revenue share) | ✔ (`item_proceeds_amount`) | | | | |
| Product demand signal (units ordered per ASIN) | ✔ (`asin`, `quantity_ordered`) | maybe, for listing title/image context | | | |
| Slow/fast movers | ✔ (velocity over time windows) | | | | |
| Order-status anomalies (spike in `CANCELLED`/`UNFULFILLABLE`) | ✔ | | | | |
| Replenishment signal | partial (demand rate only) | | ✔ (current stock level) | | |
| Profitability inputs (true margin, not just revenue) | partial (`proceeds`, not true net) | ✔ (COGS is seller input, joined via listing/profit engine) | | ✔ (fees, refunds, reimbursements) | |
| Ad-attributed performance | — | — | — | — | out of scope entirely |

## Phase 7 — Sanitized fixture plan (delivered)

16 numbered scenarios, delivered as **18 fixture JSON files** (two
scenarios — pagination and the mutable-order before/after pair — each
require two physical files) + `README.md`, created at
`apps/api/tests/fixtures/sp_api/orders/` (19 files total in that
directory; listed in the directory's own `README.md`, mirroring the
12B.3A/Listings convention exactly). Coverage:
minimal valid order, multi-item order, multi-marketplace page, 2-page
pagination (`nextToken` present then absent), mutable-order before/after
update pair, cancelled order (with the excluded free-text fields present,
to prove they must be dropped), missing-optional-fields/empty-collections,
empty result set, multi-currency monetary values, `429`/`5xx`/`403`/`400`
error envelopes, deliberately-invalid JSON syntax, unknown additive
fields, and a defense-in-depth "every excluded PII field present at once"
fixture. All identifiers are synthetic (`FIXTURE-*`, `B0TESTFIX##`,
`*.invalid` email/URL domains, `902-10000XX-10000XX` order-ID pattern) —
visibly non-production per Phase 7's requirement.

## Phase 8 — Delivery plan

| Stage | Scope | Tests | Migration/backup gate | CI gate | Live-call boundary | Security/privacy gate |
|---|---|---|---|---|---|---|
| **12B.4A** (this report) | Contract pinning, privacy boundary, ingestion design, proposed schema, sanitized fixtures | Fixture JSON validation, PII/secret scan | None (no migration) | None (docs/fixtures only) | Forbidden entirely | Default-no-PII/no-RDT decision recorded and justified |
| **12B.4B** | ORDM/Alembic migration for `amazon_seller_orders`, `amazon_seller_order_items`, `amazon_ingestion_runs.run_type` extension, `amazon_orders_sync_cursor`; single Alembic head maintained | Migration up/down against disposable Postgres; model/migration drift check (existing CI job pattern) | Full backup-before-migration gate, same as `0009`/`0011` | Existing "fresh DB zero-to-head" + "existing DB N→N+1" CI matrix, extended one step | Forbidden (schema only) | Confirm no PII column exists; confirm composite FKs enforce cross-marketplace provenance |
| **12B.4C** | Typed, one-page-per-call Orders SP-API client (`searchOrders`/`getOrder`), request/response DTO parsing, explicit field-level PII redaction (drop `giftOption`, `cancelReason`, `customization`, `serialNumbers` even if present) | Unit tests against this report's fixtures (all 16 scenarios); explicit test asserting fixture 16's PII fields never reach the parsed DTO | None | Existing backend suite | Forbidden — client is exercised only against fixtures/sandbox in CI, never live | Fixture-16-style test is a **required** CI gate, not optional |
| **12B.4D** | Durable ingestion service/worker reusing the `amazon_ingestion_runs`/lease/backoff architecture; cursor read/write against `amazon_orders_sync_cursor`; pagination loop; partial-failure watermark logic | Integration tests with a fake SP-API client (no live calls); duplicate-trigger unique-index test mirroring the existing Listings one | Backup-before-first-run gate on whichever environment first exercises it | Existing suite + new orders-worker tests | **First point where a live call becomes possible, and only under the same explicit `ASI_*_WORKER_ENABLED`-style gate pattern already proven for Listings** — never implicit | Confirm role possession (Phase 3 point 4's open question) resolved *before* any live call authorization |
| **12B.4E** | Read API (`GET` endpoints for orders/items, org-scoped, no write) | API tests, auth/tenancy tests | None | Existing suite | Forbidden (read of already-ingested data only) | Confirm no PII field ever serializable through the API schema |
| **12B.4F** | Seller Data Orders UI (mirrors 12B.3F's Seller Listings UI pattern) | Frontend component tests | None | Existing frontend suite | Forbidden | Confirm no order/item identifier logged client-side beyond what Listings already permits |
| **12B.4G** | Production worker deployment and controlled live verification (mirrors 12B.3I's operational-verification pattern exactly: single authorized worker, read-only pre/post verification, explicit one-click-sync authorization) | Full regression suite green | Fresh verified backup + checksum, same ritual as 12B.3I | All CI green | **The only stage where a live Amazon Orders call is ever authorized**, and only after explicit user sign-off, mirroring 12B.3H→12B.3I's gate exactly | Live-log scan for PII/secret leakage as a hard pass/fail gate, same as 12B.3I's Phase 5 |

Milestone names adjusted only in one respect versus the assignment's own
suggested list: no change was actually needed — `12B.4A`–`12B.4G` as
proposed in the task already matches the repository's existing boundary
conventions (one schema stage, one client stage, one ingestion stage, one
read-API stage, one UI stage, one live-verification stage), so the
suggested sequence is adopted as-is.

## Unresolved questions

1. **Run-scoping vs. rate-limit-efficient batching (Phase 4 point 11).**
   Should one `amazon_ingestion_runs` row for `run_type='orders'` remain
   scoped to exactly one `marketplace_participation_id` (simple, consistent
   with Listings, but potentially wasteful of a very scarce shared budget
   for multi-marketplace sellers), or should it be allowed to span several
   participations in one combined `searchOrders` call (rate-limit-efficient,
   but requires redesigning the composite-FK provenance pattern used
   throughout this proposal)? Not resolved here — deferred to 12B.4B.
2. **Role possession.** Does ASI's production application actually hold
   at least one of the twelve roles that authorize `searchOrders`/
   `getOrder`? Unknown/unverified from this repository; must be confirmed
   in Seller Central's Developer Console before 12B.4D.
3. **Per-seller vs. per-application rate-limit bucket confirmation.** This
   report assumes (consistent with this repository's existing Listings
   design, not reconfirmed via a new Orders-specific primary source) that
   the usage-plan bucket is per (application, authorized selling partner),
   not pooled across every organization ASI serves. Worth an explicit
   primary-source check in 12B.4D if ASI's tenant count grows large enough
   for this assumption's cost to matter.
4. **`EXPENSE`/`PROMOTION` deferral.** This report recommends deferring
   these two `includedData` categories from the first slice as unneeded
   for the stated analytics goals, not because they carry PII risk. Worth
   an explicit product decision (not just an implementation default) before
   12B.4C, in case a near-term Copilot/analytics goal actually needs them.
5. **History/change tracking.** Confirmed deferred (Phase 5), but not
   permanently ruled out — if a future milestone needs order-state history
   (e.g. time-to-ship distributions), that is new scope requiring its own
   review, not an extension quietly bolted onto 12B.4's schema.

## First-live-call authorization gate

Restated explicitly, as its own standalone gate (not only embedded in
Phase 8's table): **no live call to `searchOrders` or `getOrder` may occur
until all of the following are true**, at the start of 12B.4D at the
earliest:

1. This report's proposed schema (Phase 5) has been reviewed and
   implemented via a reviewed 12B.4B migration.
2. The 12B.4C typed client and its field-level redaction have been
   implemented and pass fixture-based tests, including the required
   fixture-16-style PII-never-reaches-the-DTO test.
3. Unresolved question 1 above (run-scoping) has been explicitly decided,
   not defaulted.
4. Unresolved question 2 above (role possession) has been confirmed by the
   user, out-of-band, in Seller Central's Developer Console.
5. The live call is gated by an explicit, single, non-implicit
   authorization mechanism analogous to `ASI_LISTINGS_WORKER_ENABLED` —
   never started automatically by a convenience script, a migration, or a
   default-on config value.
6. Exactly one worker/job may be in flight against a given seller
   account's Orders budget at any time — never more, per the no-concurrency
   rule established above.

## Verification

Re-run in full during this correction pass (not only asserted from the
original pass):

- **Fixture JSON validated:** all 17 non-malformed fixtures (of 18 total
  fixture JSON files) re-parsed cleanly with `python3 -m json.tool`;
  `14_malformed_json.json` re-confirmed invalid by design (documented
  above and in the fixtures' own `README.md`)
  and explicitly excluded from the validation pass by name, not silently
  skipped.
- **Fixture re-audit against this correction's 9-point checklist:** (1)
  every fixture uses `2026-01-01`'s camelCase field shape — grepped for
  v0-style artifacts (`AmazonOrderId`, `PurchaseDate`, a `"Payload"`
  wrapper, `"OrderStatus"`) and found none; (2) shapes were constructed
  directly from the extracted `Order`/`OrderItem`/`ItemProduct`/etc.
  definitions in the pinned model, not guessed; (3) all identifiers are
  visibly synthetic (`FIXTURE-*`, `B0TESTFIX##`, `*.invalid`,
  `902-10000XX-10000XX`); (4) `"buyer"`/`"recipient"` objects appear in
  **exactly one** file, `16_restricted_pii_fields_present.json` — grepped
  and confirmed; (5) that fixture and its README entry both state the
  values must never reach persistence, DTO serialization, or logs; (6) the
  malformed-JSON fixture is labeled in its filename, documented in the
  README, and excluded by name from validation; (7) the pagination-token
  value (`FIXTURE-OPAQUE-PAGINATION-TOKEN-PAGE-2-DO-NOT-PARSE`) is
  visibly synthetic; (8) no fixture is a verbatim copy of Amazon's own
  sandbox scenarios — all are hand-constructed against the schema, per the
  README's explicit statement of that choice; (9) grepped for
  bearer/client_secret/refresh_token/access_token/AKIA-shaped strings
  across every fixture and found none.
- **No production code changed:** confirmed via `git status` — this
  milestone (including this correction pass) touched only
  `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md` and
  `apps/api/tests/fixtures/sp_api/orders/*` (new files only). No file
  under `apps/api/app/`, `apps/web/src/`, `alembic/`, or any existing
  fixture directory was modified; no unrelated file appears in `git status`.
- **No Amazon/Supabase call occurred:** all research (including this
  correction's re-verification of the roles claim) was performed against
  Amazon's public GitHub model repository and public documentation pages
  via `curl`/`WebFetch`/`WebSearch`; no SP-API endpoint, LWA token
  endpoint, or Supabase connection was contacted at any point.
- **No application role, credential, or `.env` file was touched.**
- **Repository remains on the new branch:** `milestone-12b4a-orders-contract`,
  created from verified `main` (`89fa20b`).
- **Not run, and not needed:** the backend/frontend automated test suites
  — no executable code changed in this milestone, so running `pytest`/
  `npm test` would exercise nothing related to this work; they were
  deliberately not run, per this phase's own "do not run unnecessary full
  suites" instruction.

`12B.4A ORDERS CONTRACT AND PRIVACY BOUNDARY PINNED — SCHEMA REVIEW REQUIRED`

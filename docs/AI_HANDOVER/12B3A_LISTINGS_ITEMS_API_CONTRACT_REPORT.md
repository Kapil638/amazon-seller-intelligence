# 12B.3A — searchListingsItems Official Contract Report

Durable record of the 12B.3A contract-validation pass. Planning/validation
only — no production code, migration, or live Amazon call was made while
producing this report.

## Pinned source

- Source: `https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/listings-items-api-model/listingsItems_2021-08-01.json`
- Pinned commit: `94219a3fd0b9ee9c319ce06bac293146440aa927` (2026-08-19T03:52:33Z)
- Spec type: Swagger 2.0
- Operation: `searchListingsItems`, `GET /listings/2021-08-01/items/{sellerId}`

Fetched directly via `curl` + Python `json` parsing against Amazon's own
public model repository (primary source), not an AI-summarized fetch of the
documentation site — an earlier `WebFetch` pass against the large raw JSON
file mis-described `relationships` as a single object rather than an array;
the direct parse corrected this.

## Fixtures

Sanitized, schema-derived fixtures (no real seller data) live at
`apps/api/tests/fixtures/sp_api/listings/` — see that directory's own
`README.md` for the full list and per-file rationale. Summary:

| File | Scenario |
|---|---|
| `01_normal_listing.json` | One complete, healthy listing. |
| `02_multiple_listings_one_page.json` | Three listings, single final page. |
| `03_pagination_page_{1,2,3}_of_3.json` | 3-page sequence; page 3 omits `nextToken` (the documented end-of-pagination signal). |
| `04_empty_result.json` | Structurally valid, zero-item response. |
| `05_listing_without_asin.json` | Draft listing, `summaries[].asin` omitted. |
| `06_listing_with_issues.json` | `ERROR`/`WARNING`/`INFO` issues, cross-marketplace `marketplaceIds`, `enforcements`/`exemption`. |
| `07_fba_and_merchant_fulfilled.json` | Two `fulfillmentAvailability` entries, different channel codes. |
| `08_variation_relationship.json` | Parent/child SKU pair, array-shaped `relationships`. |
| `09_malformed_item.json` | One valid item + one item missing the only required field (`sku`). |
| `10_rate_limited_error.json` | `429` `ErrorList` body; no `Retry-After` header documented. |
| `11_mid_pagination_failure.json` | `500` `ErrorList` body for page 2 of 3, after page 1 succeeded. |

## Parameters (exact names/cardinality)

| Name | In | Required | Cardinality |
|---|---|---|---|
| `sellerId` | path | yes | single string |
| `marketplaceIds` | query | yes | array, csv (ASI restricts to exactly one per request by product decision, not Amazon constraint) |
| `issueLocale` | query | no | single string |
| `includedData` | query | no | array, csv, default `["summaries"]` |
| `identifiers` | query | no | array, csv, `maxItems: 20` |
| `identifiersType` | query | conditionally required with `identifiers` | enum `ASIN, EAN, FNSKU, GTIN, ISBN, JAN, MINSAN, SKU, UPC` |
| `variationParentSku` | query | no | single string |
| `packageHierarchySku` | query | no | single string |
| `pageSize` | query | no | integer, max 20, default 10 |
| `pageToken` | query | no | single string |
| `sortBy` | query | no | enum `sku, createdDate, lastUpdatedDate`, default `lastUpdatedDate` |
| `sortOrder` | query | no | enum `ASC, DESC`, default `DESC` |

**Seller ID source:** the `sellerId` path parameter is a plain string; Amazon
does not derive it from the token or request context. Per the existing
12B.1D fail-closed identity design, ASI must supply the stored,
OAuth-captured `amazon_connections.selling_partner_id`, never a value from
any API response.

## `includedData` enum (exact)

`summaries, attributes, issues, offers, fulfillmentAvailability, procurement, relationships, productTypes`. Default `["summaries"]`.

**Minimal set recommended for the first slice:** `summaries, issues, offers, fulfillmentAvailability, productTypes`. Deferred: `relationships` (variation graph reconciliation adds complexity not needed yet, even though the contract cleanly supports it), `attributes` (`additionalProperties: true` — Amazon gives no schema to validate against), `procurement` (vendor-only, out of ASI's seller-central-only scope).

## Pagination

- `ItemSearchResults.pagination` = `{ nextToken?: string, previousToken?: string }`.
- Request token: `pageToken` (either direction).
- **End-of-pagination signal (verbatim):** "When you receive the last page, there is no `nextToken` key in the pagination object." Absence of the key, not an empty string.
- **Hard ceiling (documented, verbatim):** "the maximum number of items (SKUs) that can be returned and paged through is 1000." `numberOfResults` reports the true total match count, which can exceed 1000 even though only 1000 are retrievable — the natural "no nextToken" signal fires at the 1000-item cutoff regardless. A future reconciliation service must treat pagination as incomplete for deactivation purposes whenever `reported_total_results > 1000` and `records_received` did not reach that count, even though the API's own end-of-pagination signal fired.

## Rate limits / retry

- Documented usage plan: 5 requests/second, burst 5 (default).
- `x-amzn-RateLimit-Limit` — actual applied rate limit, on 200 responses.
- `x-amzn-RequestId` — on success and every documented error response, including 429.
- **No `Retry-After` header is documented anywhere on this operation.** Backoff timing must come from ASI's own client-side throttling.

## Error responses

`400, 403, 404, 413, 415, 429, 500, 503` all return `ErrorList = { errors: Error[] }`, `Error = { code: string, message: string, details?: string }` (`code`/`message` required).

## Response item shape (`Item`)

Required: `sku` only. Optional: `summaries`, `attributes`, `issues`, `offers`, `fulfillmentAvailability` (array), `procurement` (array), `relationships`, `productTypes`.

- **SKU:** `item.sku` (top-level, required).
- **ASIN:** `item.summaries[].asin` (optional — omitted on draft/pre-ASIN listings).

## `summaries` (`ItemSummaryByMarketplace`)

Required: `marketplaceId, productType, status, createdDate, lastUpdatedDate`. Optional: `asin, conditionType, fnSku, itemName, mainImage`.
- `status` enum is **only** `BUYABLE, DISCOVERABLE` — and can be an empty array.
- `conditionType` has 13 documented values (`new_new` … `club_club`).

## `issues` (`Issue`)

Required: `code, message, severity, categories`. Optional: `attributeNames[]`, `enforcements`, `marketplaceIds[]` (plural — one issue can span multiple stores).
- `severity` enum: `ERROR, WARNING, INFO`.
- `enforcements.actions[]` and `enforcements.exemption` are both required when `enforcements` is present.

## `offers` (`ItemOfferByMarketplace`)

Required: `marketplaceId, offerType, price`. Optional: `points`, `audience`. `offerType` enum: `B2C, B2B`.

## `fulfillmentAvailability`

`{ fulfillmentChannelCode: string (required, no enum — confirmed unconstrained), quantity?: integer >= 0 }`. No `marketplaceId` field on this object.

## `productTypes`

Array of `{ marketplaceId: string, productType: string }` pairs.

## `relationships` (confirms and corrects an earlier AI-summarized error)

`item.relationships` is an **array** of `ItemRelationshipsByMarketplace = { marketplaceId (required), relationships: ItemRelationship[] (required) }`. `ItemRelationship = { type: "VARIATION"|"PACKAGE_HIERARCHY" (required), childSkus?[], parentSkus?[], variationTheme? }`. Not a single object.

## `attributes`

`{ type: object, additionalProperties: true }` — fully unconstrained.

## Milestone sequencing (unchanged from the accepted plan)

12B.3A (this report) → 12B.3B (schema/migration/models) → 12B.3C (SP-API client/pagination) → 12B.3D (reconciliation/run serialization) → 12B.3E (read API) → 12B.3F (Seller Data UI) → 12B.3G (explicit live-sync trigger) → 12B.3H (ASIN Analyzer seller-owned panel).

`12B.3A OFFICIAL LISTINGS CONTRACT PINNED — SCHEMA REVIEW REQUIRED`

# searchListingsItems fixtures — 12B.3A

All fixtures in this directory are shaped against the **official** SP-API
Listings Items API v2021-08-01 model, fetched directly from the primary
source (not summarized, not guessed):

- Source: `https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/listings-items-api-model/listingsItems_2021-08-01.json`
- Pinned commit: `94219a3fd0b9ee9c319ce06bac293146440aa927` (2026-08-19T03:52:33Z)
- Operation: `searchListingsItems`, `GET /listings/2021-08-01/items/{sellerId}`

No real seller data. All SKUs, ASINs, and identifiers are synthetic. These
are data fixtures only — no application code reads them yet (12B.3A is a
planning/validation pass; parsing code is 12B.3C).

| File | Scenario |
|---|---|
| `01_normal_listing.json` | One complete, healthy listing (single item, single marketplace, no issues). |
| `02_multiple_listings_one_page.json` | Three listings on a single (final) page. |
| `03_pagination_page_1_of_3.json` / `_2_of_3` / `_3_of_3` | A 3-page sequence; page 3 omits `nextToken` entirely (the documented end-of-pagination signal). |
| `04_empty_result.json` | A structurally valid, zero-item response (`numberOfResults: 0`). |
| `05_listing_without_asin.json` | A listing whose `summaries` entry omits the optional `asin` field. |
| `06_listing_with_issues.json` | Multiple issues spanning `ERROR`/`WARNING`/`INFO` severity, cross-marketplace `marketplaceIds`, and an `enforcements`/`exemption` block. |
| `07_fba_and_merchant_fulfilled.json` | Two `fulfillmentAvailability` entries with different `fulfillmentChannelCode` values. |
| `08_variation_relationship.json` | A variation parent and one of its children, both carrying `relationships[].relationships[].type == "VARIATION"`. |
| `09_malformed_item.json` | One structurally invalid item (missing the only required `Item` field, `sku`) alongside one valid item, to exercise partial-malformation handling. |
| `10_rate_limited_error.json` | A `429` `ErrorList` body (schema-accurate; HTTP status/headers are set by the test harness, not the fixture). |
| `11_mid_pagination_failure.json` | A `500` `ErrorList` body representing page 2 of a 3-page sequence failing after page 1 succeeded. |

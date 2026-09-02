# Orders API fixtures — 12B.4A

All fixtures in this directory are shaped against the **official**, current
(non-deprecated) SP-API Orders API model, fetched directly from the primary
source (not summarized, not guessed):

- Source: `https://raw.githubusercontent.com/amzn/selling-partner-api-models/main/models/orders-api-model/orders_2026-01-01.json`
- Pinned commit: `96d516badc8d69a566a4160e3c7b315600e043a7` (2026-07-29T17:11:19Z)
- Operations: `searchOrders` (`GET /orders/2026-01-01/orders`), `getOrder` (`GET /orders/2026-01-01/orders/{orderId}`)

See `docs/AI_HANDOVER/12B4A_ORDERS_API_CONTRACT_REPORT.md` for the full
contract report, including three additional **official Amazon sandbox**
static-scenario responses (embedded in the model's `x-amzn-api-sandbox`
extension) that were used to cross-check field shapes — not copied verbatim
here, since Amazon's own sandbox examples use real-looking synthetic PII
(fake names/addresses) whereas every fixture in this directory uses
obviously-fake `FIXTURE-*` / `*.invalid` placeholders instead.

No real seller, buyer, or order data. All order IDs, ASINs, SKUs, names,
addresses, and emails are synthetic and visibly fake. These are data
fixtures only — no application code reads them yet (12B.4A is a
research/contract/privacy-boundary pass; the typed client and parser are
12B.4C).

| File | Scenario |
|---|---|
| `01_minimal_valid_order.json` | One order using only the schema's *required* fields (`orderId`, `createdTime`, `lastUpdatedTime`, `salesChannel`, `orderItems`). |
| `02_order_multiple_items.json` | One order, two items, `proceeds`/`fulfillment` populated at both order and item level. |
| `03_multiple_marketplaces.json` | Two orders in one page, in two different marketplaces (`ATVPDKIKX0DER`=US, `A2EUQ1WTGCTBG2`=CA) — proves ASI must scope every row by `marketplace_participation_id`, never assume one marketplace per seller. |
| `04_pagination_page_1_of_2.json` / `04_pagination_page_2_of_2.json` | A 2-page sequence; page 1 carries `pagination.nextToken`, page 2 omits `pagination` entirely (the documented end-of-pagination signal — note the *response* field is `pagination.nextToken` while the *request* field is `paginationToken`, different names). |
| `05_order_before_update.json` / `05_order_after_update.json` | The same `orderId`, re-fetched later: `lastUpdatedTime` advances, `fulfillmentStatus` moves `UNSHIPPED`→`SHIPPED`, item `quantityFulfilled` moves `0`→`2` — proves orders are mutable and must be upserted, not treated as immutable events. |
| `06_cancelled_order.json` | Order-level `fulfillmentStatus: CANCELLED` plus item-level `cancellation.cancellationRequest`/`cancellationExecution`, including the free-text `cancelReason` fields that 12B.4's privacy boundary requires ASI to discard (only `requester`/`cancelledBy` may be persisted). |
| `07_missing_optional_fields.json` | Minimal order with empty optional arrays (`programs: []`, `associatedOrders: []`, `orderAliases: []`), no `marketplaceId` on `salesChannel`, no `asin` on the item's `product` — exercises "optional means may be absent, not merely empty." |
| `08_empty_result.json` | Structurally valid, zero-order response (`{"orders": []}`). |
| `09_monetary_values_multi_currency.json` | Two orders in non-USD currencies (JPY — zero-decimal-styled integer amount as a string; EUR), exercising `Money.amount`/`Money.currencyCode` handling across currencies. |
| `10_throttling_429.json` | A `429` `ErrorList` body (schema-accurate; HTTP status/headers are set by the test harness, not the fixture — no `Retry-After` header is documented for this operation). |
| `11_transient_5xx.json` | A `500` `ErrorList` body. |
| `12_authentication_failure_403.json` | A `403` `ErrorList` body. |
| `13_invalid_request_400.json` | A `400` `ErrorList` body illustrating the documented mutual-exclusivity rule between `createdAfter`/`createdBefore` and `lastUpdatedAfter`/`lastUpdatedBefore`. |
| `14_malformed_json.json` | **Deliberately invalid JSON syntax** (missing commas, truncated). Exists to exercise raw-response parse-failure handling, not schema validation. **This file will fail a JSON-syntax check by design** — the verification pass for this milestone explicitly excludes it by name for that reason; it must never be excluded silently. |
| `15_unknown_additive_fields.json` | A structurally valid, otherwise-normal order carrying extra fields not present in the pinned model (`futureFieldNotYetDocumented`, `anotherFutureAttribute`) — proves forward-compatible parsing must ignore unrecognized fields rather than fail closed. |
| `16_restricted_pii_fields_present.json` | A structurally valid order populated with `buyer`, `recipient`, `payment`, `tax`, item `product.customization`, and item `fulfillment.packing.giftOption` — every field this milestone's privacy boundary excludes. This shape must never occur in real ASI traffic, since 12B.4 never requests `includedData=BUYER/RECIPIENT/PAYMENT/TAX`; it exists as a defense-in-depth test proving the future parser drops these fields unpersisted and unlogged even if Amazon ever returned them unexpectedly. All names/addresses/emails are synthetic (`FIXTURE-*` / `*.invalid`), not real people. |

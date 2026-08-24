# 10 — Provider and Data Source Boundaries

ADR 0002 is the product rule. Do not merge providers.

## Rainforest

- Public marketplace ASIN lookup and Amazon search discovery
- Default `PRODUCT_PROVIDER=rainforest`
- Keys stay on FastAPI
- Must remain in the product. SP-API does not replace it.

## Amazon SP-API

- Seller-owned operational intelligence after the seller authorizes ASI
- Today: connection + validation handshake only
- Canonical domain model is not the SP-API JSON (ADR 0003)
- Provenance must name the source (ADR 0004)

## Amazon Ads API

- Future seller-owned advertising **data**
- Milestone **12C**
- Advertising Intelligence **math** (`ads-calc-v1`) already exists from seller-entered spend (11C.2). That is not Ads API.

## Seller input

- COGS, fees assumptions, advertising spend entered on Profit worksheets
- Unknown stays unknown (no LLM fill-in)

## Marketplace default vs seller participation

`DEFAULT_MARKETPLACE=amazon.in` is ASI listing/UI default.

A connected seller may participate on Amazon.com or other marketplaces.

Do not treat `amazon.in` as the seller’s canonical marketplace. That identity belongs to 12B.2.

## Comparison work (future)

After 12B.2 identity + 12B.3 listing adapter:

Rainforest marketplace ASIN view **versus** SP-API seller-owned listing view.

Not a migration off Rainforest.

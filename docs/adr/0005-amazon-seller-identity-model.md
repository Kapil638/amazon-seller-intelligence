# ADR 0005 — Amazon Seller Identity Model

**Status:** Accepted  
**Date:** 23 August 2026  
**Milestone:** 12B — SP-API Seller Data Adapter & Canonical Data Model Architecture  
**Deciders:** Principal architecture review ([12B architecture](../milestone-12/milestone-12b-sp-api-data-backbone-architecture.md))

---

## Title

Amazon Seller Identity Model

---

## Context

Listing Intelligence, History, and Profit V1 key many records by **ASIN + marketplace** (plus organization). That is correct for marketplace lookup and for a unit economics worksheet.

Seller-owned Amazon operations are keyed by **seller SKU**. A seller may have multiple SKUs on one ASIN, the same ASIN in other marketplaces, and FNSKU as a warehouse identifier. Using ASIN as the seller-listing primary key would collide those facts and corrupt inventory, orders, and listing observations.

---

## Decision

ASIN alone is **not** seller-listing identity.

Canonical seller-owned listing identity is:

```text
organization_id + seller_account_id + marketplace_id + seller_sku
```

ASIN is **linked catalog identity** (marketplace-scoped), used to join Rainforest, profit models, and future ads collection.

FNSKU, when present, is an additional warehouse attribute, not the listing primary key.

Same SKU string in two marketplaces is two listings.

Profit Intelligence V1 may remain **ASIN-scoped** (`organization_id + asin + marketplace`). Connecting SKU-level Amazon data to that worksheet is a **projection** problem: multiple SKUs on one ASIN must be surfaced, not silently reduced.

Every seller-owned canonical record is `organization_id` scoped. Other-org reads remain inaccessible.

---

## Consequences

**Positive**

- Orders, inventory, and listings can ingest without ASIN collisions.
- Marketplace expansion does not reuse India-implicit identity.

**Negative / accepted**

- Join to `profit_models` is not 1:1 with `seller_products`.
- Unresolved SKUs on orders are allowed until catalog sync catches up; do not auto-invent product rows in the first ingest slices.

---

## Not in this decision

Multi-account uniqueness beyond V1’s expected one production SP-API connection per organization, or Ads advertiser profile mapping.

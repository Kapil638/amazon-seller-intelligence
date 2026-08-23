# ADR 0004 — Seller Data Provenance and Source Precedence

**Status:** Accepted  
**Date:** 23 August 2026  
**Milestone:** 12B — SP-API Seller Data Adapter & Canonical Data Model Architecture  
**Deciders:** Principal architecture review ([12B architecture](../milestone-12/milestone-12b-sp-api-data-backbone-architecture.md))  
**Related:** [ADR 0002](0002-amazon-data-provider-separation.md)

---

## Title

Seller Data Provenance and Source Precedence

---

## Context

ASI will hold facts from SP-API, future Ads API, Rainforest, seller uploads, and seller-entered private inputs (COGS, modeled fees). Those systems use different grains and different meanings of “sales,” “fees,” and “listing.”

Silently merging them (averaging, overwriting, or a generic `revenue` field) would make EvidenceEnvelope citations dishonest and would let Copilot treat incompatible numbers as one truth.

---

## Decision

SP-API, Ads API, Rainforest, seller uploads, and seller inputs remain **distinct sources** with **explicit per-domain source-of-truth rules**.

Every canonical record or observation carries provenance sufficient to answer who owns it, which system produced it, which operation/report, which source record, and when (occurred / observed / period / ingested).

High-level authority:

| Domain | Authority |
| --- | --- |
| Marketplace / competitor listing | Rainforest |
| Seller-owned listing SKU state | SP-API Listings Items |
| Order entities | Orders API |
| Period business metrics | Reports API, as **named** metrics |
| Posted settlement / fee events | Finances API |
| Advertising collection | Ads API (future) |
| COGS and internal costs | Seller input only |
| Modeled unit fees | Seller input unless the seller explicitly selects observed fees |

Conflicts are **surfaced**, never averaged. Ordered sales, shipped proceeds, and ad-attributed sales are different metrics.

Evidence claims must retain `source` and time context (`as_of` or period). EvidenceEnvelope schema is unchanged.

---

## Consequences

**Positive**

- Copilot can cite the real source.
- Uploads and APIs can coexist.
- Profit can show modeled vs observed fees without rewriting `profit-calc-v1`.

**Negative / accepted**

- UI and projections must carry source labels.
- More completeness/unknown states, fewer blended KPIs.

---

## Not in this decision

Numeric freshness SLOs, exact report metric catalog, or Ads API implementation.

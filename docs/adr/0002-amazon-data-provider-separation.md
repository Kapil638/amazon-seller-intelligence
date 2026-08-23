# ADR 0002 — Amazon Data Provider Separation

**Status:** Accepted  
**Date:** 23 August 2026  
**Milestone:** 12A.1 — Amazon Connection Beta Foundation  
**Deciders:** Product architecture freeze for Amazon-connected ASI

---

## Title

Amazon Data Provider Separation

---

## Context

ASI already uses Rainforest for marketplace listing intelligence. Milestone 12 introduces Amazon SP-API for seller-owned data, and later Amazon Ads API for advertising collection.

A generic “Amazon provider” would hide source differences and make it easy to replace Rainforest with SP-API, or to mix public marketplace facts with seller-private facts. Those are different trust boundaries, different authorization models, and different product jobs.

ASI must understand both:

- the marketplace around the seller
- the seller’s own Amazon business

---

## Decision

Rainforest remains the marketplace intelligence provider.

SP-API becomes the seller-owned intelligence provider.

Ads API becomes the advertising intelligence collection provider.

They are complementary sources. They must not be merged.

Consequences of this decision:

- Do not replace Rainforest with SP-API.
- Do not create a single generic Amazon provider that hides source differences.
- Listing Intelligence continues to use Rainforest (and mock / manual input).
- SP-API code lives in `app.amazon`, isolated from listing, profit, advertising, Copilot, and Skills.
- Advertising Intelligence (`ads-calc-v1`) stays a Python engine. Ads API later replaces collection only.
- Connection Beta may show Ads API as `NOT_CONNECTED` without implementing it.

Future architecture:

```text
Marketplace Intelligence:  Rainforest
Seller Intelligence:       SP-API
Advertising Intelligence:  Amazon Ads API (collection) + ads-calc-v1 (engine)
```

---

## Consequences

**Positive**

- Public marketplace data and seller-private data keep distinct provenance.
- Copilot and Skills can later cite the correct source instead of an opaque Amazon blend.
- Advertising collection can change without rewriting profit or marketplace intelligence.

**Negative / accepted**

- More integration surfaces to maintain.
- Operators must understand that a green SP-API connection does not mean Rainforest is connected, and vice versa.

---

## Not in this decision

Production OAuth, multi-account onboarding, Amazon data ingest, canonical seller data models, Ads API implementation, Copilot tools for SP-API, or Skill implementation.

# ADR 0003 — Canonical Amazon Seller Data Model

**Status:** Accepted  
**Date:** 23 August 2026  
**Milestone:** 12B — SP-API Seller Data Adapter & Canonical Data Model Architecture  
**Deciders:** Principal architecture review ([12B architecture](../milestone-12/milestone-12b-sp-api-data-backbone-architecture.md))

---

## Title

Canonical Amazon Seller Data Model

---

## Context

SP-API returns Amazon-shaped JSON. ASI already has domain models for listings, profit, advertising, tools, and Copilot. If those layers consume SP-API payloads directly, Amazon’s contract becomes ASI’s contract, Copilot can reason over raw JSON, and engine versions (`profit-calc-v1`, listing scores, `ads-calc-v1`) get coupled to provider field names.

12A.0 already treats Sellers v1 models as provider DTOs. That pattern must hold for all future SP-API families.

---

## Decision

SP-API payloads are **provider DTOs**. They must be normalized into **ASI canonical seller entities** before business intelligence, ToolRegistry, EvidenceEnvelope, Copilot, or Skills consume them.

Required layering:

```text
Amazon payload → SP-API DTO → adapter → canonical entity → domain projection → engine
```

Consequences:

- DTOs mirror Amazon contracts (`extra=ignore`, aliases, no formulas).
- Canonical entities are organization-scoped ASI records (accounts, marketplaces, seller products, orders, inventory observations, financial events, business metric observations).
- Intelligence engines are not rewritten to parse Amazon JSON.
- Copilot and Skills never call SP-API or ingest raw provider payloads.
- Rainforest marketplace snapshots remain a separate source (ADR 0002).

---

## Consequences

**Positive**

- Amazon schema changes are absorbed in DTOs/adapters.
- Provenance and identity can be enforced once.
- Existing engines stay calculation owners.

**Negative / accepted**

- More mapping code than “store the JSON.”
- Adapters must be tested against Amazon contract fixtures, not live Copilot prompts.

---

## Not in this decision

Table migrations, OAuth, Ads API, which SP-API family ships first, or Skill implementation.

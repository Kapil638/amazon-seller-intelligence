# 16 — Next Roadmap

Maintain milestone numbering. **Do not rename the next ingest stage to 12C.**

## Immediate next (approved)

**12B.2 — Canonical Seller Identity + Marketplace Ingestion**

Expected scope:

- seller account identity
- marketplace participation normalization
- canonical marketplace rows
- provenance (ADR 0003–0005)

Out of scope for 12B.2:

- listings/orders/inventory/reports/finances ingest
- Ads API
- Copilot/Skills changes
- replacing Rainforest
- production SecretProvider unless explicitly required as a blocker

## Then

```text
12B.3  Listings / Seller Product Adapter
         + controlled Rainforest vs SP-API ASIN comparison
12B.4  Orders
12B.5  Inventory
12B.6  Reports / business metrics
12B.7  Financial events
12B.8  Provenance / projection hardening
12B.9  Connect stable seller-data tools to intelligence/Copilot
12C    Ads API integration
```

Later: richer Copilot orchestration, Skills, seller business workflows.

## First Claude action (before any 12B.2 code)

Read `CLAUDE.md`, this package, ADRs, and `docs/milestone-12/`. Inspect code. Produce an architecture validation report confirming 12B.2 is next unless the repo reveals a blocker.

Do not implement immediately.

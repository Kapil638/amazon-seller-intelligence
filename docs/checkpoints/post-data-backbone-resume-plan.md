# Post–Data Backbone Resume Plan

**Created:** 22 August 2026  
**Depends on:** [pre-amazon-api-data-backbone-checkpoint.md](pre-amazon-api-data-backbone-checkpoint.md)  
**Architecture:** [Milestone 11D Skill Architecture](../milestone-11d-architecture.md)  
**Status:** Handoff only. **Do not execute these steps as part of the pre-API checkpoint.**

Skill implementation is intentionally paused at Milestone 11D.1 while ASI builds the Amazon-connected data backbone. This document is what to do **after** SP-API / Ads API and normalized data foundations are mature, before writing Skill Registry or the first Skill.

---

## Purpose

Connected Amazon data must land **behind the frozen stack**:

```text
SP-API / Ads API / Rainforest / uploads / seller COGS
  → canonical normalized model (with source + freshness)
  → deterministic engines
  → ToolRegistry
  → EvidenceEnvelope
  → Copilot
```

Do not let raw Amazon payloads become Copilot claims. Do not put formulas in Skills. Do not grant `confirmed=True` from a model. Do not perform Amazon writes from Copilot or Skills.

---

## 1. Verify API ingestion is stable

- SP-API and Ads API sync jobs complete without silent partial writes.
- Failures are visible (unknown / incomplete), not zero-filled.
- Re-runs are idempotent; they do not mutate historical snapshots.
- Rate limits and confirmation policy for paid/provider calls are explicit.

## 2. Verify canonical normalized data is available

- Seller-owned operational data (SP-API) and advertising data (Ads API) map into ASI models, not ad-hoc JSON in Copilot.
- Rainforest remains the **external marketplace / competitor** source; do not collapse it into seller-account data.
- Uploads remain a first-class fallback; do not delete them because APIs exist.
- Seller-entered COGS and internal costs remain seller-owned. Amazon will not supply COGS.

## 3. Verify source freshness and provenance

Every used field should be attributable:

- source system (SP-API, Ads API, Rainforest, upload, seller input, snapshot)
- as-of / period
- organization_id

Stale profit vs ads grain (unit vs period) must stay labeled. Do not present them as matching monthly books.

## 4. Verify EvidenceEnvelope adapters for connected data

- New facts enter Copilot only as claims on an `EvidenceEnvelope`.
- Kinds stay honest: `observed` / `historical` / `seller_provided` / `calculated` / `unknown`.
- Connected money and scores are never `ai_inference`.
- Missing catalog, spend, or total sales stay **unknown**, never estimated by the LLM.

## 5. Verify organization isolation

- Tokens, reports, and snapshots are scoped to the current organization.
- Other-org identifiers return not found (404 at HTTP boundaries).
- Future OAuth accounts cannot leak across orgs.

## 6. Verify historical snapshots

- `profit_snapshots` and `advertising_snapshots` remain immutable.
- Ingestion creates **new** snapshots; it does not rewrite old rows.
- History-first listing lookup still prefers saved complete reports over live Amazon lookup unless refresh is requested.

## 7. Verify Listing / Profit / Advertising tools consume normalized data correctly

Existing tools must keep their contracts:

| Tool | Must still |
| --- | --- |
| `get_profit_snapshot` | Read latest snapshot; no recalculation in the tool |
| `analyze_profitability` | Call `ProfitModelingService` / `profit-calc-v1` only |
| `get_advertising_snapshot` | Read-only; no worksheet create; no ads-calc in the tool |
| `analyze_advertising_impact` | Compose via `AdvertisingImpactService` |
| Listing tools | History-first; no product blob; scores from Listing Intelligence V2 |

Do not modify ToolRegistry.execute semantics or EvidenceEnvelope fields unless a new ADR says so.

ADR 0001 still applies: HIGH_ACOS from Seller Reports is not a P&L verdict. Ads must not rewrite `profit-calc-v1`.

## 8. Run full regression

- `cd apps/api && uv run pytest`
- `cd apps/web && npm test`
- Confirm Copilot plan → execute → synthesize still works with mock/offline fixtures.
- Confirm no Amazon writes in any new path.

Freeze baseline at this checkpoint: **472** backend tests, **20** frontend tests (22 August 2026). Counts may grow; they must not lose isolation, unknown-handling, or citation tests.

## 9. Resume Skill layer from approved Milestone 11D architecture

Only after steps 1–8:

- Implement Skill definitions as versioned code/config (not a runtime CMS for V1).
- Skills name tools; they do not execute them.
- Skills contain no formulas and mint no claims.
- No LangGraph, CrewAI, or autonomous agents unless a later ADR explicitly accepts that risk.

Read `docs/milestone-11d-architecture.md` as the Skill contract.

## 10. Choose first production Skill

Candidates (not chosen here):

- Listing Optimization (tools already existed before 11D.1)
- Profit Improvement (needs connected + seller COGS evidence quality)
- Advertising Optimization (needs Ads API provenance; still no bid writes in V1)
- Business Diagnostic (compose existing evidence only)

Pick one Skill. Do not implement the catalog in parallel.

---

## Explicit non-goals of this handoff

- Do not start SP-API or Ads API work from this file (that is a separate architecture milestone).
- Do not implement Skills until the data backbone checks above pass.
- Do not introduce Amazon write tools in the first Skill V1.

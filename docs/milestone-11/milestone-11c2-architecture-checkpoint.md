# Milestone 11C.2 — Architecture Validation Review

**Date:** 22 August 2026  
**Role:** Principal Architect checkpoint  
**Status:** **APPROVED — proceed**  
**Scope:** Architecture only. No code. No migrations. No Skill implementation.  
**Reviewed against:** [milestone-11c2-architecture.md](../milestone-11c2-architecture.md), [milestone-11c-architecture.md](../milestone-11c-architecture.md), [milestone-11c1-profit-foundation.md](milestone-11c1-profit-foundation.md), Milestone 11A ToolRegistry + EvidenceEnvelope, Milestone 11B Copilot V1, Amazon Skills Operating Playbook (19 August 2026) as **future product strategy only**.

This document is the architecture go-ahead record for Advertising Intelligence Foundation. It does not implement 11C.2 and does not create Skills, agents, LangGraph, or CrewAI.

---

## Architecture Status

**APPROVED**

Milestone 11C.2 fits the existing ASI stack and the future Skill Playbook. It adds a deterministic **Advertising Intelligence engine**, not a PPC product, not a Copilot change, and not a Skill.

The Skill Playbook remains **strategy only**. It describes business capabilities (listing, PPC optimization, TACOS/budget, reporting) that should later sit **above** ToolRegistry. This milestone must not create Skills, agents, LangGraph, or CrewAI.

Intended future layering (not this milestone):

```text
Seller Goal
  ↓
Skill Layer                 (business capabilities — later)
  ↓
Tool Registry               (technical capabilities — 11A, shipped)
  ↓
Deterministic engines       (listing, profit, advertising)
  ↓
EvidenceEnvelope
  ↓
Seller Copilot explanation
```

**Skills = business capabilities. Tools = technical capabilities.** A future Advertising Optimization Skill may use advertising, profit, and listing tools. 11C.2 only builds the advertising foundation those tools will wrap.

---

## Confirmed Frozen Decisions

These decisions must remain unchanged.

1. **Python owns money.** `ads-calc-v1` owns ACOS, TACOS, and ROAS. AI does not calculate, estimate, or invent spend.
2. **AI owns explanation only**, and only after evidence exists (11C.4). Copilot V1 stays unchanged in 11C.2.
3. **EvidenceEnvelope is the trust boundary.** Flow: inputs → calculation → snapshot → envelope → (later) Copilot. No `AdvertisingEvidenceEnvelope`.
4. **No Amazon writes.** No campaign, listing, or bid changes. Future recommendations need seller confirmation.
5. **Domains stay separate.** Listing = construction. Seller Reports = operational PPC diagnostics. Profit = unit economics before ads. Advertising = period economics. Copilot = orchestration/explanation. Skills = future business capabilities above tools.
6. **`profit-calc-v1` is unchanged.** Advertising is **not** folded into `other_cost`. After-ads is a **composition layer**.
7. **Advertising is period-based; profit V1 is unit-based.** Sibling tables: `advertising_models` + immutable `advertising_snapshots`. Not ads columns on `profit_models`.
8. **Unknown stays unknown.** Missing ad sales → ACOS unknown. Missing total sales → TACOS unknown (never copy ACOS). Missing units → after-ads unit profit unknown. Missing COGS → profitability unknown. Never return zero in place of unknown.
9. **Break-even ACOS = pre-ads margin** from the **cited** profit snapshot. Not recomputed by the ads engine. Not TACOS. Not a guaranteed maximum ACOS.
10. **Ads API later replaces collection only.** Same DTO → `ads-calc-v1` → envelope. Stamp `source=ads_api`. Do not rewrite engines, evidence, or Copilot.
11. **Do not call `PPCAnalyticsService`.** STR `HIGH_ACOS` is not a P&L verdict.
12. **Org isolation.** Every ads row has `organization_id`. Other-org access is 404. Client-submitted ACOS, TACOS, ROAS, and after-ads profit are ignored.
13. **Snapshots are append-only.** Recalculation inserts a new row. History stays reproducible. Impact stamps `profit_snapshot_id`.
14. **UX.** Advertising lives inside `/profit/[id]`. No `/advertising` nav in 11C.2.
15. **No Skill layer in this milestone.**

---

## Domain Separation

These remain separate products. Overlap is composition of snapshots, not shared formulas.

| Domain | Owns | Must not own |
| --- | --- | --- |
| Listing Intelligence | Listing score, content, construction | ACOS, profit, campaign bids |
| Seller Reports | Search-term / campaign diagnostics, wasted spend | P&L, after-ads profit, `HIGH_ACOS` as loss |
| Profit Intelligence | Unit economics before ads (`profit-calc-v1`) | Period ad spend as `other_cost`, ACOS |
| Advertising Intelligence | Period spend/sales, ACOS, TACOS, ROAS, after-ads composition | Keyword optimization, bid writes, listing score |
| Seller Copilot | Question → tools → explanation of envelopes | Any money math |
| Future Skills | Business capabilities (e.g. Advertising Optimization) | Direct engine formulas or Amazon writes |

---

## Profit Integration

```text
ProfitCalculationService        unit P&L before ads     profit-calc-v1
AdvertisingCalculationService   period efficiency       ads-calc-v1
AdvertisingImpactService        compose snapshots       after-ads impact
```

**Confirmed**

- `profit-calc-v1` remains unchanged.
- Advertising is **not** added as another unit cost field.
- Advertising remains **period-based**.
- After-ads calculations remain a **composition layer**.

```text
selling_price - landed_cost = net_profit_before_ads
net_profit_before_ads - ad_spend_per_unit = net_profit_after_ads
break_even_acos = margin_before_ads    # cited, not recomputed
```

ROI stays `net_profit_before_ads / COGS`. After-ads return must not be renamed “ROI.”

---

## Data Model

```text
profit_models
  + advertising_models          editable seller ads inputs
  + advertising_snapshots       immutable calculation history
```

**Confirmed**

1. Advertising data is **not** stored as source of truth inside `profit_models`.
2. Advertising snapshots are **immutable**.
3. Recalculation creates a **new** snapshot.
4. Historical calculations remain reproducible (`ads-calc-v1` + frozen `inputs_json`).
5. Organization isolation is maintained (`organization_id` on every row; other-org 404).

Impact should stamp `profit_snapshot_id` so after-ads numbers do not silently change when the seller later recalculates unit economics.

---

## Calculation Ownership

| Service | Owns | Must not do |
| --- | --- | --- |
| `AdvertisingCalculationService` | ACOS, TACOS, ROAS | Database, AI, profit math, STR heuristics |
| `ProfitCalculationService` | Profit before ads | ACOS, TACOS, ad spend |
| `AdvertisingImpactService` | After-ads composition | Reimplement `profit-calc-v1` or `ads-calc-v1` |

No AI involvement in any of these services.

---

## Unknown Handling

| Missing input | Result |
| --- | --- |
| Ad sales | ACOS = unknown |
| Total sales | TACOS = unknown |
| Units in period | After-ads unit profit = unknown |
| COGS / incomplete profit | Profitability / after-ads = unknown |
| Ad spend | ROAS unknown and after-ads unknown |

**Never**

- Return zero in place of unknown
- Estimate values
- Copy ACOS into TACOS
- Use AI assumptions

---

## Period Alignment

Profit V1 snapshots are **unit economics** (`calculated_at`). They are not an August P&L. Advertising snapshots **are** dated (`period_start` / `period_end`).

V1 composition is:

> stated unit contribution **minus** this ads period’s allocated spend

It is **not** two matched monthly books.

**Freeze**

- Do not present after-ads as if both sides share a calendar month.
- Cite `profit_snapshot_id` **and** ads period on every impact envelope.
- Warn if the cited profit snapshot is stale (unit inputs changed since that snapshot).
- Do not auto-join Search Term and Business Report periods. The playbook’s same-window rule applies later at ingest.

A hard calendar mismatch detector between profit and ads cannot exist until profit snapshots gain an optional `as_of` period. That is **not** required for 11C.2 and must not redesign 11C.1.

---

## Break-even ACOS

Break-even ACOS comes from **existing profit margin before ads** on the cited profit snapshot.

It must **not**:

- Be calculated independently by the advertising engine
- Be confused with TACOS
- Be shown as a guaranteed maximum ACOS

UI wording must communicate the assumption: you can spend up to this share of **ad-attributed sales** on ads before unit contribution hits zero, **if volume and other costs hold**.

---

## Future Amazon Ads API Compatibility

```text
Today                         Future
─────                         ─────
Seller input                  Amazon Ads API / reports
  ↓                             ↓
Advertising DTO               Same DTO
  ↓                             ↓
ads-calc-v1                   ads-calc-v1
  ↓                             ↓
EvidenceEnvelope              EvidenceEnvelope
```

The Ads API should replace **data collection only**. It must not require rewriting calculation services, the evidence layer, or Copilot architecture. Stamp `source = ads_api`. No bid writes.

---

## Copilot Impact

Milestone 11C.2 must **not** modify:

- Planner
- Tool Registry
- Orchestrator
- Confirmation Gate
- Synthesis prompts
- Citation validator

Future Copilot integration happens only after Advertising Intelligence evidence is mature (**11C.4**). Designed later tools (`list_advertising_snapshots`, `get_advertising_snapshot`, `calculate_advertising`) wrap this engine; they are not part of 11C.2.

---

## Future Skill Compatibility

After implementation, this foundation can support a future **Advertising Optimization Skill**.

Example later workflow (not now):

```text
Seller: "Improve profitability of this ASIN"
  → Skill: Advertising Optimization
  → Tools: advertising + profit + listing
  → Evidence-backed recommendations
  → Seller confirmation before any Amazon action
```

**No Skill implementation in 11C.2.**

### Playbook mapping (strategy only)

Source: Amazon Skills Operating Playbook (22 operational skills, reviewed 19 August 2026).

| Playbook workstream | ASI home later | 11C.2 |
| --- | --- | --- |
| Listing & content (optimizer, Rufus) | Listing Intelligence tools | Out of scope |
| PPC optimization (bids, harvest, negatives) | Seller Reports + future Advertising Skill | Out of scope — not a campaign manager |
| Budget / TACOS control | Skill above ads + profit tools | Foundation only: TACOS when total sales exist |
| Reporting dashboards | Future Business Intelligence Skill | Evidence snapshots, not a dashboard fork |
| HTML vs upload-file risk split | Diagnose vs seller-approved Amazon action | No Amazon writes |

Playbook guardrails ASI should inherit later: diagnostics vs human-approved uploads; never auto-apply Amazon changes; **same time window** before joining ads spend to total sales.

---

## Security Validation

| Control | Rule |
| --- | --- |
| Ownership | All advertising rows require `organization_id` |
| Isolation | Other-org advertising models, snapshots, and profit data → 404 |
| Client trust | Frontend must not submit ACOS, TACOS, ROAS, or calculated after-ads profit as truth |
| Backend | Calculates all metrics; extra client-calculated keys are ignored |
| Amazon | No write actions |

---

## Compatibility Review

**Copilot (11B).** Compatible by non-touch. Planner, ToolRegistry, orchestrator, confirmation gate, synthesis, and citation validator stay frozen. Ads/profit questions remain `out_of_scope` until 11C.4.

**Profit Intelligence (11C.1).** Compatible. Unit P&L remains `price − landed cost = profit before ads`. Advertising Impact Service reads a profit snapshot and subtracts allocated ad spend per unit. That is composition, not a formula rewrite.

**EvidenceEnvelope (11A).** Compatible. Seller inputs are `seller_provided`. ACOS/TACOS/ROAS are `calculated` / `ads-calc-v1`. After-ads claims are `calculated` / `advertising_impact`. Money keys must never be `ai_inference`.

**Future Skills.** Compatible as a layer above tools. 11C.2 only builds the advertising engine those Skills will need.

---

## Risks Before Implementation

Only genuine risks:

1. **Grain confusion.** Profit V1 has `calculated_at`, not a calendar month. Ads have `period_start/end`. Combining a July ads period with “current” unit economics is the V1 design. The system must not present that as a matched August P&L. Evidence must show both identities. Spec §6.4 (stale profit snapshot warning) is required, not polish.
2. **Break-even ACOS will be misread** as a hard cap or as TACOS. UI must say: pre-ads margin, ad-attributed sales, volume and other costs held constant.
3. **Manual spend is untrusted input.** Snapshots must carry source + period so a later Ads API can replace collection without laundering bad seller numbers as “observed.”
4. **STR leakage.** Wiring Seller Reports heuristics into this engine would collapse domain separation and produce false “unprofitable” claims.

Silent auto-join of Search Term and Business Report periods is already forbidden. Keep that freeze.

---

## Remaining Product Questions

Architecture defaults in **bold**. These do **not** block 11C.2.

1. Should profit snapshots later gain an optional **as_of period** so calendar mismatch can be detected as a hard warning? **Not in 11C.2.** V1 mismatch detector is: cite `profit_snapshot_id` + ads period; warn if the cited unit snapshot is stale.
2. Is ads snapshot `complete` when ACOS inputs exist but TACOS is unknown? **Yes: `partial` if total sales missing.**
3. Auto-attach last STR totals? **No** in the first drop. Resolver is a confirmed-period seam later.
4. TACOS break-even as a separately named metric? **Later.** Do not rename break-even ACOS.
5. When does Copilot get ads tools? **11C.4**, after this engine is trusted.

---

## Implementation Recommendation

**Proceed.**

High-level sequence only (not an implementation ticket):

1. `AdvertisingCalculationService` / `ads-calc-v1` — Decimal ACOS, TACOS, ROAS; unknown/zero-denominator rules; no I/O, no AI.
2. Sibling persistence — `advertising_models` (editable inputs) + `advertising_snapshots` (immutable, org-scoped). Do not alter `profit-calc-v1` or existing migrations.
3. `AdvertisingModelingService` — worksheet lifecycle, isolation, snapshot insert/retrieve. No formulas.
4. `AdvertisingImpactService` — compose cited profit snapshot + ads snapshot; stamp `profit_snapshot_id`; after-ads unknown without units/profit/spend.
5. EvidenceEnvelope claims — seller inputs, calculated ads metrics, impact claims; period and snapshot ids on the envelope.
6. APIs — nested under `/api/v1/profit/models/{id}/advertising` plus stateless preview; ignore client-calculated metrics.
7. Profit workspace panel — inputs, API-only metrics, unknown copy, read-only history. No Ads nav.
8. Tests — formulas, unknowns, immutability, org 404, `profit-calc-v1` / Listing / Seller Reports / Copilot regression. Zero live Ads API.

**Stop before:** Amazon Ads API, STR auto-ingest, campaign/keyword optimization, Copilot tools/prompts, Skills, scenarios, TACOS forecasting.

---

## Document control

| Field | Value |
| --- | --- |
| Decision | Approved — proceed |
| Ads formula | `ads-calc-v1` |
| Profit formula | Unchanged `profit-calc-v1` |
| Primary UX | Advertising panel inside Profit workspace |
| Input V1 | Manual only |
| Skills / agents | Not in this milestone |

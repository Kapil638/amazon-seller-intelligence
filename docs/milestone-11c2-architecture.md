# Milestone 11C.2 — Advertising Intelligence Foundation Architecture

**Date:** 21 August 2026  
**Status:** Implemented. Architecture remains the source of truth for later slices (Ads API, Copilot tools).  
**Depends on:** Milestone 11C.1 implemented — `profit-calc-v1`, `profit_models` / `profit_snapshots`, `/profit` workspace. Milestone 11A/11B Copilot V1 implemented; Copilot still treats ads/PPC/profit questions as `out_of_scope`.  
**Audience:** Product, backend, AI engineering, frontend.

Companions: [milestone-11c-architecture.md](milestone-11c-architecture.md), [milestone-11/milestone-11c1-profit-foundation.md](milestone-11/milestone-11c1-profit-foundation.md), [milestone-11/milestone-11c2-advertising-foundation.md](milestone-11/milestone-11c2-advertising-foundation.md), [seller-report-analytics.md](seller-report-analytics.md), [milestone-11b-architecture.md](milestone-11b-architecture.md), [milestone-11/copilot-tool-layer.md](milestone-11/copilot-tool-layer.md).

**This document is the 11C.2 architecture freeze.** Implementation is recorded in [milestone-11c2-advertising-foundation.md](milestone-11/milestone-11c2-advertising-foundation.md).

---

## 0. Product thesis

11C.1 answers: **is this unit profitable before advertising?**

Sellers still cannot answer:

- How much did I spend on ads for this ASIN, in this period?
- Is advertising efficient (ACOS / TACOS)?
- After ads, is the product still profitable?

Seller Reports already compute **observed ACOS** from a Search Term Report (`ppc-analytics-v1`). That is search-term / campaign diagnostics. Its `HIGH_ACOS` heuristic is **explicitly not profitability evidence**. 11C.2 is the missing **period economics** layer: advertising as a cost that can sit on the P&L.

```text
Listing Intelligence     → listing quality (construction)
Seller Reports           → search-term / campaign diagnostics (not P&L)
Profit Intelligence      → unit economics before ads (11C.1, shipped)
Advertising Intelligence → period ad efficiency + impact on profit (this spec)
Copilot                  → later explains envelopes; never owns math
```

**Python owns ACOS, TACOS, and after-ads profit. AI owns the sentence.**

---

## 1. Product understanding

### 1.1 Problem

A seller can have a 38% unit margin and a 45% ACOS and still not know whether ads are destroying contribution. Listing score does not answer it. STR `HIGH_ACOS` does not answer it. ChatGPT must not invent spend.

11C.2 lets the seller put **stated ad spend and sales for a period** next to **stated unit economics**, and see evidence-backed ACOS, TACOS, and profit after ads — or an honest **unknown**.

### 1.2 Target users

| User | Need |
| --- | --- |
| Private-label seller on `amazon.in` | “After PPC, do I still make money on this ASIN?” |
| Operator using `/profit` | Enter a month of ad spend without leaving the P&L |
| Operator using Seller Reports | Keep campaign/search-term work there; do not turn Reports into a ledger |

V1: **single organization, ASIN + marketplace, INR, one current period worksheet.** Campaign-level and account-level rollups are later aggregations.

### 1.3 Core questions 11C.2 must be able to ground

| Seller question | What must exist in evidence |
| --- | --- |
| “How much did I spend on ads?” | `ad_spend` seller_provided or observed |
| “Am I spending too much?” | ACOS and/or TACOS **and** (for “too much vs profit”) a profit snapshot — never STR `HIGH_ACOS` relabeled as loss |
| “Is the product profitable after advertising?” | `net_profit_after_ads` calculated, or unknown with a reason (missing COGS, missing units, missing spend) |
| “Are ads helping growth or reducing margin?” | Efficiency metrics + after-ads vs before-ads **from snapshots**. Growth/volume lift is **unknown** unless the seller supplies a conversion/volume delta (11C.3) |

### 1.4 In scope (architecture)

| Capability | 11C.2 architecture |
| --- | --- |
| Manual advertising inputs (spend, ad sales, optional total sales, period, optional units) | Yes |
| Deterministic ACOS, TACOS, ROAS, advertising share of sales | Yes |
| Advertising impact on profit (after-ads unit profit, break-even ACOS) | Yes — **composition**, not a rewrite of `profit-calc-v1` |
| Panel inside Profit workspace + historical ad snapshots | Yes |
| Future Ads API / Seller Central ads reports as **input sources** | Boundary only |
| Optional later attach of last STR/Business **rollups** as inputs | Seam only (`AdvertisingInputResolver`) |

### 1.5 Out of scope

| Deferred | Why |
| --- | --- |
| Amazon Ads API / bid writes | Product rule: no Amazon writes; ingest is later |
| New parser for advertising CSVs | Search Term parser already exists; do not fork it in 11C.2 |
| Relabeling `HIGH_ACOS` as unprofitable | Frozen in `seller-report-analytics.md` |
| Campaign / keyword optimization UI | Seller Reports remains that surface |
| Copilot tools / planner intent lift | 11C.4 |
| Scenarios (“cut PPC 20%”) | 11C.3 |
| Invented attribution, incrementality, halo | No evidence |
| Account-level advertising dashboard | Later sum of ASIN periods |

### 1.6 Business value

- Trust: every ACOS traces to spend, sales, formula version, and period
- Decision: after-ads profit only when unit economics **and** allocatable spend exist
- Reuse: same engine will serve workspace, future Copilot, and future Ads API ingest
- Non-isolation: advertising is a **second intelligence engine**, not a one-off ads screen

### 1.7 Relationship to existing PPC analytics

| | Seller Reports (`ppc-analytics-v1`) | Advertising Intelligence (`ads-calc-v1`) |
| --- | --- | --- |
| Grain | Search term / campaign rows | ASIN + period rollup |
| ACOS definition | `spend / sales` | **Same** `ad_spend / ad_sales` |
| TACOS | Not computed (STR has no total sales) | `ad_spend / total_sales` when total sales present |
| Heuristics | Wasted spend, HIGH_ACOS flags | **None** in V1 |
| Profit | Must not claim | After-ads only via profit snapshot composition |

Do **not** call `PPCAnalyticsService` from the advertising engine. Do **not** copy wasted-spend rules into profit. A future resolver may copy **totals** (spend, sales) from a saved STR analysis payload into advertising **inputs**.

---

## 2. User experience design

### 2.1 Separate workspace vs inside Profit vs both

| Option | Verdict |
| --- | --- |
| A. Separate `/advertising` workspace as the primary product | Rejected for V1 |
| B. Advertising **only** inside Profit workspace | Incomplete: history of ad periods needs a home, but not a third nav silo |
| C. Both, with Profit as the primary surface | **Recommended** |

**Recommendation: C, implemented as “panel + snapshots inside Profit,” not a new top-nav product.**

| Surface | Role in 11C.2 |
| --- | --- |
| **`/profit/[id]`** | Primary. New **Advertising** input panel + metric cards + after-ads P&L line + evidence |
| **`/profit`** | Unchanged list of ASIN models; optional badge “ads period set” from latest ad snapshot |
| **Seller Reports** | Unchanged campaign/search-term expert surface |
| **Copilot** | Unchanged in 11C.2. Later dispatch `workspace.type = profit_model` with ads evidence |
| **Nav** | Do **not** add a sixth item “Ads” in 11C.2 |

**Why not a separate Ads app**

- The seller question is profitability **after** ads. A second app repeats the listing-vs-Copilot split incorrectly.
- Parent 11C: do not grow Seller Reports into a P&L; do not grow Copilot into a calculator.
- A dedicated `/advertising` can be added later for account rollup / multi-campaign, the same way Analyze stayed listing-expert when Copilot shipped.

**Why not Profit-only with ads fields jammed into unit inputs**

- Unit price/COGS and **period** spend are different grains. Mixing them in one form without a visible period causes fake TACOS and fake after-ads profit.
- Historical ad months must be snapshots, like profit calculations.

### 2.2 Workspace information architecture (Profit detail)

Extend the 11C.1 layout; do not replace it.

```text
┌──────────────────────────────────────────────────────────────────┐
│ Profit · ASIN · marketplace · INR                                │
├────────────────────────────┬─────────────────────────────────────┤
│ UNIT INPUTS (11C.1)        │ UNIT RESULTS (11C.1)                │
│ Price, COGS, fees, opex    │ Net profit before ads, margin, ROI  │
├────────────────────────────┴─────────────────────────────────────┤
│ ADVERTISING (11C.2)                                              │
│ Period: 1 Aug 2026 – 31 Aug 2026                                 │
│ Ad spend · Ad-attributed sales · Total sales (opt) · Units (opt) │
│ [Save and calculate ads]                                         │
│ Cards: ACOS · TACOS · ROAS · Spend                               │
│ After ads: net profit / unit  |  Break-even ACOS                 │
│ Unknown: TACOS needs total sales · after-ads needs units + COGS  │
├──────────────────────────────────────────────────────────────────┤
│ EVIDENCE  profit-calc-v1 · ads-calc-v1 · sources · period        │
│ HISTORY   prior advertising snapshots (this ASIN)                │
└──────────────────────────────────────────────────────────────────┘
```

**Advertising input area**

| Field | Required for | Notes |
| --- | --- | --- |
| Period start / end | Identity of the snapshot | Seller-owned; no default invented month |
| Ad spend | ACOS, TACOS, after-ads | Blank = unknown, not ₹0 |
| Ad-attributed sales | ACOS, ROAS | Amazon ads “sales” for the same period |
| Total sales | TACOS | Optional; do not copy ad sales silently |
| Units sold in period | After-ads **unit** profit | Optional |

Hints in UI (not calculations): “Enter 0 if you ran no ads.” “TACOS needs total sales, not only ad sales.” “After-ads profit needs units so spend can be allocated per unit.”

**Metric cards (API values only)**

- ACOS, TACOS, ROAS, ad spend, ad sales
- After-ads net profit / unit (or Unknown)
- Break-even ACOS (from profit margin; or Unknown if no complete unit snapshot)

**Evidence**

Reuse Profit evidence-card language: kind, source, formula version, period `as_of` / date range, unknown notes.

**Historical view**

List advertising snapshots for this model: period, status, ACOS, TACOS, calculated_at. Opening a row is **read-only**. Recalculate after editing inputs creates a **new** snapshot. Do not edit old snapshots.

Browser never computes ACOS/TACOS/after-ads. Preview API for live fields; Calculate persists.

---

## 3. Backend architecture

### 3.1 Placement

Follow 11C.1. Do **not** put ads formulas in Copilot, `profit_rules.py`, or the frontend.

```text
HTTP  /api/v1/profit/.../advertising  (or /api/v1/advertising)
        ↓
AdvertisingModelingService     CRUD, org scope, snapshot lifecycle
        ↓
AdvertisingCalculationService  Pure ads math. No I/O. No AI. No profit formulas.
        ↓
AdvertisingImpactService       Composer: profit snapshot + ads snapshot → after-ads claims
        ↓
ads evidence + optional combined impact evidence
        ↓
Profit workspace UI  (and later ToolRegistry)
```

`ProfitCalculationService` / `profit-calc-v1` stay **unchanged**. After-ads is not a new line inside unit cost math.

`PPCAnalyticsService` stays on Seller Reports. 11C.2 does not call it.

### 3.2 Services

#### `AdvertisingCalculationService`

**Owns:** ACOS, TACOS, ROAS, advertising share of total sales, completeness for **ads inputs only**.

**Must not:** database, AI, Rainforest, SP-API, Ads API, COGS, selling price, listing score, STR heuristics, after-ads profit (that needs unit profit).

#### `AdvertisingModelingService`

**Owns:** org-scoped advertising worksheets + immutable snapshots. Orchestrates calculate → persist. Preview without persist.

**Must not:** implement formulas; accept client-supplied ACOS as truth.

#### `AdvertisingImpactService` (composer)

**Owns:** after-ads unit profit, ad spend per unit, break-even ACOS **citation of both engines**.

Inputs: latest **complete or partial** `profit_snapshot` outputs + `advertising_snapshot` outputs/inputs.

```text
If profit.net_profit_before_ads is unknown → after-ads unknown
If ads.ad_spend is unknown → after-ads unknown
If units_in_period missing or 0 → after-ads unit profit unknown; still keep period ACOS
Else:
  ad_spend_per_unit = ad_spend / units_in_period
  net_profit_after_ads = net_profit_before_ads - ad_spend_per_unit
break_even_acos = profit.margin_before_ads   # not recomputed from ads
```

**Must not:** reimplement `profit-calc-v1` or `ads-calc-v1`. If margin is missing, break-even ACOS is unknown — do not estimate.

#### `AdvertisingInputResolver` (seam, not required to ship manual V1)

Optional: map last `report_uploads` analysis payload → `{ ad_spend, ad_sales }` for an ASIN. Must **not** auto-fill `total_sales` from STR. Must **not** emit HIGH_ACOS as a profit claim. Period alignment is seller-confirmed.

### 3.3 HTTP (when implementation is approved)

Additive. Do not change 11C.1 profit calculate contracts (still unit-only).

Preferred nesting (keeps one seller object: the profit ASIN worksheet):

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/v1/profit/models/{id}/advertising` | Current ads worksheet + latest ads snapshot + impact |
| PATCH | `/api/v1/profit/models/{id}/advertising` | Update ads **inputs** only |
| POST | `/api/v1/profit/models/{id}/advertising/calculate` | Persist ads snapshot; return impact |
| GET | `/api/v1/profit/models/{id}/advertising/snapshots` | History |
| POST | `/api/v1/advertising/preview` | Stateless ads-calc-v1 (and optional impact if profit snapshot id supplied) |

Alternative: top-level `/api/v1/advertising/models` if product wants ads without a profit model. **V1 recommendation:** require a profit model id so after-ads has a place to compose. Seller can still calculate ACOS with incomplete profit (impact stays unknown).

Client-sent `acos`, `tacos`, `net_profit_after_ads` are ignored.

Cost: **0** providers.

### 3.4 Do not modify in 11C.2 implementation

- `profit_rules.py` / `ProfitCalculationService`
- Copilot planner, registry, orchestrator, synthesis, citation validator
- Listing Intelligence
- Seller Reports parsers and `ppc-analytics-v1` heuristics

---

## 4. Data model

Mirror 11C.1: living worksheet + immutable snapshots. **Do not add ad spend columns to `profit_models` as the source of truth.** Period belongs on the advertising entity. 11C parent sketched ads fields on `profit_models`; 11C.2 refines that into a sibling table so unit and period do not collide.

Suggested future migration name: `0006_advertising_models`. Do not create it in this spec.

### 4.1 `advertising_models`

One living ads worksheet per profit model (V1: `profit_model_id` unique). Draft inputs only.

| Column | Notes |
| --- | --- |
| id | UUID PK |
| organization_id | FK, required |
| profit_model_id | FK to `profit_models`, unique in V1 |
| asin | Denormalized, same as profit model |
| marketplace | Same as profit model |
| currency | `INR` V1 |
| period_start | Date, nullable until seller sets |
| period_end | Date, nullable |
| ad_spend | Numeric, nullable |
| ad_sales | Numeric, nullable (ad-attributed) |
| total_sales | Numeric, nullable |
| units_in_period | Numeric/int, nullable |
| source | `seller` \| `report_upload` \| `ads_api` (V1: `seller`) |
| report_upload_id | Nullable FK; unused until resolver ships |
| created_at / updated_at | |

### 4.2 `advertising_snapshots`

Append-only. Each calculate inserts a new row. **Never UPDATE outputs.**

| Column | Notes |
| --- | --- |
| id | UUID PK |
| organization_id | Denormalized |
| advertising_model_id | FK |
| profit_model_id | Denormalized for listing |
| status | `complete` \| `partial` \| `failed` |
| ads_formula_version | `ads-calc-v1` |
| inputs_json | Frozen inputs including period |
| outputs_json | ACOS/TACOS/ROAS/unknowns |
| completeness | `{ unknown, messages }` |
| calculated_at | |

Impact (after-ads) may be stored **inside** `outputs_json` of a **combined** calculate response, or as a sibling `impact_json` stamped with `profit_snapshot_id` used. Recommendation: `impact_json` + `profit_snapshot_id` on the advertising snapshot so after-ads is auditable against a **specific** unit snapshot. If the seller later recalculates profit, old after-ads numbers do not silently change.

### 4.3 Stored vs calculated

| Stored | Calculated |
| --- | --- |
| Period, spend, ad sales, total sales, units | ACOS, TACOS, ROAS |
| Source of inputs | Advertising share of total sales (= TACOS) |
| Formula version | Completeness |
| `profit_snapshot_id` used for impact | `ad_spend_per_unit`, `net_profit_after_ads`, `break_even_acos` (composer) |

Never persist seller-typed ACOS as truth.

### 4.4 What not to persist

- LLM copy
- STR row-level keywords
- Client-computed ACOS
- Cross-org caches

---

## 5. Calculation architecture (`ads-calc-v1`)

All money `Decimal` INR. Rates as **fractions** (`0.32` = 32%), same as `ppc-analytics-v1` and `profit-calc-v1`. Zero denominator → `null`. Missing input → unknown, **not** `0`.

```text
acos = ad_spend / ad_sales          if ad_sales > 0 else null
roas = ad_sales / ad_spend          if ad_spend > 0 else null
tacos = ad_spend / total_sales      if total_sales > 0 else null
```

**Advertising cost percentage:** do **not** introduce a third overlapping formula.

| Name in UI | Formula | When unknown |
| --- | --- | --- |
| ACOS | spend / ad-attributed sales | missing spend or ad sales; or ad sales = 0 |
| TACOS (advertising % of total sales) | spend / total sales | missing spend or total sales; or total sales = 0 |
| ROAS | ad sales / spend | missing either; or spend = 0 |

If the seller has ad sales but no total sales: ACOS may be complete; TACOS is unknown. Never set TACOS = ACOS.

**Complete vs partial**

- Ads snapshot `complete` when period, spend, and ad_sales are all present (TACOS may still be unknown — then status is `partial` if product wants TACOS in the completeness contract). **Recommendation:** `complete` means every **provided** required ads field is present for ACOS (period + spend + ad_sales). Missing total_sales → `partial` with unknown `tacos` only, ACOS still calculated.
- Missing spend → ACOS, TACOS, ROAS unknown.
- Explicit spend `0` with ad_sales > 0 → ACOS `0`, ROAS null or infinite: **ROAS unknown** (zero denominator). ACOS = 0 is valid.

**Do not invent defaults** for missing total sales, units, or organic vs sponsored split.

Composer formulas (not inside `AdvertisingCalculationService`):

```text
ad_spend_per_unit     = ad_spend / units_in_period     if units > 0 else null
net_profit_after_ads  = net_profit_before_ads - ad_spend_per_unit
break_even_acos       = margin_before_ads              # from profit snapshot
```

If `units_in_period` is missing: period ACOS/TACOS still valid; after-ads unit profit unknown, with message: `Profit after ads cannot be calculated per unit because units in the advertising period are missing.`

If profit COGS missing: after-ads unknown, with the existing COGS message plus ads not blamed as the sole cause.

---

## 6. Profit integration

### 6.1 Boundary

```text
ProfitCalculationService     unit P&L before ads     profit-calc-v1
AdvertisingCalculationService  period efficiency     ads-calc-v1
AdvertisingImpactService       subtract allocated ads from unit profit
```

| Layer | May read | Must not do |
| --- | --- | --- |
| `profit-calc-v1` | price, COGS, fees, opex | ACOS, TACOS, ad spend |
| `ads-calc-v1` | spend, ad sales, total sales | COGS, margin, “profitable” |
| Composer | both snapshot outputs | New fee math, elasticity, HIGH_ACOS |

11C.1 outputs remain:

```text
selling_price - landed_cost = net_profit_before_ads
```

11C.2 adds (composer):

```text
net_profit_before_ads - ad_spend_per_unit = net_profit_after_ads
```

Do **not** fold advertising into `other_cost` on the unit form. That hides period grain and breaks ACOS.

### 6.2 ROI

11C.1 ROI stays `net_profit_before_ads / COGS`. After-ads return is a **different** named metric later. Copilot (future) must not call after-ads profit “ROI.”

### 6.3 Break-even ACOS

Equals **pre-ads margin** from the cited profit snapshot. UI copy: you can spend up to this share of **ad-attributed sales** on ads before unit contribution hits zero, **if volume and other costs hold**. Not a TACOS break-even. Not computed by the ads engine alone.

### 6.4 Stale composition

Impact stamps `profit_snapshot_id`. If unit inputs change, workspace shows “Unit economics changed since this ads snapshot” and asks to recalculate ads impact — it does not rewrite history.

---

## 7. Evidence architecture

Reuse `EvidenceEnvelope`. `tool_name` for ads engine: `advertising_calculation`. Composer may emit `advertising_impact` as a second envelope or additional claims on a combined envelope. **Do not** invent `AdvertisingEnvelope`.

### 7.1 Ads engine claims

| Key | Kind | Source |
| --- | --- | --- |
| `asin`, `marketplace`, `currency` | seller_provided | seller_input |
| `period_start`, `period_end` | seller_provided | seller_input |
| `ad_spend`, `ad_sales`, `total_sales`, `units_in_period` | seller_provided or unknown | seller_input (later report_upload / ads_api) |
| `acos`, `tacos`, `roas` | calculated or unknown | `ads-calc-v1` |
| `ads_formula_version`, `status`, `completeness` | calculated | `ads-calc-v1` |

### 7.2 Impact claims

| Key | Kind | Source |
| --- | --- | --- |
| `net_profit_before_ads` | historical / calculated | `profit-calc-v1` via snapshot id |
| `ad_spend_per_unit` | calculated or unknown | composer + `ads-calc-v1` |
| `net_profit_after_ads` | calculated or unknown | composer |
| `break_even_acos` | calculated or unknown | composer citing `margin_before_ads` |
| `profit_snapshot_id` | historical | snapshot |

`ai_inference` is forbidden on money keys.

### 7.3 Unknown and citations (future synthesis)

- Missing ad sales → ACOS unknown; do not say “ACOS is healthy”
- Missing total sales → TACOS unknown; do not say TACOS = ACOS
- STR `HIGH_ACOS` must not appear as a claim on these envelopes
- Future Copilot: “too much on ads” requires ACOS **and** break-even or after-ads claims, not listing score
- Citation validator (11C.4): money tokens require claim keys, same as profit

---

## 8. ToolRegistry integration (future — do not implement)

11C.2 does **not** register tools. Copilot remains `out_of_scope` for ads until 11C.4.

Designed tools:

| Tool | Cost | Confirm | Output |
| --- | --- | --- | --- |
| `list_advertising_snapshots` | none | No | Period list for ASIN |
| `get_advertising_snapshot` | none | No | Ads + impact claims |
| `calculate_advertising` | none | No | `{ advertising_model_id }` only — no LLM-injected spend |

Planner (later): “My ACOS is high, why?”

1. History-first `get_advertising_snapshot` for conversation ASIN
2. If no snapshot → `profit_clarify` / ads clarify: open `/profit/{id}`, do not invent 40% ACOS
3. If snapshot has ACOS but no profit → explain efficiency only; **unknown** whether unprofitable
4. If both exist → cite ACOS vs `break_even_acos` and `net_profit_after_ads`
5. Never call `analyze_listing_v2` to explain ACOS
6. Never treat STR heuristics as the answer

---

## 9. External integration strategy

11C.2 ships **manual input**. Ingest later replaces **collection**, not `ads-calc-v1`.

```text
Today                    Later
─────                    ─────
Seller types spend       Ads API reports / Sponsored Products
Seller types ad sales    Same API attributed sales
Seller types total sales SP-API / Business Report (same DTO)
STR upload (optional)    AdvertisingInputResolver → same DTO
        ↓
AdvertisingCalculationService
        ↓
EvidenceEnvelope
```

**Amazon Ads API:** maps to `ad_spend`, `ad_sales`, period. Stamp `source = ads_api`. No bid writes.

**Seller Central advertising reports:** prefer existing Search Term parser → rollup to DTO. Do not add a second ACOS implementation. Business Report remains the candidate for `total_sales`; join only with seller-confirmed period.

**Rainforest:** not an ads source.

---

## 10. AI architecture

Unchanged two-LLM split. 11C.2 **does not** edit planner or synthesis prompts.

When 11C.4 lifts ads off `out_of_scope`:

| Role | Question |
| --- | --- |
| Planner | Which ads/profit **capability** and **registered tool**? |
| Synthesis | What do these envelopes **mean**? |

Forbidden forever: calculating ACOS, guessing spend, “typical India ACOS,” calling ads fine because no STR was uploaded, mixing listing score with TACOS.

Planner 503 fallback (later): keywords ACOS/TACOS/PPC → history-first ads snapshot or clarify.

---

## 11. Security model

Same bar as profit (financial data).

| Control | Rule |
| --- | --- |
| Ownership | All ads rows `organization_id` |
| Isolation | Other-org advertising id → 404 |
| Copilot path | Tools only (later) |
| Logs | Do not log spend, sales, ACOS values |
| Audit | Immutable snapshots; impact cites `profit_snapshot_id` |
| Writes to Amazon | None |
| Client trust | Ignore client ACOS; ignore planner `confirmed` |

---

## 12. Scalability roadmap

```text
ListingEngine          shipped
ProfitEngine           11C.1 shipped
AdvertisingEngine      11C.2 this spec
InventoryEngine        future
CashFlowEngine         future
HealthScoreEngine      composes snapshots (e.g. ACOS vs break-even)
```

Each engine: inputs → versioned calculate → snapshot → EvidenceEnvelope → Copilot tool.

Do not build a Copilot-only ads path. Do not put ACOS inside listing V2. Do not grow Seller Reports into the P&L.

11D diagnostics may later rank “ACOS above break-even on advertising snapshot {id}” **only** when those claims exist.

---

## 13. Risks and unknowns

| Risk | Why it hurts | Mitigation |
| --- | --- | --- |
| Manual spend is wrong | Bad ACOS, bad after-ads | Source + period on every snapshot; seller-owned |
| Ad sales ≠ total sales | Fake TACOS | TACOS unknown without total sales |
| STR vs Business period mismatch | Fake join | No auto-join; resolver optional and confirmed |
| Attribution / branded vs non-branded | Seller asks “why ACOS” | V1 has no campaign split; Copilot must say unknown |
| Sponsored vs organic | “Ads causing sales” | Unknown without incrementality study |
| Units missing | After-ads looks like ACOS-only | Explicit unknown message |
| Allocating period spend to unit with uneven mix | SKU mix | V1 ASIN-level; warn volume held constant |
| Ads API attribution windows | Later ingest | DTO keeps period explicit |
| Marketplace fee/ads differences | `amazon.in` only V1 | Same as profit |
| Relabeling HIGH_ACOS | False unprofitable | Separate engines; tests forbid |

---

## 14. Product owner questions

Resolve before implementation. Architecture defaults in **bold**.

1. **ASIN vs account?** **ASIN + marketplace V1** (tied to profit model).
2. **Multiple campaigns?** **No** in 11C.2. One spend/sales rollup per period. Campaigns stay on Seller Reports.
3. **Period grain?** Seller-entered start/end. **No** invented “this month.” Daily vs weekly vs monthly is the seller’s window, not three products.
4. **Must a profit model exist first?** **Yes** for V1 workspace (ads panel on `/profit/[id]`). ACOS can still calculate if unit P&L is incomplete.
5. **Is `units_in_period` required?** **No** for ACOS/TACOS. **Yes** for after-ads unit profit.
6. **Attach last STR automatically?** **No** in first 11C.2 drop. Resolver is a seam.
7. **Ads API after manual validation?** **Yes** — ingest after the engine is trusted.
8. **Compare historical ads snapshots in UI?** **List + read-only** in 11C.2; visual diff can wait.
9. **Break-even ACOS = pre-ads margin?** **Yes** (11C freeze). TACOS break-even needs another name.
10. **New nav item?** **No** for 11C.2.
11. **Explicit ₹0 ads vs blank?** Blank = unknown. Saved 0 = no spend (ACOS 0 if ad sales present).
12. **Currency / marketplace?** **INR / amazon.in** only, matching 11C.1.

---

## 15. Testing strategy (when implementation is approved)

CI: mock providers, SQLite, zero live Ads/Rainforest/OpenAI.

| Layer | Tests |
| --- | --- |
| `ads-calc-v1` | Golden ACOS/TACOS/ROAS; null denominators; missing spend → unknown; TACOS not equal ACOS when total sales missing |
| Composer | After-ads = before-ads − spend/units; missing units → unknown after-ads; does not call profit_rules internals |
| Isolation | Org B ads id → 404 |
| Immutability | Second calculate inserts new snapshot; first outputs unchanged |
| Client trust | Posted `acos` ignored |
| Regression | `profit-calc-v1` golden case (₹379) unchanged; STR HIGH_ACOS tests unchanged |
| Frontend | Displays API ACOS; save payload has no `acos` |

---

## 16. Acceptance criteria (future go-ahead)

- UI ACOS/TACOS **equal** `AdvertisingCalculationService` for the same inputs
- `profit-calc-v1` golden case unchanged
- Missing ad sales → unknown ACOS, not 0
- Missing total sales → unknown TACOS, not copied ACOS
- Missing units → unknown after-ads profit; ACOS still shown if calculable
- After-ads cites `profit_snapshot_id`
- Fees still labeled assumptions; ads labeled seller input until Ads API
- Org isolation
- No Amazon write, no Ads API client, no Copilot prompt edits in the 11C.2 implementation slice (unless product explicitly expands 11C.2 — this spec says they stay frozen)

---

## 17. Suggested implementation slice (not this document’s job)

| Slice | Ships |
| --- | --- |
| **11C.2** | `ads-calc-v1` + advertising tables + panel on `/profit/[id]` + composer after-ads + history list |
| 11C.2b optional | STR resolver (manual confirm of period) |
| 11C.3 | Scenarios including PPC ±% |
| 11C.4 | Copilot tools + citation rules for ACOS/TACOS |

---

## 18. Document control

| Field | Value |
| --- | --- |
| Architecture status | Draft for review — not an implementation ticket |
| Ads formula version (proposed) | `ads-calc-v1` |
| Profit formula | Unchanged `profit-calc-v1` |
| Primary UX | Advertising panel inside Profit workspace |
| Input V1 | Manual only |

**Decision required from product:** answers in §14, especially profit-model requirement, units for after-ads, STR attach, and whether TACOS is required for ads snapshot `complete`.

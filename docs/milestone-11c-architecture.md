# Milestone 11C — Seller Profit Intelligence Architecture

**Date:** 21 August 2026  
**Status:** Architecture for Option B. **11C.1 and 11C.2 are implemented** — unit economics plus advertising impact inside `/profit`. Remaining slices (scenarios, Copilot tools) are not started.  
**Depends on:** Milestone 11A frozen (`834a79b`) — ToolRegistry, EvidenceEnvelope, budget. Milestone 11B.1–11B.5 implemented — Copilot conversation, hybrid planner, orchestrator, synthesis, Copilot UI.  
**Audience:** Product, backend, AI engineering, frontend.

Companions: [milestone-11b-architecture.md](milestone-11b-architecture.md), [milestone-11/milestone-11-plan.md](milestone-11/milestone-11-plan.md), [milestone-11/copilot-tool-layer.md](milestone-11/copilot-tool-layer.md), [listing-intelligence-v2.md](listing-intelligence-v2.md), [seller-report-analytics.md](seller-report-analytics.md), [database-schema.md](database-schema.md).

**This document does not implement Profit Intelligence.** No APIs, migrations, fee tables, Copilot tools, or UI are created by this spec.

---

## 0. Product thesis

Amazon Seller Intelligence today answers: **how well is this listing constructed?**

Listing Intelligence V2 is explicit about what it does *not* answer: how well the product will sell, whether ads are wasting money, or whether the business makes money. Copilot V1 explains listing evidence and refuses profit, PPC, and competitor questions.

Milestone 11C introduces the second intelligence product:

> Help the seller decide whether this Amazon product is **profitable**, under **stated inputs and labeled assumptions**, and what happens if price, ads, or conversion change.

Profit Intelligence is a **business engine**, not a chat feature and not a spreadsheet-in-the-model.

| Layer | Owns |
| --- | --- |
| Deterministic profit engine | Net profit, margin, ROI, ACOS, TACOS, break-even ACOS, scenario deltas |
| Seller / integrations | Cost inputs, advertising actuals, selling price |
| Assumption catalog | Amazon fee tables and other labeled defaults (never presented as official Amazon) |
| ToolRegistry | The only way Copilot may call the profit engine |
| Planner LLM | Propose profit intent + registered tools |
| Plan validator | History-first reuse of saved models; refuse invented costs |
| Synthesis LLM | Explain envelopes; never recalculate money |
| Seller | Whether inputs are complete; whether to spend Amazon credits to seed price |

If a rupee amount is not in an `EvidenceEnvelope` from this turn (or a cited saved profit snapshot), Copilot must say it is **unknown**. It must not invent COGS, fees, ACOS, or “healthy margin.”

This is the same ASI contract as listing scores: **Python owns the number. AI owns the sentence.**

---

## 1. Product understanding

### 1.1 Problem

Sellers already have listing scores, History, and (if they uploaded a Search Term Report) observed ACOS. None of that is a P&L.

Typical seller questions that ASI cannot answer today:

- After Amazon takes its cut, do I make money on this ASIN?
- Is my 40% ACOS killing profit, or is it still fine given my margin?
- If I raise price 10%, what happens to unit profit vs. what I might lose in conversion?
- Which cost line is the real problem: COGS, FBA, or ads?

Without a profit engine, Copilot must refuse those questions. With a profit engine, Copilot can **explain** them — but only after deterministic math.

### 1.2 Target users

| User | Need |
| --- | --- |
| Private-label / brand seller on `amazon.in` | Unit economics before scaling ads or inventory |
| Reseller / arbitrage seller | Quick “is this SKU still worth it” after fee or ad changes |
| Operator using Copilot | Ask in language; land in a workspace with numbers, not a paragraph of arithmetic |

V1 of this product is **single-organization, ASIN-level, India marketplace, INR**. Multi-user auth, multi-marketplace, and account-level rollups are designed as later aggregations, not V1 scope.

### 1.3 What 11C is (Option B)

**Seller Profit Intelligence** — not chat-only P&L, and not a full Seller Business Intelligence suite.

| Capability | In 11C architecture |
| --- | --- |
| Core unit P&L (price, COGS, Amazon fees, shipping, packaging, other costs → net profit, margin, ROI) | Yes |
| Advertising intelligence (spend, ACOS, TACOS, break-even ACOS) | Yes — from seller input and/or existing STR analytics, **not** Ads API |
| Scenario modeling (price ±X%, PPC ±Y%, conversion ±Z%) | Yes — deterministic deltas from a baseline snapshot |
| Interactive workspace | Yes — primary surface |
| Copilot tools + explanation + workspace dispatch | Yes — secondary surface |
| Official Amazon fee APIs / SP-API / Ads API | Boundary only |
| Inventory, cash flow, forecasting, business health score | Roadmap only (see §11) |
| Account-wide P&L dashboard | Later aggregation over ASIN models |

### 1.4 What 11C is not

| Deferred | Why |
| --- | --- |
| ChatGPT doing arithmetic | Violates frozen ASI philosophy; unverifiable; no scenarios |
| “Is this listing profitable?” from listing score | Listing quality ≠ unit economics |
| Labeling STR `HIGH_ACOS` as unprofitable | Existing PPC heuristics are **not** profitability evidence (`seller-report-analytics.md`) |
| Live Amazon fee estimates | No SP-API; fee tables are **assumptions** |
| TACOS from Ads API | No Ads API; TACOS only when ad spend **and** total sales are in evidence |
| Autonomous price or bid changes | Product rule: no Amazon writes |
| Launch / TAM / keyword-volume research | Different product |

### 1.5 Business value

| Outcome | Why it matters |
| --- | --- |
| Trust | Every rupee traces to an input, an assumption version, or a calculated claim |
| Speed | Seller models an ASIN in one workspace instead of a private spreadsheet |
| Decision quality | Scenarios answer “what if” without the model inventing elasticity |
| Copilot leverage | The same engine serves UI sliders and Copilot answers |
| Platform path | Profit is the first **non-listing** intelligence engine; PPC, inventory, and health scores should follow this pattern, not fork Copilot |

### 1.6 Relationship to existing products

```text
Analyze / History     → Listing quality (construction)
Seller Reports        → Observed ads / traffic metrics (not P&L)
Profit Intelligence   → Unit economics (seller costs + labeled fees + ads inputs)
Copilot               → Orchestrates tools across all three; never owns math
```

Do **not** grow Analyze into a profit screen. Do **not** grow Seller Reports into a P&L. Do **not** grow Copilot into a calculator.

---

## 2. User experience design

### 2.1 Chat-only vs workspace

| Option | Verdict |
| --- | --- |
| A. Chat-only answer | Rejected as the primary product |
| B. Workspace experience | **Recommended** |

**Why not chat-only**

- Profit is interactive: sellers change COGS, fees, and ads and need the grid to update immediately.
- Copilot V1 already rejected “ChatGPT clone.” Numbers belong in trusted components, same as listing scores belong on Analyze / History.
- 11B reserved `workspace.type`; unknown types were unused because profit is 11C.
- Scenario cards, assumption labels, and “unknown” cost lines do not fit a chat bubble.
- Future Seller Business Intelligence (PPC workspace, inventory, cash flow) needs the same pattern: **engine + workspace + Copilot dispatch**. Chat-only profit would become an isolated feature.

**Why not workspace-only**

- Sellers will still ask Copilot “is B0… profitable?” and “what if I cut PPC 20%?”
- Copilot should open or refresh the workspace and **explain** the snapshot, not hide the engine behind a URL the seller must discover.

**Recommended dual surface (same as Listing Intelligence):**

| Surface | Role |
| --- | --- |
| **Profit workspace** (`/profit` and `/profit/[id]`) | Expert input, live calculation, scenarios, evidence |
| **Copilot** (`/copilot`) | Intent, missing-input clarification, explanation, dispatch `workspace.type = profit_model` |

Analyze stays the listing expert surface. Profit is the economics expert surface. Copilot is the command center.

### 2.2 Primary workflows

#### Journey 1 — “Is this product profitable?”

1. Seller opens Profit (nav) or asks Copilot with an ASIN.
2. Workspace loads or creates an **ASIN profit model** for current org + marketplace.
3. Seller enters or confirms: selling price, COGS, shipping, packaging, other costs.
4. Engine applies **fee assumption catalog** (referral + FBA band) labeled as assumptions.
5. Optional: seller enters ad spend + attributed sales, or attaches last Search Term rollup for that ASIN if one exists.
6. Engine returns a **profit snapshot** (persisted).
7. UI shows P&L stack, margin, ROI, unknowns.
8. If the seller arrived via Copilot, synthesis explains the snapshot with citations; a deep link opens the workspace.

**If COGS is missing:** Copilot and workspace both say profit is **unknown**. They do not estimate COGS from category or competitors.

**If selling price is missing:** Seller may type it, or Copilot may offer History/Rainforest seed via existing `get_product` / saved snapshot **with confirmation** if a live fetch is required. Seeded price is `observed`, not `seller_provided`, until the seller edits it.

#### Journey 2 — “What is my real margin after ads?”

1. Baseline unit P&L must already exist (Journey 1).
2. Advertising inputs: spend and (for ACOS) ad-attributed sales; (for TACOS) total sales in the same period.
3. Engine computes ACOS, TACOS, contribution after ads, break-even ACOS.
4. UI shows ads as a cost layer on the same P&L, not a separate “PPC is high” slogan.
5. If only STR upload exists and no COGS: show ACOS as **advertising evidence**, explicitly **not** profitability.

#### Journey 3 — “What if I increase price by 10%?”

1. Requires a saved **baseline snapshot** (complete enough to calculate).
2. Seller (or Copilot, after validation) submits a **scenario spec**: `{ price_delta_pct: 0.10 }` — not a guessed new profit number.
3. Engine clones baseline inputs, applies deltas, recalculates, returns a scenario snapshot.
4. UI shows a scenario card: before / after / delta, with conversion treated as **unknown unless the seller supplied a conversion delta**.
5. Synthesis may explain the card. It must not invent demand elasticity. If conversion is unchanged in the spec, copy must say unit profit assumes **volume held constant**, unless a conversion input was provided.

#### Journey 4 — Copilot with incomplete data

1. “Is B01MD1SKLL profitable?”
2. Planner selects `list_profit_models` / `get_profit_snapshot` (history-first).
3. If no model or required inputs missing → intent `profit_clarify`, **no synthesis of fake P&L**, workspace dispatch or field prompts.
4. If snapshot exists → `get_profit_snapshot` only (cost `none`), then synthesis.

### 2.3 Workspace information architecture

Keep the Copilot visual language (surface, evidence cards, activity, no raw JSON). Profit is a **workspace**, not a second chat.

**Recommended layout**

```text
┌─────────────────────────────────────────────────────────────┐
│ Profit Intelligence     ASIN · marketplace · as-of          │
├──────────────────┬──────────────────────────────────────────┤
│ INPUTS           │ OUTPUTS                                  │
│ Selling price    │ Net profit / unit                        │
│ COGS             │ Margin %                                 │
│ Referral (assum.)│ ROI %                                    │
│ FBA (assum.)     │ Break-even ACOS                          │
│ Shipping         │ Completeness: known / unknown lines      │
│ Packaging        ├──────────────────────────────────────────┤
│ Other            │ P&L STACK (evidence)                     │
│ Ad spend (opt.)  │ Price → fees → COGS → ops → ads → profit │
│ Ad sales (opt.)  ├──────────────────────────────────────────┤
│ Total sales(opt.)│ SCENARIOS                                │
│ [Calculate]      │ Price +10% · PPC −20% · CVR +10%         │
└──────────────────┴──────────────────────────────────────────┘
│ Evidence: formula version · fee catalog version · sources   │
└─────────────────────────────────────────────────────────────┘
```

**Inputs (seller-owned unless noted)**

| Field | Owner | Required for |
| --- | --- | --- |
| ASIN | Seller / Copilot slot | Identity |
| Marketplace | Default `amazon.in` | Fee catalog |
| Currency | INR in V1 | All money |
| Selling price | Seller or observed seed | Unit profit |
| COGS | Seller | Unit profit, ROI, break-even ACOS |
| Referral fee | Assumption catalog or seller override | Unit profit |
| FBA / fulfillment | Assumption catalog or seller override | Unit profit |
| Shipping to Amazon / customer | Seller | Unit profit |
| Packaging | Seller | Unit profit |
| Other opex per unit | Seller | Unit profit |
| Ad spend (period) | Seller or STR rollup | After-ads profit, ACOS, TACOS |
| Ad-attributed sales (period) | Seller or STR rollup | ACOS |
| Total sales (period) | Seller or Business Report rollup | TACOS |
| Units (period, optional) | Seller | Period P&L vs unit P&L |
| Conversion delta (scenario only) | Seller | Volume-sensitive scenarios |

**Outputs (engine-owned, never typed by the seller as facts)**

- Referral and FBA **amounts** when derived from catalog % × price (still labeled assumption)
- Net profit / unit
- Profit margin
- ROI (on COGS, definition frozen in formula version)
- ACOS, TACOS, break-even ACOS
- After-ads contribution
- Scenario deltas
- Completeness / unknown list

**Scenario cards**

Each card is a saved or preview scenario bound to `baseline_snapshot_id`:

- Title (seller or template): “Price +10%”
- Spec (machine): deltas only
- Results (engine): new stack + delta vs baseline
- Caveats (engine): e.g. `volume_held_constant: true`

Do not let the UI or Copilot submit a scenario whose **result fields** are filled by the client or the model.

**Evidence display**

Reuse Copilot evidence-card patterns:

- Each money line: value, kind (`seller_provided` / `observed` / `calculated` / `unknown`), source (`seller_input` / `product_snapshot` / `fee_catalog:amazon-in-fba-v1` / `profit-calc-v1` / `ppc-analytics-v1`)
- Footer: `profit_formula_version`, `fee_catalog_version`, `calculated_at`
- Deep links: `/profit/{model_id}`, optional `/history/{report_id}` if price was seeded from a listing snapshot

**Nav**

Add **Profit** to `app-shell` (alongside Analyze, Copilot, History, Seller Reports, Bulk). Do not bury it only inside Copilot.

Suggested Copilot chips after 11C (in addition to listing chips):

- *Is this ASIN profitable?*
- *What is my margin after ads?*
- *What if I raise price 10%?*

If inputs are missing, the chip still routes to clarify + workspace, not a hallucinated answer.

### 2.4 Frontend constraints (when implementation is approved)

- Browser never implements profit formulas. Sliders call `preview` / `calculate` APIs.
- Format INR and percents the same way Seller Reports do (`Decimal` from API; UI formats).
- Percentages in API remain **fractions** (`0.379` = 37.9%), consistent with `ppc-analytics-v1`.
- No new chat protocol. Copilot keeps plan → execute → confirm → synthesize.
- Workspace dispatch: Copilot response may include `{ workspace: { type: "profit_model", id } }`; the Copilot page opens `/profit/{id}` in-product (panel or navigation), it does not iframe a random URL.

---

## 3. Backend architecture

### 3.1 Placement

Follow existing layers. Do **not** put formulas in Copilot, routes, or the synthesizer.

```text
HTTP / Copilot tools
        ↓
ProfitModelingService          CRUD, org scope, snapshot lifecycle
        ↓
ProfitCalculationService       Pure math. No I/O. No AI.
FeeCatalogService              Versioned assumption tables. No AI.
AdvertisingInputResolver       Maps STR / Business upload rollups → ad inputs (optional)
        ↓
ProfitSnapshot (persisted)  or  ProfitPreview (ephemeral)
        ↓
profit_evidence.py             Compact claims → EvidenceEnvelope
        ↓
ToolRegistry / Synthesis
```

Analyze, History, and Seller Reports keep calling their own services. Profit HTTP is additive under `/api/v1/profit/*`.

### 3.2 Services

#### `ProfitCalculationService`

**Responsibilities**

- Accept a fully specified `ProfitInputs` DTO (all money as `Decimal`, percents as fractions).
- Apply `profit-calc-v1` formulas.
- Return `ProfitOutputs` including line items, unknowns, and formula version.
- Refuse to emit a net profit number if required unit inputs are missing; return structured unknowns instead of `0`.

**Must not**

- Call OpenAI, Rainforest, SP-API, or the database
- Default missing COGS
- Treat listing score, BSR, or rating as cost
- Round-trip through floats for money
- Read Copilot conversation state

This is the analog of `ListingAnalysisV2Service` / `listing_rules_v2.py`.

#### `FeeCatalogService`

**Responsibilities**

- Given marketplace + category band + price (+ optional size/weight band), return **labeled** referral % and FBA amount or %.
- Stamp `fee_catalog_version` (e.g. `amazon-in-fba-v1`).
- Allow seller override: stored on the model, not silently mutating the catalog.

**Must not**

- Claim fees are live Amazon quotes
- Scrape Seller Central
- Hide that the source is an assumption table

V1 catalog can be a conservative, documented static table for `amazon.in` plus “seller override.” Completeness > fake precision.

#### `ProfitModelingService`

**Responsibilities**

- Org-scoped CRUD for profit models and scenarios
- Load latest snapshot (history-first for Copilot)
- Persist calculation snapshots (immutable once written, like `analysis_runs`)
- Orchestrate: inputs + catalog → `ProfitCalculationService` → snapshot
- Preview path: same calculation, no persist (slider UX)

**Must not**

- Contain formula implementations (delegate)
- Accept client-supplied `net_profit` as truth

#### `AdvertisingInputResolver` (thin)

**Responsibilities**

- Optional: from last `report_uploads` analysis payload, roll up spend/sales for an ASIN or state “ASIN not in report.”
- Output advertising **inputs**, not profit conclusions.
- Preserve existing rule: STR `HIGH_ACOS` is not a profit claim.

**Must not**

- Join Search Term and Business reports into a fake P&L without seller confirmation of period alignment
- Invent TACOS when total sales are absent

Existing `PPCAnalyticsService` / `BusinessAnalyticsService` stay the analytics engines. This resolver only adapts their structured output into profit **inputs**.

### 3.3 Formula contract (`profit-calc-v1`)

Frozen for V1 unless a new version string is introduced. All money INR `Decimal`.

**Unit economics (per unit sold)**

```text
amazon_fees_per_unit = referral_fee + fba_or_fulfillment_fee
operating_costs_per_unit = shipping + packaging + other
landed_cost_per_unit = cogs + amazon_fees_per_unit + operating_costs_per_unit

net_profit_before_ads = selling_price - landed_cost_per_unit
margin_before_ads     = net_profit_before_ads / selling_price     if selling_price > 0 else null
roi_on_cogs           = net_profit_before_ads / cogs              if cogs > 0 else null
```

**Advertising (period; attach to unit model only when period inputs exist)**

```text
acos  = ad_spend / ad_sales          if ad_sales > 0 else null
tacos = ad_spend / total_sales       if total_sales > 0 else null

# Break-even ACOS: ad spend as % of ad-attributed sales that drives
# contribution after product costs to zero.
# Using unit contribution before ads:
break_even_acos = net_profit_before_ads / selling_price
                = margin_before_ads
```

Break-even ACOS equals **pre-ads margin** under this definition (contribution available to spend on ads per rupee of selling price). Document this in UI: “You can spend up to {break_even_acos} of ad-attributed sales on ads before unit contribution hits zero, **assuming volume and other costs hold**.”

If the product owner later wants break-even on **TACOS** (ads vs total sales), that is a second named metric, not a silent rename.

**After-ads (only if units in period or ad spend can be allocated per unit)**

V1 recommended allocation:

- If `units_sold_in_period` provided: `ad_spend_per_unit = ad_spend / units`
- Else: after-ads unit profit is **unknown**; still report period ACOS/TACOS

```text
net_profit_after_ads = net_profit_before_ads - ad_spend_per_unit
```

Zero denominators → `null`, same as PPC analytics. Missing required inputs → claim kind `unknown`, not `0`.

**ROI definition (freeze)**

V1 ROI = `net_profit_before_ads / COGS` (inventory cash on the unit). Do not mix ad spend into the ROI denominator in V1. After-ads return can be a **separate** named metric later (`return_after_ads`) so Copilot cannot conflate them.

### 3.4 HTTP API (when implementation is approved)

Additive. Do not change Analyze / History / Reports / Bulk / Copilot contracts except: Copilot may register new tools and accept `profit_*` intents that are `out_of_scope` today.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/v1/profit/models` | Create model (asin, marketplace, inputs) |
| GET | `/api/v1/profit/models` | List for current org; filter `asin` |
| GET | `/api/v1/profit/models/{id}` | Model + latest snapshot |
| PATCH | `/api/v1/profit/models/{id}` | Update **inputs** only; does not rewrite old snapshots |
| POST | `/api/v1/profit/models/{id}/calculate` | Persist new snapshot |
| POST | `/api/v1/profit/preview` | Stateless calculate (sliders) |
| POST | `/api/v1/profit/models/{id}/scenarios` | Create scenario spec |
| POST | `/api/v1/profit/models/{id}/scenarios/{sid}/calculate` | Persist scenario snapshot |
| GET | `/api/v1/profit/fee-catalog` | Public assumption bands + version (no secrets) |

Copilot continues to use `/api/v1/copilot/*` and reaches profit **only** through ToolRegistry.

Provider cost: calculate/preview = **0** Rainforest/OpenAI. Seeding price via product fetch remains existing paid tools.

### 3.5 Suggested implementation slices (not this document’s job to build)

Architecture is Option B complete. If implementation is phased:

| Slice | Ships |
| --- | --- |
| 11C.1 | Engine + models + workspace P&L (no ads, no Copilot tools) |
| 11C.2 | Advertising inputs + ACOS/TACOS + after-ads composition. Spec: [milestone-11c2-architecture.md](milestone-11c2-architecture.md). Manual first. Do not change `profit-calc-v1`. |
| 11C.3 | Scenarios |
| 11C.4 | ToolRegistry + planner intents + Copilot dispatch |

Do not ship Copilot profit answers before 11C.1 snapshots exist.

---

## 4. Data model

### 4.1 Principles

Mirror listing persistence:

| Listing Intelligence | Profit Intelligence |
| --- | --- |
| `product_snapshots` | seller + catalog **inputs** on `profit_models` |
| `analysis_runs` + `listing_analysis_results` | `profit_snapshots` (immutable calculation) |
| `listing_score_version` | `profit_formula_version` + `fee_catalog_version` |
| History-first reuse | Latest complete snapshot for ASIN |

**Store** what a human or integration asserted, plus **frozen outputs** of a calculation version (audit / Copilot evidence).

**Calculate** every money output from inputs + versions. Never treat a UI-typed “margin 40%” as stored truth.

### 4.2 Proposed entities (migration only when implementation is approved)

Suggested revision name (from the 11 plan): `0005_profit_models`. Do **not** alter `0001`–`0004`.

All tables: `organization_id`, timestamps. Soft-delete optional on models (`deleted_at`), not on snapshots (immutable).

#### `profit_models`

Living worksheet for one org + ASIN + marketplace (V1 unique: org, asin, marketplace among non-deleted).

| Column | Notes |
| --- | --- |
| id | UUID PK |
| organization_id | FK, required |
| asin | Normalized, case-insensitive lookup |
| marketplace | Default `amazon.in` |
| currency | `INR` V1 |
| display_name | Optional |
| selling_price | Numeric/Decimal, nullable |
| selling_price_source | `seller` \| `product_snapshot` \| `unknown` |
| product_snapshot_id | Nullable FK if price seeded |
| cogs | Nullable |
| shipping_cost | Nullable |
| packaging_cost | Nullable |
| other_cost | Nullable |
| referral_fee_amount | Nullable override |
| referral_fee_rate | Nullable override |
| fba_fee_amount | Nullable override |
| fee_category_key | Catalog band the seller picked |
| ad_spend | Nullable, period |
| ad_sales | Nullable |
| total_sales | Nullable |
| units_in_period | Nullable |
| advertising_period_start / end | Nullable |
| report_upload_id | Nullable FK if ads seeded from upload |
| notes | Seller text; untrusted for math |
| created_at / updated_at / deleted_at | |

Inputs are **current draft**. Changing them does not mutate historical snapshots.

#### `profit_snapshots`

Immutable result of one calculate call.

| Column | Notes |
| --- | --- |
| id | UUID PK |
| organization_id | Denormalized for isolation |
| profit_model_id | FK |
| scenario_id | Nullable FK; null = baseline |
| status | `complete` \| `partial` (unknowns present) \| `failed` |
| profit_formula_version | e.g. `profit-calc-v1` |
| fee_catalog_version | e.g. `amazon-in-fba-v1` |
| inputs_json | Exact inputs used (JSON) |
| outputs_json | Exact outputs (JSON) |
| completeness | e.g. `{ unknown: ["cogs"] }` |
| calculated_at | |

Copilot `get_profit_snapshot` reads this row, not the draft model alone.

#### `profit_scenarios`

| Column | Notes |
| --- | --- |
| id | UUID PK |
| profit_model_id | FK |
| organization_id | Denormalized |
| name | “Price +10%” |
| spec_json | Deltas only: `price_pct`, `ad_spend_pct`, `conversion_pct`, optional absolute overrides |
| baseline_snapshot_id | FK, required |
| created_at | |

Scenario **results** live in `profit_snapshots` with `scenario_id` set. Recalculate if baseline or formula version changes; do not silently edit old scenario snapshots.

### 4.3 What is stored vs calculated

| Stored | Calculated (never independently stored as seller truth) |
| --- | --- |
| COGS, shipping, packaging, other | Net profit, margin, ROI |
| Price + source | Fee amounts derived from catalog × price |
| Seller fee **overrides** | Catalog default fees at calculate time (copied into snapshot inputs) |
| Ad spend / sales / units / period | ACOS, TACOS, break-even ACOS, after-ads profit |
| Scenario **spec** | Scenario output stack |
| Formula + catalog version on snapshot | — |

Fee catalog rows themselves are **code or a versioned seed table**, not seller data. Seller overrides are seller data.

### 4.4 What not to persist

- LLM explanations (those stay on `copilot_messages`)
- Rainforest product payloads (already on `product_snapshots`)
- Raw STR CSV bytes (already on `report_uploads` when DB is configured)
- Client-computed margins
- Cross-org caches

### 4.5 Linkage to existing tables

| Existing | Link |
| --- | --- |
| `organizations` | Every profit row |
| `product_snapshots` | Optional price seed |
| `analysis_runs` | Optional convenience “open listing” link only — **not** an input to profit math |
| `report_uploads` | Optional advertising seed |
| `copilot_conversations` | Optional `last_profit_model_id` in conversation context later — not required for V1 if Copilot tools look up by ASIN |

Do **not** write profit fields onto `listing_analysis_results`.

---

## 5. ToolRegistry integration

### 5.1 New tools (Copilot-callable only via registry)

| Tool | Cost | Confirm | Purpose |
| --- | --- | --- | --- |
| `list_profit_models` | none | No | History-first: models/snapshots for ASIN |
| `get_profit_snapshot` | none | No | Latest complete/partial snapshot claims |
| `calculate_profit` | none | No | Run engine on **stored model** or explicit seller-confirmed input payload from the **application**, never from free-form model JSON money fields |
| `run_profit_scenario` | none | No | Apply spec to baseline snapshot |

Do **not** register in 11C: Ads API fetch, SP-API fees, inventory, cash flow, “estimate COGS.”

`get_product` / `analyze_listing_v2` remain listing tools. Planner may use `get_product` only to **seed price** after existing confirmation rules — not as a profit calculator.

### 5.2 Tool contracts (conceptual)

**`list_profit_models`**

- In: `{ asin?, limit? }`
- Out: claims `models` (id, asin, updated_at, has_complete_snapshot, unknowns)

**`get_profit_snapshot`**

- In: `{ snapshot_id? , model_id? , asin? }` — validator binds latest complete for ASIN like History-first listing
- Out: profit claims (see §6)

**`calculate_profit`**

- In: `{ model_id }` only for Copilot. Arbitrary `{ cogs: 1 }` from the planner is **rejected**. Sellers change inputs in the workspace (HTTP PATCH), then Copilot recalculates the stored model.
- Out: new snapshot claims
- Partial snapshots allowed; `unknown` claims required

**`run_profit_scenario`**

- In: `{ model_id, spec }` where `spec` is a closed enum of delta fields (percentages as fractions). Validator rejects unknown keys and absolute “net_profit” fields.
- Out: scenario snapshot claims + `baseline_snapshot_id` + `volume_held_constant`

### 5.3 How the planner selects tools

Lift **profit** off the V1 `out_of_scope` list **only** for registered intents. Competitors / launch-TAM remain out of scope.

| Seller phrasing | Intent | Validated plan |
| --- | --- | --- |
| “Is B0… profitable?” / “What’s my margin?” | `explain_profit` | `list_profit_models` → `get_profit_snapshot`; if none, `profit_clarify` (empty tools) |
| “Recalculate profit for this ASIN” | `refresh_profit` | `calculate_profit` `{ model_id }` if model exists; else clarify |
| “What if price +10%?” / “PPC −20%” | `profit_scenario` | `get_profit_snapshot` then `run_profit_scenario` with parsed spec; if no baseline, clarify |
| “What’s my ACOS / TACOS / break-even ACOS?” | `explain_profit` | Same snapshot; if ads inputs missing, unknowns — do not call STR heuristics “unprofitable” |
| “Build a profitability model” | `open_profit_workspace` | Empty tools + workspace dispatch to new/empty model; no invented numbers |
| “Should I launch this niche?” | `out_of_scope` | Unchanged: not a profit snapshot question |

**History-first (profit analog)**

- Prefer saved snapshot over calculate
- Do not call `calculate_profit` unless the seller asked to refresh or inputs changed in-product
- Do not inherit a random other ASIN’s model
- `asin_required` when the message has no ASIN and conversation has no `last_asin` — same discipline as Analyze an ASIN

**Confirmation**

- Profit tools above are `none` cost → no confirmation
- If the plan also needs Rainforest price seed → existing confirm gate, existing nonce + plan hash
- The model still cannot pass `confirmed: true`

**Fallback**

- Planner LLM 503 → keyword map: profit/margin/ROI/ACOS/TACOS → `explain_profit` + history-first
- Unknown tool name → reject, no execute
- `calculate_profit` with inline money from the LLM → invalid

---

## 6. Evidence architecture

### 6.1 Envelope reuse

Use existing `EvidenceEnvelope` / `EvidenceClaim`. Do not create a parallel “ProfitEnvelope” type. Domain meaning lives in **claim keys** and `kind` / `source`.

Recommended `kind` usage:

| Kind | When |
| --- | --- |
| `seller_provided` | COGS, typed price, typed fees, typed ads |
| `observed` | Price from product snapshot; ads from STR rollup |
| `calculated` | Net profit, margin, ROI, ACOS, TACOS, break-even, scenario deltas |
| `unknown` | Required line missing; value `null` |
| `historical` | Citing a prior snapshot’s outputs |

`ai_inference` is **forbidden** on money keys.

`source` examples: `seller_input`, `product_snapshot:{id}`, `fee_catalog:amazon-in-fba-v1`, `profit-calc-v1`, `ppc-analytics-v1`, `report_upload:{id}`.

### 6.2 Supported claim keys (V1)

Identity and versions:

- `asin`, `marketplace`, `currency`
- `profit_model_id`, `snapshot_id`, `scenario_id`
- `profit_formula_version`, `fee_catalog_version`
- `calculated_at`
- `completeness` (list of unknown keys)

Inputs:

- `selling_price`, `cogs`, `referral_fee`, `fba_fee`, `shipping_cost`, `packaging_cost`, `other_cost`
- `amazon_fees` (sum, calculated)
- `ad_spend`, `ad_sales`, `total_sales`, `units_in_period`
- `volume_held_constant` (scenarios)

Outputs:

- `net_profit_before_ads`, `margin_before_ads`, `roi_on_cogs`
- `acos`, `tacos`, `break_even_acos`
- `ad_spend_per_unit`, `net_profit_after_ads` (only if allocatable)
- `scenario_spec`, `delta_net_profit_before_ads`, `delta_margin_before_ads`

Unknown handling: if `cogs` is missing, emit `cogs` kind `unknown` **and** `net_profit_before_ads` kind `unknown`. Do not omit the output key so synthesis invents it.

### 6.3 Example envelope (illustrative)

Facts the engine may emit (values rounded only in UI, not in claims):

| Key | Example value | Kind | Source |
| --- | --- | --- | --- |
| selling_price | `999` | seller_provided | seller_input |
| cogs | `350` | seller_provided | seller_input |
| referral_fee | `80` | calculated | fee_catalog:amazon-in-fba-v1 |
| fba_fee | `190` | calculated | fee_catalog:amazon-in-fba-v1 |
| shipping_cost | `0` | unknown | — |
| amazon_fees | `270` | calculated | profit-calc-v1 |
| net_profit_before_ads | `379` | calculated | profit-calc-v1 |
| margin_before_ads | `0.379` | calculated | profit-calc-v1 |
| roi_on_cogs | `1.083` | calculated | profit-calc-v1 |
| acos | `0.32` | calculated | profit-calc-v1 |
| break_even_acos | `0.379` | calculated | profit-calc-v1 |

If shipping is unknown, `net_profit_before_ads` must either (a) stay `unknown` until shipping is provided, or (b) V1 product rule: treat missing optional opex as `0` **only if** the snapshot stamps `assumed_zero: ["shipping_cost"]` as an explicit claim. **Recommendation:** shipping/packaging/other default to `0` with `kind: seller_provided` only after the seller saves the model with those fields present (including explicit 0). Unopened models do not assume 0.

### 6.4 Citation requirements

Synthesis validator (11B.4) must be extended, not weakened:

- Money tokens in prose (`₹`, `%`, “margin”, “ACOS”) require a matching claim key
- “Profitable” / “unprofitable” requires `net_profit_before_ads` or `net_profit_after_ads` that is not `unknown`
- “After ads” language requires after-ads claims
- Scenario language requires `scenario_spec` + delta claims
- Ranking / conversion / BSR language still rejected unless those keys exist (they will not on profit envelopes)
- Existing listing citation rules unchanged

Template fallback on synthesis 503: print claim values with labels, no new arithmetic.

### 6.5 Compact evidence (like listing)

`profit_evidence.py` (when built) should emit compact claims from `outputs_json` / `inputs_json`, not the full JSON blob as one claim. Same lesson as listing-analysis evidence: synthesis needs keys, not a dump.

---

## 7. AI architecture

Two LLMs remain. Neither calculates.

### 7.1 Planner

**Question it answers:** Which **profit capability** is needed, and which **registered tools**?

- New intents only from a closed enum (`explain_profit`, `refresh_profit`, `profit_scenario`, `open_profit_workspace`, `profit_clarify`) plus existing listing intents
- Catalog includes the four profit tools
- Must not put rupee amounts into tool arguments except scenario **percent deltas** parsed from the user message (e.g. 10% → `0.10`)
- Must not call listing analysis to answer profit
- Prompt rule: missing COGS → `profit_clarify`, not `calculate_profit` with guessed costs

### 7.2 Synthesis

**Question it answers:** What does this snapshot **mean** for the seller?

Allowed: observations from claims; analysis that maps lines (“FBA is the largest cost after COGS”); recommendations that are **decision support** tied to claims (“If break-even ACOS is 37.9% and observed ACOS is 32%, ads are below break-even **on this snapshot**”).

Forbidden:

- Recalculating or rounding to a different profit number
- Estimating unknown costs
- Inventing fee tables, GST, or “typical India FBA”
- Inventing conversion lift from a price increase
- Saying “ads are fine” because no STR was uploaded
- Mixing listing score with margin

### 7.3 Context builder

Compact profit context: `last_profit_model_id`, `last_asin`, unknown keys — not raw `inputs_json` dumps, not full conversation.

### 7.4 Failure modes

| Failure | Behavior |
| --- | --- |
| Planner 503 | Fallback map → history-first snapshot or clarify |
| Calculate error | Tool envelope error claim; no synthesis of numbers |
| Synthesis 503 | Template from claims |
| Missing snapshot | Clarify + workspace link |

AI failure degrades **fluency**, not the ledger.

---

## 8. External integration strategy

11C does **not** implement Amazon APIs. Design the seams so future ingest replaces **input collection**, not formulas — same pattern as Seller Reports.

```text
Today                         Future
─────                         ──────
Seller types price            SP-API listing / pricing
Seller types COGS             Seller still types (Amazon does not know COGS)
Fee catalog v1                SP-API Fees Estimate → observed fees
Seller types ad spend         Amazon Ads API → same ad input DTO
STR / Business upload         Same normalized rows, resolver fills DTO
```

### 8.1 Amazon SP-API

| Feed | Maps to | Notes |
| --- | --- | --- |
| Catalog / listings / pricing | `selling_price` observed | Confirm/cost policy later; still not COGS |
| FBA inventory / fees estimate | `fba_fee`, `referral_fee` observed | Replaces catalog defaults; stamp source `sp_api_fees` |
| Orders / finances | Period `total_sales`, units | TACOS and period P&L |

Boundary: a future `AmazonEconomicsProvider` returns **normalized fee/price DTOs**. `ProfitCalculationService` stays pure.

### 8.2 Amazon Ads API

Maps to `ad_spend`, `ad_sales`, campaign rollups. Replaces upload for advertising **inputs**. Does not replace `profit-calc-v1`. Does not authorize bid writes.

### 8.3 Seller Central reports

Already parsed. 11C may **read** last analysis payload via `AdvertisingInputResolver`. It must not require a new parser. Search Term + Business Report remain unjoined unless the seller confirms they share a period.

### 8.4 Rainforest

Optional price seed only, existing paid tool + confirmation. Never used as COGS or fee oracle.

---

## 9. Security and trust

Financial inputs are more sensitive than listing copy.

| Control | Rule |
| --- | --- |
| Ownership | All profit rows scoped by `organization_id`. Local default org until auth exists. |
| Isolation | Org A model id → 404 for org B (same as conversations and History) |
| Copilot path | Tools only; no direct SQL from the LLM |
| Logs | Do **not** log COGS, prices, or snapshots (match Seller Reports: no row values) |
| Audit | Immutable `profit_snapshots`; Copilot `tool_executions` store evidence ids, not a second ledger |
| Traceability | Every calculated claim cites formula + catalog version; every input cites seller or integration source |
| Writes to Amazon | None |
| Client trust | Ignore client-sent `net_profit`; ignore planner-sent `confirmed` |
| Exports | Out of 11C; if added later, org-scoped and explicit |

When multi-user auth lands, profit models inherit the same org membership as History. Do not build a parallel permission system.

---

## 10. Scalability roadmap (Seller Business Intelligence)

11C must look like the **second engine** in a family, not a one-off.

```text
Intelligence engines (deterministic)
  ListingEngine        (exists)
  ProfitEngine         (11C)
  AdvertisingEngine    (exists as PPC analytics; later Ads API ingest)
  InventoryEngine      (future)
  CashFlowEngine       (future)
  HealthScoreEngine    (future: composed from the above)

Each engine:
  inputs → versioned calculate → snapshot → EvidenceEnvelope → Copilot tool

Workspaces:
  /                  Analyze
  /profit            Profit
  /reports           Ads/traffic uploads
  /inventory         later
  /copilot           orchestrates all
```

**Composition rules**

- Health score **consumes snapshots** (listing findings, profit completeness, ACOS vs break-even). It does not reimplement math.
- Copilot gains tools per engine; it does not gain a new “agent.”
- 11D diagnostics should rank **existing** listing findings + uploads; after 11C it may also rank “profit model incomplete” or “ACOS above break-even on snapshot {id}” as evidence-backed items — still no invented inventory risk.

Avoid: a Copilot-only profit path with no workspace; a Profit UI that bypasses ToolRegistry for Copilot; per-feature micro-formulas in prompts.

---

## 11. Risks and unknowns

| Risk | Why it hurts | Mitigation in architecture |
| --- | --- | --- |
| Missing COGS | Every consumer seller question | Completeness + clarify; never estimate |
| Fee table ≠ Amazon | Legal/trust; wrong decisions | Label assumptions; version catalog; UI disclaimer; SP-API later |
| Amazon fee complexity (size, category, GST, easy-ship vs FBA) | V1 table will be wrong for many SKUs | Seller override required; partial snapshots; do not hide bands |
| Period mismatch (STR vs Business vs unit costs) | Fake TACOS | Do not auto-join reports; unknowns |
| Data freshness | Stale price or ads | `calculated_at` + sources; refresh is explicit |
| Seller input burden | Empty workspace = unused product | Seed price from snapshot; explicit zeros; Copilot clarify |
| Conversion scenarios | Elasticity is unknown | Volume held constant unless seller supplies CVR delta |
| Mixing listing quality with profit | False confidence | Separate nav, separate claims, planner routing |
| INR / rounding | Drift vs UI | `Decimal`; UI formats; tests on golden fixtures |
| Treating STR HIGH_ACOS as loss | Already documented as non-P&L | Resolver copies spend/sales only |
| Scope creep to cash flow | Slips 11C | Account rollup and inventory out of V1 |

---

## 12. Product owner questions

Resolve before implementation. Architecture can proceed with defaults noted; product should confirm.

### Marketplace and money

1. **Which marketplace first?** Architecture default: `amazon.in` / INR only (matches ASI V1).
2. **GST / tax:** Exclude from V1 formulas unless product wants a dedicated tax line (seller-provided). Do not bury GST inside referral fee.
3. **Easy Ship vs FBA vs seller-fulfilled:** V1 catalog = FBA-oriented bands + fulfillment override field?

### Inputs

4. **Must sellers manually enter COGS?** Architecture default: **yes**. No Amazon-derived COGS.
5. **May missing shipping/packaging default to 0?** Recommendation: only after explicit save, including 0.
6. **How should Amazon fees be sourced in V1?** Static labeled catalog + seller override. Not live Amazon.
7. **Category mapping:** Seller picks a fee band vs. we guess from Rainforest category? **Do not guess.**
8. **Should selling price auto-seed from latest product snapshot?** Optional, observed, editable.

### Advertising

9. **Attach last STR automatically by ASIN?** Or always manual spend/sales?
10. **TACOS in V1** if Business Report is absent — omit vs. ask for total sales?
11. **Break-even ACOS definition:** Confirm = pre-ads margin (this spec). Any other definition needs a different name.

### Scenarios and persistence

12. **Should scenarios be saved** or preview-only? Recommendation: save specs + snapshots (audit + Copilot).
13. **Should profit workspace be ASIN-level or account-level?** Recommendation: **ASIN-level V1**; account P&L is a later sum of snapshots (dangerous if incomplete).
14. **One model per ASIN per marketplace**, or many named models (launch vs live)? Recommendation: one living model + many snapshots/scenarios.

### UX and Copilot

15. **Nav item “Profit”** vs only Copilot dispatch? Recommendation: **both**.
16. **Language:** “assumption” vs “estimate” vs “Amazon fees”? Recommendation: **assumption** until SP-API.
17. **May Copilot say “profitable”** when shipping is unknown? Recommendation: **no** unless optional opex explicitly zeroed.

### Sequencing

18. **Ship 11C.1 workspace before Copilot tools?** Recommendation: **yes** — evidence must exist before synthesis.
19. **Is Option B (ads + scenarios) required in the first implementation drop**, or architecture-now / phased-build?

---

## 13. Testing strategy (when implementation is approved)

CI remains: mock providers, SQLite, **zero** live Rainforest/OpenAI in tests.

| Layer | Tests |
| --- | --- |
| `ProfitCalculationService` | Golden fixtures: margin 37.9% style cases; null denominators; missing COGS → unknown not 0; Decimal |
| Fee catalog | Same price + band → stable fees; override wins; version stamp |
| HTTP | Org isolation; client-supplied net_profit ignored; preview = calculate math |
| Tools | Planner cannot inject COGS; history-first snapshot; scenario spec whitelist |
| Synthesis | Cannot invent ₹; cannot call ads “fine” without claims; scenario without elasticity |
| Frontend | Display-only; API values rendered; no local formula |

---

## 14. Acceptance criteria (for a future implementation go-ahead)

- UI numbers **equal** `ProfitCalculationService` output for the same inputs
- AI path **cannot** change those numbers
- Missing COGS → unknown profit, not a guessed margin
- Fee lines labeled with catalog version or “seller override”
- ACOS/TACOS only when inputs exist; STR heuristics never relabeled as P&L
- Scenarios show volume-held-constant unless conversion delta provided
- Copilot profit answers cite snapshot claim keys
- Org isolation on every profit route and tool
- No Amazon write APIs
- No new provider calls on calculate
- Listing Copilot behavior unchanged for non-profit intents

---

## 15. Out of scope (explicit)

- Code, migrations, fee-table files, Copilot prompt edits in this milestone document
- SP-API, Ads API, GST engine, inventory, cash flow, health score
- Multi-currency, `amazon.com` fee tables
- RAG over invoices
- Autonomous repricing or bid changes
- Using listing V2 score as a profit input

---

## 16. Document control

| Field | Value |
| --- | --- |
| Architecture status | Draft for review — not an implementation ticket |
| Frozen ASI rules | Deterministic money; EvidenceEnvelope; ToolRegistry; hybrid planner; seller-owned confirmation; no Amazon writes |
| First workspace type | `profit_model` (reserved in 11B, unused until 11C ships) |
| Formula version (proposed) | `profit-calc-v1` |
| Fee catalog version (proposed) | `amazon-in-fba-v1` |

**Decision required from product:** answers in §12, especially marketplace, COGS entry, fee source, ASIN vs account, and whether Copilot tools wait until the workspace engine exists.

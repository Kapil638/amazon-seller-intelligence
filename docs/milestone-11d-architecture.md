# Milestone 11D — Skill Architecture Foundation

**Date:** 22 August 2026  
**Role:** Principal Architect  
**Status:** Architecture only. **Not approved for implementation** until explicit go-ahead.  
**Scope:** Define how a future Skill layer fits into ASI. No code. No migrations. No Skill Registry implementation. No LangGraph, CrewAI, or agents.  
**Depends on (frozen):**  
- 11A Tool Foundation (`834a79b`) — ToolRegistry, EvidenceEnvelope, budgets, confirmation policy  
- 11B Seller Copilot Foundation (`c3b75d4`) — conversation, planner, orchestrator, confirmation gate, synthesis, Copilot UI  
- 11C.1 Profit Intelligence Foundation (`171e668`) — `profit-calc-v1`, models, snapshots, `/profit`  
- 11C.2 Advertising Intelligence Foundation (`0a21a88`) — `ads-calc-v1`, impact composition, ADR 0001  
**Audience:** Product, backend, AI engineering.

Companions: [milestone-11b-architecture.md](milestone-11b-architecture.md), [milestone-11c-architecture.md](milestone-11c-architecture.md), [milestone-11c2-architecture.md](milestone-11c2-architecture.md), [milestone-11/milestone-11c2-architecture-checkpoint.md](milestone-11/milestone-11c2-architecture-checkpoint.md), [adr/0001-advertising-intelligence-domain-boundary.md](adr/0001-advertising-intelligence-domain-boundary.md), [milestone-11/copilot-tool-layer.md](milestone-11/copilot-tool-layer.md), [milestone-11/milestone-11d1-copilot-domain-tools.md](milestone-11/milestone-11d1-copilot-domain-tools.md).

**This document does not implement Skills.** It does not create Skill tables, Skill Registry, workflows, or Amazon write paths. **11D.1** separately registered profit and advertising Copilot tools; Skills still do not exist.

---

## Numbering note

The August 2026 Copilot plan named **11D — Business Diagnostic V0** (“What should I work on today?” over listing History + uploads). That product idea is **not cancelled**.

This document reuses the 11D slot for the missing layer those diagnostics would sit in: **Skill Architecture Foundation**.

| Name in this document | Meaning |
| --- | --- |
| Milestone 11D | Skill Architecture Foundation (this spec) |
| Business Diagnostic | A **future Skill**, not a competing architecture, and not in this milestone |

Do not implement Business Diagnostic or Skill Registry as part of 11D. Profit/ads Copilot tools shipped in **11D.1** (tools only).

---

## 1. Executive Summary

ASI can already **score listings**, **calculate unit profit**, **calculate advertising efficiency**, and **explain evidence in Copilot**. What it cannot do is treat a seller **business goal** as a first-class object.

Today Copilot maps a question onto **tools**. That is correct for “Why is my listing score low?” It is incomplete for “Help me improve this ASIN.” Improving an ASIN is not one tool. It is a **business capability** that may need listing, profit, and advertising evidence together — without mixing those engines.

**Target layering (compatible with the frozen stack):**

```text
Seller Goal
  ↓
Skill Layer                 (business capabilities — later)
  ↓
Tool Registry               (technical capabilities — 11A, shipped)
  ↓
Deterministic engines       (listing, profit, advertising)
  ↓
EvidenceEnvelope            (trust boundary — 11A, shipped)
  ↓
Seller Copilot explanation  (11B, shipped)
```

**Verdict: compatible.** This is the same stack already drawn in the 11C.2 architecture checkpoint. 11D does not replace Copilot, ToolRegistry, EvidenceEnvelope, or any intelligence engine. It names the layer **above tools**.

| Layer | Owns | Must not own |
| --- | --- | --- |
| Seller Copilot | Conversation, plan/skill selection, confirmation, explanation | Formulas, Amazon writes, minting claims |
| Skill (future) | Business goal, required tools, required evidence, guardrails, output contract | Handlers, money math, listing scores |
| ToolRegistry | The only execution path into engines | Business strategy, synthesis prose |
| Intelligence engines | Deterministic numbers (`ListingAnalysisV2`, `profit-calc-v1`, `ads-calc-v1`) | Chat, Skills, Amazon mutations |
| EvidenceEnvelope | Cited claims with kind/source | Recommendations that are not in claims |

**Skills = business capabilities. Tools = technical capabilities.**

No LangGraph. No CrewAI. No autonomous agents. ASI already has controlled orchestration: plan → validate → budget → confirm → execute → synthesize. A Skill is a **named, versioned policy** on that pipeline, not a new runtime.

---

## 2. Product Vision

### 2.1 Why a Skill layer is needed

**Current state.** The seller asks **questions**. Copilot V1 understands listing questions, selects 11A tools, and explains envelopes. Profit and advertising questions are still `out_of_scope` until Copilot tools wrap those engines (designed as 11C.4, not this milestone).

| Current seller utterance | What ASI does |
| --- | --- |
| “Why is my listing score low?” | History-first tools → listing evidence → explanation |
| “Analyze B0TEST0001” | Confirm-gated product/listing tools |
| “Is this ASIN profitable?” | Copilot refuses (no profit tools yet). Workspace exists at `/profit` |
| “Help me improve this ASIN.” | No first-class object. Planner would have to invent a tool list |

**Future state.** The seller expresses **business goals**. Copilot still talks to the seller. A Skill declares *which capabilities, tools, evidence, and guardrails* that goal requires.

| Future seller utterance | What should happen |
| --- | --- |
| “Help me improve this ASIN.” | Select a Skill (or ask which goal). Run only that Skill’s tools. Explain only that Skill’s required evidence. Recommend. Never write to Amazon. |
| “Improve profitability of B0…” | Profit Improvement Skill → profit + advertising (+ listing if declared) tools |
| “What should I work on today?” | Business Diagnostic Skill → existing History + optional uploads; rank only cited findings |

Without a Skill layer, every new goal either:

1. **Bloats the planner** with ad-hoc tool lists and overlapping intents, or  
2. **Collapses domains** (profit engine starts “optimizing ads”; Copilot starts calculating money).

A Skill is the contract that keeps **goals** above **tools** and **tools** above **engines**.

### 2.2 What 11D is and is not

| 11D is | 11D is not |
| --- | --- |
| Architecture for a Skill layer above ToolRegistry | Implementation of any Skill |
| Definition of Skill vs Tool | Skill database tables or Skill Registry code |
| Mapping from Playbook capabilities onto existing ASI domains | The Amazon Seller Skill Playbook as a product surface |
| Confirmation that Copilot + tools + evidence already suffice | LangGraph, CrewAI, multi-agent, or autonomous loops |
| A freeze of guardrails Skills inherit | Amazon writes, campaign changes, spend |

The Amazon Seller Skill Playbook (22 operational skills, reviewed 19 August 2026) remains **product strategy only**. It describes the long-term seller operating system. It must not be copied into ASI as workflows, agents, or tables in this milestone.

### 2.3 Compatibility with the current ASI picture

Current shipped picture:

```text
Seller Copilot
      ↓
EvidenceEnvelope
      ↓
Listing Intelligence     Profit Intelligence     Advertising Intelligence
      ↓                        ↓                         ↓
Listing Engine           Profit Engine            Advertising Engine
```

Skills do **not** sit beside engines. They sit **above Copilot’s tool calls**:

```text
Seller Copilot  →  Skill (policy)  →  ToolRegistry  →  engines  →  EvidenceEnvelope  →  Copilot explanation
```

Engines stay independent. Copilot stays the only seller-facing intelligence UI for chat. Workspaces (`/`, `/history`, `/reports`, `/profit`) stay expert surfaces. Skills do not get their own Amazon-write console.

---

## 3. Skill Definition

### 3.1 What a Skill is

A **Skill** is a versioned **business capability**: a seller goal plus the tools, evidence, and guardrails required to pursue it inside ASI.

A Skill is **not**:

- A Python handler  
- An LLM agent with a private tool loop  
- An intelligence engine  
- A Copilot conversation  
- A Playbook SOP pasted into a prompt  

A Skill **declares**. The existing application **executes** (orchestrator + ToolRegistry) and **explains** (synthesis).

### 3.2 Required attributes

Every Skill definition must include:

| Attribute | Purpose |
| --- | --- |
| `skill_id` | Stable identifier (`profit_improvement`) |
| `name` | Seller-facing name |
| `business_goal` | One sentence: the outcome the seller asked for |
| `description` | What the Skill does and refuses |
| `required_capabilities` | ASI domains it may use (listing, profit, advertising, seller_reports, …) |
| `required_tools` | Exact ToolRegistry names. Unknown names make the Skill invalid |
| `required_evidence` | Envelope `tool_name`s and/or claim keys that must exist before synthesis may recommend |
| `guardrails` | Hard refusals (no Amazon writes, no invented money, confirmation policy) |
| `expected_output` | Shape of Copilot output (observations / analysis / recommendations / unknowns / workspace dispatch) |
| `version` | `skill_version` string (example: `profit-improvement-v1`) |

Optional later (not required to freeze the architecture): `input_slots` (ASIN, marketplace, period), `max_tools`, `confirmation_policy` override (may only **tighten** 11A/11B policy, never loosen).

### 3.3 Example — Profit Improvement Skill

```text
skill_id:            profit_improvement
name:                Profit Improvement
business_goal:       Improve ASIN profitability under stated inputs
description:         Compose unit economics and period advertising evidence.
                     Recommend. Do not change price, fees, or campaigns.
required_capabilities:
  - Profit Intelligence
  - Advertising Intelligence
  - Listing Intelligence          # optional context; does not own P&L
required_tools:                   # future Copilot tools wrapping existing engines
  - list_profit_models            # not registered
  - get_profit_snapshot           # 11D.1
  - get_advertising_snapshot      # 11D.1
  - get_saved_report              # 11A — listing context only
required_evidence:
  - tool_name=profit_calculation  (or get_profit_snapshot)
  - tool_name=advertising_calculation | advertising_impact  when ads claims are used
guardrails:
  - no Amazon writes
  - no recalculation of profit-calc-v1 or ads-calc-v1
  - unknown stays unknown
  - after-ads is composition, not a rewrite of unit P&L
  - period ads vs unit profit grain must be cited, never presented as matched monthly books
expected_output:     evidence-backed findings + labeled recommendations + unknowns
version:             profit-improvement-v1
```

This Skill **cannot ship** until a Skill layer exists. Profit and advertising **tools** now exist (11D.1). 11D still does not implement the Skill.

### 3.4 Example — Listing Optimization Skill

```text
skill_id:            listing_optimization
name:                Listing Optimization
business_goal:       Improve listing construction quality for an ASIN
required_capabilities:
  - Listing Intelligence
required_tools:
  - list_saved_reports
  - get_saved_report
  - analyze_listing_v2            # confirm-gated if live Amazon lookup
  - compare_saved_analyses        # optional, when two reports exist
required_evidence:
  - listing quality score, findings, weaknesses from History or analyze_listing_v2
guardrails:
  - do not invent conversion, rank, or search volume
  - do not write title/bullets to Amazon
  - History-first before live lookup
expected_output:     ranked construction issues citing existing findings
version:             listing-optimization-v1
```

This Skill is **architecturally implementable later** against tools that already exist. It is still **not implemented in 11D**.

### 3.5 Example — Advertising Optimization Skill

```text
skill_id:            advertising_optimization
name:                Advertising Optimization
business_goal:       Improve advertising efficiency for an ASIN without managing campaigns
required_capabilities:
  - Advertising Intelligence
  - Profit Intelligence           # break-even ACOS from cited margin
  - Seller Reports                # later: STR diagnostics as observed, not P&L
required_tools:                   # future
  - get_advertising_snapshot
  - get_profit_snapshot           # break-even ACOS citation
  - (later) get_search_term_report_summary
guardrails:
  - HIGH_ACOS from STR is not a P&L verdict (ADR 0001)
  - no bid writes, no campaign edits, no spend
  - TACOS unknown when total sales missing
expected_output:     efficiency findings + recommendations + unknowns
version:             advertising-optimization-v1
```

---

## 4. Skill vs Tool Boundary

### 4.1 Tools are technical capabilities

A **Tool** is a registered, schema-validated, budgeted wrapper around an existing service. It returns an `EvidenceEnvelope`. It does not decide *why* the seller asked.

| Tool | Technical job |
| --- | --- |
| `get_saved_report` | Load a historical listing analysis for this org |
| `analyze_listing_v2` | Score a live or cached product through Listing Intelligence V2 |
| `get_profit_snapshot` (11D.1) | Return the latest immutable profit snapshot claims |
| `analyze_profitability` (11D.1) | Ask `ProfitModelingService` to persist `profit-calc-v1` — still Python math |
| `get_advertising_snapshot` (11D.1) | Return the latest ads snapshot claims |
| `analyze_advertising_impact` (11D.1) | Compose stored snapshots through `AdvertisingImpactService` |

Tools **may** cost Rainforest or OpenAI credits. The **application** owns `confirmed=True`. The model never grants permission.

### 4.2 Skills are business capabilities

A **Skill** is a seller outcome. It names tools; it does not execute them.

| Skill | Business job |
| --- | --- |
| Listing Optimization | Improve construction quality |
| Profit Improvement | Improve ASIN profitability |
| Advertising Optimization | Improve ads efficiency / after-ads contribution |
| Business Diagnostic | Rank what to work on today from **existing** evidence |
| Product Research | Later — observed catalog distributions, not invented TAM |
| Growth Planning | Later — compose listing + profit + ads + (future) inventory |
| Seller Business Intelligence | Later — rollups and reporting, not a second Copilot |

### 4.3 Why they are different

```text
get_profit_snapshot()
  = load cited unit economics
  = technical capability
  = one envelope

“Improve profitability of my product”
  = business capability
  = may need profit + advertising + listing tools
  = many envelopes
  = one Skill policy
  = still zero Amazon writes
```

If Skills owned formulas, `profit-calc-v1` and `ads-calc-v1` would fork inside prompts.  
If Tools owned goals, ToolRegistry would become a strategy engine.  
If Copilot owned both, every new Playbook skill would rewrite planner prompts.

**Boundary test:** if it has a handler or a formula, it is not a Skill. If it has a business goal and a tool list, it is not a Tool.

### 4.4 What must not leak

| Must not happen | Why |
| --- | --- |
| Skill calls `ProfitCalculationService` directly | Bypasses ToolRegistry, budgets, org isolation, evidence |
| Tool contains “improve profitability” logic | Mixes strategy into a technical façade |
| Skill minting `EvidenceClaim`s | Breaks the 11A trust boundary |
| Skill setting `confirmed=True` | Model/skill must never grant spend permission |
| Engine depending on a Skill | Domains stay independent; Skills are consumers |

---

## 5. Skill Registry Concept

Architecture only. **Do not implement.**

### 5.1 Role

A future **Skill Registry** is a catalog of Skill definitions, analogous to ToolRegistry but **without handlers**.

```text
ToolRegistry     list_tools() / execute()     handlers private
Skill Registry   list_skills() / get_skill()  declarations only
```

Relationship:

- Skills **reference** tools by `name`.  
- ToolRegistry **does not** reference Skills.  
- Adding a Skill does not change ToolRegistry, EvidenceEnvelope, or engines.  
- Adding a Tool does not require a Skill.  
- A Skill that lists an unregistered tool is **invalid** at validation time (application-owned, same spirit as plan validator).

### 5.2 Possible attributes

Aligned with ToolRegistry’s public catalog (`name`, `description`, `input_schema`, `cost`, `confirmation_required`):

| Skill field | Tool analog |
| --- | --- |
| `skill_id` / `name` | `name` |
| `description` | `description` |
| `business_goal` | (none — tools are not goals) |
| `required_tools` | subset of registered names |
| `required_evidence` | (none on tools — tools *produce* evidence) |
| `guardrails` | confirmation/budget live on tools; Skills may only tighten |
| `version` | prompt/plan versions already exist (`copilot-plan-v1`, `profit-calc-v1`) |
| `expected_output` | synthesis schema already exists; Skill may constrain sections |

**Do not** store Python callables on a Skill. **Do not** store Amazon credentials. **Do not** store free-form agent graphs.

### 5.3 Storage (when later implemented — not now)

Prefer **versioned code/config** for V1 Skills (same pattern as tool registration and `copilot-plan-v1`), not a `skills` table. Optional persistence later is an audit log of *which skill_id/version ran on a turn*, not a CMS for live-editing production Skills without review.

No migration in 11D. Existing tables (`copilot_conversations`, `profit_models`, `advertising_models`, …) stay unchanged.

### 5.4 Catalog hash

11B already hashes the **tool catalog** onto the Plan. A future Skill-aware Plan should also record `skill_id`, `skill_version`, and a **skill catalog hash** so confirmation nonces cannot silently swap Skills.

---

## 6. Skill Execution Model

### 6.1 Future workflow

Seller: **“Improve my ASIN profitability.”**

```text
1. Copilot understands the goal          (planner + application validator)
2. Skill is selected                     (bounded catalog, not a free agent)
3. Skill identifies required tools       (declaration, not a hunt)
4. Tools execute                         (existing orchestrator + ToolRegistry)
5. Evidence is collected                 (EvidenceEnvelope per tool)
6. Required-evidence gate                (application: missing → unknown / workspace)
7. Copilot explains findings             (existing synthesis + citation validator)
```

**Validated.** This is the 11B pipeline with one insertion: **Skill selection + required-evidence gate**. It is not a new orchestrator.

### 6.2 Mapping onto the shipped Copilot pipeline

Current 11B:

```text
User message
  → Conversation / context
  → Planner (optional LLM propose)
  → Plan validator (History-first, reject, fallback)
  → Orchestrator + BudgetTracker + Confirmation Gate
  → ToolRegistry.execute
  → EvidenceEnvelope(s)
  → Synthesis + citation validator
  → Seller message
```

Future (Skill-aware), **same boxes**:

```text
User message
  → Conversation / context
  → Planner proposes intent and/or skill_id from Skill catalog
  → Plan validator binds a Skill, rewrites tool_calls to Skill.required_tools
  → Orchestrator + BudgetTracker + Confirmation Gate     (unchanged contract)
  → ToolRegistry.execute                                 (unchanged)
  → EvidenceEnvelope(s)                                  (unchanged)
  → Required-evidence check for that Skill
  → Synthesis constrained by Skill.expected_output
  → Seller message
```

The planner still **does not execute**. The Skill still **does not execute**. The orchestrator still **only** calls ToolRegistry.

### 6.3 Selection is application-owned

Mirror the hybrid planner:

| Allowed | Forbidden |
| --- | --- |
| Deterministic goal → Skill map | Unbounded LLM choosing arbitrary tools outside the Skill |
| Optional LLM **proposes** `skill_id` | LLM executing a Skill privately |
| Validator reject / clarify / rewrite | Multi-Skill loops until “the agent is satisfied” |
| One Skill per turn in V1 | Parallel conflicting Skills mutating shared state |

If the goal is ambiguous (“help me grow”), Copilot **clarifies** (existing `clarify` intent) instead of launching three Skills.

### 6.4 Missing tools, missing evidence

| Situation | Behavior |
| --- | --- |
| Skill lists a tool not in ToolRegistry | Skill is invalid; do not run; do not fake |
| Tool exists but envelope lacks required claims | Synthesis must mark those findings **unknown**; may dispatch `/profit` or Analyze |
| Profit Improvement before profit Copilot tools exist | Skill is **not callable**; Copilot stays `out_of_scope` or deep-links the workspace |
| Confirmation required | Existing 11B nonce gate; Skill cannot skip it |

### 6.5 Stop conditions (no agent loop)

Reuse 11A `BudgetTracker`: `max_tool_rounds`, `max_tools_per_turn`. A Skill may set **lower** caps, never higher. There is no “keep calling tools until the goal is met.” Recommendations are the end of the turn, not Amazon actions.

---

## 7. Copilot Integration

### 7.1 Copilot remains the user-facing intelligence layer

Sellers still talk to **Copilot**. They do not open a Skill IDE. Expert screens remain Analyze, History, Reports, Bulk, Profit.

```text
Today:     Copilot  →  Tool Registry  →  engines  →  evidence  →  explanation

Future:    Copilot  →  Skill selection  →  Tools  →  engines  →  evidence  →  explanation
```

Skills are **capability orchestration policy**. Copilot is still conversation, confirmation UX, citations, and workspace links.

### 7.2 What Copilot V1 must not change in 11D

11D is architecture. When Skills are later implemented, **do not** rewrite:

- EvidenceEnvelope schema  
- ToolRegistry.execute contract  
- BudgetTracker semantics  
- Confirmation nonce ownership  
- Citation validator (“no claim in envelopes → cannot assert”)  
- `profit-calc-v1` / `ads-calc-v1`  
- ADR 0001 domain boundary  

Allowed later (implementation milestone, not now): additive Plan fields (`skill_id`, `skill_version`), additive planner intents/goals, additive synthesis constraints, additive catalog `list_skills()`.

### 7.3 Planner intents vs Skills

Today’s intents (`explain_listing_score`, `analyze_asin`, `out_of_scope`, …) are **question routers**. Skills are **goal routers**.

A later mapping can be 1:1 (analyze_asin → Listing Optimization) or many:1 (several listing questions share one Skill). Do not explode a Skill per utterance. Do not delete intents on day one; bind them.

`out_of_scope` remains required until a Skill **and** its tools exist.

---

## 8. Evidence Integration

### 8.1 EvidenceEnvelope remains the trust boundary

Unchanged from 11A:

- Every tool returns an envelope with `evidence_id`, `tool_name`, `organization_id`, `produced_at`, `claims`.  
- Claim `kind`: `observed` · `calculated` · `historical` · `seller_provided` · `ai_inference` · `unknown`.  
- Money and scores are never `ai_inference`.  
- Synthesis cites envelopes; it does not invent actions or rupees.

**A Skill cannot make claims.** It can only **require** claims.

### 8.2 Required evidence

Before a Skill may emit **recommendations**, the application checks `required_evidence`.

Example — Profit Improvement:

| Need | Source envelope | If missing |
| --- | --- | --- |
| Unit profit / margin / ROI | profit snapshot (`profit-calc-v1`) | Unknown profitability; dispatch `/profit/[id]` |
| ACOS / TACOS / ROAS / after-ads | advertising snapshot + impact | Unknown ads efficiency; do not copy ACOS into TACOS |
| Listing construction issues | saved report or `analyze_listing_v2` | Omit listing recommendations; do not invent weaknesses |

Example — Listing Optimization:

| Need | If missing |
| --- | --- |
| Listing findings / score | History-first; else confirm live analyze; else clarify |

### 8.3 Cross-domain evidence is composition, not a merge engine

A Skill may **display** listing + profit + ads envelopes in one Copilot turn. It must not:

- Average ACOS with listing score  
- Fold ad spend into `other_cost`  
- Treat unit profit `calculated_at` and ads `period_start/end` as one fiscal month  

Cite snapshot ids and `as_of` / period on every cross-domain sentence. This is the 11C.2 grain freeze, applied at Skill level.

### 8.4 No Skill-specific envelope type

Do not create `SkillEvidenceEnvelope`. Reuse `EvidenceEnvelope`. Optional later: a turn-level **bundle** (list of envelope ids + `skill_id`) for audit. That is packaging, not a new trust object.

---

## 9. Guardrails

Skills inherit ASI’s security philosophy. They do not get a looser lane.

### 9.1 Allowed

- Analyze existing evidence  
- Explain envelopes  
- Recommend changes **for the seller to apply**  
- Dispatch expert workspaces (`/profit`, History, Reports)  
- Ask for confirmation before paid provider tools  

### 9.2 Not allowed

- Modify Amazon listings  
- Change campaigns, bids, budgets, or keywords  
- Spend money (Ads, inventory PO, or ASI provider credits without the 11B gate)  
- Perform Amazon writes (SP-API, Ads API mutate, Seller Central automation)  
- Execute destructive actions (hard-delete org data, overwrite snapshots)  
- Recalculate listing scores, profit, or ACOS/TACOS/ROAS in the Skill or in the LLM  
- Treat missing evidence as zero or as “healthy”  

Playbook split to inherit later: **diagnostics vs human-approved Amazon action**. ASI Copilot stays on the diagnostic side until a separate, explicit approval product exists. That product is not 11D.

### 9.3 Confirmation and tenancy

| Control | Rule |
| --- | --- |
| Org isolation | Skills run only with `current_organization_id()`; other-org 404 via existing repos |
| Confirmation | Skill cannot pass `confirmed=True`; only the confirm API after seller action |
| Client trust | Same as profit/ads: ignore client-submitted calculated money |
| Snapshots | Append-only history remains reproducible; Skills read snapshots, they do not rewrite them |

---

## 10. Intelligence Domain Mapping

Domains stay **independent products**. Skills **consume** them through tools. Overlap is **composition of envelopes**, not shared formulas.

| Future Skill | Uses (domains) | Must not own |
| --- | --- | --- |
| Listing Optimization | Listing Intelligence (V2 scores, findings, History) | Profit, ACOS, bids |
| Profit Improvement | Profit Intelligence; Advertising Intelligence (impact); Listing as context | `profit-calc-v1` / `ads-calc-v1` formulas; campaign manager |
| Advertising Optimization | Advertising Intelligence; Profit (cited break-even ACOS); Seller Reports later (STR as observed) | Listing construction; treating `HIGH_ACOS` as loss |
| Business Diagnostic | Listing History + last uploads (Seller Reports) | New Amazon calls; invented inventory risk |
| Product Research | Future research tools on **observed** listings | TAM, search volume, share |
| Growth Planning | Compose listing + profit + ads + future inventory | A single “growth score” engine that hides unknowns |
| Seller Business Intelligence | Future rollups over snapshots and reports | Replacing Copilot or workspaces |

**Independence rule:** deleting a Skill must not change an engine. Deleting an engine must invalidate Skills that required its tools — at catalog validation — not at runtime guesswork.

Seller Reports remains **operational PPC diagnostics**. Advertising Intelligence remains **period economics**. Profit remains **unit economics before ads**. Listing remains **construction**. Copilot remains **explanation**. Skills remain **goals above tools**. That is ADR 0001, extended from advertising to the Skill layer.

---

## 11. Amazon Skill Playbook Mapping

Source: Amazon Skills Operating Playbook (strategy only, 19 August 2026) plus ASI product families named for 11D. **Do not implement.**

| Seller capability (Playbook / vision) | Future Skill | Existing ASI foundation | Future tools required (not in 11D) |
| --- | --- | --- | --- |
| Listing & content (optimizer, Rufus-ready copy) | Listing Optimization | Listing Intelligence V2, History, 11A listing tools, Copilot explain | None for V1 of this Skill; optional later: listing AI tool (confirm-gated, still not scores) |
| Unit economics / “do I make money?” | Profit Improvement | `profit-calc-v1`, profit models/snapshots, `/profit` | `list_profit_models`, `get_profit_snapshot`, `calculate_profit` (wrap existing APIs) |
| Budget / TACOS / after-ads contribution | Profit Improvement and/or Advertising Optimization | `ads-calc-v1`, impact service, ADR 0001 | `get_advertising_snapshot`, `calculate_advertising` |
| PPC optimization (bids, harvest, negatives) | Advertising Optimization (diagnostics only) | Seller Reports STR analytics | Wrap report analytics as **observed** tools; **never** bid write tools in V1 |
| Reporting dashboards | Seller Business Intelligence | Snapshots + History + uploads | Read aggregations; not a dashboard fork inside Copilot |
| “What should I work on today?” | Business Diagnostic | Listing findings + last PPC/business upload | Deterministic ranker **behind a tool**; AI explains ranks only |
| Product research / launch | Product Research | Competitor discovery exists as expert UI | Confirm-gated search/product tools; no invented volume |
| Growth planning | Growth Planning | Composition of the above | Only after listing + profit + ads tools exist |
| HTML scrape vs approved Amazon action | (guardrail, not a Skill) | No Amazon writes | Human-approved upload/write product — later, not Copilot-autonomous |
| Inventory / restock | Inventory Intelligence (later Skill) | None | New engine first, then tools, then Skill |
| Forecasting | Forecasting Skill (later) | None | New engine first |
| Review intelligence | Review Intelligence (later) | Not a V1 engine | New engine first |
| Market intelligence | Market Intelligence (later) | Partial competitor UI | Observed distributions only |

**Ordering constraint:** Playbook item → Skill **only after** (1) a deterministic engine or existing service owns the facts and (2) a ToolRegistry wrapper exists. Skills never skip those steps.

---

## 12. Scalability Analysis

New seller domains (inventory, forecasting, product research, reviews, market) follow one additive path:

```text
1. Deterministic engine + snapshots (domain product)
2. EvidenceEnvelope via a new ToolRegistry entry
3. Expert workspace if sellers must edit inputs
4. Optional Copilot tool (11A contract)
5. Optional Skill that lists those tools
```

**What does not change** when a Skill is added:

| Frozen surface | Why it stays stable |
| --- | --- |
| Copilot core (conversation, confirm UX, synthesis loop) | Skill is a Plan field + validator policy |
| ToolRegistry | Skills only *name* tools |
| EvidenceEnvelope | Skills only *require* claims |
| Existing engines | Skills never fork formulas |
| Org isolation / 404 | Tools and repos already enforce |

**What may be added:** Skill catalog entries, planner goal enum values, synthesis section constraints, workspace deep-links.

**What must not be added for scale:** a second orchestrator, per-Skill agent, per-Skill evidence type, or a graph framework “because we have more Skills.”

---

## 13. Risks

These are real. 11D does not solve them in code. Flag them before any implementation milestone.

| Risk | Why it matters |
| --- | --- |
| Skill overlap | Profit Improvement vs Advertising Optimization both touch after-ads. Unclear selection → duplicate tool calls or conflicting advice. |
| Skill selection complexity | LLM picks the “impressive” Skill; validator must bound the catalog. |
| Skill versioning | Recommendations must cite `skill_version` the same way money cites `profit-calc-v1`. |
| Evidence requirements too weak | Skill recommends without profit snapshot → invented P&L. |
| Evidence requirements too strict | Skill never speaks because listing+profit+ads are rarely all present; need partial + unknown. |
| Conflicting Skills | One turn says “raise price,” another “cut price for ads.” V1: one Skill per turn. |
| Guardrail drift | A “Growth” Skill quietly wants bid writes “just to simulate.” Refuse at catalog review. |
| Planner bloat | Treating every Playbook SOP as a Skill without an engine. |
| Premature Skill implementation | Profit Improvement before Copilot profit tools exist. |
| Framework temptation | LangGraph/CrewAI as a shortcut around the existing orchestrator. |
| Numbering confusion | Plan’s 11D Diagnostic vs this 11D architecture. Diagnostic is a Skill later. |

---

## 14. Open Questions (Product Owner)

Answer before any Skill implementation milestone. Architecture can proceed without them; **code cannot**.

1. **Should Copilot automatically select Skills?** Architecture recommendation: hybrid — deterministic map + optional LLM propose + application validator (same as 11B planner). Confirm product appetite.  
2. **Should users manually select Skills?** Chips on `/copilot` (“Improve listing” / “Improve profit”) are compatible. A Skill picker as the primary UX is not required for V1.  
3. **Can multiple Skills run together?** Recommendation: **no** for V1 (one Skill per turn). Sequential turns may run different Skills.  
4. **How are conflicting Skills handled?** Do not merge. Clarify, or run one Skill and state unknowns for the other domain. Never average money.  
5. **How are Skill versions managed?** Same as engines and plans: immutable version strings; turn records the version; no silent swap after confirm.  
6. **Who owns Skill definitions?** Recommendation: engineering + product review in repo (like tools and ADRs), not seller-editable production Skills.  
7. **When do profit/ads Copilot tools ship relative to the first Skill?** Listing Optimization can go first (tools exist). Profit Improvement must wait for wrappers.  
8. **Is Business Diagnostic the first Skill?** It matches the old plan 11D user story and needs no Amazon writes. Product should choose first Skill explicitly.  
9. **May a Skill dispatch a workspace without running tools?** Yes — e.g. no profit snapshot → open `/profit`. That is still not a claim.  
10. **Do Skills appear in the Copilot activity timeline?** Recommendation: yes, as `skill_id` + version alongside tool executions, when implemented.

---

## 15. Architecture Recommendation

### 15.1 Decision

**Adopt the Skill layer as a future policy catalog above ToolRegistry.**

Do **not** implement it in this milestone. Do **not** introduce LangGraph, CrewAI, or autonomous agents. Do **not** modify 11A–11C.2 engines, Copilot core, or EvidenceEnvelope.

### 15.2 Compatibility verdict

| Target model step | Fits frozen ASI? |
| --- | --- |
| Seller Goal | Yes — Copilot already takes goals; Skills name them |
| Skill Layer | Yes — new catalog; no engine rewrite |
| Tool Registry | Yes — unchanged execute path |
| Deterministic engines | Yes — listing / profit / ads stay independent |
| EvidenceEnvelope | Yes — Skills require, never mint |
| Seller Copilot explanation | Yes — existing synthesis + citations |

The 11C.2 checkpoint already froze this diagram as **future** layering. 11D specifies it without building it.

### 15.3 Principles (confirmed)

1. **Skills are above Tools.**  
2. **Tools remain deterministic capabilities** (handlers + envelopes + budgets).  
3. **EvidenceEnvelope remains the trust boundary.**  
4. **Copilot remains the user-facing intelligence layer.**  
5. **Intelligence domains remain independent** (listing ≠ profit ≠ advertising ≠ seller reports).  
6. **No autonomous agents are required.**  
7. **No LangGraph or CrewAI is required.** Controlled orchestration already exists.  
8. **Existing ASI architecture is not broken** and must not be redesigned to “make room” for Skills.

### 15.4 Why not a new agent framework

ASI’s risk is **untrusted language** and **unbounded provider spend**, not missing graph syntax.

| Need | Already owned by |
| --- | --- |
| Route a turn | Planner + validator |
| Limit calls | BudgetTracker |
| Paid Amazon lookup | Confirmation gate |
| Numbers | Python engines |
| Trust | EvidenceEnvelope + citation validator |
| Seller language | Synthesis LLM only after evidence |

LangGraph/CrewAI would add a second orchestrator, a second permission story, and a path to tool loops that 11A explicitly forbids. They are **out of scope for ASI Skill architecture**.

### 15.5 Suggested later sequence (not 11D work)

1. Approve this architecture.  
2. Keep shipping domain foundations and Copilot **tools** (profit/ads wrappers) without Skills.  
3. Implement Skill Registry as **declarations + validator** only.  
4. First Skill: either Listing Optimization (tools exist) or Business Diagnostic (old plan 11D story).  
5. Profit Improvement / Advertising Optimization only after those tools exist.

### 15.6 Stop before

- Skill tables, Skill Registry code, Skill UI  
- Copilot prompt rewrites “to be agentic”  
- Any Amazon write path  
- Merging profit and advertising engines  
- Treating the Playbook as an implementation backlog inside 11D  

---

## Document control

| Item | Value |
| --- | --- |
| Document | Milestone 11D — Skill Architecture Foundation |
| Author role | Principal Architect |
| Implementation | **None** |
| Next required act | Product answers in §14; then an explicit implementation milestone if desired |
| Related freeze | ADR 0001; 11A tool layer; 11B Copilot; 11C.1/11C.2 engines |

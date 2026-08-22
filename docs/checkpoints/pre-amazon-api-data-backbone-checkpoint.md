# Amazon Seller Intelligence — Pre Amazon Data Backbone Checkpoint

**Date:** 22 August 2026  
**Checkpoint name:** `pre-amazon-api-data-backbone`  
**Product freeze (Milestone 11D.1):** `e3311821053b930c84301d85c9569bb8e2fa1b3e`  
**Previous published HEAD:** `0a21a88` — Milestone 11C.2 (`origin/main` at freeze start)

Skill implementation is intentionally paused at Milestone 11D.1 while ASI builds the Amazon-connected data backbone. After SP-API / Ads API foundations are mature, Skill implementation resumes from this checkpoint.

This archive is documentation only. It does not configure runtime. It does not implement SP-API, Ads API, Skills, agents, or Amazon writes.

---

## 1. Checkpoint purpose

Preserve the exact ASI architecture and implementation state **before** SP-API / Ads API integration begins, so a future engineer or coding assistant can:

1. Know what is already frozen (tools, evidence, Copilot, listing/profit/ads engines).
2. Avoid regressing History-first lookup, unknown-stays-unknown, or ADR 0001.
3. Resume Skill work from Milestone 11D architecture after connected Amazon data is mature — without reconstructing history from chat.

---

## 2. Date

22 August 2026.

---

## 3. Git HEAD

| Ref | Hash | Meaning |
| --- | --- | --- |
| Implementation freeze | `e3311821053b930c84301d85c9569bb8e2fa1b3e` | `feat(copilot): complete milestone 11D.1 copilot domain tools` |
| Parent | `0a21a88a62ad7fe88b5744fc1e2dec46c8aabb38` | Milestone 11C.2; was `origin/main` before 11D.1 |
| Annotated tag | `pre-amazon-api-data-backbone` | Applied to the documentation commit that adds this archive |

Do not treat this JSON/markdown as a runtime config file.

---

## 4. Git history summary

```text
e331182  feat(copilot): complete milestone 11D.1 copilot domain tools
0a21a88  feat(advertising): complete milestone 11C.2 advertising intelligence foundation  (tag: v0.11C.2)
171e668  feat(profit): complete milestone 11C.1 profit intelligence foundation
c3b75d4  feat(copilot): complete milestone 11B seller copilot foundation
834a79b  feat(copilot): complete milestone 11A intelligence tool layer
c0706cb  feat: persist history, custom scoring, and client PDF reports
7b5a050  feat: complete listing intelligence v2 and image intelligence
901fa4b  feat: complete listing intelligence v2 through AI milestone 8C
07f45e8  refine frontend into a premium SaaS intelligence UI
7c12db9  feat: establish Amazon Seller Intelligence MVP
```

Remote at freeze start: `origin` → `https://github.com/Kapil638/amazon-seller-intelligence.git`.  
`main` was **up to date** with `origin/main` at `0a21a88` (11A–11C.2 already published). 11D.1 was local-only until this freeze.

---

## 5. Completed milestone matrix

| Milestone | Status | Commit | Canonical docs |
| --- | --- | --- | --- |
| MVP / listing / image / history / scoring / PDF | Complete | `7c12db9` … `c0706cb` | `docs/listing-intelligence-v2.md`, `docs/image-media-intelligence.md`, `docs/report-lifecycle.md`, `docs/custom-scoring-profiles.md`, `docs/client-pdf-reports.md` |
| 11A Tool Foundation | Complete | `834a79b` | `docs/milestone-11/copilot-tool-layer.md`, `milestone-11a-checkpoint.md`, `milestone-11a-report.md` |
| 11B Seller Copilot | Complete | `c3b75d4` | `docs/milestone-11b-architecture.md`, `milestone-11b2`–`11b5` slice docs, `listing-analysis-evidence.md`, `copilot-history-first-lookup.md` |
| 11C Profit Intelligence (parent) | Architecture | — | `docs/milestone-11c-architecture.md` |
| 11C.1 Profit Intelligence Foundation | Complete | `171e668` | `docs/milestone-11/milestone-11c1-profit-foundation.md` |
| 11C.2 Advertising Intelligence Foundation | Complete | `0a21a88` | `docs/milestone-11c2-architecture.md`, `milestone-11c2-advertising-foundation.md`, `milestone-11c2-architecture-checkpoint.md` |
| ADR 0001 | Accepted | with 11C.2 | `docs/adr/0001-advertising-intelligence-domain-boundary.md` |
| 11D Skill Architecture Foundation | Architecture only | recorded in `e331182` | `docs/milestone-11d-architecture.md` |
| **11D.1 Copilot Domain Tool Enablement** | **Complete** | **`e331182`** | `docs/milestone-11/milestone-11d1-copilot-domain-tools.md` |

Not started: 11C.3 scenarios, Skill Registry, first Skill, 11E, SP-API, Ads API, Amazon writes.

---

## 6. Current system architecture

```text
Seller message
  → Hybrid Planner (proposes only)
  → Plan validator
  → Orchestrator
  → BudgetTracker / Confirmation Gate
  → ToolRegistry.execute()          ← only Copilot path into services
  → EvidenceEnvelope[]
  → Synthesis + citation validation
  → Seller-facing answer
```

```text
Seller Copilot
  → ToolRegistry
  → Deterministic intelligence engines (listing, profit-calc-v1, ads-calc-v1, impact compose)
  → EvidenceEnvelope
  → Copilot explanation
```

Future (not implemented):

```text
Seller Goal → Skill Layer → ToolRegistry → engines → EvidenceEnvelope → Copilot
```

Workspaces (`/analyze`, `/history`, `/profit`, `/reports`, `/bulk`) remain expert surfaces. Copilot explains evidence; it does not own formulas.

---

## 7. Current data sources

| Domain | Sources today | Storage |
| --- | --- | --- |
| Listing / marketplace | History-first saved analyses; Rainforest; mock; manual product; experimental Amazon public provider | Analysis history / report snapshots |
| Profit | Seller-provided unit inputs (price, COGS, fees, operating costs) | `profit_models` + immutable `profit_snapshots` |
| Advertising | Seller-provided period worksheet (spend, ad sales, total sales, units, dates) | `advertising_models` + immutable `advertising_snapshots` |
| Seller Reports | Uploaded STR / business reports | Upload artifacts + deterministic analytics |
| Interpretation | OpenAI for language / planning / synthesis; never money or listing scores | Prompt traces only; scores stay in Python |

Rainforest remains valuable for **external marketplace and competitor** intelligence after SP-API arrives. Uploads are not to be deleted when APIs arrive. COGS stays seller-private; Amazon does not supply it.

---

## 8. Current ToolRegistry tools

All Copilot execution goes through `ToolRegistry.execute()`. Catalog contract: `{name, description, input_schema, cost, confirmation_required}`. Handlers are private.

| Name | Wraps | Cost | Confirm |
| --- | --- | --- | --- |
| `list_saved_reports` | `AnalysisHistoryService.list_reports` | none | no |
| `get_saved_report` | `AnalysisHistoryService.get_report` | none | no |
| `analyze_listing_v2` | ProductService → Listing Intelligence V2 | Rainforest product | first paid / further gated |
| `get_product` | `ProductService.fetch_product` | Rainforest product | gated |
| `get_profit_snapshot` | Latest profit snapshot (read-only) | none | no |
| `analyze_profitability` | `ProfitModelingService.calculate` (`profit-calc-v1`) | none | no |
| `get_advertising_snapshot` | Latest ads snapshot (read-only; no worksheet create) | none | no |
| `analyze_advertising_impact` | `AdvertisingImpactService.compose` on stored snapshots | none | no |

Tools are technical capabilities. They do not understand business goals. They are not Skills.

---

## 9. Current Copilot architecture

- Conversations, messages, pending confirmations
- ConversationService + compact context
- Hybrid planner (LLM proposal or fallback rules) + application validator
- Orchestrator + confirmation nonce (server-granted `confirmed=True` only)
- Synthesis + citation validator + template fallback if the LLM fails
- Copilot UI at `/copilot`

**Planner may propose. Application validates. Planner never executes tools. LLM never grants confirmation.**

Intents at freeze: `explain_listing_score`, `summarize_report`, `list_history`, `analyze_asin`, `what_changed`, `explain_profit`, `explain_advertising_impact`, `out_of_scope`, `clarify`.

Competitors, campaign PPC management, and product launch remain `out_of_scope`.

### Preserve: rich listing evidence

Listing envelopes include `listing_quality_score`, `section_scores`, `findings`, `weaknesses`, and deterministic recommendations. Unsupported conversion / rank / PPC claims are rejected unless evidence exists.

### Preserve: History-first ASIN lookup

1. Normalize ASIN.  
2. Search saved History for this organization.  
3. If a complete report exists → use it; no provider lookup; no unnecessary confirmation.  
4. Live Amazon lookup only if no suitable report exists or the seller explicitly refreshes.

---

## 10. Listing Intelligence status

| Layer | State |
| --- | --- |
| Engine | Available (V2 listing quality; V1 legacy) |
| Snapshots | Available (History) |
| Copilot tools | Available |
| AI | Optional language on V2 evidence; does not replace scores |

---

## 11. Profit Intelligence status

| Layer | State |
| --- | --- |
| Engine | Available — `profit-calc-v1` in `profit_rules.py` via `ProfitCalculationService` |
| Snapshots | Available — immutable `profit_snapshots` |
| Workspace | `/profit` |
| Copilot tools | Available (11D.1) |

Formulas (Decimal; missing stays unknown):

```text
amazon_fees            = referral_fee + fba_fee
operating_costs        = shipping + packaging + other
landed_cost            = cogs + amazon_fees + operating_costs
net_profit_before_ads  = selling_price - landed_cost
margin_before_ads      = net_profit_before_ads / selling_price
roi_on_cogs            = net_profit_before_ads / cogs
```

COGS is seller-provided. AI must never invent COGS. Copilot does not calculate money.

---

## 12. Advertising Intelligence status

| Layer | State |
| --- | --- |
| Engine | Available — `ads-calc-v1` + `AdvertisingImpactService` |
| Snapshots | Available — immutable `advertising_snapshots` |
| Workspace | Advertising section on `/profit/[id]` |
| Copilot tools | Available (11D.1) |
| Boundary | ADR 0001 |

```text
ACOS  = ad_spend / ad_sales     when ad_sales > 0, else unknown
TACOS = ad_spend / total_sales  when total_sales > 0, else unknown
ROAS  = ad_sales / ad_spend     when ad_spend > 0, else unknown

ad_spend_per_unit      = ad_spend / units
net_profit_after_ads   = net_profit_before_ads - ad_spend_per_unit
break_even_acos        = margin_before_ads
```

Advertising is **period-based**. Profit V1 is **unit-based**. They may be composed; they must not be presented as matching monthly books. Ads must not rewrite `profit-calc-v1`. Ad spend must not fold into `other_cost`. Seller Reports `HIGH_ACOS` is not a P&L verdict. No campaign writes.

---

## 13. Evidence architecture

`EvidenceEnvelope` is the universal trust boundary. Schema at freeze is unchanged.

Required fields: `evidence_id`, `tool_name`, `organization_id`, `produced_at`, `claims`.

Claim kinds: `observed`, `calculated`, `historical`, `seller_provided`, `ai_inference`, `unknown`.

Money, scores, ACOS/TACOS/ROAS, and profit are never `ai_inference`. Unknown is never coerced to zero. Historical snapshots are immutable.

---

## 14. Security / guardrails

- Organization isolation on repositories; other-org reads surface as not found (404 at HTTP boundaries).
- Copilot tool inputs ignore client `organization_id` and client-calculated money.
- Frontend does not compute profit, ACOS, TACOS, ROAS, or after-ads profit.
- Confirmation is server-owned. Model JSON cannot set `confirmed=True`.
- No Amazon write path. No authentication/login yet (default development organization).
- Tests force mock providers; live Rainforest/OpenAI are not used in pytest.

---

## 15. Skill Architecture status

**Architecture approved** in `docs/milestone-11d-architecture.md`.  
**Implementation intentionally paused.**

Skills = business capabilities. Tools = technical capabilities.

Skills must not contain formulas, call services directly, mint `EvidenceClaim`s, set `confirmed=True`, or perform Amazon writes.

No Skill Registry, Skill tables, Skill UI, LangGraph, CrewAI, or autonomous agents.

Recommended later: versioned code/config Skill definitions — not a database CMS for V1.

Future Skill examples: Listing Optimization, Profit Improvement, Advertising Optimization, Business Diagnostic, Product Research, Growth Planning, Seller Business Intelligence.

---

## 16. Explicitly deferred functionality

- Amazon SP-API (seller-owned operational/account data)
- Amazon Ads API (seller-owned advertising data)
- OAuth / account connection / sync / canonical connected data model
- Skill Registry and any Skill implementation
- LangGraph, CrewAI, agent loops
- Amazon writes (listings, bids, campaigns)
- 11C.3 profit/ads scenarios as a product slice
- The August 2026 plan’s “11D Business Diagnostic V0” as a shipped feature (it is a **future Skill**)

---

## 17. Known current limitations

- India marketplace (`amazon.in`) / INR only
- Profit and advertising worksheets are primarily **manual seller input**
- No SP-API or Ads API freshness
- No user login; single default organization in development
- Copilot does not optimize, write, or run Skills
- Listing AI does not use Claude
- Bulk due diligence is mock-provider guarded
- Grain mismatch: unit P&L vs period ads must stay cited, never blended into one “monthly P&L”

---

## 18. Amazon API integration strategy context

Intended future data backbone (not implemented here):

| Source | Role |
| --- | --- |
| SP-API | Seller-owned Amazon operational/account data |
| Ads API | Seller-owned Amazon advertising data |
| Rainforest | External marketplace / competitor intelligence |
| Seller uploads | Manual / fallback / historical operational reports |
| Seller-entered inputs | Private business data Amazon does not know (especially COGS) |

These sources should eventually normalize into ASI’s trusted data model and flow into engines **behind ToolRegistry**, still emitting `EvidenceEnvelope`s. Do not let API payloads become Copilot claims without an adapter. Do not replace History, uploads, or seller COGS.

See [post-data-backbone-resume-plan.md](post-data-backbone-resume-plan.md).

---

## 19. Resume point after API work

After SP-API / Ads API and normalized data foundations are mature:

1. Re-read this checkpoint and `docs/milestone-11d-architecture.md`.
2. Confirm listing / profit / advertising tools still go through ToolRegistry and still return EvidenceEnvelopes.
3. Follow [post-data-backbone-resume-plan.md](post-data-backbone-resume-plan.md).
4. Resume **Skill layer** from approved Milestone 11D architecture.
5. Implement the first approved Skill (not in this checkpoint).

Do not start Skills until connected data provenance, org isolation, and unknown-handling are verified.

---

## 20. Frozen principles

Unless an explicit future ADR changes them:

1. Deterministic Python owns facts and calculations.
2. AI owns language, planning proposals, and explanation.
3. ToolRegistry is the technical execution boundary.
4. EvidenceEnvelope is the trust boundary.
5. Historical snapshots are immutable.
6. Missing data remains unknown.
7. AI must not invent money, COGS, ACOS, TACOS, ROAS, listing scores, rankings, conversion rates, or search volume.
8. No Amazon write actions.
9. Human confirmation is required where provider/API cost or future action policy requires it.
10. Skills, when implemented later, sit above tools.
11. Intelligence domains remain independent (listing ≠ profit ≠ advertising ≠ seller-report diagnostics).
12. No LangGraph / CrewAI / autonomous agent architecture is currently required.

Machine-readable companion: [pre-amazon-api-data-backbone-state.json](pre-amazon-api-data-backbone-state.json) (documentation only).

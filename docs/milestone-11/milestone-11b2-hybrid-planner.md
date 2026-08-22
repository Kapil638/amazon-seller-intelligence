# Milestone 11B.2 — Hybrid Planner

**Date:** 21 August 2026  
**Status:** Implemented. Stops at a validated Plan.  
**Depends on:** 11A ToolRegistry (`834a79b`), 11B.1 Conversation Foundation  
**Tests:** `uv run pytest` — **396 passed**. No live Rainforest. No live OpenAI.

Architecture: [../milestone-11b-architecture.md](../milestone-11b-architecture.md). Tool layer: [copilot-tool-layer.md](copilot-tool-layer.md).

---

## What 11B.2 is

The hybrid planner turns a seller message plus compact conversation context into a **versioned Plan**. The application owns validation. An optional LLM may **propose** intent and tool calls. Nothing is executed.

```text
User message
  ↓
Conversation Manager
  ↓
Context Builder
  ↓
Planner (optional LLM propose)
  ↓
Plan Validator (History-first rewrite / reject / fallback)
  ↓
Validated Plan
```

**Stop here.** Tool execution, confirmation nonce, synthesis, and Copilot UI are **not** in this milestone.

---

## Non-negotiable rules (enforced)

| Rule | How |
| --- | --- |
| Planner does not execute | No `ToolRegistry.execute()`, no ProductService, no listing scoring, no SQL in the planner package |
| ToolRegistry unchanged | `execute()`, EvidenceEnvelope, BudgetTracker, and tool contracts were not modified. Additive: `get_input_schema(name)` |
| No new migrations | Plan JSON is stored on an existing **system** message `structured_payload` |
| Model cannot grant permission | `confirmed` / `budget` / `handler` stripped; `product` blob on `analyze_listing_v2` rejected |
| Tests stay offline | SQLite path skips the LLM proposer; pytest uses fallback rules + injected fakes |

---

## Package

```text
apps/api/app/copilot/planner/
    __init__.py
    schemas.py      PlannerRequest, PlannerProposal, Plan
    prompts.py      copilot_plan (never reuse for synthesis)
    validator.py    extractors, fallback map, History-first rewrite
    service.py      PlannerService, optional AIProvider proposer
```

---

## Request

`PlannerRequest`:

| Field | Meaning |
| --- | --- |
| `user_message` | Current seller utterance |
| `conversation_id` | Existing 11B.1 conversation |
| `compact_context` | Context Builder slots (not raw history) |
| `available_tools` | Catalog from `list_tools()`; empty means fill from registry |

HTTP body is only `{ "user_message" }`. Extra keys such as `organization_id` are ignored.

---

## Plan object (`copilot-plan-v1`)

| Field | Meaning |
| --- | --- |
| `plan_id` / `plan_version` / `turn_id` | Traceability |
| `conversation_id` / `organization_id` | Org from `current_organization_id()`, never from the model |
| `intent` | Bounded enum (see below) |
| `planner_model` / `planner_prompt_version` | Null when fallback rules |
| `tool_calls` | Approved `{ name, arguments }` |
| `rejected_calls` | Dropped proposals + reason |
| `validation_status` | `accepted` · `rewritten` · `rejected` |
| `source` | `planner_llm` · `fallback_rules` · `rewritten_history_first` |
| `catalog_hash` / `plan_hash` | Audit + future nonce confirm |
| `needs_confirmation` / `confirm_summary` | Set when a paid tool is on the Plan; **confirm API not built** |
| `budget_snapshot` | Policy numbers only; no execution |

Intents: `explain_listing_score` · `summarize_report` · `list_history` · `analyze_asin` · `what_changed` · `out_of_scope` · `clarify`

---

## Routing behavior

Deterministic extractors always run first: ASIN, pasted `report_id`, conversation `last_asin` / `last_report_id`.

| Seller message | Typical Plan |
| --- | --- |
| “Why is my score low?” + ASIN in context/text | `explain_listing_score` → `list_saved_reports` (and `get_saved_report` if `last_report_id`) |
| LLM proposes `analyze_listing_v2` for explain | Validator **rewrites** to History tools |
| “Analyze B0TEST0001” | `analyze_asin` → `analyze_listing_v2`, `needs_confirmation=true` |
| “Compare with competitors” / PPC / profit | `out_of_scope`, empty `tool_calls` |
| No ASIN / report | `clarify`, empty `tool_calls` |
| Planner LLM down / invalid JSON | Fallback map; product still usable |

`compare_saved_analyses` is **not registered**. `what_changed` plans `list_saved_reports` only.

---

## API

```text
POST /api/v1/copilot/conversations/{id}/plan
```

Example:

```bash
curl -X POST "http://localhost:8000/api/v1/copilot/conversations/{id}/plan" \
  -H "Content-Type: application/json" \
  -d '{"user_message":"Why is my listing score low for B0TEST0001?"}'
```

Persists: user message (visible), system message with plan JSON (not shown as a chat bubble), updated slots (`last_asin`, `previous_intent`, …).

Still **not** implemented: `POST .../messages` chat turn, synthesis, `/copilot` UI.

**11B.3** adds `POST .../execute` and `POST .../confirm` (evidence only).

---

## LLM wiring

Prompt version: `copilot_plan`. One propose call maximum per turn.

`get_planner_service()` attaches `AIProviderPlannerProposer` only when the database URL is **not** SQLite. Pytest uses `DATABASE_URL=sqlite://`, so CI never live-calls OpenAI. Inject a fake proposer in unit tests. Failures fall back to rules.

---

## Explicitly not in 11B.2

- Tool orchestration / `execute()`
- Seller confirmation nonce
- Synthesis / seller-facing answers
- Copilot UI
- RAG, agents, Amazon writes
- New Alembic revisions
- Changes to Analyze / History / Reports / Bulk

## Next

**11B.3** is implemented — see [milestone-11b3-tool-orchestration.md](milestone-11b3-tool-orchestration.md).

**11B.4** — synthesis from envelopes, citation validator, template fallback.

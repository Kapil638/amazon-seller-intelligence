# Milestone 11B.3 — Tool Orchestration + Confirmation Gate

**Date:** 21 August 2026  
**Status:** Implemented. Stops at `EvidenceEnvelope[]`.  
**Depends on:** 11A ToolRegistry (`834a79b`), 11B.1 Conversation Foundation, 11B.2 Hybrid Planner  
**Tests:** `uv run pytest` — **411 passed**. No live Rainforest. No live OpenAI.

Architecture: [../milestone-11b-architecture.md](../milestone-11b-architecture.md). Planner: [milestone-11b2-hybrid-planner.md](milestone-11b2-hybrid-planner.md). Tool layer: [copilot-tool-layer.md](copilot-tool-layer.md).

---

## What 11B.3 is

The orchestrator takes a **validated Plan** and either blocks for seller confirmation or executes approved tools through `ToolRegistry.execute()`. It returns evidence envelopes. It does not write seller-facing answers.

```text
Validated Plan
  ↓
Confirmation Gate (nonce + plan_hash)
  ↓
Tool Orchestrator (BudgetTracker)
  ↓
ToolRegistry.execute(...)
  ↓
EvidenceEnvelope[]
```

**Stop here.** Synthesis, Copilot UI, RAG, and Amazon writes are **not** in this milestone.

---

## Non-negotiable rules (enforced)

| Rule | How |
| --- | --- |
| Registry is the only execution path | Orchestrator calls `ToolRegistry.execute()` only. It does not import ProductService, listing scoring, History services, or providers |
| Planner unchanged | 11B.3 consumes stored Plans. Planner schemas, prompts, and validator were not modified |
| Model cannot grant permission | Client `confirmed=True` is ignored. `execute(..., confirmed=True)` only after a valid seller nonce |
| First Amazon fetch still confirms | Paid tools (`get_product`, `analyze_listing_v2`) require confirmation even on the first product call |
| History-first already on the Plan | Orchestrator does not re-plan. Explain-intent Plans keep History tools |
| No new migrations | Uses `copilot_pending_confirmations` from 0004. Execution is stored on a system message |

---

## Package

```text
apps/api/app/copilot/orchestrator/
    __init__.py
    schemas.py      ExecutionRequest, ConfirmRequest, ExecutionResult
    service.py      ConfirmationGate, OrchestratorService
```

---

## Execution request

`ExecutionRequest`:

| Field | Meaning |
| --- | --- |
| `plan_id` | Stored validated Plan |
| `conversation_id` | From the URL path, never from a spoofed body org |
| `plan_hash` | Must match the stored Plan |
| `confirmation_nonce` | Seller token; required for paid Plans |
| `confirmed` | **Untrusted.** Ignored. Never copied into `execute()` |

`organization_id` is `current_organization_id()`. Extra body keys are ignored.

---

## Confirmation gate

Free tools (no nonce): `list_saved_reports`, `get_saved_report`.

Paid tools (nonce required): `get_product`, `analyze_listing_v2`.

Confirmation is required when the Plan has `needs_confirmation` **or** any tool cost is not `none`.

Nonce checks (all must pass):

- nonce exists for the current organization
- conversation matches
- not expired (15 minutes)
- not already consumed
- `plan_id`, `plan_schema_version`, and `plan_hash` match the stored Plan

After a valid confirm the nonce is marked consumed, then tools run with **application** `confirmed=True`.

Replay of the same nonce returns HTTP 409 and does not fetch Amazon again.

GET conversation still exposes only `{ plan_id, nonce_present, summary }` — never the nonce secret. The nonce is returned once on the blocked execute response so the seller client can confirm.

---

## Tool execution

After the gate passes:

```text
ToolRegistry.execute(name, arguments, budget, confirmed=<grant>)
```

- One `BudgetTracker` per turn (`begin_round()` once)
- Permission keys in arguments (`confirmed`, `budget`, `handler`) are stripped before execute
- Failures stop the remaining calls; envelopes are never fabricated
- Results are **not** turned into natural language

Allowed tools: the four 11A registrations only. `compare_saved_analyses` is **not** registered.

---

## APIs

```text
POST /api/v1/copilot/conversations/{id}/execute
POST /api/v1/copilot/conversations/{id}/confirm
```

Execute body: `{ "plan_id", "plan_hash", "confirmation_nonce"? }`.

Confirm body: `{ "nonce" }`.

Neither endpoint synthesizes a seller answer. There is still **no** `POST .../messages` chat turn and **no** `/copilot` UI.

Persists: system message `structured_payload.type = copilot_execution` with evidence refs (hidden from seller chat bubbles). Compact context may list those refs for a future synthesizer; the planner still does not receive envelopes.

---

## Explicitly not in 11B.3

- Synthesis AI / seller-facing answers
- Copilot UI / `/copilot`
- `compare_saved_analyses`
- RAG, agents, Amazon writes
- New Alembic revisions
- Changes to Analyze / History / Reports / Bulk
- Changes to ToolRegistry `execute` semantics, EvidenceEnvelope, or the planner

## Next

**11B.4** is implemented — see [milestone-11b4-synthesis.md](milestone-11b4-synthesis.md).

**11B.5** — `/copilot` UI: chips, activity, confirm modal, History deep links.

# Milestone 11B.4 — Synthesis AI + Citation Validation

**Date:** 21 August 2026  
**Status:** Implemented. Stops at a grounded seller response.  
**Depends on:** 11A ToolRegistry, 11B.1 Conversation Foundation, 11B.2 Hybrid Planner, 11B.3 Orchestrator  
**Tests:** `uv run pytest` — **424 passed**. No live Rainforest. No live OpenAI.

Architecture: [../milestone-11b-architecture.md](../milestone-11b-architecture.md). Orchestrator: [milestone-11b3-tool-orchestration.md](milestone-11b3-tool-orchestration.md).

---

## What 11B.4 is

The synthesizer turns **this-turn `EvidenceEnvelope[]`** into seller language. A citation validator keeps only claims that exist in the evidence index. If the language model is down or invents facts, an evidence template is used instead.

```text
EvidenceEnvelope[]
  ↓
allowed_facts (claim index)
  ↓
Synthesis AI (optional, copilot_synthesize)
  ↓
Citation Validator
  ↓
Final seller response
```

**Stop here.** Copilot UI, streaming, RAG, and Amazon writes are **not** in this milestone.

---

## Non-negotiable rules (enforced)

| Rule | How |
| --- | --- |
| Evidence is the only source of truth | The model receives `allowed_facts`, not SQL, Product objects, or provider JSON |
| AI does not create facts | Findings need a `claim_key` in the index. Ranking / conversion / PPC language is stripped unless those claims exist |
| Planner ≠ synthesis | Prompt `copilot_synthesize`. Never reuse `copilot_plan`. No `tool_calls` |
| Failure degrades fluency | Invalid JSON, timeout, or provider errors → template from envelopes. Never an empty answer |
| No tool execution | Synthesis does not import ToolRegistry or call `execute()` |

---

## Package

```text
apps/api/app/copilot/synthesis/
    __init__.py
    schemas.py      SynthesisRequest, SynthesisProposal, SynthesizedResponse
    prompts.py      copilot_synthesize
    validator.py    allowed_facts, citation checks, template fallback
    service.py      SynthesisService, optional AIProvider
```

---

## Request

`SynthesisRequest`:

| Field | Meaning |
| --- | --- |
| `user_message` | Current seller question (untrusted data) |
| `intent` | From the validated Plan |
| `evidence` | This-turn envelopes only |
| `compact_context` | Slots + truncated snippets + evidence refs. Nonce, catalog, and org id are dropped |

---

## Response

`SynthesizedResponse`:

| Field | Meaning |
| --- | --- |
| `summary` | Short seller explanation |
| `findings` | Evidence-backed observations |
| `recommendations` | Actions tied to a claim key or finding code |
| `citations` | `{ evidence_id, claim_key, tool_name, label }` |
| `confidence` | `high` · `medium` · `low` · `none` |
| `unknowns` | Metrics the model wanted that were not in evidence |
| `source` | `synthesis_llm` · `rewritten_citations` · `template_fallback` |
| `message` | Seller-facing markdown (Summary / Key Findings / Recommended Actions / Evidence) |

Example citation: listing quality score 72 from `get_saved_report` → label **Saved analysis**.

Rejected wording: “Amazon ranking is lower because your bullet points are weak.”  
Kept wording: “Your listing analysis identified this weakness: Add more complete bullet points.”

---

## AI wiring

One `generate_structured` call max, through `AIProvider`. The OpenAI SDK is not imported here.

SQLite / pytest skip the live provider (`DATABASE_URL=sqlite://`). Inject a fake generator in unit tests. Failures fall back to the template.

`out_of_scope` never calls the synthesizer. It returns canned routing copy.

---

## API

Isolated backend capability, not a chat turn:

```text
POST /api/v1/copilot/synthesize
```

Body: `{ "user_message", "intent", "evidence", "compact_context"? }`. Extra keys such as `organization_id` are ignored.

There is still **no** `POST .../messages` chat endpoint and **no** `/copilot` UI.

---

## Explicitly not in 11B.4

- Copilot UI / streaming
- RAG / vector store
- Agents / LangGraph / CrewAI
- Amazon writes
- New Alembic revisions
- Changes to ToolRegistry, EvidenceEnvelope, Planner, or Orchestrator

## Next

**11B.5** is implemented — see [milestone-11b5-copilot-ui.md](milestone-11b5-copilot-ui.md).

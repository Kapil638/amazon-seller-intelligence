# Milestone 11A checkpoint — Intelligence Tool Layer complete and hardened

**Date:** 21 August 2026  
**Status:** Complete. Hardened. Checkpointed.  
**Git:** `834a79b4a7e2b4cd6ba91c1da9deb9fb47779cd6` (`feat(copilot): complete milestone 11A intelligence tool layer`)  
**Tests:** `uv run pytest` — **374 passed**. No live Rainforest. No live OpenAI.

**Milestone 11B has not started.**

Behavior: [copilot-tool-layer.md](copilot-tool-layer.md). Plan: [milestone-11-plan.md](milestone-11-plan.md). Architecture: [milestone-11-architecture-review.md](milestone-11-architecture-review.md). Initial implementation record: [milestone-11a-report.md](milestone-11a-report.md). Pre-hardening review: [milestone-11a-code-review.md](milestone-11a-code-review.md) (decision **B**; High items closed in hardening).

---

## What 11A is

An internal Intelligence Tool Layer so a future Seller Copilot can call **trusted tools** instead of application services directly.

```text
User (11B, not built)
 ↓
Planner (11B, not built)
 ↓
ToolRegistry.execute(name, arguments, budget, confirmed=app-owned)
 ↓
Trusted application services
 ↓
EvidenceEnvelope
 ↓
Synthesis (11B, not built)
```

Deterministic services remain the source of truth. AI must not calculate listing scores or financial numbers.

## Hardened guarantees

| Guarantee | How |
| --- | --- |
| No unlimited tool use | `execute()` requires a `BudgetTracker`. Missing budget → `BudgetRequiredError` |
| Model cannot invent Amazon listings | Copilot `analyze_listing_v2` accepts `{ asin, marketplace? }` only. Manual product input stays on `POST /api/v1/analysis/listing/v2` |
| Model cannot grant permission | `confirmed=True` is a server keyword. A `confirmed` key in tool JSON is stripped and ignored |
| Planner sees contracts, not internals | `list_tools()` / `get_tool()` return `{ name, description, input_schema, cost, confirmation_required }` |
| Scores are copied, not recalculated | Listing V2 scoring stays in `ListingAnalysisV2Service.analyze()` |
| History stays historical | `get_saved_report` / `list_saved_reports` use snapshots; kind `historical`, source `snapshot` |
| Observed data has provider origin | Product claims use `mock` / `rainforest` from `ProductService`, not seller-fabricated payloads |

## Registered tools

| Tool | Wraps | Copilot input |
| --- | --- | --- |
| `get_saved_report` | `AnalysisHistoryService` | `{ report_id }` |
| `list_saved_reports` | `AnalysisHistoryService` | `{ asin?, limit? }` |
| `analyze_listing_v2` | `ProductService` then `ListingAnalysisV2Service` | `{ asin, marketplace? }` |
| `get_product` | `ProductService` | `{ asin, marketplace? }` |

## Budget (not billing)

- `max_tool_rounds` = 2 (third `begin_round()` rejected)
- `max_tools_per_turn` = 4
- Rainforest product: first call allowed; further calls need application `confirmed=True`
- Rainforest search / OpenAI: always require confirmation (unused in 11A)

## Explicitly not in this checkpoint

- Copilot UI / `/copilot`
- Chat endpoints
- OpenAI planner or synthesizer
- RAG / MCP / SP-API / Redis / Celery
- Alembic migrations
- REST contract changes for Analyze, History, Reports, Bulk

## Remaining risks for 11B (not blockers for this checkpoint)

- First product lookup is still unconfirmed; 11B should prefer History and confirm cache misses.
- 11B must set `confirmed=True` only after a real seller confirm step.
- Synthesis must cite envelope claims only (tools never emit conversion rate).
- Listing text in claims is untrusted content (prompt-injection surface).
- No authentication; do not host Copilot on a public URL.
- Application code can still import services and skip the registry; 11B’s only path must be `execute()`.

## Next

Start **Milestone 11B — Seller Copilot V1** only after explicit approval. This checkpoint is the freeze of the tool layer.

# MILESTONE 11A — INTELLIGENCE TOOL LAYER REPORT

**Date:** 21 August 2026  
**Status:** Complete  
**11B Copilot chat / OpenAI planner / RAG / MCP / SP-API / Ads / Redis / Celery / auth:** not started

This is the Milestone 11A completion record. Behavior lives in [copilot-tool-layer.md](copilot-tool-layer.md). Planning lives in [milestone-11-plan.md](milestone-11-plan.md). Post-implementation review: [milestone-11a-code-review.md](milestone-11a-code-review.md).

---

## Objective

Create a stable Intelligence Tool Layer that wraps existing application services so future Copilot can call trusted tools instead of services directly.

Deterministic services remain the source of truth. AI will never calculate scores or financial numbers in this layer.

## Package

- path: `apps/api/app/copilot/`
- public entry: `default_registry()`, `ToolRegistry.execute()`, `BudgetTracker`, `EvidenceEnvelope`
- HTTP routes added: **none**
- frontend changes: **none**
- Alembic: **none** (`0001`–`0003` untouched)

## Evidence

- envelope: `EvidenceEnvelope` (`evidence_id`, `tool_name`, `organization_id`, `produced_at`, `claims`)
- claim kinds: `observed`, `calculated`, `historical`, `seller_provided`, `ai_inference`, `unknown`
- claim sources (examples): `rainforest`, `mock`, `snapshot`, `manual`, `seller_upload`, `derived`

## Registry

- register by explicit name
- `get_tool` / `list_tools` / `execute`
- unknown name: `UnknownToolError`
- invalid input: `ToolValidationError`
- internal analytics helpers are not registered

## Tools implemented

| Tool | Service wrapped | Duplicate scoring? |
| --- | --- | --- |
| `get_saved_report` | `AnalysisHistoryService` | no |
| `list_saved_reports` | `AnalysisHistoryService` | no |
| `analyze_listing_v2` | `ListingAnalysisV2Service` (+ `ProductService` when only `asin` is given) | no |
| `get_product` | `ProductService` | n/a |

History isolation uses the existing `current_organization_id()` path. Other-org report IDs raise `ReportNotFoundError`.

`analyze_listing_v2` does not persist History. Existing `POST /api/v1/analysis/listing/v2` still persists.

Product provider cache is unchanged (Rainforest TTL still skips a second HTTP call).

## Budget

- module: `budget.py` (not billing)
- `max_tool_rounds` = 2
- `max_tools_per_turn` = 4
- first Rainforest **product** call allowed; additional product calls require confirmation
- Rainforest **search** requires confirmation (no search tool registered in 11A)
- OpenAI: unused; policy still requires confirmation if that cost kind is recorded

## Existing product

- Analyze / History / Reports / Bulk REST contracts: **unchanged**
- Listing V2 scoring rules: **unchanged**
- Persistence schema: **unchanged**

## Tests

- new tests: 8 in `apps/api/tests/test_copilot_tools.py`
- live Rainforest: **0**
- live OpenAI: **0**
- total backend tests at completion: **372 passed** (`uv run pytest`)

Coverage:

- unknown tool rejected
- registered tool executes
- schema validation
- envelope + claim kinds
- saved report + organization isolation
- listing tool matches `ListingAnalysisV2Service` (monkeypatch proves `analyze` is called)
- product tool uses `ProductService` and preserves Rainforest cache
- 4-tool limit; second product lookup requires confirmation

## Next

Milestone **11B** (Seller Copilot V1: chat UI, planner/synthesizer, conversation persistence) is **not** started.

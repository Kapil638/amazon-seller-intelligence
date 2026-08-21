# Intelligence Tool Layer (Milestone 11A)

Internal façade so future Seller Copilot can call **trusted tools** instead of invoking application services directly.

This is not a chat product. There is no Copilot UI, no OpenAI planner, no RAG, and no new public REST contract.

Completion record: [milestone-11a-report.md](milestone-11a-report.md). Plan: [milestone-11-plan.md](milestone-11-plan.md). Code review: [milestone-11a-code-review.md](milestone-11a-code-review.md).

## Purpose

Existing deterministic services remain the source of truth. AI must never calculate listing scores or financial numbers. Tools return **evidence-backed claims** that later synthesis can cite.

Analyze, History, Reports, and Bulk continue to call the same services they already use. 11A only adds `app.copilot`.

## Architecture

```text
Future Copilot (11B)
        ↓
ToolRegistry.execute(name, arguments, budget, confirmed=app-owned)
        ↓
registered handler (no duplicated scoring)
        ↓
AnalysisHistoryService | ListingAnalysisV2Service | ProductService
        ↓
EvidenceEnvelope (claims)
```

Call from Python:

```python
from app.copilot import BudgetTracker, default_registry

registry = default_registry()
budget = BudgetTracker()
envelope = await registry.execute(
    "list_saved_reports",
    {"asin": "B0TEST0001", "limit": 10},
    budget=budget,
)
```

`budget` is **required**. Omitting it raises `BudgetRequiredError`. There is no unlimited execution path.

Unknown names raise `UnknownToolError`. Invalid input raises `ToolValidationError`. Budget exhaustion raises `BudgetExceededError`. A second product lookup (or any search / OpenAI cost) raises `ConfirmationRequiredError` unless the **application** passes `confirmed=True`.

### Confirmation ownership

The model proposes `{name, arguments}`. The application owns permission.

`execute(..., confirmed=True)` must originate from a trusted **server-side seller confirmation** flow (Milestone 11B). Never copy `confirmed` from model JSON into that parameter. A `confirmed` key inside `arguments` is stripped and ignored.

Do not implement the confirmation UI in 11A.

## Evidence envelope

Every tool returns `EvidenceEnvelope`:

| Field | Meaning |
| --- | --- |
| `evidence_id` | UUID for this result |
| `tool_name` | Registered name |
| `organization_id` | `current_organization_id()` |
| `produced_at` | UTC timestamp |
| `claims` | List of `EvidenceClaim` |

Each claim has `key`, `value`, `kind`, `source`, `confidence`, optional `as_of` and `notes`.

**Kinds:** `observed` · `calculated` · `historical` · `seller_provided` · `ai_inference` · `unknown`

**Sources (examples):** `rainforest` · `mock` · `snapshot` · `manual` · `seller_upload` · `derived`

Scores in claims are copied from services. The tool layer does not recompute them.

## Registered tools (11A)

| Name | Wraps | Copilot input | Typical cost |
| --- | --- | --- | --- |
| `get_saved_report` | `AnalysisHistoryService.get_report` | `{ report_id }` | none |
| `list_saved_reports` | `AnalysisHistoryService.list_reports` | `{ asin?, limit? }` | none |
| `analyze_listing_v2` | `ProductService` then `ListingAnalysisV2Service.analyze` | `{ asin, marketplace? }` | Rainforest product |
| `get_product` | `ProductService.fetch_product` | `{ asin, marketplace? }` | Rainforest product |

`analyze_listing_v2` does **not** accept a `product` object. A fabricated listing must not be scored as Amazon-observed data. Manual / seller-entered listings remain on `POST /api/v1/analysis/listing/v2`.

`list_tools()` / `get_tool()` return `{name, description, input_schema, cost, confirmation_required}` only. Handlers stay private.

`marketplace` defaults to the configured default (`amazon.in`).

History tools use claim kind `historical` and source `snapshot`. They do not recalculate scores or call providers.

`analyze_listing_v2` claims include listing quality score, coverage, findings, and market signals. Scoring is a single call to `ListingAnalysisV2Service.analyze()`. This tool does **not** persist a History row; `POST /api/v1/analysis/listing/v2` still does.

`get_product` claims are `observed` with source `rainforest` or `mock` (or `manual` if that origin is used). Provider TTL cache is unchanged: a cache hit does not issue another HTTP request.

## Budget policy

`BudgetTracker` is **not billing**. It is a per-turn execution policy for a future Copilot turn.

| Limit | Default |
| --- | --- |
| `max_tool_rounds` | 2 |
| `max_tools_per_turn` | 4 |

| Cost kind | Confirmation |
| --- | --- |
| `none` | Never |
| `rainforest_product` | First call allowed; further calls need `confirmed=True` |
| `rainforest_search` | Always (no search tool in 11A) |
| `openai` | Always (OpenAI is unused in 11A) |

Pass the same tracker into every `execute()` call. Recorded counts: `tools_this_turn`, `rainforest_product_calls`, `rainforest_search_calls`, `openai_calls`. A third `begin_round()` is rejected.

For 11A tests, `get_product` is always classified as `rainforest_product`, including mock catalog ASINs, so confirmation can be exercised offline.

## What 11A does not do

- Copilot chat UI or `/copilot` route
- OpenAI planner or synthesizer
- RAG / pgvector
- MCP
- SP-API / Ads API
- Redis / Celery
- New FastAPI routes
- Alembic migrations
- Refactors of existing services
- Changes to Analyze / History / Reports / Bulk REST contracts

## Layout

```text
apps/api/app/copilot/
    __init__.py          default_registry()
    registry.py          ToolDefinition, ToolRegistry
    evidence.py          EvidenceEnvelope, EvidenceClaim
    schemas.py           tool input models
    exceptions.py        UnknownToolError, ToolValidationError, …
    budget.py            BudgetTracker
    tools/
        history.py       get_saved_report, list_saved_reports
        listing.py       analyze_listing_v2
        product.py       get_product
```

## Tests

`apps/api/tests/test_copilot_tools.py` uses existing fixtures (`conftest` mock provider + SQLite, `make_product`, `_persist_report`). No live Rainforest or OpenAI.

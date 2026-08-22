# Milestone 11D.1 — Copilot Domain Tool Enablement Foundation

**Backend tests:** `uv run pytest` in `apps/api` — **472 passed**.  
**Web tests:** `npm test` in `apps/web` — **20 passed**.  
**Depends on:** 11A ToolRegistry + EvidenceEnvelope, 11B Copilot, 11C.1 Profit Intelligence, 11C.2 Advertising Intelligence, [11D Skill Architecture](../milestone-11d-architecture.md) as **future** architecture only.

This milestone exposes existing Profit and Advertising engines to Copilot through **ToolRegistry**. It does not implement Skills, Skill Registry, LangGraph, CrewAI, agents, or Amazon writes.

---

## Why these tools exist

Listing Intelligence already had Copilot tools (`get_saved_report`, `analyze_listing_v2`, …). Profit and Advertising engines were workspace-only (`/profit`). Future Skills such as Profit Improvement need **technical tools** first.

```text
Seller Copilot
  → ToolRegistry.execute
  → ProfitModelingService / AdvertisingModelingService / AdvertisingImpactService
  → EvidenceEnvelope
  → Copilot explanation
```

**Tools are technical capabilities. They do not understand business goals.**

| Tool | Is | Is not |
| --- | --- | --- |
| `get_profit_snapshot` | Read latest immutable profit snapshot | “Improve profitability” |
| `analyze_profitability` | Persist a new `profit-calc-v1` snapshot via the existing service | A Skill |
| `get_advertising_snapshot` | Read latest ads snapshot | “Optimize advertising” |
| `analyze_advertising_impact` | Compose stored snapshots via `AdvertisingImpactService` | Bid writes or ads-calc-v2 |

Future Skills will **name** these tools. 11D.1 does not select Skills.

---

## Registered tools

All four use `cost=none`, no confirmation, and ignore extra keys (`organization_id`, client-calculated money). Organization comes from `current_organization_id()`. Other-org models raise `ProfitModelNotFoundError` (404 at HTTP boundaries).

| Name | Wraps | Input | Behavior |
| --- | --- | --- | --- |
| `get_profit_snapshot` | `ProfitModelingService.get_model` | `{ profit_model_id? , asin? }` | Read only. Claims copied as `historical` / `snapshot`. Missing snapshot → unknown profit, not zero. |
| `analyze_profitability` | `ProfitModelingService.calculate` | same | Runs `profit-calc-v1` through the service. Tool does not contain formulas. |
| `get_advertising_snapshot` | `AdvertisingModelingService.get_existing_for_profit_model` | same | Read only. Does not create an ads worksheet. Does not recalculate ACOS/TACOS/ROAS. |
| `analyze_advertising_impact` | `AdvertisingImpactService.compose` on **stored** snapshot inputs + cited profit snapshot | same | Does not call `ads-calc-v1`. Does not persist a new ads snapshot. |

`list_tools()` / `get_tool()` contracts are unchanged: `{name, description, input_schema, cost, confirmation_required}`. Handlers stay private.

---

## Evidence

`EvidenceEnvelope` schema is unchanged. Tool `tool_name` is the registered name (not `profit_calculation` / `advertising_calculation` on Copilot envelopes).

| Kind | When |
| --- | --- |
| `historical` | Snapshot values on get_* tools |
| `seller_provided` | Worksheet inputs on analyze_profitability |
| `calculated` | Engine outputs (`profit-calc-v1`, `ads-calc-v1`, `advertising_impact`) |
| `unknown` | Missing COGS, total sales, units, or no snapshot |

Money claims are never `ai_inference`. Unknown is never coerced to zero.

---

## Planner (minimal)

New intents: `explain_profit`, `explain_advertising_impact`. Fallback maps profit/ACOS questions to `get_*_snapshot` when an ASIN is present. Competitor comparison, campaign PPC, and launch stay `out_of_scope`. No Skill selection.

---

## Unchanged

- ToolRegistry.execute contract  
- EvidenceEnvelope fields  
- `profit-calc-v1` / `ads-calc-v1`  
- Listing Intelligence  
- Copilot conversation / orchestrator / confirmation gate  
- No new public REST routes (tools run through existing Copilot execute)  
- No Skill Registry, Skill UI, or Amazon writes  

---

## Tests

`apps/api/tests/test_copilot_domain_tools.py` plus catalog assertion in `test_copilot_tools.py`.

# Milestone 11A — Architecture & Code Review

**Date:** 21 August 2026  
**Scope:** Intelligence Tool Layer only (`apps/api/app/copilot/`). No 11B implementation. No code changes.  
**Reviewed against:** [milestone-11-architecture-review.md](milestone-11-architecture-review.md), [milestone-11-plan.md](milestone-11-plan.md), [copilot-tool-layer.md](copilot-tool-layer.md)  
**Tests at review:** `uv run pytest` — **372 passed** (8 in `test_copilot_tools.py`). No live Rainforest or OpenAI.

---

## 1. Executive summary

Milestone 11A is a **sound foundation** for Seller Copilot. It does the important thing correctly: existing deterministic services remain the source of truth, tools are an explicit name→handler registry, results are evidence envelopes, and there is no Copilot HTTP surface, no OpenAI planner, and no duplicated listing-score math.

It is **not yet a closed control plane**. An LLM planner in 11B would be safe **only if 11B always calls `ToolRegistry.execute()` with a `BudgetTracker` and never forwards `confirmed=True` from model output**. Those are caller contracts, not invariants of the registry.

**Final decision: B — Approved with minor fixes.**

No critical production bug was found in 11A itself (there is no public Copilot endpoint yet). Do not redesign. Apply the listed fixes before or at the start of 11B.

---

## 2. Architecture validation

11A matches the approved plan:

| Plan item | Status |
| --- | --- |
| Internal Python registry, no Copilot UI | Met |
| Four tools wrapping existing services | Met |
| Evidence envelope + claim kinds | Met |
| Budget policy module (not billing) | Met (see F2, F1) |
| No REST contract changes | Met |
| No Alembic; `0001`–`0003` untouched | Met |
| No OpenAI / RAG / MCP / SP-API | Met |
| Existing Analyze / History / Reports / Bulk unchanged | Met |
| Tests offline | Met |

Layering as implemented:

```text
Caller (tests today; Copilot 11B later)
        ↓
ToolRegistry.execute(name, arguments, budget?, confirmed?)
        ↓
Pydantic input schema
        ↓
Budget / confirmation (only if budget is passed)
        ↓
Approved handler
        ↓
AnalysisHistoryService | ListingAnalysisV2Service | ProductService
        ↓
EvidenceEnvelope
```

This is the architecture in §8 of the review: Copilot as orchestrator, not calculator. Services are not forked.

---

## 3. Tool registry

**File:** `apps/api/app/copilot/registry.py`

| Check | Result |
| --- | --- |
| Only registered names execute | **Yes.** `get_tool` dict lookup; miss → `UnknownToolError` |
| Unknown tools raise controlled exceptions | **Yes.** `UnknownToolError` |
| Schemas validated before handler | **Yes.** `input_schema.model_validate` → `ToolValidationError` |
| Internal analytics helpers not registered | **Yes.** No `listing_rules_v2` / mappers / PDF in the registry |
| Handlers call application services | **Yes.** See §4 |
| Duplicate business logic in tools | **No scoring duplication.** Tools copy/project service output into claims |

`register()` rejects blank names and duplicates. `list_tools()` returns sorted definitions.

### Is this safe for an LLM planner in 11B?

**Safe if 11B treats the registry as a closed gate:**

1. The model may propose only `{name, arguments}`.
2. The server looks up the name; it does not `eval` or import from a string path.
3. `confirmed` is a Python keyword argument, not a field on the tool JSON. Do **not** copy a model-supplied `confirmed` flag into `execute()`.
4. Always pass a per-turn `BudgetTracker`.

### Can an AI model accidentally bypass controls?

**Not through `execute(name, arguments)` alone.** The model cannot call arbitrary Python, open a SQL session, or pick an `organization_id` — those fields are not on the input schemas.

**Bypasses exist if 11B is sloppy:**

| Bypass | How |
| --- | --- |
| Skip budget | `execute(..., budget=None)` — current default |
| Skip confirmation | `confirmed=True` set by server code that trusts the model |
| Skip registry | Import `_get_product` / `ProductService` / `ListingAnalysisV2Service` directly |
| Fabricate a listing | `analyze_listing_v2` with a full `product` object (cost `none`) |
| Call handlers | `list_tools()` returns live `handler` callables |

None of these are reachable by a model that only emits JSON tool calls into a correctly written 11B endpoint. They are **11B wiring risks**, not 11A runtime holes.

---

## 4. Evidence envelope

**File:** `apps/api/app/copilot/evidence.py`

Required fields are present:

**EvidenceEnvelope:** `evidence_id`, `tool_name`, `organization_id`, `produced_at`, `claims`  
**EvidenceClaim:** `key`, `value`, `kind`, `source`, `confidence`, `as_of`, `notes`

`organization_id` is taken from `current_organization_id()`, not from tool arguments. Claim `kind` is a closed Literal; invalid kinds fail Pydantic validation (tested).

### Scores

- `analyze_listing_v2` copies `ListingAnalysisV2Service.analyze().listing_quality_score`. Tests monkeypatch `analyze` and assert it is called **once** and the claim matches the service.
- `get_saved_report` copies `detail.analysis.listing_quality_score` from the snapshot. Kind `historical`, source `snapshot`. It does not call `analyze_listing_v2` or Rainforest.
- Copilot tools do not import `app.analytics.listing_rules_v2`.

### Unknown values

Missing price on `get_product` is represented as `kind=unknown`, `confidence=none`, `value=None`. That pattern is correct.

### Hallucination risk: `conversion_rate = 15%`

**Tools will not emit that claim.** None of the four tools produce conversion, traffic, or PPC fields. Listing V2 explicitly keeps rating/reviews/price in `market_signals`, not in the quality score.

**The envelope cannot stop a future synthesizer from inventing it.** `value: Any` is unstructured. There is no allow-list of claim keys, and there is no `untrusted_content` marker (architecture §8.5 / D11). 11B synthesis must:

- cite only keys present in envelopes from this turn
- treat finding messages and titles as data, not instructions
- refuse metrics that are not in claims (conversion, search volume, “Amazon will rank you”)

If 11B binds the synthesizer to the envelope, `conversion_rate = 15%` cannot appear as a tool-backed fact. If 11B lets the model free-write, it can. That is a **11B prompt/schema problem**, not a 11A scoring bug.

### Claim completeness (11B usability)

History and listing tools project a **subset** of service output:

- No section scores (title / bullets / …)
- No recommendations
- No custom listing score / profile snapshot
- No full product payload
- `get_saved_report` findings only, not coverage or market signals

A seller asking “why is my title score low?” cannot be answered from `get_saved_report` claims alone. The synthesizer may then invent section detail. This is a **completeness gap**, not a correctness bug. Prefer adding claims or returning a structured `payload` claim in 11B rather than letting the model guess.

### Product-input provenance

When `analyze_listing_v2` is given a `product` blob, ASIN/market signals are labeled `observed` / `derived`, not `seller_provided`. A fabricated product would look like observed Amazon data. See F3 / F6.

---

## 5. Tool implementations

### `get_saved_report` — `tools/history.py`

| Requirement | Result |
| --- | --- |
| Uses `AnalysisHistoryService` | Yes — `get_report` |
| Does not refetch Amazon | Yes — snapshot only |
| Does not recalculate scores | Yes — copies persisted `listing_quality_score` |
| Historical evidence | Kind `historical`, source `snapshot` |

`ReportNotFoundError` from the service is propagated (including other-org IDs). Soft-deleted rows follow existing History behavior.

### `list_saved_reports`

| Requirement | Result |
| --- | --- |
| Organization scoped | Yes — `list_reports` → `current_organization_id()` → `AnalysisRun.organization_id` filter |
| No cross-tenant leakage | Tested: other-org `report_id` absent from list; `get_saved_report` raises `ReportNotFoundError` |
| `organization_id` not a tool input | Yes — cannot be spoofed via arguments |

ASIN filter is a parameterized equality (`asin.upper()`), not string-concatenated SQL.

**Auth caveat (pre-existing):** there is still one default organization. Tools isolate by that column. They do not add login. Shared-host Copilot remains blocked by D1, not by 11A.

### `analyze_listing_v2` — `tools/listing.py`

| Requirement | Result |
| --- | --- |
| Calls `ListingAnalysisV2Service` once | Yes — single `analyzer.analyze(product)` |
| No duplicated scoring rules | Yes — no rules module import |
| Supports `product` input | Yes |
| Supports `asin` input | Yes — `ProductService.fetch_product` then analyze |
| Does not persist History | Yes (correct for 11A; REST still persists) |

Cost: `product` present → `none`. ASIN-only → `rainforest_product`. If both are sent, **product wins** and the fetch is skipped. That is the main 11B abuse path (F3).

### `get_product` — `tools/product.py`

| Requirement | Result |
| --- | --- |
| Uses `ProductService` | Yes — `fetch_product` |
| Provider abstraction preserved | Yes — `get_product_provider()` / injectable service |
| Cache preserved | Tested: Rainforest `MockTransport` HTTP count stays 1 on second call |
| Source rainforest / mock | Yes — `origin` from `fetch_product` (`provider.name` / mock catalog) |
| Source `manual` | **Not produced.** `get_product` never calls `create_from_manual`. Manual listings would enter via `analyze_listing_v2` `{product: …}` and are labeled `derived`, not `manual` |

Default marketplace is settings (`amazon.in`). Invalid ASINs still fail inside `ProductService` (`ValueError`).

Two `ProductService` instances (listing tool vs product tool) wrap the same cached provider from `get_product_provider()`, so Rainforest TTL is shared. That is correct.

---

## 6. Budget system

**File:** `apps/api/app/copilot/budget.py`

| Rule | Implemented? |
| --- | --- |
| `max_tool_rounds = 2` | Constant exists; **not enforced by `execute()`** (F2) |
| `max_tools_per_turn = 4` | **Yes**, when a tracker is passed |
| `none` — no confirmation | Yes |
| `rainforest_product` — first free, later confirm | Yes, when a tracker is passed |
| `rainforest_search` — always confirm | Yes on the tracker; **no search tool registered in 11A** |
| `openai` — always confirm | Same; unused in 11A |
| Independent of billing | Yes — no ledger / Stripe / usage dashboard writes |
| Confirmation explicit | Yes — `confirmed: bool = False` keyword-only |

`BudgetTracker` does not bill. `record_execution` runs **after** a successful handler, so a failed fetch is not counted. Confirmation is checked **before** the handler, so a second product lookup does not hit Rainforest until confirmed.

### Can budget be bypassed by tool handlers?

**Yes, by not going through `execute` with a tracker.** Handlers themselves do not consult `BudgetTracker`. That is acceptable if 11B has a single execution path.

`assert_can_execute(COST_NONE, confirmed=True)` is used only to enforce the **count** cap; cost confirmation is applied separately. That split is easy to misread but behaves correctly for the 4-tool limit.

### Architecture 8.6 vs 11A code

| Architecture default | 11A code |
| --- | --- |
| 2 tool **rounds** hard cap | Rounds only increment if `begin_round()` / first `record_execution`; `execute` never starts round 2 or stops at round 2 |
| 4 **distinct** tools | 4 **executions** (four `list_saved_reports` calls exhaust the budget) |
| 1 unconfirmed product; **3 with confirm** | 1 unconfirmed; further allowed if `confirmed=True` until the 4-execution cap |
| Confirm on **cache miss** | First product tool is unconfirmed even on a live fetch |

11A’s written brief (“first product call allowed”) was implemented. 11B should tighten toward §8.6 (History-first, cache-miss confirm, hard product cap).

---

## 7. Findings

No code was changed. None of these are critical 11A production defects (no Copilot HTTP API). They should be fixed **before trusting an LLM planner**.

| ID | File | Issue | Severity | Recommended change |
| --- | --- | --- | --- | --- |
| F1 | `registry.py` | `budget` defaults to `None`. Unlimited `get_product` if 11B forgets the tracker. | **High** (11B) | Require a `BudgetTracker` on `execute`, or provide `execute_turn()` that constructs one. Fail closed. |
| F2 | `budget.py`, `registry.py` | `max_tool_rounds = 2` is not applied by `execute`. Check is `rounds > max` (never true at 2). | **Medium** | `begin_round()` per planner phase; refuse execute when `rounds > max` **or** when a new round would exceed max. Add a test. |
| F3 | `schemas.py`, `tools/listing.py` | LLM can pass a full `Product` and skip Rainforest + confirmation. Scores then look official. | **High** (11B) | 11B: do not expose `product` to the model. Accept `asin` (and later seller-confirmed manual). If `product` remains, mark claims `seller_provided` and require confirmation. |
| F4 | `registry.py` | `list_tools()` / `get_tool()` expose `handler` callables. | **Medium** | Public catalog type with `name`, `description`, JSON schema only. Keep handlers private. |
| F5 | `tools/history.py`, `tools/listing.py` | Claim subset omits section scores / recommendations. Synthesizer may invent them. | **Medium** | Add section-score claims or a single structured analysis payload claim for 11B. |
| F6 | `tools/listing.py` | In-memory product labeled `observed`/`derived`, not `seller_provided`. | **Medium** | Set kind/source from origin; `seller_provided` when the caller supplied `product`. |
| F7 | `evidence.py` | No `untrusted_content` (architecture D11 / §8.5). Finding messages are Amazon/seller text. | **Medium** (11B) | Tag text-heavy claims; synthesis prompt: treat as data. |
| F8 | `budget.py` | Unknown `cost_kind` → `requires_confirmation() is False`. | **Low** | Default unknown kinds to confirm or raise. |
| F9 | `budget.py` | No cap of 3 confirmed Rainforest product calls (§8.6). | **Low** | Add `max_rainforest_product_calls` in 11B. |
| F10 | `tools/product.py` | `manual` source never returned; 11A brief listed it. | **Low** | Document, or add a distinct manual ingest tool later. |
| F11 | `tests/test_copilot_tools.py` | No test for ASIN-path `analyze_listing_v2`, mock `provider_source`, `max_tool_rounds`, or execute-without-budget. | **Low** | Add those cases when tightening F1–F3. |

**Not findings (working as designed):** unknown-tool rejection; schema validation; org isolation via History; listing score equality; Rainforest cache; 4-execution cap when budget is passed; second product confirmation when budget is passed.

---

## 8. Security review (future Copilot)

| Question | Answer |
| --- | --- |
| Can future AI call arbitrary Python functions? | **No**, if 11B only `execute`s registered names. There is no `eval`, import-from-name, or MCP. |
| Can it access the database directly? | **No.** Only `AnalysisHistoryService`, which uses SQLAlchemy with bound parameters and `organization_id`. |
| Can it bypass `organization_id`? | **Not via tool JSON.** Isolation is `current_organization_id()`. **Without auth**, every local user shares the default org (existing D1). |
| Can it request Amazon **write** operations? | **No.** No listing, price, ads, or inventory tools. Read/analyze/fetch only. |
| Can it execute expensive providers without confirmation? | **Yes, today:** (1) omit `budget`; (2) first `get_product` / ASIN `analyze_listing_v2`; (3) pass a `product` blob. 11B must close (1) and (3) and decide policy for (2). |

Prompt injection (D11) is **not mitigated** in 11A: titles, bullets, and finding messages will flow into 11B synthesis. That is expected; 11B must mark them untrusted.

Destructive History delete is **not** a tool. Keep it that way for Copilot V1 (architecture §15).

---

## 9. Test review

**File:** `apps/api/tests/test_copilot_tools.py`

| Required coverage | Present |
| --- | --- |
| Unknown tool rejected | Yes |
| Schema validation | Yes (`get_saved_report` / `analyze_listing_v2` empty body) |
| Successful execution | Yes (ping + real tools) |
| Claim kind validation | Yes (invalid kind → `ValidationError`) |
| Envelope generation | Yes |
| Saved report retrieval | Yes |
| Organization isolation | Yes (list + get) |
| Listing output = `ListingAnalysisV2Service` | Yes + single `analyze` call |
| Product uses `ProductService` / cache | Yes (httpx `MockTransport`, count == 1) |
| Mock catalog path | Indirect (`B0TEST0001` in budget test); source `mock` not asserted |
| 4-tool limit | Yes |
| Second expensive call needs confirmation | Yes |
| Live Rainforest | **None** — mock env + `MockTransport` |
| Live OpenAI | **None** — OpenAI not imported by copilot tools |
| Full suite | **372 passed** |

Gaps: ASIN `analyze_listing_v2`, `max_tool_rounds`, fail-closed budget, `seller_provided` provenance. Sufficient for 11A acceptance; not sufficient to lock 11B planner safety.

CI remains offline: `conftest.py` forces `PRODUCT_PROVIDER=mock` and SQLite.

---

## 10. Risks before 11B

1. **Fail-open budget** if the Copilot route calls `execute` without a tracker.
2. **Model-controlled `confirmed`** if arguments are splat into `execute`.
3. **Fabricated Product JSON** scoring as calculated-from-observed.
4. **Thin envelopes** → synthesizer invents section scores or conversion.
5. **Untrusted listing text** in claims without a marker.
6. **First Rainforest fetch is silent** — acceptable per 11A brief; product-facing Copilot should prefer History and confirm cache misses (§8.6).
7. **No auth** — do not host `/copilot` on a public URL (plan risk table).
8. **Do not register write tools, `amazon_public` as a selectable source, or raw SQL.**

11B should also add: conversation persistence (`0004`), hybrid planner, synthesis bound to envelopes, History-first routing, and a public tool catalog without handlers.

---

## 11. Final approval decision

### **B) Approved with minor fixes**

Not **A**: fail-open budget, unenforced round cap, and unconstrained `product` input are too sharp for an LLM planner.

Not **C**: service reuse, tenant path, evidence kinds, registry allow-list, and tests are the right design. No rewrite.

**11A may stay as-is in main.** The High items (F1, F3) are **11B entry criteria**, not a reason to reopen 11A scoring or REST APIs.

Do **not** start Copilot UI, OpenAI planner, or RAG until:

1. `execute` requires a budget (or equivalent fail-closed wrapper)
2. Model-visible `analyze_listing_v2` does not accept a raw `product` (or labels it `seller_provided` + confirm)
3. 11B never sets `confirmed=True` from model JSON

No code was modified during this review.

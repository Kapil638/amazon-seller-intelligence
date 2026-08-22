# Copilot History-First Lookup

**Date:** 21 August 2026  
**Status:** Implemented. History resolution only.  
**Does not change:** Planner AI, Orchestrator execution, Confirmation Gate, Synthesis, Copilot UI.

Copilot must reuse a completed saved listing analysis before asking for a live Amazon lookup.

```text
Analyze ASIN
        |
        v
latest complete History report for this org + ASIN?
        |
   yes /     \ no
      /       \
get_saved_report   analyze_listing_v2
(no credits)       (confirmation + credits)
```

Refresh / re-analyze still uses a live lookup.

---

## Bug

History UI showed a completed report for `B01MD1SKLL`. Copilot “Analyze B01MD1SKLL” still said:

> No saved analysis for this ASIN. Fresh Amazon lookup required.

Cause: Analyze always planned `analyze_listing_v2`. It did not query History. The confirmation copy was shown whenever a paid tool was on the plan, not after a real History miss. `get_saved_report` only ran if the conversation already had `last_report_id`.

History UI lists org reports with no ASIN required. Copilot Analyze never did that lookup.

---

## Implemented

- `AnalysisHistoryService.latest_complete_report_id(asin)`
  - current organization only
  - ASIN normalized (`strip` + case-insensitive)
  - status `complete` or `partial`
  - listing result must exist
  - not deleted
  - latest by `created_at`
- Before tool selection, that report id is bound when the seller did not ask to refresh
- Analyze then selects `get_saved_report` (and `list_saved_reports`)
- No confirmation and no Amazon fetch when a saved report exists
- `list_saved_reports` ASIN filter uses the same normalization

---

## Not implemented

- New tables or migrations
- Planner AI / prompt changes
- Orchestrator or confirmation-gate redesign
- Synthesis or UI changes
- Silent Amazon lookups
- RAG, agents, Amazon writes

---

## Tests

Given a completed report for `B01MD1SKLL`, “Analyze B01MD1SKLL”:

- selects `get_saved_report`
- does not require confirmation
- does not call `ProductService.fetch_product`

No saved report → confirmation still required. Mixed-case ASIN still matches.

`uv run pytest` — **433 passed**.

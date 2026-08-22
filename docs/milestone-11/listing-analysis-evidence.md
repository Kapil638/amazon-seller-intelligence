# Rich Listing Analysis Evidence

**Date:** 21 August 2026  
**Status:** Implemented. Evidence enrichment only.  
**Does not change:** Planner, Plan Validator, Orchestrator, Confirmation Gate, Synthesis architecture, Copilot UI.

Copilot answers improve when listing-analysis evidence is richer. Deterministic Listing Intelligence V2 remains the source of truth. The synthesizer still explains claims; it does not score listings or invent recommendations.

```text
ListingAnalysisV2 (saved JSON, already persisted)
        |
        v
compact claims (score, sections, findings, recommendations)
        |
        v
EvidenceEnvelope
        |
        v
Synthesis + citation checks
        |
        v
Seller answer
```

## Implemented

- Compact listing-analysis claims in `app.copilot.listing_evidence`
- `get_saved_report` and `analyze_listing_v2` include:
  - `listing_quality_score`
  - `section_scores` (title, bullets, description/A+, images, content structure)
  - `findings` and `weaknesses`
  - `recommendations` copied from V2 recommendation actions
- Template fallback cites those claims when present
- Ranking / conversion / PPC language is still rejected

## Not implemented

- New tables or migrations (reports already store full V2 JSON)
- AI-generated business facts
- Planner / orchestrator / UI changes
- RAG, agents, Amazon writes

## Database

No schema change. `listing_analysis_results.payload` already holds `ListingAnalysisV2`, including sections, findings, and recommendations.

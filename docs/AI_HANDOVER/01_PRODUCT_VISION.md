# 01 — Product Vision

ASI helps Amazon sellers make better decisions from **trusted evidence**.

## Long-term combination (do not collapse)

```text
Marketplace intelligence     → Rainforest (public ASIN / search)
Seller-owned operations      → Amazon SP-API
Seller-owned advertising     → Amazon Ads API (future, milestone 12C)
Private costs                → seller-entered COGS and related inputs
```

Python calculates. AI explains. The seller remains in control of Amazon writes (none exist today).

## Near-term product sequence

1. **Done:** listing, profit, ads modeling, Copilot V1, Amazon connection foundation through validation.
2. **Next:** canonical seller identity + marketplace participation (12B.2).
3. **Then:** seller product/listing adapter (12B.3) and a Rainforest vs SP-API ASIN comparison.
4. **Then:** orders, inventory, reports, finances as separate slices.
5. **Then:** attach stable seller-data tools to Copilot (12B.9).
6. **Then:** Ads API (12C).
7. **Later:** richer Copilot orchestration and Skills.

## Non-goals at this stage

- Autonomous multi-agent systems
- LangGraph / CrewAI
- Destructive or unattended Amazon writes
- Replacing Rainforest
- Storing seller refresh tokens in Postgres business columns
- Treating `connected` as “catalog is synced”

# 07 — AI Copilot Architecture

Milestone 11A–11B complete. Domain tools 11D.1 complete. Skills not implemented.

## Contract

```text
User message
  → hybrid planner (rules + optional LLM plan)
  → orchestrator + confirmation gate
  → ToolRegistry.execute(tool, args, budget, confirmed)
  → EvidenceEnvelope
  → synthesizer + citation validator
  → Copilot UI
```

Copilot **does not** call Rainforest, SP-API, or Ads API. Tools wrap Python services.

## EvidenceEnvelope

Trust boundary. Typed claims. Synthesis may only state what evidence supports.

## ToolRegistry

Execution boundary. Planner catalog is metadata only (no handlers in the catalog). `confirmed=True` is application permission; a model JSON `confirmed` key is ignored.

## Skills

`docs/milestone-11d-architecture.md` describes Skills as a future layer **above** Tools. Do not implement Skills in 12B. Do not wrap SP-API as a Skill.

## Amazon + Copilot

12B.1D did **not** add Copilot tools for Amazon connection or Sellers data. 12B.9 is the approved later milestone to connect stable seller-data tools to Copilot.

Do not put `token_reference` or Amazon secrets into evidence, conversation rows, or tool output.

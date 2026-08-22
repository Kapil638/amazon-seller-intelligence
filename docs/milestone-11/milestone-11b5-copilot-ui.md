# Milestone 11B.5 — Seller Copilot UI

**Date:** 21 August 2026  
**Status:** Implemented. First Copilot workspace.  
**Depends on:** 11B.1–11B.4 backend APIs  
**Frontend tests:** `npm test` in `apps/web` — **11 passed**. Backend Copilot contracts were not changed.

---

## What 11B.5 is

A seller workspace at `/copilot`. It is not a ChatGPT clone. The UI shows what Copilot understood, what it did, which evidence it used, and asks before a live Amazon lookup.

```text
Ask
  ↓
POST /conversations/{id}/plan
  ↓
POST /conversations/{id}/execute
  ↓
If confirmation required → modal → POST /confirm
  ↓
POST /synthesize
  ↓
Summary, findings, actions, evidence cards
```

The browser never plans, scores, or grants `confirmed=True`. It only displays backend results.

---

## Route and nav

- New page: `apps/web/src/app/copilot/page.tsx` → `/copilot`
- Copilot added to the existing header next to Analyze, History, Reports, and Bulk
- Analyze / History / Reports / Bulk are unchanged

---

## Screens

| Area | What the seller sees |
| --- | --- |
| Composer | Question box + starter chips (score, saved reports, analyze ASIN, what changed) |
| Conversation | User question and grounded Copilot answer (summary / findings / actions) |
| Activity | “Checked saved analyses”, “Retrieved evidence”, “Prepared answer” |
| Evidence cards | Score, findings, saved-report deep links to `/history/{id}` |
| Confirmation | Continue / Cancel for a credit-consuming Amazon lookup. Nonce stays in memory, not on screen |

---

## APIs used (unchanged)

- `POST /api/v1/copilot/conversations`
- `GET /api/v1/copilot/conversations/{id}`
- `POST /api/v1/copilot/conversations/{id}/plan`
- `POST /api/v1/copilot/conversations/{id}/execute` (`plan_id` + `plan_hash` only)
- `POST /api/v1/copilot/conversations/{id}/confirm` (`nonce` only)
- `POST /api/v1/copilot/synthesize`

Not added: streaming, `/messages` chat, RAG, agents, Amazon writes.

---

## Explicitly not in 11B.5

- New AI capabilities in the frontend
- Streaming tokens
- RAG / vector search
- Autonomous agents
- Amazon write operations
- Backend contract changes

# Milestone 11 — Seller Copilot

Turn Amazon Seller Intelligence from a feature menu of reports into a Copilot that calls **trusted tools** and can open workspaces. Deterministic services remain the source of truth.

**Status:** 11A complete and hardened (`834a79b`). **11B.1–11B.5** implemented (Copilot workspace UI). **11C.1** implemented (Profit Intelligence foundation). 11C.2–11C.4 and 11D–11E not started.

| Document | What it is |
| --- | --- |
| [milestone-11-architecture-review.md](milestone-11-architecture-review.md) | Current-system review and recommended Copilot architecture |
| [milestone-11-plan.md](milestone-11-plan.md) | Phased plan (11A–11E), user stories, acceptance |
| [../milestone-11b-architecture.md](../milestone-11b-architecture.md) | 11B Seller Copilot V1 architecture |
| [../milestone-11c-architecture.md](../milestone-11c-architecture.md) | 11C Seller Profit Intelligence architecture |
| [milestone-11c1-profit-foundation.md](milestone-11c1-profit-foundation.md) | **11C.1 Profit Intelligence foundation** (implemented) |
| [copilot-tool-layer.md](copilot-tool-layer.md) | 11A tool layer behavior (registry, evidence, budgets) |
| [milestone-11a-report.md](milestone-11a-report.md) | 11A initial completion record |
| [milestone-11a-code-review.md](milestone-11a-code-review.md) | Pre-hardening review (B; High items later closed) |
| [milestone-11a-checkpoint.md](milestone-11a-checkpoint.md) | Hardened checkpoint — 11A freeze before 11B |
| [milestone-11b2-hybrid-planner.md](milestone-11b2-hybrid-planner.md) | 11B.2 hybrid planner (implemented) |
| [milestone-11b3-tool-orchestration.md](milestone-11b3-tool-orchestration.md) | 11B.3 orchestrator + confirmation gate (implemented) |
| [milestone-11b4-synthesis.md](milestone-11b4-synthesis.md) | 11B.4 synthesis + citation validator (implemented) |
| [milestone-11b5-copilot-ui.md](milestone-11b5-copilot-ui.md) | **11B.5 Seller Copilot UI** (implemented) |
| [listing-analysis-evidence.md](listing-analysis-evidence.md) | Rich listing-analysis claims for Copilot evidence |
| [copilot-history-first-lookup.md](copilot-history-first-lookup.md) | Reuse saved reports before live Amazon lookup |

**Milestone 11C.1** is implemented (profit engine + `/profit` workspace). Copilot profit/ads tools, Advertising Intelligence, scenarios, and 11D–11E are not started.

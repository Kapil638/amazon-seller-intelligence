# Milestone 11 — Seller Copilot

Turn Amazon Seller Intelligence from a feature menu of reports into a Copilot that calls **trusted tools** and can open workspaces. Deterministic services remain the source of truth.

**Status:** 11A complete and hardened (`834a79b`). **11B.1–11B.5** implemented (Copilot workspace UI). **11C.1** implemented (Profit Intelligence foundation). **11C.2** implemented (Advertising Intelligence foundation). **11D Skill Architecture** is specified (not implemented). **11D.1 Copilot domain tools** implemented (profit + advertising tools; not Skills). 11C.3–11C.4, Skill implementation, and 11E not started.

Amazon SP-API connection work is **Milestone 12** (through **12B.1D** as of 24 August 2026). See [../milestone-12/README.md](../milestone-12/README.md). Do not treat Copilot as calling SP-API.

| Document | What it is |
| --- | --- |
| [milestone-11-architecture-review.md](milestone-11-architecture-review.md) | Current-system review and recommended Copilot architecture |
| [milestone-11-plan.md](milestone-11-plan.md) | Phased plan (11A–11E), user stories, acceptance |
| [../milestone-11b-architecture.md](../milestone-11b-architecture.md) | 11B Seller Copilot V1 architecture |
| [../milestone-11c-architecture.md](../milestone-11c-architecture.md) | 11C Seller Profit Intelligence architecture |
| [milestone-11c1-profit-foundation.md](milestone-11c1-profit-foundation.md) | **11C.1 Profit Intelligence foundation** (implemented) |
| [../milestone-11c2-architecture.md](../milestone-11c2-architecture.md) | **11C.2 Advertising Intelligence architecture** |
| [milestone-11c2-architecture-checkpoint.md](milestone-11c2-architecture-checkpoint.md) | **11C.2 architecture validation review** (approved) |
| [../adr/0001-advertising-intelligence-domain-boundary.md](../adr/0001-advertising-intelligence-domain-boundary.md) | **ADR:** Advertising Intelligence domain boundary |
| [milestone-11c2-advertising-foundation.md](milestone-11c2-advertising-foundation.md) | **11C.2 Advertising Intelligence foundation** (implemented) |
| [../milestone-11d-architecture.md](../milestone-11d-architecture.md) | **11D Skill Architecture Foundation** (architecture only; not implemented) |
| [milestone-11d1-copilot-domain-tools.md](milestone-11d1-copilot-domain-tools.md) | **11D.1 Copilot domain tools** for profit and advertising (implemented; not Skills) |
| [../checkpoints/pre-amazon-api-data-backbone-checkpoint.md](../checkpoints/pre-amazon-api-data-backbone-checkpoint.md) | **Checkpoint:** freeze before SP-API / Ads API (through 11D.1) |
| [../checkpoints/post-data-backbone-resume-plan.md](../checkpoints/post-data-backbone-resume-plan.md) | Resume Skill work after connected Amazon data is mature |
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

**Milestone 11C.1** is implemented (profit engine + `/profit` workspace). **11C.2** is implemented (advertising engine + panel inside `/profit/[id]`). Copilot can read profit and advertising snapshots through ToolRegistry (11D.1). Skills, scenarios, and 11E are not started. The August 2026 plan’s “11D Business Diagnostic V0” remains a **future Skill**, not this architecture milestone.

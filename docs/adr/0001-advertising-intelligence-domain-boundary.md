# ADR 0001 — Advertising Intelligence Domain Boundary

**Status:** Accepted  
**Date:** 22 August 2026  
**Milestone:** 11C.2 — Advertising Intelligence Foundation  
**Deciders:** Principal architecture freeze ([architecture validation review](../milestone-11/milestone-11c2-architecture-checkpoint.md))

---

## Title

Advertising Intelligence Domain Boundary

---

## Context

ASI already has Listing Intelligence (construction), Seller Reports (operational PPC diagnostics), Profit Intelligence (unit economics before ads), and Seller Copilot (explanation of evidence). Sellers still cannot answer whether period advertising spend leaves the unit profitable.

A long-term Amazon Seller Skill Playbook describes business capabilities such as Advertising Optimization. Skills are **not** part of 11C.2. Tools remain below Skills. This decision records the domain boundary so later Skills can consume Advertising Intelligence without collapsing it into Profit, Seller Reports, or Copilot.

---

## Decision

Advertising Intelligence is an independent deterministic intelligence domain.

It composes with Profit Intelligence but does not modify Profit calculations.

Future Skills may orchestrate Advertising tools.

Tools remain below Skills.

EvidenceEnvelope remains the trust boundary.

Consequences of this decision:

- `ads-calc-v1` owns ACOS, TACOS, and ROAS. `profit-calc-v1` is unchanged.
- After-ads impact is a composition of a cited profit snapshot and an advertising snapshot (`profit_snapshot_id` is preserved).
- Advertising data lives in `advertising_models` / `advertising_snapshots`, not as columns on `profit_models`.
- Copilot, Listing Intelligence, and Seller Reports are not modified in 11C.2.
- Amazon Ads API later replaces **collection** only. It does not rewrite this engine.
- No Amazon writes. No Skill layer, LangGraph, CrewAI, or autonomous agents in this milestone.

---

## Consequences

**Positive**

- Trusted, reproducible advertising metrics with unknown handling instead of invented zeros.
- A future Advertising Optimization Skill can call advertising, profit, and listing tools without owning formulas.
- Seller Reports remains campaign/search-term diagnostics; `HIGH_ACOS` is not a P&L verdict.

**Negative / accepted**

- Profit V1 is unit-based (`calculated_at`); advertising is period-based. After-ads is not a matched monthly book. The workspace must show both identities and warn if the cited profit snapshot is stale.
- Manual seller inputs can be wrong. Source and period on every snapshot make later Ads API ingest a collection swap, not a rewrite.

---

## Not in this decision

Campaign optimization, bid writes, keyword harvesting, Copilot advertising tools, scenario modeling, and Skill implementation.

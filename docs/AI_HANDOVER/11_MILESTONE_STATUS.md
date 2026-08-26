# 11 — Milestone Status

Historical slice docs remain in `docs/milestone-11/` and `docs/milestone-12/`. This page is the **current** index.

## Milestone 11 — Seller Copilot & intelligence

| Slice | Status |
| --- | --- |
| 11A ToolRegistry / EvidenceEnvelope | **Completed** |
| 11B.1–11B.5 Copilot foundation + UI | **Completed** |
| 11C.1 Profit Intelligence | **Completed** |
| 11C.2 Advertising Intelligence | **Completed** |
| 11D Skill architecture | Architecture only; **not implemented** |
| 11D.1 Copilot domain tools (profit/ads) | **Completed** (tools, not Skills) |
| 11C.3–11C.4, 11E | Not started |

## Milestone 12 — Amazon-connected data backbone

| Slice | Status |
| --- | --- |
| 12A.0 SP-API sandbox connectivity | **Completed** |
| 12A.1 Amazon Connection Beta | **Completed** |
| 12B Canonical seller-data architecture | **Completed / architecture approved** (ADRs 0002–0006) |
| 12B.1 Production connection + security architecture | Architecture approved |
| 12B.1A Connection metadata persistence | **Completed** |
| 12B.1B SecretProvider foundation | **Completed** (dev backend; production fail-closed) |
| 12B.1C Seller authorization | **Implemented through 12B.1C.5** |
| 12B.1C.3 Frontend Connect Amazon | Implemented (no separate slice markdown historically) |
| 12B.1D Seller connection validation | **Completed** (latest Amazon implementation) |
| 12B.2A Canonical seller identity schema foundation | **Completed** (schema + migration `0009` + OAuth callback identity capture; not wired to live ingest) |
| 12B.2 Canonical seller identity + marketplace ingest (remainder) | **Next. Not started.** |
| 12B.3 Listings / seller product adapter | Not started |
| 12B.4–12B.9 | Not started |
| 12C Ads API | Not started |

Prior Git freeze: tag `amazon-api-foundation-v1` at `67f5412` (through 12B architecture docs, before 12B.1A code).

## Naming

Do **not** rename 12B.2+ ingest to 12C. Ads API remains 12C.

# 00 — Project Overview

**Product:** Amazon Seller Intelligence (ASI)  
**Repository:** `https://github.com/Kapil638/amazon-seller-intelligence.git`  
**Branch:** `main`  
**Date of this handover:** 24 August 2026  
**Latest completed Amazon milestone:** 12B.1D Seller Connection Validation Using SP-API

ASI is a local-first monorepo:

| App | Path | Role |
| --- | --- | --- |
| FastAPI | `apps/api` | Business backend, persistence, Amazon SP-API isolation, Copilot, intelligence engines |
| Next.js | `apps/web` | Seller UI. Calls FastAPI only. Never holds provider keys or Amazon tokens. |

This handover is a Cursor → Claude checkpoint. It is **not** permission to start 12B.2 implementation until Claude first validates architecture against the repo.

## What ASI is today

A seller intelligence workbench:

- Public marketplace listing intelligence (Rainforest)
- Deterministic listing quality, profit, and advertising math
- Optional OpenAI language layers on top of Python evidence
- Seller Copilot that calls ToolRegistry, not providers
- Amazon SP-API connection/authorization/validation foundation (no business-data ingest yet)

## What ASI is not

- Not an Amazon write tool
- Not an autonomous agent platform
- Not a Rainforest replacement via SP-API
- Not a production SecretProvider / cloud KMS implementation
- Not a fully proven live seller OAuth handshake (code exists; Amazon-side HTTPS + Login URI remain incomplete)

## Source of truth

When docs conflict:

1. Code and tests
2. ADRs in `docs/adr/`
3. Slice completion docs in `docs/milestone-12/`
4. This handover package
5. Older milestone/completion notes (keep as history)

Do not silently delete historical milestone records.

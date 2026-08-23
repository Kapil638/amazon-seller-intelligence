# Milestone 12A.1 — Amazon Connection Beta Foundation

**Date:** 23 August 2026  
**Status:** Implemented. Foundation only.  
**Depends on:** Milestone 12A.0 SP-API sandbox connectivity.

Create the first ASI Amazon-connected product area: a Connection Beta page that shows sandbox connection status and runs a sanitized connectivity test. This is not ingestion, not Ads API, not Copilot, and not Skills.

---

## 1. Objective

ASI can manage and test Amazon seller-data connectivity without disturbing marketplace intelligence.

The page proves:

- SP-API sandbox configuration is visible
- a connectivity test can be invoked from the product
- responses never include secrets
- Rainforest and SP-API remain separate bounded contexts

---

## 2. Architecture

Amazon seller connectivity lives in `apps/api/app/amazon/`. Marketplace intelligence stays in `apps/api/app/providers/rainforest.py`.

```text
Frontend  /connection
    GET  /api/v1/amazon/connection
    POST /api/v1/amazon/connection/test
        ↓
AmazonConnectionService
        ↓
AmazonSpApiSandboxClient (12A.0)
        ↓
LWA refresh-token grant
        ↓
Sellers API getMarketplaceParticipations (sandbox)
        ↓
Sanitized status only
```

12A.0 files were not restructured into a nested `sp_api/` package. The existing `lwa.py` / `sandbox.py` / `models.py` layout is the SP-API client. Connection Beta wraps it.

Suggested nested folders from the milestone brief were not applied where they would break 12A.0. Rainforest was not moved under `app/amazon/`.

---

## 3. Provider separation decision

See [ADR 0002](../adr/0002-amazon-data-provider-separation.md).

Rainforest, SP-API, and Ads API are complementary sources. They must not be merged behind a generic Amazon provider.

---

## 4. Rainforest vs SP-API responsibility

| Source | Purpose | Examples | Location |
| --- | --- | --- | --- |
| Rainforest | Marketplace intelligence | public listing data, competitor signals, external Amazon marketplace information | `app/providers/rainforest.py` |
| SP-API | Seller intelligence | seller-owned listings, catalog, orders, inventory, financials, account data | `app/amazon/` |
| Ads API | Advertising collection (future) | sponsored ads reports, campaign performance collection | placeholder only |

This milestone does not fetch seller operational data. SP-API is used only to prove that the seller connection works.

---

## 5. Connection lifecycle

V1 lifecycle is configuration + on-demand test.

1. Operator stores LWA sandbox credentials in `apps/api/.env` (gitignored).
2. `GET /api/v1/amazon/connection` returns configuration metadata. It does not call Amazon.
3. Seller/operator clicks **Test Connection**.
4. `AmazonConnectionService` checks that credentials exist, then invokes the 12A.0 sandbox client.
5. Frontend displays `CONNECTED`, `FAILED`, or `NOT_CONNECTED` plus a timestamp.

There is no seller OAuth, no production authorization, and no multi-account onboarding in this milestone.

### Persistence decision

**No Amazon connection table in V1.**

Reasons:

- Credentials remain environment-managed, not row-managed
- There is no per-organization OAuth token yet, so a connection row would overstate org isolation
- Last-test state can live in the page session
- Operational Amazon tables (orders, inventory, catalog, financials) belong to later data-backbone milestones

`AmazonConnection` remains a conceptual model. Future OAuth onboarding should persist metadata (id, organization_id, provider, environment, marketplace, status, last_test_at) without storing secrets.

V1 still returns `organization_id` from the current default organization so the API stays organization-aware. It is not a stored connection record.

---

## 6. Security approach

- Secrets stay in backend `.env`. They are never sent to Next.js.
- `SecretStr` continues to mask tokens in LWA DTOs.
- Connection responses are extra-forbid Pydantic models with only public fields.
- Routes run `public_model_dump` before returning so credential-shaped keys cannot leak.
- Logs record status reasons (`missing_credentials`, `authentication`, `sandbox_unavailable`) and never tokens or headers.
- Tests assert responses do not contain `access_token`, `refresh_token`, `client_secret`, or `x-amz-access-token`.
- Automated tests never call Amazon live. The sandbox client is injected/mocked.

---

## 7. Current limitations

- Sandbox only. Production SP-API is not authorized.
- No seller OAuth consent flow.
- No multi-account or per-org stored connections.
- No Amazon data ingest, sync scheduler, or background jobs.
- Ads API is a placeholder (`NOT_CONNECTED`).
- Copilot, ToolRegistry, EvidenceEnvelope, and Skills are unchanged.
- Last successful test is not persisted across reloads.

---

## 8. Future roadmap

**Milestone 12B — SP-API Seller Data Adapter and Canonical Data Model Architecture**

Design how seller-owned Amazon data is adapted into ASI canonical models without merging Rainforest.

Later:

- production seller authorization
- org-scoped connection records (metadata only)
- orders / inventory / catalog / financial ingest
- Ads API collection swapped into Advertising Intelligence without rewriting `ads-calc-v1`

---

## Product surface

Page: `/connection`  
Nav label: **Connection**  
Title: **Amazon Seller Connection (Beta)**

Does not include a seller dashboard, analytics, reports, or sync UI.

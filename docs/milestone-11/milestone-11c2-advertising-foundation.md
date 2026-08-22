# Milestone 11C.2 — Advertising Intelligence Foundation

**Date:** 22 August 2026  
**Status:** Implemented.  
**Depends on:** [milestone-11c2-architecture.md](../milestone-11c2-architecture.md), [milestone-11c1-profit-foundation.md](milestone-11c1-profit-foundation.md).  
**Backend tests:** `uv run pytest` in `apps/api` — **464 passed**.  
**Frontend tests:** `npm test` in `apps/web` — **19 passed**.

This document is the 11C.2 completion record. It does not implement Ads API ingest, Copilot ads tools, or scenario modeling.

---

## 1. Product

The first **Advertising Intelligence** engine, nested in the Profit workspace. A seller can:

- Add advertising data on a profit model
- Calculate ACOS, TACOS, and ROAS
- See after-ads unit profit next to unit economics
- Keep historical advertising snapshots
- Read evidence-backed advertising claims

Python owns all advertising math. The browser does not calculate ACOS/TACOS/ROAS. Copilot reads advertising snapshots through ToolRegistry (11D.1). Skills are not implemented.

```text
Seller advertising inputs
  ↓
AdvertisingModelingService (CRUD, org scope, snapshots)
  ↓
AdvertisingCalculationService / ads-calc-v1 (Decimal math only)
  ↓
AdvertisingImpactService (compose with profit snapshot; no formula rewrite)
  ↓
Immutable advertising_snapshots + EvidenceEnvelope claims
  ↓
/profit/[id] Advertising Intelligence section (display only)
```

---

## 2. Files changed

### Backend (new)

| Path | Role |
| --- | --- |
| `apps/api/app/analytics/advertising_rules.py` | `ads-calc-v1` formulas |
| `apps/api/app/services/advertising_calculation_service.py` | Pure calculation façade (no DB, no AI) |
| `apps/api/app/services/advertising_impact_service.py` | After-ads composition |
| `apps/api/app/services/advertising_modeling_service.py` | Worksheet / snapshot lifecycle |
| `apps/api/app/models/advertising.py` | Request/response DTOs |
| `apps/api/app/advertising/evidence.py` | Compact claims on existing `EvidenceEnvelope` |
| `apps/api/app/api/routes/advertising.py` | HTTP routes |
| `apps/api/migrations/versions/0006_advertising_models.py` | New migration only |
| `apps/api/tests/test_advertising_intelligence.py` | Engine, impact, API, isolation, immutability |

### Backend (existing, additive)

| Path | Change |
| --- | --- |
| `apps/api/app/api/routes/__init__.py` | Mount advertising router |
| `apps/api/app/core/exceptions.py` | `AdvertisingValidationError` |
| `apps/api/app/persistence/models.py` | `AdvertisingModel`, `AdvertisingSnapshot` |
| `apps/api/app/persistence/repositories.py` | `AdvertisingModelRepository` |

### Frontend (existing, additive)

| Path | Change |
| --- | --- |
| `apps/web/src/components/seller-profit.tsx` | Advertising section inside `/profit/[id]` |
| `apps/web/src/lib/profit-view.ts` | Display mappers (no math) |
| `apps/web/src/lib/api.ts` | Advertising HTTP client |
| `apps/web/src/lib/types.ts` | Advertising types |
| `apps/web/src/components/profit-ui.test.tsx` | Display / payload tests |

### Docs

| Path | Role |
| --- | --- |
| `docs/milestone-11/milestone-11c2-advertising-foundation.md` | This completion record |
| `docs/adr/0001-advertising-intelligence-domain-boundary.md` | ADR: Advertising Intelligence domain boundary |
| `docs/milestone-11/README.md` | Index |
| `docs/milestone-11c-architecture.md` | Status note: 11C.2 shipped |
| `docs/milestone-11c2-architecture.md` | Architecture freeze pointer |
| `docs/database-schema.md` | `advertising_models` / `advertising_snapshots` |
| `README.md` | Product surface + APIs |

**Not modified:** Copilot planner, ToolRegistry, orchestrator, confirmation gate, synthesis, citation validator, Listing Intelligence, Seller Reports, `profit-calc-v1`.

---

## 3. Database migrations

**New only:** `0006_advertising_models`  
**Revises:** `0005_profit_models`  
**Did not modify:** `0001`–`0005`

### `advertising_models`

Editable seller advertising worksheet. Unique `profit_model_id`.

Fields: `id`, `organization_id`, `profit_model_id`, `asin`, `marketplace`, `currency`, `period_start`, `period_end`, `ad_spend`, `ad_sales`, `total_sales`, `units_in_period`, `source` (`seller_input`; future `ads_api`), `created_at`, `updated_at`.

### `advertising_snapshots`

Immutable calculation history. Never updated. Each calculate call inserts a new row.

Fields: `id`, `organization_id`, `advertising_model_id`, `profit_model_id`, `status` (`complete` / `partial` / `failed`), `ads_formula_version` (`ads-calc-v1`), `inputs_json`, `outputs_json`, `completeness_json`, `impact_json`, `profit_snapshot_id`, `calculated_at`.

Apply (when Postgres is configured):

```bash
cd apps/api
uv run alembic upgrade head
```

SQLite tests create tables from SQLAlchemy metadata; they do not run Alembic.

---

## 4. Backend services created

| Service | Responsibility |
| --- | --- |
| `AdvertisingCalculationService` | Pure `ads-calc-v1` ACOS / TACOS / ROAS. No DB, no AI, no APIs. |
| `AdvertisingModelingService` | Worksheet lifecycle, org isolation, snapshot create/retrieve. No formulas. |
| `AdvertisingImpactService` | Compose profit snapshot + ads snapshot. Does not rewrite `profit-calc-v1`. |

---

## 5. API endpoints added

Existing Analyze / History / Reports / Bulk / Copilot / Profit routes were not changed except mounting the advertising router.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/v1/profit/models/{id}/advertising` | Inputs + latest snapshot + impact |
| PATCH | `/api/v1/profit/models/{id}/advertising` | Update **inputs** only |
| POST | `/api/v1/profit/models/{id}/advertising/calculate` | Persist a new snapshot |
| GET | `/api/v1/profit/models/{id}/advertising/snapshots` | History |
| POST | `/api/v1/advertising/preview` | Stateless calculate |

**API rules**

- Frontend never sends `acos`, `tacos`, `roas`, or `net_profit_after_ads` as truth
- Extra client-calculated keys are ignored (`extra="ignore"`)
- Org A cannot read/patch/calculate org B models (404)
- All queries use `organization_id`

---

## 6. Calculation formulas implemented

**Version:** `ads-calc-v1`  
**Service:** `AdvertisingCalculationService` → `app.analytics.advertising_rules.calculate_advertising`  
**Money type:** `Decimal` (never `float` in the engine)

```text
acos  = ad_spend / ad_sales     when ad_sales > 0, else unknown
tacos = ad_spend / total_sales  when total_sales > 0, else unknown
roas  = ad_sales / ad_spend     when ad_spend > 0, else unknown
```

**Impact (composition only)**

```text
ad_spend_per_unit     = ad_spend / units_in_period   when units_in_period > 0
net_profit_after_ads  = net_profit_before_ads - ad_spend_per_unit
break_even_acos       = margin_before_ads            copied, not recalculated
```

**Unknown handling**

- Missing `ad_sales` → ACOS unknown (do not return zero)
- Missing `total_sales` → TACOS unknown (never copy ACOS into TACOS)
- Missing `ad_spend` → ROAS unknown and after-ads profit unknown
- Missing units → after-ads unit profit unknown
- Incomplete profit snapshot → after-ads profit unknown
- Zero denominators → `null`, not `0`
- No estimates, no AI financial assumptions

**Golden case**

| Input | Value |
| --- | --- |
| Ad spend | ₹320 |
| Ad sales | ₹1,000 |
| Total sales | ₹2,000 |
| Units | 10 |
| Net profit before ads | ₹379.00 |

| Output | Value |
| --- | --- |
| ACOS | 0.320000 (32.0% in UI) |
| TACOS | 0.160000 (16.0% in UI) |
| ROAS | 3.125000 (3.13x in UI) |
| Ad spend per unit | ₹32.00 |
| Net profit after ads | ₹347.00 |
| Break-even ACOS | 0.379379 |

---

## 7. Evidence changes

No `AdvertisingEvidenceEnvelope`. Reuses `EvidenceEnvelope` / `EvidenceClaim`.

Builder: `apps/api/app/advertising/evidence.py`  
`tool_name`: `advertising_calculation`

| Claim keys | Kind | Source |
| --- | --- | --- |
| `ad_spend`, `ad_sales`, `total_sales`, `units_in_period`, period | `seller_provided` | `seller_input` |
| `acos`, `tacos`, `roas`, `ads_formula_version` | `calculated` | `ads-calc-v1` |
| `net_profit_after_ads`, `ad_spend_per_unit`, `break_even_acos` | `calculated` | `advertising_impact` |
| Missing values | `unknown` | matching source, `confidence=none` |

Period `as_of` is set from `period_end` on calculated claims. Envelopes also cite `advertising_snapshot_id` and `profit_snapshot_id`. Completeness messages explain missing TACOS/ACOS. After-ads unknown copy: `After-ads profit unavailable because units are missing.`

---

## 8. Frontend changes

No `/advertising` navigation item.

`/profit/[id]` now shows:

1. Unit Economics (existing profit-calc-v1 worksheet)
2. Advertising Intelligence (period inputs, calculate, metrics, history)

The UI displays API results only. Unknown copy examples: `TACOS unavailable because total sales are missing.` and `After-ads profit unavailable because units are missing.` Break-even ACOS is labeled as pre-ads margin, not a TACOS cap, with volume and other costs assumed constant. If unit economics changed since the cited profit snapshot, the workspace warns the seller to recalculate advertising impact. Old snapshots are read-only; editing inputs and calculating creates a new snapshot.

---

## 9. Tests added

`apps/api/tests/test_advertising_intelligence.py`

- ACOS / TACOS / ROAS formulas and Decimal precision
- Missing values and zero denominators
- Impact: before-ads profit minus spend per unit
- Missing units / profit / spend → unknown
- Snapshot immutability (recalculate inserts a new row)
- Stale `profit_snapshot_id` after unit recalculate
- Organization isolation → 404
- Client-calculated metrics ignored on preview
- Engine files must not import AI, Rainforest, or PPC analytics

`apps/web/src/components/profit-ui.test.tsx`

- Advertising payloads omit `acos` / `tacos` / `roas`
- Advertising Intelligence renders inside the profit model

Regression: existing `profit-calc-v1`, Listing Intelligence, Seller Reports, and Copilot tests remain in the suite.

---

## 10. Test results

| Suite | Command | Result |
| --- | --- | --- |
| API | `cd apps/api && uv run pytest` | **464 passed** |
| Web | `cd apps/web && npm test` | **19 passed** |

---

## 11. Confirmations

- Copilot untouched (planner, ToolRegistry, orchestrator, confirmation, synthesis, citation validator)
- Listing Intelligence untouched
- Seller Reports / `PPCAnalyticsService` untouched
- `ProfitCalculationService` / `profit-calc-v1` formulas untouched
- No AI financial calculations
- No Amazon Ads API or other external ads calls
- No Amazon write actions
- No Skills, LangGraph, CrewAI, or agents
- Snapshot history remains immutable

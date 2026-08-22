# Milestone 11C.1 — Profit Intelligence Foundation

**Date:** 21 August 2026  
**Status:** Implemented.  
**Depends on:** [milestone-11c-architecture.md](../milestone-11c-architecture.md) (Option B, slice 11C.1 only).  
**Backend tests:** `uv run pytest` in `apps/api` — **449 passed**.  
**Frontend tests:** `npm test` in `apps/web` — **16 passed**.

This document is the 11C.1 completion record. It does not implement remaining 11C slices.

---

## 1. Product

The first **Seller Profit Intelligence** engine and workspace. A seller can:

- Create a profit model for an ASIN
- Enter product economics
- Calculate profitability
- View results in a workspace
- See evidence-backed numbers
- Understand missing inputs

Python owns all money math. The browser does not calculate profit. Copilot does not call profit tools yet (11C.4).

```text
Seller inputs
  ↓
ProfitModelingService (CRUD, org scope, snapshots)
  ↓
ProfitCalculationService / profit-calc-v1 (Decimal math only)
  ↓
Immutable profit_snapshots + EvidenceEnvelope claims
  ↓
/profit workspace (display only)
```

---

## 2. Files changed

### Backend (new)

| Path | Role |
| --- | --- |
| `apps/api/app/analytics/profit_rules.py` | `profit-calc-v1` formulas |
| `apps/api/app/services/profit_calculation_service.py` | Pure calculation façade (no DB, no AI) |
| `apps/api/app/services/profit_modeling_service.py` | Create / update / calculate / list |
| `apps/api/app/models/profit.py` | Request/response DTOs |
| `apps/api/app/profit/evidence.py` | Compact claims on existing `EvidenceEnvelope` |
| `apps/api/app/api/routes/profit.py` | HTTP routes |
| `apps/api/migrations/versions/0005_profit_models.py` | New migration only |
| `apps/api/tests/test_profit_intelligence.py` | Engine, API, isolation, immutability |

### Backend (existing, additive)

| Path | Change |
| --- | --- |
| `apps/api/app/api/routes/__init__.py` | Mount profit router |
| `apps/api/app/core/exceptions.py` | Profit validation / not-found / conflict |
| `apps/api/app/persistence/models.py` | `ProfitModel`, `ProfitSnapshot` |
| `apps/api/app/persistence/repositories.py` | `ProfitModelRepository` |

### Frontend (new)

| Path | Role |
| --- | --- |
| `apps/web/src/app/profit/page.tsx` | `/profit` |
| `apps/web/src/app/profit/[id]/page.tsx` | `/profit/[id]` |
| `apps/web/src/components/seller-profit.tsx` | Workspace UI |
| `apps/web/src/lib/profit-view.ts` | Display mappers (no math) |
| `apps/web/src/components/profit-ui.test.tsx` | Display / payload tests |

### Frontend (existing, additive)

| Path | Change |
| --- | --- |
| `apps/web/src/components/app-shell.tsx` | Nav item **Profit** |
| `apps/web/src/lib/api.ts` | Profit HTTP client |
| `apps/web/src/lib/types.ts` | Profit types |

### Docs

| Path | Role |
| --- | --- |
| `docs/milestone-11/milestone-11c1-profit-foundation.md` | This completion record |
| `docs/milestone-11/README.md` | Index |
| `docs/milestone-11c-architecture.md` | Status note: 11C.1 shipped |
| `docs/database-schema.md` | `profit_models` / `profit_snapshots` |
| `README.md` | Product surface + APIs |

**Not modified:** Copilot planner, ToolRegistry, orchestrator, confirmation gate, synthesis, citation validator, Listing Intelligence, Seller Reports.

---

## 3. Database migrations

**New only:** `0005_profit_models`  
**Revises:** `0004_copilot_conversations`  
**Did not modify:** `0001`–`0004`

### `profit_models`

Editable seller worksheet. Unique `(organization_id, asin, marketplace)`.

Fields: `id`, `organization_id`, `asin`, `marketplace`, `currency`, `selling_price`, `selling_price_source`, `cogs`, `shipping_cost`, `packaging_cost`, `other_cost`, `referral_fee_amount`, `fba_fee_amount`, `fee_category_key`, `created_at`, `updated_at`.

### `profit_snapshots`

Immutable calculation history. Never updated. Each calculate call inserts a new row.

Fields: `id`, `organization_id`, `profit_model_id`, `status` (`complete` / `partial` / `failed`), `profit_formula_version` (`profit-calc-v1`), `inputs_json`, `outputs_json`, `completeness`, `calculated_at`.

Apply (when Postgres is configured):

```bash
cd apps/api
uv run alembic upgrade head
```

SQLite tests create tables from SQLAlchemy metadata; they do not run Alembic.

---

## 4. API endpoints added

Existing Analyze / History / Reports / Bulk / Copilot routes were not changed.

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/v1/profit/models` | Create model |
| GET | `/api/v1/profit/models` | List for current org (`?asin=` optional) |
| GET | `/api/v1/profit/models/{id}` | Model + latest snapshot |
| PATCH | `/api/v1/profit/models/{id}` | Update **inputs** only |
| POST | `/api/v1/profit/models/{id}/calculate` | Persist a new snapshot |
| POST | `/api/v1/profit/preview` | Stateless calculate |

**API rules**

- Frontend never sends `net_profit`, `margin`, or `ROI` as truth
- Extra client-calculated keys are ignored (`extra="ignore"`)
- Org A cannot read/patch/calculate org B models (404)

---

## 5. Calculation formulas implemented

**Version:** `profit-calc-v1`  
**Service:** `ProfitCalculationService` → `app.analytics.profit_rules.calculate_profit`  
**Money type:** `Decimal` (never `float` in the engine)

```text
amazon_fees              = referral_fee + fba_fee
operating_costs          = shipping + packaging + other_cost
landed_cost              = cogs + amazon_fees + operating_costs
net_profit_before_ads    = selling_price - landed_cost
margin_before_ads        = net_profit_before_ads / selling_price
roi_on_cogs              = net_profit_before_ads / cogs
```

**Unknown handling**

- Missing COGS → do **not** calculate profit
- `net_profit_before_ads` / `margin_before_ads` / `roi_on_cogs` are `unknown`
- Message: `The product profitability cannot be calculated because COGS is missing.`
- Blank cost lines are not treated as zero
- Zero denominators → `null`, not `0`
- No category averages, no AI estimates

**Golden case**

| Input | Value |
| --- | --- |
| Selling price | ₹999 |
| COGS | ₹350 |
| Referral + FBA | ₹270 |
| Shipping / packaging / other | ₹0 |

| Output | Value |
| --- | --- |
| Net profit | ₹379.00 |
| Margin | 0.379379 (37.9% in UI) |
| ROI on COGS | 1.082857 |

---

## 6. Evidence changes

No new envelope type. Reuses `EvidenceEnvelope` / `EvidenceClaim`.

Builder: `apps/api/app/profit/evidence.py`  
`tool_name`: `profit_calculation`

| Claim keys | Kind | Source |
| --- | --- | --- |
| `asin`, `marketplace`, `currency` | `seller_provided` | `seller_input` |
| `selling_price`, `cogs`, `referral_fee`, `fba_fee`, shipping/packaging/other | `seller_provided` or `unknown` | `seller_input` |
| `amazon_fees`, `landed_cost`, `net_profit_before_ads`, `margin_before_ads`, `roi_on_cogs` | `calculated` or `unknown` | `profit-calc-v1` |
| `profit_formula_version`, `status`, `completeness` | `calculated` | `profit-calc-v1` |

---

## 7. Frontend pages created

| Route | File |
| --- | --- |
| `/profit` | `apps/web/src/app/profit/page.tsx` |
| `/profit/[id]` | `apps/web/src/app/profit/[id]/page.tsx` |

Nav: **Profit** in `app-shell.tsx` (not only inside Copilot).

Workspace panels:

- **Inputs** — selling price, COGS, referral fee, FBA fee, shipping, packaging, other costs
- **Outputs** — net profit, margin, ROI (from API only)
- **Evidence** — source, `profit-calc-v1`, assumptions, unknown fields

---

## 8. Tests added

`apps/api/tests/test_profit_intelligence.py`

- Correct profit calculation and `Decimal` precision
- Zero-division → `null`
- Missing COGS → unknown profit + required message
- Missing cost line does not invent `0`
- Create model, calculate snapshot, retrieve latest
- Snapshots immutable (second calculate inserts a new row)
- Organization isolation (other-org model → 404)
- Client-calculated `net_profit` / `margin` / `roi` ignored
- Calculation layer has no OpenAI / Rainforest / planner imports

`apps/web/src/components/profit-ui.test.tsx`

- Formats API strings; does not recalculate
- Save payload has no `net_profit` / `margin` / `roi`
- Missing-COGS message from snapshot
- Profit workspace heading renders

---

## 9. Test results

| Suite | Command | Result |
| --- | --- | --- |
| API | `cd apps/api && uv run pytest` | **449 passed** |
| Web | `cd apps/web && npm test` | **16 passed** |

---

## 10. Confirmations

| Rule | Status |
| --- | --- |
| Copilot planner / ToolRegistry / orchestrator / confirmation / synthesis / citation validator untouched | Yes |
| Listing Intelligence untouched | Yes |
| Seller Reports untouched | Yes |
| No AI financial calculations | Yes |
| No Rainforest usage in profit calculation | Yes |
| No SP-API / Ads API | Yes |
| No Amazon write capability | Yes |
| Historical snapshots immutable | Yes |
| Unknown values remain unknown | Yes |

---

## 11. Explicitly not in 11C.1

- Copilot profit tools
- Planner intent changes
- ACOS / TACOS / break-even ACOS
- Scenario modeling
- Fee catalog automation
- Inventory, forecasting, cash flow
- Autonomous Amazon actions

Next slices (from architecture): 11C.2 advertising inputs, 11C.3 scenarios, 11C.4 Copilot tools + workspace dispatch.

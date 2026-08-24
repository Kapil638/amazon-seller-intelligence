# Milestone 12B — SP-API Seller Data Adapter & Canonical Data Model Architecture

**Date:** 23 August 2026  
**Role:** Principal Architect  
**Status:** Architecture approved. Seller-data **ingestion remains not implemented**. Connection/authorization/validation through 12B.1D does not satisfy this document’s ingest design. Next ingest slice is **12B.2**. Do not implement listings/orders from this file.  
**Depends on (frozen):** 11A–11D.1, ADR 0001, pre–Amazon data backbone checkpoint, 12A.0, 12A.1, ADR 0002  
**Companions:** [ADR 0003](../adr/0003-canonical-amazon-seller-data-model.md), [ADR 0004](../adr/0004-seller-data-provenance-and-source-precedence.md), [ADR 0005](../adr/0005-amazon-seller-identity-model.md)

This document answers:

> How does seller-owned Amazon data enter ASI, get normalized, retain source / provenance / history, and become safely consumable by Listing, Profit, Advertising, future engines, ToolRegistry, EvidenceEnvelope, Copilot, and later Skills?

It does **not** implement that path.

---

## 1. Executive Summary

ASI already understands **the marketplace around the seller** (Rainforest, History, Listing Intelligence) and **seller-private economics** (COGS, modeled fees, advertising worksheets). It does not yet understand **the seller’s own Amazon business** as durable, organization-scoped data.

Milestone 12A proved connectivity. 12A.1 exposed a Connection Beta page. **12B designs the data backbone those later ingestions will populate.**

The forbidden pattern is:

```text
Copilot → raw SP-API call → raw JSON → LLM reasoning
```

The required pattern is:

```text
Amazon SP-API
  → provider DTO
  → ingestion boundary
  → identity resolution + normalization
  → canonical ASI seller data
  → historical / current storage
  → domain projection
  → deterministic intelligence engine
  → ToolRegistry
  → EvidenceEnvelope
  → Copilot
  → later Skills
```

External Amazon schemas are contracts, not ASI’s domain model. Canonical entities sit between providers and engines. ASIN alone is not seller-listing identity. Every seller-owned record is organization-scoped. Missing remains unknown. Money is Decimal with currency. Copilot and Skills never call SP-API.

**Verdict of this review:** APPROVE WITH CHANGES. The layered model is ready to freeze. Implementation must not start with orders or finances. The first code slice after approval is connection metadata plus secret-reference architecture, then seller/marketplace identity, then listings, then a **first real seller-ASIN validation** before any operational dashboard or Copilot Amazon tool.

---

## 2. Current Baseline

Treat the following as frozen unless this document names a genuine blocking issue. None of the issues below are blocking; they are integration constraints.

| Layer | State | Constraint for 12B |
| --- | --- | --- |
| FastAPI / Next.js / SQLAlchemy / Alembic / Pydantic v2 / pytest | Shipped | New Amazon tables later use the same stack. No migrations in 12B. |
| Rainforest + mock + manual product | Marketplace listing lookup | Do not replace or merge with SP-API. |
| `product_snapshots` / analysis history | Immutable marketplace observations | Keep `source` distinct from seller-listing observations. |
| Seller report uploads | Operational STR / business files | Remain first-class. Do not deprecate. |
| Profit Intelligence `profit-calc-v1` | Unit worksheet + immutable snapshots | Unique on `organization_id + asin + marketplace`. COGS is seller-owned. |
| Advertising Intelligence `ads-calc-v1` | Period worksheet + snapshots | Ads API later replaces **collection** only (ADR 0001). |
| ToolRegistry / EvidenceEnvelope | Trust and execution boundaries | Schema unchanged. New facts enter only as claims. |
| Copilot | Planner proposes, app validates, synthesis cites | Must not become an SP-API client. |
| Skills | Architecture approved, implementation paused | Resume from 11D after backbone maturity. |
| 12A.0 / 12A.1 | Sandbox LWA + Connection Beta | Env-managed secrets. No `amazon_connections` table yet. |

Existing listing `Price.amount` is a **float**. That is marketplace-display technical debt. Canonical seller money **must not copy it**. Profit already uses `Decimal`. Canonical Amazon amounts follow Profit, not listing `Price`.

Existing profit identity is **org + ASIN + marketplace**, not SKU. Multiple seller SKUs on one ASIN is a later projection problem, not a reason to redesign `profit-calc-v1` in 12B.

---

## 3. Architectural Goals

1. Define a **canonical seller-data backbone** that future SP-API ingest can populate without rewriting engines.
2. Keep **provider DTOs** separate from **ASI entities**.
3. Make **identity, provenance, grain, money, and unknown** explicit.
4. Preserve **multi-tenant isolation** even while the first test seller is one organization.
5. Leave a clean join surface for **Ads API** without designing Ads tables now.
6. Keep Copilot, Skills, ToolRegistry, EvidenceEnvelope, and calculation versions intact.
7. Enable a later proof: one **seller-owned ASIN/SKU** analyzed from SP-API while Rainforest still serves marketplace/competitor ASINs.

---

## 4. Non-Goals

12B does **not**:

- write production code or Alembic migrations
- ingest SP-API operational data
- implement production OAuth / seller consent
- implement Ads API
- modify Copilot, Skills, ToolRegistry, or EvidenceEnvelope
- modify `profit-calc-v1`, `ads-calc-v1`, or listing scoring
- introduce Amazon writes
- introduce LangGraph, CrewAI, or autonomous agents
- ship a seller dashboard, sync UI, or reports UI for Amazon data
- freeze exact Amazon field lists that the live APIs have not yet been validated against

---

## 5. Provider Separation

ADR 0002 remains frozen. Complementary sources:

| Source | Job | Typical facts |
| --- | --- | --- |
| Rainforest | Marketplace intelligence | Public listing, competitors, external Amazon.in signals |
| SP-API | Seller-owned Amazon intelligence | Own catalog/listings, orders, inventory, finances, account, seller reports-as-API |
| Ads API (future) | Seller-owned advertising collection | Spend, ad sales, campaign/search-term collection |
| Seller uploads | Manual / historical operational data | STR, business reports already in ASI |
| Seller-entered private inputs | Facts Amazon does not know | COGS, packaging, other internal costs, modeled fees |

Do not create a generic `AmazonProvider` that hides which of those produced a title, a fee, or a revenue number.

---

## 6. Target Data Flow

```text
Amazon payload (HTTP / report document)
        ↓
SP-API DTO          provider contract, extra=ignore, no formulas
        ↓
Ingestion boundary  auth, pagination, rate limit, sync run, idempotency
        ↓
Adapter             identity resolution, money/time/unknown mapping
        ↓
Canonical entity    ASI seller data, organization scoped
        ↓
Storage             current projection and/or immutable observation/event
        ↓
Domain projection   Listing / Profit / Advertising / Business inputs
        ↓
Deterministic engine
        ↓
ToolRegistry.execute
        ↓
EvidenceEnvelope    source + as_of / period + org + marketplace
        ↓
Copilot synthesis
        ↓
Future Skill policy (paused)
```

Each layer:

| Layer | Owns | Must not own |
| --- | --- | --- |
| Amazon payload | Wire bytes | ASI meaning |
| SP-API DTO | Typed Amazon contract | Business formulas, org policy |
| Ingestion boundary | Fetch, retry, cursor, run metadata | Profit/listing scores |
| Adapter | Map DTO → canonical; refuse unsafe fields | Invented zeros; FX; COGS |
| Canonical entity | Seller-owned facts ASI will keep | Engine versions |
| Storage | Durability, uniqueness, history | Chat |
| Domain projection | Engine-shaped inputs with source labels | Recalculating ACOS/profit |
| Engine | `profit-calc-v1`, listing scores, `ads-calc-v1` | SP-API HTTP |
| Tool | Technical capability | Strategy |
| EvidenceEnvelope | Citable claims | Recommendations not in claims |
| Copilot | Language over evidence | Amazon calls, token handling |
| Skill (later) | Business goal + required tools | Payloads, OAuth, math |

---

## 7. Canonical Entity Model

Minimum entities for a real seller-data backbone. Logical names; not created in 12B.

### 7.1 AmazonConnection

Operational authorization metadata for one org’s Amazon provider link. See §14. Not a business fact table.

### 7.2 AmazonSellerAccount

The authorized selling partner.

| Concept | Notes |
| --- | --- |
| `id` | ASI UUID |
| `organization_id` | Required. SaaS tenant. |
| `connection_id` | FK to connection metadata |
| `selling_partner_id` | Amazon seller / selling partner id when available |
| `provider` | `SP_API` |
| `status` | `active`, `revoked`, `error`, `unknown` |
| `store_name` | Optional display; not identity |
| `created_at` / `updated_at` | Mutable metadata |

No secrets on this entity.

### 7.3 AmazonMarketplace

Participation of one seller account in one Amazon marketplace.

| Concept | Notes |
| --- | --- |
| `id` | ASI UUID |
| `organization_id` | Denormalized for isolation |
| `seller_account_id` | FK |
| `marketplace_id` | Amazon id (India: `A21TJRUUN4KGV`) |
| `marketplace_domain` | ASI canonical domain, e.g. `amazon.in` |
| `country_code` | e.g. `IN` |
| `currency` | Default marketplace currency, e.g. `INR` |
| `region` | SP-API region (`eu` for India) |
| `participation_status` | From Sellers API participation flags |
| `has_suspended_listings` | If Amazon provides it |

V1 target is Amazon.in. The row is still marketplace-keyed so later marketplaces do not collide.

### 7.4 SellerProduct (catalog / listing identity)

The **seller-owned listing identity**. Not “an ASIN.”

Logical natural key:

```text
organization_id + seller_account_id + marketplace_id + seller_sku
```

ASIN is a **linked catalog identifier**, nullable/changeable over observations.

Optional current projection fields: current ASIN, listing status, title stub, FNSKU if known. Rich content lives on observations.

**Why ASIN is not enough:** a seller can have multiple SKUs on one ASIN; the same ASIN exists in other marketplaces; SKU is what the seller operates; FBA introduces FNSKU as a warehouse id. Profit V1 worksheets are ASIN-scoped; that is an engine grain, not a listing identity.

### 7.5 SellerListingObservation

Immutable (or append-only) observation of seller-owned listing/catalog state.

Typical fields: `seller_product_id`, title, bullets, description, images, listing status, optional price, `source`, `source_updated_at`, `observed_at`, provenance.

This is **not** a Rainforest `product_snapshots` row. Same ASIN may have both:

- marketplace observation (`source_system=rainforest`)
- seller listing observation (`source_system=sp_api`)

Never merge into one “title” without provenance.

### 7.6 AmazonOrder

Seller order entity. Upsert by Amazon order id inside org/account/marketplace scope.

Needed for intelligence: `amazon_order_id`, status, purchase/last-update timestamps, currency, order total, fulfillment channel, sales channel, item rollup keys.

**V1 does not store buyer PII** (name, address, phone, email). See §27.

### 7.7 AmazonOrderItem

Line on an order. Natural Amazon key: order + `order_item_id`.

Links to `SellerProduct` when SKU+marketplace+account resolve; otherwise stores SKU/ASIN as unresolved identifiers (`identity_status=unresolved`). Adapters must not drop items that do not yet match a catalog row.

Fields: ASIN, seller SKU, quantity, item price, tax if present without PII, promotions when Amazon provides them as amounts. Partial money is allowed (unknown, not zero).

### 7.8 InventorySnapshot

Point-in-time observation. New row per snapshot grain, not an overwrite of history.

Logical grain:

```text
seller_account + marketplace + seller_sku + observed_at
```

Quantities: fulfillable, inbound, reserved, unfulfillable, researching, future supply — **only those Amazon actually returns**. Architecture allows sparse metrics. Missing quantity stays `null`.

Exact Amazon inventory API family is validated in 12B.5. Do not freeze field names to a guessed schema.

### 7.9 FinancialEvent

Immutable posted money event from Finances (and related settlement feeds).

Identity must be idempotent on Amazon’s event id **plus** org/account scope. If Amazon’s id is weak, compose a stable hash of provider identity fields and treat collision as a mapping failure, not a silent merge.

Does **not** include or overwrite COGS.

May later feed Profit as **observed fees / observed proceeds**, never as private cost.

### 7.10 BusinessMetricObservation

Period-based seller business metric (Reports API and similar).

Grain: org + account + marketplace + metric_name + period_start + period_end + source_operation (+ optional SKU/ASIN dimension).

Examples of **named** metrics, never a generic `revenue`:

- `ordered_product_sales`
- `ordered_units`
- `shipped_product_sales` (if a distinct report provides it)
- `sessions`
- `page_views`
- `unit_session_percentage` (only if Amazon supplies it; do not compute in ingest)

Unknown remains unknown. Do not assume sessions exist in SP-API for V1.

### 7.11 AmazonSyncRun

Operational metadata. See §22. Not seller truth.

### 7.12 Optional AmazonIngestionBlob

Isolated raw/sanitized payload store. See §13.

---

## 8. Seller / Marketplace / Product Identity

```text
Organization
  └── AmazonConnection
        └── AmazonSellerAccount
              └── AmazonMarketplace (marketplace_id, domain, currency)
                    └── SellerProduct (seller_sku) ──ASIN──► catalog identity
                          └── SellerListingObservation
                    └── AmazonOrder
                          └── AmazonOrderItem ──► SellerProduct (optional FK)
                    └── InventorySnapshot (by seller_sku)
```

**Canonical listing key:** `organization_id + seller_account_id + marketplace_id + seller_sku`.

**Catalog identity:** ASIN, marketplace-scoped. Used to join Rainforest, profit models, and ads later. Not unique for seller listings.

**Warehouse identity:** FNSKU, if present, is an attribute on product/inventory observations, not the listing PK.

**Cross-marketplace:** same SKU string in IN vs another marketplace is a different `SellerProduct`.

**Unresolved items:** orders and inventory may arrive before listings sync. Store Amazon identifiers; resolve asynchronously. Do not invent a product row from an order line unless a later implementation ADR says so. Recommendation: **do not auto-create SellerProduct from orders** in the first slices; keep unresolved.

**Profit join (later):** `profit_models` stay `(org, asin, marketplace)`. Projection from seller products to a profit worksheet is **ASIN-level** and must surface SKU multiplicity instead of picking one SKU silently.

---

## 9. SP-API DTO Layer

Keep provider contracts in `app/amazon/` without collapsing Rainforest into that package.

Recommended future layout (not created in 12B):

```text
app/amazon/
  connection.py          # 12A.1, keep
  sandbox.py / lwa.py    # 12A.0, keep
  sp_api/
    dto/                 # Sellers, Listings, Orders, … Pydantic
    clients/             # HTTP, not Copilot
    adapters/            # DTO → canonical
  ingest/                # sync runs, cursors, workers
  canonical/             # ASI entities (Pydantic)
  projections/           # engine-shaped inputs
```

DTO rules:

- Mirror Amazon field names via aliases (`countryCode` → `country_code`) as 12A.0 already does.
- `extra=ignore` on inbound Amazon bodies.
- No `Decimal` business math, no scoring, no COGS.
- `SecretStr` only for tokens in auth DTOs; operational DTOs must not carry tokens.
- Version the Amazon model (e.g. `sellers-api-model/v1`) in provenance, not by forking ASI entities.

12A.0 `MarketplaceParticipation` models are **DTOs**. They must not be reused as `AmazonMarketplace` rows without an adapter.

---

## 10. Canonical Adapter Layer

Adapters are pure mapping + validation:

1. Require `organization_id` and `seller_account_id` from the sync context, never from Amazon JSON.
2. Parse money as `Decimal`; attach currency from payload or marketplace default; fail mapping if amount exists without currency and no inherited currency is in scope.
3. Map missing Amazon fields to `null` / unknown — never `0`.
4. Drop or refuse Restricted Data / PII fields (§27).
5. Emit canonical entities + provenance. They do not write HTTP and do not call engines.

Mapping failure is an operational error on the sync run (`mapping_failed`), not a zeroed business row.

---

## 11. Provenance

Every canonical record or observation answers:

| Question | Field |
| --- | --- |
| Who owns it? | `organization_id` |
| Which Amazon account? | `seller_account_id` |
| Which marketplace? | `marketplace_id` / domain |
| Which system? | `source_system`: `sp_api` \| `ads_api` \| `rainforest` \| `seller_upload` \| `seller_input` |
| Which Amazon object type? | `source_entity_type` |
| Which Amazon id? | `source_record_id` |
| Which API/report/operation? | `source_operation` |
| Which contract version? | `source_version` |
| When Amazon last marked it true | `source_updated_at` (nullable) |
| When the fact occurred | `occurred_at` (events) |
| Observation time | `observed_at` |
| ASI ingest time | `ingested_at` |
| Which sync? | `sync_run_id` |
| Period (reports) | `period_start` / `period_end` |

No tokens, headers, or secrets in provenance.

Evidence later copies a subset onto claims: `source`, `as_of` or period notes, `organization_id` already on the envelope.

---

## 12. Historical vs Current Data

| Entity | Style | Why |
| --- | --- | --- |
| Connection, seller account, marketplace participation | **A. Current mutable projection** | Status and participation change; sellers care about now. Keep `updated_at`. |
| SellerProduct | **A + optional B** | Identity row is current; listing content is observational. |
| SellerListingObservation | **B. Immutable observation** | Title/price/status history. Optional current projection cache on `SellerProduct`. |
| AmazonOrder | **A with status upsert** | Current status is required; do not rewrite `purchase_date`. Optional status-history table later if product needs it. |
| AmazonOrderItem | **A upsert** | Idempotent on Amazon item id. |
| InventorySnapshot | **B snapshots** | Point-in-time; new row per observation. |
| FinancialEvent | **C. Immutable event** | Posted money. Never update in place except correction events as **new** rows. |
| BusinessMetricObservation | **B period observations** | Replacing a period for the same source is a new observation or a versioned upsert keyed by period+source — pick one in 12B.6. Recommendation: **upsert same period+source+metric**, keep previous row as superseded or store versions. Prefer **versioned rows** (`superseded_at`) over silent overwrite. |
| AmazonSyncRun | Operational mutable then terminal | Not business history. |
| Profit / ads / listing analysis snapshots | Already immutable | Ingestion creates **inputs**; engines create **new** snapshots. |

Trade-off: observations cost storage and simplify audit; mutable projections cost less and risk losing “what Amazon said last Tuesday.” ASI already chose snapshots for listing analysis, profit, and ads. Seller operational data should follow that instinct for inventory, listings content, finances, and reports. Orders are the exception: Seller Central itself mutates status, so a current row plus immutable financial events is enough for V1.

---

## 13. Raw Payload Strategy

| Option | Meaning |
| --- | --- |
| A | Do not retain raw payloads |
| B | Short-lived sanitized payloads in the same DB, mixed with business tables |
| C | Isolated immutable ingestion blobs, separate from canonical tables |

**Recommendation: C, with a short retention window (default 14 days, configurable), PII stripped, Copilot/tools forbidden from reading blobs.**

Why not A: Amazon schema drift and mapping bugs are expensive without replay.  
Why not B: mixed tables leak into backups, admin queries, and accidental tool access.  
Why C: debug/replay without making raw JSON a domain model.

Rules:

- Strip buyer PII before persist.
- Never store LWA tokens in blobs.
- TTL / deletion job is mandatory if C is implemented.
- Canonical tables remain the only input to projections and engines.
- Disconnect/revocation should delete blobs first (see §42).

Product Owner may choose A for a stricter data-minimization posture. Architecture prefers C-lite over infinite raw logs.

---

## 14. Connection Metadata

12A.1 correctly skipped persistence. Production SaaS needs `AmazonConnection` **later** (12B.1), still architecture-only here.

Conceptual fields:

- `id`, `organization_id`
- `provider` (`SP_API` / later `ADS_API`)
- `environment` (`sandbox` / `production`)
- `region`
- `selling_partner_id` (when known)
- `status` (`not_connected`, `connected`, `revoked`, `error`)
- `authorized_at`, `last_successful_sync_at`, `last_error_at`, `last_error_code`
- `token_reference` — opaque id into secret storage, **not** the token
- `created_at`, `updated_at`

Ads advertiser profile ids belong on a future Ads connection row, not on SP-API connection, unless Amazon’s app model later proves they are one OAuth grant. Default: **separate connection records per provider** (ADR 0002).

Do not implement this table in 12B.

---

## 15. Credential Architecture

| Environment | Model |
| --- | --- |
| 12A sandbox | Gitignored `.env` LWA client + refresh token. Acceptable only for development. |
| Production SaaS | Per-organization OAuth refresh token in a **secret manager**. Connection row stores `token_reference` only. |

Production recommendation:

1. **External secret store** (Supabase Vault, cloud secret manager, or equivalent). Ordinary Postgres columns are not the token store.
2. Encrypt at rest with rotated app/KMS keys if a vault is unavailable at first production — still not plaintext.
3. API process assumes a runtime role that can **resolve** `token_reference` for the current org’s sync worker only.
4. Copilot, Next.js, and EvidenceEnvelope never receive tokens.
5. Revocation deletes or disables the secret immediately; metadata row becomes `revoked`.
6. Audit secret access separately from seller-facing analytics.
7. Least privilege: refresh-token grant for needed SP-API roles only; no Ads scopes on the SP-API connection.

Open question #12 (secret product) does not block this pattern.

---

## 16. SP-API Domain Mapping

| Amazon family | Canonical targets | Notes |
| --- | --- | --- |
| **Sellers** | SellerAccount, Marketplace | 12A.0 already calls `getMarketplaceParticipations`. First identity ingest. |
| **Catalog Items** | ASIN catalog attributes | Amazon catalog truth for an ASIN, not seller SKU identity. Use to enrich ASIN-linked attributes. Do not treat as “our listing.” |
| **Listings Items** | SellerProduct, SellerListingObservation | Seller-owned SKU listing state. **Preferred source for own listing content.** |
| **Orders** | AmazonOrder, AmazonOrderItem | Entity grain. No PII. |
| **FBA Inventory / Inventory** | InventorySnapshot | Snapshot grain. Confirm API in 12B.5. |
| **Reports** | BusinessMetricObservation; sometimes listing/order extracts | **Asynchronous.** Request → process → download. Never a Copilot HTTP tool. |
| **Finances** | FinancialEvent | Posted money. Helps later observed fees/proceeds. Never COGS. |
| **Sales** | Evaluate before ingest | Prefer **not** to duplicate Orders (entity) or Reports (period). If Sales only repeats aggregates, skip or mark as secondary. |

### Catalog Items vs Listings Items

- **Catalog Items:** what Amazon’s catalog says an ASIN is (public-ish product identity).
- **Listings Items:** what this seller’s SKU listing is (title the seller runs, status, SKU).

Both may mention an ASIN. Only Listings Items establish `seller_sku` identity. Catalog must not overwrite seller listing observations.

---

## 17. Source-of-Truth Rules

High-level policy (ADR 0004):

| Question | Authority |
| --- | --- |
| Public / competitor listing | Rainforest |
| Own listing SKU state | SP-API Listings Items |
| Own ASIN catalog attributes | SP-API Catalog Items, labeled separately from listing observations |
| Order entity | Orders API |
| Period business report metrics | Reports API (named metrics) |
| Posted fees / settlement | Finances API |
| Advertising collection | Ads API (future) |
| COGS / internal costs | Seller input only |
| Modeled unit fees | Seller input until seller opts into observed fees |
| Historical STR/business files | Seller upload; coexist, do not silent-overwrite |

**Inside SP-API, do not average conflicts.**

| Semantic name | Owner | Must not be called |
| --- | --- | --- |
| `ordered_product_sales` | Reports (period) or order-item sum (entity) — **different grains** | `revenue` |
| `order_item_price` | Orders | settlement |
| `shipment_proceeds` / posted event | Finances | ordered sales |
| `ad_attributed_sales` | Ads API later | total sales |

If Orders-derived period totals disagree with a Business Report for the same named metric, **surface both** with source and grain. Do not pick a winner silently. Default UI later: show the metric’s **declared owner**; show conflict as completeness, not a blended number.

Sales API: use only if it provides a metric neither Orders nor Reports own. Otherwise omit.

---

## 18. Time and Grain Model

Do not mix grains in one field.

| Grain | Examples | Time fields |
| --- | --- | --- |
| Entity / event | Order, order item, financial event | `occurred_at` (purchase/posted), `source_updated_at`, `ingested_at` |
| Point-in-time | Inventory, listing observation | `observed_at`, `source_updated_at`, `ingested_at` |
| Period | Business metrics, ads worksheets | `period_start`, `period_end`, `ingested_at` |
| Calculation instant | Profit snapshot | `calculated_at` (already shipped) |

`occurred_at` = when the business event happened at Amazon.  
`observed_at` = when this observation was taken (snapshot clock).  
`source_updated_at` = Amazon’s last-update stamp when provided.  
`ingested_at` = ASI clock.  
`period_*` = inclusive/exclusive convention must be fixed per report type in 12B.6 (document Amazon’s convention; do not guess in ingest).

Profit remains **unit**. Advertising remains **period**. Inventory is **not** a daily P&L. After-ads impact already warns on stale profit snapshots (ADR 0001). Canonical Amazon data must not collapse those clocks.

---

## 19. Money and Currency

- Canonical amounts: `Decimal` (SQL `Numeric`), never float.
- Every amount has a `currency` on the row or a required inherited marketplace/order currency.
- V1 is Amazon.in / INR, but schema is multi-currency.
- **No FX in ingestion.** If a later product needs INR-normalized views, that is a projection with an explicit rate source — not the adapter.
- Do not copy listing `Price.amount: float`.

---

## 20. Unknown / Null Semantics

ASI freeze: **missing ≠ zero**.

| State | Representation | Example |
| --- | --- | --- |
| Missing | `null` | Amazon omitted tax |
| Unavailable | `null` + provenance note / completeness flag | API role not granted |
| Not applicable | `null` + reason code | FBM has no FBA inbound |
| Delayed | prior observation remains; freshness=`stale` or `syncing` | Report not done |
| Stale | last good observation + freshness | Inventory older than policy |

Adapters never manufacture zeros. Ingestion never infers COGS, conversion, or fees. AI never fills canonical money or quantities. `EvidenceClaim.kind=unknown` when a tool must speak about absence.

---

## 21. Freshness

Freshness is **per data domain**, not one account-level boolean.

Suggested statuses (thresholds not frozen): `unknown`, `never_synced`, `syncing`, `fresh`, `stale`, `partial`, `failed`.

| Domain | Freshness meaning |
| --- | --- |
| Connection | Last successful auth/test |
| Identity / marketplaces | Last Sellers sync |
| Listings | Last Listings Items sync for that SKU or account |
| Orders | Last incremental cursor time vs Amazon last-update |
| Inventory | Age of latest snapshot |
| Finances | Last posted event ingest |
| Reports | Period completeness + download time |

Do not publish fake real-time Seller Central. Product copy should say **last synced** and **as-of**.

Numeric staleness SLOs are a Product Owner decision (#9). Architecture only requires the metadata.

---

## 22. Sync Runs

`AmazonSyncRun` is operational:

- `id`, `organization_id`, `connection_id`, `data_domain`
- `started_at`, `completed_at`, `status`
- `cursor` / `next_token` snapshot
- `records_received`, `created`, `updated`, `mapping_failed`
- `error_code`, `error_summary` (no secrets)
- rate-limit annotations

Status examples: `running`, `succeeded`, `partial`, `failed`, `throttled`.

**A failed run must not write zeroed business facts.** Partial success records what mapped and marks the run `partial`.

Sync-run rows are not EvidenceEnvelope claims unless an explicit ops tool is added later. Default: seller-facing tools read canonical data, not run internals.

---

## 23. Idempotency

Repeated pulls must not duplicate entities/events.

| Domain | Idempotency key (logical) |
| --- | --- |
| Seller account | `organization_id + selling_partner_id` |
| Marketplace | `seller_account_id + marketplace_id` |
| Seller product | `seller_account_id + marketplace_id + seller_sku` |
| Order | `seller_account_id + marketplace_id + amazon_order_id` |
| Order item | `order_id + amazon_order_item_id` |
| Financial event | `seller_account_id + source_event_id` (validate Amazon uniqueness in 12B.7) |
| Listing observation | New row per `observed_at` (or content hash + time) — **duplicates in time are allowed if grain is snapshot** |
| Inventory snapshot | New row per snapshot clock |
| Business metric | `account + marketplace + metric_name + period + source_operation + dimension` |

Assumptions marked: Amazon order id uniqueness **per marketplace**; finance event ids **must be validated** before unique constraints are applied.

---

## 24. Pagination / Incremental Sync

SP-API will not return a seller’s history in one call.

Architecture must support:

- `nextToken` / pagination tokens
- date-range incremental orders (`LastUpdatedAfter`)
- report document workflows (id → `DONE` → document)
- retry of a **page** without duplicating prior pages
- partial completion (`AmazonSyncRun.status=partial`)

**Cursor storage:** on `AmazonSyncRun` (in-flight) and on a durable **sync cursor** keyed by `organization_id + connection_id + data_domain + marketplace_id` (completed watermark). Do not store cursors on Copilot conversations.

Reports are not synchronous Copilot operations.

---

## 25. Rate Limits

Rate-limit logic belongs in **ingestion workers**, never Copilot, never ToolRegistry handlers that would call Amazon.

Workers should:

- honor Amazon throttle headers
- exponential backoff + jitter
- queue per connection (and prefer per organization)
- retry transient `throttled` / `unavailable`
- avoid request storms after reconnect

One seller’s throttle should not stall unrelated orgs if workers are partitioned by `connection_id` / `organization_id`. Shared app-level LWA client-id quotas still exist; architecture cannot fully isolate Amazon’s application quota, but **work queues must be tenant-partitioned**.

---

## 26. Error Handling

| Category | Effect |
| --- | --- |
| Authentication | Connection `error`; no business zeros |
| Authorization / missing role | Domain `unavailable`; other domains may still sync |
| Revoked connection | Stop workers; delete secrets; keep or retain data per §42 |
| Throttled | Retry / `throttled` run; last good data remains |
| Amazon unavailable | Failed run; data unchanged |
| Malformed payload | Parse error; skip record; increment mapping/parse counters |
| Partial ingestion | `partial` run + completeness |
| Mapping failure | Record skipped; not coerced |
| Stale data | Freshness, not a fake refresh |

Never map all failures to “0 records ingested, therefore sales are 0.”

---

## 27. PII / Restricted Data

**V1: avoid Restricted Data Token (RDT) APIs.** Seller intelligence does not need buyer identity.

Do not store: buyer name, shipping/billing address, phone, email, tax registration of the buyer unless a later compliance ADR requires it.

Orders V1 needs:

- Amazon order id, status, timestamps
- amounts and currency
- fulfillment / sales channel
- line ASIN, SKU, quantity, item price

That is enough to project units, ordered value, and SKU mix.

If a future feature needs PII, it requires an explicit ADR, encryption, retention, and RDT. Default is **no**.

---

## 28. Proposed Logical Database Model

Logical future tables. **No migrations in 12B.**

Amazon-prefixed operational tables (do not over-generalize to “channel” yet):

- `amazon_connections`
- `amazon_seller_accounts`
- `amazon_marketplaces`
- `amazon_orders`
- `amazon_order_items`
- `amazon_financial_events`
- `amazon_business_metric_observations`
- `amazon_sync_runs`
- `amazon_sync_cursors`
- `amazon_ingestion_blobs` (optional, isolated)

ASI identity / observation:

- `seller_products`
- `seller_listing_observations`
- `inventory_snapshots`

Naming: Amazon-specific facts keep `amazon_`. Listing identity uses `seller_` because ASI owns the identity tuple. Do not invent Shopify-ready names now.

Existing tables stay: `organizations`, `product_snapshots`, `profit_*`, `advertising_*`, `report_uploads`. New Amazon data **joins**; it does not replace them.

---

## 29. Constraints and Indexing

Logical uniqueness (assumptions noted):

| Entity | Unique |
| --- | --- |
| Connection | `organization_id + provider + environment` (allow later multi-account by adding `selling_partner_id` to the key) |
| Seller account | `organization_id + selling_partner_id` |
| Marketplace | `seller_account_id + marketplace_id` |
| Seller product | `seller_account_id + marketplace_id + seller_sku` |
| Order | `seller_account_id + marketplace_id + amazon_order_id` |
| Order item | `order_id + amazon_order_item_id` |
| Financial event | TBD after Finances payload review |
| Business metric | period + metric + source + dimension |

Indexes (logical): all queries lead with `organization_id`; listings by SKU; orders by `purchase_date` / `last_update_date`; inventory by `seller_product_id + observed_at DESC`; sync runs by `connection_id + started_at`.

**Assumption:** one production connection per org per provider for V1. Multi-account requires a uniqueness change — do not pretend V1 is multi-account.

---

## 30. Domain Projections

Canonical tables are not the engines’ API.

```text
Canonical seller data
    → ListingDataProjection
    → ProfitDataProjection
    → AdvertisingDataProjection   (join keys only until Ads API)
    → SellerBusinessProjection
        → existing services
```

Example: Amazon fee events → `ProfitDataProjection.observed_referral_fee` / `observed_fba_fee` with source `sp_api` → seller or service **chooses** modeled vs observed inputs → `ProfitCalculationService` / `profit-calc-v1`.

**Not:** SP-API adapter computes margin.

Projections may be functions/services in `app/amazon/projections/`, not Copilot tools. Tools keep calling Profit/Listing services.

---

## 31. Listing Intelligence Integration

| ASIN class | Source |
| --- | --- |
| Competitor / public marketplace ASIN | Rainforest (and History-first saved analyses) |
| Seller-owned SKU/ASIN | SP-API Listings (+ Catalog enrichment, labeled) |

If both exist for the same ASIN:

- Keep both observations.
- Listing scoring of **marketplace construction** can remain Rainforest-based unless a later product explicitly scores **seller listing** documents.
- First seller-ASIN validation (§47) should project seller listing into a **Listing Intelligence-compatible** product shape **with `source=sp_api`**, without deleting Rainforest paths.

Do not point Analyze’s default lookup at SP-API for arbitrary ASINs.

---

## 32. Profit Intelligence Integration

Future:

```text
SP-API orders / fees (observed)
+ seller COGS / shipping / packaging / other (seller_input)
+ optional modeled fees (seller_input)
    → ProfitDataProjection
    → ProfitModelingService
    → profit-calc-v1 snapshot
```

**COGS:** Amazon will not supply it. Never overwrite.

**Fees — recommendation: coexist as modeled vs observed.**

| Input | Source label | Default for V1 engine |
| --- | --- | --- |
| Referral / FBA modeled | `seller_input` | **Remain the worksheet default** |
| Referral / FBA observed | `sp_api` financials / fee preview if available | Optional override, explicit |

Do not auto-switch the engine to observed fees. Product may later add “use Amazon-observed fees for this snapshot.” Until then, projections **show** observed beside modeled.

`selling_price_source` already exists on profit models (`seller` today). Extend labels later (`sp_api_listing`, `seller`) without renaming the engine.

Grain warning: order totals are not unit profit. Projection to unit fees requires a defined method (per-SKU average in a period, last order, etc.) — **that method is a later product ADR**. 12B only forbids silent averages presented as truth.

---

## 33. Advertising Compatibility

No Ads tables in 12B.

Join surface for future Ads API collection:

- `organization_id`
- Amazon seller account ↔ advertiser profile (future mapping table)
- `marketplace_id` / `amazon.in`
- ASIN
- seller SKU when Ads provides it
- `period_start` / `period_end`

`ads-calc-v1` stays the engine. Collection swap only (ADR 0001). Canonical seller orders/reports must not be renamed `ad_sales`.

---

## 34. Seller Report Upload Coexistence

Uploads remain. `source_system=seller_upload`.

When the same named metric exists from SP-API Reports and an upload:

- Do not overwrite the upload artifact.
- Seller Reports UI can keep diagnosing uploads.
- Business projections declare precedence **per metric**. Recommendation: **SP-API Reports preferred for overlapping dated business metrics once a domain is certified; uploads remain selectable** (PO question #10). Until certified, uploads stay primary for STR workflows they already own.

PPC search-term diagnostics stay on uploads until Ads API collection exists. SP-API will not replace Seller Reports’ STR job in 12B.

---

## 35. Evidence Integration

Path:

```text
SP-API → canonical → domain service/tool → EvidenceEnvelope
```

Not: raw JSON claims.

`EvidenceEnvelope` **schema stays unchanged**. New `source` strings (`sp_api_listings`, `sp_api_orders`, …). `kind`:

- seller-owned Amazon facts: `observed` (or `historical` if from stored snapshot)
- missing: `unknown`
- engine outputs: `calculated`
- COGS: `seller_provided`

`as_of` should carry observation or period end. `notes` may name grain (`period 2026-08-01/2026-08-07`, `order`).

Organization isolation remains `envelope.organization_id`.

---

## 36. Copilot Boundary

**Hard rule:** Copilot never calls SP-API, never holds refresh tokens, never parses Amazon payloads.

Future:

```text
Copilot → ToolRegistry → application tool → normalized service → EvidenceEnvelope → synthesis
```

11D.1 profit/ads tools keep reading ASI snapshots/services. They are not redesigned around Amazon JSON. After projections exist, tools may grow **optional** inputs sourced from canonical data **through services**, not through new Amazon HTTP inside the tool.

Planner still proposes. Application still validates. LLM still cannot confirm.

---

## 37. Skill Boundary

Skills remain paused (11D). Future Skills may **name** tools that read normalized seller data. Skills never:

- call SP-API
- handle OAuth
- parse Amazon payloads
- calculate metrics
- mint evidence

Resume plan: [post-data-backbone-resume-plan.md](../checkpoints/post-data-backbone-resume-plan.md). Unchanged.

---

## 38. Sync Strategy

Do not implement schedulers in 12B. Conceptual classes:

| Class | Use |
| --- | --- |
| Initial backfill | First connect; bounded history per domain |
| Incremental | Orders, finances (cursors / last-updated) |
| Periodic snapshot | Inventory, listing catalog |
| Scheduled async | Reports |
| On-demand refresh | Seller-triggered, rate-limit aware, still via workers |
| Lightweight | Connection test (already 12A.1) |

Suggested later defaults (not promises of real-time):

| Domain | Strategy |
| --- | --- |
| Sellers / marketplaces | Occasional + on connect |
| Listings / catalog | Periodic; later Notifications API if product needs it |
| Orders | Incremental, relatively frequent |
| Inventory | Periodic snapshot (e.g. few times per day — **not frozen**) |
| Finances | Incremental |
| Reports | Scheduled async |
| Connection | On-demand + health check |

Amazon is eventually consistent. Do not promise live Seller Central.

---

## 39. Historical Backfill Strategy

Do **not** freeze 30 / 90 / 365 days globally.

Recommendation: **configurable per domain**, with conservative shipped defaults after rate testing:

| Domain | Reasoning |
| --- | --- |
| Orders | Product likely wants weeks to months; Amazon APIs often cap lookback — **validate at 12B.4** |
| Inventory | Little historical value; current + snapshots going forward |
| Listings | Current state + observations going forward; optional history if Amazon provides |
| Reports | Match seller expectation for business view; often 30–90 days to start |
| Finances | Settlement cycles; start small (e.g. one or two periods) because mapping is hard |

Backfill windows are Product Owner + rate-budget decisions. Architecture requires they are **explicit settings**, not hardcoded folklore.

---

## 40. Amazon.in / Multi-marketplace Strategy

V1 production marketplace: **Amazon.in** (`A21TJRUUN4KGV`, INR, SP-API `eu` region — already used in 12A.0).

Every seller-owned row still carries `marketplace_id` (and ASI `marketplace` domain `amazon.in`). Nothing is “implicitly India.”

Later marketplaces: new `amazon_marketplaces` rows; SKUs do not collide across marketplace ids; currency follows marketplace; reports stay marketplace-scoped.

---

## 41. Eventual Consistency

ASI will lag Amazon. Product should show:

- last successful sync per domain
- as-of / period
- freshness status
- partial/failed without inventing numbers

Do not label Connection Beta “live account.” 12A.1 already avoided a dashboard; keep that honesty when data arrives.

---

## 42. Revocation / Deletion

When a seller disconnects, revokes OAuth, or leaves the org:

| Asset | Recommendation | Needs PO confirmation |
| --- | --- | --- |
| Secrets / refresh token | **Delete immediately** | No |
| Connection row | `revoked`; keep audit fields | No |
| Ingestion blobs | **Delete promptly** | Align with retention policy |
| Canonical operational rows | **Retain for a bounded period** unless seller requests purge | **Yes** (#6) |
| Derived profit/ads/listing snapshots | **Retain** — they are ASI work product with their own provenance | **Yes** if purge-all is required |
| Copilot transcripts | Existing conversation retention | Separate policy |

Flag: GDPR / Indian DPDP deletion vs “keep my historical profit reports.” Do not implement until legal/product confirms.

Workers must stop on revoke even if historical rows remain.

---

## 43. Observability

Separate **ops** from **seller analytics**.

Ops metrics: sync success/fail, lag, throttles, records in/out, mapping errors, revoked connections, secret resolve failures.

Seller-facing: freshness, last synced, completeness. **No raw Amazon errors, tokens, or payload dumps in the UI.**

Logs: same 12A.1 rule — reason codes, never secrets.

---

## 44. Risks

| Risk | Severity | Mitigation |
| --- | --- | --- |
| Copilot or a tool calls SP-API | **Critical** | Hard boundary; no Amazon HTTP in Copilot/Skill/tool handlers |
| ASIN used as listing PK | **Critical** | ADR 0005; SKU+account+marketplace |
| Multi-tenant leak (tokens or orders) | **Critical** | org on every row/query; secret per connection |
| Duplicate truth (Orders vs Reports vs Finances) | **High** | Named metrics; no silent average |
| PII / RDT creep | **High** | V1 forbid; review payloads |
| Provider-schema coupling | **High** | DTO vs canonical (ADR 0003) |
| Cross-marketplace SKU collision | **High** | marketplace in identity |
| Stale data presented as live | **High** | freshness metadata |
| Rate limits / shared app quota | **High** | tenant queues; still Amazon-app limited |
| Finance mapping complexity | **High** | Late in sequence (12B.7) |
| Raw blob growth | **Medium** | TTL, isolation |
| profit_models ASIN uniqueness vs multi-SKU | **Medium** | Projection must not pick SKU silently |
| Large reports | **Medium** | Async workers, not request path |
| Token security (plaintext DB) | **High if ignored** | secret manager + reference |

---

## 45. Open Product Questions

| # | Question | Recommendation |
| --- | --- | --- |
| 1 | Multi-seller SaaS? | **Yes.** Design org isolation now; first test can still be one org. |
| 2 | Amazon.in only for V1 production? | **Yes**, schema marketplace-keyed. |
| 3 | Initial historical depth? | **Configurable per domain**; start small (orders/reports on the order of 30–90 days after API caps are known). |
| 4 | First SP-API domain after architecture? | **Sellers + marketplaces**, then **Listings**, then seller-ASIN proof; not finances first. |
| 5 | Observed fees vs modeled? | **Coexist; default modeled; COGS never Amazon.** |
| 6 | Disconnect retention? | **Delete secrets now; retain canonical + ASI snapshots pending legal; delete blobs quickly.** |
| 7 | Raw SP-API retention? | **Isolated blobs, ~14 days, PII stripped.** |
| 8 | Sessions/conversion immediately? | **No.** Only if Reports provide them; not a V1 blocker. |
| 9 | Freshness sellers should expect? | **Eventual; show last synced.** Exact SLA later. |
| 10 | Uploads selectable when API exists? | **Yes.** Prefer certified SP-API reports for overlapping business metrics; keep uploads. |
| 11 | Orders vs Reports vs Finances authority? | **Entity=Orders; period named metrics=Reports; posted money=Finances.** Never one `revenue`. |
| 12 | Production secret manager? | **External vault with `token_reference`.** Choose vendor in 12B.1. |

None of these block **approving this architecture**. They block naive implementation choices.

---

## 46. Recommended Implementation Sequence

Do not implement these slices now.

Adjusted from the brief: **pull the first seller-ASIN validation immediately after listings**, before orders/inventory/finances. That is the cheapest proof that Rainforest and SP-API stay distinct.

```text
12B     Canonical architecture (this document)
12B.1   Production connection metadata + secret-reference / OAuth architecture
12B.2   Seller + marketplace identity ingest (Sellers API)
12B.3   Catalog / Listings Items adapter → seller_products + observations
12B.3v  First real seller-ASIN validation (separate product proof)
12B.4   Orders + order items
12B.5   Inventory snapshots
12B.6   Reports / business metrics (async)
12B.7   Financial events
12B.8   Provenance hardening + domain projections
12B.9   Connect Listing / Profit tools to projections (contracts unchanged)
12C     Ads API foundation (collection only)
```

Then Skills resume from 11D, not before.

12B.1 is still **architecture + then implementation of connection/secrets**, not ingest. Identity ingest starts at 12B.2.

---

## 47. First Real Seller-ASIN Validation Plan

**Future milestone (12B.3v). Not in 12B.**

Product question: can ASI retrieve and analyze **one seller-owned ASIN/SKU from SP-API** while Rainforest still handles marketplace/competitor ASINs?

Flow:

```text
Authorized seller SKU/ASIN
  → Listings (and optional Catalog) DTOs
  → SellerProduct + SellerListingObservation
  → ListingDataProjection (source=sp_api)
  → existing listing analysis path or a dedicated Seller Intelligence surface
```

Rainforest path unchanged. No Copilot Amazon client. No merge of titles. Success criteria: provenance shows `sp_api`; a competitor ASIN on Analyze still uses Rainforest/History.

---

## 48. ADR Recommendations

These decisions are mature enough to freeze. Drafts:

| ADR | Decision |
| --- | --- |
| **0003** Canonical Amazon Seller Data Model | SP-API payloads are DTOs; canonical ASI entities sit between providers and engines. |
| **0004** Seller Data Provenance and Source Precedence | SP-API, Ads API, Rainforest, uploads, seller inputs remain distinct; named metrics; no silent merge. |
| **0005** Amazon Seller Identity Model | Listing identity is account + marketplace + seller SKU; ASIN is catalog identity. |

---

## 49. Final Architecture Recommendation

Approve this backbone as the target for post-12A.1 Amazon work, **with the sequence change in §46** and **modeled vs observed fees** rather than automatic fee overwrite.

### Principles — confirmed

1. Rainforest remains Marketplace Intelligence.  
2. SP-API becomes Seller-owned Amazon Intelligence.  
3. Ads API will become Seller-owned Advertising Data (collection).  
4. Seller-entered COGS/private costs remain first-class.  
5. Seller uploads remain supported.  
6. SP-API DTOs are not ASI domain models.  
7. Canonical data exists between providers and intelligence engines.  
8. ASIN alone is not seller-listing identity.  
9. Every seller-owned record is organization scoped.  
10. Missing data remains unknown.  
11. Money uses Decimal and explicit currency.  
12. Data grain is never silently merged.  
13. Historical observations are preserved where required.  
14. Ingestion is idempotent.  
15. Rate limits belong to ingestion infrastructure, not Copilot.  
16. Copilot never calls SP-API directly.  
17. Skills never call SP-API directly.  
18. Evidence must retain source and time context.  
19. Existing deterministic engines remain owners of calculations.  
20. No Amazon writes are introduced.  
21. No Ads API implementation is introduced.  
22. No Skill implementation is introduced.  
23. No LangGraph, CrewAI, or autonomous agents are introduced.  
24. Existing 11A–12A.1 architecture remains intact.

No blocking contradiction with the frozen stack was found. The only engine constraint to carry forward is **profit identity is ASIN-scoped**; seller listing identity is SKU-scoped. That is a projection problem, not a reason to merge providers or rewrite `profit-calc-v1`.

---

## Architecture review summary

### Verdict

**APPROVE WITH CHANGES**

Changes already incorporated in this document: (1) first seller-ASIN validation immediately after listings; (2) isolated short-TTL raw blobs rather than infinite raw JSON or mixing blobs into business tables; (3) modeled vs observed fees, never Amazon COGS; (4) Sales API is secondary unless it owns a unique metric.

### Top architectural decisions

1. Layered DTO → adapter → canonical → projection → engine.  
2. Listing identity = org + seller account + marketplace + SKU.  
3. Named metrics; Orders / Reports / Finances do not collapse into `revenue`.  
4. Secrets out of tables; connection metadata only.  
5. Copilot/Skills/Tool handlers never speak HTTP to Amazon.

### Highest-risk areas

Tenant isolation and tokens; ASIN/SKU confusion; duplicate money semantics; Copilot coupling; finance complexity; Amazon app-level rate limits.

### Product Owner decisions needed

SaaS confirmation, backfill windows, fee-override UX, disconnect retention, raw-blob retention, freshness SLA, upload vs API selector, secret-manager vendor.

### Suggested ADRs

0003, 0004, 0005 (accepted drafts with this milestone).

### Recommended first implementation slice

**12B.1 — production connection metadata + `token_reference` secret architecture** (still no operational ingest). Then 12B.2 Sellers identity. Do not start 12B.4–12B.7 until 12B.3v can prove one seller-owned listing observation beside Rainforest.

# Amazon Seller Intelligence

AI-powered Amazon seller intelligence platform.

This repository currently contains **Milestone 0–11A**: a local monorepo with mock lookup, manual product input, deterministic listing intelligence (v1 and v2), optional **custom scoring profiles** (aggregate weights only), Rainforest real-ASIN lookup, AI listing strategy (V2 primary, V1 unchanged), optional image/media vision, competitor discovery and comparison, Seller Central report analytics, API usage, mock-only bulk ASIN due diligence, **persistent report history**, **soft-delete of saved analyses**, **client PDF export** of historical reports, and an **internal intelligence tool layer** for future Copilot (no chat UI yet).

## What this project is

Later milestones will add Amazon SP-API, Ads API, AI report interpretation, and human-approved Amazon actions.

Right now the app does these things:

1. Look up a **real Amazon.in ASIN** via Rainforest (primary workflow).
2. Look up a product against **mock data** by entering catalog ASINs `B0TEST0001`–`B0TEST0003` (no dedicated Quick Demo tab).
3. Enter a listing by hand (Manual Product fallback).
4. Run **Listing Intelligence V2** — listing quality (not sales), plus separate market signals and data coverage. Optionally apply a **Custom Scoring Profile** to re-weight the aggregate only. V1 remains available as legacy.
5. Generate **AI Strategy V2** — semantic content/SEO interpretation of V2 evidence. OpenAI, explicit click only. V1 AI remains a legacy path.
6. Optionally **Analyze Images & Media** — multimodal visual intelligence. Separate from listing-quality scores. Explicit click only.
7. **Discover candidate competitors** via Rainforest Amazon search. The seller still chooses up to three.
8. Compare selected or manually entered competitor ASINs — deterministic comparison, then optional AI competitive insights.
9. Upload a **Sponsored Products Search Term Report** or **Business Report** and run deterministic PPC / business analytics. No AI. Uploads are persisted when the database is configured.
10. See **API Budget** at the top of the app — Rainforest account credits and OpenAI spend (provider vs this app).
11. Upload a CSV/XLSX of ASINs for **Bulk Due Diligence** (mock catalog and mock AI only in this milestone).
12. Reopen **saved ASIN analyses** from **History** without calling Rainforest or OpenAI again. Export a client PDF or soft-delete a report from History; neither refreshes Amazon or AI data.
13. Create organization **scoring profiles** that change only the V2 aggregate weights. Standard V2 remains the benchmark.
14. Internal **intelligence tools** wrap Listing, Profit, and Advertising services for Copilot (`get_saved_report`, `analyze_listing_v2`, `get_profit_snapshot`, `get_advertising_snapshot`, and related tools). Copilot explains evidence; Python still owns scores and money math. These tools are not Skills.
15. Open **Profit** to model unit economics for an ASIN. Python calculates profit, margin, and ROI. Missing COGS stays unknown. On the same worksheet, enter a period of advertising spend to see ACOS, TACOS, ROAS, and profit after ads. Copilot can read those snapshots through ToolRegistry; it does not recalculate them.

All product flows produce the same normalized `Product` object. Listing analysis is a separate step after a product is loaded. **V2 listing quality does not use rating, reviews, or BSR.** Competitor comparison reuses that product model and the V1 listing scorer. Primary listing AI sits on V2 deterministic results and does not replace scores. V1 AI remains available.

**Deterministic analysis remains the source of truth for scores.** AI does not currently use Claude.

## Architecture

Product data is fetched through an abstraction. Routes and the UI depend only on `ProductService` and the normalized `Product` model:

```text
GET  /api/v1/products/{asin}          → ProductService.get_product() → ProductDataProvider → Product
POST /api/v1/products/manual          → ProductService.create_from_manual() → Product
POST /api/v1/analysis/listing         → ListingAnalysisService.analyze() → ListingAnalysis (v1)
POST /api/v1/analysis/listing/v2      → ListingAnalysisV2Service.analyze() → ListingAnalysisV2
POST /api/v1/analysis/listing/v2/reweight → ScoringProfileService (aggregate only)
GET/POST/PATCH/DELETE /api/v1/scoring-profiles → ScoringProfileService
POST /api/v1/analysis/listing/ai      → AIListingIntelligenceService.generate() → AIListingIntelligence (v1)
POST /api/v1/analysis/listing/v2/ai   → AIListingIntelligenceV2Service.generate() → AIListingIntelligenceV2
POST /api/v1/analysis/listing/v2/images/ai → AIImageIntelligenceService.generate() → AIImageIntelligence
POST /api/v1/analysis/competitors     → CompetitorComparisonService.compare() → CompetitorComparison
POST /api/v1/analysis/competitors/ai  → AICompetitiveIntelligenceService.generate() → AICompetitiveIntelligence
POST /api/v1/competitors/query        → CompetitorSearchQueryService.generate() → search query
POST /api/v1/competitors/discover     → CompetitorDiscoveryService.discover() → candidate listings
POST /api/v1/reports/analyze          → ReportAnalysisService.analyze() → PPC or Business analysis
GET  /api/v1/reports                  → AnalysisHistoryService.list_reports() → saved ASIN analyses
GET  /api/v1/reports/{report_id}      → AnalysisHistoryService.get_report() → historical report (0 providers)
DELETE /api/v1/reports/{report_id}    → AnalysisHistoryService.soft_delete() → deleted_at (0 providers)
POST /api/v1/reports/{report_id}/pdf  → ClientReportService.generate_pdf() → reuse or render analysis-report-v2
GET  /api/v1/reports/{report_id}/pdf  → ClientReportService.download_pdf() → application/pdf from private Storage
GET  /api/v1/usage/dashboard          → UsageDashboardService.get_dashboard() → provider account + app ledger
POST /api/v1/bulk/preview             → ingest ASINs from CSV/XLSX
POST /api/v1/bulk/jobs                → in-process bulk due diligence job (mock providers)
GET  /api/v1/bulk/jobs/{job_id}
POST /api/v1/profit/models            → ProfitModelingService.create_model()
GET  /api/v1/profit/models
GET  /api/v1/profit/models/{id}
PATCH /api/v1/profit/models/{id}
POST /api/v1/profit/models/{id}/calculate → ProfitCalculationService (profit-calc-v1)
POST /api/v1/profit/preview           → stateless calculate
GET  /api/v1/profit/models/{id}/advertising → AdvertisingModelingService
PATCH /api/v1/profit/models/{id}/advertising
POST /api/v1/profit/models/{id}/advertising/calculate → ads-calc-v1 snapshot
GET  /api/v1/profit/models/{id}/advertising/snapshots
POST /api/v1/advertising/preview      → stateless ads calculate
```

Persistence (when `DATABASE_URL` is set):

```text
FastAPI
    ↓
repositories / AnalysisHistoryService / ArtifactPersistenceService
    ↓
PostgreSQL (Supabase) + private Storage buckets
```

Both endpoints return:

```json
{
  "product": { "...normalized Product..." },
  "meta": { "source": "mock" }
}
```

`meta.source` is `"mock"`, `"manual"`, `"rainforest"`, or `"amazon_public"`. Provenance is API metadata, not a field on `Product`.

```text
ProductDataProvider
        │
        ├── MockProductDataProvider              [implemented]
        ├── RainforestProductDataProvider        [implemented, V1 default]
        ├── AmazonPublicProductDataProvider      [experimental]
        └── AmazonOfficialProductDataProvider    [future]
```

The rest of the application — API responses and the frontend — uses only the normalized internal `Product` model.

```text
AmazonSearchProvider
        │
        ├── RainforestAmazonSearchProvider       [implemented, V1]
        ├── MockAmazonSearchProvider             [implemented, demo ASINs / tests]
        └── AmazonOfficialSearchProvider         [future]
```

```text
AIProvider
        │
        ├── OpenAIProvider                       [implemented, V1 default]
        └── ClaudeProvider                       [future]
```

The Amazon.in public provider is experimental. See [docs/amazon-public-provider.md](docs/amazon-public-provider.md). Rainforest is the V1 default; see [docs/rainforest-provider.md](docs/rainforest-provider.md). The long-term official Amazon provider remains SP-API.

```text
ReportParser
        │
        ├── SearchTermReportParser               [implemented]
        └── BusinessReportParser                 [implemented]
```

Listing Intelligence sits on top of `Product`. V1 scoring lives in `app/analytics/listing_rules.py`. V2 scoring lives in `app/analytics/listing_rules_v2.py`. Primary AI strategy sits on `ListingAnalysisV2` through `AIListingIntelligenceV2Service` and `AIProvider`. Optional image/media vision sits on `AIImageIntelligenceService` and `AIProvider.generate_multimodal_structured`. V1 AI remains on `ListingAnalysis` through `AIListingIntelligenceService`. Competitor comparison sits on top of `Product` + `ListingAnalysis` through `CompetitorComparisonService`. Competitive AI sits on that comparison through `AICompetitiveIntelligenceService` and the same `AIProvider`. Amazon search discovery sits on `AmazonSearchProvider` (Rainforest `type=search`) and never calls OpenAI. Seller report analytics sit on normalized report rows through `PPCAnalyticsService` and `BusinessAnalyticsService` and never call OpenAI. OpenAI-specific code stays in `OpenAIProvider`. The API Budget strip reads Rainforest Account API credits and optional OpenAI organization costs on the backend only; see [docs/api-usage-dashboard.md](docs/api-usage-dashboard.md). Bulk due diligence is a separate job workflow that currently uses mock product and mock AI providers; see [docs/bulk-asin-due-diligence.md](docs/bulk-asin-due-diligence.md). See also [docs/listing-intelligence-v2.md](docs/listing-intelligence-v2.md), [docs/ai-listing-intelligence-v2.md](docs/ai-listing-intelligence-v2.md), [docs/image-media-intelligence.md](docs/image-media-intelligence.md), [docs/ai-listing-intelligence.md](docs/ai-listing-intelligence.md), [docs/competitor-intelligence.md](docs/competitor-intelligence.md), [docs/competitor-discovery.md](docs/competitor-discovery.md), and [docs/seller-report-analytics.md](docs/seller-report-analytics.md).

Marketplace identifiers use Amazon **domain** form. V1 supports `amazon.in` only. See [docs/marketplace.md](docs/marketplace.md).

## Listing Intelligence

The current primary path after a product is loaded is **Listing Intelligence V2**:

```text
POST /api/v1/analysis/listing/v2
```

Optional explicit-click AI:

```text
POST /api/v1/analysis/listing/v2/ai
POST /api/v1/analysis/listing/v2/images/ai
```

V1 (`POST /api/v1/analysis/listing` and `/listing/ai`) remains as a legacy/backward-compatible path. See [docs/listing-intelligence-v2.md](docs/listing-intelligence-v2.md) and [docs/custom-scoring-profiles.md](docs/custom-scoring-profiles.md).

### Legacy V1 scoring (still available)

After a product is loaded, the UI can still call:

```text
POST /api/v1/analysis/listing
```

Request body reuses the existing `Product` model:

```json
{
  "product": { "...normalized Product..." },
  "source": "mock"
}
```

Response envelope:

```json
{
  "product": { "...unchanged Product..." },
  "analysis": { "...ListingAnalysis..." },
  "meta": {
    "engine": "deterministic",
    "score_version": "v1",
    "source": "mock"
  }
}
```

`source` is optional request metadata echoed in `meta`. Analysis does not modify `Product`.

### Scoring philosophy

- Rules are explicit, versioned, and explainable.
- Thresholds are **internal heuristics**, not Amazon policy claims.
- Completeness (whether fields are present) is scored separately from content quality.
- Missing catalog fields that a future provider may not support are not treated as high-severity content defects.
- Social proof language is descriptive only. It does not claim conversion impact.
- Score version: **`v1`**.

Weights:

| Section | Weight |
|---------|--------|
| Title | 20% |
| Bullets | 25% |
| Description | 15% |
| Images | 15% |
| Completeness | 15% |
| Social proof | 10% |

Full rule thresholds: [docs/listing-intelligence.md](docs/listing-intelligence.md). Listing quality V2: [docs/listing-intelligence-v2.md](docs/listing-intelligence-v2.md). Primary AI strategy: [docs/ai-listing-intelligence-v2.md](docs/ai-listing-intelligence-v2.md) (`POST /api/v1/analysis/listing/v2/ai`).

## Folder structure

```text
.
├── apps/
│   ├── api/                 FastAPI backend
│   │   └── app/
│   │       ├── main.py
│   │       ├── api/routes/
│   │       ├── models/
│   │       ├── analytics/
│   │       ├── parsers/
│   │       ├── providers/
│   │       ├── services/
│   │       ├── copilot/
│   │       └── core/
│   └── web/                 Next.js frontend
├── docs/
├── README.md
└── .gitignore
```

## Requirements

- macOS
- Node.js 20+ and npm
- Python 3.12+
- [uv](https://docs.astral.sh/uv/) (preferred) or pip

## How to run backend

From the repository root:

```bash
cd apps/api
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

The API listens on [http://localhost:8000](http://localhost:8000).

Health check: [http://localhost:8000/health](http://localhost:8000/health)

Alternative without uv:

```bash
cd apps/api
python3 -m venv .venv
source .venv/bin/activate
pip install fastapi "uvicorn[standard]" pydantic pydantic-settings httpx beautifulsoup4 openai
uvicorn app.main:app --reload --port 8000
```

For tests with pip, also install `pytest`, `httpx`, and `pytest-asyncio`.

Copy `apps/api/.env.example` to `apps/api/.env`. For real ASIN lookup set `RAINFOREST_API_KEY`. For AI recommendations set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` (default `gpt-5.4`). For saved History set `DATABASE_URL` (and Storage keys if you want uploaded/generated files kept). Keys stay in that backend file only. Never put them in Next.js or `NEXT_PUBLIC_*`. CORS is already set for `http://localhost:3000`.

Persistence setup: [docs/persistence-supabase.md](docs/persistence-supabase.md). Schema: [docs/database-schema.md](docs/database-schema.md). Custom scoring profiles: [docs/custom-scoring-profiles.md](docs/custom-scoring-profiles.md). Report lifecycle: [docs/report-lifecycle.md](docs/report-lifecycle.md). Client PDFs: [docs/client-pdf-reports.md](docs/client-pdf-reports.md). Intelligence tools: [docs/milestone-11/copilot-tool-layer.md](docs/milestone-11/copilot-tool-layer.md). Completion records: [docs/persistence-report.md](docs/persistence-report.md), [docs/custom-scoring-profiles-report.md](docs/custom-scoring-profiles-report.md), [docs/milestone-11/milestone-11a-report.md](docs/milestone-11/milestone-11a-report.md).

```bash
cd apps/api
uv run alembic upgrade head
```

Run tests:

```bash
cd apps/api
uv run pytest
```

## How to run frontend

In a second terminal, from the repository root:

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run dev
```

Open [http://localhost:3000](http://localhost:3000). Copilot lives at [http://localhost:3000/copilot](http://localhost:3000/copilot). Profit lives at [http://localhost:3000/profit](http://localhost:3000/profit).

`NEXT_PUBLIC_API_BASE_URL` must point at the FastAPI server (default `http://localhost:8000`). Do not hardcode localhost in application code.

Frontend checks:

```bash
cd apps/web
npm test
```

## Sample ASINs

Use these fictional catalog IDs against the mock provider:

| ASIN       | Product                                      |
|------------|----------------------------------------------|
| B0TEST0001 | AuroraGlow Vitamin D3 Softgels               |
| B0TEST0002 | NimbusFoam Memory Contour Pillow             |
| B0TEST0003 | PeakPulse Resistance Bands Set               |

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Liveness check. Returns `status` plus `persistence` (`configured` or `disabled`). |
| GET | `/api/v1/products/{asin}` | Product lookup (Rainforest by default; mock catalog for demo ASINs). Returns `{ product, meta }`. |
| POST | `/api/v1/products/manual` | Normalize user-entered listing details into `Product`. |
| POST | `/api/v1/analysis/listing` | Deterministic listing analysis for a `Product` (v1). |
| POST | `/api/v1/analysis/listing/v2` | Deterministic listing quality V2 for a `Product`. Optional `scoring_profile_id`. Persists a historical report when the database is configured. |
| POST | `/api/v1/analysis/listing/v2/reweight` | Recalculate Custom Listing Quality Score from an existing V2 analysis or `report_id`. Zero provider calls. |
| GET | `/api/v1/scoring-profiles` | List Standard V2 plus organization custom profiles. |
| POST | `/api/v1/scoring-profiles` | Create a custom scoring profile. Weights must total 100. |
| GET | `/api/v1/scoring-profiles/{id}` | Fetch Standard V2 or a custom profile. |
| PATCH | `/api/v1/scoring-profiles/{id}` | Update a custom profile. Standard V2 cannot be changed. |
| DELETE | `/api/v1/scoring-profiles/{id}` | Soft-archive a custom profile. Historical snapshots remain. |
| POST | `/api/v1/analysis/listing/ai` | V1 AI listing recommendations on top of `ListingAnalysis`. |
| POST | `/api/v1/analysis/listing/v2/ai` | V2 AI content/SEO strategy on top of `ListingAnalysisV2`. Optional `report_id` attaches to the same saved report. |
| POST | `/api/v1/analysis/listing/v2/images/ai` | Optional multimodal image/media intelligence. Optional `report_id` attaches to the same saved report. |
| POST | `/api/v1/analysis/competitors` | Deterministic comparison of a target `Product` against 1–3 competitor ASINs. |
| POST | `/api/v1/analysis/competitors/ai` | AI competitive insights on a completed comparison. |
| POST | `/api/v1/competitors/query` | Generate a deterministic Amazon search query from a target `Product`. |
| POST | `/api/v1/competitors/discover` | Rainforest Amazon.in search → ranked candidate listings. |
| POST | `/api/v1/reports/analyze` | Multipart CSV/XLSX upload → Search Term or Business Report analytics. Original file stored when persistence is on. |
| GET | `/api/v1/reports` | Paginated saved ASIN analysis history (excludes soft-deleted). |
| GET | `/api/v1/reports/{report_id}` | Historical report (product snapshot + V2 + optional AI/image). Zero provider calls. |
| DELETE | `/api/v1/reports/{report_id}` | Soft-delete a saved analysis (`deleted_at`). Underlying rows are retained. |
| POST | `/api/v1/reports/{report_id}/pdf` | Generate or reuse the client A4 PDF (`analysis-report-v2`; v1 artifacts remain valid). Zero provider calls. |
| GET | `/api/v1/reports/{report_id}/pdf` | Download an existing client PDF from private Storage. |
| GET | `/api/v1/usage/dashboard` | Provider-account usage plus this app’s Rainforest/OpenAI ledger. |
| POST | `/api/v1/bulk/preview` | Preview unique ASINs from a CSV/XLSX upload. |
| POST | `/api/v1/bulk/jobs` | Start a bulk due-diligence job (mock providers by default). |
| GET | `/api/v1/bulk/jobs/{job_id}` | Job status / results. |
| GET | `/api/v1/bulk/jobs/{job_id}/report.xlsx` | Excel report after the job completes (in-memory job or stored file). |
| POST | `/api/v1/profit/models` | Create an ASIN profit worksheet. |
| GET | `/api/v1/profit/models` | List profit models for the current organization. |
| GET | `/api/v1/profit/models/{id}` | Model plus latest snapshot. |
| PATCH | `/api/v1/profit/models/{id}` | Update seller inputs only. |
| POST | `/api/v1/profit/models/{id}/calculate` | Persist an immutable `profit-calc-v1` snapshot. |
| POST | `/api/v1/profit/preview` | Stateless calculate. Client-sent profit/margin/ROI are ignored. |
| GET | `/api/v1/profit/models/{id}/advertising` | Advertising worksheet, latest snapshot, and after-ads impact. |
| PATCH | `/api/v1/profit/models/{id}/advertising` | Update seller advertising inputs only. |
| POST | `/api/v1/profit/models/{id}/advertising/calculate` | Persist an immutable `ads-calc-v1` snapshot. |
| GET | `/api/v1/profit/models/{id}/advertising/snapshots` | Advertising snapshot history. |
| POST | `/api/v1/advertising/preview` | Stateless ads calculate. Client-sent ACOS/TACOS/ROAS are ignored. |

Optional query parameter on GET: `marketplace` (default `amazon.in`).

Examples:

```bash
curl "http://localhost:8000/api/v1/products/B0TEST0001?marketplace=amazon.in"

curl -X POST "http://localhost:8000/api/v1/analysis/listing" \
  -H "Content-Type: application/json" \
  -d '{"product": {"asin":"B0TEST0001","marketplace":"amazon.in","title":"Example title that is long enough for scoring","bullet_points":[],"images":[],"variations":[],"last_fetched_at":"2026-08-19T06:00:00Z"}}'
```

Validation:

- ASIN must be 10 uppercase alphanumeric characters (`A–Z`, `0–9`). Input is normalized to uppercase.
- Invalid format → `400 Bad Request`
- Product not found → `404 Not Found`
- Missing / invalid Rainforest or OpenAI key, rate limit, or provider unavailable → `503`
- Catalog or AI lookup failed or could not be mapped → `502`
- Unsupported marketplace → `400 Bad Request`

Set `PRODUCT_PROVIDER=rainforest` (default) for real Amazon.in lookup, `mock` for catalog-only, or `amazon_public` for the experimental HTML provider.

## Current limitations

- There is no dedicated Quick Demo tab. Mock catalog ASINs `B0TEST0001`–`B0TEST0003` still resolve from the in-process mock catalog when entered in Analyze.
- Real ASIN lookup uses **Rainforest**. The API key stays on the backend.
- Amazon.in public lookup remains available as `PRODUCT_PROVIDER=amazon_public`. It is experimental and often blocked.
- Manual input is a fallback (`Enter product manually`). It is **not persisted**.
- Listing Intelligence V2 scores are **deterministic**. Custom scoring profiles change only the **aggregate weights**. They do not change section rules, Market Signals, Data Coverage, or AI output. See [docs/custom-scoring-profiles.md](docs/custom-scoring-profiles.md).
- Listing V2 media coverage counts images/videos; it does not judge pixels. Optional Image & Media Intelligence analyzes selected listing images after an explicit click.
- Competitor ASINs may be **entered by the seller** or **selected from search candidates**. Discovery does not auto-compare.
- Competitor comparison scores use the **V1 listing engine**. AI competitive insights are a separate, optional step.
- Seller reports are analyzed in memory. When persistence is configured, the original file is stored in private Storage (SHA-256 used to identify duplicates; they are not rejected).
- Report analytics are **deterministic**. AI interpretation is not implemented.
- Scoring thresholds are heuristics, not Amazon policy.
- No SP-API or Ads API yet.
- No Claude provider yet. OpenAI is the current AI provider.
- **PostgreSQL + Supabase Storage** persist ASIN analysis history, upload metadata, bulk jobs, generated Excel, and usage events when `DATABASE_URL` is set. Short-term provider caches remain in-process memory.
- Rainforest **account credits** come from the Account API. This app’s call counts are a separate ledger. See [docs/api-usage-dashboard.md](docs/api-usage-dashboard.md).
- OpenAI **provider spend** needs an Admin API key (`OPENAI_ADMIN_API_KEY`). App-estimated cost is calculated from response token usage and is not authoritative provider billing.
- Bulk due diligence currently uses **mock product and mock AI providers**. Live Rainforest/OpenAI bulk is guarded off. Bulk **Excel** export is implemented; PDF export is not. See [docs/bulk-asin-due-diligence.md](docs/bulk-asin-due-diligence.md).
- No authentication yet. A default development organization scopes persisted rows. RLS does not isolate users today because there is no login.
- Intelligence tools and Copilot V1 exist: `/copilot` uses plan → execute/confirm → synthesize. Analyze, History, Reports, Bulk, and **Profit** remain the expert surfaces. Profit math is Python-only (`profit-calc-v1`). Advertising math is Python-only (`ads-calc-v1`). Copilot profit/ads tools read those engines through ToolRegistry. Skills are not implemented. There is **no** RAG or Amazon write path. See [docs/milestone-11/milestone-11d1-copilot-domain-tools.md](docs/milestone-11/milestone-11d1-copilot-domain-tools.md).
- Re-analyze current listing (new snapshot + new report) and report deletion are not implemented.
- India marketplace (`amazon.in`) only. Report money is treated as INR.

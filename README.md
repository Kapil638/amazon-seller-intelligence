# Amazon Seller Intelligence

AI-powered Amazon seller intelligence platform.

This repository currently contains **Milestone 0–9 plus API Budget and Bulk ASIN Due Diligence**: a local monorepo with mock lookup, manual product input, deterministic listing intelligence, Rainforest real-ASIN lookup, AI listing recommendations, competitor discovery and comparison, Seller Central report analytics, API usage, and **mock-only bulk ASIN due diligence**.

## What this project is

Later milestones will add Amazon SP-API, Ads API, AI report interpretation, and human-approved Amazon actions.

Right now the app does these things:

1. Look up a **real Amazon.in ASIN** via Rainforest (primary workflow).
2. Look up a product against **mock data** (Quick Demo).
3. Enter a listing by hand (Manual Product fallback).
4. Run **Listing Intelligence** — deterministic scores and findings.
5. Generate **AI Recommendations** — strategic interpretation. OpenAI, explicit click only.
6. **Discover candidate competitors** via Rainforest Amazon search. The seller still chooses up to three.
7. Compare selected or manually entered competitor ASINs — deterministic comparison, then optional AI competitive insights.
8. Upload a **Sponsored Products Search Term Report** or **Business Report** and run deterministic PPC / business analytics. No AI. No database.
9. See **API Budget** at the top of the app — Rainforest account credits and OpenAI spend (provider vs this app).
10. Upload a CSV/XLSX of ASINs for **Bulk Due Diligence** (mock catalog and mock AI only in this milestone).

All product flows produce the same normalized `Product` object. Listing analysis is a separate step after a product is loaded. Competitor comparison reuses that product model and the same listing scorer. AI sits on top of deterministic results and does not replace scores.

**Deterministic analysis remains the source of truth for scores.** AI does not currently use Claude.

## Architecture

Product data is fetched through an abstraction. Routes and the UI depend only on `ProductService` and the normalized `Product` model:

```text
GET  /api/v1/products/{asin}          → ProductService.get_product() → ProductDataProvider → Product
POST /api/v1/products/manual          → ProductService.create_from_manual() → Product
POST /api/v1/analysis/listing         → ListingAnalysisService.analyze() → ListingAnalysis
POST /api/v1/analysis/listing/ai      → AIListingIntelligenceService.generate() → AIListingIntelligence
POST /api/v1/analysis/competitors     → CompetitorComparisonService.compare() → CompetitorComparison
POST /api/v1/analysis/competitors/ai  → AICompetitiveIntelligenceService.generate() → AICompetitiveIntelligence
POST /api/v1/competitors/query        → CompetitorSearchQueryService.generate() → search query
POST /api/v1/competitors/discover     → CompetitorDiscoveryService.discover() → candidate listings
POST /api/v1/reports/analyze          → ReportAnalysisService.analyze() → PPC or Business analysis
GET  /api/v1/usage/dashboard          → UsageDashboardService.get_dashboard() → provider account + app ledger
POST /api/v1/bulk/preview             → ingest ASINs from CSV/XLSX
POST /api/v1/bulk/jobs                → in-process bulk due diligence job (mock providers)
GET  /api/v1/bulk/jobs/{job_id}
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
        ├── MockAmazonSearchProvider             [implemented, Quick Demo]
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

Listing Intelligence sits on top of `Product`. Scoring lives in `app/analytics/listing_rules.py` and is exposed through `ListingAnalysisService`. AI recommendations sit on top of `ListingAnalysis` through `AIListingIntelligenceService` and `AIProvider`. Competitor comparison sits on top of `Product` + `ListingAnalysis` through `CompetitorComparisonService`. Competitive AI sits on that comparison through `AICompetitiveIntelligenceService` and the same `AIProvider`. Amazon search discovery sits on `AmazonSearchProvider` (Rainforest `type=search`) and never calls OpenAI. Seller report analytics sit on normalized report rows through `PPCAnalyticsService` and `BusinessAnalyticsService` and never call OpenAI. OpenAI-specific code stays in `OpenAIProvider`. The API Budget strip reads Rainforest Account API credits and optional OpenAI organization costs on the backend only; see [docs/api-usage-dashboard.md](docs/api-usage-dashboard.md). Bulk due diligence is a separate job workflow that currently uses mock product and mock AI providers; see [docs/bulk-asin-due-diligence.md](docs/bulk-asin-due-diligence.md). See also [docs/ai-listing-intelligence.md](docs/ai-listing-intelligence.md), [docs/competitor-intelligence.md](docs/competitor-intelligence.md), [docs/competitor-discovery.md](docs/competitor-discovery.md), and [docs/seller-report-analytics.md](docs/seller-report-analytics.md).

Marketplace identifiers use Amazon **domain** form. V1 supports `amazon.in` only. See [docs/marketplace.md](docs/marketplace.md).

## Listing Intelligence

After a product is loaded, the UI can call:

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

Full rule thresholds: [docs/listing-intelligence.md](docs/listing-intelligence.md).

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

Copy `apps/api/.env.example` to `apps/api/.env`. For real ASIN lookup set `RAINFOREST_API_KEY`. For AI recommendations set `OPENAI_API_KEY` and optionally `OPENAI_MODEL` (default `gpt-5.4`). Keys stay in that backend file only. Never put them in Next.js or `NEXT_PUBLIC_*`. CORS is already set for `http://localhost:3000`.

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

Open [http://localhost:3000](http://localhost:3000).

`NEXT_PUBLIC_API_BASE_URL` must point at the FastAPI server (default `http://localhost:8000`). Do not hardcode localhost in application code.

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
| GET | `/health` | Liveness check. Returns `{"status":"ok"}`. |
| GET | `/api/v1/products/{asin}` | Product lookup (Rainforest by default; mock catalog for demo ASINs). Returns `{ product, meta }`. |
| POST | `/api/v1/products/manual` | Normalize user-entered listing details into `Product`. |
| POST | `/api/v1/analysis/listing` | Deterministic listing analysis for a `Product`. |
| POST | `/api/v1/analysis/listing/ai` | AI listing recommendations on top of `ListingAnalysis`. |
| POST | `/api/v1/analysis/competitors` | Deterministic comparison of a target `Product` against 1–3 competitor ASINs. |
| POST | `/api/v1/analysis/competitors/ai` | AI competitive insights on a completed comparison. |
| POST | `/api/v1/competitors/query` | Generate a deterministic Amazon search query from a target `Product`. |
| POST | `/api/v1/competitors/discover` | Rainforest Amazon.in search → ranked candidate listings. |
| POST | `/api/v1/reports/analyze` | Multipart CSV/XLSX upload → Search Term or Business Report analytics. |

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

- Quick Demo uses **mock** product data (`B0TEST0001`–`B0TEST0003`).
- Real ASIN lookup uses **Rainforest**. The API key stays on the backend.
- Amazon.in public lookup remains available as `PRODUCT_PROVIDER=amazon_public`. It is experimental and often blocked.
- Manual input is **not persisted**. Refreshing the page clears it.
- Listing Intelligence scores are **deterministic**. AI recommendations are a separate, optional step.
- Competitor ASINs may be **entered by the seller** or **selected from search candidates**. Discovery does not auto-compare.
- Competitor comparison scores use the **same listing engine**. AI competitive insights are a separate, optional step.
- Seller reports are **ephemeral**. Uploads are analyzed in memory and discarded.
- Report analytics are **deterministic**. AI interpretation is not in this milestone.
- Image analysis counts URLs only. It does not download images or judge visual quality.
- Scoring thresholds are heuristics, not Amazon policy.
- No SP-API or Ads API yet.
- No Claude provider yet. OpenAI is the current AI provider.
- No database or Redis yet. Catalog and AI lookups use a small in-memory TTL cache only. API usage ledgers are process-lifetime memory.
- Rainforest **account credits** come from the Account API. This app’s call counts are a separate ledger. See [docs/api-usage-dashboard.md](docs/api-usage-dashboard.md).
- OpenAI **provider spend** needs an Admin API key (`OPENAI_ADMIN_API_KEY`). App-estimated cost is calculated from response token usage.
- Bulk due diligence currently uses **mock product and mock AI providers**. Live Rainforest/OpenAI bulk is guarded off. See [docs/bulk-asin-due-diligence.md](docs/bulk-asin-due-diligence.md).
- No authentication yet.
- India marketplace (`amazon.in`) only. Report money is treated as INR.

# Amazon Seller Intelligence — Change Summary

**Date:** 20 August 2026  
**Scope:** Milestone 10C.1 Professional PDF Design V2  
**Status:** Milestone 10C.1 complete. Authentication, SP-API, Ads API, Redis/Celery, Reviews API, Offers API, hard delete, Recycle Bin, email/public PDFs, and white-label templates were not started.

This document records what was built and updated. It is a change log, not a product spec.

---

## Milestone 10C.1 — Professional PDF Design V2 (20 August 2026)

Client PDF template upgraded from `analysis-report-v1` to `analysis-report-v2`. Persistence, deletion, analysis logic, and provider calls are unchanged.

- Presentation view model (`ClientReportViewModel`) formats labels, currency, dates, grouping, and Unicode text. ReportLab only lays out those fields.
- Cover, executive score dashboard, What to Fix First, KPI cards, coverage cards, findings groups, action-plan roadmap, suggested-copy boxes, and subdued metadata.
- v1 artifacts remain valid. Requesting PDF when only v1 exists generates and stores v2. Uniqueness remains `report_id + template_version`.
- Font embedding when a Unicode TTF is available on the host. No vendored font files. Vector score bars. Long text paginates instead of overflowing a table cell.

See [client-pdf-reports.md](client-pdf-reports.md).

---

## Milestone 10C — Report lifecycle & client PDF export (20 August 2026)

Saved History reports can be soft-deleted and exported as a professional A4 client PDF. Both operations use **persisted historical data only**.

- `DELETE /api/v1/reports/{report_id}` sets `analysis_runs.deleted_at`. Snapshots, listing/AI/image results, and scoring snapshots are retained.
- Deleted reports disappear from `GET /api/v1/reports` and return 404 on detail/PDF. Cross-organization access also returns 404.
- `POST /api/v1/reports/{report_id}/pdf` generates ReportLab A4 `analysis-report-v1` if missing; otherwise reuses the stored artifact. `GET` streams `application/pdf` from private `generated-reports`.
- History UI: Open, PDF (user-initiated, Generating…), and a ⋯ menu with Delete Report plus confirmation dialog.
- Migration `0003_report_lifecycle` adds `deleted_at` and `generated_reports.template_version`. `0001` and `0002` were not modified.

See [report-lifecycle.md](report-lifecycle.md) and [client-pdf-reports.md](client-pdf-reports.md).

---

## Milestone 10B — Custom Scoring Profiles (20 August 2026)

Optional organization-owned **Custom Weights** for the Listing Intelligence V2 **aggregate only**.

- Standard V2 (`standard-v2`, 20 / 25 / 20 / 20 / 15) remains the immutable benchmark and is always calculated.
- Custom profiles re-aggregate existing section scores. Section rules, findings, Market Signals, Data Coverage, AI V2, and Image AI are unchanged.
- Weights must total exactly 100 (server-side). Zero is allowed; negatives and totals other than 100 are rejected.
- Historical reports store a weight snapshot and custom score. Editing or archiving a profile does not rewrite old reports.
- Competitor comparison still uses standard listing scores. Custom competitor comparison was deferred.
- Creating, editing, selecting, or reweighting a profile uses **0** Rainforest and **0** OpenAI calls.

See [custom-scoring-profiles.md](custom-scoring-profiles.md). Completion record: [custom-scoring-profiles-report.md](custom-scoring-profiles-report.md).

---

## Milestone 10 — Persistence & Report History (20 August 2026)

Turned the in-memory analyzer into a persistent intelligence workspace.

- SQLAlchemy 2.0 + Alembic + PostgreSQL (Supabase). SQLite is used only for automated tests.
- Private Storage buckets `seller-report-uploads` and `generated-reports`. Service role key is backend-only.
- Default development organization (`DEFAULT_ORGANIZATION_ID`). Auth is future; tenant column exists now.
- Immutable `product_snapshots` (same ASIN can have many historical rows).
- `analysis_runs` plus listing V2 / AI V2 / image intelligence JSONB result tables. Optional AI/image attach to the same report. Optional failure → `partial`, deterministic result kept.
- `GET /api/v1/reports` (pagination/filters) and `GET /api/v1/reports/{id}` reconstruct the historical report with **zero** Rainforest and OpenAI calls.
- Frontend **History** (`/history`) is saved ASIN analyses. `/reports` remains Seller Central uploads.
- Seller report originals hashed (SHA-256) and stored. Duplicates are identified, not rejected.
- Bulk jobs/items and generated Excel persisted. Usage events dual-written when the database is configured.
- Live analysis still returns if save fails, with an explicit persistence warning.

See [persistence-supabase.md](persistence-supabase.md), [database-schema.md](database-schema.md), and [persistence-report.md](persistence-report.md).

Re-analyze current listing and report deletion are future. Re-analyze must create a new snapshot and run.

---

## Milestone 8D — Image & Media Intelligence V1 (20 August 2026)

Added optional multimodal visual intelligence as a **separate report**. Listing Score V1/V2 and AI listing V1/V2 are unchanged.

- Endpoint: `POST /api/v1/analysis/listing/v2/images/ai`
- Prompt: `image-intelligence-v1`
- Explicit **Analyze Images & Media** click only
- Deterministic HTTPS/allowlist URL validation and max-8 image selection
- Same `OpenAIProvider`; new `generate_multimodal_structured` (does not change 8C text calls)
- Cache + usage ledger workflow `image_intelligence_v1`
- 0 extra Rainforest calls; videos not analyzed as frames; no image generation; no numeric visual score

See [image-media-intelligence.md](image-media-intelligence.md).

---

## Milestone 8C — AI Content & SEO Intelligence V2 (20 August 2026)

Added a parallel V2 AI flow on top of `Product` + `ListingAnalysisV2`. V1 AI (`POST /api/v1/analysis/listing/ai`, `listing-intelligence-v1`) is unchanged.

- Endpoint: `POST /api/v1/analysis/listing/v2/ai`
- Prompt: `listing-intelligence-v2`
- One structured OpenAI call + existing repair retry; same `OpenAIProvider` and `OPENAI_MODEL`
- Cache keyed on V2 context + analysis + model + prompt version + provider (`AI_CACHE_TTL_SECONDS`, default 2700s)
- Usage ledger workflow: `listing_intelligence_v2`
- Primary UI action is **Generate AI strategy**; V1 AI is legacy-only
- No Rainforest request changes, no image vision, no Reviews/Offers APIs

See [ai-listing-intelligence-v2.md](ai-listing-intelligence-v2.md).

---

## Milestone 8B — Listing Intelligence V2 deterministic engine (20 August 2026)

Added `listing-score-v2` beside unchanged `listing-score-v1`.

- Endpoint: `POST /api/v1/analysis/listing/v2` (V1 `POST /api/v1/analysis/listing` unchanged).
- Listing quality weights: Title 20%, Bullet SEO readiness 25%, Description & A+ 20%, Media coverage 20%, Content structure 15%.
- Market signals and data coverage are separate objects and are **not** mixed into listing quality.
- When 8B shipped, AI listing recommendations still used V1 analysis.
- No Rainforest parameter changes and no OpenAI prompt changes.

See [listing-intelligence-v2.md](listing-intelligence-v2.md).

---

## Milestone 8A — Listing Intelligence V2 data foundation (20 August 2026)

The Rainforest `type=product` request is unchanged (`api_key`, `type`, `amazon_domain`, `asin`). Cold Analyze ASIN is still one product credit.

The normalized `Product` model now keeps additional optional fields from that same payload: all BSR rows, category path + IDs, Amazon-sold flag, availability type, variation `is_current_product`, richer video metadata, `videos_count`, optional image dimensions, optional A+, specifications, attributes, rating breakdown, featured/top reviews, and recent-sales **text**.

V1 listing scores, AI prompts/context, and the frontend UI were not redesigned. New Product fields are unused by React. See [listing-intelligence-v2-data-foundation.md](listing-intelligence-v2-data-foundation.md).

`ProviderCapabilities.reviews` now means a review corpus and is `false` for the product provider.

---

## Current capability


A user can:

1. Fetch a **real Amazon.in ASIN** through Rainforest (Analyze ASIN).
2. Look up a **mock** Amazon product by ASIN (Quick Demo).
3. Enter listing details by hand (Manual Product fallback).
4. Run **Listing Intelligence V2** on that product (deterministic listing quality, market signals, data coverage).
5. Generate **AI Strategy V2** (OpenAI, explicit click). V2 scores stay unchanged. V1 AI remains a legacy path.
6. Optionally **Analyze Images & Media** (OpenAI multimodal, explicit click). Separate visual report; listing scores unchanged.
7. Compare **1–3 seller-entered competitor ASINs**, then optionally generate AI competitive insights.
8. **Discover candidate competitors** with Rainforest Amazon search, then let the seller select up to three for the existing comparison.
9. Upload a **Search Term Report** or **Business Report** and view deterministic PPC / business analytics.
10. See compact **API Budget** cards (Rainforest account credits vs this app’s calls; OpenAI provider spend vs app-estimated cost).
11. Upload a CSV/XLSX of ASINs for **Bulk Due Diligence** (mock catalog and mock AI by default; Excel report after the job completes).
12. Reopen **saved ASIN analyses** from History without calling Rainforest or OpenAI.

No SP-API, Claude, or authentication is required. Persistence is optional until `DATABASE_URL` is set.

The Amazon.in public HTML lookup remains available but is experimental. Rainforest is the V1 default.

---

## Architecture (current primary path)

The rest of the application does not depend on a specific catalog vendor. Product data is fetched through `ProductDataProvider`.

Current single-ASIN user path:

```text
Analyze ASIN
    ↓
GET /api/v1/products/{asin}
    ↓
ProductService
    ↓
RainforestProductDataProvider   (mock catalog still intercepts B0TEST*)
    ↓
Normalized Product
    ↓
ListingAnalysisV2Service
    ↓
Listing Intelligence V2
       ├── Listing Quality
       ├── Market Signals
       └── Data Coverage
             ↓
       Generate AI Strategy   [explicit click]
             ↓
AIListingIntelligenceV2Service
             ↓
OpenAIProvider

Optional visual path [explicit Analyze Images & Media click]:

Normalized Product + ListingAnalysisV2
       ↓
AIImageIntelligenceService
       ↓
OpenAIProvider.generate_multimodal_structured
       ↓
Image & Media Intelligence
```

V1 listing analysis and V1 listing AI remain available as legacy/backward-compatible endpoints and a collapsed UI panel.

Provider inventory:

```text
ProductDataProvider
        │
        ├── MockProductDataProvider              implemented (demo ASINs; bulk default)
        ├── RainforestProductDataProvider        implemented (Analyze ASIN default)
        ├── AmazonPublicProductDataProvider      experimental
        └── AmazonOfficialProductDataProvider    future

AIProvider
        │
        ├── OpenAIProvider                       implemented
        └── ClaudeProvider                       future
```

Both endpoints return an HTTP envelope. Provenance is metadata, not a field on `Product`:

```json
{
  "product": { "...normalized Product..." },
  "meta": { "source": "mock" }
}
```

`meta.source` is `"mock"`, `"manual"`, `"rainforest"`, or `"amazon_public"`.

Marketplace identifiers use Amazon **domain** form. V1 supports `amazon.in` only.

---

## Milestone 0 + 1 — Foundation

Created the local monorepo and the first working product lookup.

### Added

- Monorepo layout: `apps/api` (FastAPI) and `apps/web` (Next.js)
- Normalized `Product` model (`Price`, `Image`, `BSR`, `Seller`, `Variation`)
- `ProductDataProvider` abstraction and `MockProductDataProvider`
- `ProductService` so routes do not talk to providers
- `GET /health`
- `GET /api/v1/products/{asin}`
- Next.js UI: enter an ASIN, see product details
- CORS for `http://localhost:3000`
- Root `README.md`, `.gitignore`, `.env.example`
- `docs/marketplace.md`
- Backend tests for health, valid ASIN, invalid ASIN, unknown ASIN
- Git initialized at the repository root (no commit)

### Mock catalog

| ASIN | Product |
|------|---------|
| `B0TEST0001` | AuroraGlow Vitamin D3 Softgels |
| `B0TEST0002` | NimbusFoam Memory Contour Pillow |
| `B0TEST0003` | PeakPulse Resistance Bands Set |

These are fictional products. They are not real Amazon listings.

### Validation (lookup)

- ASIN: 10 alphanumeric characters, normalized to uppercase
- Does not require a `B0` prefix
- Invalid format → `400`
- Unknown mock ASIN → `404`
- Unsupported marketplace → `400`

---

## Milestone 2 — Manual product input

Preserved mock lookup. Added a second user flow for real ASINs without any Amazon integration.

### Added

| File | Purpose |
|------|---------|
| `apps/api/app/models/manual.py` | Request body for manual listing input |
| `apps/api/tests/test_manual_products.py` | Tests for the manual endpoint |
| `apps/web/src/components/manual-product-form.tsx` | Manual listing form |
| `apps/web/src/components/ui/textarea.tsx` | Description field |

### Modified

| File | What changed |
|------|----------------|
| `apps/api/app/models/product.py` | Added `ProductSource`, `ProductMeta`, `ProductResponse` |
| `apps/api/app/models/__init__.py` | Exports for the new types |
| `apps/api/app/services/product_service.py` | `create_from_manual()` maps input → `Product` |
| `apps/api/app/api/routes/products.py` | `POST /manual`; GET now returns `{ product, meta }` |
| `apps/api/app/main.py` | CORS allows `POST`; validation errors return `400` |
| `apps/api/tests/test_products.py` | Assertions updated for the envelope |
| `apps/web/src/lib/types.ts` | Envelope + manual input types |
| `apps/web/src/lib/api.ts` | `createManualProduct()` |
| `apps/web/src/components/product-lookup.tsx` | Quick Demo / Analyze Real Product tabs |
| `apps/web/src/components/product-result.tsx` | Reused for both flows; source badge |
| `README.md` | Milestone 2 setup and API docs |

### Deleted

| File | Reason |
|------|--------|
| `apps/web/AGENTS.md` | Next.js scaffold leftover |
| `apps/web/CLAUDE.md` | Next.js scaffold leftover |

### New endpoint

```text
POST /api/v1/products/manual
```

Required: ASIN, title.  
Optional: brand, price, rating, review count, category, BSR, availability, seller, description, bullet points, image URLs, marketplace.

- Data is **not persisted**. There is still no database.
- Bullet points are separate form rows (max 10), not JSON.
- Images are URLs only. No upload, no proxy, no download.
- Marketplace defaults to `amazon.in`.

### Validation (manual)

- ASIN: 10 alphanumeric characters, uppercase
- Title: required
- Price: ≥ 0
- Rating: 0–5
- Review count: non-negative integer

---

## Milestone 3 — Deterministic listing intelligence

Added Listing Intelligence on top of the existing `Product` model. No AI.

```text
Product
  → ListingAnalysisService.analyze()
    → analytics/listing_rules.py
      → ListingAnalysis
        → POST /api/v1/analysis/listing
          → UI
```

Scoring is not in routes, providers, or React.

### Added

| File | Purpose |
|------|---------|
| `apps/api/app/models/listing_analysis.py` | `ListingAnalysis`, findings, recommendations, request/response envelope |
| `apps/api/app/analytics/listing_rules.py` | Explicit v1 scoring rules |
| `apps/api/app/analytics/__init__.py` | Analytics package |
| `apps/api/app/services/listing_analysis_service.py` | Service entry point for routes/jobs |
| `apps/api/app/api/routes/analysis.py` | `POST /api/v1/analysis/listing` |
| `apps/api/tests/test_listing_analysis.py` | Listing analysis tests |
| `apps/web/src/components/listing-intelligence.tsx` | Score, findings, and actions UI |
| `docs/listing-intelligence.md` | Weights, thresholds, limitations |

### Modified

| File | What changed |
|------|----------------|
| `apps/api/app/api/routes/__init__.py` | Registered analysis router |
| `apps/api/app/models/__init__.py` | Exports for analysis types |
| `apps/api/app/main.py` | Version 0.3.0 |
| `apps/web/src/lib/types.ts` | Analysis types |
| `apps/web/src/lib/api.ts` | `analyzeListing()` |
| `apps/web/src/components/product-lookup.tsx` | Analyze Listing action |
| `README.md` | Listing Intelligence docs |
| `docs/changes.md` | This file |

### New endpoint

```text
POST /api/v1/analysis/listing
```

Request reuses `Product`. Response:

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

This analysis is **deterministic and does not currently use AI.** Score version: **v1**.

Weights: title 20%, bullets 25%, description 15%, images 15%, completeness 15%, social proof 10%.

Full rules: [listing-intelligence.md](listing-intelligence.md).

---

## Milestone 4 — Experimental Amazon.in public lookup

Added automatic retrieval of publicly visible Amazon.in listing data from an ASIN. No AI. No SP-API.

```text
ASIN
  → GET /api/v1/products/{asin}
    → AmazonPublicProductDataProvider
      → httpx GET https://www.amazon.in/dp/{ASIN}
      → AmazonProductParser
        → Product
```

The rest of the app still consumes only the normalized `Product` model. Listing Intelligence is unchanged.

### Added

| File | Purpose |
|------|---------|
| `apps/api/app/providers/amazon_public.py` | Public Amazon.in HTTP provider |
| `apps/api/app/providers/memory_cache.py` | In-memory TTL cache (10 minutes) |
| `apps/api/app/parsers/amazon_product_parser.py` | JSON-LD + DOM parser |
| `apps/api/app/parsers/__init__.py` | Parser package |
| `apps/api/tests/test_amazon_public.py` | Parser/provider tests (no live Amazon) |
| `apps/api/tests/fixtures/amazon/*.html` | Fictional HTML fixtures |
| `docs/amazon-public-provider.md` | Experimental-provider notes |

### Modified

| File | What changed |
|------|----------------|
| `apps/api/app/core/config.py` | Default `PRODUCT_PROVIDER=amazon_public` |
| `apps/api/app/core/exceptions.py` | Blocked / fetch / parse errors |
| `apps/api/app/models/product.py` | `meta.source = amazon_public` |
| `apps/api/app/providers/factory.py` | Selects mock or amazon_public |
| `apps/api/app/providers/mock.py` | `has_product()` for demo ASINs |
| `apps/api/app/services/product_service.py` | Demo ASINs stay on mock catalog |
| `apps/api/app/api/routes/products.py` | Maps 404 / 502 / 503 |
| `apps/api/tests/conftest.py` | Forces mock during pytest |
| `apps/web/src/components/product-lookup.tsx` | Analyze ASIN / Quick Demo / Manual Product |
| `apps/web/src/components/product-result.tsx` | “Not available” for missing fields |
| `apps/web/src/lib/api.ts` | 502 / 503 messages |
| `README.md` | Milestone 4 docs |

### Behaviour

- Primary tab: **Analyze ASIN** — enter a real ASIN, no manual title/price/bullets.
- **Quick Demo** still uses `B0TEST0001`–`B0TEST0003` and never calls Amazon.
- **Manual Product** remains as fallback when Amazon blocks the lookup.
- Missing fields stay `null` or `[]`. Nothing is invented.
- Parser prefers JSON-LD, then DOM selectors.
- Successful lookups are cached in memory for 10 minutes.

### Errors

| Situation | HTTP |
|-----------|------|
| Invalid ASIN | 400 |
| Not found | 404 |
| Blocked / CAPTCHA / throttled | 503 |
| Timeout / parse failure | 502 |

### Live smoke test

A real Amazon.in ASIN lookup returned **503** (blocked/throttled). The error path is clean. Use Manual Product as fallback.

This provider is **experimental**, not Amazon-supported, and not the long-term production path. Future production remains SP-API behind the same interface.

Details: [amazon-public-provider.md](amazon-public-provider.md).

---

## Milestone 5 — Rainforest as primary real-ASIN provider

Added `RainforestProductDataProvider` behind the existing `ProductDataProvider` interface. No AI. Product, Listing Intelligence, mock catalog, and manual flow are unchanged.

```text
ASIN
  → GET /api/v1/products/{asin}
    → RainforestProductDataProvider
      → GET https://api.rainforestapi.com/request
      → map_rainforest_product()
        → Product
```

The API key is read only from backend env `RAINFOREST_API_KEY`. It is never sent to Next.js.

### Added

| File | Purpose |
|------|---------|
| `apps/api/app/providers/rainforest.py` | Rainforest HTTP provider |
| `apps/api/app/parsers/rainforest_product_mapper.py` | Official-schema mapping onto `Product` |
| `apps/api/tests/test_rainforest.py` | Mapper/provider tests (no live Rainforest, no real key) |
| `apps/api/tests/fixtures/rainforest/*.json` | Documented-shape fixtures |
| `docs/rainforest-provider.md` | Provider notes |

### Modified

| File | What changed |
|------|----------------|
| `apps/api/app/core/config.py` | Default `PRODUCT_PROVIDER=rainforest`; `RAINFOREST_API_KEY` as `SecretStr` |
| `apps/api/app/core/exceptions.py` | `ProviderConfigurationError` |
| `apps/api/app/models/product.py` | `meta.source = rainforest` |
| `apps/api/app/providers/factory.py` | Selects rainforest, mock, or amazon_public |
| `apps/api/app/api/routes/products.py` | Config / 503 / 502 mapping |
| `apps/web/src/lib/types.ts` | `rainforest` source |
| `apps/web/src/components/product-lookup.tsx` | Analyze ASIN copy |
| `apps/web/src/components/product-result.tsx` | Rainforest badge |
| `README.md` | Milestone 5 docs |

### Behaviour

- Default provider is Rainforest.
- Query params use httpx `params`: `type=product`, `amazon_domain=<marketplace>`, `asin`, `api_key`.
- Marketplace `amazon.in` maps directly to Rainforest `amazon_domain`.
- Demo ASINs still never leave the mock catalog.
- Successful lookups are cached in memory for 10 minutes.
- Missing Rainforest fields stay `null` or `[]`.

### Live smoke test

A real Amazon.in ASIN lookup returned **200** with `meta.source = rainforest`. Title, brand, bullets, and images were present. The API key was not in the response. Demo ASINs still return `source=mock`.

The key lives only in gitignored `apps/api/.env`. It does not belong in `.env.example`, Next.js, or git.

Details: [rainforest-provider.md](rainforest-provider.md).

---

## Milestone 6 — AI Listing Intelligence

Added strategic AI recommendations on top of deterministic `ListingAnalysis`. No Claude. Scores are unchanged.

```text
Product + ListingAnalysis
  → AIListingIntelligenceService
    → OpenAIProvider (Responses API, structured output)
      → AIListingIntelligence
```

### Added

| File | Purpose |
|------|---------|
| `apps/api/app/ai/base.py` | `AIProvider` abstraction |
| `apps/api/app/ai/openai_provider.py` | OpenAI structured-output provider |
| `apps/api/app/ai/factory.py` | Selects the AI provider |
| `apps/api/app/ai/context.py` | Compact Product + analysis context |
| `apps/api/app/prompts/listing_intelligence.py` | Prompt version `listing-intelligence-v1` |
| `apps/api/app/models/ai_listing_intelligence.py` | Structured AI schema |
| `apps/api/app/services/ai_listing_intelligence_service.py` | Application service |
| `apps/api/tests/test_ai_listing_intelligence.py` | Service/endpoint tests (no live OpenAI) |
| `apps/api/tests/test_openai_provider.py` | Provider config/error tests |
| `apps/web/src/components/ai-listing-intelligence.tsx` | AI recommendations UI |
| `docs/ai-listing-intelligence.md` | Architecture and policy |

### Modified

| File | What changed |
|------|----------------|
| `apps/api/app/core/config.py` | `AI_PROVIDER`, `OPENAI_API_KEY`, `OPENAI_MODEL` |
| `apps/api/app/core/exceptions.py` | AI config / auth / rate-limit / structured-output errors |
| `apps/api/app/api/routes/analysis.py` | `POST /listing/ai` |
| `apps/api/app/models/__init__.py` | Exports for AI types |
| `apps/api/app/main.py` | Version 0.6.0 |
| `apps/api/app/providers/memory_cache.py` | Hash-keyed TTL cache for AI results |
| `apps/api/pyproject.toml` | `openai` dependency |
| `apps/api/.env.example` | Blank OpenAI placeholders |
| `apps/web/src/lib/types.ts` | AI listing types |
| `apps/web/src/lib/api.ts` | `generateAIListingIntelligence()` |
| `apps/web/src/components/product-lookup.tsx` | Generate AI Recommendations button |
| `apps/web/src/components/listing-intelligence.tsx` | Deterministic-analysis label |
| `README.md` | Milestone 6 docs |

### New endpoint

```text
POST /api/v1/analysis/listing/ai
```

Request reuses `Product` and `ListingAnalysis`. Response:

```json
{
  "product": { "...unchanged Product..." },
  "analysis": { "...unchanged ListingAnalysis..." },
  "ai_intelligence": { "...AIListingIntelligence..." },
  "meta": {
    "engine": "ai",
    "provider": "openai",
    "model": "gpt-5.4",
    "prompt_version": "listing-intelligence-v1",
    "source": "rainforest",
    "usage": { "input_tokens": 0, "output_tokens": 0, "total_tokens": 0 }
  }
}
```

The UI calls this only after **Generate AI Recommendations**. Analyze ASIN and Analyze Listing do not call OpenAI.

### Behaviour

- Deterministic scores, findings, and metrics remain authoritative.
- OpenAI-specific code stays in `OpenAIProvider`.
- Product copy is sent as untrusted data (`BEGIN/END UNTRUSTED PRODUCT DATA`).
- Context is normalized Product + ListingAnalysis only. No Rainforest JSON or HTML.
- One model call, plus at most one structured-output repair retry.
- Identical analyses are cached in memory for 45 minutes.
- Development default model: `gpt-5.4` via `OPENAI_MODEL`.

### Errors

| Situation | HTTP |
|-----------|------|
| Missing key / model | 503 |
| Auth failure | 503 |
| Rate limit / quota | 503 |
| Timeout / network / unusable structured output | 502 |
| Safety refusal | 422 |

### Live smoke test

A real Amazon.in ASIN lookup returned **200** (`source=rainforest`), then deterministic analysis **74 / 100**, then OpenAI AI recommendations **200**.

- Provider: `openai`
- Model: `gpt-5.4`
- Prompt version: `listing-intelligence-v1`
- Latency: about 17 seconds
- Tokens: 1634 input / 1332 output / 2966 total
- Deterministic score unchanged
- Executive summary, priorities, title/bullet suggestions, and seller action plan were present
- No obvious unsupported conversion/competitor-metric phrases
- API key was not in the response

Details: [ai-listing-intelligence.md](ai-listing-intelligence.md).

---

## Milestone 7 — Competitor Intelligence

Added manual competitor comparison on top of the existing Product and Listing Intelligence layers. The seller enters 1–3 competitor ASINs. Rainforest retrieves each competitor. `ListingAnalysisService` scores every listing with the same rules. `CompetitorComparisonService` produces structured metrics and gaps. `AICompetitiveIntelligenceService` interprets that comparison after an explicit click.

### Architecture

```text
Target Product (already loaded)
+ competitor ASINs
  → concurrent ProductService lookups
    → ListingAnalysisService
      → CompetitorComparisonService
        → CompetitorComparison

CompetitorComparison
  → [Generate AI Competitive Insights]
    → AICompetitiveIntelligenceService
      → existing AIProvider / OpenAIProvider
        → AICompetitiveIntelligence
```

### What was added

- `apps/api/app/analytics/competitor_rules.py` — deterministic comparison version `v1`
- `apps/api/app/services/competitor_comparison_service.py`
- `apps/api/app/services/ai_competitive_intelligence_service.py`
- `apps/api/app/prompts/competitive_intelligence.py` — `competitive-intelligence-v1`
- `POST /api/v1/analysis/competitors`
- `POST /api/v1/analysis/competitors/ai`
- Frontend competitor input, comparison table, and AI competitive insights

### Evidence rules

Review count is visible review volume, not sales. Rating differences are observed ratings. Price differences are observed prices. AI must not invent sales, conversion, ads, or product claims. At the time Milestone 7 shipped, automatic competitor discovery was not implemented. Candidate search was added later; see [competitor-discovery.md](competitor-discovery.md).

### Live smoke test

One controlled live run used real Amazon.in ASINs. Rainforest retrieved the target and two competitors. Deterministic comparison and OpenAI competitive intelligence both succeeded.

- Target: `B09G9BL5CP`
- Competitors: 2 retrieved, 0 failed
- Target listing score: **74 / 100** (unchanged from the earlier listing-intelligence smoke)
- Competitor scores: 73 and 74 using the same listing engine
- Rainforest requests: 3 (target + 2 competitors)
- OpenAI provider / model: `openai` / `gpt-5.4`
- Prompt version: `competitive-intelligence-v1`
- Latency: about 36 seconds
- Tokens: 4800 input / 1763 output / 6563 total
- Structured AI output present, including price caution
- No unsupported sales/conversion/ads phrases detected
- API keys were not in the response

Observed catalog prices were missing for these listings and were represented as null rather than invented.

Details: [competitor-intelligence.md](competitor-intelligence.md).

---

## Milestone 8 — Amazon search & competitor discovery

Added seller-triggered Amazon.in search behind a new `AmazonSearchProvider`. Discovery suggests candidate listings. The seller still selects up to three ASINs. Those ASINs enter the existing Milestone 7 comparison. No OpenAI call occurs during discovery. No full Rainforest product fetch occurs until Compare Selected.

### Architecture

```text
Target Product
  → generated or edited search query
    → Rainforest type=search
      → AmazonSearchHit snippets
        → filter target / dedupe / relevance
          → seller selects ≤3
            → existing POST /api/v1/analysis/competitors
```

### What was added

- `apps/api/app/search/` — `AmazonSearchProvider`, Rainforest + mock search providers
- `apps/api/app/parsers/rainforest_search_mapper.py`
- `apps/api/app/analytics/competitor_search_query.py`
- `apps/api/app/analytics/competitor_relevance.py`
- `apps/api/app/services/competitor_discovery_service.py`
- `POST /api/v1/competitors/query`
- `POST /api/v1/competitors/discover`
- Frontend Discover Competitors → candidate list → Compare Selected

### Live smoke test

Target ASIN `B09G9BL5CP` (amazon.in). Generated query: `iphone 128gb blue`. Rainforest `type=search` returned 16 mapped hits; 12 displayed after ranking. Target ASIN was not among candidates. Sponsored flags were absent (`null`) for this response. Discovery used 1 search request and 0 extra product fetches. Selecting two candidates (`B0GZSNBYY9`, `B0GS19LS83`) reused existing `CompetitorComparisonService` (target score 74; competitor scores 78 and 75). OpenAI was not called during discovery.

Note: V1 query heuristics stripped `13` from “iPhone 13” because it matched the quantity-token rule. Sellers can restore it with the query override.

Details: [competitor-discovery.md](competitor-discovery.md).

---

## Milestone 9 — Seller Central report upload + deterministic analytics

Added CSV/XLSX upload for two Amazon export types. Report type is detected from headers. Rows are normalized, then PPC or Business analytics run in Python. No OpenAI call. No database.

### Architecture

```text
Upload CSV/XLSX
  → ReportDetectionService
    → SearchTermReportParser | BusinessReportParser
      → SearchTermPerformanceRow | BusinessPerformanceRow
        → PPCAnalyticsService | BusinessAnalyticsService
          → POST /api/v1/reports/analyze
```

### What was added

- `apps/api/app/reports/` — detection, CSV/XLSX loader, column aliases, parsers
- `apps/api/app/analytics/ppc_rules.py`
- `apps/api/app/analytics/business_report_rules.py`
- `POST /api/v1/reports/analyze`
- Frontend **Seller Reports** (`/reports`)

Details: [seller-report-analytics.md](seller-report-analytics.md).

---

## Product media quality (Rainforest gallery)

Rainforest `images[].link` was mapped as-is and rendered in a stretched `object-cover` grid. Live amazon.in payloads can include 38×50 `_SX38_` thumbs and video play-icon overlays. Mapping now prefers `main_image` first, chooses the highest-quality URL per Amazon image ID (including the unsized `I/{id}.jpg` sibling Rainforest already uses for the main image), and keeps videos on `Product.videos`. The UI is a contained main-image viewer plus thumbnails.

Details: [rainforest-provider.md](rainforest-provider.md).

---

## Tests

Current complete backend suite (20 August 2026 checkpoint):

```text
309 passed
```

Earlier milestone subsections below this heading recorded smaller counts at the time those milestones shipped. Do not add those historical numbers together.

Frontend production build (`npm run build`) succeeded. `npm run lint` reports 2 pre-existing `react-hooks/set-state-in-effect` errors in `theme-toggle.tsx` and `usage-panel.tsx` (not introduced by 8A–8D). There is no separate `typecheck` script; TypeScript runs as part of `next build`.

- Milestone 0/1: 7 product/health tests
- Milestone 2: 7 manual product tests
- Milestone 3: 11 listing analysis tests
- Milestone 4: 12 Amazon public parser/provider tests (no live amazon.in)
- Milestone 5: 16 Rainforest mapper/provider tests (no live Rainforest)
- Milestone 6: 19 AI listing intelligence tests (no live OpenAI)
- Milestone 7: 27 competitor comparison / competitive AI tests (no live Rainforest or OpenAI)
- Milestone 8: 19 competitor discovery / search-query tests (no live Rainforest)
- Milestone 9: 25 seller report upload/parser/analytics tests (fictional fixtures only)

Frontend production build (`npm run build`) succeeded.

---

## How to run

Backend:

```bash
cd apps/api
cp .env.example .env
# set RAINFOREST_API_KEY and OPENAI_API_KEY in apps/api/.env (backend only)
# optional: OPENAI_ADMIN_API_KEY for organization spend, OPENAI_BUDGET_USD=100
uv sync
uv run uvicorn app.main:app --reload --port 8000
```

Frontend:

```bash
cd apps/web
cp .env.example .env.local
npm install
npm run dev
```

- API: http://localhost:8000  
- App: http://localhost:3000  
- Health: http://localhost:8000/health

---

## API Budget dashboard (after Milestone 9)

Two layers, never presented as the same thing:

1. **Provider-account usage** — Rainforest `GET /account` credits; OpenAI organization Costs API spend when an Admin API key is configured.
2. **Application ledger** — this process’s product/search/AI calls, token totals, estimated OpenAI cost, and local cache savings.

Frontend: compact `ApiBudgetDashboard` under the main nav. Backend: `GET /api/v1/usage/dashboard`. Keys stay on FastAPI. Account API calls are free and are not counted as paid Rainforest product/search calls. See [api-usage-dashboard.md](api-usage-dashboard.md).

---

## Bulk ASIN Due Diligence (mock-only)

CSV/XLSX upload of ASINs, in-process jobs, existing `Product` + `ListingAnalysis`, portfolio report, Excel download. Product and AI providers for bulk default to **mock**. `BULK_LIVE_PROVIDER_CALLS_ENABLED=false` refuses Rainforest/OpenAI in the bulk path. Single-ASIN Rainforest/OpenAI defaults are unchanged. See [bulk-asin-due-diligence.md](bulk-asin-due-diligence.md).

---

## Intentionally not built

These remain out of scope:

- Production scraping / Selenium / proxies / CAPTCHA solving
- Claude provider
- SP-API / Ads API
- Database / Supabase
- Authentication
- Redis / Celery
- Keyword tools, review scraping, full Reviews API intelligence, Offers API
- Image generation / image editing
- PDF export (Bulk **Excel** export is implemented)
- OpenAI seller-report interpretation
- MCP / agents
- Autonomous Amazon actions

---

## Git

Remote: `origin` → `https://github.com/Kapil638/amazon-seller-intelligence.git`  
Branch: `main`

Local `.env` files are gitignored. The existing file `Amazon Seller Co-Pilot.pdf` was left untouched and was not used as a spec.

---

## Suggested next step (not started)

Pause implementation and generate **architecture documentation** for the system as built. Do not start another product feature until that pause is requested.

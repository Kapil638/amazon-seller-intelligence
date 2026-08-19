# Bulk ASIN Due Diligence

Portfolio listing review from a CSV/XLSX of ASINs. This is a **report-oriented** workflow. It does not render every product in the single-ASIN UI.

**This milestone is mock-only.** Development and tests must not consume Rainforest or OpenAI credits.

## Input format

Upload `.csv` or `.xlsx`.

Recognized ASIN column names (case-insensitive):

- `ASIN`
- `asin`
- `Amazon ASIN`
- `Product ASIN`
- `Amazon_ASIN`

If none of those headers exist, the API returns a validation error. The file is not processed.

A sample mock file lives at `apps/api/tests/fixtures/bulk/mock_asins.csv`.

## ASIN handling

For each non-blank row:

1. Trim and uppercase
2. Validate 10-character alphanumeric ASIN
3. Ignore fully blank rows
4. Keep invalid rows as failures; do not reject the whole file
5. Deduplicate **before** any provider lookup

Tracked ingest stats:

```text
input_rows
valid_rows
invalid_rows
duplicate_rows_removed
unique_asins
```

## Max ASINs

`MAX_BULK_ASINS=100` (configurable).

If unique valid ASINs exceed the limit, the upload is **rejected**. It is never silently truncated.

## Standard vs Deep AI

**Standard Due Diligence** (default):

```text
Product → ListingAnalysis → portfolio aggregation
```

OpenAI calls = 0.

**Deep AI Due Diligence**:

Selects products, then attaches `AIListingIntelligence`. In this milestone the AI provider is `MockAIProvider` only. Selection options:

- High-priority ASINs only (default)
- Top N weakest ASINs (`top_n`, default 10)
- All ASINs

Priority is deterministic. Mock AI never assigns portfolio priority.

## Priority logic

```text
HIGH    overall score < 50  OR any high-severity finding
MEDIUM  score 50–69         OR two or more medium findings (and not HIGH)
LOW     score >= 70         AND no high-severity finding
```

## Mock testing

Defaults (do not change single-ASIN Rainforest/OpenAI):

```text
BULK_PRODUCT_PROVIDER=mock
BULK_AI_PROVIDER=mock
BULK_LIVE_PROVIDER_CALLS_ENABLED=false
```

`MockProductDataProvider` / `BulkMockProductDataProvider` return fictional catalog products. Unknown ASINs fail as not-found. `B0BLKTRN01` fails once, then succeeds (retry test).

When `BULK_LIVE_PROVIDER_CALLS_ENABLED=false`, selecting `rainforest` or `openai` for bulk **fails fast** before any HTTP.

## Provider abstraction

```text
Bulk Analysis → ProductDataProvider → MockProductDataProvider   (now)
Bulk Analysis → ProductDataProvider → RainforestProductDataProvider  (later)
```

Bulk never contains Rainforest-specific mapping. Later enablement (document only — not enabled):

```text
BULK_PRODUCT_PROVIDER=rainforest
BULK_AI_PROVIDER=openai
BULK_LIVE_PROVIDER_CALLS_ENABLED=true
```

Do not turn this on until you intend to spend credits.

## Future live flow (not exercised)

```text
ASIN → cache check → Rainforest only if needed → Product
→ ListingAnalysis → AI only if requested
```

Cache key for products: `provider|marketplace|ASIN` (example `mock|amazon.in|B0BLKSTR01`).

AI cache key: SHA-256 of Product + ListingAnalysis + AI provider + model + prompt version.

## Cache TTLs

```text
PRODUCT_CACHE_TTL_SECONDS=86400
AI_ANALYSIS_CACHE_TTL_SECONDS=604800
```

In-memory only. Restarting the API process clears the cache.

## Dedup and cache-first

Duplicates are removed before lookup, so each unique ASIN is requested at most once per job. A second identical job should see cache hits and fewer (or zero) mock provider calls.

## Concurrency

`BULK_PRODUCT_CONCURRENCY=3`. ASINs are processed with a semaphore, not all at once.

## Retry policy

- not-found → no retry
- validation failure → no retry
- transient `ProductFetchFailedError` → **one** retry

No multi-attempt retry loops.

## Jobs

```text
POST /api/v1/bulk/preview
POST /api/v1/bulk/jobs
GET  /api/v1/bulk/jobs/{job_id}
GET  /api/v1/bulk/jobs/{job_id}/report.xlsx
```

In-process async job manager (`InProcessJobBackend` + `InMemoryJobStore`). Isolated so Redis/Celery/RQ/Temporal can replace it later. No Redis/Celery in V1.

Statuses: `queued` → `running` → `completed` | `completed_with_errors` | `failed`.

Frontend polls. No websockets.

## Excel report

Sheets:

- Executive Summary
- Product Findings
- Failures
- API Usage
- AI Recommendations (Deep AI mock mode only)

## API usage ledger

Each job records mock provider calls, cache hits, retries, and AI mock calls. The note is **Mock provider — no paid API usage**. These are not Rainforest/OpenAI paid calls and are separate from the API Budget dashboard.

## Out of scope

Bulk does **not** run competitor discovery, Amazon search, competitor comparison, or competitive AI.

## Current limitations

- In-memory jobs and caches vanish on process restart
- Live Rainforest/OpenAI bulk paths are implemented as factory options but **guarded off**
- Mock catalog is fictional
- No PDF export

# Seller Report Analytics (parser / analytics v1)

Upload an Amazon Seller Central CSV or XLSX. The file is parsed, normalized, and analyzed in memory. It is **not stored**.

This milestone does **not** call OpenAI. Metrics are Python calculations only.

## Supported report types

1. **Sponsored Products Search Term Report** (`search_term_report`)
2. **Business Report** / Detail Page Sales and Traffic (`business_report`)

Other Amazon reports are rejected with a clear error.

## Architecture

```text
File
  → validate extension / size
    → TabularFile (CSV or XLSX cell values)
      → ReportDetectionService
        → ReportParser
          → normalized rows
            → PPCAnalyticsService | BusinessAnalyticsService
              → structured analysis
                → frontend
```

The analytics engines consume normalized rows only. They do not know whether the source was CSV, XLSX, or (later) Ads API / SP-API.

```text
Seller Report Upload          (now)
Amazon Ads API / SP-API       (future)
        ↓
same SearchTermPerformanceRow / BusinessPerformanceRow
        ↓
same analytics services
```

## Upload limits

- Extensions: `.csv`, `.xlsx` only (`.xls` is rejected)
- Maximum size: **25 MB** (`REPORT_MAX_UPLOAD_BYTES=26214400`)
- XLSX: `data_only=True` — cached cell values, no formula execution, no macros
- Password-protected workbooks are rejected
- Empty files are rejected

## Detection rules

Headers are normalized (lowercase, punctuation stripped, `%` → `percent`).

A report is detected when at least **three distinctive columns** match:

- Search Term: Customer Search Term, Spend, Impressions, Campaign Name, Match Type, Targeting
- Business: (Child) ASIN, Sessions, Page Views, Buy Box %, Units Ordered, Unit Session %

Filename is a **weak hint only** (for example `business` or `search-term`) and is used only when the header score is 2. Filename alone never classifies a file.

If both types match: ambiguous error. If neither matches: unknown report.

## Parser versions

| Parser | Version |
|--------|---------|
| Search Term Report | `search-term-parser-v1` |
| Business Report | `business-report-parser-v1` |

## Required / optional fields

### Search Term Report

Required: Customer Search Term, Impressions, Clicks, Spend, Sales, Orders.

Optional: Date, Campaign Name/ID, Ad Group Name/ID, Targeting, Match Type, Units, Currency.

Sales/Orders aliases include `7/14/30 Day Total Sales` and `7/14/30 Day Total Orders (#)`. Cost maps to Spend.

### Business Report

Required: (Child) ASIN, Sessions, and at least one of Units Ordered or Ordered Product Sales.

Optional: Date, Parent ASIN, Title, SKU, Page Views, Buy Box Percentage, Unit Session Percentage.

## Normalized models

`SearchTermPerformanceRow` and `BusinessPerformanceRow`. Application code never uses raw Amazon column names after parsing.

Money is `Decimal` (INR). Percentages are stored as **fractions** (`0.145` = 14.5%). The UI formats fractions as percents.

## PPC formulas (`ppc-analytics-v1`)

```text
CTR  = clicks / impressions
CPC  = spend / clicks
CVR  = orders / clicks
ACOS = spend / sales
ROAS = sales / spend
```

A zero denominator yields `null`, not `0`. Missing inputs are not invented.

Aggregations: overall, by search term, by campaign.

## Wasted-spend and related heuristics

These are **V1 heuristics**, not profitability evidence. Defaults are configurable.

| Rule | Default | Meaning |
|------|---------|---------|
| Zero-order wasted spend | `PPC_WASTED_SPEND_MIN=500` | orders = 0 AND spend ≥ ₹500 → `ZERO_ORDER_SPEND` (HIGH). Review as a **negative-keyword candidate**. Nothing is applied automatically. |
| High observed ACOS | `PPC_HIGH_ACOS=0.50` | ACOS ≥ 50% AND spend ≥ ₹500 → `HIGH_ACOS` (MEDIUM). **Not** labeled unprofitable. |
| Low conversion | `PPC_LOW_CVR=0.05`, min clicks 10 | CVR < 5% with enough clicks. One-click terms are not flagged. |
| Strong observed search-term performance | min clicks 10, CVR ≥ 10%, orders > 0 | Not called “winning keywords”. |

## Business-report heuristics (`business-analytics-v1`)

| Rule | Default |
|------|---------|
| High traffic / low conversion | sessions ≥ 50 and conversion < 5% |
| Low Buy Box % | sessions ≥ 50 and Buy Box < 80% when the column exists |
| Low-volume ASINs | not flagged |

Conversion uses Unit Session Percentage when present; otherwise `units_ordered / sessions`.

## Partial rows

Malformed rows are skipped and counted in `invalid_rows` with warnings. If at least one valid row exists, analysis continues. If none exist, the request fails with 400.

## Privacy

Logs include report type, file size, valid/invalid row counts, parser version, latency, and success/failure.

Logs do **not** include file contents, row values, seller metrics, or filenames.

When `DATABASE_URL` is set, the original file (SHA-256, bytes) and analysis payload are stored via `ArtifactPersistenceService`. Seller Reports UI remains a live-upload workflow, not History.

## API

```text
POST /api/v1/reports/analyze
Content-Type: multipart/form-data
file: <csv or xlsx>
```

User-format problems return **400** with an explanatory `detail` (missing columns, unknown type, empty file, too large). Not 500.

## Frontend

**Seller Reports** in the top nav. Analyze ASIN is unchanged.

No AI button. No CSV/PDF download.

## Limitations

Amazon CSV exports that include thousands separators must quote those cells (standard CSV). Unquoted `₹4,800.00` is split on the comma. The parser itself accepts `₹4,800.00` once it is a single cell.
- Column names still vary; unknown aliases may be reported as missing columns.
- Search Term and Business Reports are **not joined**.
- Parent/variation collapsing is not applied beyond exact ASIN aggregation.
- Campaign budget is not in these exports, so budget efficiency is not inferred.

## Future

AI interpretation can sit on top of this structured analysis (explicit click), the same way listing AI sits on Listing Intelligence.

Ads API / SP-API should feed the same normalized rows and analytics services, replacing the upload step—not the formulas.

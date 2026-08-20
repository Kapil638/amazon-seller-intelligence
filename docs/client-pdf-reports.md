# Client PDF reports

Client-ready A4 PDFs for saved historical analyses. Intended for Amazon sellers, consultants, and account managers. The PDF is a **report**, not a website printout or a JSON dump.

## Purpose

Export an immutable saved analysis so it can be emailed without reopening the app. Opening, generating, or downloading a PDF never refreshes Amazon or AI data.

Current template: **`analysis-report-v2`**. Legacy artifacts remain valid as **`analysis-report-v1`**.

## Historical-data-only rule

PDF generation reads only persisted rows:

- `product_snapshots`
- `listing_analysis_results` (Listing Intelligence V2)
- optional `scoring_profile_snapshot` on that listing result
- optional `ai_listing_results` (AI Strategy V2)
- optional `image_intelligence_results`
- `analysis_runs` metadata

**Rainforest calls: 0. OpenAI calls: 0.** Expired in-memory cache, process restart, or unavailable providers must not block export. Missing optional AI/image sections are omitted, not regenerated.

Presentation V2 may format, group, label, reorder, calculate display-only character counts, and convert identifiers into friendly labels. It must not generate new recommendations, rewrite persisted AI text, change scores or findings, infer missing Amazon data, or call providers.

## Architecture

```text
AnalysisHistoryService.get_report()
        ↓
ClientReportService
        ↓
ClientReportViewModel   (presentation layer)
        ↓
PdfReportRenderer
        ↓
PDF bytes
        ↓
ArtifactPersistenceService → private generated-reports bucket
```

Routes do not format pages. They authenticate tenant scope, then delegate.

| Layer | Responsibility |
| --- | --- |
| `AnalysisHistoryService` | Load the historical report for `current_organization_id()` |
| `ClientReportService` | Reuse or generate; never call providers |
| `ClientReportViewModel` | Friendly labels, currency/dates, grouping, missing-value display, safe text |
| `PdfReportRenderer` | ReportLab layout only |
| `ArtifactPersistenceService` | Store/retrieve bytes + `generated_reports` metadata |

`ClientAnalysisReport` remains as a compatibility wrapper. Generation uses `build_client_report_view()`.

## Presentation view model

`app/reports/view_model.py` maps persisted models into layout-ready structures:

- `cover` — brand, deterministic display title, ASIN, marketplace, dates, optional image bytes
- `executive_summary` fields — overall score/status, What to Fix First, AI paragraphs
- `scores` — five section cards with labels and status text
- `market_signals` — formatted KPI values and BSR rows
- `coverage` — group percentages plus human-readable evidence labels
- `findings` — grouped by high / medium / low / information
- `ai_strategy` — modules, specification groups, confidence notes
- `action_plan` / `seller_plan`
- `suggested_copy` — persisted rewrite text plus local character count
- `image_intelligence` — only when saved
- `metadata` — subdued final-page facts

## Design hierarchy (`analysis-report-v2`)

Narrative order (page numbers flex with content length):

1. Cover
2. Contents
3. Executive Overview — hero score, five section cards, What to Fix First, AI Executive Assessment
4. Listing Quality deep dive (vector score bars)
5. Market Signals KPI cards and Data Coverage
6. What We Found and Recommended Action Plan
7. AI Content & SEO Strategy (when saved)
8. Specification Coverage (when saved)
9. Seller Action Plan (when saved)
10. Suggested Listing Copy (when saved)
11. Image & Media Intelligence (when saved)
12. Report Information and subdued Disclaimer

Approximate typography: cover eyebrow 9–10 pt, cover title 28 pt, product title 16 pt, section 18 pt, subsection 12.5 pt, body 10 pt, metadata 8.5 pt, footer 7.5 pt.

Palette constants live in `app/reports/pdf_widgets.py`: navy/charcoal (`PRIMARY_DARK`), teal accent (`ACCENT`), semantic success/warning/attention. Status always includes printed text, not color alone.

## Friendly label mapping

`app/reports/labels.py` converts persisted identifiers for display only. Database values are unchanged.

| Persisted key | Client label |
| --- | --- |
| `title` | Title Optimization |
| `bullets` | Bullet Content & SEO Readiness |
| `description_a_plus` | Description & A+ Content |
| `media_coverage` | Media Coverage |
| `content_structure` | Content Structure & Readability |
| `a_plus` | A+ Content |
| `brand_story` | Brand Story |
| `bsr_ranks` | Best Seller Rank |
| `review_count` | Review Count |

Unknown `snake_case` keys title-case on spaces. Raw field names must not appear in the PDF.

Cover display titles are derived deterministically from brand + remaining product identity. The full persisted title still appears under Product Details. No AI is used.

## Currency and date formatting

`app/reports/formatting.py`:

- `amazon.in` / INR amounts print as `₹2,599` (or `Rs.2,599` if the embedded font lacks rupee support)
- Dates use `20 Aug 2026` on the cover and a longer timestamp in metadata
- Integers use thousands separators
- Missing values print `Not available`

## Unicode and fonts

`app/reports/fonts.py` embeds a Unicode TTF when one is present on the host (DejaVu, Liberation, Noto, or macOS Arial Unicode). Helvetica is the fallback. Text is normalized for hyphen/quote variants (`Wi-Fi`, en/em dashes, apostrophes). Font files are not vendored in the repo.

Score bars are vector rectangles, not Unicode block characters.

## Cover images

Optional. URLs must pass the Milestone 8D media allowlist (`MediaUrlValidator`). Fetch uses timeout, size limit, and content-type checks. Failures omit the image; PDF generation still succeeds.

## Renderer choice

**ReportLab** (pure Python Platypus), A4.

Long callouts, suggested copy, and specification lists are flowable paragraphs so they can split across pages. Compact cards (score, KPI, action-plan header) stay `KeepTogether`.

## Template versioning and reuse

`REPORT_TEMPLATE_VERSION = "analysis-report-v2"`.  
`LEGACY_TEMPLATE_VERSION = "analysis-report-v1"`.

An artifact is unique per `report_id` + `template_version` + type `analysis_pdf`. Requesting PDF for a report that only has v1 **generates v2**. v1 is not deleted and is not treated as a v2 hit.

Filename pattern (sanitized ASIN + analysis date):

`Amazon-Listing-Analysis-{ASIN}-{YYYY-MM-DD}.pdf`

## Storage

Private bucket `generated-reports`. No public URLs. FastAPI streams `application/pdf` after verifying organization ownership. Path shape:

`{organization_id}/analysis-pdf/{report_id}/{template_version}/{filename}`

## Tenant security

Every generate/download call uses `current_organization_id()`. Unknown, other-organization, and soft-deleted reports return **404**.

## APIs

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/v1/reports/{report_id}/pdf` | Generate if missing for the current template; otherwise reuse |
| GET | `/api/v1/reports/{report_id}/pdf` | Download existing PDF (`404` if never generated) |

History does not generate PDFs on list load. Generation is user-initiated.

## Visual QA

Fixture PDFs (full AI, AI + image intelligence, deterministic-only, sparse, long content) are generated in tests. Checks include PDF signature, page count, no raw snake_case labels, incremented numbering (`01` / `02`), Unicode hyphens, INR formatting, missing-image fallback, and zero provider calls.

## Limitations

- Soft-deleted reports cannot be exported through these APIs
- No email, scheduled export, share links, white-label logos, or custom templates
- Cover image is best-effort and allowlisted only
- GET does not generate on demand; the UI POSTs first
- Contents is a static list, not clickable PDF bookmarks

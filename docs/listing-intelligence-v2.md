# Listing Intelligence V2 (deterministic)

**Score version:** `listing-score-v2` / `v2`  
**Engine:** deterministic. No OpenAI. No extra Rainforest calls.

V1 (`listing-score-v1`) is unchanged at `POST /api/v1/analysis/listing`.  
V2 is `POST /api/v1/analysis/listing/v2`.

AI listing recommendations still consume **V1** analysis. That is Milestone 8C.

---

## Purpose

Answer: **how well is this Amazon listing constructed, given the evidence we actually have?**

Do **not** answer: how well will this product sell?

V2 splits three concepts V1 mixed:

| Concept | In listing-quality score? |
|---------|---------------------------|
| Listing quality (seller-controlled copy and media coverage) | Yes |
| Market signals (rating, reviews, BSR, price, availability, seller, recent sales) | No — reported separately |
| Data coverage (how much evidence the payload contained) | No — reported separately |

These bands are **internal heuristics**, not Amazon performance grades: 85+ excellent, 70–84 good, 50–69 fair, &lt;50 poor.

---

## Architecture

```text
POST /api/v1/analysis/listing        → ListingAnalysisService        → listing-score-v1
POST /api/v1/analysis/listing/v2     → ListingAnalysisV2Service      → listing-score-v2
POST /api/v1/analysis/listing/ai     → AIListingIntelligenceService  → uses V1 analysis only
```

Rules live in `apps/api/app/analytics/listing_rules_v2.py`.  
Models live in `apps/api/app/models/listing_analysis_v2.py`.

---

## Listing quality formula

```text
listing_quality_score =
    0.20 × title
  + 0.25 × bullets
  + 0.20 × description_a_plus
  + 0.20 × media_coverage
  + 0.15 × content_structure
```

Rounded and clamped to 0–100.

**Not included:** rating, review count, BSR, price, availability, seller, FBA, recent sales, rating breakdown.

---

## Section rules

All deductions start from 100 unless the section has no content (then a low floor). Scores are structural. They do not claim conversion, search volume, or photography quality.

### Title optimization — 20%

Measures presence, character/word counts, caps, punctuation, repeated significant terms, stuffing (same significant term 3+ times).

Length bands are scorer heuristics, **not** “Amazon requires 80–180 characters.”

Codes: `TITLE_MISSING`, `TITLE_TOO_SHORT`, `TITLE_EXCESSIVELY_LONG`, `TITLE_REPETITION`, `TITLE_CAPS_HEAVY`, `TITLE_PUNCTUATION_HEAVY`, `TITLE_POSSIBLE_STUFFING`.

### Bullet content & SEO readiness — 25%

“SEO readiness” means: do bullets cover terminology **already present** in title, category, brand, specifications, attributes, and variation labels?

It does **not** mean keyword volume, rank, SQP, or PPC.

Also measures count, length, exact duplicates, high Jaccard overlap, caps, punctuation, spec gaps, possible stuffing (same term 4+ times).

Codes: `NO_BULLETS`, `LOW_BULLET_COVERAGE`, `BULLET_DUPLICATION`, `BULLET_REPETITION`, `BULLET_CAPS_HEAVY`, `BULLET_PUNCTUATION_HEAVY`, `SPECIFICATION_COVERAGE_GAP`, `PRODUCT_TERM_COVERAGE_GAP`, `POSSIBLE_BULLET_STUFFING`.

### Description & A+ content — 20%

Distinguishes standard description, A+ flag/text/images, and Brand Story.

- Thin description **with** substantial A+ is not heavily punished.
- Substantial description **without** A+ can still score reasonably; reported-absent A+ is an opportunity (INFO), not a market-failure penalty.
- `a_plus is null` → **UNKNOWN**, not `A_PLUS_NOT_PRESENT`.
- `has_a_plus_content=true` is **presence**, not quality. A flag-only object does not award a near-perfect section score.

Codes include `DESCRIPTION_MISSING`, `DESCRIPTION_THIN`, `A_PLUS_NOT_PRESENT`, `A_PLUS_PRESENT`, `A_PLUS_TEXT_AVAILABLE`, `A_PLUS_UNKNOWN`, `BRAND_STORY_PRESENT`, `A_PLUS_MEDIA_PRESENT`.

### Media coverage — 20%

Renamed from “Images” because V2 cannot judge image quality (no vision, no downloads).

Gallery count is coverage only. Seven-plus URLs score **78** before bonuses, not 95.

Video:

- `videos[]` present → `VIDEO_PRESENT`
- `videos_count > 0` and empty `videos[]` → `VIDEO_REPORTED_DETAILS_UNAVAILABLE` (not “no video”)
- both missing → UNKNOWN; not a defect

A+ / Brand Story images stay separate from the gallery.

Codes: `MAIN_IMAGE_MISSING`, `LIMITED_GALLERY`, `DUPLICATE_MEDIA`, `VIDEO_PRESENT`, `VIDEO_REPORTED_DETAILS_UNAVAILABLE`, `A_PLUS_MEDIA_PRESENT`, `BRAND_STORY_MEDIA_PRESENT`, `MEDIA_DIMENSIONS_UNKNOWN`.

### Content structure & readability — 15%

Cross-field duplicate copy, heavy caps/punctuation across fields, fragmented bullets, structured specs missing from **all** seller-facing copy, extreme term repetition.

No grammar, sentiment, or conversion claims.

---

## Evidence states

`observed` | `reported_absent` | `unknown`

Examples:

| Situation | State |
|-----------|--------|
| `a_plus.has_a_plus_content=true` | observed present |
| `a_plus.has_a_plus_content=false` | reported_absent |
| `a_plus is null` | unknown |
| `videos[]` nonempty | observed |
| `videos_count > 0`, `videos=[]` | observed (reported presence) |
| `is_sold_by_amazon=true`, `seller=null` | seller coverage **observed** (Amazon-sold) |

Unknown is not converted into a negative listing-quality penalty unless the rule has enough evidence (for example a missing title on the Product object).

---

## Market signals

Factual snapshot: rating, review_count, price, availability, availability_type, is_sold_by_amazon, seller, **bsr_ranks** (all rows; not only `Product.bsr`), recent_sales_text, rating_breakdown.

No combined “market score.”

---

## Data coverage

Answers: how much evidence was available?

Groups: `core_listing_content`, `media`, `enhanced_content`, `category_context`, `market_signals`.

Each field has an evidence state. `available` is true only for `observed`. Overall percentage is available/expected across groups. This is **not** listing quality.

Omitted specifications or A+ objects are **unknown**, not confirmed empty.

---

## Why V1 concepts changed

- **Social proof was removed from listing quality** because review volume and rating are marketplace outcomes, not copy construction.
- **Completeness became data coverage** because missing provider fields (Amazon-sold seller, omitted A+) are not the same as a poorly built listing.
- **Images became media coverage** because URL count is not photography quality.
- **SEO is called SEO readiness** because this app has no keyword volume or rank data.

---

## What V2 cannot know

Search volume, organic rank, conversion, CTR, image quality, A+ module quality, full review sentiment, offer depth, true sales, or Amazon policy compliance.

---

## Credits

V2 analyzes the Product already in the request body. **0 additional Rainforest calls. 0 OpenAI calls.**

---

## Frontend

Analyze listing quality loads V2 as the primary view and still loads V1 in a collapsed legacy panel. Primary **Generate AI strategy** uses V2. V1 AI remains a legacy path.

See [ai-listing-intelligence-v2.md](ai-listing-intelligence-v2.md).

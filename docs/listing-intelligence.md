# Listing Intelligence (score version v1)

This analysis is **deterministic and does not currently use AI**.

Scoring is implemented in `apps/api/app/analytics/listing_rules.py` and called through `ListingAnalysisService`. The same function can be used by future jobs or MCP tools.

Thresholds below are **internal heuristics**. They are not Amazon policy requirements.

## Weights

| Section | Weight |
|---------|--------|
| Title | 20% |
| Bullets | 25% |
| Description | 15% |
| Images | 15% |
| Completeness | 15% |
| Social proof | 10% |

Overall score is a weighted average, rounded, and clamped to 0–100.

Section status bands:

- 85–100 excellent
- 70–84 good
- 50–69 fair
- 0–49 poor

## Title

- Missing/blank title → score 0, `TITLE_MISSING`
- Preferred length: 80–180 characters
- Unusually short: under 40 characters
- Unusually long: over 200 characters
- Few words: under 4
- Many words: over 30
- Excessive capitalization: 40%+ of alphabetic tokens are ALL CAPS, or the whole title is uppercase
- Repeated significant words and 3+ repeats of the same keyword are flagged

Wording stays neutral: “Title is unusually long”, not “Amazon requires…”.

## Bullets

- None → score 0, `NO_BULLETS`
- Typical set used by this scorer: 5 bullets
- Unusually short: under 20 characters
- Unusually long: over 250 characters
- Exact duplicate bullets (case/whitespace-normalized)
- Optional heuristics: benefit-oriented opening word, excessive caps, excessive `!` / `?`

## Description

- Missing → score 0, `NO_DESCRIPTION`
- Unusually short: under 80 characters
- Preferred: 250–2000 characters
- Unusually long: over 3000 characters

No semantic or AI analysis.

## Images

Uses `Product.images` URLs only.

- Count, presence, duplicate URLs
- Does **not** download images
- Does **not** evaluate visual quality

## Completeness

Checks whether these fields are populated:

title, brand, price, rating, review count, bullets, description, images, category, BSR, availability, seller

This is **available data completeness**, not listing-copy quality. Gaps are low/info severity because future providers may omit fields.

## Social proof

Uses rating and review count when present.

- Missing both → neutral 50, `SOCIAL_PROOF_UNAVAILABLE` (not treated as a bad listing)
- High rating + substantial review count → strong descriptive band
- Low rating / no reviews → weaker band

Language is descriptive only, for example: “Strong visible social proof relative to a new/unreviewed listing.” It does not claim conversion impact.

## Findings and recommendations

Findings use stable codes (`NO_BULLETS`, `TITLE_MISSING`, …) and severities `high` | `medium` | `low` | `info`.

Recommendations are canned deterministic actions mapped from those codes. No rewritten listing copy is generated.

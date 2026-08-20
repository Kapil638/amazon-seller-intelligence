# Custom Scoring Profiles (Milestone 10B)

Custom scoring profiles let a workspace change **only** how Listing Intelligence V2 section scores are combined into an optional **Custom Listing Quality Score**.

They do **not** train a model, fine-tune AI, or change Amazon’s ranking. The deterministic V2 engine is unchanged.

Completion record: [custom-scoring-profiles-report.md](custom-scoring-profiles-report.md).

## Purpose

Sellers often care more about media, or more about copy. A scoring profile is a named set of **Custom Weights** for the five listing-quality sections. The **Standard Listing Quality Score** remains the universal benchmark so reports stay comparable across users and organizations.

## Standard V2 weights

System profile: `standard-v2` / **Standard V2**. Not editable. Not deletable. Not stored as a database row.

| Section | Weight |
|---------|--------|
| Title Optimization | 20 |
| Bullet Content & SEO Readiness | 25 |
| Description & A+ Content | 20 |
| Media Coverage | 20 |
| Content Structure & Readability | 15 |
| **Total** | **100** |

Every V2 analysis still computes `listing_quality_score` with these weights (`score_version` remains `v2` / listing-score-v2). A custom profile is a `scoring_profile`, not listing-score-v3.

## Custom profile behavior

`custom_listing_quality_score` =

`title_score × title_weight/100` + `bullet_score × bullets_weight/100` + … then round and clamp 0–100, matching V2 aggregation.

Section scores, findings, recommendations, Market Signals, and Data Coverage do not change.

If the organization has a **default custom profile**, a new analysis returns Standard **and** Custom. If none, Standard only. The client may send `scoring_profile_id` (`standard-v2` or a custom UUID). Passing `standard-v2` skips the org default.

## Why Standard remains visible

Custom weights are a seller preference, not a more correct score. History, competitor comparison, and cross-org comparison use Standard V2. The UI always shows Standard Listing Quality Score when a custom score is present.

## What is customizable

Only the five top-level aggregate weights. Not title length rules, bullet heuristics, SEO-readiness, A+ logic, media penalties, evidence states, Market Signals, or Data Coverage.

## Market Signals and Data Coverage

These stay separate objects. Profiles cannot assign weight to rating, review count, BSR, price, availability, seller, recent sales, or coverage percentages.

## Historical weight snapshot

When a report is saved with a custom profile, `listing_analysis_results` stores:

- `custom_listing_quality_score`
- `scoring_profile_snapshot` (profile id, name, type `custom`, weights, and the custom score)

Editing or archiving the live profile later does **not** rewrite old reports. Opening history does not recalculate.

`POST /api/v1/analysis/listing/v2/reweight` can preview another profile (`persist: false` by default). Preview must not mutate the stored snapshot. The Analyze UI may persist a reweight onto the **current** report after an explicit profile selection; History does not offer that.

## Organization scoping

Custom profiles belong to `current_organization_id()`. There are no global custom profiles. Standard V2 is a code constant available to every organization.

One **active** custom profile may be `is_default` per organization (enforced in the service layer, not a PostgreSQL partial unique index, so SQLite tests stay simple). Standard V2 is never that default; it is the system benchmark.

Active profile names are unique per organization (case-insensitive). Archived names may be reused.

## API endpoints

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/v1/scoring-profiles` | Includes Standard V2 first. `?include_archived=true` for archived custom rows. |
| POST | `/api/v1/scoring-profiles` | Server validates each weight 0–100 and **total exactly 100**. No silent normalize. Zero is allowed. |
| GET | `/api/v1/scoring-profiles/{id}` | `standard-v2` or UUID |
| PATCH | `/api/v1/scoring-profiles/{id}` | Custom only. Standard V2 → 403 |
| DELETE | `/api/v1/scoring-profiles/{id}` | Soft archive. Standard V2 → 403 |
| POST | `/api/v1/analysis/listing/v2` | Optional `scoring_profile_id`. Existing `{product, source}` bodies still work. |
| POST | `/api/v1/analysis/listing/v2/reweight` | `report_id` preferred, or existing `analysis`. 0 provider calls. |

## Persistence

Migration `0002_scoring_profiles` (do not edit `0001_m10_persistence`).

- Table `scoring_profiles`
- Columns on `listing_analysis_results`: `custom_listing_quality_score`, `scoring_profile_snapshot`

No Storage buckets. No new Rainforest or OpenAI calls. Aggregation is synchronous.

## Competitor comparison

Competitor intelligence continues to use **Standard** listing scores (currently the V1 listing engine). Custom profile comparison is **not** implemented in this milestone. Do not mix custom aggregates into competitor tables.

## AI and Image intelligence

AI Listing Intelligence V2 and Image & Media Intelligence still receive Standard V2 deterministic analysis. Changing weights does not rerun OpenAI, change prompts, or change Media Coverage section scores.

## Limitations

- No custom internal thresholds or section rule editing
- No AI-optimized weights, presets claimed as Amazon-recommended, or per-category auto profiles
- No authentication (organization foundation only)
- Archived profiles are hidden from the new-analysis selector
- Historical preview-with-another-profile is API-only (`persist: false`); History UI shows the original snapshot
- One-default-per-org is application-enforced

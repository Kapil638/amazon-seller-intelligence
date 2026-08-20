# MILESTONE 10B — CUSTOM SCORING PROFILES REPORT

**Date:** 20 August 2026  
**Status:** Complete  
**Auth / SP-API / Ads / Redis / Celery / Reviews / Offers / image generation / custom thresholds / AI weight optimization:** not started

This is the Milestone 10B completion record. Product behavior lives in [custom-scoring-profiles.md](custom-scoring-profiles.md). Schema and ER diagram live in [database-schema.md](database-schema.md).

---

## Standard profile

- name: **Standard V2** (`standard-v2`)
- weights: Title 20 / Bullets 25 / Description & A+ 20 / Media 20 / Structure 15
- editable: **no**
- deletable: **no**
- storage: code constant, not a `scoring_profiles` row

## Database

- migration: `0002_scoring_profiles` (does not edit `0001_m10_persistence`)
- table: `scoring_profiles`
- extra listing-result columns: `custom_listing_quality_score`, `scoring_profile_snapshot`
- organization scoped: **yes** (`current_organization_id()`)
- default profile behavior: one active custom `is_default` per organization, enforced in the service layer (SQLite-friendly; no PostgreSQL partial unique index)
- Standard V2 is never replaced by the organization default

## Custom scoring

- section scores modified: **no**
- Standard score preserved: **yes** (`listing_quality_score` is always Standard V2)
- Custom score formula:

`custom_listing_quality_score` = round(clamp(  
`title_score × title_weight/100`  
+ `bullet_score × bullets_weight/100`  
+ `description_a_plus_score × description_a_plus_weight/100`  
+ `media_score × media_weight/100`  
+ `content_structure_score × content_structure_weight/100`  
))

Rounding matches Listing Intelligence V2 (`int(round(...))`, clamp 0–100).

- total validation: each weight 0–100; **total must equal 100**; no silent normalize
- zero weights allowed: **yes** (UI warns that the section will not affect the custom score)
- negatives and weights over 100: rejected
- `score_version` remains `v2` (not listing-score-v3)

If the organization has a default custom profile and no `scoring_profile_id` is sent, the analysis returns Standard **and** Custom. `scoring_profile_id=standard-v2` returns Standard only.

## History

- weight snapshot persisted: **yes**
- custom score persisted: **yes**
- profile edits mutate historical reports: **no**
- profile archive breaks history: **no** (soft archive; snapshots remain)

`POST /api/v1/analysis/listing/v2/reweight` can preview another profile (`persist` defaults to false). History UI does not rewrite stored custom scores.

## API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/api/v1/scoring-profiles` | Standard V2 first. `?include_archived=true` for archived custom rows |
| POST | `/api/v1/scoring-profiles` | Server validates weights |
| GET | `/api/v1/scoring-profiles/{id}` | `standard-v2` or UUID |
| PATCH | `/api/v1/scoring-profiles/{id}` | Custom only. Standard V2 → 403 |
| DELETE | `/api/v1/scoring-profiles/{id}` | Soft archive. Standard V2 → 403 |
| POST | `/api/v1/analysis/listing/v2` | Optional `scoring_profile_id`. Existing `{product, source}` bodies still work |
| POST | `/api/v1/analysis/listing/v2/reweight` | `report_id` preferred, or existing `analysis`. 0 provider calls |

## Frontend

- profile selector: Analyze listing quality (before first run) and Listing Intelligence header
- customization UI: numeric weight editor, live total, zero-weight warnings, save, manage (rename / edit / set default / archive)
- Standard + Custom display: both shown; Standard is never hidden
- history display: list shows `Standard N` and optional `Custom N · Profile`; detail shows the historical weight snapshot
- copy: “Custom profiles change how section scores are weighted. They do not change the underlying analysis.”

## Cost

- Rainforest calls: **0**
- OpenAI calls: **0**

Creating, editing, selecting, or reweighting a profile is a pure deterministic operation.

## Compatibility

- V1 changed: **no**
- V2 internal rules changed: **no**
- AI V2 changed: **no**
- Image AI changed: **no**
- Market Signals changed: **no**
- Data Coverage changed: **no**
- Competitor comparison: still Standard scores (V1 listing engine). Custom competitor comparison was deferred.

## Tests

- new tests: 24 in `apps/api/tests/test_scoring_profiles.py`
- total backend tests: **346 passed**
- result: pass
- live Rainforest during tests: **NO**
- live OpenAI during tests: **NO**

## Frontend build

- result: **pass** (`npm run build`)

## Supabase

- 0002 applied: **yes** (`0002_scoring_profiles` is head)
- `scoring_profiles` verified: **yes**
- smoke profile: fictional **Media First Test** (15 / 20 / 15 / 40 / 10) created, read, updated, archived
- archived smoke row remains in the real database and is hidden from the active selector
- no Rainforest or OpenAI calls during smoke

## Known limitations

- Only top-level aggregate weights are customizable.
- No custom title/bullet/A+/media thresholds.
- No AI-optimized weights or Amazon-recommended presets.
- Competitor comparison does not use custom scores.
- AI V2 prompt context is unchanged.
- History UI does not offer live reweight; API preview uses `persist: false`.
- One-default-per-org is application-enforced.
- Authentication is not implemented. Tenant boundary is still the default organization.
- Re-analysis of current listings is still future and must not mutate old reports.

## Files added

- `apps/api/app/analytics/scoring_profiles.py`
- `apps/api/app/api/routes/scoring_profiles.py`
- `apps/api/app/models/scoring_profile.py`
- `apps/api/app/services/scoring_profile_service.py`
- `apps/api/migrations/versions/0002_scoring_profiles.py`
- `apps/api/tests/test_scoring_profiles.py`
- `apps/web/src/components/scoring-profile-controls.tsx`
- `docs/custom-scoring-profiles.md`
- `docs/custom-scoring-profiles-report.md`

## Files modified

- `apps/api/app/api/routes/analysis.py`
- `apps/api/app/api/routes/__init__.py`
- `apps/api/app/main.py`
- `apps/api/app/core/exceptions.py`
- `apps/api/app/models/listing_analysis_v2.py`
- `apps/api/app/models/saved_analysis.py`
- `apps/api/app/persistence/models.py`
- `apps/api/app/persistence/repositories.py`
- `apps/api/app/services/analysis_history_service.py`
- `apps/web/src/components/listing-intelligence-v2.tsx`
- `apps/web/src/components/product-lookup.tsx`
- `apps/web/src/components/historical-analysis.tsx`
- `apps/web/src/components/analysis-history.tsx`
- `apps/web/src/lib/api.ts`
- `apps/web/src/lib/types.ts`
- `README.md`
- `docs/changes.md`
- `docs/database-schema.md`
- `docs/listing-intelligence-v2.md`

The working tree also still contains uncommitted Milestone 10 / 10A persistence files. Nothing was committed or pushed for 10B.

## git diff --stat

Recorded at Milestone 10B completion (untracked files listed separately under Files added). The tracked diff also includes earlier uncommitted Milestone 10 / 10A work:

```text
 README.md                                          |  60 +-
 apps/api/.env.example                              |  10 +
 apps/api/app/api/routes/__init__.py                |   2 +
 apps/api/app/api/routes/analysis.py                | 143 +++-
 apps/api/app/api/routes/bulk.py                    |  18 +-
 apps/api/app/api/routes/reports.py                 |  66 +-
 apps/api/app/bulk/jobs.py                          |  18 +
 apps/api/app/core/config.py                        |  11 +
 apps/api/app/core/exceptions.py                    |  46 +-
 apps/api/app/main.py                               |  12 +-
 apps/api/app/models/ai_image_intelligence.py       |   4 +
 apps/api/app/models/ai_listing_intelligence_v2.py  |   4 +
 apps/api/app/models/listing_analysis_v2.py         |  30 +
 apps/api/app/reports/file_loader.py                |   2 +-
 apps/api/app/usage/ledger.py                       |  32 +-
 apps/api/pyproject.toml                            |   6 +-
 apps/api/tests/conftest.py                         |  16 +
 apps/api/tests/report_helpers.py                   |  16 +
 apps/api/tests/test_products.py                    |   2 +-
 apps/api/tests/test_report_upload.py               |  23 +-
 apps/api/uv.lock                                   | 946 +++++++++++++++++++--
 apps/web/src/components/app-shell.tsx              |   5 +-
 apps/web/src/components/listing-intelligence-v2.tsx |  49 +-
 apps/web/src/components/product-lookup.tsx         |  87 +-
 apps/web/src/lib/api.ts                            | 194 ++++-
 apps/web/src/lib/types.ts                          | 119 +++
 docs/changes.md                                    |  43 +-
 docs/listing-intelligence-v2.md                    |   2 +
 28 files changed, 1817 insertions(+), 149 deletions(-)
```

## git status

On `main`, uncommitted. `0001_m10_persistence` is not in the diff.

STOP AFTER MILESTONE 10B.

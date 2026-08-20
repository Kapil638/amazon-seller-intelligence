# Report lifecycle

Saved analyses are historical snapshots. Milestone 10C adds **soft delete** so a report can leave History without destroying the underlying record.

## Soft deletion

`analysis_runs.deleted_at` is a nullable timestamp.

| State | `deleted_at` |
| --- | --- |
| Visible in History | `NULL` |
| Removed from History | deletion time (UTC) |

Delete is **not** a cascade. These rows stay in PostgreSQL:

- `product_snapshots`
- `listing_analysis_results`
- `ai_listing_results`
- `image_intelligence_results`
- scoring profile snapshots on the listing result
- `generated_reports` metadata and Storage objects

There is **no hard-delete API** in this milestone.

## API behavior

`DELETE /api/v1/reports/{report_id}`

- Scoped with `current_organization_id()`
- Sets `deleted_at` only
- Returns `{ "report_id": "...", "deleted": true }`
- Unknown id, other organization's report, or already-deleted report → **404** (existence is not leaked)
- Zero Rainforest calls, zero OpenAI calls

`GET /api/v1/reports` excludes `deleted_at IS NOT NULL`.

`GET /api/v1/reports/{report_id}` returns **404** for deleted reports.

PDF generate/download endpoints use the same visibility rules. There is no `include_deleted` query parameter and no admin recycle-bin API yet.

## Future Restore / Recycle Bin

`deleted_at` is the foundation for a later Restore flow: clear the timestamp and the report reappears in History. Do not build Recycle Bin UI, restore, bulk delete, or retention jobs until that milestone.

## Policy

Do not permanently delete historical analyses until an explicit hard-delete/retention policy exists. Soft delete is the only supported removal path today.

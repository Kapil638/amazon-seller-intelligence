# 12B.3G — Durable Listings Synchronization

Replaces 12B.3F's synchronous "Sync listings" HTTP trigger (which ran the
entire Amazon ingestion pass inside one browser request) with a durable,
PostgreSQL-backed job queue. This is an architectural correctness fix, not
a UI-speed optimization: the old design did not survive a larger seller's
catalog, Amazon throttling, browser closure, or ordinary hosting request
timeouts.

No new infrastructure was introduced. The existing PostgreSQL database is
the durable queue, using a widened partial unique index for the
single-writer guarantee and `SELECT ... FOR UPDATE SKIP LOCKED` for
worker claim concurrency — no Redis, no Celery, no managed queue.

## State-transition diagram

```
                 enqueue_listings_run()
                          |
                          v
                      +--------+
        +------------>| queued | <- no lease, no age-based expiration;
        |             +--------+    stays here indefinitely until claimed
        |                 |
        |     claim_next_listings_job()
        |     (worker, SKIP LOCKED)
        |                 v
        |            +---------+
        |   +------->| started |<------------------+
        |   |        +---------+                    |
        |   |         |   |   |                     |
        |   | success |   |   | retryable failure    | claim_next_listings_job()
        |   | (reconcile)|   | (reschedule_listings_  | reclaims when
        |   |         |   |   run_for_retry, due)     | next_retry_at has passed
        |   |         |   |   v                       |
        |   |         |   | +-----------------+       |
        |   |         |   | | waiting_to_retry|-------+
        |   |         |   | +-----------------+
        |   |         |   |   |
        |   |         |   |   | retry budget exhausted
        |   |         |   |   v
        |   |         |   | +--------+
        |   |         |   | | failed | (failure_class="rate_limited")
        |   |         |   | +--------+
        |   |         |   |
        |   |         |   | non-retryable failure (data anomaly,
        |   |         |   | auth/config/request-shape problem)
        |   |         |   v
        |   |         | +--------+
        |   |         | | failed |
        |   |         | +--------+
        |   |         v
        |   |    +-----------+
        |   |    | timed_out | (lease expired, no worker ever finished it —
        |   |    +-----------+  reclaimed by a later claim_next_listings_job(),
        |   |                   scope freed for a fresh enqueue too)
        |   v
        | +-----------+
        +-| succeeded |  (partial reconciliation currently unused — no
          +-----------+   code path produces `partial` for Listings;
                           it remains a valid terminal status because the
                           shared CHECK constraint is not Listings-specific)
```

`cancelled` is deliberately not a state: nothing in this codebase ever
produces a transition into it. Every state above has at least one
defined, code-driven transition in or out.

`queued_at` is `AmazonIngestionRun.created_at` (row-creation time already
*is* "when queued" — no separate column needed). `started_at` is set only
on the *first* claim and is preserved across every subsequent retry
reclaim. `retry_count` (pre-existing, previously unused column) is reused
as the attempt counter, incremented only when reclaiming from
`waiting_to_retry` — never on a fresh `queued` claim.

## API vs worker responsibilities

| Process | Responsibility |
| --- | --- |
| API (FastAPI) | `POST .../listings/sync` validates ownership/eligibility and enqueues a durable job, or reports the existing one — never touches Amazon, never blocks. `GET .../listings/sync/{run_id}` reports sanitized job progress. |
| Worker (`app.amazon.listings_worker`) | A separate, independently-hosted process. Claims eligible jobs, fetches Amazon pages, reconciles listings, renews its lease, and decides retry-vs-terminal on failure. |

**The API process never runs a worker loop.** Importing
`app.amazon.listings_worker` does not start anything — only its
`if __name__ == "__main__"` block does. A queued job sits at `queued`
forever if no worker process is running; the API gives no illusion
otherwise (`202 Accepted` means "durably queued", not "will run soon").

### Trigger endpoint

`POST /api/v1/amazon/marketplace-participations/{id}/listings/sync`

| Outcome | HTTP status |
| --- | --- |
| New job queued | `202` |
| An active job already exists for this participation | `409` |
| Participation missing/foreign | `404` |
| Scope inactive / identity missing / connection unresolvable | `503` |
| Cooldown / this organization's queue backlog is at its safety limit | `429` |

Response body: `{ reason, message, job }`. `job` (when present) is the
sanitized `ListingsSyncJobStatus` contract — run id, run type, status,
participation id, page/record counters, `reported_total_results`,
`pagination_complete`, `attempt_count`, `queued_at`/`started_at`/
`last_heartbeat_at`/`next_retry_at`/`completed_at`, and `failure_class`.
Never an organization id, seller id, connection id, lease owner,
credential, token reference, page token, raw Amazon response, or internal
exception text.

### Job-status endpoint

`GET /api/v1/amazon/marketplace-participations/{id}/listings/sync/{run_id}`

Independently re-validates organization ownership and requires the run to
be a `listings` run for that exact participation. A foreign, mismatched,
or nonexistent run is indistinguishable: all return `404`.

The frontend does **not** call this endpoint directly today — see
"Frontend polling strategy" below for why. It exists and is fully tested
at the API layer for any future direct-status consumer.

## Worker

### Start command

```
cd apps/api
uv run python -m app.amazon.listings_worker
```

Run one or more instances as their own long-lived process (systemd unit,
container, supervisor-managed process — whatever this environment's
existing hosting convention is; nothing here enforces a specific one).
Multiple instances may run concurrently against the same database with no
additional coordination — `claim_next_listings_job`'s `SELECT ... FOR
UPDATE SKIP LOCKED` claim is what makes that safe.

### Required environment / settings

All new settings are typed, validated `Field(...)`s on `Settings`
(`app/core/config.py`) — no hardcoded numbers:

| Setting | Default | Meaning |
| --- | --- | --- |
| `listings_sync_max_attempts` | 5 | Total attempts before a retryable failure becomes terminal `rate_limited`. |
| `listings_sync_base_backoff_seconds` | 30.0 | Base for exponential backoff with full jitter when Amazon gives no `Retry-After`. |
| `listings_sync_max_backoff_seconds` | 900.0 | Hard cap on any single computed retry delay — including a large `Retry-After` value. |
| `listings_sync_max_total_retry_seconds` | 3600.0 | Hard cap on total elapsed retry time from first attempt. |
| `listings_sync_lease_duration_seconds` | 300 | How long a worker's claim is valid before stale-lease recovery. |
| `listings_sync_heartbeat_interval_pages` | 1 | Progress-reporting cadence only (renew/report after N fetched pages) — NOT the lease-safety mechanism; see `listings_sync_heartbeat_time_interval_seconds` below. |
| `listings_sync_heartbeat_time_interval_seconds` | 60.0 | Wall-clock cadence at which the lease is renewed WHILE a single page fetch is in flight, independent of page completion — this is what actually guarantees a lease cannot expire mid-request. Must stay comfortably below `listings_sync_lease_duration_seconds`. |
| `listings_sync_max_global_concurrent_jobs` | 4 | Max simultaneous `started` Listings jobs across the whole worker fleet — enforced **only at claim time**, never at trigger/enqueue time. |
| `listings_sync_max_concurrent_jobs_per_organization` | 1 | Max simultaneous `started` Listings jobs for one organization — enforced **only at claim time**. |
| `listings_sync_trigger_cooldown_seconds` | 30 | Minimum time after a job's own creation before the trigger accepts another request for the same participation. |
| `listings_sync_max_queued_per_organization` | 25 | Queue-backlog safety valve: max `status='queued'` jobs one organization may have outstanding at once. The trigger's *only* capacity-related admission check. |

**Corrected design (12B.3G stabilization pass)**: an earlier version of
this trigger used `listings_sync_max_global_concurrent_jobs`/
`listings_sync_max_concurrent_jobs_per_organization` — counting
`queued`+`started`+`waiting_to_retry` together — to gate the trigger
itself. That was wrong: it conflated worker EXECUTION capacity with
queue ADMISSION, so a legitimate new job could be rejected outright just
because *other* jobs happened to be queued, even though no worker was
actually busy. The trigger's only capacity-related check now is
`listings_sync_max_queued_per_organization`, counting `status='queued'`
rows *only* (never `started`/`waiting_to_retry`, which already each hold
their own participation's single-writer slot and are not "backlog" in
the sense this bounds) — reason `queue_backlog_limit_reached`. Worker
execution capacity (`listings_sync_max_global_concurrent_jobs` /
`listings_sync_max_concurrent_jobs_per_organization`) is enforced
exactly once, at claim time, by `claim_next_listings_job`'s own
`started`-only counts — a busy worker fleet never blocks a new job from
being accepted as `queued`; it only delays when that job gets claimed.
`claim_next_listings_job`'s FIFO-by-age candidate ordering also means a
high-volume organization with many older queued jobs can never fully
starve another organization's newer job: once the high-volume
organization hits its own per-organization limit, the claim loop skips
its remaining candidates and continues to the next eligible job from any
other organization in the same batch (proven in `tests/test_amazon_
listings_job_lifecycle.py`).

### Retry / backoff behavior

The SP-API client (`AmazonSpApiListingsClient`) owns short, in-request
HTTP retries — unchanged by this milestone. The durable worker owns a
*separate*, higher-level decision: whether to reschedule an entire run for
a *later* attempt after the client's own short-retry budget is exhausted.
These two layers are deliberately kept apart so they never compound into
multiplied delays.

1. Prefer Amazon's own `Retry-After` header (RFC 7231, both delta-seconds
   and HTTP-date forms) when the failure was a 429.
2. Otherwise, bounded exponential backoff with *full* jitter:
   `random.uniform(0, base * 2**(attempt-1))`.
3. Either way, capped at `listings_sync_max_backoff_seconds`.
4. Retry budget exhausts on **either** attempt count or elapsed wall-clock
   time since first start, whichever comes first — the terminal failure
   class is always the sanitized `rate_limited`, regardless of which
   retryable class triggered the last attempt.
5. A retryable failure never occupies a worker while waiting — the run is
   parked at `waiting_to_retry` and the worker moves on to its next claim.

**Retryable** failure classes: `throttled`, `transient_request_failed`,
`malformed_page`, `record_count_inconsistent` — plausibly transient or
ambiguous-origin. **Everything else** (data anomalies like
`duplicate_sku`, request-shape problems like `authentication_failed`) is
terminal immediately — retrying identical parameters cannot change the
outcome.

### Lease / heartbeat relationship

A worker holds a run via `lease_owner` + `lease_expires_at`, both compared
against the *database's* clock (`func.now()`), never the worker process's
own clock. Two independent things call `_heartbeat` (same underlying
compare-and-set, two different triggers):

1. **Page-count-based** (`listings_sync_heartbeat_interval_pages`, default
   every page) — fires *after* a page finishes, records truthful
   `pages_fetched` progress. This is a progress-reporting cadence, not a
   safety mechanism.
2. **Time-based** (`listings_sync_heartbeat_time_interval_seconds`,
   default 60s) — a background task (`_renew_lease_while_awaiting`) runs
   concurrently with exactly one in-flight `client.fetch_page()` call,
   renewing the lease on a fixed wall-clock cadence regardless of whether
   that page ever completes. **This is the actual guarantee that a lease
   cannot expire mid-request**, however slow one Amazon call is — without
   it, a single sufficiently slow request (the SP-API client's own
   short-retry loop can legitimately take up to roughly
   `sp_api_timeout_seconds × max_attempts` plus backoff sleeps — around
   90s worst-case at this client's defaults) could otherwise outlast the
   lease with nothing renewing it in the meantime, since the page-count
   heartbeat only fires once that slow page *finishes*. The moment a
   renewal from this background task fails, the run aborts as
   `lease_lost` — even if the page itself subsequently arrives
   successfully, it is never processed or written.

Every mutating operation (`heartbeat_listings_run`, `complete_listings_
run`, `reschedule_listings_run_for_retry`) is an atomic compare-and-set on
`(lease_owner, status='started', lease_expires_at > now())` — a worker
that has lost its lease (stolen or merely expired) can never overwrite a
newer owner's progress, and an expired-but-not-yet-reclaimed worker
cannot silently finalize a run it no longer holds. Proven directly —
including the slow-request/renewal/takeover/stale-refusal scenarios — in
`tests/test_amazon_listings_ingestion_service.py`.

### Crash / replay behavior

If a worker process dies mid-job, its lease simply expires. The *next*
`claim_next_listings_job`/`claim_listings_run`/`enqueue_listings_run` call
for that scope terminalizes the abandoned row to `timed_out` and frees the
scope for a fresh claim. This is the **same** recovery path used for an
ordinary retryable-failure-into-`waiting_to_retry` cycle — there is no
separate "crash recovery" code path to maintain.

**This applies only to a job that has already been claimed (`status =
'started'`).** Every stale-reclaim `UPDATE` in `AmazonIngestionRunRepository`
(`claim_listings_run`, `enqueue_listings_run`, `claim_next_listings_job`)
is gated on `status = 'started' AND lease_expires_at IS NOT NULL AND
lease_expires_at < now()`. A `queued` row has no lease at all
(`lease_owner`/`lease_expires_at` are both `NULL` until claimed) and
cannot match that predicate no matter how long `created_at` ages —
verified directly by seeding a 30-day-old `queued` row and confirming
every one of those three methods leaves it completely untouched
(`tests/test_amazon_listings_job_lifecycle.py`). There is no age-based
expiration for the `queued` state anywhere in this codebase. Stated
plainly:

> A queued job is durable and remains queued until a worker claims it or
> an authorized administrative action terminalizes it. Lease recovery
> does not apply before claim.

`GET .../listings/summary` and `GET .../listings/sync/{run_id}` are pure
reads — neither calls anything that writes to `amazon_ingestion_runs`,
confirmed by repeated read/re-snapshot tests in `test_amazon_listings_
read_service.py` and `test_amazon_listings_sync_trigger.py`. Polling
either endpoint, however many times, can never cause a queued job to
progress.

Amazon pagination tokens are **never persisted anywhere** (no column, no
log, no response, no frontend state) — so a replay after a crash always
restarts from page one. This is intentional and has these consequences,
proven in `test_amazon_listings_ingestion_service.py`/`test_amazon_
listings_worker.py`:

- **No duplicate listing rows.** `AmazonSellerListingRepository.
  reconcile_snapshot` upserts by `(marketplace_participation_id,
  seller_sku)` — reprocessing page one again is idempotent.
- **No false deactivation.** Existing listings stay visible and are only
  deactivated by a snapshot that completes *and* is internally consistent
  (`pagination_complete=True` and the accepted count matches the reported
  total). A crashed, restarted, or still-in-progress run is never
  authoritative over what "missing" means.
- **Existing valid data stays visible** the entire time a replay is
  running — the previous successful snapshot's rows are untouched until a
  *new* snapshot both completes and reconciles successfully.

### Sanitized logging

The worker logs only ASI's own internal run id (an opaque UUID, not a
seller identifier) and outcome/status strings drawn from the same fixed
vocabulary already used in API responses. Never a seller id, SKU, ASIN,
token, connection id, page token, or raw Amazon payload — verified by
`test_logs_never_contain_seller_or_organization_identifiers` in `tests/
test_amazon_listings_worker.py`.

## Frontend polling strategy

The Seller Data page polls the existing **summary** endpoint (`GET
.../listings/summary`) rather than the dedicated job-status endpoint,
because the summary's `sync` evidence already carries the latest run's
status/counters without the client needing to hold a `run_id` anywhere.
This is what lets the page "discover and resume" an active run purely
from a normal page load or reload — no run id needs to be persisted in
the URL, localStorage, or anywhere else client-visible.

- Poll interval starts at 3s, backs off ×1.5 up to a 20s ceiling
  (`LISTINGS_SYNC_POLL_INITIAL_MS`/`_MAX_MS`/`_BACKOFF_FACTOR`, exported
  from `seller-listings.tsx`), and resets to the initial interval the next
  time a job starts.
- Stops entirely once the latest status is terminal, on participation
  change, and on unmount.
- Pauses (without losing its place) while the tab is hidden
  (`document.hidden`), and does an immediate catch-up fetch on
  `visibilitychange` back to visible.
- Never clears or replaces visible listing data while a job is
  in-flight — the listings/detail refetch only fires once, exactly when
  the latest run transitions from nonterminal to terminal.
- Throttle message is exactly: *"Amazon asked us to slow down.
  Synchronization will resume automatically."* — shown whenever
  `sync.status === "waiting_to_retry"`, with a human-relative `next_retry_
  at` estimate (`formatRelativeFutureTime` in `lib/seller-listings-view.ts`)
  and never a raw timestamp/internal detail.

## Hosting implications

**Production deployment requires an independently running worker
process.** The API alone will accept and durably queue jobs forever
without ever processing them if no worker is deployed — this is by
design (see "API vs worker responsibilities" above), not an oversight to
paper over with a fallback. Deploy the worker as its own process/service
alongside (not inside) the API process.

## Monitoring

Everything needed to monitor the pipeline is a plain SQL query against
`amazon_ingestion_runs` (no new observability tooling introduced):

```sql
-- Currently queued (nothing has claimed them yet)
select id, organization_id, created_at
from amazon_ingestion_runs
where run_type = 'listings' and status = 'queued'
order by created_at;

-- Currently running, with how stale their lease is
select id, lease_owner, lease_expires_at, last_heartbeat_at
from amazon_ingestion_runs
where run_type = 'listings' and status = 'started';

-- Waiting to retry, and when
select id, attempt_count := retry_count, failure_class, next_retry_at
from amazon_ingestion_runs
where run_type = 'listings' and status = 'waiting_to_retry'
order by next_retry_at;

-- Recently failed/timed_out, for alerting
select id, organization_id, failure_class, completed_at
from amazon_ingestion_runs
where run_type = 'listings' and status in ('failed', 'timed_out')
order by completed_at desc
limit 50;
```

A queued job whose `created_at` is far in the past with no worker having
ever claimed it (no `started_at`) is the signal that no worker process is
running — the most likely operational failure mode.

### Worker readiness design

No dedicated worker-heartbeat/liveness endpoint or table exists — a
deliberate choice, not an oversight. The *product* UI never needs one:
it already never claims active work is happening until `status ==
'started'` (a worker has genuinely claimed the job), which is the exact
safety property a readiness signal would otherwise exist to protect.
Adding sanitized worker-readiness messaging to the UI without a real
signal to back it would be worse than the current honest "queued" /
"still queued" states.

For *operators*, worker liveness today can only be **inferred**, not
directly observed:

- **Alert threshold — oldest queued job**: if the oldest `status='queued'`
  row's `created_at` age exceeds a small multiple of the expected normal
  claim latency (e.g. a few minutes, tuned per deployment) with no
  `started_at` ever set, no worker is running or all workers are stuck.
  Query: the first block above, sorted by `created_at`.
- **Alert threshold — stale `started` job**: if a `started` row's
  `last_heartbeat_at` is older than `listings_sync_lease_duration_seconds`
  past `lease_expires_at`, that worker has already crashed or hung — the
  row will self-heal via the next claim attempt's stale-reclaim step, but
  a *pattern* of this happening is itself worth alerting on separately
  from any single occurrence.
- **Known gap**: with zero jobs currently in flight, there is no way to
  distinguish "worker process running but idle" from "worker process
  entirely absent" — both look identical (no rows to observe). A genuine
  periodic worker self-heartbeat (e.g. a `worker_liveness` table row each
  instance upserts on an interval) would close this gap; it is a
  reasonable future enhancement, not built or authorized in this pass.

### Required deployment order

1. Apply migration `0011_listings_job_lifecycle`.
2. Deploy the updated API (safe to run ahead of the worker — jobs simply
   accumulate as `queued`).
3. Deploy and verify at least one worker process is actually claiming
   jobs (check the monitoring queries above) **before** enabling/
   announcing the Sync button to general users — a `202 Accepted`
   response gives no guarantee any worker exists yet.

### Worker rollback procedure

Rolling back the worker is independent of rolling back the API or the
migration: stop the worker process(es). Any `queued`/`waiting_to_retry`
jobs remain exactly as they are — durable, untouched — and resume being
claimed automatically whenever a worker (old or new version) starts
again. No schema change and no job-state cleanup is needed purely to
roll back a worker deployment. If the worker code itself is being rolled
back due to a defect that already corrupted in-flight state, restore
from the nearest backup taken after the last known-good worker version
(see "Database backup status" below) rather than attempting to hand-edit
`amazon_ingestion_runs` rows directly.

## Operator maintenance: terminalizing an unclaimed queued job

There is no public, end-user-facing cancellation endpoint for Listings
jobs (deliberate — the trigger/status routes only ever enqueue or report,
never mutate an existing run into a terminal state). If a `queued` job
genuinely should never be processed (queued in error, a test trigger,
etc.), an operator can terminalize it — but **only** while it is still
genuinely unclaimed:

```
cd apps/api
uv run python -m app.amazon.listings_job_admin terminalize-queued \
    --organization-id <uuid> --run-id <uuid> [--reason cancelled_before_start]
```

Both flags are required with no default and no "latest queued job"
auto-selection — the operator must identify the exact job deliberately
(e.g. via the read-only queries in "Monitoring" below). Nothing is ever
printed back except a sanitized outcome — never the organization id, run
id, marketplace id, or any credential.

Underneath, `AmazonIngestionRunRepository.terminalize_unclaimed_listings_
run` is a compare-and-set `UPDATE` requiring **all** of: exact `id` +
`organization_id` match, `run_type = 'listings'`, `status = 'queued'`,
and `started_at`/`lease_owner`/`lease_expires_at`/`last_heartbeat_at` all
`IS NULL`. If a worker claims the row at any point before this executes
— even a fraction of a second earlier — every one of those columns
changes, the `UPDATE` matches zero rows, and the command reports a safe
conflict instead of overwriting active work. This is proven under real
concurrent racing (not just sequential simulation) in `tests/postgres/
test_disposable_postgres_listings_job_lifecycle_concurrency.py`. On
success, the run becomes `status='failed'` with `completed_at` set, the
given sanitized `failure_class` (default `cancelled_before_start`), and
`pagination_complete=False` — truthful, since it never attempted
pagination at all. No Amazon call, no listings reconciliation, no lease
field touched (already all `NULL`), and it can never touch a `started`,
`waiting_to_retry`, already-terminal, foreign-organization, or
`marketplace_participations` run.

## Rollout / rollback

- **Rollout**: apply migration `0011_listings_job_lifecycle`
  (additive-only — widens a CHECK constraint, relaxes `started_at` to
  nullable, adds two new nullable columns, drops+recreates the partial
  unique index with a wider predicate; nothing is dropped or narrowed),
  deploy the updated API, then deploy at least one worker process. Order
  matters: the API can safely run ahead of the worker (jobs simply queue
  up), but the worker cannot run against the *old* schema.
- **Rollback**: the migration's `downgrade()` proactively refuses (raises
  `RuntimeError`, no partial mutation) if any row is currently `queued` or
  `waiting_to_retry` — those states cannot be represented by the prior
  schema. Drain or manually resolve those rows first. Rolling back the API
  alone (worker still running the new code) is not a supported
  intermediate state — roll back both together.
- **Gotcha discovered applying this migration live** (2026-08-29): the
  revision id itself is a value stored in Alembic's own bookkeeping table
  (`alembic_version.version_num`, `VARCHAR(32)` by default). The original
  id chosen for this migration, `0011_amazon_listings_job_lifecycle` (34
  chars), exceeded that width — the upgrade failed on its very last
  statement with `StringDataRightTruncation`, but rolled back cleanly
  (transactional DDL) leaving the database untouched at `0010`. Fixed by
  shortening to `0011_listings_job_lifecycle` (27 chars). No offline test
  in this repository checks revision-id length against Postgres's actual
  column width — keep future revision ids at 32 characters or fewer, or
  add such a check.

## Known limitations

1. `partial` remains a valid terminal status on the shared CHECK
   constraint, but no Listings code path currently produces it — Listings
   reconciliation is all-or-nothing today (`succeeded` or `failed`/
   `timed_out`).
2. The per-organization/global admission-control counts at trigger time
   include `queued`+`waiting_to_retry` jobs the worker fleet may not be
   anywhere near processing yet — a burst of triggers can fill the
   admission-control budget without any of them being actively worked.
   This is intentionally conservative (never oversell capacity), not a
   defect, but operators tuning these settings should know queued jobs
   count against the same limit as running ones.
3. No production worker deployment exists yet in this environment — the
   worker entry point, its tests, and the guarded disposable-PostgreSQL
   concurrency suite are the complete proof available without a real
   hosting target. See `12B3F_SELLER_LISTINGS_UI.md`'s and this
   repository's `CLAUDE.md`'s existing "known limitations" for the
   broader set of things Amazon integration does not yet do.
4. **Fixed during this milestone's live verification, not left latent.**
   `AmazonIngestionRunRepository.list_for_connection()` (feeds
   `AmazonConnectionOverview.latest_ingestion`) does not filter by
   `run_type` — a pre-existing condition, not introduced here. Once
   `started_at` became nullable for the `queued` state, this became a
   live-breaking 500 the moment any Listings run existed for a
   connection: on real PostgreSQL, a `queued` run's NULL `started_at`
   sorts *first* under `ORDER BY started_at DESC` (NULLS FIRST is
   PostgreSQL's default for DESC — the opposite of SQLite's, which is why
   no offline test caught this), so `list_for_connection()[0]` returned
   the Listings row, and `AmazonIngestionStatusRead.started_at` (non-
   nullable) raised a `pydantic` `ValidationError`. Reproduced live
   against the configured Supabase database on 2026-08-29, fixed the same
   day in `AmazonConnectionService._latest_ingestion_read_state`
   (`app/amazon/connection.py`) by filtering to `run_type ==
   "marketplace_participations"` before selecting the latest row —
   `list_for_connection()` itself is intentionally left unfiltered, since
   other callers legitimately want every run type. Regression-tested in
   `tests/test_amazon_connection_api.py::
   test_connection_overview_ignores_a_listings_run_when_computing_latest_ingestion`
   using a `started` (non-null, strictly later `started_at`) Listings run
   rather than a `queued` one, so the test's failure mode does not itself
   depend on backend-specific NULL-ordering and fails deterministically
   on any database if the fix regresses.

## Database / Git alignment risk

Supabase currently matches this branch's working tree exactly
(`0011_listings_job_lifecycle`, single head), but **the migration code
itself is not yet committed, pushed, CI-proven, or merged into `main`**.
Until that happens, the live database's schema is ahead of what's
recorded in version control on `main` — a temporary deployment-source
mismatch. If this working tree were lost (uncommitted changes discarded,
branch abandoned, machine lost) before committing, recovery would require
either restoring from the working tree (if any copy survives) or
re-authoring migration `0011` from scratch against the *live* schema —
substantially more error-prone than committing a already-tested,
already-live-verified migration now. This branch's migration, models, and
every supporting file should be included together in the eventual staged
set — splitting them across commits risks a state where `main`'s models
and its migrations disagree.

## Database backup status

**Historical backups exist** (read-only inspected, `/Users/kapilsingh/ASI-
Database-Backups/`, all `0600` permissions):

| File | Captured revision | UTC timestamp |
| --- | --- | --- |
| `asi_supabase_public_0008_amazon_oauth_states_...dump` | 0008 | 2026-08-27T06:55:40Z |
| `asi_supabase_public_0009_amazon_seller_identity_...dump` | 0009 | 2026-08-28T09:25:03Z |
| `asi_supabase_public_0010_amazon_seller_listings_...dump` | 0010 | 2026-08-29T08:15:20Z |

**No backup represents the state immediately before `0011`.** The most
recent (0010) backup is timestamped `2026-08-29T08:15:20Z`, roughly 8
hours **before** the first real Listings ingestion run completed
(`2026-08-29T16:26:16Z`) — restoring it would discard all 10 real
`amazon_seller_listings` rows currently in the database, not merely "miss
0011's schema." It predates the state 0011 was applied on top of, let
alone 0011 itself.

`pg_dump`/`pg_restore` are not installed in this session's environment —
the existing three backups were produced by different tooling/session.
Executing the plan below requires either installing those tools here or
running the plan from wherever the prior three backups were made.

**Post-`0011` backup plan (prepared, not executed)**:

```bash
# 1. Dump (schema + data, custom format — matches the existing 0600
#    convention automatically via umask; chmod afterward to be explicit).
#    Never place the password directly on the command line; use a
#    .pgpass entry or an interactively-set PGPASSWORD.
TS=$(date -u +%Y%m%dT%H%M%SZ)
OUT="/Users/kapilsingh/ASI-Database-Backups/asi_supabase_public_0011_listings_job_lifecycle_${TS}.dump"
pg_dump --host=db.PROJECT_REF.supabase.co --port=5432 \
        --username=postgres --dbname=postgres --schema=public \
        --format=custom --file="$OUT"
chmod 600 "$OUT"

# 2. Checksum.
shasum -a 256 "$OUT" > "${OUT}.sha256"

# 3. Structural archive validation (metadata only — never touches any
#    database; confirms the dump is well-formed and lists its objects).
pg_restore --list "$OUT" | head -50

# 4. Archived Alembic revision verification (extracts just the
#    alembic_version row to stdout/a file — read-only relative to any
#    database, proves the dump was taken while the source was genuinely
#    at 0011).
pg_restore --data-only --table=alembic_version -f - "$OUT" | grep 0011_listings_job_lifecycle
```

No restore operation is included or implied by this plan. This backup
must exist before terminalizing the accidental job or starting a worker
— both are the first operations that would otherwise make the current
state (durable job queued, zero Listings processing since 0011) harder
to reconstruct if something unexpected happens.

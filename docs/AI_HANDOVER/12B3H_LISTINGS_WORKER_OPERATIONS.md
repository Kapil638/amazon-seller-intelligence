# 12B.3H — Listings Worker Operations & Reliable Local Startup

Companion to `docs/AI_HANDOVER/12B3G_DURABLE_LISTINGS_SYNC.md`. That
milestone built the durable queue/worker architecture; this one closes
the *operational* gap it left open — nothing in 12B.3G's own design
guaranteed a worker process would actually be running to claim a queued
job, in local development or in production.

## Root cause of "Sync remains queued because no worker is running"

Not a bug in the queue/claim logic (proven correct and duplicate-safe as
of 12B.3G/the duplicate-trigger remediation). The trigger API's job is
to enqueue a durable row and return immediately — by design, it has no
dependency on worker availability, and must not gain one (a legitimate
new job must never be rejected merely because no worker happens to be
running right now; see `AmazonListingsSyncTriggerService`'s own
docstring). The actual gap was purely operational:

- Local development had no supported way to start the worker alongside
  the frontend and API — `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md`
  never mentioned it at all before this milestone. A developer who never
  separately ran `uv run python -m app.amazon.listings_worker` would see
  every sync sit `queued` forever, with no operational explanation.
- The worker itself (`app/amazon/listings_worker.py`) had no `SIGTERM`
  handling, and a database connectivity failure in its own claim step
  (as opposed to a job-processing failure, which was already handled)
  would crash the whole process rather than recovering — so even a
  developer who *did* start it could lose it silently to a transient
  Supabase pause.
- No production deployment target exists yet at all (see below), so
  there was no supervised worker to fall back on either.

## Architecture investigation (evidence, not assumption)

- **Existing commands**: backend `cd apps/api && uv run uvicorn
  app.main:app --reload --port 8000`; frontend `cd apps/web && npm run
  dev`; worker `cd apps/api && uv run python -m
  app.amazon.listings_worker` (documented only in that module's own
  docstring, never in developer setup docs, before this milestone);
  tests `uv run pytest` / `npm test`; build `npm run build`. All
  confirmed directly from `docs/AI_HANDOVER/14_LOCAL_DEVELOPMENT_SETUP.md`
  and the modules themselves.
- **No hosting platform is configured anywhere in this repository** —
  confirmed by searching the full tree for a `Dockerfile`,
  `docker-compose*`, `Procfile`, `render.yaml`, `fly.toml`, any
  Railway/Heroku/AWS manifest, and any deploy step in
  `.github/workflows/backend-database-ci.yml` (which explicitly states
  in its own header comment that it uses no deployment or production
  credentials at all). `docs/AI_HANDOVER/03_TECH_STACK.md` lists the
  full stack and hosting is not part of it. This is why Phase 6 below is
  a deployment-neutral runbook, not a platform-specific manifest — one
  was never invented.
- **Worker behavior before this milestone** (`app/amazon/
  listings_worker.py`, prior version): `main()` caught only
  `KeyboardInterrupt` (SIGINT/Ctrl-C); `SIGTERM` had Python's default
  disposition (immediate termination, no cleanup at all). `run_forever`'s
  claim step (`claim_next_listings_job`) was not wrapped in any
  exception handling — an unhandled exception there (most plausibly a
  database connectivity failure) would propagate out of the loop and
  crash the process; only job-*processing* exceptions (inside
  `process_claimed_job`) were already contained. The idle-poll interval
  was a hardcoded module constant, not sourced from `Settings`.
- **Recovery after a temporary database failure**: confirmed absent —
  the claim step's lack of exception handling meant this was a hard
  crash, not a recoverable condition, before this milestone's fix.
- **No single local command started web + API + worker together** —
  confirmed by the absence of any root `package.json`, `Makefile`, or
  `scripts/` directory before this milestone.
- **No truthful "no worker available" signal existed in the API/UI** —
  the summary endpoint only ever reports the job's own persisted
  status/timestamps; nothing in the schema or API tracks worker
  liveness. The frontend already had (from earlier work) a stale-queue
  circuit breaker showing "Still queued" past a threshold, but its copy
  implied a worker would eventually get to it without ever admitting
  that no worker-liveness signal exists at all — corrected in Phase 5
  below.
- **Configuration distinguishes** queue polling interval (previously
  hardcoded, now `listings_worker_idle_poll_seconds`), job heartbeat
  interval (`listings_sync_heartbeat_time_interval_seconds` /
  `listings_sync_heartbeat_interval_pages`), lease duration
  (`listings_sync_lease_duration_seconds`), queue stale threshold
  (frontend `LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS`), and job retry
  limits (`listings_sync_max_attempts`, `listings_sync_base_backoff_
  seconds`, `listings_sync_max_backoff_seconds`,
  `listings_sync_max_total_retry_seconds`) — all confirmed present
  already in `app/core/config.py` before this milestone, except the
  idle-poll interval and the new worker-poll-error backoff
  (`listings_worker_poll_error_base_backoff_seconds` /
  `listings_worker_poll_error_max_backoff_seconds`), added here.
- **An idle worker makes no Amazon call**: structurally guaranteed and
  unchanged — `run_once()` returns `False` immediately when
  `claim_next_listings_job` finds nothing, before `process_claimed_job`
  (the only code path that ever touches Amazon) is reachable. Confirmed
  by the existing `test_run_once_returns_false_when_nothing_is_queued`
  test asserting zero requests were ever made to the fake Amazon client.

## Worker hardening (Phase 4)

All in `app/amazon/listings_worker.py`:

- **`ASI_LISTINGS_WORKER_ENABLED` — explicit, fail-closed authorization
  gate**, checked first thing in `main()`, before `Settings` is even
  resolved. Must be exactly `"1"` or `"true"` (case-insensitive,
  incidental surrounding whitespace tolerated); anything else — unset,
  empty, `"false"` — refuses to start (`EXIT_DISABLED = 3`), logging why
  without ever echoing the variable's actual value. This exists because
  this repository's local `.env` points at a real, live Supabase
  project — starting a worker with no explicit signal would mean any
  convenience command (this milestone's own `./scripts/dev.sh` included)
  could silently begin making real Amazon SP-API calls the moment a job
  existed. `./scripts/dev.sh` checks the identical variable itself
  before attempting to start a worker child, so the gate holds even for
  someone who never reads this module's own docstring; running the
  worker module directly is never a way around it, since the module
  enforces it independently. The API and frontend never read this
  variable. The exact same flag is the intended mechanism for
  deliberately enabling the worker on a future deployed worker service
  (see the deployment runbook below) — nothing about the flag's meaning
  changes between local and production use.
- `SIGTERM` now installs the same cooperative-stop handler as `SIGINT`,
  via `loop.add_signal_handler` (POSIX-only — see below) rather than
  relying on Python's default signal disposition. Neither signal
  interrupts an in-flight claim; `request_stop()` only sets a flag
  checked between `run_once()` calls, so a hosting platform's graceful-
  shutdown window is honored naturally. **If `SIGTERM` arrives while a
  job is mid-flight** (e.g. awaiting an Amazon page request), that
  request is not aborted — it runs to its natural conclusion, and only
  then does the loop notice the stop flag. If the supervisor's grace
  period is shorter than that and escalates to `SIGKILL`, the in-flight
  job's own `lease_expires_at` (set at claim time, never silently
  extended) still expires on its own schedule regardless of the hard
  kill, and `claim_next_listings_job`'s existing stale-lease reclaim
  marks it `timed_out` and frees the scope for a new attempt — identical
  recovery to any other hard process crash; only that one attempt's
  outcome is lost, never the row itself or a partially-written listing
  (each page's reconciliation is already transactional).
- The claim/poll step is now wrapped in bounded exponential backoff — but
  **only** for `sqlalchemy.exc.OperationalError` and `OSError` (covering
  `ConnectionError`/`TimeoutError`), the two shapes a real database/
  transport failure plausibly takes here. A bare `except Exception` was
  deliberately rejected: a genuine programming defect in the claim path
  (`TypeError`, `AttributeError`, an invariant violation) must propagate
  and crash the process for a supervisor to restart and an operator to
  notice, never be retried forever as if it were "just" a connectivity
  blip. Backoff itself (`listings_worker_poll_error_base_backoff_
  seconds` doubling up to `listings_worker_poll_error_max_backoff_
  seconds`, resetting to base the moment a poll succeeds again) is
  otherwise unchanged — never a busy loop, never a permanent crash from
  a transient outage (e.g. Supabase pausing). A `model_validator` on
  `Settings` rejects a configured base that exceeds its own max at
  startup, rather than silently producing a degenerate backoff curve.
  Deliberately a *separate* concern from the existing job-level
  Amazon-retry backoff (`listings_sync_base_backoff_seconds` etc.),
  which paces retries of one job already claimed — conflating the two
  would be a semantic error, not just a naming one.
- Configuration errors at startup (`get_settings()` raising
  `ValidationError`) are fail-closed: the process logs a sanitized error
  (never the validation error's own text, which can echo back invalid
  values) and exits immediately with a distinct, documented code
  (`EXIT_CONFIGURATION_ERROR = 2`, vs `EXIT_OK = 0`) rather than looping.
- New sanitized log lines: worker started, poll-error retry (with the
  computed delay, never the underlying exception's message at INFO/
  WARNING level — the full traceback is available at DEBUG only),
  graceful shutdown requested/completed. All existing job-claimed/
  terminal-outcome logging is unchanged.
- Existing lease, heartbeat, and stale-worker protections
  (`claim_next_listings_job`'s `SELECT ... FOR UPDATE SKIP LOCKED`, the
  partial unique index, lease-expiry reclaim) are untouched — this
  milestone only hardens the process *around* that logic, never the
  logic itself.

**Platform note**: `loop.add_signal_handler` is POSIX-only (raises
`NotImplementedError` on Windows). This project's documented local
development target is macOS/Linux (consistent with existing tooling
elsewhere in this repo, e.g. Homebrew-path Postgres tools referenced in
earlier milestones) — Windows is not a supported local development
platform, stated here honestly rather than silently degrading.

## Unified local startup (Phase 3)

`./scripts/dev.sh` — plain bash, no new dependency, no process-manager
package. Starts the backend and frontend always, and the Listings worker
only when explicitly authorized — see below — each with output prefixed
(`[backend]`, `[frontend]`, `[worker]`) via `sed`, never rewriting or
redacting log content itself (each underlying process remains
responsible for its own sanitized logging, as before).

- **Worker authorization gate**: `ASI_LISTINGS_WORKER_ENABLED=true` must
  be set for `dev.sh` to even attempt starting a worker child — without
  it, the script logs why and starts only the backend and frontend. This
  mirrors (and is enforced independently of) the identical gate inside
  `app/amazon/listings_worker.py` itself, so there is no path — through
  this script or by running the worker module directly — that starts a
  live worker without an explicit, deliberate signal. See "Worker
  hardening" above for why this exists.
- **Signal handling**: traps `INT`/`TERM` once, in the main script
  process, before starting anything. On shutdown, sends `SIGTERM` to
  each child's full process group (`set -m` gives each backgrounded
  child its own group; `kill -TERM -$pid` reaches it and any of its own
  subprocesses — e.g. `npm run dev`'s `next-server`, or Python's
  reloader — not just the immediate wrapper), waits up to 10 seconds for
  cooperative exit, then escalates to `SIGKILL` for anything still
  alive. A single Ctrl-C or `kill` on the script's own process
  terminates all three children; verified directly (see `scripts/
  test_dev_sh.sh`) — SIGTERM specifically, since SIGINT delivered
  programmatically to a background process requires a controlling
  terminal to behave as expected, which a non-interactive test runner
  does not provide; `trap ... INT TERM` registers the identical handler
  for both, so the SIGTERM proof exercises the same code path Ctrl-C
  would in a real terminal.
- **Partial-start-failure cleanup**: a short grace period after startup
  confirms every child is still alive; if one exited immediately (bad
  config, missing dependency), the others are torn down and the script
  exits non-zero rather than leaving a partial setup running.
- **Port-conflict detection**: checks `lsof` for the backend/frontend
  ports before starting anything, with a clear error rather than a
  confusing double-bind failure from `uvicorn`/`next dev` themselves.
- **Duplicate-worker protection**: checks `pgrep -f
  "app\.amazon\.listings_worker"` before starting a worker; if one is
  already running, skips starting a second (backend/frontend still
  start normally) rather than silently doubling up.
- **Existing individual commands remain fully supported and
  documented** — this script is an addition, not a replacement.
- Never modifies `.env`/`.env.local`.
- Bash 3.2 compatible deliberately (macOS ships 3.2 by default; `wait
  -n` and other 4.3+/5.1+ features are avoided throughout, confirmed by
  testing against the actual shipped `/bin/bash` version, not merely a
  newer one that might be installed locally).

## Queue-health UI (Phase 5)

`apps/web/src/components/seller-listings-sync-strip.tsx` (already
substantially built in an earlier milestone; this one closes the one
honesty gap): a fresh `queued` job shows "Waiting for synchronization
worker"; once queued past the existing stale threshold
(`LISTINGS_SYNC_STALE_QUEUE_THRESHOLD_MS`), it switches to "Still
queued" with an explicit admission — new in this milestone — that *this
page has no way to confirm whether a synchronization worker is currently
running*, offers a manual "Refresh status" (GET-only, never re-triggers
a sync), and never implies `started` until `started_at` is actually
present on the record. No schema change was needed or made — no durable
worker-registration/heartbeat table exists, and none was added; if that
ever becomes genuinely necessary, it requires its own proposed migration
and explicit authorization, not an addition here.

## Production worker deployment — no platform configured

Per the investigation above, no hosting platform is configured in this
repository. Per this milestone's own instructions, none is invented
here. The runbook below is deployment-neutral; actual deployment to any
specific provider remains a distinct, later authorization gate.

### Runbook

**API service**
- Command: `uv run uvicorn app.main:app --host 0.0.0.0 --port
  ${PORT:-8000}` (no `--reload` — that flag is local-development-only).
- Environment variables required (names only — see `apps/api/.env.example`
  for the complete authoritative list; never commit or paste actual
  values): `DATABASE_URL`, the SP-API/LWA credential set (sandbox and
  production/Draft kept separate, per this repo's existing security
  rules), `AMAZON_SECRET_BACKEND` and its supporting variables,
  `AMAZON_DEVELOPMENT_SECRET_STORE` (development-only; production
  requires a real backend, which does not exist yet per this repo's own
  documented limitations), Supabase Storage credentials, OpenAI
  credentials, `NEXT_PUBLIC_API_BASE_URL` (frontend-side).
- Startup/restart policy: standard HTTP-service policy for whatever
  platform is eventually chosen (restart on crash, health check on the
  existing `/` or an equivalent liveness route).
- Graceful shutdown: `uvicorn` already handles `SIGTERM` for in-flight
  HTTP requests; no change needed for the API process itself.
- Initial replica count: 1, matching current usage; horizontal scaling
  is a separate, later decision.

**Worker service**
- Command: `uv run python -m app.amazon.listings_worker` — exactly the
  local development command, unchanged.
- **`ASI_LISTINGS_WORKER_ENABLED=true` must be set in this service's own
  environment** — this is the intentional mechanism for deliberately
  enabling live job processing on this specific deployed service; the
  worker refuses to start (exit code 3) without it. The API service's
  environment must **not** set this variable — it never reads it, and
  setting it there would have no effect either way, but omitting it
  keeps the distinction between "a process authorized to process real
  jobs" and "a process that is not" legible at the environment-config
  level, not just in code.
- Same `DATABASE_URL` and secret configuration as the API service — the
  worker resolves Amazon credentials through the identical
  `SecretProvider`/connection-token-reference path, never a separate
  credential set.
- Startup/restart policy: restart on any process exit (the worker's own
  exit codes — `EXIT_OK = 0`, `EXIT_CONFIGURATION_ERROR = 2`,
  `EXIT_DISABLED = 3` — let a supervisor distinguish "asked to stop" from
  "cannot start at all" from "not authorized to run here" without
  parsing log text; a `2` or `3` should not trigger a restart loop
  without operator attention, since retrying either cannot succeed on
  its own without a human fixing the underlying configuration).
- Graceful shutdown: send `SIGTERM`; the worker finishes (or safely
  abandons, per its documented lease-expiry recovery) any in-flight
  claim before exiting — no forced-kill window shorter than the
  configured lease duration should be used, or a real in-flight job
  could be killed mid-Amazon-call unnecessarily (it would still recover
  via lease expiry, but a clean stop is preferable when available).
- No public HTTP exposure — the worker has no HTTP server at all, and
  none should be added merely to satisfy a platform's default health
  check; use that platform's process-liveness check instead if a
  network-based check is mandatory.
- No migration execution from worker startup — migrations remain a
  distinct, deliberate, manually-invoked step (`uv run alembic upgrade
  head`), run once per deployment before either service starts, never
  automatically by either service's own startup path.
- Initial replica count: **exactly one**, for the initial soft launch —
  `claim_next_listings_job`'s `SELECT ... FOR UPDATE SKIP LOCKED` is
  already proven safe under multiple concurrent workers (see the
  guarded PostgreSQL concurrency suite), so scaling to more than one is
  a capacity decision, not a correctness requirement, whenever queue
  depth or latency actually warrants it — document that decision at the
  time it's made, backed by real queue-age metrics, not preemptively.
- **API replicas vs. worker replicas are a completely independent
  scaling axis**: API replica count follows HTTP request volume; worker
  replica count follows queue depth/age. Scaling one must never be
  conflated with or driven by the other.

**Logs and alerting**: both services should log to whatever the
platform's standard log aggregation is; the worker's own sanitized
lines (started, claimed, terminal outcome, poll-error retry, graceful
shutdown) are already structured enough to alert on repeated poll-error
warnings (indicating a sustained database connectivity problem) or an
unexpected `EXIT_CONFIGURATION_ERROR` exit.

**Queue-age monitoring**: the existing `AmazonIngestionRun.created_at`
(queued time) vs. `started_at` (claimed time) already gives everything
needed for an operator-side "how long has the oldest queued job been
waiting" metric/alert — no new column or migration is required for this;
it is a monitoring-query concern, not a schema concern.

**Database connection considerations**: the worker holds a long-lived
connection pool exactly like the API does (`get_engine()`'s
`pool_pre_ping=True` already handles a stale/dropped connection
transparently); the new poll-error backoff in this milestone is what
specifically covers a full, sustained outage (e.g. Supabase pausing)
rather than a single dropped connection.

**Deployment order**: apply migrations once (`alembic upgrade head`) →
deploy the API service → deploy the worker service. The API never
depends on the worker being up (jobs simply queue); the worker never
depends on the API being up (it talks to the database directly) — order
between API and worker specifically does not matter, only that
migrations happen first.

**Rollback procedure**: standard for whatever platform is chosen —
redeploy the previous known-good image/commit for both services. Because
Listings runs are durable rows, not in-memory state, a worker rollback
never loses a queued job — at worst, an in-flight job's lease expires
and it is safely reclaimed by the next (rolled-back) worker instance,
per the existing lease-recovery design.

**Post-deployment verification**: confirm both services report healthy;
confirm exactly one worker process is running (never more than the
documented initial count, until a deliberate scaling decision); trigger
one real sync only with explicit separate authorization, exactly as
every prior live-Amazon step in this project has required.

Actual deployment to a specific hosting provider remains an explicit,
separate authorization gate — nothing above deploys anything.

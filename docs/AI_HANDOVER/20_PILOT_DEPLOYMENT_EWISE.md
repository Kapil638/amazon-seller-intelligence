# 20 — Private Pilot Deployment: `ewiseintelligence.com`

Durable record of the pilot-deployment design and implementation pass.
Branch: `pilot-deployment-ewise`, created from `origin/main` (includes
merged PR #27, 12B.6C Inventory Health). **No deployment, DNS change,
or Amazon developer-console change has been performed as part of this
pass** — this document, and the code/config changes alongside it, are
preparation only, pending explicit approval per the governing task's
own gating (report → approve → deploy → stop before Amazon OAuth
changes).

No existing deployment configuration (`Procfile`, `railway.json`,
`docker-compose.yml`, CI-for-deploy) existed anywhere in this repo
before this pass — confirmed by direct search. This is a greenfield
deployment design, not a modification of an existing one.

**Revision history:**
- Pass 1: initial audit + TrustedHostMiddleware, DB pool sizing, worker
  self-watchdog, public/private domain routing, draft legal pages. Two
  gaps flagged as unresolved (§0a, §0c below, original text preserved
  in git history) plus an interim-only secret-storage recommendation.
- Pass 2 (this revision): all three flagged gaps closed with real
  implementations, not workarounds — production `SecretProvider`
  (§0a), Cloudflare Access API authentication (new, §0b), and the
  SP-API Login URI (§0c) — plus Railway resource limits/cost analysis
  (§1) and updated exact OAuth values (§6). Legal pages remain drafts
  (unchanged, pending operator-supplied legal details). Still no
  external deployment, DNS, or Amazon console change.

## 0. Architectural gaps found during audit — all now resolved in code

### 0a. Production `SecretProvider` backend — implemented

Originally: `AMAZON_SECRET_BACKEND=production` raised `SecretAccessError`
unconditionally — reserved, unimplemented. **Now implemented**:
`app/amazon/production_secrets.py`'s `ProductionSecretProvider`, backed
by a new PostgreSQL table (`amazon_encrypted_secrets`, migration `0018`)
and AES-256-GCM authenticated encryption — see the final report (message
to operator) for the full design, or the module's own docstring for the
complete threat model. Summary:

- Ciphertext-only storage: `reference` (the existing ASI secret
  reference, non-secret), `key_version`, `nonce` (fresh per write),
  `ciphertext`. No column anywhere holds plaintext.
- The reference string is AES-GCM associated data — a ciphertext can
  only decrypt successfully under the exact reference it was stored
  for, defending against a row-swap/relabel at the storage layer.
- Master key material: `AMAZON_SECRET_ENCRYPTION_KEYS` (JSON
  `{key_version: base64(32 bytes)}`) + `AMAZON_SECRET_ACTIVE_KEY_VERSION`
  — Railway encrypted variables only, never committed. Missing/invalid
  configuration fails closed at `SecretProviderFactory.create()` time,
  never lazily, never falling back to development.
- Key rotation: `ProductionSecretProvider.rotate_key_version()`
  re-encrypts one row under a new configured key_version; rows not yet
  rotated stay readable under their original key_version indefinitely.
- Migration path for the existing local seller token: documented and
  tested (`app/amazon/secret_migration_admin.py`, `migrate` subcommand),
  **not executed** against the real token — requires a deliberate
  operator invocation with `AMAZON_SECRET_BACKEND=production` and real
  key material configured first.

No Railway volume is used for secrets in this design — superseding pass
1's interim "development provider + volume" recommendation entirely.

### 0b. API authentication — implemented (new gap, closed in the same pass)

CORS and `TrustedHostMiddleware` (pass 1) only ever validate an
Origin/Host header — not an identity check; any non-browser client can
set either to anything. `app/core/cloudflare_access.py`'s
`CloudflareAccessMiddleware`, opt-in via `Settings.api_auth_backend`
("disabled" by default; "cloudflare_access" for the deployed pilot),
verifies a Cloudflare Access JWT's signature (RS256, Cloudflare's own
published JWKS), issuer, audience, and expiry on every route except a
hardcoded, non-configurable allowlist (`/health`, the OAuth callback,
the OAuth Login URI). See §5 (now revised) and the final report for the
full design and exact public-endpoint inventory.

### 0c. SP-API OAuth Login URI — implemented

Originally: no `/connection/login` route existed anywhere in this
codebase; Amazon's own Developer Console still requires a real, live
Login URI for a self-authorization SP-API application even though this
app's actual flow is entirely ISV-initiated. **Now implemented**:
`GET /api/v1/amazon/connection/login` (`app/api/routes/
amazon_connection.py`) reuses `AmazonConnectionService.
start_authorization` exactly — same fresh hashed OAuth state, same
Seller Central consent URL construction the in-app "Connect Amazon"
button already uses — and redirects the browser straight there. See §6
for the exact, tested value.

### 0d. Hung-worker detection did not transfer to Railway's per-service model — fixed in pass 1

`scripts/supervisor.py`'s hung-worker detection (built this session)
polls a worker's heartbeat *externally* and force-restarts the process
if it goes stale while still alive. On Railway, each worker is its own
isolated service — nothing plays that external-supervisor role.

**Fixed as part of this pass**, not merely flagged: each worker now
runs a same-process **self-watchdog on a separate OS thread**
(`app/amazon/worker_heartbeat.py`, `_HeartbeatWatchdog`) — the only
way a worker can notice *itself* going unresponsive, since a task on
the same (possibly frozen) asyncio event loop could never get scheduled
to check. If `worker_watchdog_stale_after_seconds` (default 600s)
passes with no successful heartbeat write, the thread calls
`os._exit(1)` — a hard process death — so Railway's own
restart-on-exit policy restarts it cleanly. Enabled by default,
`enable_watchdog=True` on `start_heartbeat_loop`; no worker code
changes were needed beyond this shared module. 5 new tests
(`tests/test_amazon_worker_heartbeat.py`) mock `os._exit` and prove:
never fires while heartbeats keep succeeding, fires exactly once after
genuine silence, and a graceful `stop()` permanently prevents a late
fire.

### 0e. Draft legal pages could previously be deployed by accident — now blocked

`/privacy` and `/terms` (pass 1) intentionally still carry `[PENDING:
...]` placeholders for legal-entity/contact/jurisdiction facts only the
operator can supply — but nothing previously stopped a `npm run build`
(the exact command Railway's Nixpacks build step runs, §1) from
succeeding and deploying them exactly as-is. Final review gate, PR #28:
`apps/web/scripts/check-legal-pages-ready.mjs`, wired as the `prebuild`
npm script (npm's own lifecycle convention — runs automatically before
`build`, no Railway/CI configuration needed), scans both page source
files directly for the literal `[PENDING` marker and fails the build
(non-zero exit) if either still contains one. Scanning the actual source
rather than a separately-maintained "ready" flag means this cannot drift
out of sync with the real content. `npx next build` (bypassing the npm
lifecycle hook directly) still compiles the app cleanly — only `npm run
build`, the real deployment entrypoint, is gated. 6 tests
(`scripts/check-legal-pages-ready.test.mjs`) prove the detection logic
against fixture files and run the actual CLI as a subprocess against
both the real (still-pending) repo pages and a clean fixture tree.

## 1. Railway services

One Railway project, six services, sharing one GitHub repo (monorepo —
each service sets its own **Root Directory**).

| Service | Root Dir | Build | Start Command | Healthcheck | Recommended limits (CPU / RAM) | Est. monthly cost (Hobby/Starter tier) |
|---|---|---|---|---|---|---|
| `api` | `apps/api` | Nixpacks (auto: `uv sync`) | `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'` | `GET /health` | 0.5 vCPU / 512 MB | ~$5–10 |
| `frontend` | `apps/web` | Nixpacks (auto: `npm ci && npm run build`) | `npm run start -- --port $PORT` | `GET /` (via proxy.ts, passes through) | 0.5 vCPU / 512 MB | ~$5–10 |
| `listings-worker` | `apps/api` | same image as `api` | `uv run python -m app.amazon.listings_worker` | none (long-running process; see §0d) | 0.25 vCPU / 256 MB | ~$3–5 |
| `orders-worker` | `apps/api` | same | `uv run python -m app.amazon.orders_worker` | none | 0.25 vCPU / 256 MB | ~$3–5 |
| `sales-traffic-worker` | `apps/api` | same | `uv run python -m app.amazon.sales_traffic_worker` | none | 0.25 vCPU / 256 MB | ~$3–5 |
| `inventory-worker` | `apps/api` | same | `uv run python -m app.amazon.inventory_worker` | none | 0.25 vCPU / 256 MB | ~$3–5 |

**Estimated total: ~$25–45/month** on Railway's Hobby/Starter usage-based pricing for a private pilot's expected low traffic — actual cost depends on Railway's current pricing and real usage; confirm current rates before committing. The CPU/RAM figures are conservative starting points (each worker is idle between polls, never processing more than one job at a time by design — see §3/`*_sync_max_concurrent_jobs_per_organization`), configured as Railway per-service resource limits in its dashboard; raise only if the dashboard's own usage graphs show a service actually approaching its limit.

Every worker service enables `--restart-on-failure` (Railway's default
for non-cron services) — combined with each worker's own self-watchdog
(§0d), this is now the full production equivalent of the local
supervisor's hung-worker detection.

No `railway.json`/`railway.toml` files were added — Railway's per-service
**custom Start Command** (set in its dashboard) is sufficient for all
six services sharing two build roots, and avoids inventing repo-level
config I cannot validate without an actual deploy.

**Usage monitoring and spending alerts:** Railway's own per-service
dashboard already shows CPU/memory/network usage over time — no new
code needed. Two operator actions to configure once services exist
(external, not performed by this pass): (1) check each service's usage
graph during the pilot's first week to validate or revise the resource
limits above; (2) set a Railway **spending alert** (Team/Project
Settings → Usage → set a spending limit/notification threshold) at
roughly $50/month — comfortably above the ~$25–45 estimate, so it
catches a genuine runaway (an infinite retry loop, a leaked connection)
without false-alarming on normal pilot usage.

**6 services vs. 3 (reasoned analysis, not measured — do not redesign
on this estimate alone, per the governing instruction):** Railway's
Hobby/Starter tiers bill by actual resource consumption (CPU-seconds ×
allocation, memory-GB-seconds), not a flat per-service fee. Merging the
4 workers into fewer services (e.g. one process running all 4 worker
loops, or 3 services combining 2 workers each) would not reduce total
CPU/memory-time consumed — the same claim/poll loops run the same
amount either way — so it would not materially reduce Railway's
usage-based bill. What it *would* lose is the failure isolation the
governing instruction already asked to keep (item 5): today, a crash or
a stuck event loop in one worker's dependencies can only take down that
one worker (contained by `restart-on-failure` + the self-watchdog,
§0d); merging workers reintroduces one process's failure taking down
others sharing it. **Recommendation: keep 6 services for the pilot** —
the isolation benefit is concrete and already required, and the cost
saving from consolidating is not expected to be material. Revisit only
if real Railway usage data (once deployed) shows a fixed per-service
minimum/idle charge large enough to change this conclusion — that number
cannot be known without an actual deployment, which this pass does not
perform.

## 2. Required environment variables per service

### `api`
```
ASI_DB_RUNTIME_CONTEXT=api                    # NOT auto-set — main.py deliberately never sets this itself
DATABASE_URL=<Supabase pooler URL>
DB_POOL_SIZE=1
DB_MAX_OVERFLOW=0
CORS_ORIGINS=["https://app.ewiseintelligence.com"]
ALLOWED_HOSTS=["api.ewiseintelligence.com"]
SP_API_OAUTH_REDIRECT_URI=https://api.ewiseintelligence.com/api/v1/amazon/connection/callback
SP_API_PRODUCTION_APPLICATION_ID=<existing value>
SP_API_PRODUCTION_LWA_CLIENT_ID=<existing value>
SP_API_PRODUCTION_LWA_CLIENT_SECRET=<existing value, rotate per §7>
AMAZON_SECRET_BACKEND=production
AMAZON_SECRET_ENCRYPTION_KEYS={"v1":"<base64 32-byte key, generate per §8>"}
AMAZON_SECRET_ACTIVE_KEY_VERSION=v1
API_AUTH_BACKEND=cloudflare_access
CLOUDFLARE_ACCESS_TEAM_DOMAIN=<your-team>.cloudflareaccess.com
CLOUDFLARE_ACCESS_AUDIENCE=<Application Audience (AUD) tag from the Access application protecting api.*>
DEFAULT_ORGANIZATION_ID=<existing value>
SUPABASE_URL=<existing value>
SUPABASE_SERVICE_ROLE_KEY=<existing value>
OPENAI_API_KEY=<existing value>
RAINFOREST_API_KEY=<existing value>
# every other existing .env.example value carried over unchanged
```
`ASI_<WORKER>_WORKER_ENABLED` is deliberately **absent** on the `api`
service — it must never claim/process jobs itself. No Railway volume is
attached to this service — the production secret backend is Postgres,
not a file.

### each of the 4 worker services
```
ASI_<WORKER>_WORKER_ENABLED=true    # e.g. ASI_INVENTORY_WORKER_ENABLED=true — only this one worker's own flag
DATABASE_URL=<same Supabase pooler URL>
DB_POOL_SIZE=1
DB_MAX_OVERFLOW=1
AMAZON_SECRET_BACKEND=production
AMAZON_SECRET_ENCRYPTION_KEYS={"v1":"<same key as api>"}
AMAZON_SECRET_ACTIVE_KEY_VERSION=v1
SUPABASE_URL=<existing value>
SUPABASE_SERVICE_ROLE_KEY=<existing value>
```
No `API_AUTH_BACKEND`/`CLOUDFLARE_ACCESS_*` on worker services — they
serve no HTTP routes at all, so `CloudflareAccessMiddleware` (an API
concern) has nothing to protect there.
`ASI_DB_RUNTIME_CONTEXT` is **not** set here — each worker's own
`main()` sets it internally (`"listings_worker"` etc.) immediately after
confirming its own enable flag.

### `frontend` (build-time — `NEXT_PUBLIC_*` is inlined at build, not read at runtime)
```
NEXT_PUBLIC_API_BASE_URL=https://api.ewiseintelligence.com
NEXT_PUBLIC_MARKETING_HOSTS=ewiseintelligence.com,www.ewiseintelligence.com
```

## 3. Database connection budget

The shared Supabase session-mode pooler has a **hard `pool_size: 15`**
ceiling — directly observed refusing connections (`EMAXCONNSESSION`)
this session under far less load than an unbounded 5-service deploy
would produce (SQLAlchemy's own default is `pool_size=5 +
max_overflow=10` = up to 15 connections **per process**, unset).

**Final review gate, PR #28 — tightened from 11 to 9.** With the
`DB_POOL_SIZE`/`DB_MAX_OVERFLOW` values in §2:
- `api`: 1 + 0 = **1** max (was 2 + 1 = 3)
- 4 workers: 1 + 1 = 2 max each = **8** max (unchanged — see below for
  why this floor is not further reducible without a real regression)
- **Total worst case: 9 of 15** — leaves 6 of headroom for the Supabase
  dashboard's own connections and brief overlap during a rolling
  deploy, roughly double what 11-of-15 left.

**Why workers stay at 2 each, not 1:** each worker runs its liveness
heartbeat as a genuinely separate `asyncio.create_task` alongside its
own claim/poll loop (`app/amazon/worker_heartbeat.py`'s
`start_heartbeat_loop`) — each opens its own `session_scope()`
independently, so at any given moment a worker may legitimately have
two DB-bound operations in flight at once: the periodic heartbeat write
and whatever the main loop is doing. Capping a worker at exactly 1
connection (`DB_POOL_SIZE=1, DB_MAX_OVERFLOW=0`) would force the
heartbeat write to queue behind the main loop's connection for as long
as that connection is held — directly undermining the very liveness
signal `worker_heartbeat_stale_after_seconds` /
`worker_watchdog_stale_after_seconds` (§0d) exist to keep reliable, and
risking a worker being reported/treated as unavailable while it is
actually fine. `DB_POOL_SIZE=1, DB_MAX_OVERFLOW=1` keeps the steady-
state cost at one warm connection while still allowing that brief,
genuine overlap without blocking — this is the safety margin the
governing instruction's "without weakening request handling" refers to,
and 2×4=8 is treated as a floor, not a further-negotiable number.

**Why `api` can safely go to 1, not 2:** unlike a worker, the API
process has no competing background task holding a connection open —
its DB usage is purely one connection per in-flight HTTP request that
happens to touch the database (Amazon connection/listings/orders/
sales-traffic/inventory routes; plain product-lookup/AI routes never
touch it at all). At `DB_POOL_SIZE=1, DB_MAX_OVERFLOW=0`, a second
concurrent DB-touching request (e.g. two dashboard widgets fetching in
parallel) queues for the connection rather than failing — SQLAlchemy's
pool blocks the checkout (default 30s timeout) instead of erroring, so
the practical effect is a few extra milliseconds of latency on a rare
concurrency coincidence, not a dropped request. This is judged an
acceptable, explicitly-disclosed trade-off **specifically because** this
is a single-operator, low-traffic private pilot (per the governing
task's own framing) — revisit (raise `DB_POOL_SIZE` on `api` first,
recalculating this whole budget) if real post-deployment usage shows
actual queuing/timeout symptoms, rather than pre-emptively guessing.

Tune down the worker count itself (§1's 6-vs-3-service question) rather
than shrinking below this floor per-service, if the budget ever needs to
tighten further than 9.

## 4. Cloudflare DNS records

| Type | Name | Target | Proxy |
|---|---|---|---|
| CNAME | `@` (or `ewiseintelligence.com`) | Railway `frontend` service domain | Proxied |
| CNAME | `www` | Railway `frontend` service domain | Proxied |
| CNAME | `app` | Railway `frontend` service domain | Proxied |
| CNAME | `api` | Railway `api` service domain | Proxied |

Both `ewiseintelligence.com`/`www` and `app.ewiseintelligence.com` point
at the **same** Railway `frontend` service (one deployment, `proxy.ts`
splits public vs. private by Host header — §5 of the main design). TLS
is Cloudflare's own (Full or Full Strict, matching Railway's own TLS
termination) — no certificate management needed on Railway's side
beyond what it already provides.

## 5. Cloudflare Access

**Revised in pass 2**: both `app.ewiseintelligence.com/*` AND
`api.ewiseintelligence.com/*` are protected by Cloudflare Access —
superseding pass 1's "do not gate api.* at all" recommendation, which
relied on CORS as the caller boundary. Correction 2 of the governing
task was explicit that CORS is not authentication; the backend-side
`CloudflareAccessMiddleware` (§0b) is the real boundary, and Cloudflare
Access is what actually issues the JWT it verifies — so `api.*` must sit
behind an Access application for that JWT to exist on real requests at
all.

**Recommended setup: one Access application covering
`*.ewiseintelligence.com` scoped to `app.*` and `api.*` only** (not the
bare domain/`www`, which stay outside Access entirely per the public
marketing/legal requirement) — sharing one Application Audience (AUD)
tag and one login session across both hostnames, so a seller
authenticates once and both the frontend page load and every direct
browser→API call (this app's existing fetch pattern, `apps/web/src/lib/
api.ts`) carry a valid session. Set `CLOUDFLARE_ACCESS_AUDIENCE` on the
`api` Railway service to this one application's AUD tag.

**Final review gate, PR #28 — a frontend fix was required for this to
actually work, corrected in this pass.** A cross-origin `fetch()` (every
call in `api.ts` targets `apiBaseUrl()`, a different origin from the
frontend itself — true in any deployed environment and in local dev
alike) sends **no cookies at all** unless `credentials: "include"` is
set explicitly. Cloudflare Access authenticates a browser via exactly
such a session cookie; without `credentials: "include"`, a browser
already signed in on `app.ewiseintelligence.com` would still have every
one of its own API calls to `api.ewiseintelligence.com` intercepted by
Cloudflare's own login challenge instead of ever reaching the backend —
the "no frontend code change required" claim this section originally
made was wrong. Fixed: every request in `api.ts` now goes through a
single `apiFetch()` wrapper (`credentials: "include"` set in one place;
call sites unchanged, a mechanical rename) — verified by
`src/lib/api-credentials.test.ts`, which both exercises representative
exported functions against a mocked `fetch` and greps `api.ts`'s own
source for any bare `fetch(` call site that bypasses the wrapper. The
backend side of this was already correct (`CORSMiddleware`'s
`allow_credentials=True` with a non-wildcard `allow_origins` list — the
combination required for a credentialed cross-origin request to
succeed at all; see `app/main.py`'s `register_request_middleware`).

Explicit bypass rules required on this Access application (in addition
to `app.core.cloudflare_access.PUBLIC_PATHS`'s own independent,
hardcoded exemption for the same three paths — belt and suspenders,
never relying on only one side):

- `api.ewiseintelligence.com/health` — Railway's own healthcheck prober,
  and this pass's one remaining public liveness check, hit this
  directly and can never complete an Access login challenge.
- `api.ewiseintelligence.com/api/v1/amazon/connection/callback` —
  Amazon's own server redirects the seller's browser here directly
  after consent; it can never complete an Access login.
- `api.ewiseintelligence.com/api/v1/amazon/connection/login` — this
  app's registered Login URI (§0c); reached the same way.
- `ewiseintelligence.com/*` and `www.ewiseintelligence.com/*` — a
  different hostname, never touched by an Access application scoped to
  `app.*`/`api.*` in the first place; no bypass rule needed at all (the
  cleanest possible exclusion, unchanged from pass 1).

`/health/workers` is deliberately **not** a bypass rule — it now
requires a verified Access identity like any other protected route
(§0b), since it reveals internal `worker_type` names.

## 6. SP-API OAuth — exact values

**Current (temporary, `trycloudflare.com`) redirect URI**, read directly
from the live `.env`:
```
https://easy-physiology-maximize-surely.trycloudflare.com/api/v1/amazon/connection/callback
```

**New value to register**, derived from the same
`/api/v1/amazon/connection/callback` path (`apps/api/app/api/routes/
amazon_connection.py`) — never guessed:
```
https://api.ewiseintelligence.com/api/v1/amazon/connection/callback
```

Per your instruction #14: if Amazon's Seller Partner Portal accepts
multiple registered redirect URIs, **add** the new one alongside the
existing tunnel URI rather than replacing it, until the new one is
validated end-to-end (an authorization_code exchange actually
succeeding against `api.ewiseintelligence.com`). Only remove the old
`trycloudflare.com` entry after that validation. **I will not touch
the Amazon Developer Console myself** — this value is for you to enter,
and I will stop and wait for your confirmation before any live OAuth
test against the new domain.

**Login URI — real, tested value (pass 2, §0c)**, `GET
/api/v1/amazon/connection/login` (`apps/api/app/api/routes/
amazon_connection.py`), covered by `tests/test_amazon_oauth_login.py`
(6 tests: redirects into the exact same consent-URL construction as
`POST /connection/authorize`, creates a real hashed OAuth state row,
ignores any attacker-supplied query parameters, fails closed with 503
when no application id is configured):
```
https://api.ewiseintelligence.com/api/v1/amazon/connection/login
```
This is the value to enter in Amazon's Developer Console's Login URI
field — it is a genuine endpoint of this application, not a
placeholder, and behaves identically to clicking "Connect Amazon"
inside ASI's own UI.

**Frontend return URI** (where a seller lands back in the app after the
callback finishes, success or failure) — read directly from
`app.amazon.oauth_callback.frontend_connection_return_url`, called by
the callback route with `cfg.cors_origins[0]` as the origin:
```
https://app.ewiseintelligence.com/connection?amazon=success   (or ?amazon=denied / ?amazon=error)
```
This depends on `CORS_ORIGINS`'s **first** entry being
`https://app.ewiseintelligence.com` on the deployed `api` service (§2) —
worth a dedicated post-deployment check (§11) precisely because it is
derived from a list's first element rather than its own dedicated
setting; flagged here rather than silently trusted.

## 7. Amazon Ads API — future redirect URL

No Ads OAuth code exists yet (confirmed by the same audit that led to
§0c) — this is a **prediction** based on the SP-API callback's own path
convention, for you to note
for when Ads API approval/scope assignment completes and that
integration is actually built:
```
https://api.ewiseintelligence.com/api/v1/amazon/ads-connection/callback   (exact path TBD at implementation time)
```
Do **not** register this with the "EWise Ads Intelligence" LWA security
profile yet — it names a route that does not exist. Keep Ads
credentials (a separate LWA client id/secret pair under that profile)
and tokens fully separate from the SP-API ones already in `Settings` —
this repo's existing secret-reference format
(`asi/amazon/{provider}/...`) already reserves `"ADS_API"` as a
distinct provider value for exactly this separation
(`apps/api/app/amazon/secrets.py`), though the OAuth-state table's own
`provider` column currently has a hard DB `CHECK` limiting it to
`'SP_API'` only — widening that is a migration a future Ads-OAuth
implementation pass would need, not this one.

## 8. Secret inventory

| Secret | Source | Destination | Rotation |
|---|---|---|---|
| `DATABASE_URL` | Supabase dashboard (connection pooling settings) | Railway env var, all 5 backend-family services | Rotate via Supabase if the pooler password is ever exposed; update all 5 services together |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase dashboard (API settings) | Railway env var, `api` + 4 workers | Same as above |
| `SP_API_PRODUCTION_LWA_CLIENT_ID` / `_SECRET` | Amazon Seller Central Developer Console (Draft/Production app) | Railway env var, `api` only (workers resolve secrets via SecretProvider, not this pair directly — confirm this at implementation time) | Rotate if ever exposed in a log/commit; requires re-registering with Amazon |
| `OPENAI_API_KEY` | OpenAI dashboard | Railway env var, `api` only | Standard OpenAI key rotation |
| `RAINFOREST_API_KEY` | Rainforest dashboard | Railway env var, `api` only | Standard provider rotation |
| `AMAZON_SECRET_ENCRYPTION_KEYS` (pass 2, §0a) | Generated locally, once: `python3 -c "import os,base64; print(base64.b64encode(os.urandom(32)).decode())"` — 32 random bytes, base64-encoded, wrapped as `{"v1":"<value>"}` | Railway env var, `api` + 4 workers (identical value on every service — any of them may need to decrypt a row) | Rotation procedure, final review gate PR #28 — verified never leaves existing ciphertext permanently unreadable if followed in order: (1) generate a new key, add it as a new `key_version` **alongside** the old one in the JSON object — never replacing it yet, since removing a key while any row still depends on it makes that row's ciphertext permanently, cryptographically unrecoverable (there is no way to decrypt without the exact key it was encrypted with); (2) set `AMAZON_SECRET_ACTIVE_KEY_VERSION` to the new version so new writes use it; (3) re-encrypt existing rows on your own schedule via `ProductionSecretProvider.rotate_key_version()` — rows not yet rotated stay fully readable under the still-present old key the whole time; (4) **before** removing the old key from the JSON object, call `ProductionSecretProvider.count_rows_for_key_version(old_version)` and confirm it returns exactly `0` — this is the one required verification step, not optional; only once it does is the old key safe to delete from `AMAZON_SECRET_ENCRYPTION_KEYS`. See `tests/test_amazon_production_secrets.py`'s rotation tests for the exact behavior this procedure relies on. |
| Amazon seller refresh-token references | Generated per-seller during OAuth | `amazon_encrypted_secrets` (Supabase Postgres), ciphertext only — see §0a | Rotation is per-value re-encryption above; a seller can independently revoke/reauthorize from Seller Central at any time |
| `CLOUDFLARE_ACCESS_AUDIENCE` (pass 2, §0b) | Cloudflare dashboard, assigned when the Access application is created | Railway env var, `api` only | Not secret-sensitive in the same way as a credential (it identifies an application, not a key) — rotate by editing the Access application if it is ever recreated |
| `WORKER_WATCHDOG_STALE_AFTER_SECONDS` etc. | Not a secret — operational config | Railway env var, workers | N/A |

**Never committed anywhere** — confirmed: no `.env` file is tracked by
git (`.gitignore` already covers it), and this pass added no new
secret to any tracked file; every new `.env.example` addition above is
a placeholder/comment, never a real value.

## 9. Deployment procedure (once approved)

1. Generate the production secret-encryption key (§8) and note it
   somewhere secure outside this repo — needed for step 3.
2. Create the Railway project, connect the GitHub repo.
3. Create the 6 services per §1, each with its own Root Directory,
   Start Command, and the resource limits from §1.
4. Set every env var per §2 on each service (copy real values from the
   current local `.env` for anything marked "existing value"; use the
   key from step 1 for `AMAZON_SECRET_ENCRYPTION_KEYS` — never paste
   any of this into this document or any commit).
5. Deploy `api` first with `API_AUTH_BACKEND` left at its default
   (`disabled`/unset) for this initial deploy — the Cloudflare Access
   application doesn't exist yet (it is created in step 9), so every
   protected route would 401 with nothing able to issue it a valid
   token. Confirm `GET https://<railway-api-domain>/health` returns
   `200` (body `{"status": "ok"}` only) before deploying anything else.
6. Deploy the 4 workers; confirm each publishes a heartbeat (checked
   directly against the database, or temporarily via
   `GET /health/workers` on the Railway-provided domain before Access is
   enabled — see step 10).
7. Deploy `frontend` with `NEXT_PUBLIC_API_BASE_URL` pointed at the
   `api` service's Railway-provided domain (not yet the custom domain).
8. Add the 4 Cloudflare DNS records (§4) once Railway confirms each
   service's custom domain is verified.
9. Create the Cloudflare Access application covering `app.*` and `api.*`
   (§5), with the 3 bypass rules (`/health`, the callback, the login
   URI). Note its Application Audience (AUD) tag.
10. Set `API_AUTH_BACKEND=cloudflare_access` and
    `CLOUDFLARE_ACCESS_TEAM_DOMAIN`/`CLOUDFLARE_ACCESS_AUDIENCE` on the
    `api` service using that AUD tag; redeploy `api`. Re-verify
    `GET https://api.ewiseintelligence.com/health` still returns `200`
    (it must — this is the one route Access bypasses AND
    `PUBLIC_PATHS` exempts), and that `GET /health/workers` now requires
    an authenticated browser session (a bare `curl` should get `401`).
11. **Stop.** Do not touch Amazon's Developer Console yet. Confirm with
    the operator that both `https://api.ewiseintelligence.com/health`
    and `https://app.ewiseintelligence.com` resolve correctly end-to-end
    (through Cloudflare, through Access) before any further step.
12. Only after that confirmation: register the new redirect URI AND the
    new Login URI (§6) — the redirect URI alongside the existing tunnel
    one, per instruction #14, until validated — in Amazon's console,
    update `SP_API_OAUTH_REDIRECT_URI` on the `api` service to the new
    value, and validate one real OAuth round-trip end-to-end before
    removing the old tunnel redirect URI.

## 10. Rollback procedure

- **Frontend/API bad deploy:** Railway keeps prior deploy images — use
  its own "Redeploy previous version" per affected service. No DNS
  change needed (same custom domain, same service).
- **Worker bad deploy:** same per-service rollback; a worker's own
  restart-on-failure + self-watchdog (§0d) means a genuinely broken
  worker fails visibly (`/health/workers` shows it unavailable) rather
  than silently, so a bad worker deploy is detectable before it causes
  real harm.
- **DNS/Cloudflare misconfiguration:** revert the specific DNS record;
  propagation is typically fast (Cloudflare-managed). The `trycloudflare.com`
  tunnel remains available as the existing, already-working OAuth path
  throughout (§6 — never removed until the new one is validated), so
  local/dev OAuth testing is never blocked by anything in this rollout.
- **Amazon OAuth redirect/login URI change:** since the old redirect URI
  is kept registered alongside the new one until validated (§6/§9 step
  12), rolling back is simply reverting `SP_API_OAUTH_REDIRECT_URI` on
  the `api` service to the old tunnel value — no Amazon console change
  needed for *this* specific rollback, only for eventually removing the
  old URI once no longer needed. The Login URI has no "old value" to
  revert to (§0c — none existed before this pass); if it ever needs to
  stop working temporarily, the safest action is leaving the registered
  value as-is (it fails closed with a 503 if `SP_API_PRODUCTION_APPLICATION_ID`
  is ever unset, never with an unhandled error) rather than pointing
  Amazon's console at a nonexistent route again.
- **`API_AUTH_BACKEND=cloudflare_access` misconfigured or the Access
  application itself broken:** set `API_AUTH_BACKEND=disabled` on the
  `api` service and redeploy — this immediately reopens every route with
  no auth middleware at all (the pre-pass-2 behavior), unblocking the
  frontend while the Access application is fixed. Treat this as a
  temporary, monitored state, not a resting one — CORS/TrustedHost alone
  are not a substitute (§0b).
- **Secret backend misconfigured (missing/invalid
  `AMAZON_SECRET_ENCRYPTION_KEYS`/`AMAZON_SECRET_ACTIVE_KEY_VERSION`):**
  `SecretProviderFactory.create()` fails closed immediately — the `api`
  service and every worker will error on any secret operation rather
  than silently falling back to `development` or losing data. Fix the
  env var and redeploy; no data is lost or corrupted by this failure
  mode, since nothing is written until the keys are valid.
- **A bad key rotation (§8):** since the old key_version stays present
  in `AMAZON_SECRET_ENCRYPTION_KEYS` until deliberately removed, rows
  not yet rotated remain readable throughout — a rotation only affects
  rows explicitly passed to `rotate_key_version()`.

## 11. Post-deployment verification checklist

- [ ] Before deploying the frontend at all: `/privacy` and `/terms` no
      longer contain `[PENDING` (§0e) — if they still do,
      `npm run build` already refused to build; this is a prerequisite,
      not a post-deployment check.
- [ ] `GET https://api.ewiseintelligence.com/health` → `200`, body is
      exactly `{"status": "ok"}` (no `persistence` field — pass 2
      sanitized this route since it is the one public path)
- [ ] `GET https://api.ewiseintelligence.com/health/workers` with no
      Access session → `401` (pass 2 — this route now requires a
      verified identity, since it reveals worker_type names)
- [ ] `GET https://api.ewiseintelligence.com/health/workers`, from an
      authenticated browser session → all 4 worker types
      `available: true` within a few minutes of deploy
- [ ] `https://app.ewiseintelligence.com` requires a Cloudflare Access
      login, then loads the full app
- [ ] `https://ewiseintelligence.com` loads the public marketing page
      (never the full app), with no Access login required
- [ ] `https://ewiseintelligence.com/privacy` and `/terms` load without
      Cloudflare Access, from an unauthenticated/incognito session
- [ ] `https://ewiseintelligence.com/seller/inventory` (an app-only
      path) redirects to `https://app.ewiseintelligence.com/seller/inventory`,
      never serves app content on the bare domain
- [ ] `https://app.ewiseintelligence.com/*` requires Cloudflare Access
      login; `https://ewiseintelligence.com/*` does not
- [ ] CORS: a request to `api.ewiseintelligence.com` from
      `app.ewiseintelligence.com`'s origin succeeds (given a valid
      Access session); from an unrecognized origin, fails
- [ ] `GET https://api.ewiseintelligence.com/api/v1/amazon/connection/login`
      with no Access session, no cookies → `302` straight to
      `sellercentral.amazon.<tld>/apps/authorize/consent?...` (proves
      the bypass rule + `PUBLIC_PATHS` both work for the real Login URI)
- [ ] A bare `curl` (no Access session) against any other
      `/api/v1/amazon/*` route → `401`, generic body, never a stack
      trace or internal detail
- [ ] `SELECT * FROM amazon_encrypted_secrets` from the Supabase SQL
      editor shows only `reference`/`key_version`/`nonce`/`ciphertext`/
      timestamps — no plaintext column, confirming the production
      secret backend is actually the one active (not a silent fallback)
- [ ] No live Amazon OAuth attempted until §9 step 12's explicit stop
      point is reviewed with the operator

## 12. Tests run this pass

**Pass 1:**
- Backend: 1907 passed, 90 skipped (was 1897 before this pass — +10 new:
  3 DB pool-size, 2 TrustedHostMiddleware, 5 heartbeat-watchdog).
- Frontend: 218 passed (was 204 — +14 new, `public-routing.test.ts`).
- TypeScript clean, production build clean (all routes including
  `/marketing`, `/privacy`, `/terms` generate; `proxy.ts` registers
  correctly with zero deprecation warnings after switching from the
  deprecated `middleware.ts` convention).
- Lint: 36 errors — matches `origin/main` baseline exactly, zero new
  (11 real `react/no-unescaped-entities` errors found and fixed in the
  new legal-page JSX during this pass).
- No migration added. No live Amazon or AI call made during this pass.

**Pass 2 (this revision — see the final report delivered alongside this
document for the complete breakdown):**
- Backend: 1984 passed, 94 skipped (+77 net new since pass 1's 1907: 36
  production-secret-backend tests, 7 secret-migration-admin-CLI tests,
  26 Cloudflare Access tests, 3 net-new combined-middleware-stack tests
  (2 pre-existing from pass 1 in the same file), 5 OAuth-login-URI
  tests, plus small updates to 3 existing tests whose assertions named
  an exact table count/message/head-revision that necessarily changed —
  no net count change from those). 4 new disposable-PostgreSQL tests for
  migration `0018` (opt-in, skipped without
  `ASI_ALLOW_DISPOSABLE_POSTGRES=1`, accounting for the +4 skipped delta
  90→94).
- 1 new migration: `0018_amazon_encrypted_secrets`, additive-only,
  reversible (`downgrade()` drops the table — no seller data is lost by
  a downgrade since Supabase business data lives elsewhere; only
  connection/token state would need reauthorization, exactly like the
  interim-volume story pass 1 already disclosed).
- No frontend changes this pass — frontend test count unchanged from
  pass 1 (218 passed).
- No live Amazon or AI call made during this pass. No Railway,
  Cloudflare, Supabase, or Amazon console configuration performed.

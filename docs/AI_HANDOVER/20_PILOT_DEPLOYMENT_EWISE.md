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

## 0. Two blocking architectural gaps found during audit

Both are reported honestly rather than worked around silently, per the
audit-first requirement.

### 0a. Production `SecretProvider` backend does not exist

`AMAZON_SECRET_BACKEND=production` raises `SecretAccessError` — it is
explicitly reserved, unimplemented (`apps/api/app/amazon/secrets.py`,
`SecretProviderFactory.create`). The only working backend today is
`DevelopmentSecretProvider`: a plaintext JSON file at a configurable
local path (`AMAZON_DEVELOPMENT_SECRET_STORE`, default
`.data/amazon-development-secrets.json`), explicitly documented in its
own module as "not a production vault."

**This means: as shipped, there is no production-grade place to store a
seller's Amazon refresh-token reference.** Two options, requiring your
decision before any real seller is connected through the deployed
pilot:

1. **Interim, lower-effort:** keep `AMAZON_SECRET_BACKEND=development`
   (or unset) on the deployed API service, with a Railway **persistent
   volume** mounted at the `AMAZON_DEVELOPMENT_SECRET_STORE` path.
   Works, but: plaintext token storage, single API instance only (no
   horizontal scaling — two instances would each keep their own
   divergent file), and the file's own docstring already warns it was
   never designed for this. Zero new code required.
2. **Proper fix:** implement a real production `SecretProvider` backed
   by the existing Supabase Postgres (a new table, values encrypted at
   rest with a symmetric key supplied via a Railway env var — e.g.
   Fernet from the `cryptography` package), replacing the
   `raise SecretAccessError(...)` branch. This is a genuine, scoped
   feature (new module + a new migration) — **not implemented in this
   pass**, since it is a real architecture/security decision your
   review should weigh in on before any code is written, not something
   to build unilaterally while "preparing deployment configuration."

**Recommendation:** option 1 for the initial private pilot (small,
known seller count, single API instance, accepted interim risk,
explicitly disclosed to pilot sellers) with option 2 planned as a
near-term follow-up milestone before any broader rollout.

### 0b. Hung-worker detection did not transfer to Railway's per-service model — now fixed in this pass

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

### 0c. Amazon Ads OAuth Login URI — not implemented, flagged not fabricated

Confirmed directly in code and prior design docs: the SP-API "Login
URI" (the endpoint Amazon would hit if a seller started authorization
*from* Amazon's own app store) was never implemented —
`docs/milestone-12/milestone-12b1c4a-oauth-callback-foundation.md`
states this explicitly ("Login URI is not implemented"), and no
`/connection/login` route exists anywhere in `apps/api/app/api/routes/`.
This app's only working flow is **ISV-initiated** (a seller clicks
"Connect Amazon" inside ASI's own UI), which never invokes a Login URI
at all — Amazon's consent URL (`build_seller_central_consent_url`,
`apps/api/app/amazon/oauth.py`) only ever sends `application_id`,
`state`, and optional `version=beta`; no `redirect_uri` or login
callback is part of that URL.

If Amazon's Developer Console nonetheless requires *some* value in that
field for a complete SP-API application registration, I cannot report
a functioning new one — building a real Login URI handler is out of
this deployment pass's scope. See §6 for what I recommend registering
there as a safe placeholder, pending your decision.

## 1. Railway services

One Railway project, six services, sharing one GitHub repo (monorepo —
each service sets its own **Root Directory**).

| Service | Root Dir | Build | Start Command | Healthcheck | Est. monthly cost (Hobby/Starter tier) |
|---|---|---|---|---|---|
| `api` | `apps/api` | Nixpacks (auto: `uv sync`) | `uv run uvicorn app.main:app --host 0.0.0.0 --port $PORT --proxy-headers --forwarded-allow-ips='*'` | `GET /health` | ~$5–10 |
| `frontend` | `apps/web` | Nixpacks (auto: `npm ci && npm run build`) | `npm run start -- --port $PORT` | `GET /` (via proxy.ts, passes through) | ~$5–10 |
| `listings-worker` | `apps/api` | same image as `api` | `uv run python -m app.amazon.listings_worker` | none (long-running process; see §0b) | ~$3–5 |
| `orders-worker` | `apps/api` | same | `uv run python -m app.amazon.orders_worker` | none | ~$3–5 |
| `sales-traffic-worker` | `apps/api` | same | `uv run python -m app.amazon.sales_traffic_worker` | none | ~$3–5 |
| `inventory-worker` | `apps/api` | same | `uv run python -m app.amazon.inventory_worker` | none | ~$3–5 |

**Estimated total: ~$25–45/month** on Railway's Hobby/Starter usage-based pricing for a private pilot's expected low traffic — actual cost depends on Railway's current pricing and real usage; confirm current rates before committing.

Every worker service enables `--restart-on-failure` (Railway's default
for non-cron services) — combined with each worker's own self-watchdog
(§0b), this is now the full production equivalent of the local
supervisor's hung-worker detection.

No `railway.json`/`railway.toml` files were added — Railway's per-service
**custom Start Command** (set in its dashboard) is sufficient for all
six services sharing two build roots, and avoids inventing repo-level
config I cannot validate without an actual deploy.

## 2. Required environment variables per service

### `api`
```
ASI_DB_RUNTIME_CONTEXT=api                    # NOT auto-set — main.py deliberately never sets this itself
DATABASE_URL=<Supabase pooler URL>
DB_POOL_SIZE=2
DB_MAX_OVERFLOW=1
CORS_ORIGINS=["https://app.ewiseintelligence.com"]
ALLOWED_HOSTS=["api.ewiseintelligence.com"]
SP_API_OAUTH_REDIRECT_URI=https://api.ewiseintelligence.com/api/v1/amazon/connection/callback
SP_API_PRODUCTION_APPLICATION_ID=<existing value>
SP_API_PRODUCTION_LWA_CLIENT_ID=<existing value>
SP_API_PRODUCTION_LWA_CLIENT_SECRET=<existing value, rotate per §7>
AMAZON_SECRET_BACKEND=development             # see §0a — pending your decision
AMAZON_DEVELOPMENT_SECRET_STORE=/data/amazon-development-secrets.json   # on an attached Railway volume, §0a option 1
DEFAULT_ORGANIZATION_ID=<existing value>
SUPABASE_URL=<existing value>
SUPABASE_SERVICE_ROLE_KEY=<existing value>
OPENAI_API_KEY=<existing value>
RAINFOREST_API_KEY=<existing value>
# every other existing .env.example value carried over unchanged
```
`ASI_<WORKER>_WORKER_ENABLED` is deliberately **absent** on the `api`
service — it must never claim/process jobs itself.

### each of the 4 worker services
```
ASI_<WORKER>_WORKER_ENABLED=true    # e.g. ASI_INVENTORY_WORKER_ENABLED=true — only this one worker's own flag
DATABASE_URL=<same Supabase pooler URL>
DB_POOL_SIZE=1
DB_MAX_OVERFLOW=1
AMAZON_SECRET_BACKEND=development
AMAZON_DEVELOPMENT_SECRET_STORE=/data/amazon-development-secrets.json   # same shared volume as api, §0a
SUPABASE_URL=<existing value>
SUPABASE_SERVICE_ROLE_KEY=<existing value>
```
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

With the `DB_POOL_SIZE`/`DB_MAX_OVERFLOW` values in §2:
- `api`: 2 + 1 = 3 max
- 4 workers: 1 + 1 = 2 max each = 8 max
- **Total worst case: 11 of 15** — leaves headroom for the Supabase
  dashboard's own connections and brief overlap during a rolling
  deploy. Tune down further if this proves too tight in practice; never
  raise it without recalculating this budget.

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

**Policy scope: `app.ewiseintelligence.com/*` only.** Explicit
exclusions (never gated by Access):

- `ewiseintelligence.com/*` and `www.ewiseintelligence.com/*` — a
  **different hostname**, never touched by an Access policy scoped to
  `app.*` in the first place; this is the cleanest possible exclusion
  (no bypass rule needed at all).
- `api.ewiseintelligence.com/api/v1/amazon/connection/callback` — the
  registered OAuth redirect URI. Amazon's own server calls this
  directly; it can never complete a Cloudflare Access login challenge.
  **Must be an explicit bypass rule** on the `api.*` hostname if Access
  is ever applied there too (current design does not gate `api.*`
  behind Access at all — see below).
- `api.ewiseintelligence.com/health` and `/health/workers` — Railway's
  own healthcheck prober hits these directly and cannot complete an
  Access login either.

**Recommendation: do not put `api.ewiseintelligence.com` behind
Cloudflare Access at all.** The backend has no user-facing browser UI of
its own — every legitimate caller is either the `app.*` frontend (via
CORS, already restricted to that one origin) or Amazon's own OAuth
callback. Gating it with Access would require bypass rules for the
callback and every health-check path, widening the exclusion surface
for no real benefit; CORS + TrustedHostMiddleware (§ code changes,
already implemented) already scope who can call it meaningfully. Apply
Access to `app.ewiseintelligence.com/*` only.

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

**Login URI:** no functioning value exists to report (§0c). If Amazon's
console requires a non-empty value, the safest **placeholder**
recommendation — pending your decision — is
`https://app.ewiseintelligence.com/connection` (the existing "Connect
Amazon" page a human would land on), clearly understood to not
implement Amazon's own Login-URI-initiated handshake. Do not register
this as if it were a working integration.

## 7. Amazon Ads API — future redirect URL

No Ads OAuth code exists yet (§0c audit) — this is a **prediction**
based on the SP-API callback's own path convention, for you to note
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
| Amazon seller refresh-token references | Generated per-seller during OAuth | `DevelopmentSecretProvider`'s JSON file on a Railway volume (§0a interim) | No rotation mechanism exists yet beyond a seller re-authorizing from Seller Central |
| `WORKER_WATCHDOG_STALE_AFTER_SECONDS` etc. | Not a secret — operational config | Railway env var, workers | N/A |

**Never committed anywhere** — confirmed: no `.env` file is tracked by
git (`.gitignore` already covers it), and this pass added no new
secret to any tracked file; every new `.env.example` addition above is
a placeholder/comment, never a real value.

## 9. Deployment procedure (once approved)

1. Create the Railway project, connect the GitHub repo.
2. Create the 6 services per §1, each with its own Root Directory and
   Start Command.
3. Set every env var per §2 on each service (copy real values from the
   current local `.env` for anything marked "existing value" — never
   paste them into this document or any commit).
4. Attach a Railway persistent volume to the `api` service (and each
   worker, if §0a option 1 is confirmed) at the
   `AMAZON_DEVELOPMENT_SECRET_STORE` path.
5. Deploy `api` first; confirm `GET https://<railway-api-domain>/health`
   returns `200` before deploying anything else.
6. Deploy the 4 workers; confirm each publishes a heartbeat via
   `GET /health/workers` on the `api` service.
7. Deploy `frontend` with `NEXT_PUBLIC_API_BASE_URL` pointed at the
   `api` service's Railway-provided domain (not yet the custom domain).
8. Add the 4 Cloudflare DNS records (§4) once Railway confirms each
   service's custom domain is verified.
9. Add Cloudflare Access to `app.ewiseintelligence.com/*` only (§5).
10. **Stop.** Do not touch Amazon's Developer Console yet. Confirm with
    the operator that `https://api.ewiseintelligence.com/health`
    resolves correctly end-to-end (through Cloudflare) before any
    further step.
11. Only after that confirmation: register the new redirect URI (§6)
    alongside the existing tunnel one in Amazon's console, update
    `SP_API_OAUTH_REDIRECT_URI` on the `api` service to the new value,
    and validate one real OAuth round-trip end-to-end before removing
    the old tunnel URI.

## 10. Rollback procedure

- **Frontend/API bad deploy:** Railway keeps prior deploy images — use
  its own "Redeploy previous version" per affected service. No DNS
  change needed (same custom domain, same service).
- **Worker bad deploy:** same per-service rollback; a worker's own
  restart-on-failure + self-watchdog (§0b) means a genuinely broken
  worker fails visibly (`/health/workers` shows it unavailable) rather
  than silently, so a bad worker deploy is detectable before it causes
  real harm.
- **DNS/Cloudflare misconfiguration:** revert the specific DNS record;
  propagation is typically fast (Cloudflare-managed). The `trycloudflare.com`
  tunnel remains available as the existing, already-working OAuth path
  throughout (§6 — never removed until the new one is validated), so
  local/dev OAuth testing is never blocked by anything in this rollout.
- **Amazon OAuth redirect URI change:** since the old URI is kept
  registered alongside the new one until validated (§6/§9 step 11),
  rolling back is simply reverting `SP_API_OAUTH_REDIRECT_URI` on the
  `api` service to the old tunnel value — no Amazon console change
  needed for *this* specific rollback, only for eventually removing the
  old URI once no longer needed.
- **Secret-backend interim volume (§0a option 1) lost/corrupted:** every
  connected seller would need to re-authorize from Seller Central — no
  ASI business data is lost (that lives in Supabase, separate from the
  volume), only the connection/token state.

## 11. Post-deployment verification checklist

- [ ] `GET https://api.ewiseintelligence.com/health` → `200`,
      `persistence: "configured"`
- [ ] `GET https://api.ewiseintelligence.com/health/workers` → all 4
      worker types `available: true` within a few minutes of deploy
- [ ] `https://app.ewiseintelligence.com` loads the full app
- [ ] `https://ewiseintelligence.com` loads the public marketing page
      (never the full app)
- [ ] `https://ewiseintelligence.com/privacy` and `/terms` load without
      Cloudflare Access, from an unauthenticated/incognito session
- [ ] `https://ewiseintelligence.com/seller/inventory` (an app-only
      path) redirects to `https://app.ewiseintelligence.com/seller/inventory`,
      never serves app content on the bare domain
- [ ] `https://app.ewiseintelligence.com/*` requires Cloudflare Access
      login; `https://ewiseintelligence.com/*` does not
- [ ] CORS: a request to `api.ewiseintelligence.com` from
      `app.ewiseintelligence.com`'s origin succeeds; from an
      unrecognized origin, fails
- [ ] No live Amazon OAuth attempted until §9 step 10's explicit stop
      point is reviewed with the operator

## 12. Tests run this pass

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

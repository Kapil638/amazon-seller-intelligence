# 25 August 2026 — Production Connect Amazon (US)

**Date:** 25 August 2026  
**Scope:** Local Connect Amazon against the Draft/Production EWise app for a US Professional seller  
**Status:** Uncommitted working-tree changes on `main` (not a new milestone). 12B.1D handshake behaviour is unchanged. **12B.2 is not started.**

This note records what we changed today and why. It is not a product spec and it does not replace `CLAUDE.md` or `docs/AI_HANDOVER/`.

---

## Why this work happened

The Connection page showed **Sandbox / EU / Amazon.in** while connecting a **Production** app and a **US** seller. Those labels were defaults and leftover Sandbox rows, not the Draft app.

Seller Central Allow then returned to ASI at **Pending validation**. That is the designed 12B.1D split: OAuth callback does **not** call SP-API. A later handshake (`POST /connection/test`) marks `connected`.

After refresh, **Last error** was `secret_access_failed`. The refresh token lived only in API memory. Uvicorn `--reload` wiped it. Postgres still had `pending_validation` + `token_reference`, so handshake could not read the grant.

A later Allow + Validate succeeded. Live local state at session end:

| Field | Value |
| --- | --- |
| Environment | Production |
| Region | NA (US) |
| Marketplace | amazon.com |
| Status | Connected |
| Authorized / validated | 25 Aug 2026, 11:48 am (local) |

ASI still does **not** ingest listings, orders, inventory, reports, finances, or Ads.

---

## Correct Connect Amazon process

```text
Connect Amazon (PRODUCTION)
  → Seller Central Allow
  → GET /callback (LWA code exchange, store refresh via SecretProvider)
  → pending_validation
  → Validate connection  (POST /connection/test handshake)
  → connected | degraded | error / requires_reauth
```

1. **Connect Amazon** — leave ASI, Allow in Seller Central (`sellercentral.amazon.com` for NA).
2. Return to ASI — **Pending validation** is expected. Authorized at is set. Status is **not** Connected yet.
3. **Validate connection** — Sellers `GET /sellers/v1/marketplaceParticipations`. Success → Connected.
4. If **Last error** is `secret_access_failed` — the grant token was lost. Click **Connect Amazon again**, Allow once more, then Validate. The old token cannot be recovered.

**Test Connection** on the lower card remains the sandbox credential check. Do not use it as the seller-grant step when the top card is Pending validation.

---

## Behaviour changes

### Connect Amazon is Production / NA / amazon.com

Previously hardcoded or defaulted to Sandbox + EU + amazon.in.

- `POST /connection/authorize` defaults to **PRODUCTION**.
- Frontend always calls `authorizeAmazonConnection("PRODUCTION")`.
- Settings default `SP_API_REGION` is **`na`** (was `eu`).
- Connection display/consent marketplace follows region: `na`/`us` → `amazon.com`, `eu` → `amazon.in`, `fe` → `amazon.co.jp`.
- GET `/connection` prefers the **PRODUCTION** row. Leftover **SANDBOX** Test Connection rows are not shown on the seller-authorization card.
- Pending rows now persist `region` and `application_id` when those fields change.
- Handshake tries the PRODUCTION token row first, then SANDBOX.

Sandbox LWA + sandbox refresh token remain **Test Connection** only. Draft/Production LWA + application id remain **Connect Amazon** only. Do not mix those credential sets.

### Development secrets survive API reload

`DevelopmentSecretProvider` writes seller refresh tokens to a **gitignored local file** (default `.data/amazon-development-secrets.json`). Empty `AMAZON_DEVELOPMENT_SECRET_STORE` keeps in-memory-only behaviour.

- Not Postgres. Not a production vault. Not frontend/Copilot/EvidenceEnvelope.
- `AMAZON_SECRET_BACKEND=production` still fails closed.
- `.gitignore` includes `.data/` and `*.amazon-development-secrets.json`.

The 10:44 grant that hit `secret_access_failed` was already gone before file persistence existed.

### Connection UI

- Production / NA (US) / Amazon.com fallbacks when no PRODUCTION row exists.
- After Allow: **Validate connection** (not “wait for sandbox”).
- After `secret_access_failed`: **Connect Amazon again** and copy that the stored grant cannot be read.
- After successful handshake, the page reloads overview so status can show **Connected**.
- Future-connections SP-API chip: “Seller ingest not started” (no longer “Status: Sandbox”).

---

## Files touched

| Area | Files |
| --- | --- |
| Connection service / OAuth | `apps/api/app/amazon/connection.py`, `oauth.py`, `sandbox.py` |
| Secrets | `apps/api/app/amazon/secrets.py`, `apps/api/app/core/config.py` |
| HTTP | `apps/api/app/api/routes/amazon_connection.py` |
| Persistence | `apps/api/app/persistence/repositories.py` (`region`, `application_id` on lifecycle update) |
| Frontend | `apps/web/src/components/amazon-connection.tsx`, `apps/web/src/lib/api.ts` |
| Config examples | `apps/api/.env.example`, `.gitignore`, `docs/marketplace.md` |
| Tests | Amazon connection / OAuth / seller-validation / secret-provider tests; `amazon-connection-ui.test.tsx`; `conftest.py` |

No new Alembic migration. Head remains `0008_amazon_oauth_states`. No Copilot, Skills, Ads, or listing-engine changes.

---

## What did not change

- Rainforest is still the public marketplace source.
- Tenancy is still `organization_id`.
- Refresh/access tokens are still not in business tables or API JSON.
- Callback still does not call SP-API.
- `connected` still requires validation success.
- Listings / orders / inventory / reports / finances ingest still not started (**12B.2+**).
- Ads API still **12C**.
- Production SecretProvider cloud backend still unimplemented.
- Website OAuth Login URI handling is still incomplete. Live Amazon consent still needs HTTPS Redirect URI (localhost is rejected).

---

## Local follow-up (operator)

- Do not commit `apps/api/.env` or `.data/amazon-development-secrets.json`.
- After API or tunnel restart, confirm `SP_API_OAUTH_REDIRECT_URI` still matches Seller Partner Portal.
- Hard-refresh `/connection` if the tab still shows Sandbox or `secret_access_failed` from an earlier grant.

---

## Next approved milestone

**12B.2 — Canonical Seller Identity + Marketplace Ingestion**

Do not start listings ingest, orders, Ads, Copilot Amazon tools, or Skills as 12B.2. First Claude action remains the architecture-validation report in `docs/AI_HANDOVER/17_CLAUDE_START_HERE.md`, unless the user explicitly starts that slice.

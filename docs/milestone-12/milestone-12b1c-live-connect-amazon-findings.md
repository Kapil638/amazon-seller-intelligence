# 12B.1C — Live Connect Amazon findings

**Date:** 23–24 August 2026 (investigation)  
**Status:** Investigation notes from local Connect Amazon testing. Kept as immutable POC history.

**Code update (24 August 2026):** 12B.1C.4A, 12B.1C.5, and 12B.1D later landed in the repository. Callback, LWA exchange, SecretProvider storage, and Sellers validation **exist in code**. The live Amazon website round-trip (Login URI, exact HTTPS redirect, seller consent) remains **incomplete**. Do not rewrite the findings below as if the investigation never happened, and do not pretend live authorization is production-tested.

**Architecture:** [milestone-12b1c-amazon-seller-authorization-architecture.md](milestone-12b1c-amazon-seller-authorization-architecture.md)  
**Implemented (code):** [12B.1C.2](milestone-12b1c2-authorize-start-oauth-state.md), frontend Connect Amazon (12B.1C.3), [12B.1C.4A](milestone-12b1c4a-oauth-callback-foundation.md), [12B.1C.5](milestone-12b1c5-lwa-token-exchange.md), [12B.1D](milestone-12b1d-seller-connection-validation.md)

This note records what happened when Connect Amazon was tried against real Seller Central / Solution Provider Portal identities. It does not change product code. Do not treat sandbox Test Connection, SPP developer login, or Seller Central dashboard login as a completed seller grant.

Amazon docs referenced during diagnosis: [Website Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/website-authorization-workflow), [Step 6: Set up the Authorization Workflow](https://developer-docs.amazon.com/sp-api/docs/onboarding-step-6-set-up-the-authorization-workflow), [Authorization errors](https://developer-docs.amazon.com/sp-api/docs/authorization-errors) (`MD9100`, missing OAuth setup).

---

## 1. What ASI can and cannot do today

| Step | Status |
| --- | --- |
| `POST /api/v1/amazon/connection/authorize` | Implemented. Creates hashed OAuth state, sets connection `pending_authorization`, returns a Seller Central consent URL. Does not HTTP-redirect. |
| Frontend **Connect Amazon** / **Continue Amazon authorization** | Implemented. Navigates the browser to `authorization_url`. Does not store URL or `state` in React. |
| Seller Central **Authorize EWise** consent page | Amazon-owned. ASI cannot render it. |
| Amazon Login URI + Redirect URI handlers | **Not implemented** (12B.1C.4). |
| Authorization-code exchange, `put_secret`, `token_reference` | **Not implemented** (12B.1C.5–6). |
| Mark `connected` via Sellers handshake | **Not implemented** (12B.1D). |

Expected machine until callback exists:

```text
Connect / Continue
  → pending_authorization
  → (Amazon consent — not observed in this test)
  → ASI callback — not built
  → pending_validation / connected — cannot happen yet
```

`GET /connection` does not call Amazon. Reloading ASI, finishing Amazon login, or returning to `/connection` by hand cannot move status off `pending_authorization`.

---

## 2. Identities involved (do not mix)

Three Amazon identities were used in this test. They are not interchangeable.

| Identity | What it proves | What it does not prove |
| --- | --- | --- |
| Solution Provider Portal (developer, India) | EWise exists; SP-API app credentials; sandbox Test Connection | A Professional selling account; consent |
| SP-API / LWA credentials in local `.env` (client id, secret, sandbox refresh token, application id) | Developer can call sandbox SP-API | Seller Central login; seller grant |
| US Professional Seller Central (**Snark Totes**, United States, `sellercentral.amazon.com`) | The selling partner who must click Allow | That EWise was authorized; that ASI received a code |

India developer registration plus a US seller account is **valid**. Public SP-API apps are authorized by the **seller’s** Seller Central store, not by the developer’s country. Consent for this seller must use `sellercentral.amazon.com` (NA), not `sellercentral.amazon.in`.

OTP on an Amazon login only proves that Amazon account exists. It does not prove a Professional selling account on that store.

---

## 3. Timeline of what we saw

### 3.1 Local blockers (23 August)

1. Postgres was behind on Alembic; `GET /connection` failed until `alembic upgrade head` (through `0008_amazon_oauth_states`).
2. `SP_API_APPLICATION_ID` was missing → authorize returned 503 until it was set. EWise in SPP is **Sandbox**, application id format `amzn1.sp.solution.…` (not legacy `amzn1.sellerapps.app.…`). Amazon accepted that id on the consent URL with `version=beta`.
3. Uvicorn `--reload` does not pick up `.env` until a Python file changes; settings were reloaded by touching `app/core/config.py`.

Authorize then returned 200 with host `sellercentral.amazon.in` (product default marketplace `amazon.in`). Browser reached Amazon Sign In.

### 3.2 Wrong Amazon session: “Apply to sell”

The SPP / shopping Amazon email is **not** a US Professional seller. After OTP, Seller Central showed **Not Authorized** / “this email is not associated with any accounts,” then **Register** / **Apply**.

Later, with cookies still present, `https://sellercentral.amazon.com` in a **normal** browser opened seller **onboarding**:

- URL: `sellercentral.amazon.com/mario/rfb/orbis-agreements/...`
- Copy: “You are applying to sell in the United States Amazon store (Amazon.com).”
- CTA: **Agree and continue** (0% complete)

The same URL in a **private** window went to **Login**, because there was no session.

After clearing browsing data, the normal browser landed on the correct Seller Central surface (login or the existing seller account), not the apply funnel.

**Do not click Agree and continue / Sign up** to finish Connect Amazon. That would start a new seller account. Use **Log in** with the Professional seller email.

Public `sellercentral.amazon.com` marketing homepage (**Create an Amazon selling account** / **Sign up**) is also the new-seller funnel. Use the small **Log in** control, not Sign up.

### 3.3 Consent host switched to US

Local override (not a product V1 marketplace change):

- `SP_API_OAUTH_CONSENT_BASE_URL=https://sellercentral.amazon.com`
- `SP_API_CONSENT_VERSION_BETA=true`
- `SP_API_REGION=na` (settings only; see §5)

Authorize then built consent on **US** Seller Central. Listing intelligence `DEFAULT_MARKETPLACE` remained `amazon.in`.

### 3.4 Snark Totes login without Authorize EWise (24 August)

While logged in as **Snark Totes** / United States, **Continue Amazon authorization** sent the browser to Seller Central. After login the address bar was:

`https://sellercentral.amazon.com/amazonsell/business`

That is the seller **My business** dashboard (healthy account, US store), **not** `/apps/authorize/consent`. There was no Authorize EWise / Allow screen, and Amazon did not return the browser to ASI.

ASI `/connection` still showed **Pending authorization**. That is consistent with §1: no grant reached ASI, and no callback exists.

---

## 4. Why ASI stays Pending

Pending is set **when Connect / Continue is clicked**, before Amazon shows consent.

It stays Pending because:

1. The seller did not complete **Authorize EWise** (Amazon never showed that page in this test).
2. Even after Allow, Amazon only returns to the **OAuth Redirect URI** registered on the app. ASI has no `GET /connection/callback` (12B.1C.4), so the browser cannot be sent to `/connection?amazon=success` and ASI cannot store a grant.

Pending is not a UI bug and is not cleared by a successful Seller Central dashboard login.

---

## 5. Why the Connection card shows Amazon.in and EU

| Field on `/connection` | Source | Meaning in this test |
| --- | --- | --- |
| Marketplace **Amazon.in** | `settings.default_marketplace` (`amazon.in`) | Rainforest / listing-intelligence default. **Not** the seller store being authorized. |
| Region **EU** | `amazon_connections.region` on the **existing** SANDBOX row | Row was created when `SP_API_REGION` was still `eu`. `_ensure_pending_connection` does not rewrite region on a row that is already `pending_authorization`. |
| Environment **Sandbox** | Authorize body / saved row | Draft/sandbox app testing (`version=beta`). |
| Persisted status **PENDING_AUTHORIZATION** | Saved row | Consent started; no secret. |

Consent URL origin is **not** taken from the Amazon.in label. With the local override it is `https://sellercentral.amazon.com`.

---

## 6. Why Authorize EWise never appeared

ASI’s frontend only navigates if the URL is HTTPS Seller Central, path `/apps/authorize/consent`. The API builds:

```text
https://sellercentral.amazon.com/apps/authorize/consent
  ?application_id={EWise id}
  &state={raw}
  &version=beta
```

`version=beta` is required while the app is draft/sandbox. Redirect URI is **not** added to this URL (Amazon documents it on app registration and on the later callback, not on this consent URI).

After Amazon login, the **new** Seller Central shell (`/amazonsell/business`) dropped that consent URL and opened the dashboard. ASI cannot prevent that.

Most likely Amazon-side cause: **the current EWise client is Sandbox**, so Solution Provider Portal **does not show OAuth Login URI / Redirect URI** on Edit App. Sandbox Edit App is only name + API type. **View sandbox credentials** and **Create Token** are LWA sandbox credentials / self-token, not website OAuth.

OAuth URIs appear only when registering a **Production** app client (**Add new app client** → Application type **Production** → public app → Login URI + Redirect URI). That production client starts in **Draft**; website consent then uses that application id plus `version=beta`. Amazon does not accept `localhost` redirect URIs (HTTPS required).

Amazon typically requires **HTTPS** for those URIs and rejects `http://localhost`. Local callback testing will need a public HTTPS URL (or equivalent) when 12B.1C.4 is built.

Secondary cause: after a login interstitial, Amazon often does not resume `/apps/authorize/consent` and sends the session to seller home. Retry **Continue** while **already** logged in as Snark Totes; watch the address bar. Consent should stay on `/apps/authorize/consent`. A jump to `/amazonsell/business` means Amazon still refused or abandoned consent.

Self-authorization in SPP (**Authorize** next to Edit App) is for **private** applications only. EWise is being tested as a public/sandbox website-authorization app; that SPP self-auth path is not the V1 seller Connect flow.

---

## 7. What is fine vs what is blocked

**Fine**

- India SPP developer + US Professional seller (Snark Totes).
- Using US Seller Central for this seller, even though ASI V1 listing default is Amazon.in.
- ASI remaining Pending after dashboard login.
- Sandbox Test Connection using developer `.env` refresh token (does **not** persist `connected`).

**Blocked (Amazon + missing slice)**

- No Authorize EWise screen in this test.
- No return to `localhost:3000/connection`.
- No path to `connected`.

**Do not**

- Complete **Apply to sell** / **Agree and continue** as a workaround.
- Treat SPP or LWA app credentials as Seller Central login.
- Treat `/amazonsell/business` as successful app authorization.
- Implement 12B.1C.4 until explicitly approved.

---

## 8. Next actions (when continuing)

1. Do not expect OAuth URI fields on the existing **Sandbox** EWise client. For website consent, use the **Production** (Draft) app client and set **OAuth Login URI** / **Redirect URI** on that row (HTTPS, not localhost). ASI Connect uses `SP_API_PRODUCTION_APPLICATION_ID`, not the sandbox id. See [sandbox vs production credential split](milestone-12b1c-sandbox-production-credential-split.md).
2. Stay logged in as Snark Totes (United States). Click **Continue Amazon authorization**. Confirm the address bar stays on `/apps/authorize/consent` and Allow appears.
3. Approve **12B.1C.4** before expecting ASI to leave Pending or to redirect to `/connection?amazon=…`.
4. Optional later cleanup (not required for consent): persist NA region on the SANDBOX connection row; stop displaying listing `amazon.in` as if it were the seller store.

---

## 9. Local config notes (no secrets)

Consent host override used for this US seller test lives in `apps/api/.env` (not committed):

- `SP_API_OAUTH_CONSENT_BASE_URL=https://sellercentral.amazon.com`
- `SP_API_CONSENT_VERSION_BETA=true`
- `SP_API_REGION=na` (new rows only; existing pending row stayed `eu`)
- `DEFAULT_MARKETPLACE` still `amazon.in`

Do not commit `.env`. Do not log raw OAuth `state`, authorization codes, or tokens.

---

## 10. Production Draft app (24 August, later)

A second EWise client was created in SPP:

| Client | Status | Application id | Use |
| --- | --- | --- | --- |
| EWise | Sandbox | `amzn1.sp.solution.d7f85703-3883-49aa-a6d8-87880b4a6f41` | Sandbox Test Connection / sandbox LWA |
| EWise | Draft | `amzn1.sp.solution.59bb7b37-e1b7-4358-9e27-37f6e8202221` | Website consent (`version=beta`) |

Draft LWA was briefly pasted over the sandbox keys (mixed). Sandbox `.env` was restored, then separate `SP_API_PRODUCTION_*` keys were added. See [sandbox vs production credential split](milestone-12b1c-sandbox-production-credential-split.md).

`SP_API_OAUTH_REDIRECT_URI` is still unset in `.env` (unused until 12B.1C.4). OAuth Login URI and Redirect URI are set on **Edit App of the Draft row**, not the Sandbox row. Amazon still does not accept `localhost` redirect URIs.

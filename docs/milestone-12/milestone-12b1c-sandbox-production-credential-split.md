# 12B.1C — Separate sandbox and production SP-API credentials

**Date:** 24 August 2026  
**Status:** Local config + small authorize wiring. Not 12B.1C.4.  
**Context:** [Live Connect Amazon findings](milestone-12b1c-live-connect-amazon-findings.md)  
**Architecture:** [12B.1C seller authorization](milestone-12b1c-amazon-seller-authorization-architecture.md)

This note records why sandbox and Draft/production Amazon credentials were split, and what changed in code and `.env`. No secrets, authorization codes, or raw OAuth `state` are documented here.

---

## Reason

ASI had a **single** set of SP-API settings:

- `SP_API_APPLICATION_ID`
- `SP_API_LWA_CLIENT_ID`
- `SP_API_LWA_CLIENT_SECRET`
- `SP_API_SANDBOX_REFRESH_TOKEN`

Those were created for **12A.0 Test Connection** against the **Sandbox** EWise client in Solution Provider Portal.

Connect Amazon (website authorization) cannot use that Sandbox client. SPP does not show **OAuth Login URI** / **OAuth Redirect URI** on Sandbox Edit App. A second EWise client was created as **Production**, which Amazon lists as **Draft**. That client has a different application id and different LWA client id/secret.

Pasting Draft LWA into the same keys as the sandbox refresh token **mixes two Amazon apps**:

| Mix | What breaks |
| --- | --- |
| Draft LWA + sandbox refresh token | Test Connection / LWA token refresh fails (`invalid_client` / `invalid_grant`) |
| Sandbox application id on the consent URL | Seller Central will not show **Authorize EWise** for the Draft app |
| Draft application id with sandbox LWA later | Token exchange (12B.1C.5) would use the wrong client |

Two SPP clients must stay two local credential sets.

| SPP client | Status | Application id | Local use |
| --- | --- | --- | --- |
| EWise | Sandbox | `amzn1.sp.solution.d7f85703-3883-49aa-a6d8-87880b4a6f41` | Test Connection |
| EWise | Draft | `amzn1.sp.solution.59bb7b37-e1b7-4358-9e27-37f6e8202221` | Connect Amazon consent (`version=beta`) |

India developer registration vs US seller (Snark Totes) is unrelated to this split. This split is **app client** (sandbox vs Draft), not marketplace.

---

## What changed

### Local `.env` (not committed)

Sandbox block **kept** as restored:

- `SP_API_APPLICATION_ID` — sandbox app id
- `SP_API_LWA_CLIENT_ID` / `SP_API_LWA_CLIENT_SECRET` — sandbox LWA
- `SP_API_SANDBOX_REFRESH_TOKEN` — sandbox refresh token

Production / Draft block **added**:

- `SP_API_PRODUCTION_APPLICATION_ID`
- `SP_API_PRODUCTION_LWA_CLIENT_ID`
- `SP_API_PRODUCTION_LWA_CLIENT_SECRET`

Do not copy Draft values into the sandbox keys, or sandbox values into the `SP_API_PRODUCTION_*` keys.

An earlier rewrite briefly cleared sandbox LWA while moving Draft values into production keys. That was reverted by restoring sandbox `.env`, then production keys were **appended** so sandbox was not overwritten again.

### `apps/api/.env.example`

Documents the production key names. Values stay empty. Reminds not to mix sandbox and Draft LWA.

### `apps/api/app/core/config.py`

New settings:

- `sp_api_production_application_id`
- `sp_api_production_lwa_client_id`
- `sp_api_production_lwa_client_secret`

`Settings.consent_application_id()` returns the Draft/production application id when set, otherwise the sandbox `sp_api_application_id` (tests and older env files).

### `apps/api/app/amazon/connection.py`

`start_authorization()` builds the Seller Central consent URL with `cfg.consent_application_id()`, not the sandbox id.

Test Connection is unchanged: it still uses sandbox `sp_api_lwa_client_id` / `sp_api_lwa_client_secret` / `sp_api_sandbox_refresh_token`.

Production LWA client id/secret are **stored for 12B.1C.5** (authorization-code exchange). They are not placed on the consent URL. Amazon’s consent query is still `application_id`, `state`, and optional `version=beta`.

### Tests

`tests/test_amazon_oauth_authorize.py`:

- Default authorize fixtures set `sp_api_production_application_id=""` so local `.env` cannot leak a Draft id into pytest.
- New case: when both ids are set, the consent URL uses the production application id.

---

## What this does not do

- Does not implement **12B.1C.4** (Login URI + callback). ASI still stays `pending_authorization` after Seller Central login.
- Does not register OAuth Login URI / Redirect URI in SPP. Those fields exist only on **Edit App of the Draft row**, must be HTTPS, and must not be `localhost`.
- Does not mark the seller `connected`.
- Does not change Rainforest `DEFAULT_MARKETPLACE` (`amazon.in`) or rewrite the saved connection row’s region (`eu` leftover).

---

## How to use

| Action | Credentials |
| --- | --- |
| **Test Connection** on `/connection` | Sandbox LWA + sandbox refresh token |
| **Connect / Continue Amazon authorization** | Draft `SP_API_PRODUCTION_APPLICATION_ID` on the consent URL |

After changing `.env`, restart the API or touch a Python file so uvicorn `--reload` rereads settings.

---

## Still blocked for live consent

Amazon will not show **Authorize EWise** until the Draft app has OAuth Login URI and Redirect URI in SPP. After Allow, the browser still will not return to ASI until 12B.1C.4 exists.

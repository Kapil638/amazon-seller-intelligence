# Milestone 12A.0 — SP-API Sandbox Connectivity Proof

**Date:** 22 August 2026  
**Status:** Implemented. Connectivity only.  
**Depends on:** Pre–Amazon API checkpoint (`13a91c2` / `pre-amazon-api-data-backbone`).

Prove that the ASI backend can exchange an EWise **sandbox refresh token** for an LWA access token and call one SP-API **static sandbox** Sellers operation. This is not ingestion, not Copilot, not production OAuth, and not Ads API.

Sandbox payload is **mocked Amazon test data**. It must not be interpreted as a real seller account. This milestone does not persist seller operational data, does not connect SP-API to Copilot, does not implement production seller authorization, and does not implement Ads API.

---

## 1. Objective

EWise sandbox credentials → LWA access token → `getMarketplaceParticipations` on the EU static sandbox → typed Pydantic parse → provenance record → automated offline tests.

---

## 2. Architecture

Isolated package `app.amazon`. Not registered in ToolRegistry. Not imported by Copilot, Profit, Advertising, Listing, or Skills.

```text
LwaClient
  POST https://api.amazon.com/auth/o2/token
  grant_type=refresh_token
        ↓
short-lived access token (in memory only)
        ↓
AmazonSpApiSandboxClient
  GET {sandbox_host}/sellers/v1/marketplaceParticipations
  header x-amz-access-token
        ↓
Sellers v1 DTOs + SpApiSandboxProvenance
```

Developer entry point (not a public REST route):

```text
cd apps/api
uv run python -m app.amazon
```

---

## 3. Environment variables

Configured via existing `Settings` (`apps/api/.env`, gitignored). Placeholders in `apps/api/.env.example`:

| Variable | Purpose |
| --- | --- |
| `SP_API_SANDBOX_ENABLED` | Must be `true` for the manual CLI proof |
| `SP_API_LWA_CLIENT_ID` | LWA client identifier (EWise) |
| `SP_API_LWA_CLIENT_SECRET` | LWA client secret |
| `SP_API_SANDBOX_REFRESH_TOKEN` | Sandbox refresh token from Solution Provider Portal |
| `SP_API_REGION` | Default `eu` (India is in the Europe selling region) |
| `SP_API_SANDBOX_BASE_URL` | Optional override of the sandbox host |
| `SP_API_LWA_TOKEN_URL` | Default `https://api.amazon.com/auth/o2/token` |

Obtain the sandbox refresh token in Seller Central / Solution Provider Portal:

EWise → Action → Create Token → Sandbox Testing → Create Token

Do not paste secrets into git, chat, or the frontend.

---

## 4. Authentication flow

Current Amazon documentation ([Connect to the SP-API](https://developer-docs.amazon.com/sp-api/docs/connecting-to-the-selling-partner-api), retrieved 22 August 2026):

1. `POST /auth/o2/token` with `grant_type=refresh_token`, `refresh_token`, `client_id`, `client_secret`.
2. Receive `access_token`, `token_type`, `expires_in`. Token is **not** stored in the database.
3. Call SP-API with `x-amz-access-token`, `x-amz-date`, and `user-agent`.

`getMarketplaceParticipations` is not a restricted operation. **RDT is not used.** AWS SigV4 is **not** required by the current connect documentation for this call.

`client_credentials` is not used: Sellers operations require a selling-partner refresh token.

---

## 5. Chosen Sellers API operation

| Field | Value |
| --- | --- |
| API | Sellers API v1 |
| Operation | `getMarketplaceParticipations` |
| Method / path | `GET /sellers/v1/marketplaceParticipations` |
| Parameters | none (static fixture `request.parameters` is `{}`) |
| Model | [sellers-api-model/sellers.json](https://github.com/amzn/selling-partner-api-models/blob/main/models/sellers-api-model/sellers.json) `info.version` = `v1` |
| Sandbox | Static only (`x-amzn-api-sandbox.static`) |

Fixture response (Amazon mock, not real seller data) includes marketplace `ATVPDKIKX0DER` / Amazon.com / `BestSellerStore`. That is the documented static example, not an India production account.

---

## 6. Sandbox endpoint / region

India (`amazon.in`) is in Amazon’s **Europe** selling region.

Default host: `https://sandbox.sellingpartnerapi-eu.amazon.com`

Override with `SP_API_SANDBOX_BASE_URL` if Amazon’s published host changes.

---

## 7. Typed DTOs

`app.amazon.models` (external-provider layer only):

- `LwaTokenResponse` (`access_token` is `SecretStr`)
- `Marketplace`, `Participation`, `MarketplaceParticipation`
- `GetMarketplaceParticipationsResponse`
- `MarketplaceParticipationsSandboxResult`

No canonical ASI Amazon data model is introduced here.

---

## 8. Provenance

`SpApiSandboxProvenance` records: `provider=amazon_sp_api`, `environment=sandbox`, `api=sellers`, operation, region, endpoint host, `fetched_at`, HTTP status, model version `sellers-api-model/v1`.

It does **not** store client secret, refresh token, access token, or authorization headers. Nothing is written to profit/ads/history tables.

---

## 9. Manual verification

1. Put credentials only in `apps/api/.env` (never commit that file).
2. Set `SP_API_SANDBOX_ENABLED=true`.
3. `cd apps/api && uv run python -m app.amazon`
4. Expect sanitized SUCCESS output and a participation count. If credentials are missing, the CLI exits 0 and tells you to configure `.env`.

The CLI prints host and operation **before** the request. It never prints tokens.

---

## 10. Automated tests

`apps/api/tests/test_sp_api_sandbox.py` uses `httpx.MockTransport` only. Pytest does not call Amazon.

Coverage: LWA request shape, typed parse, 401, timeout, malformed token JSON, missing credentials, EU sandbox URL, Sellers path and `x-amz-access-token`, payload parse, 401/403/429/5xx, timeout, malformed payload, provenance fields, secret-free serialization.

`conftest.py` clears SP-API env vars so a local `.env` cannot trigger live calls during pytest.

---

## 11. Known limitations

- Static sandbox only; response is Amazon’s fixture, not the EWise production account.
- No token cache, no DB persistence, no sync jobs.
- No production OAuth / multi-seller authorization.
- No orders, inventory, catalog, reports, or finances ingestion.
- No Ads API.
- No Copilot tools for SP-API.
- `getAccount` is not called (EU-only roles; not needed for this proof).

---

## 12. Next milestone

**Milestone 12A — Amazon Connected Data Backbone Architecture & Canonical Data Model**

Do not start that work from this document automatically. Skills remain paused until the data backbone is designed and mature.

# 09 — Amazon Authorization and Security

## Credential boundary (ADR 0006)

| Store | Allowed | Forbidden |
| --- | --- | --- |
| Postgres `amazon_connections` | status, region, env, opaque `token_reference`, optional selling_partner_id | refresh/access tokens, LWA secrets, auth codes |
| Postgres `amazon_oauth_states` | `state_hash`, expiry, consumed | raw ASI state secret, codes, tokens |
| SecretProvider | refresh token (and only that long-lived secret) | putting tokens on business tables |
| Frontend / public JSON | connection lifecycle, messages | `token_reference`, tokens, secrets |
| Copilot / EvidenceEnvelope / logs | sanitized errors | tokens, codes, pointers |

Pointer format: `asi/amazon/{provider}/{environment}/{organization_id}/{connection_id}`.

## OAuth flow (implemented through 12B.1C.5)

```text
POST /api/v1/amazon/connection/authorize
  → persist hashed state (0008)
  → connection pending_authorization
  → return Seller Central consent URL (frontend navigates)

GET /api/v1/amazon/connection/callback?state&spapi_oauth_code[&selling_partner_id]
  → consume state (single use, TTL)
  → LWA authorization_code grant (redirect_uri must match portal exactly)
  → put_secret(refresh_token only)
  → bind token_reference
  → pending_validation  (NOT connected)
```

Callback does not call SP-API.

## Validation (12B.1D)

`POST /connection/test` with a bound `token_reference` performs the Sellers handshake and may move `pending_validation` → `connected`.

## Sandbox vs Draft/Production (do not mix)

POC finding: sandbox application and Draft/Production website-authorization application use **separate** local credential sets.

- Sandbox LWA + sandbox refresh token → Test Connection only
- Draft/Production application id + LWA client id/secret → Connect Amazon

Mixing sandbox LWA with production app id causes Amazon authorization errors.

## Production SecretProvider

`AMAZON_SECRET_BACKEND=production` **fails closed**. No AWS/Vault implementation yet. Dev/CI uses `DevelopmentSecretProvider`.

## Known hardening gaps

1. Live seller grant not fully proven.
2. Website OAuth **Login URI** handler incomplete.
3. Redirect/Login URIs must be exact public HTTPS; localhost rejected by Amazon for the real round-trip.
4. Process HTTP access logs may include callback query strings (`spapi_oauth_code`). Application code must not log them; production should redact at uvicorn/proxy.
5. Connect Amazon default environment is SANDBOX in local development.

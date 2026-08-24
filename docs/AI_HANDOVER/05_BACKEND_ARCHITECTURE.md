# 05 — Backend Architecture

Package: `apps/api/app`.

## Layout (stable)

| Area | Path | Notes |
| --- | --- | --- |
| HTTP routes | `app/api/routes/` | FastAPI routers |
| Core config | `app/core/config.py` | pydantic-settings; Amazon sandbox vs production LWA split |
| Persistence models | `app/persistence/models.py` | SQLAlchemy; includes `AmazonConnection`, `AmazonOAuthState` |
| Repositories | `app/persistence/repositories.py` | Includes Amazon connection + OAuth state repos |
| Listing / product | `app/services/`, `app/analytics/` | Deterministic engines |
| Copilot | `app/copilot/` | Registry, planner, orchestrator, synthesis |
| Amazon SP-API | `app/amazon/` | Isolated. No Copilot/Skills/Rainforest token coupling |

## Amazon modules (12A–12B.1D)

| Module | Role |
| --- | --- |
| `sandbox.py` | 12A.0 sandbox Sellers client (env refresh token) |
| `lwa.py` / `lwa_token.py` | LWA refresh-token and authorization-code grants |
| `connection.py` | Connection service, overview, test, authorize overlay |
| `connection_secrets.py` | Resolve `token_reference` → SecretProvider |
| `secrets.py` | SecretProvider Protocol, DevelopmentSecretProvider, factory, fail-closed production |
| `oauth.py` | Authorize-start + hashed state |
| `oauth_callback.py` | Callback consume + LWA exchange + `put_secret` + bind pointer |
| `sellers.py` | Connection-scoped Sellers client (12B.1D) |
| `seller_validation.py` | Handshake: pending_validation → connected / degraded / error |
| `models.py` | SP-API DTOs (not canonical ASI seller model) |
| `common.py` | Shared sanitizers / public dump helpers |

## Connection test behaviour (important)

`POST /api/v1/amazon/connection/test`:

- If the persisted SANDBOX (or PRODUCTION) row has `token_reference` and status in `pending_validation | connected | degraded` → **12B.1D seller validation** (SecretProvider refresh token + Sellers API).
- Else SANDBOX without seller token → **12A.1 env-token sandbox Test Connection**. That path must **not** persist `connected`.

## Config split

See `apps/api/.env.example`:

- Sandbox: `SP_API_LWA_CLIENT_ID` / `SECRET` / `SP_API_SANDBOX_REFRESH_TOKEN` / optional `SP_API_APPLICATION_ID`
- Production/Draft Connect Amazon: `SP_API_PRODUCTION_APPLICATION_ID`, `SP_API_PRODUCTION_LWA_CLIENT_ID`, `SP_API_PRODUCTION_LWA_CLIENT_SECRET`, `SP_API_OAUTH_REDIRECT_URI`

Do not collapse these.

## Tenancy

`current_organization_id()` from settings default org. Not `selling_partner_id`.

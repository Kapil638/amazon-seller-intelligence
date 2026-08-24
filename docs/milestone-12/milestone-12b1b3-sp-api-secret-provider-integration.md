# Milestone 12B.1B.3 — SP-API Client Integration with SecretProvider

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1B.4.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A** and **12B.1B.1–12B.1B.2**  
**Architecture:** [milestone-12b1b-secret-provider-architecture.md](milestone-12b1b-secret-provider-architecture.md)  
**Prior slice:** [milestone-12b1b2-development-secret-provider.md](milestone-12b1b2-development-secret-provider.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice wired existing SP-API sandbox client credential resolution through SecretProvider. No OAuth, token-reference binding, seller authorization, production secret provider, database, frontend, or Copilot changes.

---

## Implementation Summary

`AmazonSpApiSandboxClient` no longer reads `Settings.sp_api_sandbox_refresh_token`. The seller refresh token is resolved through `SecretProvider.get_secret(development_sandbox_token_reference())`. App LWA `client_id` / `client_secret` stay in settings. Injected `lwa=` still bypasses resolution so existing HTTP unit tests remain valid.

---

## Files Created/Changed

- `apps/api/app/amazon/sandbox.py` — `resolve_sandbox_refresh_token()`; client uses SecretProvider
- `apps/api/tests/test_amazon_sp_api_secret_integration.py` — new

Unchanged: Protocol, DevelopmentSecretProvider internals, `amazon_connections`, migrations, LWA grant logic, connection service, routes, frontend, Copilot, Skills.

---

## SP-API Credential Flow Before

```text
AmazonSpApiSandboxClient
        │
Settings.sp_api_sandbox_refresh_token
        │
LwaClient(refresh_token=…)
```

---

## SP-API Credential Flow After

```text
AmazonSpApiSandboxClient
        │
resolve_sandbox_refresh_token()
        │
SecretProvider.get_secret(development_sandbox_token_reference())
        │
DevelopmentSecretProvider (.env fallback / in-memory)
        │
SecretStr → LwaClient
```

The client does not read `SP_API_SANDBOX_REFRESH_TOKEN` or `os.getenv`.

Development sandbox behaviour is unchanged for local developers: `.env` still supplies the token, but only inside `DevelopmentSecretProvider`.

---

## SecretProvider Integration Details

| Item | Behaviour |
| --- | --- |
| Default construction | `get_secret_provider()` + `development_sandbox_token_reference(default_organization_id)` |
| Injected provider | Optional `secret_provider=` for tests |
| Injected LWA | Optional `lwa=` still skips SecretProvider (existing sandbox HTTP tests) |
| App credentials | LWA `client_id` / `client_secret` remain settings-based |
| Seller secret | Refresh token only, as `SecretStr` |
| Missing secret | `SpApiConfigurationError` with the existing missing-credentials message (no token text) |
| Token binding | Not implemented; development reference only |

Reference used in this slice:

`asi/amazon/SP_API/SANDBOX/{organization_id}/00000000-0000-4000-8000-000000000001`

---

## Security Validation

- Sandbox module source has no `sp_api_sandbox_refresh_token`, `SP_API_SANDBOX_REFRESH_TOKEN`, or `os.getenv`
- Client ignores a refresh token on `Settings` when the provider has none
- Repr/logs/exceptions do not contain `Atzr|` / `Atza|` / client secret
- Existing sandbox tests still assert secrets are absent from JSON
- No HTTP secret API
- No tokens stored in the database

---

## Tests Added

`apps/api/tests/test_amazon_sp_api_secret_integration.py`

1. Client sends the provider-resolved refresh token to LWA (settings token unset)
2. Development sandbox env fallback still resolves
3. Client credential path does not read environment; settings token is ignored
4. Missing secret → safe `SpApiConfigurationError`
5. Secrets never appear in logs or repr
6. Injected `lwa=` still constructs without a provider token

---

## Test Results

`82 passed` in 0.61s

- `test_amazon_sp_api_secret_integration.py`
- `test_amazon_secret_provider.py`
- `test_amazon_development_secret_provider.py`
- `test_amazon_connection_persistence.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_service.py`
- `test_amazon_connection_api.py`
- `test_amazon_connection.py`
- `test_sp_api_sandbox.py`

No regression in 12B.1B.1, 12B.1B.2, 12B.1A, or SP-API sandbox tests.

---

## Concerns

`AmazonConnectionService._credentials_ready()` and the CLI preflight still inspect settings for “is sandbox configured?”. That is not the SP-API client credential path. Binding a real `token_reference` from `amazon_connections` is **12B.1B.4**.

---

## Explicit Confirmation

**Only Milestone 12B.1B.3 SP-API client integration implemented. No OAuth, token binding, seller authorization, production secret provider, database changes, frontend, or Copilot changes added.**

---

## Next slice (not started)

**12B.1B.4 — Secret reference validation**

Wait for explicit approval before implementing.

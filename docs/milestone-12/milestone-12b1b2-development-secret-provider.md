# Milestone 12B.1B.2 — DevelopmentSecretProvider

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1B.3.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A** and **12B.1B.1**  
**Architecture:** [milestone-12b1b-secret-provider-architecture.md](milestone-12b1b-secret-provider-architecture.md)  
**Prior slice:** [milestone-12b1b1-secret-provider-interface.md](milestone-12b1b1-secret-provider-interface.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice added the local development SecretProvider only. No AWS, encrypted database, OAuth, token-reference binding, SP-API client wiring, frontend, or Copilot changes.

---

## Implementation Summary

Added `DevelopmentSecretProvider` implementing the existing `SecretProvider` Protocol. Local resolution uses an in-memory `SecretStr` map plus sandbox `.env` fallback for the default organization. The Protocol was not changed. Callers can depend on `SecretProvider` / `get_secret_provider()` without reading environment variables themselves.

---

## Files Created/Changed

- `apps/api/app/amazon/secrets.py` — `DevelopmentSecretProvider`, factory, env-fallback helper
- `apps/api/app/core/config.py` — `amazon_secret_backend` (default `development`)
- `apps/api/.env.example` — commented `AMAZON_SECRET_BACKEND=development`
- `apps/api/tests/test_amazon_development_secret_provider.py` — new

Unchanged: Protocol methods, database models, migrations, Amazon clients, LWA, connection service, routes, frontend, Copilot, Skills.

---

## DevelopmentSecretProvider Design

```text
DevelopmentSecretProvider
  put_secret(reference, value: SecretStr) -> None
  get_secret(reference) -> SecretStr
  exists(reference) -> bool
  delete_secret(reference) -> None
```

| Behaviour | Detail |
| --- | --- |
| Contract | Implements `SecretProvider`; `isinstance(..., SecretProvider)` is true |
| Memory store | `reference → SecretStr`; overwrites the same reference; missing delete is a no-op |
| Env fallback | `SP_API_SANDBOX_REFRESH_TOKEN` for default-org `SANDBOX` ASI references only |
| Validation | Every reference goes through `validate_secret_reference()` |
| Repr | `SecretProvider(backend=development)` |
| Not stored | LWA `client_id` / `client_secret`; raw token strings |
| Not done | `token_reference` rows, connection status changes, seller authorization |

Factory: `get_secret_provider()` returns this backend. Any other `amazon_secret_backend` raises `SecretAccessError` (fail closed). `reset_secret_provider()` drops the process-local instance for tests.

Synthetic env pointer helper: `development_sandbox_token_reference()`  
`asi/amazon/SP_API/SANDBOX/{organization_id}/00000000-0000-4000-8000-000000000001`

---

## Secret Resolution Flow

```text
get_secret(reference)
        │
        ├─ invalid reference → InvalidSecretReferenceError
        ├─ in-memory map hit → SecretStr
        ├─ default-org SANDBOX ASI reference
        │     + SP_API_SANDBOX_REFRESH_TOKEN set → env SecretStr
        └─ else → SecretNotFoundError
```

```text
SP-API client (still 12A .env until 12B.1B.3)
        │
SecretProvider
        │
DevelopmentSecretProvider
        │
.env / in-memory SecretStr
```

Env fallback is development-only. It does **not** apply to `PRODUCTION` references or other organization ids. It is not seller authorization.

A memory overlay on a sandbox env reference wins until `delete_secret`; after delete, the `.env` token is visible again. That does not mutate `.env` and does not persist `connected`.

---

## Security Validation

- Values in and out are `SecretStr`
- Repr/str/logs/exceptions do not contain `Atzr|`, `Atza|`, or client secrets
- Token-shaped references (`Atzr|…`, `Atza|…`, PEM) are rejected
- Production and other-org references never receive the sandbox env token
- Unimplemented backends do not leak env tokens in the error
- No httpx / LWA / SP-API imports in the provider
- No HTTP secret API

---

## Configuration Changes

| Setting | Default | Purpose |
| --- | --- | --- |
| `amazon_secret_backend` | `development` | Select SecretProvider backend |

No AWS, Vault, or encryption-key settings.

---

## Tests Added

`apps/api/tests/test_amazon_development_secret_provider.py`

1. Implements `SecretProvider`
2. `put_secret` stores `SecretStr` (plain strings rejected)
3. `get_secret` returns `SecretStr`
4. `exists` matches stored state
5. `delete_secret` removes stored secrets
6. Missing secret → safe `SecretNotFoundError`
7. Sandbox env fallback for the default org
8. Secrets absent from logs, repr, and exceptions
9. No Amazon API calls
10. Factory defaults to development; AWS backend is rejected

---

## Test Results

`76 passed` in 0.62s

- `test_amazon_secret_provider.py`
- `test_amazon_development_secret_provider.py`
- `test_amazon_connection_persistence.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_service.py`
- `test_amazon_connection_api.py`
- `test_amazon_connection.py`
- `test_sp_api_sandbox.py`

No regression in 12B.1B.1, 12B.1A, or SP-API sandbox tests.

---

## Concerns

The SP-API sandbox client still reads `.env` directly. Wiring it through SecretProvider is **12B.1B.3**.

Env fallback covers any default-org `SANDBOX` ASI reference, not only the synthetic env connection id. After deleting an overlay on that reference, the `.env` token is visible again. That is development-only and is not treated as seller authorization.

---

## Explicit Confirmation

**Only Milestone 12B.1B.2 DevelopmentSecretProvider implemented. No AWS provider, database secret storage, OAuth, token binding, SP-API production changes, frontend, or Copilot changes added.**

---

## Next slice (not started)

**12B.1B.3 — SP-API client integration**

Wait for explicit approval before implementing.

# Milestone 12B.1B.5 — Production Secret Provider Preparation

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1C.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A** and **12B.1B.1–12B.1B.4**  
**Architecture:** [milestone-12b1b-secret-provider-architecture.md](milestone-12b1b-secret-provider-architecture.md)  
**Prior slice:** [milestone-12b1b4-secret-reference-validation.md](milestone-12b1b4-secret-reference-validation.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice prepared production SecretProvider **selection** only. No AWS SDK, Azure Key Vault, HashiCorp Vault, cloud credentials, OAuth, token binding, database, frontend, or Copilot changes.

---

## Implementation Summary

Formalized `SecretProviderFactory`. `AMAZON_SECRET_BACKEND=development` still returns `DevelopmentSecretProvider`. `production` is a reserved backend that **fails closed** and never falls back to `.env` / development storage. Unknown backends also fail closed. The `SecretProvider` Protocol is unchanged. Application code still depends on the interface, not the storage mechanism.

---

## Files Created/Changed

- `apps/api/app/amazon/secrets.py` — `SecretProviderFactory`, `resolve_amazon_secret_backend`, production fail-closed
- `apps/api/app/core/config.py` — documented `amazon_secret_backend`
- `apps/api/.env.example` — production is reserved; no cloud credential vars
- `apps/api/tests/test_amazon_secret_provider_factory.py` — new
- `docs/milestone-12/milestone-12b1b5-production-secret-provider-preparation.md` — this file

Unchanged: database/migrations, OAuth, seller authorization, SP-API operations, frontend, Copilot, Skills. No boto3/azure/hvac dependencies.

---

## Provider Selection Flow

```text
SecretProviderFactory.create(settings)
        │
AMAZON_SECRET_BACKEND
        │
        ├─ development (default) → DevelopmentSecretProvider
        ├─ production            → SecretAccessError (not implemented; no .env fallback)
        └─ any other value       → SecretAccessError (unknown; fail closed)
```

```text
Application
      │
SecretProvider
      │
      ├─ DevelopmentSecretProvider → .env / memory
      └─ future ProductionSecretProvider → AWS Secrets Manager / Vault / cloud store
```

The application must not change when the live backend is swapped. Callers use `get_secret_provider()` / `SecretProvider` only.

---

## Production Provider Contract (not implemented)

A future production backend **must** implement `SecretProvider`:

| Method | Requirement |
| --- | --- |
| `put_secret(reference, value: SecretStr)` | Store seller refresh material. No raw strings. |
| `get_secret(reference) -> SecretStr` | Return `SecretStr`. Missing → `SecretNotFoundError` without token text. |
| `exists(reference) -> bool` | Never return secret material. |
| `delete_secret(reference)` | Safe no-op if missing. |

Also required: no logging/repr/serialization of values; opaque ASI `token_reference` only; no sandbox `.env` fallback; no reads of `SP_API_SANDBOX_REFRESH_TOKEN`.

This slice does **not** implement AWS Secrets Manager, Azure Key Vault, HashiCorp Vault, or encrypted-database storage.

---

## Configuration Changes

| Setting | Default | Meaning |
| --- | --- | --- |
| `AMAZON_SECRET_BACKEND` / `amazon_secret_backend` | `development` | Live SecretProvider backend |

Allowed now:

- `development` — local provider
- `production` — reserved; fails closed until implemented

Unknown values (including `aws_secrets_manager`, `vault`) fail closed. **No AWS/Vault/KMS environment variables were added.**

Production mode must be selected explicitly. It cannot silently use development.

---

## Future Secret Lifecycle (documentation only)

```text
Seller Authorization (12B.1C, not this slice)
        │
Generate refresh token (Amazon / LWA)
        │
SecretProvider.put_secret(reference, SecretStr)
        │
Receive token_reference (opaque ASI pointer)
        │
amazon_connections.token_reference  (metadata only; never the token)
        │
SecretProvider.get_secret(token_reference)
        │
SP-API Client (SecretStr refresh token → LWA access token in memory)
```

OAuth, authorization_code exchange, and `token_reference` bind are **not** implemented here.

---

## Security Validation

- Production selection with a sandbox refresh token still raises; it does not construct `DevelopmentSecretProvider`
- Cached development provider is not returned when settings ask for `production`
- Factory errors contain no `Atzr|` / `Atza|` / client secrets
- Factory source has no boto3 / botocore / azure / hvac
- Secrets stay out of the database, API responses, and frontend
- `token_reference` remains an opaque identifier

---

## Tests Added

`apps/api/tests/test_amazon_secret_provider_factory.py`

1. Factory selects development provider
2. Unknown provider fails safely
3. Production selection does not fall back to development
4. SecretProvider contract methods/types unchanged
5. Factory errors never include secret values
6. Existing sandbox / connection / SecretProvider tests remain the regression suite

---

## Test Results

`95 passed` in 0.71s

- `test_amazon_secret_provider_factory.py`
- `test_amazon_secret_provider.py`
- `test_amazon_development_secret_provider.py`
- `test_amazon_sp_api_secret_integration.py`
- `test_amazon_connection_secret_resolver.py`
- `test_amazon_connection_persistence.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_service.py`
- `test_amazon_connection_api.py`
- `test_amazon_connection.py`
- `test_sp_api_sandbox.py`

No regression in 12B.1A or 12B.1B.1–12B.1B.4.

---

## Concerns

A real production SecretProvider (AWS Secrets Manager or encrypted-DB fallback) is still unbuilt. Setting `AMAZON_SECRET_BACKEND=production` in a deployed environment will fail closed until that work is approved after 12B.1C or as a dedicated follow-on.

`token_reference` still cannot be written by the repository. Seller authorization remains 12B.1C.

---

## Explicit Confirmation

**Only Milestone 12B.1B.5 Production Secret Provider Preparation implemented. No AWS provider, cloud integration, OAuth, seller authorization, token binding, database changes, frontend, or Copilot changes added.**

---

## Next slice (not started)

**12B.1C — Production authorization flow**

Wait for explicit approval before implementing.

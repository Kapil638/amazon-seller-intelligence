# Milestone 12B.1B.4 — Secret Reference Validation and Connection-to-Secret Resolution Preparation

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1B.5.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A** and **12B.1B.1–12B.1B.3**  
**Architecture:** [milestone-12b1b-secret-provider-architecture.md](milestone-12b1b-secret-provider-architecture.md)  
**Prior slice:** [milestone-12b1b3-sp-api-secret-provider-integration.md](milestone-12b1b3-sp-api-secret-provider-integration.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice prepared org-scoped secret reference resolution only. No OAuth, seller authorization, token creation, database secret storage, production provider, frontend, or Copilot changes.

---

## Implementation Summary

Added `AmazonConnectionSecretResolver`: given `organization_id` and connection metadata, it returns a validated ASI `token_reference` pointer and can fetch `SecretStr` material from SecretProvider. It does not persist, does not update `amazon_connections.status` or `token_reference`, and does not call Amazon. Unbound SP-API sandbox connections still use `development_sandbox_token_reference()`.

---

## Files Created/Changed

- `apps/api/app/amazon/secrets.py` — `parse_asi_amazon_secret_reference`, `build_asi_secret_reference`, `AsiAmazonSecretReference`
- `apps/api/app/amazon/connection_secrets.py` — new resolver
- `apps/api/tests/test_amazon_connection_secret_resolver.py` — new

Unchanged: database/migrations, repository (still cannot write `token_reference`), SP-API client, LWA, connection service, routes, frontend, Copilot, Skills.

---

## Secret Resolution Flow

```text
organization_id + AmazonConnection metadata
        │
        ├─ org mismatch → InvalidSecretReferenceError
        ├─ token_reference set
        │     └─ must be ASI format and match org / connection / provider / environment
        │           → SecretProvider.get_secret(reference)
        ├─ token_reference null + SP_API SANDBOX
        │     → development_sandbox_token_reference(org)
        └─ else → InvalidSecretReferenceError / SecretNotFoundError
```

Future authorization flow (not implemented here):

```text
Amazon Authorization → create seller secret → generate token_reference
        → amazon_connections.token_reference → SecretProvider → SP-API client
```

---

## Reference Validation Rules

Valid:

`asi/amazon/{SP_API|ADS_API}/{SANDBOX|PRODUCTION}/{organization_id}/{connection_id}`

Example: `asi/amazon/SP_API/PRODUCTION/org123/connection456`

Rejected:

- empty / oversized / newline
- `Atzr|…`, `Atza|…`
- PEM private keys
- raw token strings (`raw-refresh-token-value`)
- incomplete ASI paths

A stored `token_reference` must also match the connection’s organization, id, provider, and environment. Cross-org attempts never call `get_secret`.

---

## Organization Isolation Validation

- Caller `organization_id` must match `connection.organization_id`
- A row cannot point at another org’s or another connection’s ASI path
- Cross-org resolve raises `InvalidSecretReferenceError` with no token text

---

## Security Validation

- Resolver `__repr__` is `AmazonConnectionSecretResolver()`
- Exceptions/logs do not contain `Atzr|` / `Atza|` / client secrets
- No persistence of secrets or `token_reference`
- No Amazon HTTP, no Copilot imports
- Public APIs still omit `token_reference`

---

## Tests Added

`apps/api/tests/test_amazon_connection_secret_resolver.py`

1. Valid ASI reference accepted
2. Token-shaped references rejected
3. Raw secret values rejected
4. Connection resolves the bound reference; sandbox null falls back to development ref
5. Organization isolation
6. Missing secret → safe `SecretNotFoundError`
7. Secrets never in logs/errors
8. Resolver does not call Amazon or persist

---

## Test Results

`90 passed` in 0.66s

- `test_amazon_connection_secret_resolver.py`
- `test_amazon_secret_provider.py`
- `test_amazon_development_secret_provider.py`
- `test_amazon_sp_api_secret_integration.py`
- `test_amazon_connection_persistence.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_service.py`
- `test_amazon_connection_api.py`
- `test_amazon_connection.py`
- `test_sp_api_sandbox.py`

No regression in 12B.1A or 12B.1B.1–12B.1B.3.

---

## Concerns

The repository still cannot write `token_reference` (intentional in 12B.1A). 12B.1C will need a narrow internal bind. The SP-API client still uses the development sandbox reference, not this resolver, until seller authorization exists.

---

## Explicit Confirmation

**Only Milestone 12B.1B.4 Secret Reference Validation and Connection-to-Secret Resolution Preparation implemented. No OAuth, seller authorization, token creation, database secret storage, production provider, frontend, or Copilot changes added.**

---

## Next slice (not started)

**12B.1B.5 — Production provider preparation**

Wait for explicit approval before implementing.

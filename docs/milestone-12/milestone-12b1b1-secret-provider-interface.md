# Milestone 12B.1B.1 — SecretProvider Interface and Abstractions

**Date:** 23 August 2026  
**Status:** Implemented. Waiting for approval before 12B.1B.2.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A**  
**Architecture:** [milestone-12b1b-secret-provider-architecture.md](milestone-12b1b-secret-provider-architecture.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This slice created the SecretProvider contract only. No storage provider, OAuth, token handling, SP-API, frontend, or Copilot changes.

---

## Implementation Summary

Added a narrow `SecretProvider` Protocol in `app.amazon.secrets`. Application code can depend on the interface, not on secret storage. Values in and out are `SecretStr`. Safe exceptions redact credential-shaped material. No local `.env`, AWS, or encrypted-database provider was implemented.

---

## Files Created/Changed

- `apps/api/app/amazon/secrets.py` — new (Protocol, helpers, exceptions)
- `apps/api/tests/test_amazon_secret_provider.py` — new (abstraction unit tests)

Unchanged: database models, migrations, Amazon clients, LWA, connection service, routes, frontend, Copilot, Skills.

---

## SecretProvider Interface Design

```text
SecretProvider
  put_secret(reference, value: SecretStr) -> None
  get_secret(reference) -> SecretStr
  exists(reference) -> bool
  delete_secret(reference) -> None
```

| Method | Purpose |
| --- | --- |
| `put_secret` | Store secret material. Future use: OAuth/token onboarding. Overwrites the same reference. |
| `get_secret` | Retrieve secret material as `SecretStr`. Missing secret raises `SecretNotFoundError`. |
| `exists` | Return whether material exists. Never returns the secret. |
| `delete_secret` | Remove material. Missing references are a safe no-op. |

Helpers (not storage):

| Helper | Purpose |
| --- | --- |
| `validate_secret_reference` | Reject empty, oversized (>128), newline, or credential-shaped pointers |
| `secret_provider_repr(backend=...)` | Safe repr, e.g. `SecretProvider(backend=development)` |
| `redact_secret_material` | Strip `Atzr|`, `Atza|`, and private-key PEM from strings |

Location: `apps/api/app/amazon/secrets.py` (isolated Amazon module; not exported from Copilot).

---

## Allowed usage boundary

Documented in the module docstring.

**Allowed future callers**

- Amazon connection flow
- SP-API credential resolution

**Forbidden callers**

- Frontend
- API payloads
- Copilot
- Skills
- EvidenceEnvelope
- Reports
- Analytics engines

The interface exists only as an internal security boundary.

---

## Exceptions Added

Defined next to the Protocol (not a new exception framework):

| Exception | Default message |
| --- | --- |
| `SecretNotFoundError` | Requested Amazon secret was not found. |
| `SecretAccessError` | Amazon secret could not be retrieved. |
| `InvalidSecretReferenceError` | Amazon secret reference is invalid. |

Exception constructors run `redact_secret_material`. Token-only custom messages fall back to the default sentence. Messages never include refresh tokens, access tokens, or PEM private keys.

---

## Security Guarantees Added

- In/out values are `SecretStr`; `repr`/`str` of `SecretStr` do not include the raw token
- Provider `__repr__` helper never includes tokens or credentials
- `token_reference` validation refuses using a refresh/access token as the pointer
- Max reference length 128 matches `amazon_connections.token_reference`
- Abstraction does not import LWA, sandbox client, or httpx
- No HTTP secret API
- No secret storage

---

## Tests Added

`apps/api/tests/test_amazon_secret_provider.py`

1. Protocol contract exists (`put_secret`, `get_secret`, `exists`, `delete_secret`)
2. Secret values use `SecretStr`
3. Exceptions do not leak secret values
4. Provider representations do not expose secrets
5. Missing secret error is safe
6. Abstraction does not call Amazon

Tests use an in-test `_ContractProbe` only. That is not `LocalSecretProvider` / `DevelopmentSecretProvider`.

---

## Test Results

`63 passed` in 0.54s

- `test_amazon_secret_provider.py`
- `test_amazon_connection_persistence.py`
- `test_amazon_connection_repository.py`
- `test_amazon_connection_service.py`
- `test_amazon_connection_api.py`
- `test_amazon_connection.py`
- `test_sp_api_sandbox.py`

No regression in 12B.1A or SP-API sandbox tests.

---

## Concerns

Unstructured client secrets in a custom exception string (no `Atzr|` / `Atza|` / PEM shape) cannot be reliably redacted. Callers must keep using the default messages.

`validate_secret_reference` caps length at 128 to match `amazon_connections.token_reference`. A later AWS ARN longer than 128 would need a schema change.

---

## Explicit Confirmation

**Only Milestone 12B.1B.1 SecretProvider interface and abstractions implemented. No secret storage provider, OAuth, token handling, SP-API changes, frontend, or Copilot changes added.**

---

## Next slice (not started)

**12B.1B.2 — Local development provider**

Wait for explicit approval before implementing.

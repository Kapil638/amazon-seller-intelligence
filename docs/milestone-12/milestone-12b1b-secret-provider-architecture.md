# Milestone 12B.1B — SecretProvider Architecture

**Date:** 23 August 2026  
**Status:** Architecture approved. **12B.1B.1–12B.1B.5 implemented.** Production backend remains fail-closed. This file is the architecture record, not a “do not implement” gate.  
**Baseline:** tag `amazon-api-foundation-v1` (`67f5412`) plus completed **12B.1A** (connection metadata persistence)  
**Parent architecture:** [milestone-12b1-production-connection-security-architecture.md](milestone-12b1-production-connection-security-architecture.md)  
**Parent plan:** [milestone-12b1-implementation-plan.md](milestone-12b1-implementation-plan.md)  
**ADR:** [0006 — Amazon Connection Credential Boundary](../adr/0006-amazon-connection-credential-boundary.md)

This document does not implement SecretProvider, store credentials, add OAuth, add seller authorization, add token exchange, call SP-API, or modify Copilot, Skills, or frontend.

---

## Frozen principles

1. Application code must never directly access secrets.
2. The database must never store refresh tokens, access tokens, client secrets, or private keys.
3. `amazon_connections` stores only `token_reference` and connection metadata.
4. SecretProvider owns secret retrieval.
5. `AmazonConnectionService` must not know the secret storage implementation.
6. Copilot and Skills must never access secrets directly.
7. Local development must continue working.
8. Production secret storage should be replaceable.

---

## 1. Current Credential Flow Review

### Current flow

```text
.env / Settings (SecretStr)
        │
        ├─ SP_API_LWA_CLIENT_ID
        ├─ SP_API_LWA_CLIENT_SECRET      ← app-level LWA
        └─ SP_API_SANDBOX_REFRESH_TOKEN  ← one process-wide seller refresh token
                │
                ▼
        LwaClient (holds plaintext in memory)
                │
                ▼
        AmazonSpApiSandboxClient
                │
                ▼
        GET /sellers/v1/marketplaceParticipations
```

`AmazonConnectionService.test_sp_api()` still uses this path. GET/overview never calls Amazon. `amazon_connections.token_reference` exists but is unused. The repository **rejects** writing `token_reference`. Sandbox success still must not persist `connected`.

This was acceptable for 12A sandbox development. It is not a production seller-token model (ADR 0006).

### Files

| File | Role |
| --- | --- |
| `apps/api/app/core/config.py` | Loads LWA + sandbox refresh token as `SecretStr` |
| `apps/api/.env.example` | Empty placeholders |
| `apps/api/app/amazon/lwa.py` | Refresh-token grant; copies secrets into instance fields |
| `apps/api/app/amazon/sandbox.py` | Constructs `LwaClient` from settings |
| `apps/api/app/amazon/connection.py` | `credentials_configured()` reads the same three settings |
| `apps/api/app/amazon/__main__.py` | CLI sandbox proof from env |
| `apps/api/tests/conftest.py` | Clears SP-API env in pytest |

### Direct secret access points

- Settings: `sp_api_lwa_client_id`, `sp_api_lwa_client_secret`, `sp_api_sandbox_refresh_token`
- `LwaClient.__init__` unwraps `SecretStr` into `_client_id` / `_client_secret` / `_refresh_token`
- `AmazonSpApiSandboxClient` passes those settings into `LwaClient`
- HTTP: LWA form body; SP-API header `x-amz-access-token`
- Rainforest / OpenAI / Supabase keys are **separate** and stay out of 12B.1B

### Security risks

- One process-wide sandbox refresh token is not multi-tenant
- App LWA secret and seller refresh token share the same settings object
- Business services still know *where* secrets live (`.env`)
- Local Postgres backups would be unsafe if tokens were ever columns (they are not; keep that)
- `token_reference` cannot yet be bound to a stored secret

---

## 2. SecretProvider Architecture Proposal

Keep secrets behind a **narrow Protocol** used only by Amazon connection code. No HTTP secret API. Copilot, Skills, frontend, and EvidenceEnvelope never import it.

Target architecture:

```text
Application
        │
SecretProvider Interface
        │
Secret Storage Implementation
        │
Amazon Credentials / Tokens
```

### Interface

```text
Protocol SecretProvider
  put_secret(reference, value: SecretStr) -> None
  get_secret(reference) -> SecretStr
  exists(reference) -> bool
  delete_secret(reference) -> None
```

| Method | Why required |
| --- | --- |
| `get_secret` | Only way to obtain a seller refresh token at runtime |
| `exists` | Distinguish “no secret yet” from “storage failure” without returning material |
| `delete_secret` | Disconnect / revoke / failed persist rollback (needed in 12B.1C) |
| `put_secret` | Required for tests now and OAuth later. 12B.1B does **not** call Amazon to obtain a token |

`rotate_secret` is `put_secret` at the same reference, or put-new + swap `token_reference` + delete-old. Do not build an enterprise secret platform.

Returns must be `SecretStr`. `__repr__` of providers must not include values. Missing secret → `SpApiConfigurationError` (or existing persistence error), never a raw token string in the exception.

### Who may call it

Only after `AmazonConnectionRepository.get(organization_id, …)` returns a row. `get_secret` must **not** accept a reference from HTTP, query params, or Copilot.

### What it stores

Seller **refresh tokens** only.

- App LWA `client_id` / `client_secret` stay in process/platform env (one ASI EWise app, many sellers).
- Access tokens stay in memory for one LWA exchange + one SP-API call.
- V1: no access-token cache.

### What it does not do

OAuth, `authorization_code` exchange, ingest, Ads tokens, Rainforest/OpenAI keys.

---

## 3. Secret Storage Strategy

| Implementation | Environment | Behaviour |
| --- | --- | --- |
| `DevelopmentSecretProvider` | local / pytest | Maps a **synthetic** reference for the default org to `SP_API_SANDBOX_REFRESH_TOKEN`. Optional in-memory map for `put`/`get`/`delete` in tests. Never logs values. |
| `EncryptedDatabaseSecretProvider` | production fallback | Ciphertext + `key_id` in `amazon_secret_ciphertexts` (not `amazon_connections`). Master key in env/KMS. AES-GCM. |
| `AwsSecretsManagerProvider` | preferred production | One SM object per `token_reference`. **Skeleton in 12B.1B**; pytest never calls AWS. |

Select via settings, e.g. `amazon_secret_backend=development|encrypted_db|aws_secrets_manager`. Default `development`.

Application code depends on the Protocol only. Swapping backends does not change `LwaClient`, routes, or Copilot.

**Preferred production:** AWS Secrets Manager (or GCP Secret Manager / Azure Key Vault on the eventual host). Refresh tokens must not share undifferentiated Postgres backups with listings and profit snapshots.

**Fallback:** encrypted application-managed ciphertext table + env/KMS master key. Acceptable to ship 12B.1B before a cloud SM is provisioned.

**Not preferred as the only control:** Supabase Vault on the same service role as business tables. It is not a separate trust boundary. Supabase remains the Postgres host; secrets should still be encrypted or in a dedicated SM.

Local sandbox without a row keeps working: no `token_reference` → current `.env` LWA path (12A.1).

---

## 4. Token Reference Design

`amazon_connections.token_reference` is an **opaque pointer**. Postgres never stores `Atzr|…`.

ASI-generated form (not a URL):

```text
asi/amazon/{provider}/{environment}/{organization_id}/{connection_id}
```

Vendor ids (ARN, UUID) are allowed if the DB stores only that string.

```text
GET connection (org-scoped)
        │
        ▼
row.token_reference  (or null)
        │
        ├─ null → 12A.1 env sandbox refresh token (dev only)
        └─ set  → SecretProvider.get_secret(row.token_reference)
                        │
                        ▼
                  SecretStr refresh token
                        │
                        ▼
                  LwaClient + app client_id/secret from env
```

**12B.1A gap:** repository `update()` / `create()` refuse `token_reference`. 12B.1B needs a **narrow internal setter** used only after `put_secret`, still org-scoped, still never exposed on public models (`token` is a forbidden JSON key fragment).

Database backups of listings/profit do not contain seller tokens. Secret store failure ≠ connection metadata loss; status stays `not_connected` / `error` without a usable secret.

---

## 5. SP-API Client Impact

### Unchanged

- Sellers path, DTOs, provenance
- `grant_type=refresh_token` LWA call
- Access token as `SecretStr`; no persist
- GET `/connection` does not call Amazon
- Sandbox test must not persist `connected`
- Copilot / Skills / Rainforest / profit / ads engines

### Change in 12B.1B

| Component | Change |
| --- | --- |
| New `app/amazon/secrets.py` | Protocol + development provider |
| `config.py` | `amazon_secret_backend` |
| `AmazonSpApiSandboxClient` / factory | Optional refresh token argument; default remains settings |
| `AmazonConnectionService.test_sp_api` | If org-scoped row has `token_reference`, resolve via provider; else env |
| Repository | Internal bind/clear of `token_reference` only |

`LwaClient` can keep taking `SecretStr` arguments. The **factory** above it must stop reading the seller refresh token from settings when a connection reference exists.

App `client_id` / `client_secret` still come from settings. Do not put them in SecretProvider per seller.

### Future flow

```text
Current:

SP-API Client
      │
    .env


Future:

SP-API Client
      │
SecretProvider
      │
Secret Storage
```

---

## 6. Security Review

| Risk | Impact | Mitigation |
| --- | --- | --- |
| HTTP `get_secret(reference)` | Critical | No secret routes. Resolve only from org-scoped row |
| `token_reference` in GET JSON | High | Keep off public models; `public_model_dump` already rejects `token*` |
| Logs of `Atzr|` / client_secret | High | Log connection id + status only; `SecretStr`; provider `__repr__` empty |
| Tokens on `amazon_connections` | Critical | Column remains reference only |
| Cross-org reference guess | High | Load row by org first; refuse get if reference ≠ `row.token_reference` |
| pytest hitting AWS/Amazon | High | Development provider + conftest; no live SM |
| Frontend / Copilot import | High | Provider lives under `app.amazon.secrets`; no Copilot import |
| Orphan secret after failed DB write | Medium | `put` then bind reference; on failure `delete_secret` (full use in 12B.1C) |
| Access token persistence | High | Memory only; no V1 cache |
| Dev `.env` treated as seller OAuth | High | Env path does not set `connected` |
| Accidental API responses | High | extra-forbid + `public_model_dump`; never serialize `SecretStr` |
| Developer access to production tokens | High | Development backend cannot resolve production references from sandbox env |
| Database exposure | Critical | No token columns; ciphertext table only if encrypted_db fallback |

---

## 7. Test Strategy

Do not create tests in this checkpoint. When 12B.1B is coded:

### Unit tests

- Protocol implementations: put / get / exists / delete
- Development provider maps synthetic default-org ref to sandbox env token
- Missing secret → safe error, no token in message
- `repr` / JSON dump contain no `Atzr|` / client_secret
- Delete then get raises a safe configuration/auth error

### Security tests

- `get_secret` not callable from routes
- Service refuses a reference that does not match the loaded org row
- Public connection JSON still has no `token_reference`
- Secrets never serialized; secrets never logged

### Integration tests

- Sandbox client uses provider-supplied refresh token (mocked LWA)
- No row → existing 12A env path still works
- pytest never calls AWS or live Amazon

### Regression

- `test_sp_api_sandbox.py`, connection persistence/repo/service/API/UI tests stay green

---

## 8. Migration Strategy

No schema change required for development-only 12B.1B. Encrypted-DB backend would add `amazon_secret_ciphertexts` later (still not plaintext on `amazon_connections`).

### Phase A — Compatible overlay

Interface + `DevelopmentSecretProvider`. Env sandbox unchanged if `token_reference` is null.

### Phase B — Optional resolve

If row has reference, test path uses provider. Else `.env`. Do not persist `connected`.

### Phase C — Bind reference

Tests `put_secret` + internal `token_reference` bind. No OAuth.

### Phase D — Production skeleton

Flag + typed AWS or encrypted-DB adapter. CI stays on development backend.

**Rollback:** set backend to `development`, leave `token_reference` null, 12A.1 still works.

**Backward compatibility:** local development and sandbox tests keep using `.env` until a row has a bound reference.

---

## 9. Implementation Sequence

### 12B.1B.1 — SecretProvider interface

**Objective:** Protocol + errors; no storage.  
**Files affected:** `apps/api/app/amazon/secrets.py` (new)  
**Dependencies:** none  
**Exit criteria:** Type-checkable Protocol; no AWS; no Copilot import

### 12B.1B.2 — Local development provider

**Objective:** `.env` / in-memory map; pytest-safe.  
**Files affected:** `secrets.py`, `config.py` (`amazon_secret_backend`)  
**Dependencies:** 12B.1B.1  
**Exit criteria:** put/get/exists/delete tests; no secret in logs/repr; 12A tests still pass

### 12B.1B.3 — SP-API client integration

**Objective:** Factory/client can take a provider-resolved refresh token; env fallback if no reference.  
**Files affected:** `sandbox.py` and/or small factory; `connection.py` test path only  
**Dependencies:** 12B.1B.2  
**Exit criteria:** Mocked LWA still used in tests; no row → env path; with reference → provider path; sandbox success still does not persist `connected`

### 12B.1B.4 — Secret reference validation

**Objective:** Org-scoped bind/clear of `token_reference`; get only if it matches the row.  
**Files affected:** `repositories.py` (narrow setter), `connection.py`  
**Dependencies:** 12B.1B.3  
**Exit criteria:** Cross-org reference rejected; public JSON still omits `token_reference`

### 12B.1B.5 — Production provider preparation

**Objective:** Skeleton `AwsSecretsManagerProvider` and/or `EncryptedDatabaseSecretProvider` behind the flag.  
**Files affected:** `secrets_aws.py` and/or `secrets_encrypted.py`; optional later migration for ciphertext table  
**Dependencies:** 12B.1B.1–2  
**Exit criteria:** Default remains development; pytest never calls AWS; PO can choose SM vs encrypted DB without rewriting callers

**Explicitly out of 12B.1B:** OAuth, Connect Amazon UI, `authorization_code`, ingest, Copilot, Skills, frontend.

---

## 10. Risks

1. **Blurring app LWA secret vs seller refresh token** — keep app creds in env; SecretProvider is seller refresh only.
2. **Using sandbox `.env` success as `connected`** — forbidden; already true in 12B.1A.
3. **Exposing `token_reference` on GET** — sanitizer would 500 or leak a handle; keep it off public models.
4. **Calling `get_secret` with a client-supplied string** — only from a loaded org row.
5. **Repository currently cannot set `token_reference`** — intentional 12B.1A; needs a narrow internal API in 12B.1B.4.
6. **Cloud SM not provisioned** — skeleton + encrypted-DB fallback is enough to freeze the boundary.
7. **Local Postgres missing `amazon_connections`** — ops (apply `0007`); not a SecretProvider design issue.

No architecture blockers. ADR 0006 already accepted this boundary.

---

## 11. Final Recommendation

**Approve 12B.1B as specified.** Implement a Protocol + development provider first. Keep 12A sandbox `.env` as the no-row fallback. Do not start OAuth (12B.1C) or ingest.

**First slice after explicit approval:** **12B.1B.1 — SecretProvider interface.**

Wait for that approval before writing code.

---

## Explicit non-goals (this checkpoint)

- Write SecretProvider code
- Create files other than this plan
- Create migrations
- Store credentials
- Add OAuth or seller authorization
- Add token exchange
- Call SP-API
- Modify Copilot, Skills, or frontend

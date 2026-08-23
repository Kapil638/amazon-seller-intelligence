# ADR 0006 — Amazon Connection Credential Boundary

**Status:** Accepted  
**Date:** 23 August 2026  
**Milestone:** 12B.1 — Production Connection Metadata + Secure Token Architecture  
**Deciders:** Principal architecture review ([12B.1 architecture](../milestone-12/milestone-12b1-production-connection-security-architecture.md))  
**Related:** [ADR 0002](0002-amazon-data-provider-separation.md), [ADR 0003](0003-canonical-amazon-seller-data-model.md)

---

## Title

Amazon Connection Credential Boundary

---

## Context

12A stores EWise sandbox LWA client credentials and a sandbox refresh token in process `.env`. That is enough to prove connectivity. It is not a multi-tenant SaaS authorization model.

If refresh tokens are stored as ordinary columns on `amazon_connections`, they leak through backups, logs, admin queries, Copilot, and the frontend. If app LWA `client_secret` is copied per seller, rotation and Amazon’s public-app model are both wrong.

Amazon’s current public-application model is: one LWA application (client id/secret) plus many selling-partner refresh tokens obtained via the website or Appstore authorization workflows.

---

## Decision

1. **Connection metadata** (organization, provider, environment, region, status, selling partner id, timestamps, opaque `token_reference`) is stored in the ASI database.
2. **OAuth/LWA refresh tokens** are stored only in an approved **SecretProvider**. The database stores **`token_reference` only**, never the token.
3. **App-level credentials** (`client_id`, `client_secret`, application id, redirect URIs) are distinct from **seller-level authorization** (refresh token per connection).
4. **Access tokens** are short-lived and are not persisted as business data.
5. **Frontend, Copilot, EvidenceEnvelope, and Skills** cannot access secret material. There is no secret HTTP API. The application resolves `token_reference` only after loading an **organization-scoped** connection row.

Ads API, if added later, uses a **separate** connection row and **separate** token reference.

Sandbox `.env` remains valid for 12A developer connectivity via a development SecretProvider. It is not the production seller-token store.

---

## Consequences

**Positive**

- Seller tokens are not mixed into listing/profit backups as plaintext.
- App client-secret rotation does not rewrite seller rows.
- Connection status can exist without implying Amazon data freshness.

**Negative / accepted**

- Secret manager or encryption-key operations become a runtime dependency for production Test Connection / ingest.
- 12B.1 must implement orphan-secret cleanup if metadata insert fails after `put_secret`.

---

## Not in this decision

Choice of AWS vs GCP vs Azure vs encrypted-Postgres fallback (recommended in 12B.1, selected at 12B.1B), operational Amazon ingest, or Ads OAuth implementation.

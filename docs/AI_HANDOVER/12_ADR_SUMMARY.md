# 12 — ADR Summary

Index: `docs/adr/`. Full text lives in those files. Do not restate decisions here in a way that contradicts the ADR.

## ADR 0001 — Advertising Intelligence domain boundary

Advertising math is its own domain (`ads-calc-v1`) fed by seller inputs / future Ads API. It is not Listing Intelligence and not Copilot invention.

## ADR 0002 — Amazon data provider separation

Rainforest, SP-API, and Ads API stay separate. Do not replace Rainforest with SP-API.

## ADR 0003 — Canonical Amazon seller data model

SP-API DTOs ≠ ASI domain entities. Normalize into canonical models with provenance. 12B.1D still uses Sellers DTOs only for handshake; canonical identity tables start at 12B.2.

## ADR 0004 — Provenance and source precedence

Named source-of-truth. Historical snapshots stay immutable. Unknown remains unknown.

## ADR 0005 — Amazon seller identity model

Listing identity is account + marketplace + SKU (not ASIN alone). Tenancy remains `organization_id`. `selling_partner_id` is Amazon identity, not the ASI tenant key. Tables not built until 12B.2.

## ADR 0006 — Amazon connection credential boundary

Metadata in DB; tokens only via SecretProvider; opaque `token_reference`; never in frontend/Copilot/logs.

If a change would violate an ADR, stop and get explicit architecture approval. Do not “temporarily” violate ADR 0002 or 0006.

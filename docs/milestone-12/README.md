# Milestone 12 — Amazon-connected data backbone

Skill implementation remains paused. This track proves Amazon SP-API connectivity without changing Copilot, ToolRegistry, or intelligence engines.

**Latest completed implementation:** **12B.1D** Seller Connection Validation Using SP-API.  
**Next:** **12B.2** Canonical Seller Identity + Marketplace Ingestion.  
**Ads API** remains **12C**.

Do not start listings/orders/inventory/reports/finances ingest or Ads API from this index.

| Document | Status |
| --- | --- |
| [milestone-12a0-sp-api-sandbox-connectivity.md](milestone-12a0-sp-api-sandbox-connectivity.md) | **12A.0** SP-API sandbox connectivity proof (implemented) |
| [milestone-12a1-amazon-connection-beta.md](milestone-12a1-amazon-connection-beta.md) | **12A.1** Amazon Connection Beta foundation (implemented) |
| [milestone-12b-sp-api-data-backbone-architecture.md](milestone-12b-sp-api-data-backbone-architecture.md) | **12B** Canonical seller-data backbone (architecture approved) |
| [milestone-12b1-production-connection-security-architecture.md](milestone-12b1-production-connection-security-architecture.md) | **12B.1** Production connection + token architecture (architecture approved) |
| [milestone-12b1-implementation-plan.md](milestone-12b1-implementation-plan.md) | **12B.1** Implementation plan (historical; slices 12B.1A–12B.1D are implemented) |
| [milestone-12b1a-connection-metadata-persistence.md](milestone-12b1a-connection-metadata-persistence.md) | **12B.1A** Connection metadata persistence (implemented) |
| [milestone-12b1a1-amazon-connection-metadata-database.md](milestone-12b1a1-amazon-connection-metadata-database.md) | **12B.1A.1** Database model + migration 0007 (implemented) |
| [milestone-12b1a2-amazon-connection-repository.md](milestone-12b1a2-amazon-connection-repository.md) | **12B.1A.2** Repository (implemented) |
| [milestone-12b1a3-amazon-connection-service-overlay.md](milestone-12b1a3-amazon-connection-service-overlay.md) | **12B.1A.3** Service overlay (implemented) |
| [milestone-12b1a4-amazon-connection-api-integration.md](milestone-12b1a4-amazon-connection-api-integration.md) | **12B.1A.4** API integration (implemented) |
| [milestone-12b1a5-amazon-connection-frontend-state.md](milestone-12b1a5-amazon-connection-frontend-state.md) | **12B.1A.5** Frontend connection state (implemented) |
| [milestone-12b1b-secret-provider-architecture.md](milestone-12b1b-secret-provider-architecture.md) | **12B.1B** SecretProvider architecture (approved; implementation in 12B.1B.1–5) |
| [milestone-12b1b1-secret-provider-interface.md](milestone-12b1b1-secret-provider-interface.md) | **12B.1B.1** SecretProvider interface (implemented) |
| [milestone-12b1b2-development-secret-provider.md](milestone-12b1b2-development-secret-provider.md) | **12B.1B.2** DevelopmentSecretProvider (implemented) |
| [milestone-12b1b3-sp-api-secret-provider-integration.md](milestone-12b1b3-sp-api-secret-provider-integration.md) | **12B.1B.3** SP-API client SecretProvider integration (implemented) |
| [milestone-12b1b4-secret-reference-validation.md](milestone-12b1b4-secret-reference-validation.md) | **12B.1B.4** Secret reference validation (implemented) |
| [milestone-12b1b5-production-secret-provider-preparation.md](milestone-12b1b5-production-secret-provider-preparation.md) | **12B.1B.5** Production SecretProvider preparation (fail-closed; implemented) |
| [milestone-12b1c-amazon-seller-authorization-architecture.md](milestone-12b1c-amazon-seller-authorization-architecture.md) | **12B.1C** Seller authorization flow (architecture; code through 12B.1C.5) |
| [milestone-12b1c2-authorize-start-oauth-state.md](milestone-12b1c2-authorize-start-oauth-state.md) | **12B.1C.2** Authorize start + OAuth state (implemented) |
| [milestone-12b1c4a-oauth-callback-foundation.md](milestone-12b1c4a-oauth-callback-foundation.md) | **12B.1C.4A** OAuth callback foundation (implemented) |
| [milestone-12b1c5-lwa-token-exchange.md](milestone-12b1c5-lwa-token-exchange.md) | **12B.1C.5** LWA token exchange + SecretProvider storage (implemented) |
| [milestone-12b1d-seller-connection-validation.md](milestone-12b1d-seller-connection-validation.md) | **12B.1D** Seller connection validation using SP-API (implemented; no ingest) |
| [milestone-12b1c-live-connect-amazon-findings.md](milestone-12b1c-live-connect-amazon-findings.md) | **12B.1C** Live Connect Amazon findings (23–24 Aug 2026 investigation). Code later landed through 12B.1D; live Amazon round-trip still incomplete. |
| [milestone-12b1c-sandbox-production-credential-split.md](milestone-12b1c-sandbox-production-credential-split.md) | **12B.1C** Sandbox vs Draft/production credential split (still in force) |
| [../adr/0002-amazon-data-provider-separation.md](../adr/0002-amazon-data-provider-separation.md) | **ADR 0002** Rainforest / SP-API / Ads API stay separate |
| [../adr/0003-canonical-amazon-seller-data-model.md](../adr/0003-canonical-amazon-seller-data-model.md) | **ADR 0003** SP-API DTOs ≠ ASI domain model |
| [../adr/0004-seller-data-provenance-and-source-precedence.md](../adr/0004-seller-data-provenance-and-source-precedence.md) | **ADR 0004** Provenance and named source-of-truth |
| [../adr/0005-amazon-seller-identity-model.md](../adr/0005-amazon-seller-identity-model.md) | **ADR 0005** Listing identity is account + marketplace + SKU |
| [../adr/0006-amazon-connection-credential-boundary.md](../adr/0006-amazon-connection-credential-boundary.md) | **ADR 0006** Metadata in DB; tokens only via SecretProvider |
| [../AI_HANDOVER/17_CLAUDE_START_HERE.md](../AI_HANDOVER/17_CLAUDE_START_HERE.md) | Claude handover entry point (24 Aug 2026) |

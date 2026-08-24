# 08 — Amazon SP-API Architecture

## Isolation

All SP-API lives in `apps/api/app/amazon/`. DTOs in `models.py` are **external provider DTOs**, not the ASI canonical seller model (ADR 0003).

## What is live in code

| Capability | Milestone | Amazon call? |
| --- | --- | --- |
| Sandbox LWA + Sellers with env refresh token | 12A.0 / 12A.1 Test Connection | Yes (sandbox), not persisted as seller `connected` |
| Persist connection metadata | 12B.1A | No |
| SecretProvider + opaque pointer | 12B.1B | No |
| Authorize start + hashed state | 12B.1C.2 | No (builds consent URL only) |
| OAuth callback foundation | 12B.1C.4A | No |
| Authorization-code → refresh in SecretProvider | 12B.1C.5 | LWA token endpoint only |
| Seller validation handshake | 12B.1D | `GET /sellers/v1/marketplaceParticipations` |

No listings, catalog, orders, inventory, reports, or finances clients.

## Hosts

- Sandbox Sellers: derived from `SP_API_REGION` (default EU sandbox host) or `SP_API_SANDBOX_BASE_URL`
- Production Sellers: derived from region or `SP_API_PRODUCTION_BASE_URL`
- Local Connect Amazon currently creates/uses a **SANDBOX** row. Live seller grant needs **PRODUCTION** environment on the connection row.

## Validation (12B.1D)

`AmazonSellerValidationService`:

1. Resolve `token_reference` via SecretProvider (org-scoped)
2. LWA refresh-token grant using credentials for that environment
3. Sellers `getMarketplaceParticipations`
4. Success → `connected`, optional `selling_partner_id`, `last_successful_validation_at`
5. Invalid grant → `error` / `requires_reauth`, `delete_secret`, `clear_token_reference`
6. Transient Amazon failure → `degraded` (keep secret)
7. Missing participation → identity unavailable (not ingest)

`sellingPartnerId` may be absent; participation can still validate.

## Ingest

None. `last_successful_sync_at` stays null. Do not start ingest before 12B.2 identity tables.

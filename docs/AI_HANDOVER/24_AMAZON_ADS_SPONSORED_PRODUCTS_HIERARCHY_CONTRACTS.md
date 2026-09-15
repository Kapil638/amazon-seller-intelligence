# 24. Amazon Ads Sponsored Products Hierarchy Contracts (PR B1)

Status: **contracts and schema foundation implemented, fixture-tested. No live Amazon call made. No hierarchy synchronization orchestration exists yet (PR B2).**

Contract authority: `docs/AI_HANDOVER/23_AMAZON_ADS_API_OFFICIAL_RESEARCH_AND_INGESTION_BLUEPRINT.md` (verified at PR B1 start: 1,263 lines, 123,564 bytes, SHA-256 `ec987b13b14412e2a859d43d7e2bab642edd0811d82cf48037d5a53b3bbfc482`). This document records implementation status against that blueprint; it is never itself a source of new Amazon facts.

Document 22 does not exist and is not used.

## 1. Confidence key (mirrors the blueprint's own §0.1)

- **Officially documented** — the blueprint extracts this fact from a named official Amazon page/schema.
- **Live-confirmed** — additionally verified against a real production Amazon response (campaigns only, 2026-09-13, prior to this PR).
- **UNCONFIRMED** — this PR's own inference (most often: the confirmed campaigns/ad-groups envelope-key pattern applied to an operation name) where the blueprint itself does not state the fact. Never presented as documented.
- **Fixture-tested** — exercised in this PR's test suite against a synthetic payload shaped to the officially documented (or, where UNCONFIRMED, the inferred) schema. Never exercised against a real Amazon response.

## 2. Five-endpoint evidence matrix

### 2.1 Campaigns

| Field | Value |
|---|---|
| Blueprint section | §10.2 |
| Official URL | `https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod` |
| Endpoint path | `POST /sp/campaigns/list` |
| HTTP method | POST |
| `Content-Type` | `application/vnd.spcampaign.v3+json` (lowercase `spcampaign` — live-confirmed casing; blueprint's own official pages mix `spCampaign`/`spcampaign` capitalization and record this as an official-sources conflict, §7.3) |
| `Accept` | same as `Content-Type` |
| Required headers | `Amazon-Advertising-API-ClientId`, `Authorization: Bearer <token>`, `Amazon-Advertising-API-Scope: <profileId>` |
| Request body | `{ maxResults, nextToken? }` — this PR sends only these two documented fields; `campaignIdFilter`/`nameFilter`/`portfolioIdFilter`/`stateFilter`/`includeExtendedDataFields` are documented but not sent (no filtering need yet) |
| Response envelope | `{ campaigns[], nextToken, totalResults }` — **live-confirmed** |
| Pagination | `nextToken` (string, absent when exhausted); `totalResults` int64, documented but not currently consumed |
| Identifier type | `campaignId` **string** per SP v3 OpenAPI (official columns-glossary page types the same name **Integer** — §7.3 conflict; this codebase persists a canonical string via `normalize_ads_entity_id`, accepting a lossless int→str conversion and rejecting bool/float/blank) |
| Officially documented state values | `ENABLED, PAUSED, ARCHIVED, PROPOSED, ENABLING, USER_DELETED, OTHER` — full enum given explicitly |
| Documented parent identifiers | none (campaign is the hierarchy root); `portfolioId` present as an **opaque** external reference only (blueprint §10.5: Phase A does not call `/portfolios/list`) |
| Fields selected for persistence | `campaignId`, `name`, `state`, `targetingType`, `budget.budget`/`budget.budgetType`, `startDate`/`endDate`, `portfolioId` (opaque) |
| Unresolved | Client-ID header-name conflict (§7.1) and `Amazon-Ads-AccountId` requirement conflict (§7.2) — both are Reporting v3/cross-cutting concerns, not specific to this endpoint; this client fails closed on 401 rather than guessing a second header |
| Classification | **Live-confirmed** (media type, envelope key, path); **fixture-tested** for the full 7-value state enum and identifier normalization |

### 2.2 Ad groups

| Field | Value |
|---|---|
| Blueprint section | §10.3 |
| Official URL | `https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod` |
| Endpoint path | `POST /sp/adGroups/list` |
| HTTP method | POST |
| `Content-Type` | `application/vnd.spAdGroup.v3+json` — **officially documented** |
| `Accept` | same |
| Required headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` (§10.3's own table states these explicitly) |
| Request body | `{ maxResults, nextToken? }` sent; `adGroupIdFilter`/`campaignIdFilter`/`campaignTargetingTypeFilter`/`nameFilter`/`stateFilter`/`includeExtendedDataFields` documented, not sent |
| Response envelope | `{ adGroups[0–1000], nextToken, totalResults }` — **officially documented** |
| Pagination | `nextToken`; `totalResults` |
| Identifier type | `adGroupId`, `campaignId` — normalized via the same canonical-string function |
| Officially documented state values | **Not enumerated** by §10.3 (required field, no value list) — treated as `SIBLING_ENTITY_STATES` (`ENABLED, PAUSED, ARCHIVED`) only, matching the shared `stateFilter` convention; do not assume the campaign enum |
| Documented parent identifiers | `campaignId` (required) |
| Fields selected for persistence | `adGroupId`, `campaignId`, `name`, `state`, `defaultBid` |
| Unresolved | State enum beyond the 3 confirmed values; whether `includeExtendedDataFields` unlocks additional fields this PR does not request |
| Classification | **Officially documented** (media type, envelope key, request/response item field names — §10.3 gives a full `Required:`/`Optional:` breakdown); **fixture-tested**; **not** live-confirmed |

### 2.3 Product ads (advertised products)

| Field | Value |
|---|---|
| Blueprint section | §10.4 |
| Official URL | `https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod` |
| Endpoint path | `POST /sp/productAds/list` |
| HTTP method | POST |
| `Content-Type` | `application/vnd.spProductAd.v3+json` — **officially documented** |
| `Accept` | same |
| Required headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` (general convention, §6.6; not repeated verbatim in §10.4's own table) |
| Request body | `{ maxResults, nextToken? }` sent; `adGroupIdFilter`/`adIdFilter`/`campaignIdFilter`/`stateFilter`/`includeExtendedDataFields` documented, not sent |
| Response envelope | **UNCONFIRMED** — §10.4 documents the response *item* schema (`SponsoredProductsProductAd`) but its table has no "Response envelope" row at all, unlike §10.2/§10.3. This codebase uses `"productAds"`, inferred from the operation name (`ListSponsoredProductsProductAds`) and the confirmed campaigns/ad-groups pattern — **not itself extracted from an official page** |
| Pagination | `nextToken` assumed by the same pattern; not independently documented for this operation either |
| Identifier type | `adId`, `adGroupId`, `campaignId` — normalized |
| Officially documented state values | **Not enumerated** — `SIBLING_ENTITY_STATES` only |
| Documented parent identifiers | `adGroupId`, `campaignId` (both required per §10.4's `Required:` list) |
| Fields selected for persistence | `adId`, `adGroupId`, `campaignId`, `state`, `asin` (vendor-only), `sku` (seller-only) |
| Unresolved | **Envelope key** (highest-priority open item for PR B2/live validation); state enum |
| Classification | Media type and response-item schema **officially documented**; envelope key **UNCONFIRMED**; **fixture-tested** against the inferred shape; **not** live-confirmed |

### 2.4 Keywords

| Field | Value |
|---|---|
| Blueprint section | §11.5 |
| Official URL | `https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod` |
| Endpoint path | `POST /sp/keywords/list` |
| HTTP method | POST |
| `Content-Type` | `application/vnd.spKeyword.v3+json` — **officially documented** |
| `Accept` | same |
| Required headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` (general convention) |
| Request body | `{ maxResults, nextToken? }` sent; `adGroupIdFilter`/`campaignIdFilter`/`keywordIdFilter`/`keywordTextFilter`/`locale`/`matchTypeFilter`/`stateFilter`/`includeExtendedDataFields` documented, not sent |
| Response envelope | **UNCONFIRMED** — §11.5 documents no response schema at all (no item schema, no envelope). This codebase uses `"keywords"`, inferred the same way as product ads |
| Pagination | assumed `nextToken`, not documented for this operation |
| Identifier type | `keywordId`, `adGroupId`, `campaignId` — normalized |
| Officially documented state values | **Not enumerated** — `SIBLING_ENTITY_STATES` only |
| Documented parent identifiers | `adGroupId`, `campaignId` implied by the request filters (`adGroupIdFilter`, `campaignIdFilter`); not stated as required response fields since no response schema is documented |
| Fields selected for persistence | `keywordId`, `adGroupId`, `campaignId`, `keywordText`, `matchType`, `state`, `bid` — **field NAMES themselves are UNCONFIRMED**, inferred from the documented request filter names (`keywordTextFilter` → `keywordText`, `matchTypeFilter` → `matchType`) and this codebase's pre-existing implementation, not extracted from an official response schema |
| Unresolved | Envelope key; full response item schema; the blueprint's own flagged permission gap (§11.5: `advertiser_campaign_view` is not listed as a valid permission for this operation — a view-only grant may be unable to list keywords at all) |
| Classification | Media type **officially documented**; envelope key AND response field names **UNCONFIRMED**; **fixture-tested** against the inferred shape only; **not** live-confirmed |

### 2.5 Product targets

| Field | Value |
|---|---|
| Blueprint section | §11.6 |
| Official URL | `https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod` |
| Endpoint path | `POST /sp/targets/list` |
| HTTP method | POST |
| `Content-Type` | `application/vnd.spTargetingClause.v3+json` — **officially documented** |
| `Accept` | same |
| Required headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` (general convention) |
| Request body | `{ maxResults, nextToken? }` sent; `adGroupIdFilter`/`asinFilter`/`campaignIdFilter`/`expressionTypeFilter`/`stateFilter`/`targetIdFilter`/`includeExtendedDataFields` documented, not sent |
| Response envelope | **UNCONFIRMED** — §11.6 documents no response schema. This codebase uses `"targetingClauses"`, inferred from the operation name (`ListSponsoredProductsTargetingClauses`) |
| Pagination | assumed `nextToken` |
| Identifier type | `targetId`, `adGroupId`, `campaignId` — normalized |
| Officially documented state values | **Not enumerated** — `SIBLING_ENTITY_STATES` only |
| Documented parent identifiers | `adGroupId`, `campaignId` implied by request filters |
| Fields selected for persistence | `targetId`, `adGroupId`, `campaignId`, `expressionType`, `expression`, `state`, `bid` — **field names UNCONFIRMED**, same basis as keywords |
| Unresolved | Envelope key; full response item schema |
| Classification | Media type **officially documented**; envelope key AND response field names **UNCONFIRMED**; **fixture-tested** only; **not** live-confirmed |

## 3. State enums implemented

| Entity | Constraint (DB) | Enum enforced | Source |
|---|---|---|---|
| Campaign | `ck_amazon_ads_campaigns_state` (migration `0020_ads_campaign_state_enum`) | `ENABLED, PAUSED, ARCHIVED, PROPOSED, ENABLING, USER_DELETED, OTHER` | Blueprint §10.2, `SponsoredProductsCampaign.state`, fully enumerated |
| Ad group | `ck_amazon_ads_ad_groups_state` (unchanged since `0019`) | `ENABLED, PAUSED, ARCHIVED` | Not enumerated by §10.3 — left at the pre-existing, narrower value set |
| Product ad | `ck_amazon_ads_advertised_products_state` (unchanged) | `ENABLED, PAUSED, ARCHIVED` | Not enumerated by §10.4 |
| Keyword | `ck_amazon_ads_keywords_state` (unchanged) | `ENABLED, PAUSED, ARCHIVED` | Not enumerated by §11.5 |
| Product target | `ck_amazon_ads_product_targets_state` (unchanged) | `ENABLED, PAUSED, ARCHIVED` | Not enumerated by §11.6 |

`app.amazon.ads_models.CAMPAIGN_STATES` / `SIBLING_ENTITY_STATES` are the offline-parser equivalents of these two DB constraints — kept in sync deliberately (widening one without the other would let the parser accept an item the database would then reject).

## 4. Migration

`migrations/versions/0020_ads_campaign_state_enum.py` — revises `0019_amazon_ads_foundation`, now the single Alembic head. Widens **only** `amazon_ads_campaigns.state`'s CHECK constraint; touches no other table, no column type, no data. `downgrade()` refuses if any campaign row holds a state the pre-0020 three-value constraint cannot represent. Not applied to production.

## 5. Per-item parsing contract

`app.amazon.ads_client.parse_entity_list_envelope()` (+ `EntityParseResult`) — offline, reusable, independently unit-tested against synthetic fixtures (no HTTP). For one page of any entity-list response:

- validates every item independently (one malformed item never discards its valid siblings);
- reports `total_items` / `accepted_items` / `schema_rejected_items` / `unsupported_state_items` separately;
- exposes `is_contract_mismatch` (`total_items > 0 and accepted_items == 0`) — a nonempty, all-rejected page is never indistinguishable from an ordinary empty one.

This is the function PR B2's orchestration is expected to call directly; PR B1 does not itself call it from any synchronization loop, checkpoint, or persistence-writing code path.

### 5.1 Top-level envelope truth table (second review)

A missing or null entity key must never be silently read as "zero entities" — the envelope key itself is an unconfirmed inference for product ads/keywords/product targets (§2.3–2.5), so a wrong inference must surface as a contract mismatch, not vanish as an empty page:

| `payload[response_key]` | Result |
|---|---|
| key absent | raises `AdsApiParseFailedError` |
| `null` | raises `AdsApiParseFailedError` |
| present, not a JSON array | raises `AdsApiParseFailedError` |
| `[]` | valid, genuinely empty page |

Every raised message names only `response_key` itself (a constant this module already knows) — never the response body or any entity data.

### 5.2 Pagination-token truth table (second review)

| `payload["nextToken"]` | Result | Basis |
|---|---|---|
| absent | pagination complete (`next_token=None`) | Blueprint §13.4/§17: "Follow `nextToken` until absent" |
| non-empty string | returned exactly as received (never trimmed/transformed) | No official source states this opaque token has trim-safe whitespace |
| `null` | pagination complete (`next_token=None`) | **Not** stated by document 23's own prose, which only ever says "until absent". This codebase's own live-confirmed `POST /sp/campaigns/list` response has its observed terminal page send `"nextToken": null` rather than omit the key. Labeled **Production-observed but not contract authority** per the blueprint's own §0.1 confidence tier — deliberately never asserted as something document 23 itself permits. Decision confirmed with the operator during PR B1's second review specifically to avoid this client raising a parse error on the exact, already-verified shape of the one endpoint confirmed live |
| blank/whitespace-only string | raises `AdsApiParseFailedError` | Cannot function as a continuation token |
| any other type (number, bool, array, object) | raises `AdsApiParseFailedError` | Not an opaque token |

## 6. Identifier normalization

`app.amazon.ads_models.normalize_ads_entity_id()`, applied via `field_validator(mode="before")` to every own-id and parent-id field on all five entity DTOs (and, separately, `portfolioId` — opaque but still normalized).

Accepted: non-blank string (whitespace-trimmed), Python `int` (lossless `str()` conversion, arbitrary precision). Rejected: `bool`, `float` (even integral), blank/whitespace-only string, any other type. Test coverage: `tests/test_amazon_ads_client.py` — integer, string, whitespace-padded string, oversized integer (precision-preserving), boolean, float, blank string, `None`, list, dict.

## 6.1 Page-size bound (second review — ownership clarified)

`Settings.ads_entity_list_page_size` (default 100, bounded 1–1000) exists but is **not read by `ads_client.py` itself** — `page_size` is always an explicit per-call argument on `list_campaigns()`/`list_ad_groups()`/etc. `HttpAmazonAdsApiClient._list_entities()` validates that argument against the same 1–1000 range and **rejects** (`ValueError`, raised before any HTTP request) a value outside it — it no longer silently clamps, since clamping could mask a caller-side configuration error instead of surfacing it. PR B1 introduces the setting but wires nothing to read it; PR B2's orchestration is the intended reader, expected to pass `settings.ads_entity_list_page_size` as `page_size` on each list call. The 1000 ceiling itself remains a conservative, undocumented-maximum stand-in (blueprint §13.4: SP v3's own exact `maxResults` ceiling is "Not documented"), borrowed from Ads API v1's sibling `SPQueryCampaign` operation.

## 7. Hierarchy

`organization → Ads connection → advertiser profile → campaign → ad group → {product ad | keyword | product target}`, matching the pre-existing `amazon_ads_*` schema (migration `0019`) and its repositories (`app/persistence/repositories.py`, `_AmazonAdsEntityRepositoryBase` and subclasses — already present before this PR, untouched by it). `ads_campaign_id`/`ads_ad_group_id` foreign keys are required, non-nullable columns; the repository `upsert()` methods require the caller to resolve and pass them explicitly — there is no code path that fabricates a parent. Missing-parent detection, rejection counting, and reconciliation orchestration are explicitly PR B2 scope, not implemented here.

## 8. What PR B1 deliberately does not do

- No hierarchy synchronization orchestration, checkpoint, or worker/scheduler wiring.
- No live Amazon call, anywhere, including in tests.
- `ads_entity_sync_service.py` remains absent from this PR (and from the repository generally — see the PR B1 kickoff exchange; it does not currently exist to preserve).
- No portfolio listing (`POST /portfolios/list`) — `portfolioId` stays an opaque string.
- No Reporting v3, attribution-window, or Ads-to-SP-API join changes.
- No Copilot or frontend changes.
- `ADS_API_BACKEND` remains `disabled`.

## 9. Unresolved contract uncertainties carried into PR B2

1. **Envelope key for product ads, keywords, product targets** (`productAds`, `keywords`, `targetingClauses`) — inferred, not documented. Highest-priority item for a future supervised live-validation pass, exactly as `list_campaigns` was independently verified on 2026-09-13 before this pattern was trusted for campaigns.
2. **Response item field names for keywords and product targets** — no official schema exists in the blueprint for either; current fields are a best-effort carryover from this codebase's pre-existing (already fixture-only) implementation.
3. **State enum for ad groups/product ads/keywords/product targets** — blueprint requires a `state` field but never enumerates its values for these four entities. Left at the pre-existing three-value set; do not widen without either an equivalent documented enum or independent live verification.
4. **Client-ID header name** (blueprint §7.1) and **`Amazon-Ads-AccountId`** (§7.2) — both remain open official-sources conflicts, orthogonal to this PR's endpoints; this client continues to fail closed rather than guess.
5. **`includeExtendedDataFields`** — documented as a request option on all five endpoints; not sent by this PR, so `extendedData.creationDateTime`/`lastUpdateDateTime`/`servingStatus` are not requested or persisted.

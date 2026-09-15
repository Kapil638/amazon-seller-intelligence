# Amazon Ads API Official Research and Ingestion Blueprint

**Completeness gate:** **Research complete — ready for operator review**

Status: **research and design only**. This document does not authorize ingestion, schema changes, live Amazon Ads calls, worker enablement, or configuration changes. Operator review of this blueprint is the next gate; Phase A implementation is not started by this document.

Date of official-docs review: **14 September 2026** (first pass, completion pass, and final official-documentation pass, same calendar day).

Related internal documents (implementation status only, never Amazon contract authority):

- `docs/AI_HANDOVER/21_AMAZON_ADS_READONLY_FOUNDATION.md`

This blueprint is the Amazon contract authority for Ads API work.

---

## 0. Completeness gate and blockers

The gate **Research complete — ready for operator review** requires all of: complete Phase A contracts; complete Phase B priority contracts; Ads API v1 versus Sponsored Products v3 decided or explicitly blocked; authorization and account context documented; report-specific retention recorded; official conflicts separated from observed behavior; every Phase A/B technical claim has an exact official URL; remaining unknowns have impact and a resolution path; no community source used as contract authority.

**Result: pass for operator review.** Phase A ingestion is still **not authorized** until the operator accepts this blueprint. Remaining unknowns in §17 do not prevent a Phase A design.

### Previously open blockers (resolved this pass)

| ID | Resolution | Official URL |
|---|---|---|
| B1 | Campaign-management overview rendered. Ads API v1 is the ad-product-agnostic campaign-management family; it currently supports DSP, SB, SP, SD, ST; **at launch, one ad product per call**. It does **not** deprecate SP v3 or give a shutdown date. Reporting is still a separate family. | https://advertising.amazon.com/API/docs/en-us/guides/campaign-management/overview · entity matrix https://advertising.amazon.com/API/docs/en-us/guides/campaign-management/entities/campaign |
| B2 | List 200 schema is `SponsoredProductsCampaign`. Official download: https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/SponsoredProducts_prod_3p.json (linked from the OpenAPI page as “Download Version 3 OpenAPI spec”). The rendered list **sample** remains `{ }`; the schema, not the empty sample, is the contract. | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod |
| B3 | Sponsored Products is supported on v1 `SPQueryAdGroup`, `SPQueryAd`, `SPQueryTarget` (`POST /adsApi/v1/query/adGroups|ads|targets`). Media type `application/json`. SP campaign object has **no** `targetingType`; AUTO/MANUAL analogue is `autoCreationSettings.autoCreateTargets`. | https://advertising.amazon.com/API/docs/en-us/api-spec-v1-adgroups · Ads spec https://advertising.amazon.com/API/docs/en-us/api-spec-v1-ads · Targets https://advertising.amazon.com/API/docs/en-us/api-spec-v1-targets · SP merged OpenAPI https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/AmazonAdsAPISPMerged_prod_3p.json |
| B4 | Portfolios Version 3. Read: `POST /portfolios/list`. Names/states **excluded from Phase A required endpoints**; persist opaque `campaign.portfolioId` only. Listing names is Phase B. Mutations out of scope. | https://advertising.amazon.com/API/docs/en-us/guides/portfolios/get-started · https://advertising.amazon.com/API/docs/en-us/reference/portfolios · OpenAPI https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/Portfolios_prod_3p.json |
| B5 | Columns glossary (`h1` Metrics) extracted for the Phase A/B set: Type, Description, Report types. Additivity is **Not documented** on that page. Identifier types on the glossary (`Integer`) conflict with SP v3 OpenAPI (`string`) — §7.3. | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/columns |
| B6 | Catalogued in §12.1. `reportTypeId` values: `spGrossAndInvalids` / `sbGrossAndInvalids` / `sdGrossAndInvalids` (report-type page) vs glossary `spGrossandInvalids` (casing conflict). Prompt: `spPromptAdExtension` / `sbPromptAdExtension`. Video: `spVideoAdExtension`. All deferred after Phase B. | URLs in §12.1 |
| B7 | Budget usage guide + SP v3 budget/recommendation operations + Ads API v1 Recommendations API (Aug 2026; current types do **not** replace SP budget/keyword/bid recs). Applying a recommendation is a write — out of scope. | https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/overview · https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/getting-started · https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/overview · https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/recommendation-types |
| B8 | Deprecations index does **not** list SP v3 campaign-management or Reporting v3. SP v2 reporting `/v2/sp/{recordType}/report` shut off **30 Mar 2023**. Profiles `/v1/profiles` shut off **28 May 2024**; replacement is `GET /v2/profiles`. SP v3 list APIs are **not** called deprecated. | https://advertising.amazon.com/API/docs/en-us/reference/deprecations · https://advertising.amazon.com/API/docs/en-us/release-notes/deprecations · https://advertising.amazon.com/API/docs/en-us/reference/1/reports |
| B9 | Manager accounts guide extracted. `profileId` of a **linked advertiser** is the Scope for sponsored-ads calls. Manager accounts are not API-global: only the region of the linked account. DSP reporting uses `dspAdvertiserId` as account id. | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/manager-accounts |
| B10 | Stream, AMC, Attribution, Benchmarks overviews extracted. Dedicated URL `/guides/dsp/overview` is **not found**; working DSP docs include Guidance and Quick Actions. Stream datasets catalogued from the data guide. | URLs in §15 |

### Remaining unknowns that do **not** block Phase A

These stay in §17. They are not reopened as B1–B10.

| Unknown | Impact | Phase |
|---|---|---|
| Header ClientId name conflict (`Amazon-Ads-ClientId` vs `Amazon-Advertising-API-ClientId`) | Phase A uses the SP v3 / Reporting v3 / Profiles family only and fails closed on 401/400. Amazon Support still needed before dual-stack. | A operations, not A design |
| Glossary `campaignId`/`adGroupId` Type **Integer** vs SP v3 OpenAPI **string** | Persist as canonical strings after lossless conversion; reject bool/float. | A |
| Exact numeric `maxResults` default on SP v3 lists | Follow `nextToken` until absent; official text is “max page size for given API”. | A |
| Report download URL TTL | Re-request if the presigned URL expires; TTL **Not documented**. | A ops |
| Whether v1 `campaignId` equals SP v3 `campaignId` | Irrelevant if Phase A does not dual-stack. | Later v1 migration |
| `Amazon-Ads-AccountId` on SP Reporting v3 | Phase A omits it and fails closed; Support needed before adding it | A ops |
| Column additivity | Glossary has no Additivity field. Conservative non-additivity rules in §14.2 remain. | A/B Copilot |
| Dedicated Amazon DSP “overview” URL | `/guides/dsp/overview` returned not found. DSP remains Phase E; use `/guides/dsp/guidance-and-quick-actions` and Reporting v3 DSP types. | E |
| Stream message ordering | FIFO queues are not supported. Ordering of delivered messages is **Not documented**. | D |
| Stream replay | FAQ has no self-serve replay. Missing data: DLQ + support. | D |

No community source is used as contract authority. Official Amazon-hosted OpenAPI JSON files linked from Advanced Tools Center “Download … spec” buttons are treated as official schema pages. GitHub discussions are **not** used.

---

## 0.1 Confidence labels

Every technical contract in this document uses one of:

- **Officially verified** — taken from a rendered official Amazon page named next to the claim.
- **Official sources conflict** — two official Amazon pages disagree; both are kept.
- **Not documented** — the extracted official page did not state the field.
- **Official page inaccessible** — this pass could not render the page; contract not inferred.
- **Eligibility dependent** — official page states preview, beta, Brand Registry, manager, DSP, or similar gating.
- **Production-observed but not contract authority** — a prior live Amazon response or EWise code path; never used to close an official conflict.

Do not read “verified” as “EWise currently works.”

---

## 1. Executive summary

Amazon Ads is not one API with one version. The official Advanced Tools Center presents independently versioned surfaces:

1. **Amazon Ads API v1** — common-model campaign management. Officially recommended for *new* campaign-management work. Reporting is explicitly **not yet** generated from this common model. Source: [Amazon Ads API v1 overview](https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/overview) and [Getting started with Amazon Ads API v1](https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/getting-started). Confidence: **Officially verified**.
2. **Product-specific APIs** (nav label **Amazon Ads API v0**), including Sponsored Products **Version 3** OpenAPI. Source: [API overview](https://advertising.amazon.com/API/docs/en-us/reference/api-overview) and [Sponsored Products v3 OpenAPI](https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod). Confidence: **Officially verified**.
3. **Reporting version 3** — async `POST /reporting/reports` with per-ad-product `reportTypeId`. Independent of Ads API v1 and of SP entity Version 3. Source: [Reporting v3 overview](https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/overview) and [Get started](https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/get-started). Confidence: **Officially verified**.

**Phase A campaign-management recommendation:** use **Sponsored Products Version 3 product-specific read APIs** for campaign / ad-group / advertised-product listing, and **Reporting v3** for performance. Do **not** build Phase A hierarchy on Ads API v1. Official support for SP on v1 exists (`SPQueryCampaign` / `SPQueryAdGroup` / `SPQueryAd` / `SPQueryTarget`), but v1 still lacks a first-class `targetingType`, uses a different header/media-type family, and does not include reporting. Long-term, revisit v1 after Amazon documents targeting type, ID-equivalence with Reporting v3, and product-specific replacements. Reasons: §5. Confidence: **Officially verified** for the facts cited.

EWise has a read-only Ads foundation (OAuth, profiles, Reporting v3 `spCampaigns`, report-run ledger, campaign daily facts, fail-closed HTTP client). That foundation is not a complete Ads intelligence layer.

EWise remains **read-only**. Mutation endpoints are catalogued and marked out of scope.

---

## 2. Official-source policy

Source hierarchy:

1. Exact Amazon Ads API endpoint, schema, or report reference pages on [advertising.amazon.com/API/docs](https://advertising.amazon.com/API/docs).
2. Official Amazon release, migration, and deprecation notices on the same site.
3. Official Amazon guides for business interpretation on the same site.
4. Official Amazon-maintained GitHub (`amzn/ads-advanced-tools-docs`) as supplementary evidence only.
5. Existing EWise code only to classify **current implementation status**.

Not used as contract authority: blogs, Stack Overflow, community SDKs, search-result summaries, Airbyte/Openbridge/WithOne, or copied unofficial OpenAPI files.

HTTP `GET` of Advanced Tools Center URLs returns a client-rendered SPA shell. Official pages were opened in a browser and the **rendered text** was extracted. Unauthenticated pages visited returned documentation content; a “Sign in with your Amazon Developer account” control is present and was not used.

A live Amazon response may validate accepted production behavior. It does **not** supersede an official contract.

---

## 3. API terminology and independent versioning

There is **no universal Amazon Ads v1** that covers reporting, entities, and streaming.

| Official name | What it versions | Official URL | Reporting? |
|---|---|---|---|
| Amazon Ads API v1 | Common-model campaign management across ad products | https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/overview | **Not yet.** Future vision lists reporting, recommendations, rules, media planning as expected expansions. |
| Amazon Ads API v0 (nav label) | Product-specific families | https://advertising.amazon.com/API/docs/en-us/reference/api-overview | Reporting is a sibling family under this nav. |
| Sponsored Products Version 3 | SP campaign-management OpenAPI (`OAS 3.0.1` on the rendered page) | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod | No. Entity CRUD/list only. |
| Sponsored Products Version 2 | Older SP OpenAPI | https://advertising.amazon.com/API/docs/en-us/sponsored-products/2-0/openapi | **Deprecated for new work.** |
| Reporting version 3 | Async `POST /reporting/reports` | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/overview | Yes. Current official sponsored-ads reporting path. |
| Reporting version 2 | Migrated-from family | https://advertising.amazon.com/API/docs/en-us/reference/migration-guides/reporting-v2-v3 | **Deprecated for new work.** FAQ: SP v2 reporting endpoints deprecated **30 March 2023**. |
| Marketing Stream | Push datasets, not Reporting v3 | https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-stream/overview | Near-real-time alternative. |

Official API hosts ([API overview](https://advertising.amazon.com/API/docs/en-us/reference/api-overview)):

| URL | Region | Marketplaces listed on that page |
|---|---|---|
| `https://advertising-api.amazon.com` | North America (NA) | US, CA, MX, BR |
| `https://advertising-api-eu.amazon.com` | Europe (EU) | UK, FR, IT, ES, DE, NL, AE, PL, TR, EG, SA, SE, BE, IN, ZA |
| `https://advertising-api-fe.amazon.com` | Far East (FE) | JP, AU, SG |

---

## 4. Report-specific retention and range matrix

These are **not** official conflicts. Each report type publishes its own retention and maximum request range. Do not create one global Sponsored Products retention rule.

Data latency below is from the Reporting FAQ unless a report-type page states otherwise. FAQ: initial impression/click data within **12 hours**; small traffic-validation changes up to **three days**; initial conversion data within **24 hours**; conversion restatements **1, 7, and 28 days** after the conversion event; because conversions are reported on the ad-interaction date, a 14-day attribution window can restate up to **42 days** (14 + 28) after the report date. Generation can take **up to three hours**. Recommended pull rate: **1–2 report requests per advertiser per day per report type**. Source: [Reporting FAQ](https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/faq). Confidence: **Officially verified**.

FAQ also states a *general* “historical reporting window of up to 95 days.” That is **not** a global SP rule. Search-term, SB, and SD pages publish different retentions. Treat the FAQ sentence as a version-3 marketing maximum, then obey the report-type page. Confidence: **Officially verified** (both statements).

### 4.1 Sponsored Products

| reportTypeId | Max historical lookback | Max request date range | timeUnit | Attribution windows in official base columns | Data latency | Traffic eligibility | Official URL |
|---|---|---|---|---|---|---|---|
| `spCampaigns` | 95 days | 31 days | `SUMMARY` or `DAILY` | Click 1d/7d/14d/30d purchases, sales, units; same-SKU variants. No view-through in the SP campaign base list | FAQ 12h traffic / 24h conversions / 3-day traffic restatement / 1-7-28 conversion restatement | Impression and click traffic columns present; conversions are click-attributed windows | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/campaign |
| `spTargeting` | 95 days | 31 days | `SUMMARY` or `DAILY` | Click 1d/7d/14d/30d plus `salesOtherSku7d` / `unitsSoldOtherSku7d` | Same FAQ | Traffic + conversions | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/targeting |
| `spSearchTerm` | **65 days** | 31 days | `SUMMARY` or `DAILY` | Click 1d/7d/14d/30d plus other-SKU 7d | Same FAQ | **Click-gated:** “only include impressions that resulted in at least one ad click.” Console includes impression-only terms; API does not ([FAQ](https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/faq)) | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/search-term |
| `spAdvertisedProduct` | 95 days | 31 days | `SUMMARY` or `DAILY` | Click 1d/7d/14d/30d plus other-SKU 7d | Same FAQ | Traffic + conversions | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/advertised-product |
| `spPurchasedProduct` | 95 days | 31 days | `SUMMARY` or `DAILY` | 1d/7d/14d/30d units/sales/purchases and other-SKU at those windows | Same FAQ | **Conversion-only.** No impressions/clicks/cost in the official SP base list | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/purchased-product |

`spCampaigns` groupBy values: `campaign`, `adGroup`, `campaignPlacement`. There is **no** dedicated SP ad-group or SP placement report type.

### 4.2 Sponsored Brands and Sponsored Display (context only; Phase C)

| reportTypeId | Max lookback | Max request range | Notes | Official URL |
|---|---|---|---|---|
| `sbCampaigns` / `sbTargeting` / `sbSearchTerm` | 60 days | 31 days | SB reporting is **preview**; `isMultiAdGroupsEnabled=False` omitted until GA | campaign / targeting / search-term report-type pages |
| `sbPurchasedProduct` | **731 days** | **731 days** | Conversion-oriented; no cost/clicks/impressions in official base list | purchased-product report-type page |
| `sdCampaigns` / `sdTargeting` / `sdAdvertisedProduct` / `sdPurchasedProduct` | 65 days | 31 days | View-attributed metrics are first-class on SD | same family of report-type pages |

---

## 5. Campaign-management API decision (Ads API v1 versus Sponsored Products v3)

### 5.1 Official comparison

| Criterion | Ads API v1 (SP product filter) | Sponsored Products Version 3 | Confidence |
|---|---|---|---|
| Current GA | [v1 getting started](https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/getting-started): “All clients who have completed onboarding … are permitted to call the Ads API v1 generally.” Some resources are closed beta | SP v3 OpenAPI is the current product-specific campaign-management spec. SP **Version 2** is the deprecated sibling | **Officially verified** |
| Supported SP entities in extracted specs | Campaigns `SPQueryCampaign`; Ad groups `SPQueryAdGroup` `POST /adsApi/v1/query/adGroups`; Ads `SPQueryAd` `POST /adsApi/v1/query/ads`; Targets `SPQueryTarget` `POST /adsApi/v1/query/targets`. Required `adProductFilter`. Media `application/json` | Campaigns, ad groups, product ads, keywords, targeting clauses, negative keywords, negative targeting, budget usage, budget recommendations | **Officially verified** |
| List/read coverage | `SPQueryCampaign`: required `adProductFilter`; optional campaignId/name/portfolio/state filters; `maxResults` 1–1000 default 1000; `nextToken`. Same pagination on AdGroup/Ad/Target query | `ListSponsoredProductsCampaigns` `POST /sp/campaigns/list` with equivalent filters plus `includeExtendedDataFields` | **Officially verified** |
| Marketplace availability | v1 OpenAPI ad-product selector includes Sponsored Products. Hosts are the same NA/EU/FE advertising-api hosts | Same regional hosts. Profiles are region-scoped | **Officially verified** |
| Required scopes | v1 OpenAPI: OAuth2; permissions include `advertiser_campaign_view` / `campaign_view`. LWA scope for most Ads API use is `advertising::campaign_management` ([Authorization grants](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/authorization-grants)) | Same LWA scope. List operations: `advertiser_campaign_edit` or `advertiser_campaign_view` | **Officially verified** |
| Headers | Required: `Amazon-Ads-ClientId`. Optional in schema: `Amazon-Ads-AccountId`, `Amazon-Advertising-API-Scope`. Getting started: Scope required for most sponsored ads; AccountId required for **ADSP and cross-product**, not stated as required for SP-only | Required: `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` | **Official sources conflict** on ClientId name — see §7 |
| Pagination | `nextToken` + `maxResults` (1–1000 SP) | `nextToken` + `maxResults` + `totalResults` on list campaigns | **Officially verified** |
| Response schema | Envelope `{ campaigns, nextToken }`. `SPCampaign`: `adProduct`, `autoCreationSettings.autoCreateTargets` (required), `budgets[]`, `campaignId` string, `optimizations`, `portfolioId`, `state`, `status.deliveryStatus`. **No `targetingType` field** on `SPCampaign` or the v1 entity matrix | Envelope `{ campaigns, nextToken, totalResults }`. Items are `SponsoredProductsCampaign`. Required: `budget`, `campaignId`, `name`, `startDate`, `state`, `targetingType` (`AUTO`/`MANUAL`). Nested `budget.budget` + `budgetType` (`DAILY`/`OTHER`), `dynamicBidding.strategy`, `dynamicBidding.placementBidding`, `portfolioId`, `extendedData` when requested. Rendered list **sample** remains `{ }` | **Officially verified** on OpenAPI schemas. Empty sample is a docs defect, not a missing type |
| Budget coverage | `budgets[]` monetary + `recurrenceTimePeriod: DAILY` | `SponsoredProductsBudget`: required `budget` (double) + `budgetType`; optional `effectiveBudget` | **Officially verified** |
| Targeting coverage | `SPQueryTarget` supported. `SPTargetType` includes `THEME` (“formerly known as Auto Targets for Sponsored Products”). Campaign-level AUTO vs MANUAL is `autoCreateTargets`, not `targetingType` | Keywords `POST /sp/keywords/list`; targeting clauses `POST /sp/targets/list`; campaign `targetingType` AUTO/MANUAL; ad-group list filter `campaignTargetingTypeFilter` | **Officially verified** |
| Negative targeting | `SPQueryTarget` has `negativeFilter` / `negative` boolean on `SPTarget` | `POST /sp/negativeKeywords/list`, `POST /sp/negativeTargets/list` | **Officially verified** |
| Advertised products | `SPQueryAd`: `SPAd.creative.productCreative.productCreativeSettings.advertisedProduct` with `productId` + `productIdType` `ASIN`/`SKU`. Top-level `asin`/`sku` **not** on `SPAd` | `POST /sp/productAds/list`, media `application/vnd.spProductAd.v3+json`. `asin` vendor-only; `sku` seller-only | **Officially verified** |
| Portfolios | `portfolioId` / `portfolioIdFilter` on `SPQueryCampaign`. No Portfolios family in Ads API v1 API Specifications | `portfolioId` on `SponsoredProductsCampaign`. Separate Portfolios v3: `POST /portfolios/list` | **Officially verified** |
| Recommendations | v1 Recommendations API (GA Aug 2026) is a **separate** common-model surface. Current types: `REMOTE_CAMPAIGN_PAUSED`, `AUTOMATIC_GLOBAL_ADS_ENROLLMENT`, `BRAND_PRODUCT_SEGMENT`, `BRAND_PRODUCT_KEYWORD`. Types beyond that table return `INVALID_RECOMMENDATION_TYPE`. v1 overview still lists reporting/recommendations/rules as future expansions of campaign-management | SP v3: budget recommendations, initial budget recommendation, keyword recommendations, bid recommendations, product-target recommendations | **Officially verified**. Do not treat v1 Recommendations API as covering SP budget/keyword/bid recs |
| Reporting compatibility | Reporting is **not** in Ads API v1 | Reporting v3 joins on campaign/ad group/ad IDs. Official Profiles example lists campaigns via SP v3 then (separately) reports via Reporting v3 | **Officially verified** |
| Deprecation direction | v1 overview: v1 “will eventually fully replace the ad product-specific APIs.” Campaign-management overview: common model, one ad product per call at launch. Deprecations index does **not** shut off SP v3 | SP Version 2 is deprecated. SP Version 3 is the current product-specific path and is **not** listed as deprecated | **Officially verified** |
| Preview / eligibility | Generally available to onboarded clients; some v1 resources closed beta. Campaign-management: one ad product per payload at launch | SP v3 list operations: `advertiser_campaign_edit` or `advertiser_campaign_view`. SB reporting is preview (separate) | **Officially verified** |

### 5.2 Recommendation for Phase A

**Use Sponsored Products Version 3 read APIs for hierarchy, and Reporting v3 for facts.**

The recommendation distinguishes six things:

| Lens | Finding |
|---|---|
| Official support | Ads API v1 **does** support Sponsored Products query/read for campaigns, ad groups, ads, and targets. Campaign-management overview: common model, currently SP/SB/SD/ST/DSP, **one ad product per call at launch**. SP v3 remains the current product-specific GA path. |
| Missing documented fields | v1 `SPCampaign` has **no** `targetingType`. Phase A needs AUTO vs MANUAL as a first-class campaign field. v1 advertised products nest ASIN/SKU under `creative.productCreative…advertisedProduct`, not top-level `asin`/`sku`. v1 entity matrix also omits `targetingType` for SP. |
| Migration direction | v1 “will eventually fully replace the ad product-specific APIs.” Deprecations index does **not** put SP v3 or Reporting v3 on a shutdown clock. Revisit v1 later; do not wait for it for Phase A. |
| Current implementation compatibility | EWise already lists campaigns with `POST /sp/campaigns/list` and Reporting v3. That is **implementation status**, not the reason for the recommendation. |
| Short-term Phase A needs | Profiles example, `targetingType`, nested `{budget, budgetType}`, product ads with `asin`/`sku`, Reporting v3 joins, one header family (`Amazon-Advertising-API-ClientId` + Scope). |
| Long-term migration plan | After Amazon documents (a) `targetingType` or an official AUTO mapping from `autoCreateTargets`, (b) ID-equivalence of v1 `campaignId` with Reporting v3 `campaignId`, (c) product ads/negatives on v1 or an explicit out-of-scope, migrate campaign-management reads to v1 **without** moving reporting. Keep Reporting v3 until v1 generates reports. |

Reasons that are official, not production folklore:

1. Reporting is not in Ads API v1. Phase A performance **must** be Reporting v3 regardless of entity family.
2. The official Profiles guide’s worked example for listing SP campaigns is `POST /sp/campaigns/list` with `Content-Type` / `Accept: application/vnd.spCampaign.v3+json` and `Amazon-Advertising-API-Scope`. Source: [Profiles](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles).
3. Phase A requires targeting type. SP v3 `SponsoredProductsCampaign.targetingType` is required (`AUTO`/`MANUAL`). Update text: `targetingType` cannot be updated. v1 `SPCampaign` has no such field.
4. Mixing v1 campaign IDs with SP v3 ads/keywords would require an official ID-equivalence statement that is **Not documented**.
5. Dual-stack in Phase A would encode the unresolved ClientId conflict (§7.1) into every worker.

**Do not treat this as “v1 is wrong” or “v1 does not support SP.”** Official v1 is the stated future of campaign management and already supports SP query/read.

EWise already calls `POST /sp/campaigns/list`. That is **implementation status**, not the reason for the recommendation.

---

## 6. Authorization and account management

Compare official contracts with EWise **without changing code**.

### 6.1 Login with Amazon and consent

Official: [Authorization overview](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/overview), [Authorization grants](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/authorization-grants). Confidence: **Officially verified**.

- Advertiser grants a client application access via LWA OAuth 2.0.
- Client must be Amazon-approved. Client identifier is a required API credential.
- Consent hosts: NA `https://www.amazon.com/ap/oa`, EU `https://eu.account.amazon.com/ap/oa`, FE `https://apac.account.amazon.com/ap/oa`.
- Query parameters: `client_id`, `scope`, `response_type=code`, `redirect_uri`, optional `state`, optional PKCE `code_challenge` / `code_challenge_method`.
- `redirect_uri` must be a complete HTTPS URL on the LwA client’s Allowed Return URLs.
- Authorization code is returned on the redirect as `code`. Codes expire in **5 minutes** and are single-use.

EWise: consent URL `https://www.amazon.com/ap/oa` (NA host only in code), `response_type=code`, hashed state, `redirect_uri` from config. Matches NA. EU/FE consent hosts are **not** selected in the current helper — implementation gap versus official regional table, not an Amazon conflict.

### 6.2 Required Ads scopes

Official grants table:

| Scope | Description |
|---|---|
| `advertising::campaign_management` | Required scope for **most** Amazon Ads API requests |
| `advertising::audiences` | Required for the Data Provider API |

Source: [Authorization grants](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/authorization-grants) and [Refresh tokens](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/refresh-tokens). Confidence: **Officially verified**.

EWise default: `advertising::campaign_management`. Matches the official “most uses” scope. Data Provider / audiences is out of EWise scope.

v1 OpenAPI list/query also names permission strings such as `advertiser_campaign_view`. Those are API permission labels on operations, not a second LWA scope to put on `/ap/oa`. Confidence: **Officially verified** as OpenAPI “Requires one of these permissions”; mapping from LWA scope → those permission strings is **Not documented** on the extracted grants page.

### 6.3 Access tokens

Official: [Access tokens](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/access-tokens). Confidence: **Officially verified**.

- Valid **60 minutes** (`expires_in` 3600).
- Header: `Authorization: Bearer <token>`.
- Tokens begin with `Atza|`. Maximum size 2048 bytes.
- Remain valid until expiry or advertiser revocation.

EWise: Bearer header; tokens in SecretProvider only. Matches.

### 6.4 Refresh and revocation

Official: [Refresh tokens](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/refresh-tokens). Confidence: **Officially verified**.

- Token exchange: `POST` regional LWA token URL with `grant_type=authorization_code` then later `grant_type=refresh_token`.
- Token URLs: NA `https://api.amazon.com/auth/o2/token`, EU `https://api.amazon.co.uk/auth/o2/token`, FE `https://api.amazon.co.jp/auth/o2/token`. Resulting tokens are **valid globally**.
- Refresh tokens begin with `Atzr|`. Max 2048 bytes. Bound to one client application.
- Successful refresh returns a **new access token and the same refresh token**.
- Issued **on or after 30 July 2026** for advertising scopes: expire **365 days** from advertiser consent.
- Issued **before 30 July 2026**: no fixed expiration until revocation or other invalidation.
- Invalid when: 365-day expiry (new tokens), advertiser revokes, advertiser removes the app, LwA credentials change/delete, Amazon detects suspicious activity.
- Invalid refresh → HTTP 400 `{ "error": "invalid_grant", "error_description": "..." }`. Must obtain a new grant.

EWise: NA token URL `https://api.amazon.com/auth/o2/token`; refresh token only in SecretProvider. 365-day rotation monitoring for post-2026-07-30 tokens is **not** an EWise product behavior yet (implementation status). Official best practice: encrypt, never log, never put in client-side code.

### 6.5 Profiles, account types, marketplace, currency, timezone

Official: [Profiles](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles). Confidence: **Officially verified**.

| Field | Official contract |
|---|---|
| Resource | `GET /v2/profiles` |
| Scope header | **Must not** be sent on this call |
| Max items | 5000 |
| Region | Returns only profiles whose marketplace is in the **API host region**. Wrong host → `[]` |
| `profileId` | Integer in the official sample; passed later as `Amazon-Advertising-API-Scope` |
| `countryCode` | Marketplace country |
| `currencyCode` | Account currency |
| `timezone` | Profile timezone (sample uses `America/Los_Angeles` even for CA/MX/US) |
| `accountInfo.marketplaceStringId` | Amazon marketplace id (US `ATVPDKIKX0DER`, CA `A2EUQ1WTGCTBG2`, MX `A1AM78C64UM0Y8` in the sample) |
| `accountInfo.type` | `vendor` · `seller` · `agency` |
| Seller vs vendor | Seller and vendor: SP/SB/SD only. SB/SD seller/vendor: Brand Registry required (**Eligibility dependent**) |
| Agency | DSP and Data Provider APIs only |
| Permissions filter | Default: profiles with view **and** edit campaigns. Query: `accessLevel`, `apiProgram` (example: `accessLevel=view&apiProgram=report`) |
| Manager accounts | Linked to multiple advertisers; Profiles returns only advertiser accounts in the **host region** |

EWise: `GET /v2/profiles` without Scope; parses `profileId` as int, country/currency/timezone/`accountInfo`. Matches the official sample shape. Does not send `accessLevel=view` — may hide report-only profiles. **Implementation discrepancy**, not an official conflict.

One LWA grant commonly yields **multiple** profiles (marketplace × account). Never 1:1 with SP-API selling partner. Tenant remains `organization_id`.

### 6.6 Required account/profile headers

Authorization overview required headers for “each request”:

- `Amazon-Advertising-API-ClientId`
- `Authorization: Bearer …`
- Nearly all resources also: `Amazon-Advertising-API-Scope` = profile id

Missing/incorrect ClientId or Authorization → 401. Missing/incorrect Scope when required → 401 or 400.

v1 getting started uses **`Amazon-Ads-ClientId`** as the required client header and adds `Amazon-Ads-AccountId` for ADSP/cross-product and `Amazon-Ads-Manager-AccountId` for manager operations. See §7.

EWise sends `Amazon-Advertising-API-ClientId`, Bearer, `Amazon-Advertising-API-Scope`. Matches authorization overview and SP v3 OpenAPI. Does not send `Amazon-Ads-AccountId` or `Amazon-Ads-ClientId`.

### 6.7 Global / multi-account

Official: tokens from any regional LWA token host are globally valid; **API data hosts are regional**. Profile IDs are not valid as Scope on the wrong regional host (4XX Unauthorized). Manager-account multi-advertiser listing is region-filtered.

Official: [Manager accounts](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/manager-accounts). Confidence: **Officially verified**.

- Manager accounts link multiple advertising accounts. Created in console or `POST /managerAccounts` (mutation, out of scope).
- Access token represents an Amazon user with access to a manager account; the client then calls on behalf of **linked** accounts.
- Linked sponsored-ads accounts grant **Editor** or **Viewer** to the manager. Viewer may read and generate reports; mutating Editor-only operations return **401 Unauthorized**.
- Use the **linked account’s** `profileId` as `Amazon-Advertising-API-Scope`. `GET /managerAccounts` returns up to **50** linked accounts per manager, each with `accountId`, `accountName`, `accountType` (example `SELLER`), `dspAdvertiserId`, `marketplaceId`, `profileId`.
- `GET /profiles` (authorization guide wording) returns sponsored-ads accounts that gave **Editor** permission to a manager owned by the authorized user, plus profiles directly owned by the user.
- Manager accounts can accept linked accounts in any marketplace combination, but the **API is not global**: the manager can access a linked account only on the API host for that account’s region. `GET /profiles` and `GET /managerAccounts` return only profiles whose marketplace is in that host’s region.
- Managed-service DSP advertisers use a manager account to call reporting. Linked DSP rows have `accountType` `DSP_ADVERTISING_ACCOUNT`; use `dspAdvertiserId` as the account ID for a DSP reporting request.
- Manager accounts do **not** grant Selling Partner API access.
- Associate/disassociate endpoints exist; out of scope.

`adsAccountId` / `Amazon-Ads-AccountId`: required in Ads API v1 for ADSP and cross-product (getting started); optional on SP-filtered v1 query headers; required on Marketing Stream DSP subscription ops as “DSP advertiser level account”. Not required on the extracted SP v3 / Reporting v3 / Portfolios sponsored-ads samples. Conflict with whether SP Reporting needs AccountId remains §7.2.

---

## 7. Official conflicts (unresolved)

Live production behavior is recorded as **Production-observed but not contract authority**. It does not close these conflicts. No live diagnostic was run in this documentation task.

### 7.1 Client ID header name

| | |
|---|---|
| **Official pages** | [Reporting v3 get started](https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/get-started) header table: `Amazon-Ads-ClientId`. [Ads API v1 getting started](https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/getting-started) and v1 OpenAPI: `Amazon-Ads-ClientId` required. [Authorization overview](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/overview), [Profiles](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles), SP v3 OpenAPI, and official reporting cURL samples: `Amazon-Advertising-API-ClientId`. |
| **Conflicting requirements** | One official name versus the other for the LwA application client identifier. |
| **What EWise sends** | `Amazon-Advertising-API-ClientId` |
| **Production observed** | Prior live SP list and `spCampaigns` create succeeded with `Amazon-Advertising-API-ClientId`. Not contract. |
| **Risk** | A future v1 or reporting stack that enforces the table spelling could 401. Dual-sending both headers is **Not documented**. |
| **Resolution path** | Amazon Ads API support clarification. Do not “pick a winner” in code until support or a single official page retracts the other name. |
| **Support required?** | **Yes.** |

### 7.2 `Amazon-Ads-AccountId` on Sponsored Products Reporting v3

| | |
|---|---|
| **Official pages** | Reporting v3 get-started table: `Amazon-Ads-AccountId` “Required for reporting requests across all ad products including ADSP, Sponsored Products, Sponsored Brands, and Sponsored Display.” Official SP report-type cURL samples omit it. v1 getting started: AccountId required for **ADSP and cross-product**, not for SP-only. Reporting overview: `advertiserAccount.id` corresponds to Accounts API `adsAccountId` and to header `Amazon-Ads-AccountId`. |
| **Conflicting requirements** | Required for SP reporting versus omitted from official SP samples versus required only for ADSP/cross-product on v1 getting started. |
| **What EWise sends** | Does not send `Amazon-Ads-AccountId`. Sends `Amazon-Advertising-API-Scope`. |
| **Production observed** | Prior live `spCampaigns` create succeeded without AccountId. Not contract. |
| **Risk** | Multi-account or future enforcement could reject SP report creates. |
| **Resolution path** | Amazon support + Accounts/Reporting guide re-read for SP-only versus multi-account. Implementation diagnostic only after operator approval (not this task). |
| **Support required?** | **Yes.** |

### 7.3 Other official documentation defects (not header conflicts)

- Search-term sample `name` string says `SB search terms report 7/5-7/10` while `adProduct` is `SPONSORED_PRODUCTS` and `reportTypeId` is `spSearchTerm`. Source: search-term report-type page.
- Get-started sample response `endDate` equals `startDate` while the request range is 7/5–7/10.
- Media type casing: Profiles example uses `application/vnd.spCampaign.v3+json`; EWise live success used `application/vnd.spcampaign.v3+json`. Official pages themselves mix capitalisation. Treat as **Official sources conflict** on spelling; HTTP 415 is documented if the media type is wrong. Confidence: mixed official casing + **Production-observed but not contract authority** for lowercase.
- Identifier wire types: Reporting v3 columns glossary types `campaignId`, `adGroupId`, `adId`, `portfolioId`, `keywordId` as **Integer**. SP v3 / Portfolios OpenAPI types those IDs as **string**. Phase A persists canonical strings after lossless conversion; does not treat the glossary Integer as a JSON number requirement.
- Gross/invalid `reportTypeId` casing: report-type page `spGrossAndInvalids` vs glossary `spGrossandInvalids`. Use the **report-type page** string for create-report requests; record the glossary spelling as a defect.
- Budget usage guide sample uses `lastUpdatedDate` and one example field `budgetConsumptionPercent`; SP v3 OpenAPI uses `usageUpdatedTimestamp` and `budgetUsagePercent`. Persist OpenAPI names; treat the guide sample aliases as defects.
- Portfolios `includeExtendedDataFields` description says “targetingClauses” (copy-paste). Extended fields on `Portfolio` are `creationDateTime`, `lastUpdateDateTime`, `servingStatus`, `statusReasons`.
- SP v3 `SponsoredProductsAdGroup.adGroupId` description says “identifier of the keyword” (copy-paste). Store as ad-group id.

Search-term 65-day versus campaign 95-day retention is **not** in this section. See §4.

---

## 8. Current EWise implementation inventory

Classification key unchanged: Implemented and officially verified · Implemented but official contract unverified · Partially implemented · Designed only · Missing · Not applicable · Eligibility unknown.

| Capability | Classification | Notes |
|---|---|---|
| Ads OAuth start/callback, hashed state, SecretProvider | Partially implemented | NA consent/token hosts. Official EU/FE LWA hosts not selected. Scope `advertising::campaign_management` matches official default. PKCE optional in Amazon docs; EWise does not send it. |
| Advertiser profiles `GET /v2/profiles` | Implemented and officially verified | Shape matches Profiles guide. Missing `accessLevel`/`apiProgram` filters. |
| Regional Ads hosts NA/EU/FE | Implemented and officially verified | Matches API overview. |
| `POST /sp/campaigns/list` | Partially implemented | Path, view permission, and Profiles-guide media type family match. EWise lowercase `spcampaign` vs official sample `spCampaign`. Persistence of listed campaigns is not the reporting path. Official item schema is `SponsoredProductsCampaign` (§10.2). |
| SP ad groups / product ads / keywords / targets list | Partially implemented | Official media types now extracted (§10–11). EWise still sends generic `application/json` and `items` except campaigns. |
| Negative keywords / negative targets | Missing | Official list operations extracted. |
| Portfolios listing | Missing | Phase A stores opaque `campaign.portfolioId` only. `POST /portfolios/list` is Phase B (§10.5). |
| Reporting v3 create/poll/download for `spCampaigns` | Partially implemented | Nested `configuration` matches get-started. Media type still `application/json` versus official `application/vnd.createasyncreportrequest.v3+json`. Column set and groupBy incomplete. |
| Report-run ledger, lease, resume, checkpoint | Implemented | EWise operational design. Official 425 duplicate-create not modelled. |
| Campaign daily facts | Partially implemented | 14d-only; single attribution_window natural key. |
| Budget usage / recommendations | Missing | Official contracts extracted (§11.8). Not implemented. |
| SB / SD / Stream / AMC / DSP | Missing | See §15. |
| Mutation | Not applicable | Read-only. |

---

## 9. Reporting v3 lifecycle (all sponsored-ads report types)

| Field | Official value | Official URL | Confidence |
|---|---|---|---|
| Operation | Request a report `POST /reporting/reports` | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/get-started | **Officially verified** |
| Access | Read (async create is not a campaign mutation) | same | **Officially verified** |
| Request media type | `application/vnd.createasyncreportrequest.v3+json` | same | **Officially verified** |
| Headers | See §7 (conflict) | same | **Official sources conflict** |
| Request schema | `{ name, startDate, endDate, configuration: { adProduct, groupBy, columns, reportTypeId, timeUnit, format, filters? } }` | same | **Officially verified** |
| Response envelope | `configuration`, `createdAt`, `endDate`, `failureReason`, `fileSize`, `generatedAt`, `name`, `reportId`, `startDate`, `status`, `updatedAt`, `url`, `urlExpiresAt` | same | **Officially verified** |
| Status | `PENDING` or `PROCESSING` while generating; `COMPLETED` when `url` present | same | **Officially verified** |
| Poll | `GET /reporting/reports/{reportId}` | same | **Officially verified** |
| Max generation | “up to three hours” (get-started and FAQ) | get-started + FAQ | **Officially verified** |
| Duplicate create | HTTP **425** if identical parameters requested too soon | get-started | **Officially verified** |
| Throttle | HTTP **429**; delay between status checks; exponential backoff. FAQ: repeated status checks may 429 | get-started + FAQ | **Officially verified** |
| Download | `url` is an S3 link; GET the URL | get-started | **Officially verified** |
| Format | Most sponsored-ads types: gzip then JSON array | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/overview | **Officially verified** |
| Idempotency key | **Not documented**. Duplicate identical creates → 425 | get-started | **Not documented** / **Officially verified** for 425 |
| `urlExpiresAt` TTL | Field exists; exact TTL **Not documented** on get-started | get-started | **Not documented** |
| Recommended frequency | 1–2 requests per advertiser per day per report type | FAQ | **Officially verified** |
| Exports vs reports | Exports = campaign-structure metadata snapshot; reports = performance (+ optional metadata). Prefer exports for names/ids, then join | FAQ | **Officially verified** |

Identifier note: official get-started sample **rows** use JSON numbers for `campaignId` / `adGroupId`. EWise list endpoints observed **strings**. Join with lossless string conversion; reject floats/bools. Confidence: sample **Officially verified**; EWise list strings **Production-observed but not contract authority**.

---

## 10. Phase A endpoint contracts

A visible OpenAPI tag is not a contract. The following are extracted operations.

Unless noted, marketplace availability is the three regional advertising-api hosts; profile marketplace must match host region ([Profiles](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles)). Rate-limit guidance: HTTP 429 / ThrottlingException; Reporting FAQ plus get-started backoff. Retry: bounded exponential backoff; honor `Retry-After` if present (**Not documented** whether Reporting always sends it).

### 10.1 Profiles / account context

| Field | Contract |
|---|---|
| API family / version | Accounts / Profiles **v2** |
| HTTP | `GET /v2/profiles` |
| Read/write | Read |
| Required LWA scope | `advertising::campaign_management` |
| Required headers | `Amazon-Advertising-API-ClientId`, `Authorization: Bearer`. **No** `Amazon-Advertising-API-Scope` |
| Optional query | `accessLevel`, `apiProgram`, `profileTypeFilter` |
| Request media type | Official example `application/json` |
| Response media type | JSON array (not an envelope) |
| Response schema | See §6.5. Max 5000 items |
| Identifier | `profileId` number in official sample |
| Pagination | **Not documented** beyond the 5000 cap |
| Official URL | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles |
| EWise | Implemented; omits view/report filters |
| Discrepancy | EWise always uses NA LWA token host; profiles still must be fetched per Ads data region |
| Confidence | **Officially verified** |

Profiles OpenAPI page linked from that guide was not re-extracted this pass. Operation contract above is from the rendered guide, not only a nav tag.

### 10.2 Campaign listing (SP v3) — recommended Phase A family

| Field | Contract |
|---|---|
| API family / version | Sponsored Products Version 3 |
| HTTP | `POST /sp/campaigns/list` (`ListSponsoredProductsCampaigns`) |
| Read/write | Read |
| Permissions | `advertiser_campaign_edit` or `advertiser_campaign_view` |
| Required headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` |
| Request/response media type | `application/vnd.spCampaign.v3+json` |
| Request schema | `campaignIdFilter`, `includeExtendedDataFields`, `marketplaceBudgetAllocationFilter` (officially “not functional yet”), `maxResults`, `nameFilter` (`queryTermMatchType` including `BROAD_MATCH`), `nextToken`, `portfolioIdFilter`, `stateFilter` (`ENABLED` / `PAUSED` / `ARCHIVED` for live entities) |
| Response envelope | `{ campaigns[], nextToken, totalResults }` |
| Identifier | `campaignId` **string** (OpenAPI). Glossary types the same name as Integer — §7.3 |
| Pagination | `nextToken`; `totalResults` int64 |
| Errors | 400 CampaignAccessException, 401, 403, **415** UnsupportedMediaType, 429 Throttling (`Retry-After` on 429), 500 |
| Official URLs | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod · download https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/SponsoredProducts_prod_3p.json · worked example https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles |
| EWise | Path + envelope `campaigns` + media type family; lowercase subtype; does not send `includeExtendedDataFields` |
| Known discrepancy | Rendered list **sample** remains `{ }`. Schema is `SponsoredProductsCampaign` (below). EWise DTO shape matches the schema, not the empty sample |
| Confidence | Operation and campaign object **Officially verified** from OpenAPI schema. Empty sample is a documentation defect |

**`SponsoredProductsCampaign` (list item; same schema referenced by the 200 response):**

| Field | Type / enum | Required on read object | Notes |
|---|---|---|---|
| `campaignId` | string | yes | “The identifier of the campaign.” |
| `name` | string | yes | |
| `state` | `ENABLED`, `PAUSED`, `ARCHIVED`, `PROPOSED`, `ENABLING`, `USER_DELETED`, `OTHER` | yes | Filter live entities with ENABLED/PAUSED/ARCHIVED only |
| `targetingType` | `AUTO`, `MANUAL` | yes | Cannot be updated (Update operation text) |
| `startDate` | date `YYYY-MM-DD` | yes | |
| `endDate` | date, nullable | no | |
| `budget.budget` | number double | yes (budget object required) | Monetary value |
| `budget.budgetType` | `DAILY`, `OTHER` | yes | |
| `budget.effectiveBudget` | number double | no | |
| `dynamicBidding.strategy` | `LEGACY_FOR_SALES` (down only), `AUTO_FOR_SALES` (up and down), `MANUAL` (fixed), `RULE_BASED`, `OTHER` | if `dynamicBidding` present | `dynamicBidding.strategy` required when object present |
| `dynamicBidding.placementBidding[]` | `{ placement, percentage 0–900 }` | no | `PLACEMENT_TOP`, `PLACEMENT_PRODUCT_PAGE`, `PLACEMENT_REST_OF_SEARCH`, `SITE_AMAZON_BUSINESS` |
| `portfolioId` | string | no | “existing portfolio to which the campaign is associated” |
| `extendedData.creationDateTime` / `lastUpdateDateTime` | date-time | no | Request `includeExtendedDataFields=true` |
| `extendedData.servingStatus` | enum including `CAMPAIGN_STATUS_ENABLED`, `CAMPAIGN_PAUSED`, `CAMPAIGN_OUT_OF_BUDGET`, `ENDED`, `PENDING_START_DATE`, portfolio/account reasons | no | Delivery / serving status |
| `globalCampaignId` | string | no | Marketplace campaign managed by a global campaign |
| `marketplaceBudgetAllocation` | `AUTO`, `MANUAL` | no | Global-campaign budget split. List filter “not functional yet” |
| `tags` | object map, max 50 | no | |
| `autoManageCampaign` | boolean | no | |
| `offAmazonSettings` | object | no | |
| `siteRestrictions` | `AMAZON_BUSINESS`, `AMAZON_HAUL` | no | Officially “not ready for use at the moment” except Amazon Business note on create |

Rate limit: 429 ThrottlingException with `Retry-After` seconds. TPS numeric value **Not documented** on this operation beyond campaign-management overview (TPS per ad product at launch).

### 10.3 Ad-group listing (SP v3)

| Field | Contract |
|---|---|
| HTTP | `POST /sp/adGroups/list` (`ListSponsoredProductsAdGroups`) |
| Media type | `application/vnd.spAdGroup.v3+json` |
| Read/write | Read |
| Permissions | `advertiser_campaign_edit` or `advertiser_campaign_view` |
| Headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` required |
| Request | `adGroupIdFilter`, `campaignIdFilter`, `campaignTargetingTypeFilter` enum `AUTO`/`MANUAL`, `includeExtendedDataFields`, `maxResults`, `nameFilter`, `nextToken`, `stateFilter` |
| Response envelope | `{ adGroups[0–1000], nextToken, totalResults }` |
| Response item (`SponsoredProductsAdGroup`) | Required: `adGroupId`, `campaignId`, `defaultBid`, `name`, `state`. Optional: `extendedData`, `globalAdGroupId`. Official `adGroupId` description says “keyword” — §7.3 |
| Official URL | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod |
| EWise | Wrong media type (`application/json`) and envelope `items` |
| Confidence | **Officially verified** |

### 10.4 Advertised-product listing (SP v3)

| Field | Contract |
|---|---|
| HTTP | `POST /sp/productAds/list` (`ListSponsoredProductsProductAds`) |
| Media type | `application/vnd.spProductAd.v3+json` |
| Read/write | Read |
| Permissions | `advertiser_campaign_edit` or `advertiser_campaign_view` |
| Request | `adGroupIdFilter`, `adIdFilter`, `campaignIdFilter`, `includeExtendedDataFields`, `maxResults`, `nextToken`, `stateFilter` |
| Official URL | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod |
| EWise | Generic JSON / `items` |
| Response item (`SponsoredProductsProductAd`) | Required: `adId`, `adGroupId`, `campaignId`, `state`. Optional: `asin` (vendors only), `sku` (sellers only), `customText`, `extendedData`, `globalAdId`, `globalStoreSetting` |
| Confidence | **Officially verified** |

### 10.5 Portfolio association (Phase A: opaque id only)

A campaign belongs to **at most one** portfolio. Source: [Get started with Portfolios](https://advertising.amazon.com/API/docs/en-us/guides/portfolios/get-started).

**Phase A:** persist `SponsoredProductsCampaign.portfolioId` when present. Do **not** require `POST /portfolios/list`. Do not invent names.

**Phase B (optional read):** list portfolios for name/state/budget captions.

| Field | Contract |
|---|---|
| Family / version | Portfolios **3.0** (page title also shows “Version 1” / OAS 3.0.1 — media type is v3) |
| List | `POST /portfolios/list` (`ListPortfolios`) |
| Get-by-id | **Not documented** as a dedicated GET; filter with `portfolioIdFilter` |
| Read permissions | `advertiser_campaign_edit` or `advertiser_campaign_view` |
| Headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope`; optional `Prefer` |
| Media type | `application/vnd.spPortfolio.v3+json` |
| Request | `includeExtendedDataFields`, `nameFilter`, `nextToken`, `portfolioIdFilter` (1–1000 ids), `stateFilter` |
| Pagination | `nextToken`; `portfolios` 0–1000; `totalResults` |
| Portfolio object | Required: `portfolioId` string, `name`, `state`. Optional: `budget` `{amount nullable, currencyCode, startDate, endDate, policy}`, `inBudget`, `budgetControls.campaignUnspentBudgetSharing.featureState` `ENABLED`/`DISABLED`, `extendedData` |
| `state` OpenAPI enum | **`ENABLED` only** on `EntityState`. Guide sample is `ENABLED`. PAUSED/ARCHIVED **Not documented** on this enum |
| Budget policy | `DATE_RANGE`, `MONTHLY_RECURRING`, `NO_CAP`, `OTHER` |
| Marketplace | Same regional hosts; currency enum includes USD/INR/… Profile Scope selects marketplace |
| Mutations (out of scope) | `POST /portfolios` create (edit permission only); `PUT /portfolios` update |
| Budget usage (Phase B) | `POST /portfolios/budget/usage`, media `application/vnd.portfoliobudgetusage.v1+json`, 1–100 ids, **207** multi-status |
| Errors | 400, 401, 403, 415, 429 (+ `Retry-After`), 500 |
| Official URLs | https://advertising.amazon.com/API/docs/en-us/guides/portfolios/get-started · https://advertising.amazon.com/API/docs/en-us/reference/portfolios · https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/Portfolios_prod_3p.json |

### 10.6 Reporting v3 `spCampaigns`

Shared create/status/download: §9.

| Grain | groupBy | Extra columns (in addition to SP campaign base metrics) | Official URL |
|---|---|---|---|
| Campaign | `campaign` | `campaignName, campaignId, campaignStatus, campaignBudgetAmount, campaignBudgetType, campaignRuleBasedBudgetAmount, campaignApplicableBudgetRuleId, campaignApplicableBudgetRuleName, campaignBudgetCurrencyCode, topOfSearchImpressionShare` | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/campaign |
| Ad group | `adGroup` | `adGroupName, adGroupId, adStatus` | same |
| Campaign placement | `campaignPlacement` | `placementClassification` plus campaign metadata / ToS impression share. Grouping by `campaignPlacement` alone matches `campaign` + `campaignPlacement`. Amazon Business placement data starts 5 September 2024 | same |

`adProduct`: `SPONSORED_PRODUCTS`. `reportTypeId`: `spCampaigns`. `format`: `GZIP_JSON`. Filters only when a **single** groupBy is used. `campaignStatus`: ENABLED, PAUSED, ARCHIVED. `adStatus` when grouped by adGroup. `campaignSite=AmazonBusiness` when grouped by campaignPlacement.

Official SP campaign **base metrics**:
`impressions, addToList, qualifiedBorrows, royaltyQualifiedBorrows, clicks, cost, purchases1d, purchases7d, purchases14d, purchases30d, purchasesSameSku1d, purchasesSameSku7d, purchasesSameSku14d, purchasesSameSku30d, unitsSoldClicks1d, unitsSoldClicks7d, unitsSoldClicks14d, unitsSoldClicks30d, sales1d, sales7d, sales14d, sales30d, attributedSalesSameSku1d, attributedSalesSameSku7d, attributedSalesSameSku14d, attributedSalesSameSku30d, unitsSoldSameSku1d, unitsSoldSameSku7d, unitsSoldSameSku14d, unitsSoldSameSku30d, kindleEditionNormalizedPagesRead14d, kindleEditionNormalizedPagesRoyalties14d, date, startDate, endDate, campaignBiddingStrategy, costPerClick, clickThroughRate, spend`

Request `date` iff `timeUnit=DAILY`; `startDate`/`endDate` iff `SUMMARY`.

Never copy SB/SD campaign columns onto SP.

EWise: `groupBy: ["campaign"]`, columns `date, campaignId, impressions, clicks, cost, sales14d, purchases14d` only; `Content-Type: application/json`.

**Phase A HTTP endpoint count (operations):** 7 — Profiles GET, campaigns list, ad groups list, product ads list, report create, report status, report download. **Phase A report configurations:** 3 (`spCampaigns` × campaign / adGroup / campaignPlacement).

---

## 11. Phase B endpoint and report contracts

Never reuse a column set across report types unless Amazon documents the same list on both pages.

### 11.1 Targeting performance — `spTargeting`

| Field | Official value |
|---|---|
| reportTypeId | `spTargeting` |
| adProduct | `SPONSORED_PRODUCTS` |
| groupBy | `targeting` |
| timeUnit | SUMMARY or DAILY |
| Max request range | 31 days |
| Historical retention | 95 days |
| Filters | `keywordType`: `BROAD`, `PHRASE`, `EXACT` for keywords; `TARGETING_EXPRESSION`, `TARGETING_EXPRESSION_PREDEFINED` for targeting expressions |
| Traffic / click / conversion gating | Traffic columns present; conversions click-windowed. Not click-gated like search term |
| Attribution | 1d/7d/14d/30d plus same-SKU; **other-SKU** `salesOtherSku7d`, `unitsSoldOtherSku7d` |
| Promoted vs brand-halo | Same-SKU columns + other-SKU 7d |
| Multi-touch | **Not documented** on this page |
| Official URL | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/targeting |
| EWise | Missing |
| Confidence | **Officially verified** |

Base metrics include traffic (`impressions, clicks, cost, costPerClick, clickThroughRate`), 1d/7d/14d/30d purchases/sales/units, same-SKU variants, other-SKU 7d, `acosClicks7d`, `acosClicks14d`, `roasClicks7d`, `roasClicks14d`, keyword/target identity fields, campaign/ad-group metadata, `topOfSearchImpressionShare`, `adKeywordStatus`.

### 11.2 Search-term performance — `spSearchTerm`

| Field | Official value |
|---|---|
| reportTypeId | `spSearchTerm` |
| groupBy | `searchTerm` |
| Retention | 65 days |
| Max range | 31 days |
| Click gating | Only impressions that resulted in ≥1 click. FAQ: Console includes impression-only terms; API does not |
| Missing keyword | Product-detail placement with no search keyword → `searchTerm` is `*` |
| Official URL | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/search-term |
| EWise | Missing |

### 11.3 Advertised-product performance — `spAdvertisedProduct`

| Field | Official value |
|---|---|
| reportTypeId | `spAdvertisedProduct` |
| groupBy | `advertiser` |
| Join keys | `advertisedAsin`, `advertisedSku`, `campaignId`, `adGroupId`, `adId` |
| Filters | `adCreativeStatus`: ENABLED, PAUSED, ARCHIVED |
| Retention / range | 95 / 31 days |
| Other-SKU | `salesOtherSku7d` / `unitsSoldOtherSku7d` |
| Official URL | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/advertised-product |
| EWise | Missing |

### 11.4 Purchased-product performance — `spPurchasedProduct`

| Field | Official value |
|---|---|
| reportTypeId | `spPurchasedProduct` |
| groupBy | `asin` |
| Official interpretation | Performance for products purchased **but not advertised** as part of a campaign |
| Traffic | **Not** in official SP base list |
| Other-SKU | Full 1/7/14/30 other-SKU units/sales/purchases |
| Official URL | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/purchased-product |
| EWise | Missing |

Do not compute ACOS from this report alone.

### 11.5 Keywords (entity)

| Field | Contract |
|---|---|
| HTTP | `POST /sp/keywords/list` (`ListSponsoredProductsKeywords`) |
| Media type | `application/vnd.spKeyword.v3+json` |
| Permissions extracted | `advertiser_campaign_edit` or `campaign_proposed` — **view-only string `advertiser_campaign_view` not listed on this operation**. Impact: a view-only grant may be unable to list keywords. Confidence: **Officially verified** as extracted; whether view works anyway is **Not documented** |
| Request | `adGroupIdFilter`, `campaignIdFilter`, `includeExtendedDataFields`, `keywordIdFilter`, `keywordTextFilter`, `locale`, `matchTypeFilter` (`BROAD`/`EXACT`/`OTHER`/`PHRASE`), `maxResults`, `nextToken`, `stateFilter` |
| Official URL | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod |
| EWise | Generic JSON / `items` |

### 11.6 Product / category / automatic targets (entity)

| Field | Contract |
|---|---|
| HTTP | `POST /sp/targets/list` (`ListSponsoredProductsTargetingClauses`) |
| Media type | `application/vnd.spTargetingClause.v3+json` |
| Permissions | `advertiser_campaign_edit` or `campaign_proposed` (same view-gap as keywords) |
| Request | `adGroupIdFilter`, `asinFilter`, `campaignIdFilter`, `expressionTypeFilter`, `includeExtendedDataFields`, `maxResults`, `nextToken`, `stateFilter`, `targetIdFilter` |
| Official URL | same SP v3 OpenAPI |
| EWise | Generic JSON / `items` |

Automatic versus manual targeting expressions are distinguished with `expressionTypeFilter` / `keywordType` on reports. Do not collapse AUTO campaign targeting with MANUAL keyword rows.

### 11.7 Negative keywords and negative product targets

| Operation | Path | Media type | Permissions | Notes |
|---|---|---|---|---|
| List negative keywords | `POST /sp/negativeKeywords/list` | `application/vnd.spNegativeKeyword.v3+json` | `advertiser_campaign_edit` or `advertiser_campaign_view` | `matchTypeFilter`: `NEGATIVE_BROAD` / `NEGATIVE_EXACT` / `NEGATIVE_PHRASE` / `OTHER` |
| List negative targeting clauses | `POST /sp/negativeTargets/list` | `application/vnd.spNegativeTargetingClause.v3+json` | `advertiser_campaign_edit` or `advertiser_campaign_view` | `asinFilter`, `negativeTargetIdFilter` |

Official URL: SP v3 OpenAPI. EWise: missing.

### 11.8 Budget usage and recommendations (read-only; advisory Copilot evidence)

**Do not apply recommendations automatically.** Persist as estimated/advisory evidence with expiration/status when documented.

#### Budget usage (Phase B)

| Item | Official value |
|---|---|
| Guide | https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/overview · https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/getting-started |
| Meaning | Near-real-time **budget usage percent** = current campaign ad spend for the day / specified daily budget, including budget-rule and average-daily-budget increases. Active, paused, or expired campaigns/portfolios |
| SP operation | `POST /sp/campaigns/budget/usage` (`spCampaignsBudgetUsage`) |
| Media | `application/vnd.spcampaignbudgetusage.v1+json` |
| Headers | `Amazon-Advertising-API-ClientId`, `Amazon-Advertising-API-Scope` required |
| Permissions | `advertiser_campaign_edit` or `advertiser_campaign_view` |
| Request | `campaignIds` 1–100 strings |
| Response | **207** `{ success[], error[] }`. OpenAPI success item: `campaignId`, `budget`, `budgetUsagePercent`, `index`, `usageUpdatedTimestamp` |
| Pagination | None (batch of 100) |
| Also | SB `POST /sb/campaigns/budget/usage`; SD `POST /sd/campaigns/budget/usage`; portfolios §10.5 |
| Copilot | Point-in-time `budget_now` grain; label estimated as of `usageUpdatedTimestamp`. Do not treat as a daily fact |

#### SP v3 budget / bid / keyword / product-target recommendations (Phase B)

| Operation | Path | Media | Notes |
|---|---|---|---|
| Budget recs / missed opportunities | `POST /sp/campaigns/budgetRecommendations` | `application/vnd.budgetrecommendation.v3+json` | Recommended daily budget; percent time in budget (`-1` = insufficient history); estimated missed impressions/clicks/sales. **Estimated** |
| New-campaign initial budget | `POST /sp/campaigns/initialBudgetRecommendation` | `application/vnd.spinitialbudgetrecommendation.v3.4+json` | For **creating** a campaign; out of Phase B ingest unless used as a read of hypotheticals. Request `targetingType` auto/manual |
| List budget rules | `GET /sp/budgetRules` | sample `application/json` | `pageSize` max 30; `nextToken`. **Read**. Create/associate rules are writes |
| Bid recommendations | `POST /sp/targets/bid/recommendations` | `v3` media type **deprecated** (shut off 15 May 2025); current `v4`/`v5` (`application/vnd.spthemebasedbidrecommendation.v4+json` / `.v5+json`) | Replacement for `/v2/sp/targets/bidRecommendations` |
| Keyword recommendations | `POST /sp/targets/keywords/recommendations` | `application/vnd.spkeywordsrecommendation.v3|v4|v5+json` | Replacement for deprecated `/v2/.../suggested/keywords` (shut off 1 Jun 2026) |
| Product-target recommendations | `POST /sp/targets/products/recommendations` | OpenAPI | Suggested target ASINs |

Official URL for the table: https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod. Deprecation dates: https://advertising.amazon.com/API/docs/en-us/release-notes/deprecations.

#### Ads API v1 Recommendations API (consolidated interface; not a Phase B substitute for SP recs)

Official title: [Recommendations API overview](https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/overview). Amazon calls this “a consolidated interface that returns actionable recommendations conforming to a single, standardized model.” Launch **August 2026**, worldwide, all registered Ads API developers.

Read-only operations:

| Operation | Path | Mode |
|---|---|---|
| List | `GET /adsApi/v1/list/recommendations?{filters}` | Types that do not need type-specific input |
| Query | `POST /adsApi/v1/query/recommendations` | Discovery (no type filter) or scoped (type + optional details) |

Universal fields: `recommendationType`, `recommendationInsight`, `recommendedObjects`, `state`. How-to: https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/how-to.

https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/recommendation-types currently lists only: `REMOTE_CAMPAIGN_PAUSED` (SP), `AUTOMATIC_GLOBAL_ADS_ENROLLMENT` (multi-product), `BRAND_PRODUCT_SEGMENT` / `BRAND_PRODUCT_KEYWORD` (SB, scoped POST). Other type names return **400** `INVALID_RECOMMENDATION_TYPE`. Design to ignore unknown types.

Overview marketing text mentions bid adjustments, keyword suggestions, and budget changes as examples of “many recommendation types.” The **types page is the contract**. Those SP budget/keyword/bid recs remain on SP v3 until the types page lists them.

DSP has a **separate** Guidance / Quick Actions family (`POST /dsp/v1/guidance/.../list` read; Quick Actions executions are **mutations**, out of scope). URL: https://advertising.amazon.com/API/docs/en-us/guides/dsp/guidance-and-quick-actions. Phase E.

Copilot: store as `claim_type=estimated` / advisory. Include `state`, type, and last-update when present. Never auto-apply.

**Phase B HTTP/report count:** 6 entity/budget operations + 4 report types (`spTargeting`, `spSearchTerm`, `spAdvertisedProduct`, `spPurchasedProduct`) = **10** priority contracts. Gross/invalid, Prompt, Video are catalogued in §12.1 and **deferred**.

---

## 12. Priority supporting references

| Official page | URL | Classification | Extraction |
|---|---|---|---|
| Reporting v3 columns glossary | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/columns | **Required for Phase A and B** | §12.2 |
| Reporting v3 FAQ | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/faq | **Required for Phase A** | Extracted: latency, restatement, 1–2 pulls/day, search-term Console vs API, SB preview, v2 SP reporting deprecated 30 Mar 2023, exports vs reports, 3-hour generation |
| Budget usage | https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/overview | **Required for Phase B** | §11.8 |
| Recommendations API | https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/overview | **Phase B/C read** | §11.8 |
| Portfolios | https://advertising.amazon.com/API/docs/en-us/guides/portfolios/get-started | **Phase A** opaque `portfolioId`; **Phase B** names | §10.5 |
| Deprecations | https://advertising.amazon.com/API/docs/en-us/reference/deprecations · https://advertising.amazon.com/API/docs/en-us/release-notes/deprecations | **Required for Phase A** | §14.8 |
| Release notes | https://advertising.amazon.com/API/docs/en-us/release-notes/index | Watch list | Index + deprecations page extracted; not every historical note |
| Gross/invalid, Prompt, Video | §12.1 | **Deferred** after Phase B | Extracted |

### 12.1 Supporting report types (deferred)

| | Gross and invalid traffic | Prompt Ad Extension | Video Ad Extension |
|---|---|---|---|
| Title | Gross and invalid traffic reports | Prompt Ad Extension reports | Video Ad Extension reports |
| URL | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/gross-and-invalid-traffic | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/prompt-ad-extension | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/video-ad-extension |
| `reportTypeId` | `spGrossAndInvalids` / `sbGrossAndInvalids` / `sdGrossAndInvalids` (page). Glossary uses `spGrossandInvalids` — §7.3 | `spPromptAdExtension` / `sbPromptAdExtension` | `spVideoAdExtension` |
| Ad products | SP, SB, SD (same columns) | SP and SB | SP only |
| Status | No preview/beta label on the page | “new ad format”; no GA/preview token | No preview/beta label; “available for advertisers selling in the United States” |
| Eligibility | **Not documented** beyond ad-product matrix | `marketplaceId` filter values: **US** | US; `marketplaceId` filter **US** |
| `groupBy` | `campaign` | `promptAdExtension` | `videoAdExtension` |
| `timeUnit` | SUMMARY or DAILY | SUMMARY or DAILY | SUMMARY or DAILY |
| Retention / max range | **365 / 365** days | **95 / 90** days | **95 / 90** days |
| Format | GZIP_JSON or CSV | GZIP_JSON or XLSX | GZIP_JSON or XLSX |
| Columns (page base list) | `campaignName, campaignStatus, clicks, date, endDate, grossClickThroughs, grossImpressions, impressions, invalidClickThroughRate, invalidClickThroughs, invalidImpressionRate, invalidImpressions, startDate` | SP list includes `promptText`, traffic, 1/7/14/30 + same-SKU + other-SKU, `spend`, `portfolioName`, `campaignBudgetCurrencyCode`; filter US | Same conversion family plus `video5SecondViews`, quartile views, `viewabilityRate`; `creativeExtensionId` matches campaign-management `adExtensionId` |
| Reporting / Copilot value | Data-quality (invalid vs served traffic). Do not mix with `spCampaigns` totals without labelling | Prompt creative performance; clicks also appear in existing SP/SB reports | Video vs image on the same `adId`; impressions of both formats remain in existing SP reports |
| Recommended phase | After Phase B (quality) | After Phase B / C | After Phase B |

### 12.2 Reporting v3 column glossary (Phase A/B)

Source: https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/columns (`h1` Metrics). Each entry is `Type` / `Description` / `Report types`. **Additivity: Not documented.** Nullability: **Not documented** except where the description states a condition (e.g. SKU “Not available for vendors”).

**Request columns only from the intersection of this glossary’s Report-types list and the report-type page’s column set.** Glossary lists extra reports; that does not authorize adding a column omitted from the report-type page.

| Field | Type | Official definition (abridged) | Listed for `spCampaigns` on glossary? |
|---|---|---|---|
| `date` | String | Date of ad activity `YYYY-MM-DD` | yes (also many others) |
| `campaignId` | Integer | Campaign ID | yes |
| `campaignName` | String | Campaign name | yes |
| `campaignStatus` | String | Campaign status | yes |
| `campaignBudgetAmount` | Decimal | Total budget allocated to the campaign | yes |
| `campaignBudgetType` | String | One of daily or lifetime | yes |
| `campaignBudgetCurrencyCode` | String | Campaign currency code | yes |
| `campaignBiddingStrategy` | String | Bidding strategy associated with a campaign | **yes — glossary lists `spCampaigns` only** |
| `impressions` | Integer | Total number of ad impressions | yes |
| `clicks` | Integer | Total number of clicks on an ad | yes |
| `cost` | Decimal | Total cost of ad clicks | yes |
| `spend` | Decimal | Total cost of ad clicks | yes (glossary subset is smaller than `cost`) |
| `costPerClick` | Decimal | Total cost / total clicks | yes |
| `clickThroughRate` | Decimal | Clicks / impressions | yes |
| `purchases{1,7,14,30}d` | Integer | Attributed conversion events within N days of an **ad click** | yes |
| `sales{1,7,14,30}d` | Decimal | Sales within N days of an ad click. `sales14d` also notes vCPM click+view vs CPC click-only | yes |
| `purchasesSameSku*` / `attributedSalesSameSku*` / `unitsSoldSameSku*` | Integer/Decimal | Same purchased SKU as advertised | yes |
| `unitsSoldClicks*` | Integer | Units ordered within N days of an ad click | yes |
| `topOfSearchImpressionShare` | Decimal | Share of eligible top-of-search impressions | yes (also some SB) |
| `adGroupId` / `adGroupName` / `adStatus` | Integer/String | Ad group id/name; `adStatus` description says “Status of the ad group” | yes |
| `placementClassification` | String | Page location where an ad appeared | yes (also `sbPlacement`) |
| `addToList` / `qualifiedBorrows` / `royaltyQualifiedBorrows` | Integer/Decimal | Wish-list / Kindle Unlimited (book advertisers) | yes |
| `portfolioId` | Integer | Portfolio the campaign is associated with | **no `spCampaigns`** on glossary (targeting/search/purchased/advertised/ST) |
| `portfolioName` | String | Portfolio name | **no `spCampaigns`** (prompt/video extensions only on glossary) |
| `targeting` / `keyword` / `keywordId` / `keywordType` / `matchType` | String/Integer | Targeting expression / keyword text / match | **not** `spCampaigns` — `spTargeting` / `spSearchTerm` |
| `searchTerm` | String | Customer search term; same as query | `spSearchTerm`, `sbSearchTerm` only |
| `advertisedAsin` / `advertisedSku` | String | Advertised product; SKU not for vendors | `spAdvertisedProduct`, `spPurchasedProduct`, extensions — **not** `spCampaigns` |
| `purchasedAsin` | String | Purchased product ASIN | `spPurchasedProduct`, `sbPurchasedProduct` |
| `salesOtherSku7d` / `unitsSoldOtherSku7d` | Decimal/Integer | Other-SKU within 7 days of click | targeting/search/advertised/purchased — **not** `spCampaigns` |
| `acosClicks7d` / `14d`, `roasClicks7d` / `14d` | Decimal | ACOS/ROAS from click windows | targeting/search/advertised — **not** `spCampaigns` on glossary |
| `adId` | Integer | Unique numerical ID of the ad | advertised-product / extensions — **not** `spCampaigns` |
| `newToBrand*` | Integer/Decimal | First-time brand orders, 1-year lookback; not for book vendors | **SB/SD/ST/DSP**, not SP campaign reports |
| `impressionsViews` | Integer | MRC-viewable impressions | **SD only** |
| `promotedAsin` / `promotedSku` / `matchedTargetAsin` | String | SD advertised / SD product-page ASIN | **SD only** |
| `invalidImpressions` / `invalidClickThroughs` | Integer | Traffic-quality filter removals | `spGrossAndInvalids` family, not `spCampaigns` |
| `promptText` | String | AI-generated prompt copy | `spPromptAdExtension`, `sbPromptAdExtension` |
| Multi-touch / `viewAttributedSales14d` / `budgetUsagePercent` as report columns | — | **Not found** as glossary headings | Do not invent |

`kindleEditionNormalizedPagesRead` without `14d` is listed for **SD** report types on the glossary; SP campaign base list uses `kindleEditionNormalizedPagesRead14d`. Request the **report-type page** name.

---

## 13. Phase A Implementation Specification

This section is a **design** for a future implementation agent. It is not an implementation authorization.

### 13.1 Selected campaign-management API family and reason

**Sponsored Products Version 3** read list APIs + **Reporting v3** `spCampaigns`. Reason: §5.2. Do not call Ads API v1 `SPQueryCampaign` in Phase A.

### 13.2 Endpoints (read-only)

1. `GET {regionalHost}/v2/profiles` — no Scope header.
2. `POST {regionalHost}/sp/campaigns/list`
3. `POST {regionalHost}/sp/adGroups/list`
4. `POST {regionalHost}/sp/productAds/list`
5. `POST {regionalHost}/reporting/reports`
6. `GET {regionalHost}/reporting/reports/{reportId}`
7. `GET {presignedHttpsUrl}` — no Ads auth headers, HTTPS only, no redirects.

Hosts: NA `https://advertising-api.amazon.com`, EU `https://advertising-api-eu.amazon.com`, FE `https://advertising-api-fe.amazon.com`. Select host from the profile’s region, not from the LWA token host.

### 13.3 Headers and media types

Entity lists:

- `Amazon-Advertising-API-ClientId`
- `Authorization: Bearer <access_token>`
- `Amazon-Advertising-API-Scope: <profileId>`
- `Content-Type` and `Accept`: `application/vnd.spCampaign.v3+json` / `application/vnd.spAdGroup.v3+json` / `application/vnd.spProductAd.v3+json` respectively.

Reporting create:

- Same ClientId / Bearer / Scope.
- `Content-Type: application/vnd.createasyncreportrequest.v3+json`
- Do **not** silently add `Amazon-Ads-AccountId` or rename ClientId until §7 is closed with Amazon support. Document the conflict in the run provenance.

Profiles: ClientId + Bearer only.

Until support closes §7, fail closed on 401/400 rather than guessing a second header.

### 13.4 Pagination

All SP v3 lists: request `maxResults` (EWise today 50; official default is “max page size for given API” — exact max **Not documented** on the extracted list block). Follow `nextToken` until absent. Persist `totalResults` when present for completeness checks.

Profiles: single GET; if `len == 5000`, treat as possible truncation (**Not documented** whether a continuation token exists).

### 13.5 Entity schemas to persist (slowly changing dimensions)

Persist only fields named on the official SP v3 list schemas (§10.2–10.4). Additional live fields may be stored as opaque JSON **without** promoting them to Copilot metrics. Do not treat a Production-observed extra field as a contract.

**Campaign (natural key: organization_id, ads_profile_id, campaign_id string):**

- `campaignId` (canonical string; glossary Integer vs OpenAPI string — §7.3)
- `name`
- `state` (`ENABLED` / `PAUSED` / `ARCHIVED` for live entities; other OpenAPI enum values stored if present)
- `targetingType` required `AUTO` / `MANUAL`
- `startDate` (`YYYY-MM-DD`); `endDate` when present (nullable)
- `budget.budget`, `budget.budgetType` (`DAILY` / `OTHER`), `budget.effectiveBudget` when present
- `dynamicBidding.strategy` and `dynamicBidding.placementBidding[]` when present
- `portfolioId` when present (opaque; no name join in Phase A)
- `includeExtendedDataFields=true` → persist `extendedData.creationDateTime`, `extendedData.lastUpdateDateTime`, `extendedData.servingStatus`
- Optional opaque: `globalCampaignId`, `tags`, `autoManageCampaign` — not Copilot metrics
- Do **not** invent targeting type from reports. `campaignBiddingStrategy` is a **report** column, not a substitute for `dynamicBidding`

**Ad group:** required `adGroupId`, `campaignId`, `defaultBid`, `name`, `state`; optional `extendedData`, `globalAdGroupId`.

**Product ad:** required `adId`, `adGroupId`, `campaignId`, `state`; `asin` vendors only; `sku` sellers only; optional `extendedData`, `globalAdId`.

Do not persist tokens, `token_reference`, or presigned URLs.

### 13.6 Report configurations (Phase A)

Three separate report-run types; never one checkpoint for all three.

Common: `adProduct=SPONSORED_PRODUCTS`, `reportTypeId=spCampaigns`, `timeUnit=DAILY`, `format=GZIP_JSON`, max 31-day `startDate`/`endDate` slices, lookback capped at **95 days**.

**Campaign grain** `groupBy: ["campaign"]`
Required columns: `date, campaignId, campaignName, campaignStatus, campaignBudgetAmount, campaignBudgetType, campaignBudgetCurrencyCode, campaignBiddingStrategy, impressions, clicks, cost, spend, costPerClick, clickThroughRate, purchases1d, purchases7d, purchases14d, purchases30d, sales1d, sales7d, sales14d, sales30d, purchasesSameSku1d, purchasesSameSku7d, purchasesSameSku14d, purchasesSameSku30d, attributedSalesSameSku1d, attributedSalesSameSku7d, attributedSalesSameSku14d, attributedSalesSameSku30d, unitsSoldClicks1d, unitsSoldClicks7d, unitsSoldClicks14d, unitsSoldClicks30d, unitsSoldSameSku1d, unitsSoldSameSku7d, unitsSoldSameSku14d, unitsSoldSameSku30d, topOfSearchImpressionShare`
Optional (include if still under payload/time budget): Kindle and addToList/borrow columns from the official base list.
Do not include SB/SD-only metrics.

**Ad-group grain** `groupBy: ["adGroup"]`
Same traffic/conversion base + `adGroupName, adGroupId, adStatus`. Do not assume campaign budget columns are valid at this grain unless they appear on the official adGroup additional list (they do not).

**Placement grain** `groupBy: ["campaignPlacement"]`
Base + `placementClassification` + campaign metadata / ToS share as documented. Single groupBy if filters are used.

### 13.7 Database grains and natural keys

| Grain | Natural key (plus organization_id) |
|---|---|
| Profile dimension | `ads_profile_id` |
| Campaign dimension | `ads_profile_id, campaign_id` |
| Ad group dimension | `ads_profile_id, ad_group_id` |
| Product ad dimension | `ads_profile_id, ad_id` |
| Campaign-day fact | `ads_profile_id, campaign_id, date, time_unit=DAILY, report_type_id=spCampaigns, group_by=campaign` — store 1/7/14/30 columns in-row, **not** a single `attribution_window` key |
| Ad-group-day fact | same with `ad_group_id` and `group_by=adGroup` |
| Placement-day fact | `ads_profile_id, campaign_id, date, placement_classification, group_by=campaignPlacement` |

EWise’s current `amazon_ads_daily_performance_facts` key `(profile, entity_type, entity_external_id, date, attribution_window)` cannot represent placement or multi-window columns. Phase A needs a new grain design (migration is **not** authorized by this document).

Every fact: marketplace id, currency, timezone name, `report_run_id`, Amazon `reportId`, retrieved_at.

### 13.8 Attribution handling

- Persist 1d/7d/14d/30d click windows side by side.
- Do not add windows together.
- Re-request closed dates inside 95 days so FAQ restatements (traffic 3 days; conversions 1/7/28 and up to 42 days for a 14d window) can refresh.
- Do not treat “today” as final. Prefer explaining days older than 3 days for traffic and older than the restatement horizon for conversions.
- Same-SKU columns are not other-SKU. Other-SKU is Phase B.

### 13.9 Checkpoints, backfill, retry/resume, idempotency

- Checkpoint per profile **and** per (`reportTypeId`, `groupBy`, `timeUnit`).
- Backfill: 31-day slices covering `min(95 days, operator-approved history)`.
- Create report → persist Amazon `reportId` before poll.
- HTTP 425: wait and poll the in-flight identical report; do not treat as malformed body.
- HTTP 429: backoff; reduce concurrent creates; FAQ says spread through the day; 1–2 completed downloads per report type per advertiser per day is the official recommendation — Phase A three groupBys already exceed “one type” if counted separately; treat each groupBy as its own report type for throttling math and expect to serialize them.
- Resume: distinguish “create succeeded, poll left” from “create never succeeded.”
- Upsert facts on the natural key. Do not double-count overlapping slices; last successful COMPLETED download for that key wins after row-contract validation.
- No client-supplied Amazon idempotency key is documented.

### 13.10 Console reconciliation

After the first successful COMPLETED `spCampaigns` campaign-grain download for one closed date: operator compares Ads Console campaign totals for the same profile, marketplace, date, and 14-day click window against ingested `cost`, `clicks`, `impressions`, `sales14d`. Placement and ad-group grains are reconciled separately; they will not equal campaign totals automatically.

### 13.11 Data-quality rejection rules

Reject a run (do not mark succeeded) if:

- Non-empty body parses to zero rows.
- `campaignId` / `adGroupId` is boolean or non-integer float.
- `timeUnit=DAILY` rows lack `date`, or `SUMMARY` rows are stored as daily facts.
- Currency missing on money columns.
- `groupBy` in the stored provenance ≠ requested groupBy.
- Download URL is not HTTPS.

### 13.12 Copilot capabilities unlocked after Phase A (tools over ingested facts only)

- Campaign performance explanation (multi-window).
- Placement efficiency (`placementClassification`).
- Campaign structure quality (counts/states/budgets/names — not bids).
- Sync / data-quality diagnosis.
- Attribution-maturity explanation (1 vs 7 vs 14 vs 30).

Not unlocked: wasted-spend, search-term harvest, negatives, advertised-product profitability, TACOS vs SP-API, budget-constrained alerts (needs budget usage).

### 13.13 Explicit exclusions

- Any POST/PUT that creates, updates, or deletes campaigns, bids, budgets, or ads.
- Ads API v1 campaign query.
- SP Version 2 and Reporting v2.
- SB/SD/Stream/AMC/DSP/Attribution.
- `spTargeting` / `spSearchTerm` / `spAdvertisedProduct` / `spPurchasedProduct`.
- `POST /portfolios/list` (Phase B). Phase A stores opaque `portfolioId` only.
- Sending both ClientId header names.
- Inventing `Amazon-Ads-AccountId` from profileId.
- Applying recommendations, budget rules, or DSP Quick Actions.
- Live Amazon calls in automated tests.
- Copilot calling Ads APIs directly.
- Storing secrets in business tables or evidence.
- Using Production-observed live responses as the sole contract.

---

## 14. Ads-to-SP-API joining strategy

Tenancy key is always `organization_id`. Never use Ads `profileId`, SP-API `selling_partner_id`, or Amazon account IDs as the tenant.

### 14.1 Safe join keys

| Key | Ads source | SP-API / seller source | Ambiguity |
|---|---|---|---|
| Organization | `amazon_ads_connections.organization_id` | All seller tables | None if both grants belong to the same org |
| Seller account | Optional `amazon_seller_account_id` on Ads connection (best-effort in current schema) | `amazon_seller_accounts` (12B.2A schema; live population still limited) | **Ambiguous** until canonical identity is populated |
| Advertiser profile | `profileId`, `countryCode`, `currencyCode`, `timezone`, `accountInfo.marketplaceStringId` | Marketplace participation rows | One SP-API selling partner often maps to **multiple** Ads profiles (marketplace × account type). Never 1:1. |
| Marketplace | Profile `marketplaceStringId` / country | SP-API marketplace id / `amazon.com` vs `amazon.in` | Country code is not a marketplace id. Join on Amazon marketplace id, not country. |
| Currency | `campaignBudgetCurrencyCode` / profile `currencyCode` | Order/finance currency; listing price currency | Do not convert. If currencies differ, suppress money ratios. |
| Timezone | Profile `timezone`; official facts are profile-local dates. Official Profiles sample uses `America/Los_Angeles` for CA/MX/US together | SP-API sales/traffic dates (report-specific timezone) | **Ambiguous** if the SP-API report timezone ≠ Ads profile timezone. Persist both; do not silently coerce to UTC. |
| ASIN | `advertisedAsin`, `purchasedAsin`, `promotedAsin`, `matchedTargetAsin` | Listings, orders, inventory, sales-and-traffic | Purchased ASIN is not advertised ASIN. |
| Seller SKU | `advertisedSku`, `promotedSku` | Listings SKU, FBA inventory SKU | SKU is marketplace-scoped. Same SKU string on two marketplaces is not the same product. |
| Date | Report `date` for DAILY; `startDate`/`endDate` for SUMMARY | Order purchase date, sales-traffic date, inventory snapshot date | SUMMARY ranges are not daily facts. Do not join a SUMMARY row to a single order day. |
| Attribution model/window | Column suffix 1d/7d/14d/30d; click vs view | SP-API has no Ads attribution window | Paid sales ≠ ordered sales. Always label the window. |

### 14.2 Metric construction

| Metric | Ads inputs | SP-API / seller inputs | Suppression |
|---|---|---|---|
| CTR | clicks / impressions from the **same** report row | none | impressions = 0 |
| CPC | cost / clicks | none | clicks = 0 |
| Conversion rate | purchases{window} / clicks | none | clicks = 0; never mix windows |
| ACOS | cost / sales{window} or official `acosClicks{window}` | none | sales = 0; window mismatch |
| ROAS | sales{window} / cost or official `roasClicks{window}` | none | cost = 0 |
| TACOS | Ads cost / **total** sales | SP-API ordered sales (or seller sales+traffic) for same marketplace/day | Missing either side; currency/timezone mismatch |
| Paid-sales share | Ads attributed sales{window} / total sales | SP-API total sales | Do not treat attributed sales as a subset without caveats (halo, other-SKU, nested windows) |
| Organic-sales share | 1 − paid share **only if** paid ⊆ total is validated | same | Usually **suppress**; official halo/other-SKU can overlap poorly with ordered sales |
| Break-even ACOS | Ads ACOS vs seller unit economics | COGS + fees + price from profit engine | Missing COGS; unknown fees |
| Contribution profit after advertising | unit contribution − Ads cost | profit engine + Ads cost at matching grain | Grain mismatch (campaign vs ASIN) |
| Budget utilization | Budget usage API and/or `campaignBudgetAmount` vs cost | none | Missing budget amount; rule-based budgets |
| Search-term waste | `spSearchTerm` cost with low/no purchases{window} | none | Click-gated report; zeros are not “no impressions” |
| Placement efficiency | `spCampaigns` `groupBy campaignPlacement` | none | Do not use the SB Placement report type for SP |
| Inventory-adjusted opportunity | advertised-product demand vs FBA/on-hand | inventory health / listings | Stale inventory snapshots |
| Cross-sell / brand-halo | `spPurchasedProduct` advertised vs purchased ASIN | listings catalog | Missing catalog match |

Additive vs non-additive (conservative until an official additivity page is extracted):

- **Treat as additive within one report, one `timeUnit=DAILY`, one groupBy, one attribution window:** impressions, clicks, cost, purchases*d, sales*d, units* at that same window — still **do not** add 1d+7d+14d+30d (nested windows).
- **Do not add across attribution windows.**
- **Do not add SP + SB + SD** without an official cross-program document.
- **Do not add same-SKU + other-SKU + advertised-product sales** into “total attributed sales” without an official statement. Purchased-product official prose describes **non-advertised** purchases.
- **Share, rate, and CPC/ACOS/ROAS columns are non-additive.** Recompute from additive components or request Amazon’s column at the exact grain shown to the user.

### 14.3 Reporting semantic model

Proposed user-facing grains (not a migration):

1. **Account day** — profile + date + ad product + attribution window.
2. **Campaign day** — plus campaignId, placement optional.
3. **Target day** — keyword or targeting expression, never mixed without `keywordType`.
4. **Search-term day** — click-gated; store `searchTerm` as sensitive business text (never logs).
5. **Advertised product day** — ASIN + SKU.
6. **Purchased product day** — advertised ASIN × purchased ASIN; conversion-only.
7. **Budget now** — point-in-time usage, not a daily fact.
8. **Entity current** — slowly changing campaign/ad group/ad/keyword/target/portfolio snapshot.

Each stored fact must carry: `organization_id`, `ads_profile_id`, marketplace id, currency, timezone name, `ad_product`, `report_type_id`, `time_unit`, `group_by`, attribution window(s) actually ingested, `report_run_id`, retrieved_at, Amazon `reportId`.

### 14.4 Copilot skill-to-data matrix

All prescriptive output is advisory. Copilot may not call Ads or SP-API directly. Skills consume ToolRegistry tools over already-ingested, evidence-tagged facts.

| Skill | Type | Required Ads datasets | Required SP-API / seller datasets | Metric definitions | Min history | Attribution maturity | Quality checks | Suppression | Evidence shown |
|---|---|---|---|---|---|---|---|---|---|
| Campaign performance explanation | Descriptive | `spCampaigns` DAILY campaign | Optional listings names | impressions, clicks, cost, sales{window}, ACOS/ROAS | 7 complete days | Re-request last lookback; do not explain “today” as final. FAQ: traffic may move for 3 days | Row counts > 0; currency present | Incomplete day; mixed windows | Campaign id/name, date range, window, reportTypeId |
| Wasted-spend detection | Diagnostic | `spSearchTerm` + `spTargeting` | none | cost with purchases{window}=0 or ACOS ≫ target | 14 days | Prefer 14d or 30d, not 1d | Click-gate disclosed; `searchTerm=*` handled | Search-term history > 65d; SUMMARY mistaken for daily | Search term hashed or truncated in logs; full term only in UI evidence envelope as observed |
| Search-term harvesting | Prescriptive (advisory) | `spSearchTerm` | none | converting queries not in exact keyword set | 14 days | 14d+ | Join to keyword entity list | Missing keyword list; view-only grant may not list keywords (§11.5) | Query, match type, purchases, sales |
| Negative-target candidates | Prescriptive (advisory) | `spSearchTerm` + `spTargeting` + negative entity lists | none | high cost, low conversion, not already negated | 14 days | 14d+ | Negatives list freshness | Missing negatives ingestion | Candidate expression, cost, conversions |
| Budget-constrained profitable campaigns | Diagnostic | Budget usage + `spCampaigns` + budgetRecommendations | optional profit | time-in-budget < 100% and ACOS below break-even | 7 days | 7d+ | Budget usage 207 errors isolated | Recommendation estimated missed sales labelled estimated | Budget %, recommended budget, ACOS |
| Placement efficiency | Diagnostic | `spCampaigns` groupBy `campaignPlacement` | none | cost and sales by `placementClassification` | 14 days | 14d | Single groupBy filters | Using SB Placement report for SP | Placement class, cost, sales |
| Advertised-product profitability | Diagnostic | `spAdvertisedProduct` | listings, profit/COGS, fees | contribution after ads at ASIN | 14 days | 14d | SKU/ASIN match; currency | Missing COGS | ASIN, SKU, ads cost, contribution |
| Purchased-product / cross-sell | Descriptive | `spPurchasedProduct` | listings catalog | advertised vs purchased ASIN units/sales | 14 days | 14d | No cost on this report | ACOS requested on this report alone | ASIN pair, units, sales |
| Brand-halo analysis | Descriptive | `spPurchasedProduct` + same-SKU vs other-SKU columns | listings | other-SKU share of attributed units | 14 days | 14d | Do not add to advertised-product sales | Missing advertised ASIN | Halo ASIN, other-SKU metrics |
| Inventory-aware advertising risk | Diagnostic | `spAdvertisedProduct` | inventory health / FBA qty | ads spend while inventory low | 7 days | 7d | Inventory snapshot age | Stale inventory | ASIN, ads cost, on-hand, snapshot time |
| Paid vs organic | Diagnostic | advertised-product sales{window} | sales-and-traffic ordered sales | paid share labelled attributed, not causal | 14 days | 14d | Timezone/marketplace match | Organic computed as residual | Both numerators and denominator |
| Break-even ACOS | Diagnostic | advertised-product or campaign cost/sales | profit-calc-v1 | break-even vs actual ACOS | 14 days | 14d | Profit engine unknown fees | Unknown COGS | Break-even formula inputs |
| TACOS trends | Descriptive | ads cost by day | total sales by day | cost / total sales | 28 days | rolling lookback | Same marketplace | Either series missing | Daily TACOS, window |
| Campaign structure quality | Diagnostic | entity lists (campaigns, ad groups, keywords, targets, negatives) | none | counts, match types, AUTO vs MANUAL | current snapshot | n/a | List pagination complete | Incomplete list ingest | Structure counts, not performance |
| Attribution-maturity explanation | Descriptive | multi-window columns on one report | none | 1d vs 7d vs 14d vs 30d | overlapping dates | inherent | Same reportId | Mixing SP click with SD view | Table of windows |
| Sync / data-quality diagnosis | Diagnostic | report-run ledger, checkpoints, sync errors | n/a | freshness, 425/429, row-contract failures | n/a | n/a | No secrets in evidence | — | Run status, age, counts |

### 14.5 Proposed data architecture

Design only. No migration in this task.

| Store | Purpose |
|---|---|
| Profile/account dimensions | profileId, country, marketplace id, currency, timezone, account type, selected flag |
| Campaign hierarchy dimensions | portfolio, campaign, ad group, ad; slowly changing (type 2) state/budget/bidding |
| Target dimensions | keyword vs targeting expression vs negative; match type; bid |
| Product mappings | advertised ASIN/SKU ↔ listing identity; purchased ASIN as a separate mapping |
| Daily performance facts | one table **per grain** (campaign, placement, target, search term, advertised product, purchased pair) |
| Hourly stream facts | Phase D only; separate high-volume store + retention |
| Attribution facts | windowed conversion columns stored in-column, not collapsed to a single 14d |
| Report-run ledger | already exists; extend with `report_type_id`, `group_by`, `time_unit`, `column_set_hash`, Amazon `reportId` |
| Sync checkpoints | per profile **and** per reportTypeId + groupBy; never one high-water for all datasets (search-term retention 65d ≠ campaign 95d) |
| Slowly changing configuration | entity snapshots with valid_from/valid_to |
| Source provenance | Amazon reportId, request hash, retrieved_at, official reportTypeId, docs URL/version date |
| Schema versions | vendor media type + reportTypeId + column list hash |
| Data-quality results | row-contract mismatch, partial columns, 425 coalescing |
| Freshness | last successful COMPLETED download per grain |
| Tenant isolation | `organization_id` on every table; profile-scoped queries |
| Retention | daily sponsored-ads facts: keep within Amazon’s documented retention plus EWise policy; search terms: sensitive; Stream hourly: roll up |
| Backfill | 31-day request slices covering min(official retention, operator-approved history) |
| Idempotency | natural key upsert; persist Amazon `reportId` before poll; honor 425 |
| Resume-after-failure | existing lease model; distinguish “report already created” from “not created” |
| Ads Console reconciliation | after each new reportTypeId, compare totals for a closed date to Console screenshots/exports (operator-performed) |

### 14.6 Data-quality and reconciliation rules

1. Never mark a run succeeded if zero rows parsed from a non-empty body.
2. Persist Amazon integer IDs as canonical strings only after lossless conversion; reject bools/floats.
3. Request `date` iff `timeUnit=DAILY`; `startDate`/`endDate` iff `SUMMARY`.
4. Split search-term vs targeting keywordType filters; do not mix BROAD/PHRASE/EXACT with TARGETING_EXPRESSION in one analytical grain without labelling.
5. Re-request within official **per-reportTypeId** retention so late conversions refresh (FAQ: traffic up to 3 days; conversions 1/7/28 days after the event; 14d window → up to 42 days).
6. Cap each request at 31 days except where official pages say otherwise (SB purchased product 731).
7. Treat HTTP 425 as “wait for in-flight identical report”, not as a malformed body.
8. Treat HTTP 429 with `Retry-After` if present; otherwise bounded backoff.
9. Reconcile campaign cost totals from `spCampaigns` against advertised-product cost and targeting cost **separately**; they are different grains, not automatic equals.
10. Console reconciliation gate: same profile, marketplace, date, and attribution window before declaring a reportTypeId production-complete.
11. FAQ recommended cadence: 1–2 report requests per advertiser per day per report type; serialize Phase A groupBys.

### 14.7 Security, tenancy, retention, and provenance

- Refresh/access tokens only in SecretProvider. Database stores `token_reference` only.
- No tokens, client secrets, authorization codes, or presigned download URLs in API JSON, frontend, Copilot, logs, or this document.
- Download: HTTPS only, no redirects. Official page says the URL is an S3 link and does not publish a hostname allowlist.
- Search terms and targeting expressions are advertiser-confidential. Do not log raw values.
- Tenant key: `organization_id`. Profile is a sub-scope.
- Read-only: no bid/budget/campaign writes.
- Provenance on every fact: provider `ADS_API`, reportTypeId, reportId, retrieved_at.
- Official refresh-token guidance: encrypt at rest, never client-side, never logs. Post-30-July-2026 tokens expire in 365 days from consent.
- Disconnect/deletion behavior remains an operator-facing privacy commitment; not specified by the Ads reporting pages extracted here.

### 14.8 Versioning and deprecation strategy

Do not call an API deprecated unless Amazon explicitly does so.

| Surface | Official status | Announcement / shutdown | Replacement | Official URL |
|---|---|---|---|---|
| Sponsored Products Version 2 OpenAPI | Deprecated for new work | Deprecations index lists related v2 reporting and suggested-keywords; SP v2 campaign-management is the older sibling of v3 | SP Version 3 OpenAPI | https://advertising.amazon.com/API/docs/en-us/sponsored-products/2-0/openapi · https://advertising.amazon.com/API/docs/en-us/reference/deprecations |
| Sponsored Products Version 3 list/CRUD | **Not listed as deprecated** | No shutdown date on deprecations index or campaign-management overview | Ads API v1 is the stated future of campaign management; no SP v3 shutoff | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod · https://advertising.amazon.com/API/docs/en-us/guides/campaign-management/overview |
| Reporting v2 `POST /v2/sp/{recordType}/report` | Deprecated / shut off | Shut off **30 March 2023** | Reporting v3 `POST /reporting/reports` | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/faq · https://advertising.amazon.com/API/docs/en-us/reference/1/reports |
| Reporting v3 | **Not listed as deprecated** | Current sponsored-ads reporting path | Keep until Ads API v1 generates reports | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/overview |
| Ads API v1 | GA for onboarded clients; some resources closed beta | “will eventually fully replace the ad product-specific APIs.” No SP v3 shutdown date | N/A (destination, not replacement yet for reporting) | https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/overview |
| Profiles `/v1/profiles` | Shut off | **28 May 2024** | `GET /v2/profiles` (current; not deprecated) | https://advertising.amazon.com/API/docs/en-us/release-notes/deprecations |
| SP v2 suggested keywords | Shut off | **1 June 2026** | `/sp/targets/keywords/recommendations` | same deprecations page |
| SP bid-recommendation **v3 media type** | Shut off | **15 May 2025** | v4/v5 media types | same |
| Legacy account APIs (`GET /dsp/advertisers`, `/adsAccounts`) | Deprecate Jul 2026, shutoff Jul 2027 | 2026/2027 | Accounts APIs in Ads API v1 family | same |
| Sponsored Brands reporting | **Preview** (not a deprecation) | Preview omits `isMultiAdGroupsEnabled=False` until GA | Wait for GA before treating as production facts | Reporting v3 overview + FAQ |
| DSP reports v2/v3 (legacy DSP reporting families) | Listed on deprecations index | See index | Reporting v3 DSP types / migration pages | https://advertising.amazon.com/API/docs/en-us/reference/deprecations |

Phase A uses SP v3 reads + Reporting v3 only. Phase A/B are **not** affected by an SP v3 or Reporting v3 shutdown. Pin vendor media types per operation. Re-read official pages when Amazon changes OAS or report-type columns.

---

## 15. Deferred-product overview

These are **not** implementation specifications. They are catalogued so Phase A does not invent them later.

| Product | Official API family / version | Purpose | Eligibility | Data offered | Delivery model | Reporting value | Copilot value | Dependencies | Security / ops | Phase | Official URLs |
|---|---|---|---|---|---|---|---|---|---|---|---|
| Sponsored Brands | Product-specific OpenAPI + Reporting v3 | Brand / video / store ads | Brand Registry for seller/vendor SB ([Profiles](https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles)); **SB reporting is preview** | `sbCampaigns`, `sbTargeting`, `sbSearchTerm`, `sbPurchasedProduct` (731d lookback) | Reporting v3 async | High once GA | Brand / NTB language after GA | Phase A–B SP stable | Preview: omit `isMultiAdGroupsEnabled=False` until GA | C | https://advertising.amazon.com/API/docs/en-us/sponsored-brands/3-0/openapi · report-type pages |
| Sponsored Display | Product-specific + Reporting v3 | Display / view-through | Brand Registry for seller/vendor SD | `sdCampaigns`, `sdTargeting`, `sdAdvertisedProduct`, `sdPurchasedProduct`; view-attributed metrics | Reporting v3 async | High for view-through | Must label click vs view | Phase B; operator confirms SD | Entity OpenAPI not fully extracted this pass | C | SD report-type pages |
| Amazon Marketing Stream | Stream subscriptions OpenAPI v1 (`application/vnd.amazonmarketingstreamsubscriptions.v1+json`) | Intra-day push instead of high-frequency Reporting v3 | “Integrated partners and other advertisers.” Exact self-serve eligibility **Not documented** beyond that sentence | See §15.1 | Push to advertiser/partner AWS | Hourly deltas; not a daily-fact replacement | Low until daily facts exist | Reporting v3 canary clean; AWS destination | Refresh-token-bound subscriptions; IAM on SQS/Firehose | D | §15.1 |
| Amazon Marketing Cloud | AMC APIs on Amazon Ads API (instance-level APIs retired **1 Aug 2024**, 410) | Privacy-safe clean room; custom queries; audiences | Separate from Ads campaign-management approval. Markets listed on overview (NA, BR, several EU/ME/APAC) | Event-level Ads inputs (DSP, sponsored ads, Amazon Live) + Amazon store purchases; **Not** a substitute for `spCampaigns` | Query / reporting / S3; not Reporting v3 | Advanced measurement / MTA / audiences | None until eligibility + written operator approval | AMC instance + S3 policy | OAuth same family; do not assume Ads grant includes AMC | E | https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-cloud/overview |
| Amazon DSP | Product-specific DSP APIs + Reporting v3 DSP types + Ads API v1 DSP samples | Programmatic display/video | Agency profile type; DSP advertiser / manager-linked `DSP_ADVERTISING_ACCOUNT` | DSP reports; Guidance API recommendations | Reporting v3 + DSP endpoints | Upper funnel | Separate semantic layer | Manager / DSP account | `Amazon-Ads-AccountId` / `dspAdvertiserId`. Quick Actions are writes | E | https://advertising.amazon.com/API/docs/en-us/guides/dsp/guidance-and-quick-actions · manager accounts · Reporting DSP types. **`/guides/dsp/overview` not found** |
| Amazon Attribution | Attribution API (**beta**) | Off-Amazon media last-touch to Amazon shopping | Professional seller brand owners in Brand Registry and vendors; US, CA, MX, UK, DE, FR, IT, ES, NL | Tags for non-Amazon media; programmatic click and click-attributed conversion reporting | Attribution API (not SP 14d) | Off-Amazon last-touch | Do not mix with SP 14d | Beta access | Beta | E | https://advertising.amazon.com/API/docs/en-us/guides/amazon-attribution/overview |
| Benchmarks | Reporting v3 `crossProgramBenchmarks` / `dspBenchmarks` | Category peer comparison | **Brand owner or representative** on Amazon Stores | NTB/CTR/CPC/CPM/video completion vs P25/P50/P75; `adProduct=ALL`; `groupBy` `brandCategoryBenchmarks` | Reporting v3 async; from 1 Jan 2025; updates every 48 hours | Comparative, **not** account truth | After SP/SB/SD facts | Brand eligibility | 456d retention; 90d daily / 456d weekly/monthly | E | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/benchmarks |
| Advanced measurement (Brand Metrics, Brand View Pro, MMM, conversion path) | Sidebar / report types | Brand / MTA | **Eligibility dependent**; several labelled beta | Conversion path availability `ALL` | Reporting / dedicated products | Brand / MTA | Never fold into SP daily facts | After SP facts | Beta labels | E | FAQ sibling nav; conversion-path report type |

### 15.1 Amazon Marketing Stream (Phase D)

Titles/URLs: [Overview](https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-stream/overview) · [Data guide](https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-stream/data-guide) · [FAQ](https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-stream/amazon-marketing-stream-faq) · [OpenAPI](https://advertising.amazon.com/API/docs/en-us/amazon-marketing-stream/openapi) · download https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/AmazonMarketingStream_prod_3p.json · [Managing subscriptions](https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-stream/managing-subscriptions)

| Topic | Official value |
|---|---|
| Purpose | Subscribe to advertising datasets; Amazon delivers to advertiser/partner AWS without intra-day Reporting v3 polling |
| Subscription model | Create subscriptions (`POST /streams/subscriptions`; DSP uses `POST /dsp/streams/subscriptions`). Bound to the **refresh token + email** used at create. `GET /streams/subscriptions` only shows subscriptions for that token. `clientRequestToken` on OpenAPI for create retries |
| AWS destinations | Amazon Data Firehose (S3/Redshift/Snowflake/Splunk/OpenSearch) or **simple SQS**. **FIFO queues are not supported** |
| AWS regions | NA `us-east-1` + `advertising-api.amazon.com`; EU `eu-west-1` + `-eu`; FE `us-west-2` + `-fe` |
| Supported datasets (data guide) | DSP: `adsp-traffic`, `adsp-conversion`, `adsp-clickstream`, `adsp-rich-media`. SP: `sp-traffic`, `sp-conversion`. SD: `sd-traffic`, `sd-conversion`. SB (**beta** in nav): `sb-traffic`, `sb-conversion`, `sb-clickstream`, `sb-rich-media`. Messaging: `budget-usage`. Recs: `sponsored-ads-campaign-diagnostics-recommendations` (beta), `sp-budget-recommendations`. Campaign management: `ads-campaign-management-campaigns`, `-adgroups`, `-ads`, `-targets`. NA/EU/FE all marked `x` on the data-guide table |
| Delivery latency | Overview: reporting traffic/conversions **hourly**; messaging (entity + budget ±5%) **near real-time**. Exact SLA **Not documented** |
| Schema / version | Datasets may add fields; communicated in release notes. Plan for additive schema |
| Duplicate delivery | FAQ: SQS/SNS **at-least-once**. Deduplicate on `idempotencyId` |
| Ordering | FIFO not supported. Ordered delivery **Not documented** |
| Retry / replay | No self-serve replay documented. Missing data: check DLQ, CloudWatch, then Amazon Ads API support with time range. SNS confirmation must complete within **2 days** or `FAILED_CONFIRMATION` |
| Idempotency | Application must be idempotent; use `idempotencyId`. Negative impressions/clicks/spend are **corrections** — include them |
| Timestamps | `time_window_start` ISO 8601 |
| Limitations | Advertising data only; no retail data. Placeholder keyword values are incomplete |
| Console vs Stream | Small discrepancies expected; FAQ does not name Stream as Console-authoritative |
| Copilot | Do not replace Reporting v3 daily facts. Hourly deltas are a later overlay |

---

## 16. Prioritized ingestion roadmap

Unchanged order: A (SP campaign intelligence) → B (targeting/search/ASIN/budget/negatives) → C (SB/SD) → D (Stream) → E (AMC/DSP/benchmarks).

**Phase A entry** still requires operator approval of this document **and** a dedicated read-only header diagnostic only after Amazon support or an explicit operator decision on §7. This research task did not run that diagnostic.

**Phase B entry:** Phase A Console reconciliation passed; confirm whether the Ads grant’s list-keywords permissions include view or only `campaign_proposed`.

---

## 17. Gaps and unresolved questions

B1–B10 are **closed**. Remaining items do not block a Phase A design. Amazon Support is still required for the header conflicts in §7 before dual-stack or a second header family is attempted.

| Unknown | Impact | Phase | Official URL | Attempted | Required resolution |
|---|---|---|---|---|---|
| ClientId header name (`Amazon-Ads-ClientId` vs `Amazon-Advertising-API-ClientId`) | Phase A uses SP v3 / Reporting v3 / Profiles family only and fails closed on 401/400 | A ops | §7.1 | Rendered authorization, Profiles, SP v3 OpenAPI, Reporting get-started, v1 getting started | Amazon Ads API support |
| `Amazon-Ads-AccountId` required for SP reporting (get-started table) vs omitted on official SP samples | Multi-account / future enforcement | A ops | §7.2 | Same pages | Amazon support; do not invent AccountId from profileId |
| Glossary Integer IDs vs OpenAPI string IDs | Persist canonical strings | A | §7.3 · columns glossary · SP v3 OpenAPI | Both pages extracted | Keep conflict; lossless string persist |
| Exact numeric `maxResults` default on SP v3 lists | Pagination completeness | A | SP v3 OpenAPI (“max page size for given API”) | List operation extracted | Follow `nextToken` until absent |
| Report download URL TTL | Resume window | A ops | Reporting get-started / FAQ | TTL not on those pages | Re-request if presigned URL expires |
| Whether v1 `campaignId` equals SP v3 `campaignId` | Future v1 migration only | Later | Not documented | v1 + SP v3 schemas compared; no equivalence statement | Official statement before dual-stack |
| Column additivity | Copilot rollups | A/B | Columns glossary has no Additivity field | Glossary extracted | Conservative non-additivity §14.2 |
| View-only LWA grants vs `POST /sp/keywords/list` | Phase B keyword harvest may 403 | B | SP v3 OpenAPI lists `advertiser_campaign_edit` or `campaign_proposed` | Operation extracted | Support or later read-only diagnostic |
| Profile `timezone` sample uses `America/Los_Angeles` for CA/MX/US | Date joins | A | Profiles guide | Sample extracted | Persist Amazon’s timezone string; do not “correct” it |
| Stream message ordering / replay | Phase D completeness | D | Stream overview + FAQ | FIFO unsupported; replay **Not documented** | Operator + support if Stream is started |
| Dedicated DSP overview URL | Phase E navigation | E | https://advertising.amazon.com/API/docs/en-us/guides/dsp/overview | Browser: “requested document was not found” | Use sibling DSP guides |

Unresolved operator decisions: attribution windows to display; search-term evidence policy; SB preview ingest versus wait for GA; backfill depth ≤ official retention; whether to change reporting Content-Type before the next live call; disconnect/deletion of historical facts.

---

## 18. Official-source index

| Page | URL |
|---|---|
| Amazon Ads API overview (hosts) | https://advertising.amazon.com/API/docs/en-us/reference/api-overview |
| Amazon Ads API v1 overview | https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/overview |
| Amazon Ads API v1 getting started | https://advertising.amazon.com/API/docs/en-us/reference/amazon-ads/getting-started |
| Ads API v1 Campaigns OpenAPI (includes SPQueryCampaign) | https://advertising.amazon.com/API/docs/en-us/api-spec-v1-campaigns |
| Ads API v1 Ad groups / Ads / Targets | https://advertising.amazon.com/API/docs/en-us/api-spec-v1-adgroups · https://advertising.amazon.com/API/docs/en-us/api-spec-v1-ads · https://advertising.amazon.com/API/docs/en-us/api-spec-v1-targets |
| Ads API v1 SP merged OpenAPI download | https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/AmazonAdsAPISPMerged_prod_3p.json |
| Authorization overview | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/overview |
| Authorization grants | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/authorization-grants |
| Access tokens | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/access-tokens |
| Refresh tokens | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/refresh-tokens |
| Profiles | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/profiles |
| Manager accounts | https://advertising.amazon.com/API/docs/en-us/guides/account-management/authorization/manager-accounts |
| Developer guides overview | https://advertising.amazon.com/API/docs/en-us/guides/overview |
| Onboarding | https://advertising.amazon.com/API/docs/en-us/guides/onboarding/overview |
| Campaign management overview | https://advertising.amazon.com/API/docs/en-us/guides/campaign-management/overview |
| Campaign entity matrix | https://advertising.amazon.com/API/docs/en-us/guides/campaign-management/entities/campaign |
| Reporting v3 overview | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/overview |
| Reporting v3 get started | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/get-started |
| Reporting FAQ | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/faq |
| Reporting columns | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/columns |
| Reporting v2→v3 migration | https://advertising.amazon.com/API/docs/en-us/reference/migration-guides/reporting-v2-v3 |
| Deprecated Reports (v2 family) | https://advertising.amazon.com/API/docs/en-us/reference/1/reports |
| Report types overview | https://advertising.amazon.com/API/docs/en-us/guides/reporting/v3/report-types/overview |
| Campaign / targeting / search-term / advertised / purchased reports | `.../report-types/campaign` · `targeting` · `search-term` · `advertised-product` · `purchased-product` |
| Gross/invalid, Prompt, Video, Benchmarks | `.../report-types/gross-and-invalid-traffic` · `prompt-ad-extension` · `video-ad-extension` · `benchmarks` |
| Sponsored Products v3 OpenAPI | https://advertising.amazon.com/API/docs/en-us/sponsored-products/3-0/openapi/prod |
| SP v3 OpenAPI download | https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/SponsoredProducts_prod_3p.json |
| Sponsored Products v2 OpenAPI | https://advertising.amazon.com/API/docs/en-us/sponsored-products/2-0/openapi |
| Portfolios guide / API / OpenAPI | https://advertising.amazon.com/API/docs/en-us/guides/portfolios/get-started · https://advertising.amazon.com/API/docs/en-us/reference/portfolios · https://d1y2lf8k3vrkfu.cloudfront.net/openapi/en-us/dest/Portfolios_prod_3p.json |
| Budget usage | https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/overview · https://advertising.amazon.com/API/docs/en-us/guides/budgets/usage/getting-started |
| Recommendations API | https://advertising.amazon.com/API/docs/en-us/guides/recommendations/recommendations-api/overview · `.../how-to` · `.../recommendation-types` |
| DSP Guidance and Quick Actions | https://advertising.amazon.com/API/docs/en-us/guides/dsp/guidance-and-quick-actions |
| Sponsored Brands OpenAPI | https://advertising.amazon.com/API/docs/en-us/sponsored-brands/3-0/openapi |
| Marketing Stream | https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-stream/overview · `.../data-guide` · `.../amazon-marketing-stream-faq` · https://advertising.amazon.com/API/docs/en-us/amazon-marketing-stream/openapi |
| Amazon Marketing Cloud | https://advertising.amazon.com/API/docs/en-us/guides/amazon-marketing-cloud/overview |
| Amazon Attribution (beta) | https://advertising.amazon.com/API/docs/en-us/guides/amazon-attribution/overview |
| Deprecations index | https://advertising.amazon.com/API/docs/en-us/reference/deprecations |
| Deprecations announcements | https://advertising.amazon.com/API/docs/en-us/release-notes/deprecations |
| Release notes | https://advertising.amazon.com/API/docs/en-us/release-notes/index |
| Ads API v1 release notes | https://advertising.amazon.com/API/docs/en-us/release-notes/ads-api |
| Official GitHub (supplement only) | https://github.com/amzn/ads-advanced-tools-docs |
| Data policies | https://advertising.amazon.com/API/docs/policy/en_US |

---

## Confirmation

This final official-documentation pass changed **only this Markdown file**. No application code, tests, migrations, endpoints, workers, environment variables, Railway/Cloudflare/Supabase access, live Amazon Ads API calls, report creates, token refresh against Amazon, or secret material were involved. No production identifiers, campaign names, raw search terms, or production rows appear above. No commit or PR was created.

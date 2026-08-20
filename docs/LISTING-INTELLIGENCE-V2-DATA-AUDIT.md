# Listing Intelligence V2 — Data Audit

**Status:** read-only architecture and data audit. No V2 scoring, mapping, prompts, or product-model changes were implemented.

**Evidence labels used throughout:**

- **OBSERVED IN OUR CODE/FIXTURES** — present in this repository (implementation and/or test JSON).
- **SUPPORTED BY RAINFOREST DOCUMENTATION BUT NOT OBSERVED LOCALLY** — described on Traject/Rainforest product-results/parameters pages; not present in our Rainforest fixtures and not mapped by our code.
- **UNKNOWN** — cannot be confirmed without a live `type=product` call (not performed).

Official Rainforest references consulted (documentation only; no live API usage):

- [Product parameters](https://docs.trajectdata.com/rainforestapi/product-data-api/parameters/product)
- [Product results](https://docs.trajectdata.com/rainforestapi/product-data-api/results/product)

---

## 1. Executive summary

The current Analyze-ASIN path spends **one Rainforest Product Data API credit** (`type=product`) and then **throws away a large fraction of listing-intelligence value** during `map_rainforest_product()`.

What we actually send:

```text
GET {RAINFOREST_BASE_URL}
  api_key=<secret>
  type=product
  amazon_domain=<marketplace>   # amazon.in in V1
  asin=<asin>
```

No optional flags are sent: not `include_a_plus_body`, not `include_image_block_videos`, not `include_summarization_attributes`, not `fields`, not reviews/offers types.

What we keep on normalized `Product` is a **thin listing snapshot**: title, brand, buy-box price, rating, review **count**, bullets, description **string**, still-image URLs, videos (when Rainforest already included `videos[]`), a **single category string**, **first BSR row only**, availability raw text, third-party seller (if any), and variation ASINs/labels.

V1 deterministic scoring (`listing-score-v1` / `SCORE_VERSION = "v1"`) measures **surface heuristics** on that snapshot: character/word counts, bullet count/length/caps/punctuation/duplicates, a small “benefit-word prefix” bonus, **image URL count** (explicitly not quality), a 12-field completeness ratio that mixes seller content with market signals, and social proof from rating + review volume.

OpenAI Listing Intelligence is **text-only**. It receives full title/bullets/description plus **image_count**, not image URLs or pixels. It does not receive videos, A+, specifications, category tree IDs, extra BSR rows, or variation attributes.

**Highest-leverage finding:** before buying Reviews, Offers, or extra product-scrape flags, V2 can extract more from the **same** `type=product` credit by mapping fields we already receive (or that Rainforest documents as default on that type) and by sending richer **text** context to AI. A+ HTML/`all_images` and image-block video depth may require **request parameters** on the same `type=product` endpoint; some of those parameters cost extra credits per official docs.

**Do not treat this audit as a V2 scoring spec.** Input signals are listed; weights and thresholds are intentionally omitted.

---

## 2. Current Rainforest product request

**Source of truth:** `apps/api/app/providers/rainforest.py` → `RainforestProductDataProvider._fetch`.

| Parameter | Sent? | Value |
|-----------|-------|--------|
| `api_key` | Yes | backend secret (never logged/exposed here) |
| `type` | Yes | `product` |
| `amazon_domain` | Yes | marketplace identifier (`amazon.in` in V1; from request, not hardcoded in provider) |
| `asin` | Yes | requested ASIN |
| `url` | No | — |
| `gtin` | No | — |
| `fields` | No | full default payload (whatever Rainforest returns) |
| `include_a_plus_body` | **No** | A+ HTML / `body_text` / documented `all_images` not requested |
| `include_image_block_videos` | **No** | official docs: **2 credits** instead of 1 |
| `include_summarization_attributes` | **No** | official docs: **2 credits**; `customers_say` / summarization attributes |
| `variant_prices` | **No** | official docs: **2 credits** |
| `include_book_description_raw` | No | — |
| `import_delivery` | No | official docs: **2 credits** |
| `include_safety_product_resources` | No | official docs: **3 credits** |

HTTP: `GET settings.rainforest_base_url` (default `https://api.rainforestapi.com/request`), timeout `RAINFOREST_TIMEOUT_SECONDS` (default 60).

**Options affecting A+ / media / HTML / variations / offers / reviews / specs:** none are requested. Whatever appears for those topics is only what Rainforest includes in the **default** `type=product` body.

**Cache (OBSERVED):** in-memory TTL `RAINFOREST_CACHE_TTL_SECONDS` (default **600s**). Cache stores the **mapped `Product`**, not raw Rainforest JSON. A cache hit records a ledger cache hit and **does not** call Rainforest. Discarded raw fields cannot be recovered from cache.

**Capabilities mismatch (OBSERVED):** `ProviderCapabilities.reviews=True` and `variations=True` are advertised on the provider. The product HTTP call does **not** fetch review text. Variations are mapped from `product.variants` when present.

**Fixture echo:** `apps/api/tests/fixtures/rainforest/product.json` `request_parameters` is exactly `{type, amazon_domain, asin}` and `request_info.credits_used` is `1`.

---

## 3. Current normalized Product model

**Source:** `apps/api/app/models/product.py`. Frontend mirror: `apps/web/src/lib/types.ts`.

| Field | Type | Notes |
|-------|------|--------|
| `asin` | str | |
| `marketplace` | str | e.g. `amazon.in` |
| `title` | str | required |
| `brand` | str \| null | |
| `price` | `{amount, currency}` \| null | buy-box only |
| `rating` | float 0–5 \| null | |
| `review_count` | int \| null | count only |
| `bullet_points` | list[str] | |
| `description` | str \| null | |
| `images` | `{url, alt?, variant?, is_main}` | `alt` exists on the model but mapper never sets it |
| `videos` | `{title, thumbnail_url, video_url, duration_seconds}` | |
| `category` | str \| null | **one flattened string** |
| `bsr` | `{rank, category}` \| null | **one row** |
| `availability` | str \| null | buy-box raw text |
| `seller` | `{name, id, is_fba, rating}` \| null | `rating` always `None` from Rainforest mapper |
| `variations` | `{asin, label, attributes}` | variation dimensions only |
| `last_fetched_at` | datetime | overwritten on cache hit |

**Not on Product:** A+ / brand story, specifications/attributes (except variation dimensions), category IDs/tree as structured data, extra BSR rows, image width/height, image alt, offer list, review bodies, recent sales, badges, Prime, rating breakdown, manufacturer, ingredients.

Serialization: FastAPI returns this model as-is on `GET /api/v1/products/{asin}`. Analyze Listing and AI endpoints accept the already-normalized `Product` in the request body; they do not re-fetch Rainforest unless a later competitor compare does.

---

## 4. Rainforest → Product mapping table

**Mapper:** `apps/api/app/parsers/rainforest_product_mapper.py`.  
**Scoring:** `apps/api/app/analytics/listing_rules.py`.  
**AI:** `apps/api/app/ai/context.py`.

| Rainforest source | Product field | Mapped? | Used by deterministic analysis? | Used by AI? |
|-------------------|---------------|---------|--------------------------------|-------------|
| `product.title` | `title` | Yes (required; missing → parse error) | Yes (length, words, caps, repeats) | Yes (full text) |
| `product.asin` else request ASIN | `asin` | Yes | No (identity only) | Yes |
| `product.brand` | `brand` | Yes | Completeness only | Yes |
| `product.buybox_winner.price.value` + `.currency` | `price` | Yes | Completeness only (not price quality) | Yes |
| `product.rating` | `rating` | Yes | Completeness + social proof | Yes |
| `product.reviews_total` else `product.ratings_total` | `review_count` | Yes | Completeness + social proof | Yes |
| `product.feature_bullets[]` strings | `bullet_points` | Yes | Yes (count, length, caps, punct, dupes, benefit prefix) | Yes (full list) |
| `product.description` else `product.book_description` | `description` | Yes | Yes (length bands only) | Yes (full string) |
| `product.main_image.link` | `images[].url` (`is_main`) | Yes | Image **count** | **Count only** |
| `product.images[].link` | `images[].url` | Yes (deduped by Amazon image ID) | Count | Count only |
| `product.images[].variant` | `images[].variant` | Yes | No | No |
| `product.images_flat` | additional URL candidates | Yes | via resulting images | via count |
| `product.videos[].title` | `videos[].title` | Yes | **No** | **No** |
| `product.videos[].thumbnail` | `videos[].thumbnail_url` | Yes | No | No |
| `product.videos[].link` if playable | `videos[].video_url` | Yes | No | No |
| `product.videos[].duration_seconds` | `videos[].duration_seconds` | Yes | No | No |
| play-icon overlay URLs in `images[]` | `videos[]` thumbnail-only | Yes | No | No |
| `product.categories_flat` | `category` | Yes (preferred) | Completeness only | Yes (string) |
| `product.categories[].name` joined | `category` | Fallback if no flat | Completeness | Yes |
| `product.search_alias.title` | `category` | Fallback if no categories | Completeness | Yes |
| `product.bestsellers_rank[0].rank` | `bsr.rank` | Yes | Completeness only (**not** social-proof score) | Yes (first row only) |
| `product.bestsellers_rank[0].category` | `bsr.category` | Yes | Completeness | Yes |
| `product.buybox_winner.availability.raw` | `availability` | Yes | Completeness | Yes |
| `product.buybox_winner.fulfillment.third_party_seller.{name,id}` | `seller` | Yes if name present | Completeness | Yes (name, is_fba, rating=null) |
| `fulfillment.is_fulfilled_by_amazon` | `seller.is_fba` | Yes | No (except seller present) | Yes |
| `product.variants[].asin` | `variations[].asin` | Yes | **No** | **No** |
| `product.variants[].title` | `variations[].label` | Yes | No | No |
| `product.variants[].dimensions[]` | `variations[].attributes` | Yes | No | No |
| (generated) | `last_fetched_at` | Yes | No | No |

Image helper: `apps/api/app/parsers/amazon_media.py` prefers unsized `I/{id}.{ext}` sibling when grouping Amazon image IDs.

---

## 5. Data currently discarded

### 5.1 Discarded from fields **OBSERVED** in our product/media fixtures

These exist in `product.json` and/or `media.json` and are **not** stored on `Product` (or are stored then unused).

| Observed Rainforest path | What happens | Listing-intelligence relevance |
|--------------------------|--------------|--------------------------------|
| `product.bestsellers_rank[1…]` | Only index `0` mapped | **P0.** Fixture has Electronics #32614 **and** Sports & Action Video Cameras #161. Leaf rank is discarded. Raw BSR without the right category is misleading. |
| `product.categories[].category_id` | Names joined or flat string only | Category context / browse-node identity lost. |
| `product.search_alias` | Used only if no categories | Browse alias discarded when tree exists. |
| `product.buybox_winner.availability.type` | Only `raw` kept | Structured in-stock vs raw display text. |
| `product.buybox_winner.fulfillment.is_sold_by_amazon` | Ignored | Amazon-sold listings often have **no** `third_party_seller` → `seller` is `None` → completeness treats seller as “missing listing data”. |
| `product.variants[].is_current_product` | Ignored | Which variation is the fetched ASIN. |
| `product.videos[].group_type` | Ignored | Distinguishes this-product vs related videos (docs). |
| Image width/height | **Not in our fixtures** | See §7. |
| `Image.alt` | Model field never populated | No alt from Rainforest image objects in fixtures (`link` + `variant` only). |
| Raw Rainforest JSON | Mapped then dropped; cache is `Product` | Cannot re-parse extra fields without another fetch. |

**Mapped but unused by V1 score and AI (still displayed in UI for media):**

- `Product.videos` (gallery in `product-media-gallery.tsx`)
- `Product.variations` (not sent to OpenAI)
- `Image.variant` / `is_main` (gallery does not require variant)

### 5.2 Documented default `type=product` fields **NOT in our fixtures** (therefore UNKNOWN on amazon.in live)

Official product-results **example** includes many keys our fixtures omit. If Rainforest returns them on amazon.in, the mapper currently **silently drops** them.

High-value examples (documentation, not local JSON):

- `product.a_plus_content` (presence, brand story, optional body)
- `product.specifications` / `specifications_flat`
- `product.ingredients` / `diet_type` / `manufacturer`
- `product.rating_breakdown`
- `product.recent_sales` (e.g. “50+ bought in past month”)
- `product.top_reviews` (featured review **text** on the product page)
- `product.more_buying_choices`
- `product.sub_title` (often brand store link)
- `product.images_count` / `videos_count`
- Video `width` / `height` / `group_id`

In-repo provider doc (`docs/rainforest-provider.md`) already notes a live amazon.in observation: **`videos_count` may be present while `videos` is omitted** unless a deeper video scrape ran. That is consistent with not sending `include_image_block_videos`.

---

## 6. A+ Content audit

**Code search:** no `a_plus_content` (or similar) in `apps/api` application code. Mapper never reads it. Product model has no A+ fields. AI context has no A+. Scoring has no A+.

**Fixtures:** none of `product.json`, `media.json`, `missing_fields.json` contain `a_plus_content`.

| Question | Answer | Evidence |
|----------|--------|----------|
| Available from existing product call? | **UNKNOWN locally.** **Documentation: YES for presence/brand story on default product results.** A+ **HTML** / **plain `body_text`** / documented **`all_images` (URL + optional alt `name`)** require `include_a_plus_body=true` on the **same** `type=product` request. Official parameters page does **not** list an extra credit charge for `include_a_plus_body` (unlike `include_image_block_videos`). | Traject product results + parameters. Not in our fixtures. |
| Currently mapped? | **NO** | `rainforest_product_mapper.py` |
| Currently used? | **NO** | scoring, AI, frontend |
| Where mapping stops | `map_rainforest_product()` never reads `product.a_plus_content`. Provider docs state A+ / brand-story images are **not** mapped into `Product.images`. | `docs/rainforest-provider.md` |

Documented `a_plus_content` shape (documentation sample):

- `has_a_plus_content` (bool)
- `has_brand_story` (bool)
- `third_party` (bool)
- `brand_story.hero_image` / `brand_story.images[]` (URLs)
- `company_logo`, `company_description_text` (documented)
- `body` / `body_text` — **only if** `include_a_plus_body=true`
- `all_images[].link` + `all_images[].name` (alt) — documented as A+ images; parameters/results text ties rich A+ body (and product-updates note `all_images`) to `include_a_plus_body`

**Brand Story:** documented as part of default `a_plus_content` when `has_brand_story` is true. **Not observed in our fixtures. Not mapped. Not used.**

---

## 7. Image / media audit

### What Rainforest provides (observed)

| Item | Observed in fixtures? |
|------|------------------------|
| Main image | Yes — `product.main_image.link` |
| Secondary images | Yes — `product.images[]` with `link`, `variant` (`MAIN`, `PT01`, …) |
| Flattened URL list | Yes in `media.json` — `images_flat` |
| Size tokens in URL | Yes — `_SX38_SY50_`, `_SL1500_`, unsized `I/{id}.jpg` |
| Play-icon overlay thumbs | Yes — `media.json` |
| Explicit `width` / `height` on **image** objects | **No** in our fixtures (only `link`, `variant`) |
| A+ images | **No** in fixtures |
| Variant (color/style) images as a separate gallery | Not as a dedicated array; parent `images[]` + `variants[]` ASINs |

**Documentation (not in our image objects):** official sample `images[]` also uses `link` + `variant` only. Video objects in the **docs** sample include `width`/`height`; our `media.json` videos do **not** include width/height.

### What Product stores

| Rainforest | Product |
|------------|---------|
| `image.link` (best sibling per Amazon image ID) | `Image.url` |
| `image.variant` | `Image.variant` |
| main vs others | `Image.is_main` |
| — | `Image.alt` always `None` |

Lost during normalization:

- Alternate size URLs for the same ID (38px vs 1500px vs unsized) — **intentional**; best URL kept.
- Overlay video thumbs as stills — **intentional**; moved toward `videos`.
- A+ / brand-story images — **never ingested**.
- Pixel dimensions as structured fields — **not present** on observed image objects; URL tokens are a weak proxy only if parsed (V1 does not parse them for scoring).
- Duplicate URLs across IDs — exact URL dedupe after choosing best per ID.

V1 image score uses **`len(urls)`** only. Notes in code: “Quality was not evaluated.”

Frontend: `product-media-gallery.tsx` displays stills (`<img src=url>`) and videos. `alt` falls back to product title in the gallery, not Rainforest alt text.

---

## 8. Video audit

| Aspect | Received? | Mapped? | Discarded? | Displayed? | Used in scoring? | Sent to AI? |
|--------|-----------|---------|------------|------------|------------------|-------------|
| `product.videos[]` | Observed in `media.json`; not in `product.json` | Yes | `group_type`; docs width/height if present | Yes (gallery) | **No** | **No** |
| Title | Observed | Yes | — | Yes | No | No |
| Thumbnail | Observed | Yes | — | Yes | No | No |
| Playable URL (`link` ending `.mp4`/`.m3u8`/`.webm`/`.mov`) | Observed | Yes as `video_url` | Non-playable links ignored | Yes if URL | No | No |
| Duration | Observed | Yes | — | Available on model | No | No |
| Creator/source | **Not in our fixtures** | No | — | No | No | No |
| Overlay thumbs without `videos[]` | Observed | Thumbnail-only `ProductVideo` | Not kept as stills | Yes as Video | No | No |
| `videos_count` without `videos[]` | Documented in `docs/rainforest-provider.md` from a live amazon.in payload | **Not mapped** | Count lost | No | No | No |
| Related / `videos_additional` | Docs; not in fixtures | Explicitly not mapped | Yes | No | No | No |

**Credit note (documentation):** `include_image_block_videos=true` is a **second credit** on `type=product` for structured image-block videos (`videos_for_this_product`, related). We do **not** send it. Some `videos[]` still appear without that flag (fixture + mapper tests). Completeness of video arrays on amazon.in is **UNKNOWN** without a live call.

Videos are a **separate list** from `Product.images` (OBSERVED).

---

## 9. Bullet / SEO input audit

**Flow:** `product.feature_bullets` → mapper `_bullets()` → `Product.bullet_points` → `_score_bullets()` → AI `bullet_points` full list.

| Topic | Current behavior (code) |
|-------|-------------------------|
| Exact source | `product.feature_bullets` array of strings |
| Normalization | `str.strip()`; skip non-strings and blank strings |
| Max/min at map time | **None** (manual entry API caps 10 bullets; Rainforest path does not) |
| Order | **Preserved** |
| HTML stripped? | **No** in mapper (`_as_str` only) |
| Duplicates removed at map? | **No** |
| Full text to OpenAI? | **Yes** — entire `product.bullet_points` list, no truncation in `build_ai_listing_context` |
| Scoring ignores | Empty strings after strip (`empty_bullets_ignored` metric) |

**V1 bullet measures (exact):**

- count vs 3 / 5 (`BULLET_TARGET_COUNT = 5`)
- length vs 20 / 250 chars
- duplicate count after whitespace-normalized lowercase
- excessive caps (≥50% of alpha tokens ALL CAPS, token length > 1)
- excessive punctuation (`!` + `?` count > 2)
- **bonus** `min(8, benefit_starts * 2)` if first word is in `BENEFIT_STARTS` (`designed`, `easy`, `features`, `helps`, `ideal`, `includes`, `made`, `perfect`, `protects`, `provides`, `reduces`, `supports`)

Not measured: keyword coverage vs category, search terms, spec coverage, benefit vs feature semantics (except the prefix heuristic).

---

## 10. SEO-relevant data (from `type=product` only)

**Do not treat these as Amazon keyword-volume or search-rank signals.**

### DIRECT LISTING CONTENT

OBSERVED and mapped: title, bullets, description, brand, image URLs (not pixels), videos (mapped, unused by AI).

OBSERVED and unused by AI: variation titles/dimension values.

DOCUMENTATION, not in fixtures: A+ `body_text`, A+ image alt (`all_images[].name`) if `include_a_plus_body`; brand story copy/images; `company_description_text`; `book_description` (mapper already falls back).

### CATEGORY CONTEXT

OBSERVED: `categories_flat`, `categories[].name` + `category_id` (IDs discarded), `search_alias`, first BSR category.

Lost: full BSR list (second rank is the more specific node in the fixture), browse-node IDs.

### PRODUCT ATTRIBUTE CONTEXT

OBSERVED: variation `dimensions` name/value (mapped, AI unused).

DOCUMENTATION, not in fixtures: `specifications[]` `{name,value}`, `specifications_flat`, `ingredients`, `diet_type`, `manufacturer`, item dimensions/weight inside specs.

If those appear on a live product payload, AI could later compare specs vs bullets (coverage gaps). **Not implemented. Not observed locally.**

### COMPETITOR / SEARCH DATA — NOT AVAILABLE FROM PRODUCT CALL

`type=search` is a **separate credit** (`RainforestAmazonSearchProvider`: `type=search`, `amazon_domain`, `search_term`). Used only for competitor discovery. Search-result titles/ASINs/sponsored flags are **not** free byproducts of `type=product`.

Keyword volume, organic rank, ads, estimated sales: **not** in product call.

---

## 11. Description audit

| Source | Mapped? | Notes |
|--------|---------|--------|
| `product.description` | Yes | `_as_str` trim only |
| `product.book_description` | Fallback if description missing | |
| HTML description | No dedicated field | If Rainforest returns HTML inside `description`, it is stored as the string; mapper does **not** strip tags |
| `include_book_description_raw` | Not requested | |
| A+ body / `body_text` | Not requested / not mapped | See §6 |
| Editorial reviews | Documentation; not in fixtures; not mapped | |

V1 description score is **length-only**:

| Length | Score |
|--------|-------|
| missing | 0 |
| `< 80` | 40 |
| `< 250` | 75 |
| `250–2000` | 95 |
| `≤ 3000` | 78 |
| `> 3000` | 55 |

No semantic quality, no HTML vs A+ distinction, no “A+ present so Amazon description is thin” logic.

---

## 12. Attributes / specifications audit

**OBSERVED in fixtures:**

- Variation attributes: `variants[].dimensions[]` `{name, value}` → `Variation.attributes` (e.g. Style = Standard).
- Category names/IDs (IDs dropped).
- Title/bullets/description free text (may mention materials, but not structured).

**NOT in our product fixtures:** `attributes`, `specifications`, `technical details` as a named block, `ingredients`, `material`, `color`/`size` at product level, `manufacturer`.

**Documentation sample `specifications[]` names include:** Brand Name, Item Weight, Product Dimensions, Item model number, Color Name, Special Features, connectivity, etc., plus a `specifications_flat` string.

**Useful for later AI (not implemented):** structured spec values that never appear in bullets (coverage / differentiation). Requires confirming live amazon.in payloads actually include `specifications` on the default product call.

---

## 13. Category / BSR audit

**Category (OBSERVED):**

1. Prefer `categories_flat` (full breadcrumb string).
2. Else join `categories[].name` with ` > ` (**drops `category_id`**).
3. Else `search_alias.title`.

Normalized `Product.category` is **one string**. Tree as structured nodes is lost.

**BSR (OBSERVED):**

`_bsr()` takes **`bestsellers_rank[0]` only**. Requires `rank >= 1` and non-empty `category`.

Fixture:

1. Electronics — 32614  
2. Sports & Action Video Cameras — 161  

Product stores **Electronics / 32614**. The specific-node rank **161 is discarded**.

V1 uses BSR only as a **completeness bit** (present/absent). It does **not** score rank magnitude. AI receives the truncated first row only.

This is a documented false-precision risk if anyone later treats `Product.bsr.rank` as “how the product ranks in its selling category.”

---

## 14. Market signals audit (same `type=product` call)

These are **not** listing-copy quality. They still arrive on the product credit when Rainforest parses the PDP.

| Signal | Observed in fixtures | Mapped | V1 use |
|--------|----------------------|--------|--------|
| Rating | Yes | Yes | Completeness + 10% social-proof section |
| Review **count** | Yes (`reviews_total` / `ratings_total`) | Yes | Completeness + social proof |
| Review **text** | No in fixtures | No | — |
| BSR | Yes (2 rows) | First row only | Completeness only |
| Price (buy box) | Yes | Yes | Completeness; competitor comparison uses price |
| Availability raw | Yes | Yes | Completeness + comparison metric |
| Availability type | Yes (`in_stock`) | **No** | — |
| Seller name/id | Yes if third-party | Yes | Completeness |
| Sold by Amazon | Yes (`is_sold_by_amazon`) | **No** | Causes empty seller on Amazon-sold listings |
| FBA | Yes | `seller.is_fba` | AI sees it; score does not |
| Prime / badges | Not in our fixtures | No | — |
| `recent_sales` | Docs only | No | — |
| Rating breakdown | Docs only | No | — |

**Buy Box object observed:** `buybox_winner.{availability, fulfillment, price}` only. Docs also describe shipping, more buying choices, etc.

---

## 15. Reviews — distinction

| Data | Same `type=product` call? | Our status |
|------|---------------------------|------------|
| `product.rating` | Yes (observed) | Mapped |
| `product.ratings_total` / `reviews_total` | Yes (observed) | Mapped to one `review_count` |
| Featured `top_reviews` bodies | **Documentation: often included on product results** | **Not in our fixtures. Not mapped.** |
| Full review corpus, pagination, filters | **No** — Rainforest `type=reviews` | Not called. Do not confuse with product credit. |
| `customers_say` / summarization attributes | Extra param `include_summarization_attributes=true` (**2 credits**, docs) | Not requested |

`ProviderCapabilities.reviews=True` does **not** mean we ingest review text.

---

## 16. Offers — distinction

| Data | Same `type=product`? | Our status |
|------|----------------------|------------|
| Buy Box winner price / availability / fulfillment | Yes (observed) | Partially mapped |
| Full offer list, offer count, all sellers | **No** — `type=offers` (docs) | Not called |
| `more_buying_choices` | Docs on product results | Not in fixtures; not mapped |

---

## 17. Search — distinction

| Call | Params (code) | Used for |
|------|---------------|----------|
| `type=product` | `amazon_domain`, `asin` | Analyze ASIN, competitor **compare** (one product per competitor ASIN) |
| `type=search` | `amazon_domain`, `search_term` | Competitor **discovery** only |

Search query generation is **deterministic heuristics** (`apps/api/app/analytics/competitor_search_query.py`), **not OpenAI**.

Search cache TTL: `RAINFOREST_SEARCH_CACHE_TTL_SECONDS` default **900s**. Search mapper ignores `recent_sales` on search hits (`rainforest_search_mapper.py` comment).

Do not treat search titles, sponsored flags, or SERP position as data “already paid for” by the product call.

---

## 18. V1 deterministic score audit

**Code:** `apps/api/app/analytics/listing_rules.py`. Version string: `SCORE_VERSION = "v1"`.  
These are internal heuristics, **not Amazon policy**.

### Weights

| Section | Weight |
|---------|--------|
| Title | 20% |
| Bullets | 25% |
| Description | 15% |
| Images | 15% |
| Completeness | 15% |
| Social proof | 10% |

Overall = clamp(round(Σ score × weight)).

### Status bands (all sections)

| Score | Status |
|-------|--------|
| ≥ 85 | excellent |
| ≥ 70 | good |
| ≥ 50 | fair |
| else | poor |

### Title

Start 100. Deductions:

- chars `< 40`: −30; `< 80`: −12; `181–200`: −8; `> 200`: −22
- words `< 4`: −15; `> 30`: −10
- caps ratio ≥ 0.4 or entire title uppercase: −15
- keyword 3+ repeats: −20; else repeated significant words: −min(16, 8 × n)
- average word length ≥ 12: −6

Preferred length band (info finding, no extra points): 80–180 chars. Preferred min words: 4.

### Bullets

Start 100 if any bullets.

- `< 3` bullets: −30; `< 5`: −12
- short (`< 20`): −min(24, 8 × n)
- long (`> 250`): −min(30, 10 × n)
- duplicates: −20
- caps indexes: −min(15, 5 × n)
- punct indexes: −min(10, 5 × n)
- **bonus** +min(8, 2 × benefit-prefix count)

### Description

Fixed bands by character count (see §11). Not additive deductions from 100 except the band table.

### Images

| Count | Score (before dupes) |
|-------|----------------------|
| 0 | 0 |
| 1 | 40 |
| 2–3 | 65 |
| 4–6 | 88 |
| ≥ 7 | 95 |

Duplicate URLs: −15. Code notes quality not evaluated.

### Completeness

`round(100 * present / 12)` over `COMPLETENESS_FIELDS`. Missing fields → LOW finding `COMPLETENESS_GAPS`. No extra bonuses.

### Social proof

Not BSR. Uses rating + review_count only.

| Condition | Score |
|-----------|-------|
| both missing | 50 |
| reviews == 0 | 25 |
| rating ≥ 4.5 and reviews ≥ 500 | 95 |
| ≥ 4.3 and ≥ 100 | 86 |
| ≥ 4.0 and ≥ 50 | 74 |
| ≥ 4.0 and `< 20` | 58 |
| rating ≥ 3.5 (else) | 52 |
| rating `< 3.5` | 32 |
| reviews missing (rating only) | cap 60 |

---

## 19. Score-input provenance

| V1 score section | Input data | Rainforest field | Normalized field | Data quality concern |
|------------------|------------|------------------|------------------|----------------------|
| Title | Title string | `product.title` | `Product.title` | Length/caps ≠ semantic or SEO quality; thresholds not category-specific |
| Bullets | Bullet strings | `product.feature_bullets` | `Product.bullet_points` | Count/length ≠ persuasion; benefit-prefix is a tiny English-word list |
| Description | Description string | `product.description` or `book_description` | `Product.description` | Length ≠ content quality; A+ ignored; HTML not stripped |
| Images | URL list | `main_image` / `images` / `images_flat` | `Product.images[].url` | Count ≠ white-background / infographic / lifestyle quality; A+ images excluded |
| Completeness | 12 populated flags | mixed PDP + market + parse success | many Product fields | Mixes seller copy with rating/price/BSR/seller-parse |
| Social proof | Rating + count | `rating`, `reviews_total`/`ratings_total` | `rating`, `review_count` | Volume ≠ listing quality; no review text; not BSR |

Videos, variations, extra BSR, A+, specs: **not inputs** to V1.

---

## 20. False-precision risks (no scoring changes)

Findings from code, not opinions dressed as policy:

1. **Image URL count treated as image quality** — scorer comments say quality was not evaluated; 7+ URLs score 95.
2. **Description length treated as content quality** — 250–2000 chars → 95 with no claim checks.
3. **Bullet count treated as bullet quality** — 5 bullets of fluff can outscore 3 strong bullets; benefit-word bonus is shallow.
4. **Missing Rainforest/mapped field treated as incomplete listing** — Amazon-sold buy box → no `third_party_seller` → seller “missing”; BSR parse miss → incomplete; category miss is provider parse, not seller negligence.
5. **Review volume in overall listing score (10%)** — social proof is market outcome, not copy completeness.
6. **First BSR row without leaf category** — fixture proves the more specific rank is dropped; AI still sees the remaining row as “the” BSR.
7. **Benefit-word heuristic** — English prefix list; easy to game; not benefit substance.
8. **Title 80–180 / word 4–30** — global, not category-aware (grocery vs electronics).
9. **Completeness 12/12 can look “complete”** with weak copy if market fields are populated.
10. **AI is instructed not to recalculate scores** (`listing_intelligence.py`) so it **amplifies** deterministic false precision rather than correcting it.
11. **`videos_count` without `videos[]`** (in-repo live observation) would look like “no video” if we later scored on `Product.videos` length alone.

---

## 21. Completeness fields: seller content vs market vs provider

`COMPLETENESS_FIELDS` (exactly 12):

| Field | Classification | Why |
|-------|----------------|-----|
| `title` | **A. Seller-controlled listing content** | PDP title |
| `brand` | **A** (with caveats) | Brand on listing; also Amazon brand registry display |
| `bullet_points` | **A** | Seller bullets |
| `description` | **A** | Seller description; A+ not counted |
| `images` | **A** | Seller gallery; A+ not counted; empty if provider returned no URLs |
| `price` | **B. Market / offer signal** | Buy Box price, not copy |
| `rating` | **B** | Customer ratings |
| `review_count` | **B** | Volume |
| `bsr` | **B** (+ **C** if unparsed) | Rank is market; absence may be parse/marketplace |
| `availability` | **B** | Offer state |
| `seller` | **B** + **C** | Who holds Buy Box; **C** when Amazon-sold and mapper requires `third_party_seller` |
| `category` | **C. Taxonomy / provider** | Amazon tree; seller does not “write” browse nodes the way they write bullets |

---

## 22. Current OpenAI context

**Builder:** `apps/api/app/ai/context.py`  
**Prompt:** `apps/api/app/prompts/listing_intelligence.py` (`PROMPT_VERSION = "listing-intelligence-v1"`)  
**Transport:** `apps/api/app/ai/openai_provider.py` — `responses.parse` with **string** `system` + **string** `user` prompts only.

| Product data | Sent to OpenAI? | Truncated? | Notes |
|--------------|-----------------|------------|--------|
| title | Yes | No | Full string |
| bullets | Yes | No | Full list |
| description | Yes | No | Full string |
| images | **Count only** | n/a | `image_count`; no URLs |
| category | Yes | No | Flattened string |
| rating | Yes | No | |
| review count | Yes | No | Not review text |
| BSR | Yes | First row only | |
| A+ | No | — | Not on Product |
| attributes/specs | No | — | Variation attrs also omitted |
| videos | No | — | On Product, excluded from context |
| variations | No | — | |
| availability | Yes | No | |
| seller | Partial | — | name, is_fba, rating (always null from RF) |
| deterministic scores/findings | Yes | No | Authoritative; model told not to recalculate |

Competitive AI (`build_ai_competitive_context`) wraps the same listing context per ASIN plus comparison metrics/gaps.

**SEO/content judgment:** OpenAI can judge **title/bullet/description wording** from text. It **cannot** judge image composition, A+ modules, spec coverage (unless mentioned in copy), or video. Prompt forbids inventing keyword volume.

---

## 23. Image AI capability — current state

**CURRENT: text-only (not multimodal).**

Proof:

- `OpenAIProvider.generate_structured` sends `input=[{role: system, content: system_prompt}, {role: user, content: prompt}]` where `prompt` is a **string** (JSON blob).
- No `image_url` / input_image parts in `apps/api/app/ai/`.
- Context includes `image_count`, not URLs or bytes.

Do not implement vision in this audit.

---

## 24. API-call / cost map

No live calls were made. Counts are from code paths. “Cold cache” = empty in-memory TTL caches.

Rainforest product cache: **600s**. Search: **900s**. OpenAI listing/competitive: **2700s** (`AI_CACHE_TTL_SECONDS`), keyed on context + model + prompt version.

Known mock ASINs `B0TEST0001`–`B0TEST0003` short-circuit to mock catalog (**0** Rainforest).

Bulk Due Diligence defaults: `bulk_product_provider=mock`, `bulk_ai_provider=mock`, live RF forbidden unless guard enabled — **0** paid Rainforest/OpenAI in the default bulk path.

Account dashboard `GET /account` is documented **free** (not a product credit).

| User action | Rainforest calls (cold cache) | OpenAI calls (cold cache) | Cache behavior |
|-------------|-------------------------------|---------------------------|----------------|
| Analyze ASIN (`GET /products/{asin}`) | **1 × `type=product`** | 0 | Product mapped object cached 600s |
| Analyze Listing (`POST /analysis/listing`) | **0** | 0 | Uses Product already in the request body |
| Generate AI Strategy (listing intelligence) | **0** | **1** | AI result cached 2700s; identical context is a ledger cache hit |
| Discover competitors | **1 × `type=search`** | 0 | Search cached 900s; query is heuristic, not AI |
| Competitor comparison (1–3 ASINs) | **1 `type=product` per competitor ASIN** (max 3). Target is **not** re-fetched | 0 | Each ASIN uses product cache if still warm |
| Generate AI competitive insights | **0** extra RF | **1** | Separate AI cache from listing intelligence |
| Usage dashboard account | 0 product/search credits | 0 (OpenAI admin spend is a different key/endpoint) | Account JSON cached ~60s |
| Manual product | 0 | 0 | User-entered fields |

Typical UI sequence (live ASIN, cold everything, then compare 3 competitors + both AIs):

`1 product + 1 search + 3 product + 2 OpenAI` = **5 Rainforest credits + 2 OpenAI** if no overlap with product cache. If compare happens within 10 minutes of Analyze ASIN, competitor ASINs still cost extra; **target** does not.

---

## 25. Same-call opportunities (no extra Rainforest type)

Prioritized by evidence. **No V2 weights.**

### P0 — highly valuable (strong local evidence and/or already on Product unused)

1. **Keep all `bestsellers_rank` rows** (observed two-row fixture; mapper drops all but first). Leaf-category rank is the useful one.
2. **Preserve category tree + `category_id`** (observed).
3. **Use already-mapped videos** in AI context and as listing-content presence (not as “quality”) — data is already paid for when `videos[]` is present.
4. **Send variation titles/attributes to AI** — already on Product; currently omitted from OpenAI.
5. **Do not treat Amazon-sold listings as missing seller** — observed `is_sold_by_amazon`; completeness currently penalizes them.
6. **Stop mixing market signals into “listing completeness”** conceptually for V2 inputs (classification only; no new formula here).

### P1 — valuable if live amazon.in payload matches Rainforest’s default schema (UNKNOWN locally; documented)

1. **`a_plus_content.has_a_plus_content` / `has_brand_story` / brand-story image URLs** — same `type=product`, no extra param in the official sample.
2. **`specifications` / `specifications_flat`** — attribute coverage vs bullets for AI.
3. **`top_reviews` featured text** — not full Reviews API; still not a substitute for review intelligence.
4. **`recent_sales`**, **`rating_breakdown`** — market context, not copy quality.
5. **`videos_count`** when `videos` is empty — avoid false “no video”.
6. **`include_a_plus_body=true` on the same type** — A+ `body_text` + image alts; **credit cost not listed as extra** in official parameters (verify before assuming it is free). This is still `type=product`, not Reviews/Offers/Search.

### P2 — nice-to-have / extra credits on the same endpoint

1. **`include_image_block_videos=true`** — official **2 credits**. Only if default `videos[]` is too thin (in-repo note: count without array).
2. Image pixel metadata — **not observed** on image objects; URL size tokens could be parsed without another call (weak signal).
3. `include_summarization_attributes` — **2 credits**; review themes, not listing copy.

---

## 26. Features requiring extra API calls (other types or paid flags)

| Desired intelligence | Existing default product call enough? | Additional Rainforest API required? | API type / flag |
|----------------------|----------------------------------------|-------------------------------------|-----------------|
| Customer review corpus / sentiment at scale | No (maybe a few `top_reviews` if present — UNKNOWN locally) | Yes for real coverage | `type=reviews` |
| Review themes (`customers_say`) | No | Optional extra credit on product | `include_summarization_attributes=true` (2 credits, docs) |
| Full Buy Box / offer depth / all sellers | Partial (winner only) | Yes | `type=offers` |
| Search discovery / SERP / sponsored | No | Yes | `type=search` (already used for competitors) |
| Category research / keyword volume | No | Yes / other products | not in product PDP |
| Sales estimation | No (`recent_sales` is a coarse PDP string if present) | Yes / Keepa-style / other | not Rainforest product |
| Stock estimation | No (availability raw/type only) | Often yes | not reliable from PDP |
| Deep image-block / related videos | Sometimes `videos[]` already | Often yes | `include_image_block_videos=true` (2 credits) |
| A+ HTML + all A+ images/alts | Presence/brand story: maybe default (UNKNOWN). Full body: extra **parameter** | Same `type=product` + `include_a_plus_body` | not a different `type` |
| Competitor listing compare | No — each ASIN is another product credit | Yes (already implemented) | `type=product` per ASIN |

---

## 27. Recommended V2 **input signals** (no weights, no thresholds)

Candidate inputs only, for later scoring design:

**From data we already map:** title, bullets, description, image URLs/count, videos (presence/duration/title), category string, all BSR rows (after mapping fix), rating, review_count, price, availability, seller/FBA, variation attributes.

**From observed-but-discarded fields:** extra BSR, category IDs, `availability.type`, `is_sold_by_amazon`.

**From documented default product JSON (verify on one amazon.in fixture later):** A+ flags, brand story, specifications, ingredients, recent_sales, rating_breakdown, top_reviews, videos_count.

**Explicitly not V2 listing-copy inputs without a product decision:** review volume as “listing quality,” first-row BSR as category rank, image count as photography quality.

---

## 28. Unknowns requiring verification

Do **not** spend production credits until this audit is reviewed. When verified, capture a **redacted fixture** in-repo.

1. Does default `type=product` on **amazon.in** include `a_plus_content` without `include_a_plus_body`?
2. Does it include `specifications` / `ingredients` for grocery vs electronics?
3. Does it include `top_reviews`?
4. How often is `videos[]` populated vs `videos_count` only?
5. Does `include_a_plus_body=true` consume **1 or >1** credit on current Traject billing?
6. Are `all_images` alts reliable on amazon.in?
7. Image objects: any live payloads with numeric `width`/`height`?
8. Does Amazon-sold buy box omit `third_party_seller` on amazon.in as in the mapper’s implicit assumption?

---

## Master signal matrix

| Signal | Rainforest product call | In Product model | V1 uses it | AI receives it | Potential V2 value |
|--------|-------------------------|------------------|------------|----------------|--------------------|
| Title | Yes (observed) | Yes | Yes | Yes | High (already used; V2 should stay semantic, not just length) |
| Bullets | Yes (`feature_bullets`) | Yes | Yes | Yes (full text) | High |
| Description | Yes | Yes | Length only | Yes | High (plus A+ if verified) |
| Images (URLs) | Yes | Yes (url, variant, is_main) | Count only | Count only | High for presence; quality needs vision or heuristics later |
| Image dimensions | Not in our image objects | No | No | No | P2 / UNKNOWN |
| Image alt | Not on gallery objects; A+ alts need extra param (docs) | Field unused | No | No | P1 if A+ `all_images` |
| Videos | Sometimes (fixture + live note) | Yes when present | **No** | **No** | **P0** (already paid when present) |
| A+ Content presence | Docs yes; fixtures no | No | No | No | **P1** pending live verify |
| A+ body / modules / alts | Extra `include_a_plus_body` (docs) | No | No | No | P1; confirm credit |
| Brand Story | Docs in `a_plus_content`; fixtures no | No | No | No | P1 pending verify |
| Attributes / specifications | Docs; fixtures no (except variation dims) | Variation attrs only | No | No | **P1** if present on PDP |
| Category tree | Partial (flat + names; IDs dropped) | One string | Completeness | String only | **P0** to keep tree/IDs |
| BSR context | Yes (multiple rows observed) | **First row only** | Completeness | Truncated | **P0** |
| Rating | Yes | Yes | Completeness + social | Yes | Market signal, not copy |
| Review count | Yes | Yes | Completeness + social | Yes | Market signal |
| Review text | Docs `top_reviews` maybe; full = `type=reviews` | No | No | No | Extra API for corpus |
| Price / availability / seller | Buy box subset | Partial | Completeness | Partial | Market / offer |
| Variations | Yes | Yes | No | **No** | **P0** as AI context |

---

## Final recommendation (three groups)

### USE MORE FROM EXISTING PRODUCT CALL

- Map and retain **all BSR rows** and **category IDs/tree**.
- Feed **videos** and **variation attributes** into AI (already on `Product` when Rainforest sent them).
- Treat **Amazon-sold** vs 3P seller correctly (`is_sold_by_amazon`).
- If live payloads include them (verify once): **A+ flags, brand story, specifications, videos_count, featured top_reviews, recent_sales**.
- Keep using title/bullets/description text — already the strongest SEO/content inputs we have **without** another credit.
- Optional same-`type` flag: `include_a_plus_body` for A+ text/alts after confirming **credit cost**.

### REQUIRES OPTIONAL EXTRA RAINFOREST CALL

- **`type=reviews`** — review intelligence / sentiment at scale.
- **`type=offers`** — offer depth beyond buy-box winner.
- **`type=search`** — discovery/SERP (already used for competitors; not free with product).
- **`include_image_block_videos=true`** — 2 credits (docs) for deeper video scrape.
- **`include_summarization_attributes=true`** — 2 credits (docs).
- **Another `type=product` per competitor ASIN** — already how comparison works.

### NOT CURRENTLY SUPPORTED / NEEDS OTHER DATA SOURCE

- Keyword **search volume**, true organic rank, ads, TACOS/ACOS, conversion, inventory depth, Keepa-style sales estimates.
- **Multimodal image quality** (current AI is text-only).
- Amazon **policy** compliance scoring (V1 is internal heuristics).
- Full A+ module structure beyond what Rainforest parses (even with `include_a_plus_body`, we have not observed the payload locally).

---

## Appendix A — Fixture inventory

Files under `apps/api/tests/fixtures/rainforest/`:

| File | Role | Product keys (if any) |
|------|------|------------------------|
| `product.json` | Happy-path product | title, search_alias, asin, brand, variants, categories, categories_flat, rating, ratings_total, reviews_total, main_image, images, feature_bullets, description, buybox_winner, bestsellers_rank |
| `media.json` | Image/video mapping | title, asin, main_image, images, images_flat, videos |
| `missing_fields.json` | Sparse product | title, asin |
| `search.json` | `type=search` | (search_results, not product) |
| `account.json` | Account API | — |
| `invalid_key.json` / `not_found.json` | Errors | — |

**Not represented in product fixtures:** `a_plus_content`, `specifications`, `ingredients`, `top_reviews`, `offers`, `more_buying_choices`, `recent_sales`, `videos_count`, `images_count`, image `width`/`height`.

## Appendix B — Files inspected (application)

- `apps/api/app/providers/rainforest.py`
- `apps/api/app/parsers/rainforest_product_mapper.py`
- `apps/api/app/parsers/amazon_media.py`
- `apps/api/app/models/product.py`
- `apps/api/app/analytics/listing_rules.py`
- `apps/api/app/analytics/competitor_rules.py`
- `apps/api/app/analytics/competitor_search_query.py`
- `apps/api/app/services/listing_analysis_service.py`
- `apps/api/app/services/product_service.py`
- `apps/api/app/services/ai_listing_intelligence_service.py`
- `apps/api/app/services/competitor_comparison_service.py`
- `apps/api/app/services/competitor_discovery_service.py`
- `apps/api/app/ai/context.py`
- `apps/api/app/ai/competitive_context.py`
- `apps/api/app/ai/openai_provider.py`
- `apps/api/app/prompts/listing_intelligence.py`
- `apps/api/app/search/rainforest_search_provider.py`
- `apps/api/app/core/config.py`
- `apps/api/tests/test_rainforest.py` + fixtures listed above
- `apps/web/src/lib/types.ts`, `apps/web/src/components/product-media-gallery.tsx`
- In-repo docs: `docs/rainforest-provider.md`, `docs/listing-intelligence.md` (code treated as source of truth where they differ)

No production application code was modified for this audit. No live Rainforest or OpenAI requests were made.

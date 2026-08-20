# Listing Intelligence V2 — Data Foundation (Milestone 8A)

**Status:** implemented. This milestone is a **normalized data foundation only**.

It does **not** introduce Listing Score V2, AI context changes, extra Rainforest request parameters, Reviews API, Offers API, or image vision.

Related audit: [LISTING-INTELLIGENCE-V2-DATA-AUDIT.md](LISTING-INTELLIGENCE-V2-DATA-AUDIT.md).

---

## 1. Purpose

Keep the existing Rainforest `type=product` credit, but stop discarding useful fields during `map_rainforest_product()`.

```text
Existing Rainforest type=product call
        ↓
Richer normalized Product
        ↓
V1 deterministic analysis UNCHANGED
        ↓
OpenAI listing intelligence UNCHANGED
```

New fields sit **alongside** legacy Product fields. Consumers that ignore unknown JSON keys continue to work.

---

## 2. Product fields added

| Product field | Type |
|---------------|------|
| `bsr_ranks` | `list[BSR]` |
| `category_path` | `list[CategoryNode]` |
| `is_sold_by_amazon` | `bool \| None` |
| `availability_type` | `str \| None` |
| `variations[].is_current_product` | `bool \| None` |
| `videos[].group_type`, `group_id`, `width`, `height` | optional |
| `videos_count` | `int \| None` |
| `images[].width`, `images[].height` | optional ints |
| `a_plus` | `APlusContent \| None` |
| `specifications` | `list[ProductSpecification]` |
| `specifications_flat` | `str \| None` |
| `attributes` | `ProductAttributes \| None` |
| `rating_breakdown` | `RatingBreakdown \| None` |
| `featured_reviews` | `list[FeaturedReview]` |
| `recent_sales_text` | `str \| None` |

Legacy fields (`title`, `bsr`, `category`, `availability`, `seller`, `images`, `videos`, …) are unchanged.

---

## 3. Source Rainforest JSON paths

| Normalized field | Rainforest path |
|------------------|-----------------|
| `bsr` (legacy) | `product.bestsellers_rank[0]` |
| `bsr_ranks` | `product.bestsellers_rank[]` |
| `category` (legacy) | `categories_flat`, else joined `categories[].name`, else `search_alias.title` |
| `category_path` | `product.categories[]` `{name, category_id}` |
| `is_sold_by_amazon` | `buybox_winner.fulfillment.is_sold_by_amazon` |
| `seller` | `buybox_winner.fulfillment.third_party_seller` only (no invented “Amazon” name) |
| `availability` | `buybox_winner.availability.raw` |
| `availability_type` | `buybox_winner.availability.type` |
| `variations[].is_current_product` | `variants[].is_current_product` |
| `videos_count` | `product.videos_count` |
| `a_plus` | `product.a_plus_content` |
| `specifications` | `product.specifications[]` |
| `attributes.manufacturer` | `product.manufacturer` |
| `attributes.ingredients` | `product.ingredients` |
| `attributes.diet_type` | `product.diet_type` |
| `attributes.listed` | `product.attributes[]` |
| `rating_breakdown` | `product.rating_breakdown` |
| `featured_reviews` | `product.top_reviews` |
| `recent_sales_text` | `product.recent_sales` |

HTML bodies (`a_plus_content.body`, `top_reviews[].body_html`) are **not** stored.

---

## 4. Observed locally vs documentation-only

**OBSERVED IN OUR EXISTING FIXTURES** (`product.json`, `media.json`, `missing_fields.json`, `amazon_sold.json`):

- multiple `bestsellers_rank` rows
- `categories[]` with `category_id`
- `buybox_winner.availability.type` + `raw`
- `fulfillment.is_sold_by_amazon` with and without `third_party_seller`
- `variants[].is_current_product` and dimension name/value pairs
- `videos[]` with title, thumbnail, playable link, duration, `group_type` (media fixture)

**SUPPORTED BY RAINFOREST DOCUMENTATION BUT NOT YET OBSERVED LOCALLY** (covered by `docs_only_enrichment.json` and `videos_count_only.json` for mapper tests only):

- `a_plus_content` (presence, brand story, `body_text`, `all_images`)
- `specifications` / `specifications_flat`
- `manufacturer` / `ingredients` / `diet_type` / title `attributes`
- `rating_breakdown`
- `top_reviews`
- `recent_sales`
- `videos_count` without `videos[]`
- explicit image `width`/`height`
- video `width`/`height`/`group_id` (added to `media.json` for mapper coverage; not claimed as observed on the original media fixture)

These documentation-only fixtures are **not** evidence that amazon.in always returns those keys on a default product call.

---

## 5. Optional field behavior

If Rainforest omits a key, the mapper stores `null` or `[]`. Sparse products (`missing_fields.json`) remain valid. The mapper does not invent browse nodes, seller names, pixel sizes, sales counts, or A+ flags.

---

## 6. Backward compatibility

Existing Product field names were not removed or renamed. Mock, manual, amazon_public, bulk, listing analysis, competitor, and AI request bodies still construct `Product` with the original required fields; new fields default empty.

Frontend types gained optional properties. The UI was not redesigned and does not have to render the new fields.

---

## 7. BSR preservation

`Product.bsr` is still **only the first** `bestsellers_rank` row, including the previous invalid-first-row behavior.

`Product.bsr_ranks` keeps every valid `{rank, category}` row in order.

Example from `product.json`:

- `bsr`: Electronics / 32614
- `bsr_ranks`: Electronics / 32614 **and** Sports & Action Video Cameras / 161

V1 scoring still uses presence of `bsr` only.

---

## 8. Category tree preservation

`Product.category` remains the flattened string (`categories_flat` preferred).

`Product.category_path` is the structured `categories[]` list. IDs are stored only when Rainforest provides them. The fixture’s `categories[]` can be shorter than `categories_flat`; missing nodes are **not** inferred.

---

## 9. Amazon-sold handling

`Product.is_sold_by_amazon` is the explicit boolean from Rainforest, or `null` if absent.

Amazon-sold listings with no `third_party_seller` still have `seller = null`. That is **not** rewritten to `seller.name = "Amazon"`. Future V2 can distinguish:

- seller unavailable (`seller is null` and `is_sold_by_amazon is null`)
- Amazon is the seller (`is_sold_by_amazon is true`, `seller` may still be null)
- third-party seller (`seller` populated; `is_sold_by_amazon` may be false)

V1 completeness still uses `seller` only.

---

## 10. Availability structure

`availability` remains the raw display string. `availability_type` stores `in_stock` and similar tokens when present.

---

## 11. Variation enrichment

`asin`, `label`, and `attributes` (dimension name/value map) are unchanged. `is_current_product` is preserved when it is a boolean. Child ASINs and variant prices are not fetched.

---

## 12. Video enrichment

Existing video mapping still extracts playable URLs and overlay thumbs. Optional `group_type`, `group_id`, `width`, and `height` are stored when present.

`videos_count` is stored only if Rainforest sends it. A non-empty `videos[]` does **not** fill `videos_count`. A positive `videos_count` with empty `videos[]` means “provider reported videos exist; detailed objects were not returned.”

`include_image_block_videos` is not sent (extra credits).

---

## 13. Image metadata

Gallery URL selection is unchanged. Explicit `width`/`height` on the **chosen** image URL object are stored. Dimensions are not parsed from CDN size tokens. A+ images are not mixed into `Product.images`.

---

## 14. A+ representation

`Product.a_plus` is optional. Mapped when `product.a_plus_content` is an object:

- `has_a_plus_content`, `has_brand_story`, `third_party`
- `company_logo`, `company_description` (`company_description_text`)
- `body_text` if already present (no `include_a_plus_body` request)
- `all_images` → `a_plus.images` `{url, alt}` from `{link, name}`
- `brand_story` hero/logo/description/image URLs

A+ is not scored and not sent to OpenAI. Presence is not quality.

---

## 15. Specifications

`specifications` is a list of `{name, value}` from Rainforest’s specifications table. `specifications_flat` is the optional concatenated string. Values are not derived from title or bullets.

---

## 16. Rating breakdown

Star bands `five_star` … `one_star` with optional `percentage` and `count`. This is market-signal context, not V1 social-proof input.

---

## 17. Featured reviews

`featured_reviews` maps `top_reviews` on the **product** payload: id, title, body (plain text), rating, profile name, verified purchase, date raw/utc.

This is **not** a review corpus and **not** `type=reviews`.

---

## 18. Recent-sales semantics

`recent_sales_text` stores Amazon’s descriptive string (for example `"50+ bought in past month"`). It is not parsed into an integer sales figure.

---

## 19. Provider capability correction

`ProviderCapabilities.reviews` now means **review corpus**. Rainforest and amazon_public product providers set `reviews=False`. `ratings=True` covers star rating and review count.

Featured product-page reviews do not set `reviews=True`.

---

## 20. API credit implications

Cold Analyze ASIN is still **one** `type=product` request with parameters:

`api_key`, `type`, `amazon_domain`, `asin`

Not sent: `include_a_plus_body`, `include_image_block_videos`, `include_summarization_attributes`, `variant_prices`, `type=reviews`, `type=offers`.

The cache still stores mapped `Product` only. Raw provider JSON is **not** retained (complexity vs. value). Unmapped keys on a live payload cannot be recovered without another credit.

---

## 21. Fields intentionally not used by V1

All new foundation fields. Weights remain 20/25/15/15/15/10. Completeness still uses the original 12 fields. Social proof still uses rating + review_count only.

---

## 22. Fields intentionally not sent to OpenAI

A+, image URLs, videos / `videos_count`, specifications, attributes, category tree, extra BSR rows, featured reviews, recent sales, rating breakdown, `is_sold_by_amazon`, `availability_type`.

`build_ai_listing_context` and `listing-intelligence-v1` prompts are unchanged.

---

## 23. Remaining unknowns (live amazon.in)

Do not spend production credits until product review:

1. Whether default amazon.in product JSON includes `a_plus_content` without `include_a_plus_body`.
2. Whether `specifications`, `top_reviews`, `recent_sales`, and `rating_breakdown` appear on typical IN listings.
3. How often `videos_count` appears without `videos[]`.
4. Whether image objects include numeric width/height.
5. Billing of `include_a_plus_body` if that flag is considered later.

---

## Cache / raw snapshot decision

A sanitized raw snapshot was **not** implemented. Richer normalization is the recovery path for known fields. Limitation: anything still unmapped on a live response is lost after mapping.

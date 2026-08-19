# Rainforest product provider

V1’s primary real-ASIN catalog source. It calls the Rainforest Product Data API and maps the result onto the existing normalized `Product` model.

The rest of the application never sees Rainforest JSON.

## How it works

```text
GET /api/v1/products/{asin}
  → ProductService
    → RainforestProductDataProvider
      → GET https://api.rainforestapi.com/request
      → map_rainforest_product()
        → Product
```

Query parameters (passed via httpx `params`, never concatenated by hand):

```text
type=product
amazon_domain=<marketplace>
asin=<ASIN>
api_key=<backend secret>
```

`amazon_domain` is the existing marketplace identifier (`amazon.in` in V1). It is not hardcoded in the provider.

Provenance is `meta.source = "rainforest"`. It is not stored on `Product`.

## Configuration

Set these in `apps/api/.env` (backend only):

```text
PRODUCT_PROVIDER=rainforest
RAINFOREST_API_KEY=
```

Do **not** put the key in Next.js, `NEXT_PUBLIC_*`, source code, tests, or git.

Optional:

```text
RAINFOREST_TIMEOUT_SECONDS=60
RAINFOREST_CACHE_TTL_SECONDS=600
```

If `RAINFOREST_API_KEY` is missing, a real-ASIN lookup returns **503** with a configuration error. Health checks and Quick Demo mock ASINs still work.

Pytest always forces `PRODUCT_PROVIDER=mock` and uses fixture JSON. The suite never calls Rainforest and never contains a real key.

## Account API (usage dashboard)

`GET https://api.rainforestapi.com/account` is called only by the backend usage dashboard. Rainforest documents this endpoint as **free**. It is not counted as a product or search call in the application ledger.

The dashboard maps `credits_used`, `credits_limit`, `credits_remaining`, `credits_reset_at`, and a short `usage_history`. Account email, name, plan, and `api_key` are stripped before the JSON leaves the API. See [api-usage-dashboard.md](api-usage-dashboard.md).

Known mock ASINs (`B0TEST0001`–`B0TEST0003`) still resolve from the mock catalog.

## Mapping

Mapped from documented Rainforest product fields when present:

| Product field | Rainforest source |
|---------------|-------------------|
| title | `product.title` |
| brand | `product.brand` |
| price | `product.buybox_winner.price` |
| rating | `product.rating` |
| review_count | `product.reviews_total`, else `product.ratings_total` |
| bullet_points | `product.feature_bullets` |
| description | `product.description`, else `product.book_description` |
| images | `product.main_image.link` first, then `product.images[].link` / `images_flat`, choosing the highest-quality URL per Amazon image ID |
| videos | `product.videos[]` when present; video play-icon overlay thumbs are not stored as stills |
| category | `product.categories_flat` |
| bsr | first `product.bestsellers_rank` entry |
| availability | `product.buybox_winner.availability.raw` |
| seller | `product.buybox_winner.fulfillment.third_party_seller` |
| variations | `product.variants` |

Missing values stay `null` or `[]`. Nothing is invented.

## Media mapping

Rainforest documents:

- `main_image.link` — primary image on the product page
- `images[]` with `link` and optional `variant` (`MAIN`, `PT01`, …) — additional gallery images, excluding the main image
- `images_flat` — comma-separated image URLs
- `videos[]` — image-block videos (`title`, `thumbnail`, `link`, `duration_seconds`, `group_type`) when Amazon/Rainforest supplies them

Observed on a live amazon.in product (`B09G9BL5CP`): `main_image.link` was an unsized `I/{id}.jpg` at 2560×2560. `images[].link` is sometimes `_SL1500_` (1500px) and can also be `_SX38_SY50_` 38×50 gallery thumbs, including a `PKdp-play-icon-overlay` video thumb. `videos_count` may be present while `videos` is omitted unless a deeper video scrape ran.

### Still images

1. Collect URLs from `main_image`, `images[]`, and `images_flat`.
2. Drop video overlay thumbs (`play-icon-overlay`) and any URL whose Amazon image ID matches a `videos[].thumbnail`.
3. Group remaining URLs by Amazon `/images/I/{id}`.
4. For each ID, choose the best **already-known** sibling. When the host is Amazon’s media CDN, the unsized `https://m.media-amazon.com/images/I/{id}.{ext}` form (the same shape Rainforest uses for `main_image`) is included as a candidate. It was verified larger than `_SL1500_` and `_SX38_`. Original URLs are not rewritten into arbitrary `._SX*` sizes.
5. Put the main image first. Exact duplicate URLs are removed.

`Product.images[0]` is the main still. `Image.variant` and `Image.is_main` are optional extras; older clients can ignore them.

### Videos

`Product.videos` is a separate list (`title`, `thumbnail_url`, `video_url`, `duration_seconds`). `video_url` is set only when Rainforest `videos[].link` is a playable media URL (`.mp4`, `.m3u8`, …). Overlay thumbs without a playable URL become videos with a thumbnail only. Listing Intelligence `image_count` uses stills only.

The provider does **not** send `include_image_block_videos=true` (extra Rainforest credits). If Amazon only exposes a video count/thumbnail, the UI labels it Video and does not invent a stream.

### Frontend

The gallery uses a main viewer plus thumbnails (`object-contain`, muted background). Standard `<img>` is used rather than `next/image` because Amazon CDN hostnames vary by listing; there is no unrestricted `hostname: "**"` config.

### Limitations

- If Rainforest only returns a 38px thumb and the unsized sibling is missing, the UI shows that thumb at a contained size instead of stretching it.
- A+ / brand-story images are not mapped into `Product.images`.
- Related-product videos (`videos_additional`) are not mapped.

## Errors

| Situation | HTTP |
|-----------|------|
| Invalid ASIN | 400 |
| Product not found | 404 |
| Missing / invalid API key | 503 |
| Rate limit / credits / Rainforest 503 | 503 |
| Timeout, unexpected status, parse failure | 502 |

Use **Manual Product** as a fallback when lookup fails.

## Other providers

```text
PRODUCT_PROVIDER=mock
PRODUCT_PROVIDER=amazon_public
```

`amazon_public` remains experimental HTML retrieval. Production Amazon access later is still SP-API behind the same interface.

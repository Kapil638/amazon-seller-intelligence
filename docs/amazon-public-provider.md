# Amazon.in public product provider (experimental)

This provider is **prototyping infrastructure**. It is not an Amazon-supported API.

It fetches the public product page:

```text
https://www.amazon.in/dp/{ASIN}
```

then maps HTML into the existing normalized `Product` model.

## How it works

```text
GET /api/v1/products/{asin}
  → ProductService
    → AmazonPublicProductDataProvider
      → httpx GET
      → AmazonProductParser (JSON-LD, then DOM selectors)
        → Product
```

Parser code is isolated from HTTP. Selectors live in `apps/api/app/parsers/amazon_product_parser.py`.

Provenance is `meta.source = "amazon_public"`. It is not stored on `Product`.

## Configuration

```bash
# apps/api/.env
PRODUCT_PROVIDER=amazon_public
```

Other values:

```text
PRODUCT_PROVIDER=mock
```

Optional:

```text
AMAZON_PUBLIC_TIMEOUT_SECONDS=12
AMAZON_PUBLIC_CACHE_TTL_SECONDS=600
```

Pytest always forces `mock` so the automated suite never calls amazon.in.

Known mock ASINs (`B0TEST0001`–`B0TEST0003`) still resolve from the mock catalog even when the public provider is selected. That keeps Quick Demo offline.

## Known limitations

- Amazon HTML and anti-bot behaviour change without notice.
- Simple HTTP requests are often blocked, throttled, or served a CAPTCHA.
- Not every field is present. Missing values stay `null` or `[]`.
- Images are URL lists only. They are not downloaded or quality-checked.
- This is not suitable as the long-term production provider.

Expected failures:

| Situation | HTTP |
|-----------|------|
| Invalid ASIN | 400 |
| Product not found | 404 |
| Blocked / CAPTCHA / throttled | 503 |
| Timeout, unexpected status, parse failure | 502 |

Use **Manual Product** as a fallback when lookup fails.

## Future replacement

A paid structured-data provider (Rainforest) now sits behind the same `ProductDataProvider` interface. See [rainforest-provider.md](rainforest-provider.md). That is the V1 default.

Production Amazon access later remains `AmazonOfficialProductDataProvider` via SP-API.

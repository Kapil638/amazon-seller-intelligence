# Competitor Discovery (discovery version v1)

This feature **suggests Amazon.in search results**. It does not decide who the competitors are.

The seller still confirms up to three candidate ASINs. Those ASINs then enter the existing Milestone 7 comparison flow.

Prompt/AI is **not** used here.

## Architecture

```text
Target Product
  → CompetitorSearchQueryService
    → search query (generated or seller-edited)
      → AmazonSearchProvider
        → RainforestAmazonSearchProvider   [V1]
          → Rainforest type=search
          → map_rainforest_search()
            → AmazonSearchHit snippets
      → filter target ASIN
      → dedupe ASINs
      → relevance score
      → top 12 candidates
        → seller selects up to 3
          → existing POST /api/v1/analysis/competitors
```

```text
AmazonSearchProvider
    ├── RainforestAmazonSearchProvider     current
    ├── MockAmazonSearchProvider           Quick Demo
    └── AmazonOfficialSearchProvider       future
```

Product lookup stays on `ProductDataProvider`. Search is a separate capability. `RainforestProductDataProvider.get_product()` is unchanged.

## Search provider

Rainforest Product Data API, one request:

```text
type=search
amazon_domain=amazon.in
search_term=<query>
api_key=<backend secret>
```

No `page` parameter. No pagination loop. Response `search_results[]` is mapped from documented fields only: `asin`, `title`, `brand` (if present), `price.value` / `price.currency`, `rating`, `ratings_total`, `image`, `position`, `sponsored`, `categories`.

`recent_sales` is ignored. It is not sales evidence for this product.

## Query-generation rules (query version v1)

From the target title (and category if the title is too thin):

- lowercase and strip punctuation
- remove the target brand tokens
- drop stopwords (`the`, `and`, `for`, …)
- drop promotional words (`sale`, `official`, `combo`, …)
- drop pack/size tokens (`1kg`, `60`, `count`, `bottle`, …)
- keep unique tokens in order
- cap at 6 tokens and 80 characters

Example: title `Brand X Whey Protein Powder Chocolate 1kg` → `whey protein powder chocolate`.

The generated query is always returned in the discovery payload.

## Seller override

`POST /api/v1/competitors/query` returns the generated query without calling Rainforest.

`POST /api/v1/competitors/discover` accepts `search_query`. If `null`, the generated query is used. If a string is supplied, it is validated (2–80 characters after trim) and used as-is. Whitespace-only or oversized queries return 400.

## Relevance scoring (relevance version v1)

This is **search-result relevance to the target listing**, not market competitiveness.

| Signal | Weight |
|--------|--------|
| Title token Jaccard overlap | 50 |
| Core query-token overlap in the candidate title | 30 |
| Category match when both sides have a usable category | 15 |
| Different known brand (possible substitute) | 5 |

Score is rounded and clamped to 0–100. Sponsored status does not change the score.

Low scores are not auto-rejected. Candidates without ASIN or title are skipped because they cannot be selected.

## Filtering and variants

- Target ASIN is removed.
- Duplicate ASINs keep the first occurrence.
- Same-brand results are kept. They may be variants or sibling listings.
- Parent/variation collapsing is not implemented unless identity is an exact ASIN match.

## Sponsored results

If Rainforest sets `sponsored`, the candidate has `is_sponsored`. The UI shows a **Sponsored** badge. This is not ad spend or campaign strength.

## Search position

`position` is the **observed search-result position for this query**. It is not organic ranking, traffic, or keyword rank. Sponsored rows also have a position.

## Cost controls

- One Rainforest search call per Discover/Search Amazon action
- No automatic second query or keyword expansion
- No pagination
- No full `type=product` fetch for candidates
- Full Product fetch happens only after the seller selects up to 3 ASINs (existing comparison)
- In-memory cache key: `rainforest-search|{provider}|{marketplace}|{normalized query}`
- Default TTL: 15 minutes (`RAINFOREST_SEARCH_CACHE_TTL_SECONDS=900`)

## Evidence limitations

Valid:

- This listing appeared at position 4 for the search “whey protein powder”.
- This candidate has high title/category similarity to the target listing.

Invalid:

- This is your fourth-largest competitor.
- This is the market leader.
- This listing has stronger sales.

## Manual seller confirmation

Discovery labels results as **suggested / candidate** listings. Compare Selected reuses Milestone 7 comparison unchanged.

## Future

SP-API or other structured search can implement `AmazonSearchProvider` without changing the discovery service or UI.

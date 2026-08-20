# Image & Media Intelligence V1

Milestone 8D adds **optional multimodal visual intelligence** on top of the normalized `Product` already fetched for Listing Intelligence V2.

Prompt version: **`image-intelligence-v1`**.

This layer answers: *How effectively does the listing visually communicate the product using the image evidence actually available?*

It does **not** answer conversion, CTR, sales, ranking, or Amazon policy compliance. It does **not** generate replacement images. It does **not** change `listing_quality_score`.

## Architecture

```text
Normalized Product
  → MediaUrlValidator
  → deterministic MediaEvidenceBuilder / selector
  → AIImageIntelligenceService
    → AIProvider.generate_multimodal_structured()
      → OpenAIProvider (Responses API, image URLs + structured output)
        → AIImageIntelligence
```

Listing Intelligence V1/V2 and AI listing V1/V2 are unchanged:

```text
Product → ListingAnalysisV2 → listing_quality_score
Product + ListingAnalysisV2 → AIListingIntelligenceV2Service → listing-intelligence-v2
```

Vision is a **separate report**. Future milestones may decide whether visual signals belong in a combined score.

## Explicit user action

Image analysis never runs during Analyze ASIN, Listing Intelligence, Generate AI Strategy, or competitor flows.

Only **Analyze Images & Media** may start the multimodal OpenAI request.

## Rainforest

**0 additional Rainforest credits.** The endpoint uses `Product.images`, `Product.a_plus` media, and video **metadata** already on the Product. It does not re-fetch the ASIN and does not enable extra Rainforest parameters.

## Image sources

| Source | From |
|--------|------|
| Main image | `Product.images` with `is_main=true`, else the first gallery image |
| Gallery | remaining `Product.images` |
| A+ | `Product.a_plus.images` |
| Brand Story | hero, logo, extra Brand Story URLs, company logo |

Video files and video thumbnails are **not** sent to the model.

## URL security

`MediaUrlValidator` requires HTTPS and rejects localhost, private/link-local IPs, `file://`, `data:`, `ftp:`, userinfo, and non-443 ports.

Allowlist (hosts observed in this repository’s Product/fixture data):

- `m.media-amazon.com`
- `placehold.co` (mock catalog)

Optional extra hosts: `OPENAI_VISION_ALLOWED_HOSTS` (comma-separated). Invalid URLs are skipped with warnings; they do not crash analysis.

## Image selection

Deterministic. No preliminary OpenAI call.

Default maximum: **`OPENAI_VISION_MAX_IMAGES=8`**.

Priority: main → gallery (with reserved slots for A+ / Brand Story) → A+ → Brand Story. Duplicate URLs are dropped.

Returned metadata: `images_available`, `images_selected`, `images_skipped`, `selection_reason`.

## OpenAI model

Uses `OPENAI_MODEL` unless `OPENAI_VISION_MODEL` is set.

Listing AI V1/V2 always use `OPENAI_MODEL`. Setting a vision model does not change 8C.

## A+ / Brand Story / video

| Evidence | Behavior |
|----------|----------|
| `a_plus is null` | “A+ evidence was unavailable from the supplied product data.” Not “no A+.” |
| `has_a_plus_content=false` | Provider reported A+ was not present. |
| A+ images present | Visual role analysis of those images |
| Brand Story media | Identity / distinctness vs gallery |
| Videos | Structural presence only. Frames are not analyzed. |

8C still owns A+ **text** intelligence. 8D does not duplicate that report.

## Cost controls

- Explicit click only
- Max 8 images
- One multimodal structured call + existing repair retry
- In-process cache (`AI_CACHE_TTL_SECONDS`, default 2700s) keyed on selected URLs + product facts + V2 analysis + model + prompt version + provider
- Cache hits record the existing OpenAI cache-hit ledger counter

Zero valid images: **no OpenAI call**. HTTP 422: “No valid listing images were available for visual analysis.”

## Endpoint

```text
POST /api/v1/analysis/listing/v2/images/ai
```

Request: `{ "product", "analysis": ListingAnalysisV2, "source" }`  
The server builds media evidence from Product. Clients cannot attach arbitrary extra image URLs.

`meta.engine` is `"multimodal_ai"`. `analysis` is echoed unchanged.

Usage ledger workflow: **`image_intelligence_v1`**.

## Prompt injection policy

Product titles, bullets, descriptions, A+ copy, image alt text, and **visible text inside images** are untrusted data.

The versioned system prompt (`image-intelligence-v1`) tells the model not to follow instructions found in listing copy or in pixels. User content is wrapped in `BEGIN UNTRUSTED …` / `END UNTRUSTED …` delimiters. Image parts are labeled `BEGIN UNTRUSTED IMAGE {id}` / `END UNTRUSTED IMAGE {id}`.

## Known limitations

- No image generation or editing
- No numeric image-quality / conversion score
- No video-frame analysis
- No OCR service (the model may read visible text in pixels)
- No Amazon policy engine
- Host allowlist is conservative; unusual CDNs are skipped
- OpenAI fetches the image URLs; this app does not download pixels locally

## Testing policy

Automated tests mock the OpenAI SDK. No live Rainforest or OpenAI calls.

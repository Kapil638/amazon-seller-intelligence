# AI Listing Intelligence V2

Milestone 8C adds **semantic listing content & SEO intelligence** on top of Listing Intelligence V2.

Prompt version: **`listing-intelligence-v2`**.

This layer answers: *What specifically should the seller improve in this listing, and why?*

It does **not** change deterministic V2 listing-quality scores. It does not invent Amazon keyword volume, rank, conversion, sales, CTR, PPC, or profitability. Image vision is out of scope (Milestone 8D).

## Purpose

Deterministic V2 can count bullets, flag missing specifications, and score structural SEO readiness. It cannot judge whether copy is natural, whether benefits are translated from features, or whether A+ text adds incremental value.

V2 AI uses the normalized `Product` plus `ListingAnalysisV2` to produce grounded title, bullet, description, A+, specification-coverage, and rewrite guidance.

## Architecture

V1 remains unchanged:

```text
Product + ListingAnalysis
  → AIListingIntelligenceService
    → AIProvider / OpenAIProvider
      → listing-intelligence-v1
        → AIListingIntelligence
```

V2 is a parallel flow that reuses the same `AIProvider` / `OpenAIProvider`. There is no second OpenAI client.

```text
Product + ListingAnalysisV2
  → AIListingIntelligenceV2Service
    → AIProvider / OpenAIProvider
      → listing-intelligence-v2
        → AIListingIntelligenceV2
```

One explicit **Generate AI strategy** click causes at most one structured OpenAI call, plus the existing structured-output repair retry. There is no title/bullet/SEO/A+ agent swarm.

Rainforest is not called. V2 AI operates on the Product already fetched.

## Endpoint

```text
POST /api/v1/analysis/listing/v2/ai
```

V1 remains:

```text
POST /api/v1/analysis/listing/ai
```

Request: `{ "product": ..., "analysis": <ListingAnalysisV2>, "source": "rainforest" }`

Response:

```json
{
  "product": { "...unchanged Product..." },
  "analysis": { "...unchanged ListingAnalysisV2..." },
  "ai_intelligence": { "...AIListingIntelligenceV2..." },
  "meta": {
    "engine": "ai",
    "provider": "openai",
    "model": "...OPENAI_MODEL...",
    "prompt_version": "listing-intelligence-v2",
    "usage": { "input_tokens": 0, "output_tokens": 0, "total_tokens": 0 }
  }
}
```

`analysis` is echoed unchanged. The model used is `OPENAI_MODEL` (no V2-specific model override).

## Input context

`build_ai_listing_v2_context()` sends only compact, normalized evidence:

| Block | Includes |
|-------|----------|
| Product identity | ASIN, marketplace, title, brand, category, category path, bullets, description, variation attributes |
| Specifications | `specifications`, clipped `specifications_flat`, product attributes |
| A+ | evidence state, flags, body_text / company_description / Brand Story text, image **alt** text, media counts |
| Media coverage | gallery count, main image present, video evidence state / counts, A+ media count, Brand Story media present |
| Listing Analysis V2 | listing quality score, section scores, findings, deterministic recommendations, evidence states, market signals, data coverage |

Not sent:

- raw Rainforest JSON or HTML
- image pixels or image/video URLs
- featured-review corpus
- provider credentials
- unbounded duplicate strings (long text is clipped)

## Evidence policy

Every high-priority recommendation should cite `evidence_codes` drawn from:

- a Product field
- a specification / attribute / variation attribute
- category context
- A+ evidence
- a V2 deterministic finding
- a market signal used only as factual context

Ungrounded recommendations must be omitted.

## SEO scope

Allowed: natural terminology, semantic coverage of known attributes, category relevance, missing concepts found in Product/specifications, repetition, title-to-bullet coverage, usefulness, readability.

Not allowed unless explicitly supplied: “high-volume keyword”, search volume, organic rank, Amazon keyword position, traffic potential, CPC, keyword conversion, SQP.

Good: *Consider naturally incorporating “stainless steel” into a bullet because it appears in the structured specifications but not in customer-facing copy.*

Bad: *“Stainless steel” is a high-volume Amazon keyword.*

## A+ handling

| Evidence | AI may say |
|----------|------------|
| `a_plus = null` / unknown | “A+ data was not available in the supplied evidence.” Not “the listing has no A+.” |
| `has_a_plus_content = false` | “Rainforest reported no A+ Content for this listing.” |
| present, body_text missing | Structural presence only; state that full A+ text was not available. |
| present, body_text available | Assess incremental value, repetition, and missing attributes. |

Do not infer A+ quality from image count alone.

## Specification coverage

AI reports which structured facts are already in customer-facing copy, which are missing, and which should not be forced into copy because they are irrelevant (internal SKUs, etc.).

## Rewrite constraints

Suggested title, up to five bullets, and an optional description excerpt must stay inside supplied Product facts.

Do not invent certifications, ingredients, medical claims, performance, warranty, compatibility, materials, dimensions, quantities, or features.

Do not add unsupported superlatives (`best`, `#1`, `guaranteed`, `clinically proven`).

Bullets should be concise, natural, benefit-led where appropriate, not stuffed, not ALL CAPS, and not a dump of every specification.

## Score authority

V2 deterministic scores are final.

Allowed: *The 62/100 bullet score is mainly driven by limited attribute coverage and repetition.*

Not allowed: *I would score the bullets 82/100.*

Market signals (rating, reviews, BSR, recent-sales text) may be mentioned as facts. They do not prove copy quality or that recommendations will increase sales.

## Prompt injection

Seller/product content is untrusted. User prompts wrap:

```text
BEGIN UNTRUSTED PRODUCT DATA
...
END UNTRUSTED PRODUCT DATA

BEGIN UNTRUSTED A+ CONTENT
...
END UNTRUSTED A+ CONTENT

BEGIN UNTRUSTED SPECIFICATIONS
...
END UNTRUSTED SPECIFICATIONS
```

The system prompt forbids following instructions inside titles, bullets, descriptions, seller names, specifications, A+ copy, or review text, and forbids revealing hidden instructions.

## Structured output

`AIListingIntelligenceV2`:

- `executive_assessment`
- `priority_actions` (max 5; each with `evidence_codes`)
- `content_analysis` (title, bullets + SEO readiness notes, description, A+, structure)
- `specification_coverage`
- `rewrite_suggestions`
- `seller_action_plan` (max 7)
- `confidence_notes`

## Cache

In-process `MemoryTtlValueCache` using `AI_CACHE_TTL_SECONDS` (default **2700** seconds / 45 minutes).

Cache key is a stable hash of:

- V2 Product context
- ListingAnalysisV2
- model
- prompt version `listing-intelligence-v2`
- AI provider name

Identical repeated clicks return the cached result and record an OpenAI **cache hit** (no provider call). Product, analysis, prompt version, or model changes invalidate the key.

## Cost controls

- Explicit user click only.
- Compact context; clipped long text.
- One structured call + at most one repair retry.
- Compact output limits (see schema `max_length`).
- Existing `OPENAI_MODEL`, timeout, and `openai_max_output_tokens`.

## Token usage

V2 AI calls use the existing application usage ledger (`workflow=listing_intelligence_v2`):

- provider calls
- cache hits / calls saved
- input / output / total tokens
- model
- latency
- app-estimated cost when pricing is available

## What V2 AI cannot know

- Keyword volume, rank, traffic, CPC, SQP
- Conversion, CTR, sales, profitability
- Image/video visual quality (no pixels sent)
- Full review sentiment (featured reviews are omitted from V2 AI context)
- Whether recommendations will increase sales
- Confirmed A+ absence when the payload omitted A+ (`unknown`)

## Future image vision

Milestone 8D may add multimodal image analysis. This milestone does not send image URLs to OpenAI, download images, or create image-quality scores.

## Frontend

Primary path:

```text
Analyze ASIN → Listing Intelligence V2 → Generate AI strategy
```

V1 AI remains as a collapsed legacy/dev path. The UI renders structured report sections, not a chatbot transcript.

## Errors

Same mapping as V1 AI: missing key/model **503**, rate limit **503**, unusable structured output **502**, safety refusal **422**.

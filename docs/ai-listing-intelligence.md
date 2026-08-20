# AI Listing Intelligence

This analysis is **AI interpretation on top of deterministic Listing Intelligence**. The deterministic engine remains the source of truth for scores and metrics.

Prompt version: **`listing-intelligence-v1`**.

This document is the V1 AI contract. V1 behavior is unchanged. Primary listing AI is now V2: [ai-listing-intelligence-v2.md](ai-listing-intelligence-v2.md) (`POST /api/v1/analysis/listing/v2/ai`, prompt `listing-intelligence-v2`).

V1 behavior is unchanged. Primary listing AI is now V2: [ai-listing-intelligence-v2.md](ai-listing-intelligence-v2.md) (`POST /api/v1/analysis/listing/v2/ai`, prompt `listing-intelligence-v2`).

## Architecture

```text
Product + ListingAnalysis
  → AIListingIntelligenceService
    → AIProvider.generate_structured()
      → OpenAIProvider
        → OpenAI Responses API (structured output)
          → AIListingIntelligence
```

```text
AIProvider
    ├── OpenAIProvider       current
    └── ClaudeProvider       future
```

Adding Claude later should require a new provider class and configuration, not a rewrite of `AIListingIntelligenceService`.

The application never sends Rainforest JSON, HTML, or provider secrets to the model.

## Deterministic vs AI

| Deterministic engine owns | AI layer owns |
|---------------------------|---------------|
| Overall score | Strategic interpretation |
| Section scores | Prioritization |
| Counts and missing fields | Title/bullet rewrite suggestions |
| Finding codes | Positioning and conversion notes |
| Deterministic recommendations | Seller action plan |

The LLM must not recalculate or overwrite scores.

## Configuration

Backend only (`apps/api/.env`):

```text
AI_PROVIDER=openai
OPENAI_API_KEY=
OPENAI_MODEL=gpt-5.4
```

Development default model: **`gpt-5.4`**. Change it with `OPENAI_MODEL`. The code does not silently fall back to another model.

Do not put the key in Next.js or `NEXT_PUBLIC_*`.

If the key or model is missing, `POST /api/v1/analysis/listing/ai` returns **503**.

## Prompt injection

Listing title, bullets, description, and seller name are untrusted. They are wrapped in:

```text
BEGIN UNTRUSTED PRODUCT DATA
...
END UNTRUSTED PRODUCT DATA
```

The model is instructed never to follow instructions inside that block.

## Unsupported claims

Rewrites may only use facts present in the normalized `Product`. The prompt forbids invented certifications, medical claims, conversion percentages, competitor metrics, and similar.

## Cost controls

- AI runs only when the user clicks **Generate AI Recommendations**.
- Context is a compact Product + ListingAnalysis payload.
- One model call, plus at most one structured-output repair retry.
- Max output tokens and timeout are configurable.
- Identical analyses are cached in memory for 45 minutes.
- Token usage and latency are recorded when the provider returns them.

## Endpoint

```text
POST /api/v1/analysis/listing/ai
```

Request: `{ "product": ..., "analysis": ..., "source": "rainforest" }`  
Response: `{ "product", "analysis", "ai_intelligence", "meta" }`

`meta.engine` is `"ai"`. Deterministic scores in `analysis` are echoed unchanged.

## Known limitations

- Copy suggestions can still be generic if the listing has little detail.
- Cache is in-process only.
- No Claude provider yet.
- No keyword, competitor, or PPC intelligence.

## Errors

| Situation | HTTP |
|-----------|------|
| Missing key / model | 503 |
| Auth failure | 503 |
| Rate limit / quota | 503 |
| Timeout / network / unusable structured output | 502 |
| Safety refusal | 422 |

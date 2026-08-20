# API Budget dashboard

The compact **API Budget** strip at the top of Amazon Seller Intelligence answers two different questions. They are never the same number.

1. **Provider-account usage** — what Rainforest and OpenAI say this account/organization has consumed.
2. **This app’s ledger** — what *this process* actually requested, plus how many provider calls the local caches avoided.

Other applications, other API keys, or dashboard activity on the same provider account can make the two layers diverge. The UI labels them **Account** / **Provider spend** versus **This app**.

## Backend

```text
GET /api/v1/usage/dashboard
GET /api/v1/usage/dashboard?refresh=true
```

`refresh=true` bypasses the provider-account cache only. It does not call Rainforest product/search APIs or OpenAI Responses.

The JSON envelope is:

```json
{
  "rainforest": {
    "account": {
      "source": "rainforest_account_api",
      "available": true,
      "credits_used": 21,
      "credits_limit": 100,
      "credits_remaining": 79,
      "usage_percentage": 21.0,
      "reset_at": "2026-09-19T00:00:00+00:00"
    },
    "app": {
      "source": "application_ledger",
      "product_calls": 14,
      "search_calls": 3,
      "cache_hits": 17,
      "calls_saved": 17
    }
  },
  "openai": {
    "account": {
      "source": "openai_organization_costs_api",
      "available": true,
      "spend_usd": 0.18,
      "budget_usd": 100.0
    },
    "app": {
      "source": "application_ledger",
      "estimated_spend_usd": 0.16,
      "requests": 6,
      "total_tokens": 15253,
      "cache_hits": 2,
      "calls_saved": 2
    }
  }
}
```

If a provider-account lookup fails, the rest of the app keeps working. The card shows **Usage temporarily unavailable**. Product lookup, search, and AI are not blocked.

## Rainforest — authoritative account credits

Rainforest documents `GET https://api.rainforestapi.com/account`. Calls to the Account API are **free** and do not consume Product Data API credits.

The backend uses `RAINFOREST_API_KEY` only. The key is never sent to Next.js.

Mapped fields:

| Field | Source |
| --- | --- |
| `credits_used` | Account API |
| `credits_remaining` | Account API |
| `credits_limit` | Account API, or `used + remaining` when the limit field is omitted |
| `usage_percentage` | `used / limit` |
| `reset_at` | `credits_reset_at` |
| `usage_history` | last 14 points; supports a flat date list or monthly `credits_total_per_day` |

The live Account API may omit `credits_limit`. When that happens the backend derives `limit = used + remaining` so the progress bar still has a denominator. Identity fields, destination IPs, and plan details are not exposed.

Not exposed: `api_key`, account email, account name, plan, or other identity fields.

Do **not** assume one HTTP request equals one Rainforest credit. The bill is the Account API. This app’s ledger counts product calls, search calls, cache hits, and failures separately.

## OpenAI — official provider spend

OpenAI’s organization Costs API is:

```text
GET https://api.openai.com/v1/organization/costs?start_time=<unix seconds>
Authorization: Bearer <Admin API key>
```

This is an **Admin API**. A normal project `OPENAI_API_KEY` cannot call it. Admin keys also cannot be used for Responses / Chat Completions.

Configure on the backend only:

```text
OPENAI_ADMIN_API_KEY=          # or OPENAI_ADMIN_KEY
OPENAI_BUDGET_USD=100          # display budget; visual only
```

If the admin key is missing, provider spend is `available: false` with status `not_configured`. App-tracked usage still works.

The dollar figure is month-to-date organization spend (UTC calendar month), summed from official cost buckets. It can include other apps and keys on the same organization. That is why it is labeled **Provider spend**, not “this app”.

`OPENAI_BUDGET_USD` is an application display budget. V1 does not read or set OpenAI’s organization spend-limit endpoint.

## OpenAI — application estimated cost

Every successful Responses call already returns `usage`. The backend records:

- `input_tokens`
- `cached_input_tokens` (OpenAI prompt-cache reads, when present)
- `output_tokens`
- `total_tokens`
- `model`
- `workflow` (`listing_intelligence`, `listing_intelligence_v2`, `image_intelligence_v1`, `competitive_intelligence`, `bulk_listing_intelligence`, `portfolio_summary`)

Estimated USD is calculated from a single table:

```text
apps/api/app/usage/openai_pricing.py
```

`PRICING_VERSION` is the public list-price snapshot. Unknown models still record tokens and return **cost unavailable**. Prices are not invented, not copied around the codebase, and do not include long-context surcharges, batch, flex, or regional uplifts.

This estimate is **not** the OpenAI invoice.

## Cache savings

Local TTL caches avoid provider calls:

| Cache | What a hit means |
| --- | --- |
| Rainforest product (`MemoryTtlCache`) | One product Account/Product API call avoided |
| Rainforest search | One `type=search` call avoided |
| Listing / competitive / image AI | One OpenAI Responses call avoided |

`calls_saved` equals those local cache hits. OpenAI prompt-cache tokens (`cached_input_tokens`) are a different mechanism and only affect estimated cost.

Rainforest Account API lookups are not counted as product or search calls.

## Refresh strategy

| Source | Interval |
| --- | --- |
| Rainforest Account API | cached 60 seconds |
| OpenAI Costs API | cached 300 seconds |
| Application ledger | updated immediately after calls |
| Frontend poll | 60 seconds |
| Refresh button | `?refresh=true` — provider-account only |

Twenty dashboard requests within the TTL share one provider-account fetch. The UI does not poll every few seconds.

## Warning levels

Visual only. Requests are **not** blocked.

| Band | Rainforest credits or OpenAI spend / budget |
| --- | --- |
| `< 70%` | normal |
| `70–89%` | warning |
| `90%+` | critical |

## Future hard budget guard (not implemented)

Designed, not enabled:

- Stop Rainforest product/search when remaining credits ≤ N.
- Stop OpenAI calls after a daily/monthly spend cap.
- Confirm expensive bulk AI jobs.

V1 must not block core lookup or AI because usage monitoring failed or a budget threshold was crossed.

## Security

Never expose to the browser:

- `RAINFOREST_API_KEY`
- `OPENAI_API_KEY`
- `OPENAI_ADMIN_API_KEY` / `OPENAI_ADMIN_KEY`
- Rainforest account email/name
- provider account credentials

Provider usage HTTP happens on the FastAPI process only. The frontend receives sanitized aggregates.

## Limitations

- Application ledger is **in-memory** and resets when the API process restarts. There is no Redis/database yet.
- Rainforest account credits are the provider’s source of truth; this app does not reconstruct the Rainforest bill from HTTP counts.
- OpenAI provider spend requires a separate Admin API key and reflects the **organization**, not only this app.
- App-estimated OpenAI cost uses a versioned public price table and can lag official pricing changes.
- Usage history is a small expandable list, not a full chart.
- No automated test calls live Rainforest or OpenAI.

See [rainforest-provider.md](rainforest-provider.md) for product/search mapping.

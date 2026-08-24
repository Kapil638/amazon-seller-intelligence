# 13 — Testing and Guardrails

## Backend

```bash
cd apps/api && uv run pytest
```

Verified 24 August 2026: **620 passed**. Offline. `conftest.py` clears SP-API environment so tests cannot accidentally hit live Amazon.

Amazon tests use placeholder `Atzr|test-…` / `Atza|test-…` strings and assert they never appear in public JSON, logs, or `token_reference`.

Do **not** add live Amazon, live Rainforest, or live OpenAI to the default pytest suite.

## Frontend

```bash
cd apps/web && npm test
```

Verified 24 August 2026: **33 passed** (3 files). Connection UI tests reject secret-shaped fields.

## Alembic

```bash
cd apps/api && uv run alembic heads && uv run alembic current && uv run alembic history
```

Expect one head: `0008_amazon_oauth_states`.

## Security guardrails in code

- Pydantic `extra="forbid"` on public Amazon models
- `public_model_dump` / sanitizers reject `token` key fragments
- SecretStr for tokens
- SecretProvider rejects token-shaped references
- `bind_token_reference` rejects `Atzr|` / `Atza|` values
- Production secret backend fail-closed

## What tests do not prove

- Real Seller Central consent
- Exact Amazon Login URI / Redirect URI production match
- Production SecretProvider
- Ingest correctness (no ingest yet)

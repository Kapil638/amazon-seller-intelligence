# 03 — Tech Stack

## Backend (`apps/api`)

- Python 3.12+
- FastAPI, Pydantic v2, pydantic-settings
- httpx
- SQLAlchemy 2.0, Alembic, psycopg
- Supabase client (Storage; FastAPI holds the service role)
- OpenAI SDK
- uv for deps (`uv run pytest`, `uv run alembic`)
- pytest (no live Amazon, no live Rainforest in default tests)

## Frontend (`apps/web`)

- Next.js 16, React 19, TypeScript
- Tailwind CSS 4
- Vitest + Testing Library (`npm test`)

## Data / infra (dev)

- PostgreSQL via Supabase when `DATABASE_URL` is set
- Private Storage buckets for uploads and generated PDFs/Excel
- In-process TTL caches for Rainforest/OpenAI duplicate suppression (not history)

## AI

- OpenAI is the current `AIProvider`
- Claude as a provider is not implemented
- Copilot planner/synthesis use OpenAI; tools remain Python

## Amazon

- SP-API Sellers v1 (`GET /sellers/v1/marketplaceParticipations`)
- Login with Amazon token endpoint (`https://api.amazon.com/auth/o2/token`)
- Separate sandbox vs production/Draft LWA application credentials
- SecretProvider Protocol; development backend only

## Explicitly out of stack today

- Redis / Celery
- LangGraph / CrewAI / agent frameworks
- AWS Secrets Manager / Vault production backend
- Amazon Ads API SDK

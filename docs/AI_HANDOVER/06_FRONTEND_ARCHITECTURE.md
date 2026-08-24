# 06 — Frontend Architecture

Package: `apps/web`.

## Rules

- Talks only to FastAPI (`NEXT_PUBLIC_API_BASE_URL`).
- Never receives Amazon tokens, `token_reference`, LWA secrets, or service-role keys.
- Types in `src/lib/types.ts`; HTTP in `src/lib/api.ts`.

## Amazon Connection UI

`src/components/amazon-connection.tsx` plus `amazon-connection-ui.test.tsx`.

Behaviours as of 12B.1C–D:

- Loads `GET /api/v1/amazon/connection`
- **Test Connection** → `POST /connection/test` (sandbox env-token or seller handshake depending on backend row)
- **Connect Amazon** / continue → `POST /connection/authorize`, then browser navigates to `authorization_url`
- Does not store raw OAuth `state` or authorization URLs in React state beyond the navigation
- Renders lifecycle `connection_status` separately from display `status`
- Extra-forbid public payloads: tests assert `Atzr|` / `Atza|` never render

Amazon callback is a **backend** route (`GET /api/v1/amazon/connection/callback`), not a Next.js page.

## Other surfaces (unchanged by 12B.1)

Analyze, History, Reports, Bulk, Profit, Copilot (`/copilot`). Do not change Copilot UI as part of Amazon connection work.

## Tests

`npm test` → Vitest. Current baseline: 3 files, 33 passed. No live Amazon.

# RoboCo Control Panel

Next.js 16 control panel for the RoboCo AI agent system. Formerly a separate repository (`rennf93/roboco-panel`), now vendored under `panel/` in this monorepo so `docker compose up -d` brings up the whole stack from one place.

## Stack

- Next.js 16 (App Router, standalone output)
- TypeScript
- Tailwind CSS
- Radix UI primitives
- `dnd-kit` for drag/drop (kanban)
- pnpm for package management

## Running in production (the normal path)

Use the root-level Docker Compose:

```bash
# from the repo root (one level up from this directory)
docker compose up -d
```

The panel is built as part of the compose stack via `docker/panel.Dockerfile` and served internally on port 3000. Nginx (also in the compose stack) is the single externally-exposed service on `http://localhost:3000` and routes:

- `/api/*` and `/ws/*` → orchestrator (FastAPI, port 8000)
- everything else → the Next.js panel

The panel uses relative URLs (`/api/v1`, `/ws`) so nothing here needs a backend URL in `.env`.

## Running the panel alone for UI development

```bash
cd panel
pnpm install
pnpm dev
```

That gives you Next dev-server on `localhost:3000`, but you still need the orchestrator reachable at `localhost:8000` (or via nginx) for API calls to work. Easiest: `docker compose up -d` the backend services, then run `pnpm dev` against that.

## Build scripts

- `pnpm dev` — development server with hot reload
- `pnpm build` — production build (outputs `.next/standalone/`)
- `pnpm start` — run the standalone build
- `pnpm lint` — ESLint

## Where things live

- `src/app/` — Next.js App Router pages
- `src/components/` — React components (organized by feature: tasks, agents, channels, …)
- `src/lib/api/` — typed API client (thin wrappers over `fetch`)
- `src/lib/` — constants, utilities, WebSocket hooks
- `src/types/` — shared TypeScript types mirroring backend schemas
- `src/hooks/` — reusable React hooks (see [Frontend hooks](../docs/frontend/hooks.md))

## Hooks

The panel exposes public hooks under `@/hooks`. See [Frontend hooks](../docs/frontend/hooks.md) for full API reference and examples.

### `usePageRefresh`

Page-scoped refresh coordinator. Pages register data-refetch callbacks; the navbar refresh button in `src/components/layout/header.tsx` calls `refresh()` and reflects the combined `loading`/`disabled` state. The button is disabled when no callbacks are registered (the registry is empty) and while a refresh is in progress.

```tsx
import { usePageRefresh } from "@/hooks";

const { register, unregister, refresh, loading, disabled } = usePageRefresh();
```

- `disabled` is `true` when no callbacks are registered (there is nothing to refresh)
- `disabled` becomes `false` once a callback is registered
- `disabled` returns to `true` when all callbacks are unregistered

Wrap your page or layout in `PageRefreshProvider` from `@/components/providers` before consuming the hook. Dashboard pages should register their refetch callbacks and avoid adding inline "Refresh" buttons; see [`docs/frontend/components/page-refresh-provider.md`](../docs/frontend/components/page-refresh-provider.md) for the full wiring list and examples.

## Backend schema changes

When the backend changes response shapes, mirror them in `src/types/` and the relevant `src/lib/api/` module. Keep API paths relative so nginx routing keeps working.

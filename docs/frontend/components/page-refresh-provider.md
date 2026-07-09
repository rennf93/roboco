# Page-scoped refresh provider

A React Context + provider that lets global UI chrome (the navbar refresh button) trigger a refresh that is scoped to the page the user is currently viewing.

## Purpose

Several dashboard pages fetch their own data through TanStack Query. A global refresh button in the header needs to re-fetch data for the *current* page without invalidating every other page's cache. `PageRefreshProvider` maintains a simple callback registry: pages register their own refetch callbacks when mounted, and the navbar button invokes every registered callback when clicked.

This keeps refresh semantics page-local. Only the components that are currently mounted and have registered callbacks participate in a refresh cycle.

## Files

| File | Role |
|------|------|
| `panel/src/components/providers/page-refresh-provider.tsx` | Provider component, context value, and `RefreshCallback` / `PageRefreshState` types. |
| `panel/src/components/providers/index.ts` | Barrel export (`PageRefreshProvider`, `PageRefreshContext`, types). |
| `panel/src/hooks/use-page-refresh.ts` | Public `usePageRefresh` hook that consumes the context. |
| `panel/src/components/app-providers.tsx` | Root provider stack; wraps the app in `PageRefreshProvider`. |
| `panel/src/components/layout/header.tsx` | Navbar refresh button that calls `refresh()` and reflects `loading`. |

## API

### `PageRefreshState`

```ts
interface PageRefreshState {
  disabled: boolean;
  loading: boolean;
  register: (callback: RefreshCallback) => void;
  unregister: (callback: RefreshCallback) => void;
  refresh: () => Promise<void>;
}
```

- `disabled` — whether refresh actions are currently disabled (controlled by the provider prop).
- `loading` — whether a refresh cycle is currently running.
- `register(callback)` — add a callback to invoke on the next refresh.
- `unregister(callback)` — remove a previously registered callback.
- `refresh()` — invoke every registered callback concurrently and update `loading` until they settle.

### `RefreshCallback`

```ts
type RefreshCallback = () => void | Promise<void>;
```

May be sync or async; `refresh` always returns a `Promise` and awaits async callbacks with `Promise.all`.

### `PageRefreshProviderProps`

```ts
interface PageRefreshProviderProps {
  children: React.ReactNode;
  disabled?: boolean;
}
```

- `children` — React tree that can consume the context.
- `disabled` — when `true`, `refresh()` is ignored and `disabled` is exposed as `true`.

## How to consume

Pages and panels that want to expose a refresh action should:

1. Import `usePageRefresh` from `@/hooks`.
2. In a `useEffect`, register a callback that refetches the page's data.
3. Unregister the same callback on unmount.

```tsx
"use client";

import { useEffect } from "react";
import { usePageRefresh } from "@/hooks";
import { useProducts } from "@/hooks/use-products";

export default function ProductsPage() {
  const { data: products, error, refetch } = useProducts();
  const { register, unregister, refresh } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);

  if (error) {
    return <OfflineState onRetry={() => void refresh()} />;
  }

  return <ProductTable products={products} />;
}
```

## How the navbar button triggers refresh

`panel/src/components/layout/header.tsx` consumes the same context:

```tsx
const { refresh, loading } = usePageRefresh();

<Button
  variant="ghost"
  size="icon"
  onClick={() => void refresh()}
  disabled={loading}
  aria-label="Refresh only the current page"
  title="Refresh only the current page"
>
  <RefreshCw className={cn("h-5 w-5", loading && "animate-spin")} />
</Button>
```

The button sits between the connection-status badge and the theme toggle. It is disabled and shows a spinner while any registered refresh callback is still running. Concurrent clicks are coalesced: a second click while a cycle is in progress does nothing.

## Pages wired to the navbar refresh

The following routes and dashboard components register refetch callbacks with `usePageRefresh`. New pages should follow the same pattern.

| Route / component | Registered refetch(es) |
|---|---|
| `app/(dashboard)/a2a/page.tsx` | `refetchConversations`, `refetchPairs`, `refetchMessages` |
| `app/(dashboard)/agents/page.tsx` | `refetch` (orchestrator status) |
| `app/(dashboard)/agents/[agentId]/page.tsx` | `refetch` (agent status) |
| `app/(dashboard)/journals/page.tsx` | `refetch` (agent list) |
| `app/(dashboard)/journals/[entryId]/page.tsx` | `refetch` (journal entry) |
| `app/(dashboard)/metrics/page.tsx` | `refetchTasks`, `refetchStatus` |
| `app/(dashboard)/notifications/page.tsx` | `refetch` (notifications list) |
| `app/(dashboard)/products/page.tsx` | `refetch` (products) |
| `app/(dashboard)/projects/page.tsx` | `refetch` (projects) |
| `app/(dashboard)/tasks/page.tsx` | `refetch` (tasks) |
| `app/(dashboard)/tasks/[taskId]/page.tsx` | `refetch` (task detail) |
| `app/(dashboard)/work-sessions/page.tsx` | `refetch` (work sessions) |
| `components/auditor/auditor-dashboard.tsx` | `refetch` (auditor dashboard) |
| `components/business/pitches-tab.tsx` | `refetch` (pitches) |
| `components/dashboard/command-center.tsx` | `refetch` (CEO overview) |
| `components/dashboard/release-proposal-card.tsx` | `refetch` (release proposal) |
| `components/dashboard/roadmap-review-queue.tsx` | `refetch` (roadmap cycles) |
| `components/dashboard/x-post-queue.tsx` | `refetch` (X post queue) |
| `components/git/git-browser.tsx` | project/status/log/branches refetches |
| `components/kanban/core/kanban-board.tsx` | `refetch` (kanban tasks) |
| `components/knowledge-base/knowledge-base-browser.tsx` | stats/health refetches |

## Design decisions

- **Callback-set registry, not query invalidation**: the provider stores `Set<RefreshCallback>` and lets each page decide how to refresh. This avoids spraying React Query cache invalidations across unrelated pages.
- **No scope keys**: the previous implementation keyed callbacks by a scope string and tracked an "active scope". That was removed because React mount/unmount lifecycle already scopes callbacks to the visible page; extra scope bookkeeping added complexity without benefit.
- **React Context over Zustand**: the state is transient and tied to the React tree, so Context is the lighter fit.
- **Coalesced concurrent refreshes**: `refresh()` ignores subsequent calls while a cycle is already running, preventing double-refetch and keeping the button disabled honestly.
- **Provider types live next to the provider**: unlike the first implementation, the context value types and `RefreshCallback` type now live in `components/providers/page-refresh-provider.tsx` and are re-exported by `components/providers/index.ts`. This matches the current panel boundary that keeps provider primitives together.

## Testing

Run the provider-related tests with the panel test suite:

```bash
cd panel
pnpm test page-refresh
```

Covered behaviors:

- `usePageRefresh` throws when called outside a `PageRefreshProvider`.
- Registering callbacks makes `refresh()` invoke them.
- `refresh()` returns a promise and awaits async callbacks.
- Unregistering prevents the callback from being called.
- `refresh()` is a no-op when `disabled` is `true`.
- The navbar button renders between the connection-status badge and the theme toggle.
- The navbar button exposes an accessible page-scoped label.
- The navbar button is disabled and shows a spinner while a refresh callback is running.
- Concurrent clicks do not start a second refresh cycle.

## Related work

- Completed prerequisite: **Add public `usePageRefresh` hook** — consumes this provider.
- This task: **Add navbar refresh button and remove inline dashboard refresh buttons** — wires the header button and removes per-page inline buttons.

## Migration / rollout

No consumer migration is needed for end users. For developers adding a new dashboard page:

1. Wrap tests for the page in `PageRefreshProvider` from `@/components/providers` if they render page-level components that call `usePageRefresh`.
2. Register the page's refetch callbacks and unregister them on unmount.
3. Do not add a new inline "Refresh" button; use the shared navbar button instead.

Operation-specific refresh controls (for example, a "Reindex" button inside a knowledge-base card or a "Retry" on an `OfflineState`) are intentionally preserved when their action is local to a sub-component, not a whole-page refresh.

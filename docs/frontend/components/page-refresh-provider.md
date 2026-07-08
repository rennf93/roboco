# Page-scoped refresh provider

A React Context + provider that lets global UI trigger a refresh action that is scoped to whichever page the user is currently viewing.

## Purpose

The panel has several pages that each fetch their own data. A global refresh button in the navbar needs to re-fetch data for the *current* page without invalidating every other page's React Query cache. `PageRefreshProvider` maintains a registry of scope-keyed refresh callbacks and an "active scope", so the refresh button can call the right handler for the current page.

## Files

| File | Role |
|------|------|
| `panel/src/store/page-refresh-context.ts` | Context value type (`PageRefreshContextValue`), callback type (`RefreshCallback`), and the raw React context object (`PageRefreshContext`). |
| `panel/src/components/page-refresh-provider.tsx` | Provider component that manages scope registration, active scope, and dispatch. |
| `panel/src/components/providers.tsx` | Root provider stack; wraps the app in `PageRefreshProvider`. |
| `panel/src/components/__tests__/page-refresh-provider.test.tsx` | Unit tests for registration, active scope, explicit scope, unregister, and multi-scope isolation. |

## API

### `PageRefreshContextValue`

```ts
interface PageRefreshContextValue {
  activeScope: string | null;
  setActiveScope: (scope: string | null) => void;
  register: (scope: string, callback: RefreshCallback) => void;
  unregister: (scope: string) => void;
  refresh: (scope?: string) => Promise<void>;
}
```

- `activeScope` — the scope currently considered active, or `null` if none.
- `setActiveScope` — mark a scope as active (e.g. on page mount) or clear it.
- `register(scope, callback)` — associate a refresh callback with a scope.
- `unregister(scope)` — remove a previously registered callback.
- `refresh(scope?)` — invoke the callback for the given scope, or the active scope if none is provided. Does nothing when there is no target scope.

### `RefreshCallback`

```ts
type RefreshCallback = () => void | Promise<void>;
```

May be sync or async; `refresh` always returns a Promise and awaits async callbacks.

## How to consume

> **Note:** the public `usePageRefresh` hook that wraps this context is being added in a sibling task. Until that lands, import the context directly only in the hook or in tests.

Pages that want to expose a refresh action should:

1. Pick a stable scope string, usually matching the route or feature (e.g. `"dashboard"`, `"tasks"`).
2. Register a callback that performs the refresh (typically invalidating/re-fetching React Query cache keys for that page).
3. Set that scope as active while the page is mounted.
4. Unregister and clear the active scope on unmount.

Example pattern using the raw context (to be replaced by the sibling `usePageRefresh` hook):

```tsx
"use client";

import { useContext, useEffect } from "react";
import { PageRefreshContext } from "@/store/page-refresh-context";

export default function TasksPage() {
  const ctx = useContext(PageRefreshContext);

  useEffect(() => {
    if (!ctx) return;
    ctx.register("tasks", async () => {
      // trigger the page-specific refetch
    });
    ctx.setActiveScope("tasks");
    return () => {
      ctx.unregister("tasks");
      ctx.setActiveScope(null);
    };
  }, [ctx]);

  return <div>{/* page content */}</div>;
}
```

## Design decisions

- **Split between `store/` and `components/`**: the context value type lives in `src/store/` (state-management layer) and the JSX provider lives in `src/components/`, matching the panel's architectural boundary that keeps state primitives out of component folders and JSX out of the store layer.
- **React Context over Zustand**: the state is transient and tightly coupled to the React tree (mount/unmount lifecycle), so Context is the lighter, clearer fit.
- **Scope registry keyed by string**: allows multiple pages to register independently. Only the active scope's callback is invoked by default, so unrelated pages are not refreshed.
- **No React Query invalidation inside the provider**: the provider is deliberately dumb about *how* a page refreshes. Each page owns its refresh logic, keeping concerns separated.

## Testing

Run the provider tests with the panel test suite:

```bash
cd panel
pnpm test page-refresh-provider
```

Covered behaviors:

- Default active scope is `null`.
- Registering a callback and setting the active scope makes `refresh()` invoke it.
- `refresh("explicit-scope")` invokes the callback for that scope regardless of the active scope.
- Unregistering prevents the callback from being called.
- `refresh()` is a no-op when no active scope is set and no explicit scope is passed.
- `setActiveScope` updates and clears the active scope.
- Multiple scopes can be registered independently; only the active scope is refreshed by default.

## Related work

- Sibling task: **Add public `usePageRefresh` hook** — wraps this context in a friendly hook with cleanup.
- Sibling task: **Add navbar refresh button and remove inline dashboard refresh buttons** — the consumer of this provider.

## Migration / rollout

No migration needed. The provider is added at the root of the provider stack in `panel/src/components/providers.tsx` and does not change existing data fetching behavior until a page registers its own refresh callback.

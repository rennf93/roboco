# Frontend hooks

This page documents the public React hooks available under `panel/src/hooks`.

## `usePageRefresh`

A page-scoped refresh coordinator. Pages and panels register callbacks that refetch their data; UI chrome calls `refresh()` and reflects the combined `loading`/`disabled` state.

### When to use it

Use `usePageRefresh` when several components on the same page need to refresh together from a single trigger, such as a navbar refresh button. It keeps the refresh lifecycle scoped to the current page and avoids invalidating unrelated data.

### Setup

Wrap the page (or root layout) with `PageRefreshProvider`:

```tsx
import { PageRefreshProvider } from "@/components/providers";

export default function Layout({ children }: { children: React.ReactNode }) {
  return <PageRefreshProvider>{children}</PageRefreshProvider>;
}
```

### Basic usage

```tsx
import { useEffect } from "react";
import { usePageRefresh } from "@/hooks";
import { useTasks } from "@/hooks";

export function TasksPanel() {
  const { refetch } = useTasks();
  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const refresh = () => refetch();
    register(refresh);
    return () => unregister(refresh);
  }, [register, unregister, refetch]);

  return <div>{/* task list */}</div>;
}
```

### Triggering a refresh from UI chrome

```tsx
import { usePageRefresh } from "@/hooks";

export function RefreshButton() {
  const { refresh, loading, disabled } = usePageRefresh();

  return (
    <button onClick={refresh} disabled={disabled || loading}>
      {loading ? "Refreshing…" : "Refresh"}
    </button>
  );
}
```

### API reference

#### `PageRefreshProvider`

| Prop | Type | Default | Description |
|------|------|---------|-------------|
| `children` | `React.ReactNode` | required | React tree that can consume the context. |
| `disabled` | `boolean` | `false` | When `true`, `refresh()` is ignored and `disabled` is exposed as `true`. |

#### `usePageRefresh`

Returns a `PageRefreshState` object:

| Property | Type | Description |
|----------|------|-------------|
| `disabled` | `boolean` | Whether refresh actions are currently disabled. |
| `loading` | `boolean` | Whether a refresh cycle is currently in progress. |
| `register` | `(callback: RefreshCallback) => void` | Add a callback to invoke on the next refresh. |
| `unregister` | `(callback: RefreshCallback) => void` | Remove a previously registered callback. |
| `refresh` | `() => Promise<void>` | Run every registered callback and update `loading`. |

`RefreshCallback` is `() => void | Promise<void>`. Synchronous and asynchronous callbacks are both supported.

### Behavior

- `usePageRefresh` throws if called outside a `PageRefreshProvider` so consumers fail fast instead of silently missing refreshes.
- Concurrent calls to `refresh()` are coalesced: a second call while one is running returns immediately and does not start another cycle.
- When `disabled` is `true`, `refresh()` is a no-op and callbacks are not invoked.
- `register` and `unregister` are stable across renders and can be used as `useEffect` dependencies.

### Exports

- `usePageRefresh` from `@/hooks`
- `PageRefreshProvider` from `@/components/providers`
- Types: `PageRefreshState`, `RefreshCallback`, `PageRefreshProviderProps`

### Migration notes

`panel/src/components/providers.tsx` was renamed to `panel/src/components/app-providers.tsx` so that `@/components/providers` could be used as a barrel export for `PageRefreshProvider`. Update any direct import of the root providers component from `@/components/providers` to `@/components/app-providers`.

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

### Navbar refresh button

The canonical consumer is `panel/src/components/layout/header.tsx`. The refresh button is rendered between the connection-status badge and the theme toggle. Its accessible label and tooltip read **"Refresh only the current page"**, and it is disabled with a spinning icon while the registered refresh cycle is running.

Dashboard pages no longer include their own inline "Refresh" buttons. Instead, each page registers its refetch callbacks with `usePageRefresh` and lets the shared header button drive the refresh. See [`components/page-refresh-provider.md`](../components/page-refresh-provider.md) for the full list of wired pages and the registration pattern.

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

- `panel/src/components/providers.tsx` was renamed to `panel/src/components/app-providers.tsx` so that `@/components/providers` could be used as a barrel export for `PageRefreshProvider`. Update any direct import of the root providers component from `@/components/providers` to `@/components/app-providers`.
- The earlier scope-keyed provider files (`panel/src/components/page-refresh-provider.tsx` and `panel/src/store/page-refresh-context.ts`) were deleted. The current implementation lives in `panel/src/components/providers/page-refresh-provider.tsx` and is consumed through `usePageRefresh` from `@/hooks`.

## Data-hook null-guard audit

Every useQuery hook in `panel/src/hooks/` has been audited for missing `enabled` guards on undefined/null IDs, staleTime mismatches, and refetchInterval leaks on unmount.

### Audit results

All hooks carrying id-driven queries (`useTask`, `useSubtasks`, `useBoardReview`, `useTaskFindings`, `useTaskCollisionMap`, `useProject`, `useWorkSession`, `useWorkSessionForTask`, `useAgentStatus`, `useAgentDefinition`, `useJournalByAgent`, `useJournalEntry`, `useNotification`, `useGitStatus`, `useGitLog`, `useGitBranches`, `useGitDiff`, `useGitFile`, `useMemberScorecard`, and others) already carry correct `enabled: !!id` guards preventing undefined/null IDs from reaching the API.

**Special case: board-review polls.** `useTask` includes a conditional `refetchInterval` when the task belongs to the Board team and `board_review_complete` is still `false`. The interval is correctly wired to self-disable via a selector function — once the backend reports `board_review_complete: true`, the refetchInterval gate closes and no further polls are scheduled. TanStack Query's `Observer` already tears down the interval timer on unmount, so there is no lifecycle leak.

No code changes were required. A regression test suite (`panel/src/hooks/__tests__/use-tasks-null-guards.test.tsx`) verifies the enabled guards and the board-review poll behavior with fake timers.

### Using these hooks safely

When calling any id-driven hook, always pass the id from a verified source:

```tsx
import { useTask } from "@/hooks";

export function TaskDetail({ taskId }: { taskId: string | undefined }) {
  // The hook's `enabled` guard ensures no API call occurs when taskId is empty
  const { data, isLoading, error } = useTask(taskId);

  if (!taskId) return <p>No task selected</p>;
  if (isLoading) return <p>Loading...</p>;
  if (error) return <p>Error: {error.message}</p>;

  return <div>{data?.title}</div>;
}
```

No manual guard is needed before calling the hook — the `enabled: !!taskId` guard is built in and prevents wasted API calls and race conditions.

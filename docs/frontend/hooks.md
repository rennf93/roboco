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

## `useStalledTasks`

Returns the current stalled-task set — blocked tasks whose `blocker_resolver_type` is `human` (the dispatcher has given up and won't respawn the assigned agent). Fetched once and shared across every surface on a page via TanStack Query's cache, so the Overview "Stalled / Needs You" section, the Tasks page stalled filter, and the task-detail header stalled chip do not make three separate requests.

```tsx
import { useStalledTasks } from "@/hooks/use-dashboard";

const { data: stalledTasks, isLoading, isError, refetch } = useStalledTasks();
```

### Data source

The hook does not call a dedicated stalled endpoint. `dashboardApi.getStalledTasks()` fetches `GET /tasks/blocked` (which returns `TaskResponse[]`) and filters to `blocker_resolver_type === "human"`, returning `Task[]`. The stall classification is the backend's own — the frontend never re-derives it. See [`stalled-needs-you.md`](./stalled-needs-you.md) for the full feature contract.

### API reference

| Return | Type | Notes |
|--------|------|-------|
| `data` | `Task[] \| undefined` | The current stalled set. Empty array when nothing is stalled. |
| `isLoading` | `boolean` | First-fetch loading state. |
| `isError` | `boolean` | Fetch failed. Consumers render a distinct error state, never the empty state. |
| `refetch` | `function` | Re-runs the query. Registered with the page refresh coordinator on the Tasks page. |

- **Query key:** `["dashboard", "stalled-tasks"]` (`dashboardKeys.stalledTasks()`).
- **Refetch interval:** 60 seconds.
- **Mock mode:** returns `[]`.

### Consumer contract

All labels shown to the user (status, `blocker_resolver_type`, stalled membership) come verbatim from the backend response — no client-side relabeling or fabricated text. The only client-computed display value is the duration string in the Overview panel, which is display formatting over `task.updated_at`, not a stall condition.

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

## Mount-only effect audit

**Scope: this section is a frontend-only accounting.** It covers lint suppressions (`eslint-disable`, `@ts-ignore`, `@ts-expect-error`) found in `panel/` alone — it is not the company-wide suppression ledger. Backend's [`docs/backend/lint-suppression-reconciliation.md`](../backend/lint-suppression-reconciliation.md) is the canonical 32-item reconciliation (2 waived + 9 framework-exempt + 2 fixed-at-source + 19 already-resolved) covering every suppression Sentinel's `no_lint_suppressions` hygiene scan originally flagged company-wide; this single frontend `eslint-disable` is already accounted for there as item 12, and this section is the detailed narrative writeup for that one item.

A direct grep of `panel/` for `eslint-disable`, `@ts-ignore`, and `@ts-expect-error` (excluding `node_modules`) historically found exactly **one** frontend suppression, documented below. It was fixed at the source in a later round (see disposition below); re-running the same grep against `panel/` today returns zero hits.

### `eslint-disable` formerly in `journals-view.tsx`, now removed — fixed at the source, no waiver needed

Sentinel's `no_lint_suppressions` hygiene scan flagged `// eslint-disable-next-line react-hooks/exhaustive-deps` guarding the mount-only localStorage-restore effect in `JournalsViewContent` (`panel/src/components/journals/journals-view.tsx`). That effect restores the `agent`/`type`/`task` filters saved from a prior visit into the URL, but only on a fresh `/agents?tab=journals` visit that carries no query params yet — it must run exactly once per mount, never again, or it would clobber a later intentional "clear filters" action with stale saved state.

The suppression was removed by replacing the empty `[]` dependency array with a `useRef` mount-guard:

```tsx
const hasRestoredRef = useRef(false);
useEffect(() => {
  if (hasRestoredRef.current) return;
  hasRestoredRef.current = true;
  // ...restore-from-localStorage logic, reading urlAgentId/urlType/urlTask/searchParams/router
}, [urlAgentId, urlType, urlTask, searchParams, router]);
```

The ref guard, not the (now honest and complete) dependency array, is what enforces "exactly once per mount" — so `react-hooks/exhaustive-deps` is satisfied without changing the effect's actual behavior. This is the same idiom already used elsewhere in the panel for mount-only effects: `panel/src/components/scroll-restoration.tsx` (`hasRestored`) and `panel/src/components/a2a/a2a-transcript.tsx` (`hasScrolledRef`). Prefer this pattern over `eslint-disable` + `[]` for any new mount-only effect — it keeps the dependency array truthful for future maintainers while still only firing once.

A naive fix that just added `router`/`searchParams` to the deps array **without** the ref guard would have been wrong: those values change across every navigation, so the effect would re-run on every subsequent URL change and re-apply the stale saved filters over a user's later, intentional filter clear. A regression suite, `panel/src/components/journals/__tests__/journals-view.test.tsx`, locks in the correct behavior: the restore fires once on a fresh no-param visit, is skipped when the URL already carries params, and — the key regression guard — never re-fires after mount even once the URL round-trips through params and back to empty.

No entry was added to `.roboco/conventions.yml`'s `waivers:` list — the suppression was eliminated at the source, not exempted. (The backend cell's unrelated waiver entries already committed to that file were left untouched.)

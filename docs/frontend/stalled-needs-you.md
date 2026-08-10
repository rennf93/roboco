# Stalled / Needs You

The panel surfaces work the dispatcher has given up on across three locations, all driven by a single shared hook so no surface re-derives what "stalled" means client-side.

## What "stalled" means

A task is stalled when it is blocked **and** its `blocker_resolver_type` is `human` — the dispatcher's respawn breaker has stopped reviving the assigned agent and is waiting on a human to resolve the blocker. That classification is the backend's own; the frontend never computes it.

The shared hook fetches the stalled set from the existing blocked-tasks endpoint and filters it:

- **Endpoint:** `GET /tasks/blocked` → `TaskResponse[]`
- **Client filter:** `blocker_resolver_type === "human"`
- **Returns:** `Task[]` (the same `Task` type every other task surface uses — `task.id`, `task.title`, `task.assigned_to`, `task.status`, `task.blocker_resolver_type`, `task.updated_at`)

There is no dedicated stalled endpoint. An earlier plan to consume `GET /dashboard/stalled-tasks` was abandoned after the backend landed the stalled classification as `BlockerResolverType` (HUMAN vs AGENT) on the task model (PR #866) rather than as a separate route. `blocker_resolver_type` was added to the frontend `Task` type in `panel/src/types/index.ts` to carry it.

## The shared hook: `useStalledTasks`

```tsx
import { useStalledTasks } from "@/hooks/use-dashboard";

const { data: stalledTasks, isLoading, isError, refetch } = useStalledTasks();
```

| Return | Type | Notes |
|--------|------|-------|
| `data` | `Task[] \| undefined` | The current stalled set. Empty array when nothing is stalled. |
| `isLoading` | `boolean` | First-fetch loading state. |
| `isError` | `boolean` | Fetch failed. Surfaces render the distinct error state, never the empty state. |
| `refetch` | `function` | Re-runs the query. Registered with the page refresh coordinator. |

- **Query key:** `dashboardKeys.stalledTasks()` → `["dashboard", "stalled-tasks"]`. One fetch is shared across every surface on a page via TanStack Query's cache, so the Overview section, the Tasks filter, and the detail header on the same page do not make three requests.
- **Refetch interval:** 60 seconds.
- **API client:** `dashboardApi.getStalledTasks()` in `panel/src/lib/api/dashboard.ts`. In mock mode it returns `[]`.

All three surfaces below consume this one hook. Do not call `GET /tasks/blocked` directly from a component — use `useStalledTasks` so the cache and the verbatim-label contract stay centralized.

## Surface 1: Overview "Stalled / Needs You" section

Component: `StalledNeedsYouPanel` (`panel/src/components/dashboard/stalled-needs-you-panel.tsx`), wired into `CommandCenter` below the strategy-signals row.

Each row shows, sourced verbatim from the backend response:

| Field | Source | Display |
|-------|--------|---------|
| Title | `task.title` | Truncated single line. |
| Status | `task.status` | Outline badge, verbatim status string. |
| Assignee | `task.assigned_to` | Resolved to agent name via `useAgents`; falls back to the raw agent id. |
| Stall reason | `task.blocker_resolver_type` | Verbatim (`human`). |
| Duration stalled | `task.updated_at` | Display-only formatting (`< 1h`, `Nh`, `Nd`) — see note below. |

Every row is a `Link` to `/tasks/{task.id}` (the task detail page). A destructive badge in the header shows the count when the set is non-empty.

### State contract

The panel renders exactly one of four states, in this precedence:

1. **Error** (`isError`): a destructive-styled "Failed to load stalled tasks." block. This branch is checked **before** the empty-state branch, so a failed fetch can never render as "nothing is stalled".
2. **Loading** (`isLoading`): two `Skeleton` placeholders.
3. **Empty** (`data` is `[]`): an explicit "Nothing is stalled right now" message with a dimmed icon.
4. **Populated**: the row list.

### Duration is display-only

`formatStalledDuration(task.updated_at)` computes the elapsed time from the backend's `updated_at` timestamp purely for display. It is not a stall condition and does not decide what counts as stalled — that is `blocker_resolver_type=human` from the backend alone. A missing or future `updated_at` renders `"unknown"`.

## Surface 2: Tasks page stalled filter

The Tasks page (`panel/src/app/(dashboard)/tasks/page.tsx`) narrows the list to stalled tasks when `?stalled=1` is in the URL. The filter is wired through the page's **existing** URL filter-state mechanism — the same `useSearchParams` + `updateParams` pattern used by the status, team, type, project, and product filters. There is no second/parallel filter store.

- **URL param:** `stalled=1` to enable, absent (or `updateParams({ stalled: null })`) to clear.
- **Toggle:** the `TaskFilters` "Stalled" button (`panel/src/components/tasks/task-filters.tsx`) calls `onStalledChange`, which calls `handleStalledChange` → `updateParams`. It is `aria-pressed` and shows the live `stalledCount` (or a destructive icon when the fetch has errored). An active "Stalled" chip appears in the active-filters row with a clear control.
- **Filtering:** `stalledTaskIds` is a `Set` of stalled task ids built from `useStalledTasks().data`. The page's `filteredTasks` memo drops any task whose id is not in that set when `stalledFilter` is on.

### Error state on the Tasks page

When `stalledFilter` is on **and** the stalled fetch has errored, the `TaskTable` is withheld entirely and a destructive "Failed to load stalled tasks" message renders in its place. The filter logic also short-circuits (`stalledError` is checked alongside `stalledTaskIds.has`), so a failed fetch never collapses the list to a silently-empty table that looks indistinguishable from "no tasks match".

The stalled `refetch` is registered with the page's `usePageRefresh` callbacks alongside the task, project, and product refetches, so the navbar refresh button refreshes the stalled set too.

## Surface 3: Task detail header stalled chip

Component: `TaskHeader` (`panel/src/components/tasks/task-detail/task-header.tsx`).

The header calls `useStalledTasks()` and checks whether the viewed task's id is in the stalled set. When it is, a red "stalled" chip (with an `AlertOctagon` icon) renders next to the existing "bounced xN" chip. The chip's tooltip shows `stalledEntry.blocker_resolver_type` verbatim. The chip is absent when the task is not in the stalled set.

The stalled membership check is by id only (`t.id === task.id`), and the chip's label text comes straight from the backend response — no client-side re-derivation.

## Labels come verbatim from the backend

Status, stall reason (`blocker_resolver_type`), and stalled membership are all sourced directly from the backend `GET /tasks/blocked` response. The frontend does not relabel, translate, or fabricate any of these. The only client-computed display value is the duration string, which is explicitly display formatting over `updated_at`, not a stall condition.

## Tests

Component tests cover populated, empty, and error states for all three surfaces:

- `panel/src/components/dashboard/__tests__/stalled-needs-you-panel.test.tsx` — Overview section (populated asserts title/assignee/status/reason/duration + link href; empty asserts the empty-state text; error asserts the error text and that the empty-state text is absent).
- `panel/src/app/(dashboard)/tasks/__tests__/stalled-filter.test.tsx` — Tasks filter (populated narrows to the stalled set; empty backend set renders zero tasks not the unfiltered list; fetch failure renders the error message and withholds the table).
- `panel/src/components/tasks/task-detail/__tests__/task-header-stalled.test.tsx` — detail-header chip across the three states.

The error-state tests explicitly assert the empty-state text is absent, pinning the "error never renders as empty" contract.
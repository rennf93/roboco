# Stalled / Needs You

The panel surfaces work the dispatcher has given up on across three locations, all sourced from the backend's durable stalled marker so no surface re-derives what "stalled" means client-side.

## What "stalled" means

A task is stalled when the dispatcher's respawn breaker has given up reviving its assigned agent and is waiting on a human to resolve it - a durable marker on the task row, not a status.

- **Marker fields:** `tasks.stalled_reason` (why) and `tasks.stalled_since` (when), set by `TaskService.mark_stalled` at the exact point the orchestrator's respawn breaker (`AgentOrchestrator._pm_respawn_should_gate`) fires its one-shot CEO notification.
- **Cleared on genuine forward progress:** any status transition clears it unconditionally (`TaskService._clear_stale_stalled_marker`, called from the single `_emit_status_transition_audit` chokepoint every transition funnels through), and `TaskService.clear_stalled_marker` clears it directly on the dispatcher's own re-observation path.
- **Not status-restricted:** a stalled task can be `in_progress`, `blocked`, or any other non-terminal status - the marker is independent of `blocker_resolver_type`, which only classifies the `blocked` status itself. `TaskService.list_stalled_tasks` excludes only `completed`/`cancelled` tasks.

## The dedicated endpoint: `GET /dashboard/stalled-tasks`

Backed by `TaskService.list_stalled_tasks`, returning every task with a live `stalled_reason`, oldest-stalled-first. Response is `list[StalledTaskResponse]` (`roboco/api/schemas/dashboard.py`):

| Field | Type | Notes |
|-------|------|-------|
| `task_id` | UUID | |
| `title` | string | |
| `assignee_id` | UUID \| null | |
| `assignee_slug` | string \| null | Human-readable agent slug, already joined server-side - no extra agent lookup needed on the frontend. |
| `status` | string | The task's current status (any non-terminal value, not just `blocked`). |
| `reason` | string | `stalled_reason` verbatim. |
| `stalled_since` | datetime | |
| `stalled_seconds` | number | Server-computed elapsed time - the frontend never re-derives duration from a raw timestamp. |

The frontend `StalledTask` type (`panel/src/types/index.ts`) mirrors this field-for-field.

## The shared hook: `useStalledTasks`

```tsx
import { useStalledTasks } from "@/hooks/use-dashboard";

const { data: stalledTasks, isLoading, isError, refetch } = useStalledTasks();
```

| Return | Type | Notes |
|--------|------|-------|
| `data` | `StalledTask[] \| undefined` | The current stalled set. Empty array when nothing is stalled. |
| `isLoading` | `boolean` | First-fetch loading state. |
| `isError` | `boolean` | Fetch failed. Surfaces render the distinct error state, never the empty state. |
| `refetch` | `function` | Re-runs the query. Registered with the page refresh coordinator. |

- **Query key:** `dashboardKeys.stalledTasks()` → `["dashboard", "stalled-tasks"]`. One fetch is shared across every surface on a page via TanStack Query's cache.
- **Refetch interval:** 60 seconds.
- **API client:** `dashboardApi.getStalledTasks()` in `panel/src/lib/api/dashboard.ts` calls `GET /dashboard/stalled-tasks`. In mock mode it returns `[]`.

This hook backs the Overview section and the Tasks page filter (Surfaces 1 and 2 below). The task-detail header (Surface 3) does **not** use it - see that section.

## Surface 1: Overview "Stalled / Needs You" section

Component: `StalledNeedsYouPanel` (`panel/src/components/dashboard/stalled-needs-you-panel.tsx`), wired into `CommandCenter` below the strategy-signals row.

Each row shows, sourced verbatim from the backend response:

| Field | Source | Display |
|-------|--------|---------|
| Title | `task.title` | Truncated single line. |
| Status | `task.status` | Outline badge, verbatim status string. |
| Assignee | `task.assignee_slug`, falling back to `task.assignee_id` | Rendered as-is - no client-side agent lookup, the backend already joins the slug. |
| Stall reason | `task.reason` | Verbatim `stalled_reason`. |
| Duration stalled | `task.stalled_seconds` | Display-only formatting (`< 1h`, `Nh`, `Nd`) over the server-computed elapsed seconds. |

Every row is a `Link` to `/tasks/{task.task_id}` (the task detail page). A destructive badge in the header shows the count when the set is non-empty.

### State contract

The panel renders exactly one of four states, in this precedence:

1. **Error** (`isError`): a destructive-styled "Failed to load stalled tasks." block. This branch is checked **before** the empty-state branch, so a failed fetch can never render as "nothing is stalled".
2. **Loading** (`isLoading`): two `Skeleton` placeholders.
3. **Empty** (`data` is `[]`): an explicit "Nothing is stalled right now" message with a dimmed icon.
4. **Populated**: the row list.

### Duration is display-only

`formatStalledDuration(task.stalled_seconds)` converts the backend's own elapsed-seconds figure into a display string. It is not a stall condition and does not decide what counts as stalled - that is the `stalled_reason`/`stalled_since` marker on the backend alone.

## Surface 2: Tasks page stalled filter

The Tasks page (`panel/src/app/(dashboard)/tasks/page.tsx`) narrows the list to stalled tasks when `?stalled=1` is in the URL. The filter is wired through the page's **existing** URL filter-state mechanism — the same `useSearchParams` + `updateParams` pattern used by the status, team, type, project, and product filters. There is no second/parallel filter store.

- **URL param:** `stalled=1` to enable, absent (or `updateParams({ stalled: null })`) to clear.
- **Toggle:** the `TaskFilters` "Stalled" button (`panel/src/components/tasks/task-filters.tsx`) calls `onStalledChange`, which calls `handleStalledChange` → `updateParams`. It is `aria-pressed` and shows the live `stalledCount` (or a destructive icon when the fetch has errored). An active "Stalled" chip appears in the active-filters row with a clear control.
- **Filtering:** `stalledTaskIds` is a `Set` of `task_id`s built from `useStalledTasks().data`. The page's `filteredTasks` memo drops any task whose id is not in that set when `stalledFilter` is on.
- **Composes with the Status filter:** because the stalled set is not status-restricted (see "What 'stalled' means" above), `Stalled` + a specific `Status` (e.g. `In Progress`) correctly narrows to tasks matching both - a breaker-tripped `in_progress` task shows up under `Stalled` + `In Progress`, not just `Stalled` + `Blocked`.

### Error state on the Tasks page

When `stalledFilter` is on **and** the stalled fetch has errored, the `TaskTable` is withheld entirely and a destructive "Failed to load stalled tasks" message renders in its place. The filter logic also short-circuits (`stalledError` is checked alongside `stalledTaskIds.has`), so a failed fetch never collapses the list to a silently-empty table that looks indistinguishable from "no tasks match".

The stalled `refetch` is registered with the page's `usePageRefresh` callbacks alongside the task, project, and product refetches, so the navbar refresh button refreshes the stalled set too.

## Surface 3: Task detail header stalled chip

Component: `TaskHeader` (`panel/src/components/tasks/task-detail/task-header.tsx`).

Unlike Surfaces 1 and 2, the header does **not** call `useStalledTasks()`. The backend's `TaskResponse` (and the frontend `Task` type) already carries `stalled_reason`/`stalled_since` directly on every task fetch, so a second network round-trip to the list endpoint would be redundant. The header reads `task.stalled_reason` directly: when non-null, a red "stalled" chip (with an `AlertOctagon` icon) renders next to the existing "bounced xN" chip, and its tooltip shows `task.stalled_reason` verbatim. The chip is absent when `stalled_reason` is null.

## Labels come verbatim from the backend

Status, stall reason, assignee slug, and duration are all sourced directly from the backend. The frontend does not relabel, translate, or fabricate any of these; the only client-computed display value is the duration string formatted from `stalled_seconds`.

## Tests

- `panel/src/lib/api/__tests__/dashboard.test.ts` - the regression guard: exercises `dashboardApi.getStalledTasks()` against a mocked HTTP client and asserts it requests `GET /dashboard/stalled-tasks`. Every other test below mocks `@/lib/api/dashboard` wholesale and therefore never executes the fetcher itself - this is the one test that would have caught the endpoint drifting to the wrong route.
- `panel/src/components/dashboard/__tests__/stalled-needs-you-panel.test.tsx` - Overview section (populated asserts title/assignee/status/reason/duration + link href, including the assignee_id fallback when assignee_slug is null; empty asserts the empty-state text; error asserts the error text and that the empty-state text is absent).
- `panel/src/app/(dashboard)/tasks/__tests__/stalled-filter.test.tsx` — Tasks filter (populated narrows to the stalled set; empty backend set renders zero tasks not the unfiltered list; fetch failure renders the error message and withholds the table).
- `panel/src/components/tasks/task-detail/__tests__/task-header-stalled.test.tsx` - detail-header chip driven by `task.stalled_reason` directly (populated, null, and absent).

The error-state tests explicitly assert the empty-state text is absent, pinning the "error never renders as empty" contract.

# Task Detail Navigation & Timestamps

This guide documents three related UI features added to the task-detail page: inline absolute timestamps, parent-task breadcrumbs, and list-context-aware prev/next navigation.

## Inline Absolute Timestamps

**Location:** `panel/src/lib/utils.ts`, `formatAbsoluteTimestamp()`

All progress updates and checkpoints now display a standardized absolute timestamp (e.g., "Jul 10, 2026, 3:45 PM") **in addition to** the existing relative time text (e.g., "2m ago"). The absolute format is consistent across the panel, achieved via a shared helper that was extracted from the earlier `checkpoint-card.tsx` local implementation.

### Implementation

```typescript
// lib/utils.ts
export function formatAbsoluteTimestamp(timestamp: string): string {
  return new Date(timestamp).toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}
```

### Usage in Components

- **`tab-progress.tsx` ProgressUpdatesSection:** Each progress entry now shows relative time + `·` + absolute time on the same line, with the absolute time also in the `title` tooltip for hover clarity.
- **`tab-progress.tsx` CheckpointsSection:** Same treatment as progress updates.
- **`progress-timeline.tsx`:** In the timeline view, relative time + absolute time on the same line.
- **`checkpoint-card.tsx`:** The checkpoint component now uses the shared helper instead of its own format.

The relative time remains the primary visual — it's glanceable. The absolute time is the secondary, precise reference, useful when comparing timestamps across different time zones or when exact dates matter (e.g., "was this before or after June 30?").

## Parent Task Breadcrumb

**Location:** `panel/src/components/tasks/task-detail/task-breadcrumb.tsx`

When a task has a parent, a breadcrumb renders above the task title: `Parent Title > Child Title`. The breadcrumb is absent for root tasks (those with `parent_task_id = null`), keeping the UI clean for single-level work.

### Implementation Details

- Renders only if `task.parent_task_id` is set; otherwise returns `null`.
- Uses `useTask(parentId)` to fetch the parent task data (title + styling).
- While loading, a skeleton placeholder avoids layout shift.
- The parent title is a clickable link to `/tasks/{parent.id}`.
- Long titles are truncated with a `title` attribute tooltip.
- Only the immediate parent is shown — deeper ancestry is reachable by following the chain one hop at a time, matching how the rest of the panel represents task hierarchy.

### Example

If Task B is a child of Task A:
- Viewing Task B shows: `Task A > Task B`
- Clicking "Task A" navigates to that parent
- If Task A also has a parent, viewing Task A shows that grandparent, not Task B

This recursive, one-level-at-a-time model prevents breadcrumbs from becoming unwieldy and mirrors the panel's task-tree representation elsewhere.

## Prev/Next Task Navigation

**Location:** `panel/src/components/tasks/task-detail/task-list-nav.tsx`

Two chevron buttons (← →) on the task-detail page move to the adjacent task **within the current Tasks list filter/sort context**. This preserves the user's list view experience: if they filtered by status, sorted by priority, or searched for a term, those same filters apply when they navigate prev/next.

### How It Works

1. **List context capture:** The Tasks page (`tasks/page.tsx`) passes the currently visible task IDs (id + title) to `useScrollRestorationStore.setTaskListNav()` whenever the table's filtered/sorted order changes. The context includes the full query string (filters, search, sort) so a "Back to list" link can restore the exact view.

2. **Session-scoped state:** The context lives in `sessionStorage` via Zustand's persist middleware. It survives navigation within the same browser session but is cleared if the browser is closed. This matches the "come back where you left off" UX without persisting across sessions.

3. **Fallback when no context:** If the user navigates to a task via a direct link, search result, notification, or any path that doesn't pass through the Tasks list, `taskListNav` is `null`. Both buttons render **disabled** with a tooltip explaining: "Open this task from the Tasks list to enable prev/next navigation within that list's filter/sort order." This is the **documented fallback** — no guessing, no silent behavior change.

4. **Edge cases:**
   - **First item in list:** Prev button is disabled; next is enabled.
   - **Last item in list:** Next button is disabled; prev is enabled.
   - **Task not in captured list:** Both buttons are disabled (task was navigated to via another route after the list was visited).
   - **Query string preservation:** The href for each nav link includes the original query string, so navigating back to the Tasks page from a nested task restores the same filters.

### Components

**`TaskListNav`:** The main export. Reads `taskListNav` from the store, computes prev/next items, and renders two `NavButton` children.

**`NavButton`:** A single direction button. If enabled, it wraps a Next.js `<Link>`; if disabled, it's a plain button with a tooltip explaining why. The tooltip shows either the next task's title (when enabled) or the reason it's disabled.

### Implementation Example

```typescript
export function TaskListNav({ task }: TaskListNavProps) {
  const context = useScrollRestorationStore((state) => state.taskListNav);

  const items = context?.items ?? [];
  const index = items.findIndex((item) => item.id === task.id);
  const hasContext = index !== -1;
  const prevItem = hasContext && index > 0 ? items[index - 1] : null;
  const nextItem =
    hasContext && index < items.length - 1 ? items[index + 1] : null;

  const query = context?.queryString ? `?${context.queryString}` : "";
  // Render NavButton for each direction with the computed item and query
}
```

## Store Extension

**Location:** `panel/src/lib/stores/scroll-restoration-store.ts`

The `useScrollRestorationStore` (already managing scroll positions, section expansions, etc.) now includes:

- **`taskListNav: TaskListNavContext | null`** — The current task list context, or `null` if no list has been visited.
- **`setTaskListNav(context: TaskListNavContext)`** — Updates the context when the Tasks table reports a new visible order.

### Schema

```typescript
export interface TaskListNavItem {
  id: string;
  title: string;
}

export interface TaskListNavContext {
  items: TaskListNavItem[];
  queryString: string; // e.g., "status=in_progress&sort=-created_at"
}
```

The store uses `sessionStorage` persistence, so state is automatically restored on page reload within the same session but is cleared when the session ends.

## Integration Points

### Tasks List Page (`tasks/page.tsx`)

Calls `setTaskListNav()` whenever the table's visible order changes:

```typescript
const setTaskListNav = useScrollRestorationStore((state) => state.setTaskListNav);
const handleVisibleOrderChange = useCallback(
  (items: { id: string; title: string }[]) => {
    setTaskListNav({ items, queryString: searchParamsString });
  },
  [setTaskListNav, searchParamsString],
);
```

### TaskTable (`components/tasks/task-table.tsx`)

Now accepts an optional `onVisibleOrderChange` callback, fired whenever the computed visible task order changes (e.g., on filter, sort, pagination, or expansion/collapse). The Tasks page wires this callback to capture the order.

### Task Detail Page (`[taskId]/page.tsx`)

Renders both `TaskBreadcrumb` and `TaskListNav` near the top of the page, in a flex row:

```tsx
<div className="flex items-center justify-between gap-4">
  <TaskBreadcrumb task={task} />
  <TaskListNav task={task} />
</div>
```

The breadcrumb takes the left; nav buttons take the right, preserving the layout for long parent titles.

## Design Considerations

### Why relative + absolute times?

Relative times ("2m ago") are glanceable but ambiguous across time zones and when comparing entries hours or days apart. Absolute times are precise but verbose if shown alone. Combining them gives quick context (relative) + accuracy (absolute) without sacrificing space.

### Why session-scoped, not persistent?

Task lists reflect current filters, which change frequently. Persisting a stale list order across sessions (e.g., "I opened this task from a filtered search yesterday, but I reopened the browser and the filter is gone") would silently navigate to the wrong next task. Session scope keeps the invariant: prev/next only works within the current session's list context.

### Why disable instead of guess?

A task opened via direct link, notification, or external system doesn't have a parent list context. We could try to infer one (e.g., "show all tasks" or "show tasks in this project"), but that's invisible magic — the user wouldn't know why they're seeing a particular next task. Disabling with a clear explanation ("open from the list to enable this") is explicit and honest.

## Future Extensions

Possible enhancements:

1. **Remember list context across sessions:** A "pin this list" feature could persist context across browser sessions.
2. **Breadcrumb depth:** Allow showing full ancestry (A > B > C) instead of one level; would require more space and careful truncation.
3. **Recently viewed tasks:** A dropdown of tasks you've visited recently, separate from the list context.
4. **Keyboard shortcuts:** Arrow keys to navigate prev/next when the buttons are enabled.

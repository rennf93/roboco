# A2A Conversations Filter Control

**Location:** `panel/src/components/a2a/` **Design spec:** `docs/ux_ui/design/conversations-filter-control.md` **Related:** A2A page (`panel/src/app/(dashboard)/a2a/page.tsx`)

## Overview

The A2A filter control provides a multi-dimension Popover-triggered filter panel for narrowing down agent-to-agent conversations by Agent, Task, Status, and Date range. It replaces the previous free-text search + status toggle and applies different filter rules to the Switchboard (org-chart pairs) and Conversation List (message feeds) views per the design spec's per-view rules.

**Key principle:** The same filter state manages both views, but which dimensions apply depends on the active view—Switchboard pairs only narrow by Agent (since they may have no conversation), while the Conversation List applies all four dimensions.

## Component API

### `A2AFilterBar`

**Path:** `panel/src/components/a2a/a2a-filter-bar.tsx`

```typescript
interface A2AFilterBarProps {
  filters: A2AFilters;
  onFiltersChange: (filters: A2AFilters) => void;
  agentOptions: string[];
  view: "switchboard" | "list";
}
```

**Props:**
- `filters` — The current filter state (see `A2AFilters` below).
- `onFiltersChange` — Callback fired on any filter change; receives the entire updated filter object.
- `agentOptions` — Distinct agent slugs to display in the Agent checkbox list, already deduped and sorted. Derive this via `distinctA2AAgents(conversations, pairs)` in the parent component.
- `view` — The active view mode. When `"switchboard"`, an inline note reminds that Task/Status/Date only apply to the List view.

**Rendered output:**
- **Collapsed (no filters):** A compact trigger button labeled `Filters` with a funnel icon.
- **With active filters:** The trigger shows a count badge (`Filters · N`).
- **Expanded:** A `Popover` displaying all four filter dimensions stacked vertically, plus an inline note if in Switchboard view.
- **Chip row:** Below the header, a wrapping row of `Badge` chips—one per active filter value—with individual remove `X` buttons and a shared `Clear all` button. Only rendered when >=1 filter is active.

## Filter State

### `A2AFilters`

**Path:** `panel/src/components/a2a/a2a-filter-utils.ts`

```typescript
interface A2AFilters {
  agents: string[];              // Selected agent slugs
  taskIdFragment: string;        // Free-text fragment to match against task_id
  noLinkedTask: boolean;         // Match conversations with task_id === null
  statuses: A2AConversationStatus[]; // "active" | "archived"
  dateFrom: string;              // YYYY-MM-DD or ""
  dateTo: string;                // YYYY-MM-DD or ""
}
```

**Empty state:** `EMPTY_A2A_FILTERS` = all fields empty or falsy.

## Filter Dimensions

Each dimension narrows the loaded conversations or pairs via a pure matcher function in `a2a-filter-utils.ts`.

### 1. Agent (applies to both views)

- **Source:** `agent_a` and `agent_b` fields on `AdminConversationSummary` / `AdminPairSummary`.
- **Match logic:** A conversation/pair matches if **either** participant is in the selected `agents` array.
- **Widget:** Checkbox list in the Popover, with a `Clear` button when >=1 agent is selected.
- **Empty behavior:** If `agents.length === 0`, all items pass (no agent filter applied).

```typescript
// Example: agents = ["be-dev-1"]
// Match: conversation with agent_a="be-dev-1" agent_b="be-qa" ✓
// Match: conversation with agent_a="ux-dev-1" agent_b="be-dev-1" ✓
// No match: conversation with agent_a="ux-dev-1" agent_b="ux-qa" ✗
```

**Switchboard only:** Pairs are narrowed by Agent alone (design doc §1 "Per-view applicability").

### 2. Task (List view only)

Combines two controls for maximum flexibility:

- **Task ID fragment input:** Free-text match against the full `task_id` (case-insensitive). Displayed as a single chip labeled `Task: <fragment>` when set.
- **"No linked task" toggle:** Matches conversations with `task_id === null`. Displayed as a separate chip when active.

**Match logic:**
```
IF (fragment is empty AND noLinkedTask is false)
  PASS (no task filter)
ELSE
  PASS if (fragment matches task_id) OR (noLinkedTask is true AND task_id is null)
```

In other words: **if both controls are empty, no filtering; if one or both are set, match conversations that satisfy either condition (OR logic).**

```typescript
// Example 1: taskIdFragment="abcdef", noLinkedTask=false
// Match: task_id="abcdef01-0000-..." ✓
// No match: task_id="ffffffff-0000-..." ✗
// No match: task_id=null ✗

// Example 2: taskIdFragment="", noLinkedTask=true
// No match: task_id="abcdef01-0000-..." ✗
// Match: task_id=null ✓

// Example 3: taskIdFragment="abcdef", noLinkedTask=true
// Match: task_id="abcdef01-0000-..." ✓
// No match: task_id="ffffffff-0000-..." ✗
// Match: task_id=null ✓
```

**Switchboard:** This dimension does not apply (pairs may have no conversation to check a task against).

### 3. Status (List view only)

- **Source:** `status` field on `AdminConversationSummary` (values: `"active"` | `"archived"`).
- **Widget:** Two toggle buttons (Active / Archived) with `aria-pressed`.
- **Match logic:** A conversation matches if its `status` is in the selected `statuses` array.
- **Empty behavior:** If `statuses.length === 0`, all items pass (no status filter applied).

```typescript
// Example: statuses = ["active"]
// Match: conversation with status="active" ✓
// No match: conversation with status="archived" ✗
```

**Switchboard:** This dimension does not apply.

### 4. Date range (List view only)

- **Source:** `last_message_at` field on `AdminConversationSummary`, falling back to `created_at` if null (same fallback the list already uses for display).
- **Widget:** Two native `<input type="date">` fields (From / To), in the viewer's local timezone.
- **Match logic:** Both dates are compared at day granularity. A conversation matches if:
  ```
  IF (dateFrom is empty AND dateTo is empty)
    PASS (no date filter)
  ELSE IF (timestamp is null/empty)
    FAIL
  ELSE
    PASS if (timestamp >= dateFrom) AND (timestamp <= dateTo)
  ```
- **Rendered chips:** One chip per set date (`From <date>` / `To <date>`), independently removable.

```typescript
// Example: dateFrom="2026-07-01", dateTo="2026-07-05"
// Match: last_message_at="2026-07-03T10:30:00Z" ✓
// No match: last_message_at="2026-07-10T10:30:00Z" ✗
// No match: last_message_at=null (falls back to created_at if null) [depends on created_at]
```

**Switchboard:** This dimension does not apply.

## Usage in Components

### Parent Setup

In the parent component (e.g., `A2APage`), wire the filter state and compute the agent options:

```typescript
import { A2AFilterBar } from "@/components/a2a/a2a-filter-bar";
import {
  EMPTY_A2A_FILTERS,
  distinctA2AAgents,
  filterConversations,
  filterPairs,
  type A2AFilters,
} from "@/components/a2a/a2a-filter-utils";

function A2APageContent() {
  const [filters, setFilters] = useState<A2AFilters>(EMPTY_A2A_FILTERS);

  // Derive agent options from the loaded data
  const agentOptions = useMemo(
    () => distinctA2AAgents(conversations, pairs),
    [conversations, pairs]
  );

  // Apply filters to both views
  const filteredPairs = useMemo(
    () => filterPairs(pairs, filters),
    [pairs, filters]
  );

  const filteredConversations = useMemo(
    () => filterConversations(conversations, filters),
    [conversations, filters]
  );

  return (
    <>
      <A2AFilterBar
        filters={filters}
        onFiltersChange={setFilters}
        agentOptions={agentOptions}
        view={view} // "switchboard" or "list"
      />

      {view === "switchboard" ? (
        <A2ASwitchboard pairs={filteredPairs} />
      ) : (
        <A2AConversationList conversations={filteredConversations} />
      )}
    </>
  );
}
```

### Filter Functions

**`filterConversations(conversations, filters): AdminConversationSummary[]`** Applies all four dimensions (Agent, Task, Status, Date) to narrow the conversation list.

**`filterPairs(pairs, filters): AdminPairSummary[]`** Applies Agent dimension only to narrow switchboard pair cards.

**`distinctA2AAgents(conversations, pairs): string[]`** Derives the checkbox option set by scanning all loaded pairs and conversations, deduping agent slugs, and sorting alphabetically. Call this in a `useMemo` in the parent whenever pairs/conversations change.

**`activeA2AFilterCount(filters): number`** Returns the count of active filter values (one per chip). Drives the trigger's count badge. An empty fragment/date counts as 0; a set date counts as 1 per date.

## Per-View Rules

**This is critical:** Different dimensions apply depending on which view is active.

| Dimension | Switchboard (pairs) | List (conversations) |
|-----------|---------------------|----------------------|
| Agent | ✓ (always applies) | ✓ |
| Task | ✗ (N/A—pairs may have no conversation) | ✓ |
| Status | ✗ | ✓ |
| Date | ✗ | ✓ |

**Switchboard hint:** When the view is `"switchboard"`, the Popover displays an inline note: *"Task, Status, and Date filters apply to the Conversation List view."* This prepares the user if they set those filters before switching to List.

## Design Notes

### Client-Side Filtering

**Important limitation:** Filtering currently runs client-side over the already-fetched page of conversations/pairs (capped at `limit=100` from the backend). There are **no backend query params** for these dimensions yet.

**Implication:** If the loaded data doesn't contain a matching item, the filter won't find it. This is acceptable for the current conversation volume and is explicitly noted in the design spec as "Future work."

**Future task:** A later PR will add backend query params (`agent`, `task_id`, `status`, `from`, `to`) to `GET /a2a/chat/admin/conversations` so filtering can work server-side without this limitation.

### Persistence

Filter state is **local to the page component** (`useState`), not persisted to localStorage or the URL. Reloading the page resets filters to empty. This is intentional and consistent with the page's existing behavior (no search persistence today).

### Debouncing

The Task ID fragment input does **not** debounce; it updates the filter state on every keystroke. For a small dataset (100 conversations) this is fine. If performance becomes an issue with larger datasets, add a 300ms debounce in the parent using `useCallback` + `useRef` on the `onFiltersChange` callback.

## Testing

### Unit Tests

**Component tests:** `panel/src/components/a2a/__tests__/a2a-filter-bar.test.tsx`
- Trigger button rendering (collapsed and with badge count)
- Popover open/close and focus management
- Agent checkbox toggling and per-dimension clearing
- Task input and "No linked task" toggle
- Status button toggling
- Date input binding
- Chip row rendering (one chip per active value)
- Chip `X` button removing individual filters
- Clear all button resetting everything
- Switchboard view hint message

**Utility tests:** `panel/src/components/a2a/__tests__/a2a-filter-utils.test.ts`
- `filterConversations`: all four dimensions, combinations
- `filterPairs`: Agent dimension only
- `distinctA2AAgents`: dedup + sort correctness
- `activeA2AFilterCount`: count logic per dimension
- Edge cases: empty data, null values, case-insensitivity

### Integration Tests

`panel/src/app/(dashboard)/a2a/__tests__/page.test.tsx` includes:
- Rendering the filter trigger in the page header
- Switching views and filtering by Agent in Switchboard
- Switching to List and filtering by Agent, Task ID, and Status

## Accessibility

The component follows the design spec's accessibility contract:

- **Trigger button:** `aria-expanded` reflects Popover open state, `aria-haspopup="dialog"`.
- **Checkboxes:** Standard HTML `<label>` + `<Checkbox>` pair; the whole row is a hit target.
- **Status toggle buttons:** Real `<button>` with `aria-pressed`, not `<div onClick>`.
- **Date inputs:** Native `<input type="date">` with full keyboard support.
- **Chip remove buttons:** Icon-only, with `aria-label="Remove <chip label> filter"`.
- **Focus management:** Radix `Popover` handles focus trap and escape-to-close.
- **Contrast:** All color pairs are existing shadcn/ui tokens already in production, meeting WCAG AA (4.5:1 minimum).

## Related Files

- **Component:** `panel/src/components/a2a/a2a-filter-bar.tsx`
- **Utilities:** `panel/src/components/a2a/a2a-filter-utils.ts`
- **Tests:** `a2a-filter-bar.test.tsx`, `a2a-filter-utils.test.ts`
- **Page integration:** `panel/src/app/(dashboard)/a2a/page.tsx`
- **Design spec:** `docs/ux_ui/design/conversations-filter-control.md`
- **Related component (reference pattern):** `panel/src/components/tasks/task-filters.tsx` (the Popover + Checkbox + Badge-chip idiom this one mirrors)

## Common Questions

**Q: Why doesn't the fragment search the topic field anymore?** A: The design spec replaced free-text search with four discrete dimensions. Task ID fragment and Agent are the most common filters; if you need to search topics, that would be a fifth dimension—raise it in design review if needed.

**Q: Can I make filters persist across page reloads?** A: Not in this version. To add localStorage persistence, wrap `setFilters` in the parent with `useEffect` to sync to localStorage and restore on mount. This would be a follow-up task.

**Q: What happens to filtered state when new conversations arrive via WebSocket?** A: Filters remain active. The `filterConversations` function is re-run against the refreshed list on every data update, so incoming messages are immediately re-evaluated against the current filters.

**Q: Can filters be set via URL query params?** A: Not currently. The state is ephemeral. To support deep-linking (e.g., `?agents=be-dev-1&status=active`), add a URL param sync layer in the parent using `useSearchParams` / `useRouter` from Next.js. This would be a follow-up task.

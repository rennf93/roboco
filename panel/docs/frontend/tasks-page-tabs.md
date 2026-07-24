# Tasks Page: List & Kanban Tabs

## Overview

The Tasks page (`panel/src/app/(dashboard)/tasks/page.tsx`) now provides two complementary views of the task backlog: **List** and **Kanban**, accessible via top-level tabs. Both tabs share a unified URL-driven filter state, allowing users to switch between views without losing their current filters or search query.

## Tab Structure

### Top-Level Tabs: List | Kanban

- **List**: The traditional table view with TaskFilters and TaskTable. This is the default view and renders the original tasks page content byte-for-byte, just wrapped inside a `TabsContent` element.
- **Kanban**: Embeds four kanban workflow views (Developer, QA, PR Review, PM) as nested sub-tabs, mirroring the structure of the standalone `/kanban` page.

### Kanban Sub-Tabs

When viewing the Kanban tab, users can switch between four workflow-specific kanban boards via sub-tabs:

1. **Developer** – Tasks claimed and worked by developers, backlog through completion (Dev lifecycle)
2. **QA** – Quality assurance review workflow (QA review gate)
3. **PR Review** – In-path PR-review gate for assembled PRs before the PM merges
4. **PM** – Project management overview covering every lifecycle state, including recovery states

Each sub-tab includes a tooltip (hover-friendly on desktop) explaining its scope.

## URL-Driven State

### Query Parameters

The tabs and kanban view are completely URL-driven. The page reads and writes two query parameters:

- **`tab`**: `"list"` (default) or `"kanban"`
  - Omitting the `tab` parameter defaults to the List view
  - Setting `?tab=kanban` shows the Kanban tab

- **`view`**: `"dev"` (default), `"qa"`, `"pr-review"`, or `"pm"` (only meaningful when `tab=kanban`)
  - Omitting the `view` parameter defaults to the Developer kanban
  - Setting `?view=qa` shows the QA kanban board

### Filter State Persistence

All existing filters—search query (`q`), status, team, task type, project, product, sort field, sort direction, pagination, and expanded rows—are preserved as URL parameters and **shared across both tabs**. Switching tabs does not clear filters; users can switch between List and Kanban while maintaining their active filters.

**Example URLs:**
- `/tasks?q=auth&tab=list` – List view filtered by "auth" search
- `/tasks?q=auth&tab=kanban&view=qa` – QA kanban filtered by "auth" search
- `/tasks?status=pending&team=backend&tab=kanban&view=dev` – Dev kanban, pending tasks, backend team only

## Shared Kanban Team Filter

The kanban views—DevKanban, QaKanban, PrReviewKanban, and PmKanban—support **single-team selection**, while the List tab's TaskFilters supports **multi-select team filtering**.

To bridge this difference:

- When exactly one team is selected in the List tab, that team is passed to the active kanban view
- When no team or multiple teams are selected, the kanban view shows "All Teams" in its dropdown
- Changing the team in either tab writes the same `team` URL parameter, so changes sync across both views

This ensures the kanban view's team selector always reflects the current filter state, even though it can only show one team at a time.

## Component Integration

### Imports

The page imports the four kanban view components directly:

```typescript
import {
  DevKanban,
  QaKanban,
  PrReviewKanban,
  PmKanban,
} from "@/components/kanban";
```

### Embedding Pattern

Each kanban view is embedded inside a `TabsContent` and receives two props:

```typescript
<TabsContent value="dev" className="mt-6">
  <DevKanban
    team={sharedKanbanTeam}
    onTeamChange={handleKanbanTeamChange}
  />
</TabsContent>
```

**Props:**
- `team`: The currently selected team (or `undefined` if no team or multiple teams are selected)
- `onTeamChange`: Callback to update the team filter in the parent page's URL state

### Kanban View Modifications

Each kanban view wrapper (DevKanban, QaKanban, PrReviewKanban, PmKanban) was updated to support **optional controlled team state**:

- **Controlled (when embedded on tasks page)**: If `onTeamChange` is provided, the view is controlled by the parent's URL-driven state
- **Uncontrolled (when used standalone on /kanban page)**: If `onTeamChange` is omitted, the view manages its own internal team state via `useState`

This backward-compatible pattern allows the kanban views to be reused independently without modification.

## Implementation Details

### Tab Selection via `pickTab()`

The page uses the `pickTab()` helper from `@/lib/tabs` to safely parse and validate tab/view parameters:

```typescript
const activeTab = pickTab(searchParams.get("tab"), TASKS_VIEW_TABS, "list");
const kanbanView = pickTab(searchParams.get("view"), KANBAN_VIEWS, "dev");
```

This ensures invalid or missing values default to sensible defaults.

### Handlers

Three handler functions manage tab and filter changes:

- `handleTabChange(value)` – Updates the `tab` URL param when the user clicks List or Kanban
- `handleKanbanViewChange(value)` – Updates the `view` URL param when the user switches kanban sub-tabs
- `handleKanbanTeamChange(team)` – Updates the `team` URL param when the user changes the kanban team dropdown

All handlers use the existing `updateParams()` callback, which safely merges changes into the current query string and navigates without resetting scroll.

### Drag-and-Drop & Mobile Navigation

The kanban views' drag-and-drop functionality (via `@dnd-kit/core`) and mobile single-column navigation remain completely unchanged. These features are owned by `KanbanBoard` and are not affected by the tab refactor.

## Deep Linking

Deep links to `/tasks/[taskId]` work regardless of which tab (List or Kanban) is currently active. The page's task-list navigation (managed via `useScrollRestorationStore`) captures the current filtered/sorted order whenever the visible task list changes, enabling "prev/next" navigation in the task detail panel to work consistently across both tabs.

## Accessibility & UX Notes

- **Tooltips on Kanban Triggers**: Each kanban sub-tab trigger is wrapped in a Tooltip (via Radix UI) with a brief explanation of that workflow's scope. This helps users understand what each kanban board represents.
- **Data-State Override**: The Tooltip's `asChild` slot can interfere with TabsTrigger's `data-state` attribute. The code explicitly re-asserts `data-[state=active]` styling to ensure the active indicator works correctly despite the Tooltip wrapper.
- **Sticky Filters**: The TaskFilters bar in the List tab remains sticky (`position: sticky; top: 0`), providing a consistent filtering experience as users scroll through the task table.

## Testing Considerations

When testing the tasks page:

1. Verify that filters persist when switching between List and Kanban tabs
2. Confirm that the kanban team selector shows the correct team when exactly one team is selected
3. Test that deep links to task details work from both List and Kanban views
4. Verify that kanban drag-and-drop works correctly when accessed via the tasks page (not just the standalone `/kanban` route)
5. Confirm that mobile single-column kanban navigation works as expected
6. Test that URL parameters are correctly read and written (e.g., `?tab=kanban&view=qa`)

## Related Pages

- **Standalone Kanban Page** (`panel/src/app/(dashboard)/kanban/page.tsx`) – Provides an alternative kanban-only experience (Stream3-B will redirect and unify navigation)
- **Task Detail Panel** – Remains unchanged; deep links and prev/next navigation continue to work across all views
- **Task Filters** (`panel/src/components/tasks`) – Shared filter component used by the List tab

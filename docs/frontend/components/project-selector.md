# ProjectSelector component

A reusable dropdown selector for picking a project, with optional filtering by team and video-engine opt-in status.

## Purpose

Panels and dialogs that need to let users pick a project use `ProjectSelector` to display and filter the list. The component fetches the project list via the `useProjects` hook, groups projects by their assigned team, and optionally filters by team membership or video-engine enablement.

## Files

| File | Role |
|------|------|
| `panel/src/components/projects/project-selector.tsx` | Main component and `ProjectSelectorProps` type. |
| `panel/src/components/projects/__tests__/project-selector.test.tsx` | Component tests (filter, grouping). |
| `panel/src/lib/api/projects.ts` | `useProjects` hook and mock-mode project data. |
| `panel/src/types/index.ts` | `ProjectSummary` type (includes `video_engine_enabled`). |

## API

### `ProjectSelectorProps`

```typescript
interface ProjectSelectorProps {
  // The currently selected project ID (null for unselected)
  value: string | null;
  // Called when the user selects a project
  onChange: (projectId: string | null) => void;
  // Placeholder text shown when no project is selected
  placeholder?: string;
  // Filter to projects assigned to a specific team (optional)
  filterByTeam?: Team;
  // Disable the selector (optional, default: false)
  disabled?: boolean;
  // Whether to clear the selection when the user deselects (optional, default: true)
  allowClear?: boolean;
  // Restrict the list to projects with video_engine_enabled = true (optional, default: false)
  videoEngineOnly?: boolean;
}
```

**Props explained:**

- `value` — the currently selected project's ID, or `null` if unselected. The selector updates this via `onChange`.
- `onChange` — callback fired when the user selects a project (receives the project ID) or clears the selection (receives `null` if `allowClear` is true).
- `placeholder` — text shown in the trigger button when `value` is `null`. Defaults to "Select a project...".
- `filterByTeam` — if set, only show projects assigned to this team (e.g. `Team.FRONTEND`). Useful when an operation is scoped to a single team.
- `disabled` — if true, the dropdown is unclickable and grayed out.
- `allowClear` — if true (default), the user can click a clear button to set `value` to `null` and trigger `onChange(null)`. If false, a selection is mandatory.
- `videoEngineOnly` — if true, filter the project list to only projects with `video_engine_enabled === true`. Used by video-authoring flows (e.g. `RequestVideoDialog`) to show only opted-in projects.

### `ProjectSummary`

The shape of each project in the list:

```typescript
interface ProjectSummary {
  id: string;
  name: string;
  slug: string;
  git_url: string;
  assigned_cell: Team;
  is_active: boolean;
  has_workspace: boolean;
  has_git_token: boolean;
  video_engine_enabled: boolean;
}
```

- `video_engine_enabled` — whether the project has opted into the video engine. Used by the `videoEngineOnly` filter.

## How to use

### Basic usage

Pick any project in the list:

```tsx
"use client";

import { useState } from "react";
import { ProjectSelector } from "@/components/projects/project-selector";

export default function MyComponent() {
  const [projectId, setProjectId] = useState<string | null>(null);

  return (
    <div>
      <ProjectSelector
        value={projectId}
        onChange={setProjectId}
        placeholder="Select a project..."
      />
      {projectId && <p>You picked: {projectId}</p>}
    </div>
  );
}
```

### Filter by team

Show only projects in a specific team:

```tsx
import { Team } from "@/types";

<ProjectSelector
  value={projectId}
  onChange={setProjectId}
  filterByTeam={Team.FRONTEND}
  placeholder="Select a frontend project..."
/>
```

### Filter by video-engine opt-in

Show only projects that have opted into the video engine (for video-authoring dialogs):

```tsx
<ProjectSelector
  value={projectId}
  onChange={setProjectId}
  videoEngineOnly
  placeholder="Select a project with video enabled..."
/>
```

### Mandatory selection

Disable the clear button so the user must pick a project:

```tsx
<ProjectSelector
  value={projectId}
  onChange={setProjectId}
  allowClear={false}
  placeholder="Project (required)"
/>
```

### Combine filters

Both team and video-engine filters can be used together:

```tsx
<ProjectSelector
  value={projectId}
  onChange={setProjectId}
  filterByTeam={Team.BACKEND}
  videoEngineOnly
  placeholder="Backend projects with video enabled..."
/>
```

## Filtering order

Filters are applied in this order:

1. **Video-engine filter** (if `videoEngineOnly` is true) — keep only projects with `video_engine_enabled === true`.
2. **Team filter** (if `filterByTeam` is set) — keep only projects assigned to the given team.
3. **Grouping** — group the remaining projects by their assigned team for display.

This ensures a video-engine-filtered list still groups correctly, and a team-filtered list can still have the video-engine filter applied on top.

## Empty states

The component handles empty states gracefully:

- **No projects in the data source** — the dropdown shows the placeholder and is disabled.
- **All projects filtered out** — the dropdown shows the placeholder and is disabled. This happens when all available projects are filtered by `videoEngineOnly` or `filterByTeam`.
- **No projects with video enabled** (when `videoEngineOnly` is true) — a calling dialog (like `RequestVideoDialog`) should check `videoProjects.length` and show a friendly empty-state message to the user.

## Loading state

The selector uses the `useProjects` hook, which may return `isLoading: true` while fetching the project list. The selector passes this through as a `disabled` state until the data arrives.

## Design decisions

- **Radix UI Select**: built on Radix's `<Select>` primitive for accessibility (keyboard navigation, ARIA labels).
- **TanStack Query caching**: projects are fetched once via `useProjects` and cached, so multiple selectors on the same page share the same query.
- **Usable without TypeScript**: the component type-checks all inputs but can be used from plain JS contexts (e.g. test fixtures) by omitting types.
- **Memoized grouping**: the grouping computation is memoized with a dependency array so re-renders don't re-compute groups unless the filter props change.

## Testing

Run the selector tests with:

```bash
cd panel
pnpm test project-selector
```

Covered behaviors:

- `videoEngineOnly` filters out projects with `video_engine_enabled === false`.
- Without `videoEngineOnly`, all projects appear in the list.
- Team filtering works independently and in combination with video-engine filtering.
- Selecting a project calls `onChange` with the project ID.
- Clearing a selection (when `allowClear` is true) calls `onChange(null)`.
- The selector is disabled when `disabled` prop is true or while data is loading.
- The selector is disabled when all projects are filtered out.

## Related work

- **RequestVideoDialog** (`panel/src/components/dashboard/video-post-queue.tsx`) — uses `videoEngineOnly` to show only projects that have opted into the video engine for on-demand video requests.
- **ProjectsPage** (`app/(dashboard)/projects/page.tsx`) — uses the base selector without filters to list and manage all projects.

## Migration / rollout

No breaking changes. The new `videoEngineOnly` prop defaults to `false`, so existing uses of `ProjectSelector` are unaffected. New code that needs to filter by video-engine opt-in status should pass `videoEngineOnly={true}`.

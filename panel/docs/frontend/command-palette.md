# Command Palette Reference

The global command palette enables keyboard-driven navigation across the entire RoboCo panel. Press **Cmd+K** (Mac) or **Ctrl+K** (Windows/Linux) from any page to open it, then fuzzy-search tasks, agents, projects, and navigation pages. When the input is empty, recently visited items appear instead.

## Usage

### Opening

- **Mac**: Cmd+K
- **Windows/Linux**: Ctrl+K
- Opens a Radix Dialog-based search overlay positioned at 20% from the top of the viewport

### Keyboard Navigation

| Key | Action |
|-----|--------|
| Arrow Down | Move selection down (wraps at end) |
| Arrow Up | Move selection up (wraps at start) |
| Enter | Navigate to the currently selected item |
| Escape | Close the palette (Radix Dialog built-in dismiss) |

### Search Categories

Results are grouped into four categories, appearing in this order:

1. **Tasks** — searched server-side by title, description, and short ID (via `tasksApi.list({q})`)
2. **Agents** — fuzzy-matched on name and slug (client-side)
3. **Projects** — fuzzy-matched on project name (client-side)
4. **Pages** — fuzzy-matched on page title from the sidebar navigation (client-side)

Each category shows up to 6 results. Enter navigates to the entity's detail page:
- Task: `/tasks/{id}`
- Agent: `/agents/{id}`
- Project: `/projects?q={name}` (filters the project list since no dedicated detail route exists)
- Page: the page's navigation href

### Recents

When the input is empty (before or after clearing a search), the palette shows **Recent** items — up to 10 entries pulled from localStorage under the key `roboco-cmd-recents`. These are ordered by most recent first.

**Recents are populated on navigation**: whenever you press Enter or click a result, that item is added to recents (or moved to the front if already there). The recents list is capped at 10 items; older entries are automatically dropped.

---

## Architecture

### Component: `CommandPalette`

**File**: `panel/src/components/layout/command-palette.tsx`

A thin Radix Dialog renderer around the `useCommandPalette` hook. The component:
- Mounts once in the dashboard layout (`panel/src/app/(dashboard)/layout.tsx`) so the hotkey works on every page
- Listens for Cmd+K / Ctrl+K globally and opens the dialog
- Delegates all search/navigation logic to the hook
- Renders grouped results with icons, titles, and subtitles
- Handles arrow key and Enter navigation

**Props**: None — fully self-contained.

**Key Features**:
- Auto-focuses the input on open (via Radix's `onOpenAutoFocus`)
- Wrapping text (long titles are truncated with `text-truncate`)
- Shows "No results" when a search returns nothing; "No recent items yet" when recents are empty
- Icon for each result type (task = ListTodo, agent = Bot, project = FolderGit2, page = Compass)

### Hook: `useCommandPalette`

**File**: `panel/src/hooks/use-command-palette.ts`

Manages all data, keyboard navigation, and navigation state for the palette. Returns:

```typescript
{
  open: boolean;
  setOpen: (next: boolean) => void;
  query: string;
  setQuery: (value: string) => void;
  groups: CommandGroup[];           // Grouped results: Tasks, Agents, Projects, Pages
  flatItems: CommandItem[];         // Flattened items for keyboard navigation
  selectedIndex: number;            // Currently highlighted result
  moveSelection: (delta: number) => void;  // +1/-1 for arrow keys
  selectCurrent: () => void;        // Navigate to flatItems[selectedIndex]
  navigateTo: (item: CommandItem) => void; // Navigate + add to recents + close
}
```

**Features**:
- **Dialog state driven by UI Store**: `useUIStore().commandPaletteOpen` and `.setCommandPaletteOpen()` manage open/close (so other components like header search can open it via the store)
- **Debounced search**: Typed input is debounced 150ms before triggering results (cheap debounce, not caching the query)
- **Server-side task search**: Tasks are fetched via `tasksApi.list({ q: trimmedQuery })` only while the dialog is open and the query is non-empty; client-side for agents/projects/pages
- **Recents on empty query**: `loadRecents()` is called fresh each time the query becomes empty, so a pick made just before closing appears immediately on the next open
- **Selection resets on new query**: Typing clears and resets `selectedIndex` to 0 (jump to top result)
- **Dialog close resets state**: Closing the dialog clears the query and selection so the next open starts fresh
- **Flat navigation index**: `flatItems` is a flat array across all groups; `selectedIndex` wraps around and addresses items by position, not group

**Export**: Exported as named export from `@/hooks/use-command-palette`, used by `CommandPalette` component.

### Helper: `fuzzyScore(query: string, label: string): number | null`

**File**: `panel/src/lib/fuzzy-match.ts`

Pure function that scores a label against a fuzzy query. Returns a score (lower is better) or `null` if the query doesn't match.

**Algorithm**: Subsequence matching — every character in the query must appear in the label in order (not necessarily contiguous). Score rewards contiguous matches and early matches; gaps are penalized.

**Usage**: Called by `scoreAndSort()` to rank agents, projects, and pages.

### Helper: `loadRecents() / addRecent(item)`

**File**: `panel/src/lib/command-palette-recents.ts`

localStorage-backed functions for managing recent items.

- `loadRecents()`: Returns the current recent items array (up to 10) from localStorage key `roboco-cmd-recents`, or `[]` if empty/missing
- `addRecent(item)`: Adds or moves `item` to the front of recents, caps the array at 10, and saves back to localStorage

**Item shape**: `{ type: CommandRecentType, id: string, title: string }`

**CommandRecentType**: Union type of `"task" | "agent" | "project" | "page"`

---

## Data Flow

```
1. User presses Cmd+K
   ↓
2. Global keydown listener in CommandPalette detects it
   ↓
3. setOpen(true) fires, opening the Radix Dialog
   ↓
4. User types in the input
   ↓
5. onChange → setQuery() → debounceMs 150ms → groups re-compute
   ↓
6. Groups fetch tasks (server-side if query non-empty, only while open)
   ↓
7. Agents/projects/pages are fuzzy-scored client-side
   ↓
8. Arrow keys move selectedIndex within flatItems
   ↓
9. Enter presses selectCurrent() → navigateTo(item)
   ↓
10. navigateTo() calls addRecent() then router.push()
   ↓
11. setDialogOpen(false) closes the palette and resets state
```

---

## Verification Against Live Data

All four search categories are backed by live API data or real navigation config:

- **Tasks**: Fetched from the backend via `tasksApi.list({ q, limit: 6 })`, which already fuzzy-searches by title/description/id-prefix
- **Agents**: Fetched from the backend via `useAgentDefinitions()`, which returns all agent definitions
- **Projects**: Fetched from the backend via `useProjects()`, which returns all projects
- **Pages**: Statically imported from `navItems` in `panel/src/components/layout/sidebar.tsx` — the source of truth for the sidebar navigation

This ensures the palette always searches against populated, real data. Mock or stub data is never used in place of live API calls.

---

## Known Limitations & Future Improvements

1. **No dedicated project detail route**: Projects link to a filtered list view (`/projects?q={name}`) rather than a detail page, since no such route exists
2. **Maximum 6 results per group**: This is a hardcoded limit (`MAX_RESULTS_PER_GROUP`) to keep the results concise and readable
3. **localStorage persistence**: Recents are stored in the browser's localStorage, so they are device/profile-specific and not synced across sessions

---

## Testing

The command palette is tested in `panel/src/components/layout/__tests__/command-palette.test.tsx` and `panel/src/hooks/__tests__/use-command-palette.test.ts` with coverage for:

- Opening on Cmd+K / Ctrl+K
- Rendering grouped results with live data
- Keyboard navigation (arrow keys, Enter, Escape)
- Closing on Escape or navigation
- Empty-query recents display
- "No results" message on empty results
- Recents updates on navigation
- Result click handling

# Filter control for A2A conversations

Design spec for a filter control on the panel's `/a2a` page
(`panel/src/app/(dashboard)/a2a/page.tsx`), which is the CEO-facing
conversation-first view of agent-to-agent chats. Today the page has no
filtering at all: Panel 1 shows either the org-chart **Switchboard**
(`A2ASwitchboard`) or the classic **Conversation List** (`A2AConversationList`),
capped at the last 100 conversations with no way to narrow them.

This spec covers the filter dimensions, their placement, the active-filter
chip representation, and accessibility. It reuses the Popover + Checkbox +
Badge-chip pattern already shipped in
`panel/src/components/tasks/task-filters.tsx` rather than inventing a new
filter idiom, and stays within primitives already in
`panel/src/components/ui/` (`popover.tsx`, `checkbox.tsx`, `badge.tsx`,
`button.tsx`, `input.tsx`).

## Design-bar dial read

This is a dense admin surface (a live operations console), not a marketing
page, so per the UX/UI team's design bar defaults: **DESIGN_VARIANCE 2**
(the control sits in an already-fixed grid, no asymmetry), **MOTION_INTENSITY
2** (popover open/close and chip enter/exit only, no scroll choreography),
**VISUAL_DENSITY 7** (compact chips and a single icon-button trigger, not an
airy multi-field form bar like the full-width `TaskFilters` on `/tasks`,
because Panel 1 is only 4/12 columns wide at `lg`+).

## 1. Filterable dimensions

Four dimensions, each backed by a field already on the wire today
(`AdminConversationSummary` / `AdminPairSummary` in `panel/src/lib/api/a2a.ts`).
None of these are supported as backend query params yet — `a2aApi.listAdminConversations`
only accepts `limit`. The spec below filters **client-side over the already-fetched
page** (currently capped at `limit=100`); the "Future work" note calls out
the backend seam so a later task can add server-side params without changing
this UI contract.

| # | Dimension | Source field(s) | Value domain | Control widget |
|---|---|---|---|---|
| 1 | **Agent** | `agent_a`, `agent_b` (conversation is a match if either equals a selected agent) | The distinct set of agent slugs present in the currently loaded `conversations`/`pairs` list, deduplicated and sorted, labeled via `getAgentDisplayName()` (`panel/src/lib/agent-utils.ts`) — not a static enum, since the agent roster grows | Checkbox list inside the Filters popover (same pattern as `TaskFilters`' Status/Team checkbox lists) |
| 2 | **Task** | `task_id` (nullable — some conversations aren't task-scoped) | Free-text match against the task's short id (`task_id.slice(0, 8)`, the same truncation `A2AConversationList` already renders) plus an explicit **"No linked task"** toggle for `task_id === null` | A single `Input` (text) for the id fragment, wired with the same 300ms debounce pattern the `/tasks` page uses for `searchQuery`, plus one checkbox for "No linked task" |
| 3 | **Status** | `status` (`"active"` \| `"archived"`, the same two values `A2AConversationList`'s `Badge variant` already branches on) | The 2 known statuses | Two-option toggle group (button pair, `aria-pressed`, same idiom as the page's own Switchboard/List view toggle) — a checkbox list would be overkill for 2 values |
| 4 | **Date/time range** | `last_message_at` (falls back to `created_at` when null, matching the list item's own `formatDistanceToNow` fallback) | Any ISO-8601 instant; user picks calendar dates, compared at day granularity in the viewer's local timezone | Two `Input type="date"` fields labeled "From" / "To" — no date-range-picker component exists in `panel/src/components/ui/`, so this stays within already-installed primitives per the ladder (native `<input type="date">` is a browser-native widget, not a new dependency) |

**Per-view applicability.** The Switchboard is org-chart pair cards
(`AdminPairSummary`), not a conversation feed — most pairs have never
talked (`conversation_id: null`). Only **Agent** applies there (narrows
which pair cards render, same predicate as dimension 1 above); Task/Status/
Date filters have no meaning for a pair with no conversation, so selecting
them while in Switchboard view shows a one-line inline note ("Task, Status,
and Date filters apply to the Conversation List view") rather than silently
hiding pairs. Switching to List view via the existing `LayoutGrid`/`ListIcon`
toggle applies all four dimensions.

**Future work (not this task):** once conversation volume regularly exceeds
the `limit=100` page, promote Task/Status/Date to real backend query params
on `GET /a2a/chat/admin/conversations` (`agent`, `task_id`, `status`,
`from`, `to`) so filtering isn't limited to whatever page happened to load.
Client-side filtering as specified here is correct for the current data
volume and ships without a backend task.

## 2. Placement

The control must not crowd the conversation-first message stream: it lives
entirely inside **Panel 1** (the list/switchboard card), never inside
**Panel 2** (transcript + composer). Concretely, it is added to the existing
Panel-1 header row in `A2APageContent` (`page.tsx` lines ~269-298), which
today holds a `Radio` icon, a label, and the Switchboard/List view-toggle
buttons:

```
Collapsed (no active filters) — Panel 1 header, unchanged height:
┌─────────────────────────────────────────────┐
│ 📻 Switchboard      [▤][≡]      [⏷ Filters] │  <- new trigger, right-aligned
├─────────────────────────────────────────────┤
│  ...pair cards / conversation list...        │
```

The trigger is a single compact `Button variant="outline" size="sm"` reading
`Filters` (funnel icon, `lucide-react`'s `SlidersHorizontal`), matching the
view-toggle buttons' height (`h-7`) so the header row's height never changes
— that's what keeps it from crowding the list below. It shows an active-count
badge inline (`Filters · 2`) instead of a separate counter chip when >=1
filter is set, same abbreviation `TaskFilters` already uses for its
per-dimension triggers (`"${n} statuses"`).

Clicking the trigger opens **one** `Popover` (not four separate popovers
like the full-width `/tasks` page — Panel 1 is too narrow at 4/12 columns
for a row of triggers) containing all four dimension controls stacked
vertically, each in its own labeled section with a small `Clear` link when
that dimension has a selection — directly modeled on each section inside
`TaskFilters`' existing per-dimension `PopoverContent` blocks:

```
Expanded (popover open), anchored bottom-right of the trigger:
                                    ┌─────────────────────────┐
                                    │ Agent            Clear  │
                                    │ ☑ be-dev-1               │
                                    │ ☐ be-qa                  │
                                    │ ☐ ux-pm                  │
                                    ├─────────────────────────┤
                                    │ Task                    │
                                    │ [ id fragment...  ]      │
                                    │ ☐ No linked task          │
                                    ├─────────────────────────┤
                                    │ Status                  │
                                    │ [ Active ] [ Archived ]   │
                                    ├─────────────────────────┤
                                    │ Date range               │
                                    │ From [____] To [____]     │
                                    ├─────────────────────────┤
                                    │        [ Clear all ]      │
                                    └─────────────────────────┘
```

Filters apply live as each control changes (no separate "Apply" button) —
consistent with `TaskFilters`, whose `onStatusChange`/`onTeamChange` etc.
fire immediately. The popover's max height is capped (`max-h-[70vh]
overflow-y-auto`, same idea as `TaskFilters`' `max-h-64 overflow-y-auto`
project/product lists) so it never grows taller than the viewport on small
screens.

On mobile (`<lg`, where Panel 1 is the only visible pane per the page's
existing `onDetailLevel` show/hide split), the trigger and popover behave
identically — the popover already clamps to the viewport, so no separate
mobile layout is needed.

## 3. Active-filter chips + clear-all

When one or more filters are active, a **chip row** appears directly below
the Panel-1 header (above the list/switchboard content), pushing the list
down by exactly the chip row's own height — it does not overlay content and
it collapses to zero height (not rendered at all) when no filters are set,
so the empty/default state is pixel-identical to today's layout:

```
┌─────────────────────────────────────────────┐
│ 📻 Switchboard      [▤][≡]   [⏷ Filters · 3]│
├─────────────────────────────────────────────┤
│ be-dev-1 ✕   Active ✕   From 07/01 ✕  Clear all │  <- chip row, wraps on overflow
├─────────────────────────────────────────────┤
│  ...filtered pair cards / conversation list...│
```

Each chip is a `Badge variant="secondary"` with a trailing `X`
(`lucide-react`) icon button, exactly `TaskFilters`' existing chip markup
(`<Badge variant="secondary" className="gap-1">{label}<X className="h-3 w-3
cursor-pointer hover:text-destructive" onClick={...} /></Badge>`). One chip
per active value:

- **Agent**: one chip per selected agent, labeled with `getAgentDisplayName()`.
- **Task**: one chip for the id-fragment text (`Task: <fragment>`) if set, one
  chip labeled `No linked task` if that toggle is on.
- **Status**: one chip per selected status (`Active` / `Archived`).
- **Date range**: up to two chips, `From <date>` and `To <date>`, each
  independently removable.

Clicking a chip's `X` removes only that value (unchecking the matching
control inside the popover, same two-way binding `TaskFilters` uses between
its checkbox state and its chip `onClick`). The row wraps (`flex flex-wrap
gap-2`) rather than truncating or scrolling horizontally, so every active
filter stays visible without an extra interaction.

**Clear all** is a `Button variant="ghost" size="sm"` at the end of the chip
row, visible only when >=1 filter is active (same condition that gates the
whole chip row and mirrors `TaskFilters`' own "Clear all" button), and it
resets every one of the four dimensions in a single click.

## 4. Accessibility

### Keyboard operability

| Control | Behavior |
|---|---|
| `Filters` trigger button | Reachable by `Tab` in header-row order (after the view-toggle buttons); `Enter`/`Space` opens the popover; `aria-expanded` reflects open state; `aria-haspopup="dialog"` (Radix `Popover` primitive already provides this) |
| Popover content | Focus moves to the first checkbox on open (Radix default); `Tab`/`Shift+Tab` cycles through all controls in visual top-to-bottom order (Agent checkboxes → Task input → "No linked task" checkbox → Status toggle buttons → From/To date inputs → Clear all); `Escape` closes the popover and returns focus to the trigger (Radix default) |
| Checkboxes (Agent, "No linked task") | `Space` toggles; each wrapped in a `<label>` per `TaskFilters`' existing pattern so the whole row is a hit target, not just the 16px box |
| Status toggle buttons | Real `<button>` elements with `aria-pressed`, not `<div onClick>` — `Enter`/`Space` toggles, matching the page's existing Switchboard/List `Button` toggle idiom (`aria-pressed={view === "switchboard"}`) |
| Date inputs | Native `<input type="date">`, which ships full keyboard support (arrow keys move segments, typing digits enters them) from the browser — no custom widget to re-implement |
| Chip remove (`X`) | Each chip's `X` is a real `<button aria-label="Remove <chip label> filter">` (icon-only, so `aria-label` is required — the current `TaskFilters` chips use a bare `<X>` with no accessible name, which this spec explicitly fixes rather than copies) |
| Clear all | Real `<button>`, reachable by `Tab` after the last chip |

### WCAG AA contrast

The control introduces zero new colors — every state below reuses the
app's existing shadcn/ui tokens (`panel/src/app/globals.css`), which are
already in production use across the panel, so this control carries no new
contrast risk. Stated minimums (per WCAG 2.1 AA): **4.5:1** for body/label
text, **3:1** for large text (≥18px/14px-bold) and for non-text UI
components (focus rings, icon-only button boundaries).

| Element | Tokens | Notes |
|---|---|---|
| `Filters` trigger label + count | `--foreground` on `--background` (light: `oklch(0.145 0 0)` on `oklch(1 0 0)`) | Near-black on white — the app's default body-text pair, already far above 4.5:1 everywhere else in the panel |
| Checkbox/toggle labels inside popover | `--popover-foreground` on `--popover` | Same near-black-on-white pair as body text |
| Chip text | `--secondary-foreground` on `--secondary` (light: `oklch(0.205 0 0)` on `oklch(0.97 0 0)`) | Same pair `TaskFilters`' own `Badge variant="secondary"` chips already use in production |
| Chip `X` icon (default) | `--muted-foreground` on `--secondary` | `muted-foreground` is the token shadcn ships specifically calibrated to clear 4.5:1 against near-white backgrounds |
| Chip `X` icon (hover) | `--destructive` on `--secondary` | Existing `hover:text-destructive` class already used by `TaskFilters`; destructive red is tuned against both light/dark `--background` per the shared token, not introduced here |
| Status toggle button (selected) | `--primary-foreground` on `--primary` (light: `oklch(0.985 0 0)` on `oklch(0.205 0 0)`) | Near-white on near-black — the app's own primary-button pair |
| Focus-visible ring (all controls) | `--ring` outline, ≥3:1 against `--background` and `--card` | Radix + Tailwind's default `focus-visible:ring` treatment already applied to `Button`/`Checkbox`/`Input` across the panel |

Because every pair above is an existing, already-shipped token combination
(not a new color introduced by this spec), no new contrast audit tooling is
required — QA can spot-check with devtools' contrast inspector against this
table rather than measuring from scratch. Dark mode uses the same token
names with their dark-mode values (`globals.css` `.dark` block), which
preserve the same relative-lightness relationships (e.g. `--secondary` /
`--secondary-foreground` stay a light-text-on-darker-chip pair), so no
dark-mode-specific override is needed.

## 5. Empty and edge states

- **Zero results after filtering**: the list/switchboard content area shows
  the same empty-state idiom the components already use for "no data at all"
  (`MessagesSquare`/`Radio` icon + one line of `text-muted-foreground`), but
  with copy that names the cause: `"No conversations match the current
  filters"` plus an inline `Clear all` link — distinct from today's `"No A2A
  conversations yet"` (zero data) and `"No allowed A2A pairs configured"`
  (zero pairs), so the CEO isn't told there's no data when there's just no
  match.
- **Agent list is empty on first load** (conversations/pairs still loading):
  the Agent checkbox section shows 2 `Skeleton` rows (same `Skeleton`
  component the list/switchboard already use for their own loading state)
  instead of an empty list, so the popover doesn't imply there are zero
  agents.
- **Filters persist across live updates**: the page already invalidates and
  refetches conversations on every `a2a.message` WebSocket frame
  (`page.tsx` lines 131-140); filter *state* is local component state, not
  derived from the fetch, so an incoming live message does not reset active
  filters — it's re-evaluated against the refreshed list.

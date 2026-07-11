# Navigation/structure spec: breadcrumb, prev/next, constraints distinction

Status: implemented (v0.21.0+) Owner: ux-dev-2 Surface: task detail page (`panel/src/app/(dashboard)/tasks/[taskId]/page.tsx`) and its header (`panel/src/components/tasks/task-detail/task-header.tsx`) and description tab (`panel/src/components/tasks/task-detail/task-description.tsx`).

## Dial read

Per the team design bar, this is dense product UI (task detail / admin panel):

- **DESIGN_VARIANCE:** 2 — the breadcrumb and prev/next controls are a predictable single row above the existing header; no new grid or layout shape.
- **MOTION_INTENSITY:** 1 — hover/focus states only (existing `Button` / `Link` hover treatment), no transition is introduced.
- **VISUAL_DENSITY:** 8 — compact 28px-tall controls that add one row of chrome, nothing more; the constraints card stays a `Card`, not a heavier modal or full-width banner.

## Problem

The task detail page currently has no sense of *where this task sits*: the header's only navigation is a single `ArrowLeft` icon button that always goes to `/tasks` (`task-header.tsx` lines 474-479), regardless of whether the task has a parent. A reader drilling into a subtask of a subtask loses the parent chain the moment they land on the page, and moving between sibling tasks (e.g. checking each dev subtask of the same parent while triaging) requires going back to the list and re-finding the next one every time.

Separately, `task.constraints` (the auto-attached project-wide architectural standard, read-only) renders in `task-description.tsx` as a `Card` with only `border-dashed` and a muted title (lines 181-196) — visually one dash away from the free-form, user-authored `description` card right above it. A reader skimming the page has no fast visual cue that one box is "the project's rule" and the other is "this task's own words."

This spec is a pure design/markup change: no new dependency, no new backend field (`parent_task_id`, `sequence`, and `project_id` already exist on `Task`; `useTask` and `useSubtasks` already exist in `@/hooks/use-tasks`).

## 1. Breadcrumb trail

**Implementation status:** ✓ Complete (v0.21.0+). This diverged from the rich multi-crumb trail originally proposed for this section — see "What shipped" below for the design that actually rendered.

**Component:** `TaskBreadcrumb` at `panel/src/components/tasks/task-detail/task-breadcrumb.tsx`, rendered in the task detail page (`[taskId]/page.tsx`) above the `TaskHeader` component, replacing the standalone `ArrowLeft` button that was removed from `task-header.tsx`.

**What shipped.** A single ancestor level, not the multi-crumb trail below: `{Parent title}  >  {Current title}`. It renders only when `task.parent_task_id` is set — `null` for a root task, so the row is simply absent rather than showing an empty or placeholder crumb. The parent is fetched with one `useTask(parentId)` call (a `Skeleton` covers the loading gap); the parent title is a `Link` to `/tasks/{parent.id}`, truncated with a `title=` tooltip; the current task's own title renders last, non-interactive, in `text-foreground/70`. Separator: `ChevronRight` (`lucide-react`). Deeper ancestry is reached by following the chain up one hop at a time — clicking into the parent shows its own parent, and so on — rather than being flattened into one row.

**Why not the rich trail.** The proposed `Tasks` crumb existed to carry the back-to-list affordance the standalone `ArrowLeft` button used to own; that's redundant with the persistent sidebar's own "Tasks" nav link (`components/layout/sidebar.tsx`), so dropping it doesn't remove a control, it removes a duplicate. The `{Project name}` crumb and the multi-ancestor walk (a `useTask(parentId)` fetch per generation, collapsing behind a `DropdownMenu` past 3 entries) added fetches and layout branching for a case — chains long enough to need collapsing — that's rare given hierarchies cap at 3 levels per `CLAUDE.md`'s MegaTask section; the one-hop-at-a-time model covers it with a fifth of the markup. Full writeup: `docs/guide/task-detail-navigation.md`.

**Accessibility.** The row carries `aria-label="Parent task"`; the current-task span is plain non-interactive text, not a focusable element.

## 2. Prev/next list navigation

**Implementation status:** ✓ Complete (v0.21.0+). List-context-aware navigation, documented separately in `docs/guide/task-detail-navigation.md`.

**Component:** `TaskListNav` at `panel/src/components/tasks/task-detail/task-list-nav.tsx`, rendered in the task detail page (`[taskId]/page.tsx`) alongside the breadcrumb —  two chevron icon buttons that move to adjacent tasks within the current Tasks list filter/sort context, or disabled when viewed outside the list context.

**Data & ordering.** This diverged from the original sibling-order proposal during implementation (see `docs/guide/task-detail-navigation.md` for the full writeup) — "adjacent" means the previous/next row in the **Tasks list's last-visited filter/sort order**, not a `parent_task_id` sibling. The Tasks list page (`tasks/page.tsx`) reports its currently visible, filtered/sorted task order to `useScrollRestorationStore.setTaskListNav({ items, queryString })` whenever it changes; `TaskListNav` reads that `taskListNav` context, finds `task.id`'s index in `items`, and derives the prev/next item from `index - 1` / `index + 1`. The context is session-scoped (`sessionStorage` via the existing Zustand `persist` middleware), so it survives navigation within a session but doesn't persist across sessions. `useSubtasks` / `parent_task_id` / `sequence` are not part of this feature — sibling-order navigation, as originally proposed below, was not what shipped.

**Controls.** Two `Button variant="outline" size="icon"` using `ChevronLeft` / `ChevronRight` (`lucide-react`), each wrapped in a `Tooltip`:

- **Prev** links (`Link href="/tasks/{id}{queryString}"`) to `items[index - 1]`; **disabled** (not hidden — a disabled control at the boundary communicates "this is the first one," an absent control reads as "there is no prev/next feature here") when the current task is first in the captured list order, or when no list context exists for this session, or when the current task isn't part of the captured order (opened via a direct link, search, or notification instead of from the list).
- **Next** mirrors this at `index + 1`, disabled when current is last (or the same no-context/not-in-list cases as Prev).
- Each button's tooltip shows the target item's `title` when enabled, or the fixed explanation "Open this task from the Tasks list to enable prev/next navigation within that list's filter/sort order." when disabled — so hovering a disabled button still explains why, rather than looking broken.
- The href carries the captured `queryString` (the Tasks list's filters/sort at the time it was visited), so navigating there preserves that view.

**Keyboard.** ✓ Implemented. `Alt+ArrowLeft` / `Alt+ArrowRight` navigate prev/next exactly as the visible buttons do (same disabled-at-boundary and no-context behavior — the shortcut is a no-op when the corresponding button would be disabled). A `window` `keydown` listener in `TaskListNav` is suppressed whenever the focused element is an `input`, `textarea`, or `contenteditable` node, so it doesn't fight text-field cursor movement.

**Empty state.** When no list context has been captured this session, or the current task isn't found in the captured `items`, both buttons render disabled with the fallback tooltip above rather than being omitted — consistent with the boundary-disabled treatment, so the control's presence is predictable regardless of which task the reader is on.

## 3. Constraints visual distinction

**Implementation status:** ✓ Complete (v0.21.0+). Constraints now render with distinctive amber styling and a ShieldAlert icon.

**File:** `panel/src/components/tasks/task-detail/task-description.tsx` (lines 181-199).

**Treatment — amber "read-only architectural" tint, matching the existing convention already used for the *same* concept elsewhere in the panel:** the per-project Conventions tab uses `border-amber-500/40` for its "this is a committed, canonical rule" card (`conventions-tab.tsx` line 412), and `edit-project-dialog.tsx` uses `text-amber-600 dark:text-amber-400` plus a `KeyRound` icon (line 214-215) for the same "system-controlled, read-only" semantic on the git-token field. `task.constraints` is generated from that same `.roboco/conventions.yml` map (per `CLAUDE.md`'s Architectural Conventions Standard section), so reusing that exact token pairing — instead of introducing a new color — makes the same underlying concept look the same everywhere it appears, rather than inventing a fourth "read-only" treatment.

```tsx
<Card className="border-amber-500/40 bg-amber-500/5">
  <CardHeader className="pb-3">
    <CardTitle className="text-base flex items-center gap-2 text-amber-700 dark:text-amber-400">
      <Lock className="h-4 w-4" />
      Constraints
    </CardTitle>
  </CardHeader>
  <CardContent>
    <p className="text-xs text-muted-foreground mb-3">
      Architectural standard derived from the project conventions —
      read-only. Applies to every task in this project.
    </p>
    <Markdown>{task.constraints}</Markdown>
  </CardContent>
</Card>
```

- `Lock` (`lucide-react`, already a project dependency — no new icon package) replaces no icon at all today, signaling "not editable" at a glance before the reader even reads the label — the `description` card above it has an `Edit3` pencil in its header for the opposite reason (it IS editable), so the two icons now read as a matched pair of opposite affordances.
- `bg-amber-500/5` is a barely-there tint (5% opacity) — enough to differentiate the card body from the plain-white `description` card above without competing with it or reading as a warning/error state (which would call for `destructive`/red, wrong semantic here — this is informational, not an alert).
- The header text and icon both take `text-amber-700 dark:text-amber-400` (matching `edit-project-dialog.tsx`'s exact light/dark pair) instead of the current plain `text-muted-foreground`, so the section title itself carries the distinction, not just the border.
- The explanatory caption paragraph (already present, unchanged) stays `text-muted-foreground` — only the card chrome and heading change color; body copy stays neutral for readability.

**Placement.** Unchanged — still renders directly below the `description` `Card` in the Overview tab, so the reading order (task's own words, then the project-wide rule) is preserved; only the visual weight changes.

## Non-goals

- No new shadcn/ui primitive (no `breadcrumb.tsx` added to `components/ui`) — the breadcrumb composes existing `Link`, `Skeleton`, and `lucide-react` icons already in the tree, per the "fewest files" bar.
- No change to how ancestors are fetched server-side — the breadcrumb reuses `useTask` exactly as it exists today; no new endpoint, no new query param. Prev/next reuses the Tasks list's already-fetched page data instead of a dedicated sibling endpoint.
- No persisted "last visited sibling" or breadcrumb history — this is a point-in-time structural view of the current task's position, not a session/browsing-history feature.
- No persisted keyboard-shortcut preference (e.g. a toggle to disable `Alt+Arrow`) — the shortcut always mirrors whatever the visible buttons currently allow.

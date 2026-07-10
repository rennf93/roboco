# Navigation/structure spec: breadcrumb, prev/next, constraints distinction

Status: proposed
Owner: ux-dev-2
Surface: task detail page (`panel/src/app/(dashboard)/tasks/[taskId]/page.tsx`)
and its header (`panel/src/components/tasks/task-detail/task-header.tsx`) and
description tab (`panel/src/components/tasks/task-detail/task-description.tsx`).

## Dial read

Per the team design bar, this is dense product UI (task detail / admin panel):

- **DESIGN_VARIANCE:** 2 — the breadcrumb and prev/next controls are a
  predictable single row above the existing header; no new grid or layout
  shape.
- **MOTION_INTENSITY:** 1 — hover/focus states only (existing `Button` /
  `Link` hover treatment), no transition is introduced.
- **VISUAL_DENSITY:** 8 — compact 28px-tall controls that add one row of
  chrome, nothing more; the constraints card stays a `Card`, not a heavier
  modal or full-width banner.

## Problem

The task detail page currently has no sense of *where this task sits*: the
header's only navigation is a single `ArrowLeft` icon button that always goes
to `/tasks` (`task-header.tsx` lines 474-479), regardless of whether the task
has a parent. A reader drilling into a subtask of a subtask loses the parent
chain the moment they land on the page, and moving between sibling tasks
(e.g. checking each dev subtask of the same parent while triaging) requires
going back to the list and re-finding the next one every time.

Separately, `task.constraints` (the auto-attached project-wide architectural
standard, read-only) renders in `task-description.tsx` as a `Card` with only
`border-dashed` and a muted title (lines 181-196) — visually one dash away
from the free-form, user-authored `description` card right above it. A reader
skimming the page has no fast visual cue that one box is "the project's rule"
and the other is "this task's own words."

This spec is a pure design/markup change: no new dependency, no new backend
field (`parent_task_id`, `sequence`, and `project_id` already exist on `Task`;
`useTask` and `useSubtasks` already exist in `@/hooks/use-tasks`).

## 1. Breadcrumb trail

**Component:** new `TaskBreadcrumb` at
`panel/src/components/tasks/task-detail/task-breadcrumb.tsx`, rendered inside
`TaskHeader` as a new row **above** the existing title row (still inside the
`<div className="border-b pb-4">` wrapper), replacing the standalone
`ArrowLeft` button — the breadcrumb's leading "Tasks" crumb takes over that
back-to-list function, so no control is lost.

**Data.** The chain is built client-side from data already available:
`Project` (via the existing `useProject(task.project_id)` call `TaskMetadata`
already makes — lift it into `TaskHeader` or pass as a prop from
`page.tsx`'s `useTaskDetail`, which already resolves `project`) and the
ancestor chain, walked with one `useTask(parentId)` call per ancestor
generation (small trees in practice — task hierarchies cap at 3 levels
umbrella → root-subtask → cell task → dev subtask per `CLAUDE.md`'s MegaTask
section, so this is at most 3 sequential fetches, each already cached by
React Query once visited).

**Rendered chain, left to right:**

```
Tasks  /  {Project name}  /  {Ancestor 1 title}  /  {Ancestor 2 title}  /  {Current task title}
```

- `Tasks` — static crumb, links to `/tasks` (replaces the old `ArrowLeft`
  button's destination).
- `{Project name}` — links to `/projects` (matching `TaskMetadata`'s existing
  Project card link target, since there is no single-project detail route
  today); omitted entirely when `task.project_id` is null (a branchless
  fan-out/umbrella task).
- One crumb per ancestor, oldest first, each a `Link` to `/tasks/{id}`,
  showing that ancestor's `title` truncated to **24 characters** with a
  trailing ellipsis (CSS `truncate` on a `max-w-[10rem]` span, `title=`
  attribute carries the full string for hover).
- The current task's own title renders **last, non-interactive** (a `span`,
  not a `Link`) in `font-medium text-foreground` — every other crumb is
  `text-muted-foreground hover:text-foreground`, so the current position is
  the one crumb that doesn't look clickable.
- Separator: `ChevronRight` (`lucide-react`, `h-3.5 w-3.5 text-muted-foreground`),
  matching the existing `|` separator weight already used elsewhere in
  `TaskHeader`'s metadata row (visually consistent icon-as-divider language).

**Collapsing long chains.** When the rendered chain (Project + ancestors)
exceeds **3 entries before the current task**, collapse the middle ones into
a single `…` crumb: always show the first entry (`Project`) and the last
ancestor (immediate parent), with everything in between behind a
`DropdownMenu` (already a dependency, used elsewhere in `task-header.tsx`)
triggered by the `…` — each hidden ancestor is a `DropdownMenuItem` linking to
`/tasks/{id}`. This keeps the row to a bounded width on narrow viewports
without truncating to unreadable fragments.

**Responsive.** Below `md:`, the breadcrumb row wraps its own `flex-wrap`
(same technique `TaskHeader`'s existing metadata row already uses) rather
than horizontally scrolling — a 3-level chain wrapping to two lines is
preferable to introducing a new scroll container.

**Accessibility.** Wrap the row in `<nav aria-label="Task breadcrumb">`; the
current-task span carries `aria-current="page"`.

## 2. Prev/next sibling navigation

**Component:** new `TaskPrevNext` at
`panel/src/components/tasks/task-detail/task-prev-next.tsx`, rendered in the
existing header's right-hand action area (`task-header.tsx`'s
`<div className="shrink-0">` block, lines 598-649), immediately to the left
of the "Actions" dropdown button — two icon buttons, not a full toolbar,
so it doesn't compete with the primary Actions control for attention.

**Data & ordering.** "Sibling" means another task sharing the same
`parent_task_id`. Fetch with the existing `useSubtasks(task.parent_task_id)`
hook (already used by `SubtasksList` for the reverse direction — children —
so this is the same hook pointed at the parent instead of at `task.id`), sort
the result by `sequence` ascending (the field `TaskMetadata` already surfaces
as a read-only "Sequence" card), and locate the current task's index in that
sorted list.

**Controls.** Two `Button variant="ghost" size="icon"` (matching the
existing `ArrowLeft` button's exact styling in `task-header.tsx`) using
`ChevronLeft` / `ChevronRight` (`lucide-react`):

- **Prev** navigates (`router.push` or `Link href="/tasks/{id}"`) to the
  sibling at `index - 1`; **disabled** (not hidden — a disabled control at
  the boundary communicates "this is the first one," an absent control
  reads as "there is no prev/next feature here") when the current task is
  first in sequence, or when `task.parent_task_id` is null (no parent, so
  no sibling set — every button in the pair is disabled in that case, not
  removed, since the feature applies uniformly across the task tree).
- **Next** mirrors this at `index + 1`, disabled when current is last.
- Each button carries a `title` tooltip naming the target: `"Previous:
  {sibling.title}"` / `"Next: {sibling.title}"` (truncated to ~40 chars),
  or `"No previous task"` / `"No next task"` when disabled — so hovering a
  disabled button still explains why, rather than looking broken.

**Keyboard.** `Alt+ArrowLeft` / `Alt+ArrowRight` trigger the same navigation
when focus is not inside a text input, `Textarea`, or `contenteditable`
element (guard on `document.activeElement.tagName`) — a plain
`useEffect` + `window.addEventListener("keydown", ...)` in `TaskPrevNext`,
cleaned up on unmount. `Alt+Arrow` (not the bare arrow key) avoids colliding
with normal text-field cursor movement anywhere else on the page, so no
existing input behavior changes.

**Empty state.** When `useSubtasks` resolves to a single-item list (no
siblings) or `task.parent_task_id` is null, both buttons render disabled with
the "No previous/next task" tooltip rather than being omitted — consistent
with the boundary-disabled treatment above, so the control's presence is
predictable regardless of which task the reader is on.

## 3. Constraints visual distinction

**File:** `panel/src/components/tasks/task-detail/task-description.tsx`,
replacing the existing `constraints` block (lines 181-196).

**Treatment — amber "read-only architectural" tint, matching the existing
convention already used for the *same* concept elsewhere in the panel:** the
per-project Conventions tab uses `border-amber-500/40` for its "this is a
committed, canonical rule" card (`conventions-tab.tsx` line 412), and
`edit-project-dialog.tsx` uses `text-amber-600 dark:text-amber-400` plus a
`KeyRound` icon (line 214-215) for the same "system-controlled, read-only"
semantic on the git-token field. `task.constraints` is generated from that
same `.roboco/conventions.yml` map (per `CLAUDE.md`'s Architectural
Conventions Standard section), so reusing that exact token pairing —
instead of introducing a new color — makes the same underlying concept look
the same everywhere it appears, rather than inventing a fourth "read-only"
treatment.

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

- `Lock` (`lucide-react`, already a project dependency — no new icon
  package) replaces no icon at all today, signaling "not editable" at a
  glance before the reader even reads the label — the `description` card
  above it has an `Edit3` pencil in its header for the opposite reason (it
  IS editable), so the two icons now read as a matched pair of opposite
  affordances.
- `bg-amber-500/5` is a barely-there tint (5% opacity) — enough to
  differentiate the card body from the plain-white `description` card
  above without competing with it or reading as a warning/error state
  (which would call for `destructive`/red, wrong semantic here — this is
  informational, not an alert).
- The header text and icon both take `text-amber-700 dark:text-amber-400`
  (matching `edit-project-dialog.tsx`'s exact light/dark pair) instead of
  the current plain `text-muted-foreground`, so the section title itself
  carries the distinction, not just the border.
- The explanatory caption paragraph (already present, unchanged) stays
  `text-muted-foreground` — only the card chrome and heading change color;
  body copy stays neutral for readability.

**Placement.** Unchanged — still renders directly below the `description`
`Card` in the Overview tab, so the reading order (task's own words, then the
project-wide rule) is preserved; only the visual weight changes.

## Non-goals

- No new shadcn/ui primitive (no `breadcrumb.tsx` added to `components/ui`)
  — the breadcrumb composes existing `Link`, `DropdownMenu`, and
  `lucide-react` icons already in the tree, per the "fewest files" bar.
- No change to how siblings/ancestors are fetched server-side — this reuses
  `useTask` and `useSubtasks` exactly as they exist today; no new endpoint,
  no new query param.
- No persisted "last visited sibling" or breadcrumb history — this is a
  point-in-time structural view of the current task's position, not a
  session/browsing-history feature.
- No global keyboard-shortcut registry — the `Alt+Arrow` binding is local to
  `TaskPrevNext` and unregisters on unmount; a cross-page shortcut system is
  out of scope for this pass.

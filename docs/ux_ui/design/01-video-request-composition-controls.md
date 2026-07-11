# Video request: project picker, re-render control, composition preview panel

Interaction spec for three additions to the existing on-demand video flow: a project picker in the request dialog, a re-render control with four visual states, and a composition preview panel (live iframe + captions side by side) in the approval screen. Written so a frontend developer can implement directly from this document without further design clarification.

## Scope and where this lives

All three pieces extend one existing file: `panel/src/components/dashboard/video-post-queue.tsx`.

| Piece | Existing component it extends | New sub-component to add |
|---|---|---|
| Project picker | `RequestVideoDialog` (the "Request a video" dialog) | none — reuses `ProjectSelector` |
| Re-render control | `VideoPostRow` (one card in the approval queue) | `RerenderControl` |
| Composition preview panel | `VideoPostRow` | `CompositionPreviewPanel` |

None of this replaces the existing rendered-MP4 preview and caption textareas already in `VideoPostRow` (lines 145-258 of the current file) — the composition preview panel is a new block shown above them, giving the CEO a fast, pre-render look at the live composition before the MP4 cuts exist or while iterating.

This spec does not cover the backend contract (`project_id` on `VideoRequestBody`, the re-render action route, or the composition-HTML proxy route) — those are backend/frontend implementation details tracked on the sibling code task. Every prop name below is written as the request shape the frontend needs; the implementer wires it to whatever route lands.

**Design bar dial read:** this is dense product UI (an approval queue inside the existing panel chrome), not a landing surface — variance 2, motion 2, density 7, per the UX/UI cell's default for dashboard work. No new color, radius, or shadow tokens; every value below is a token or class already used in `video-post-queue.tsx` or `release-proposal-card.tsx`.

---

## 1. Project picker (`RequestVideoDialog`)

### Component

Reuse `ProjectSelector` from `panel/src/components/projects/project-selector.tsx` as-is — it already renders a `Select` grouped by cell (Backend / Frontend / UX/UI / Other) with a `FolderGit2` icon and a cell `Badge`, matching this dialog's existing shadcn/ui primitives (`Select`, `Label`, `Input`, `Textarea`, `Checkbox` are already imported in this file).

`ProjectSelector` today has no way to restrict the list to video-enabled projects. Add one optional prop rather than a new component:

```tsx
interface ProjectSelectorProps {
  // ...existing props unchanged...
  videoEngineOnly?: boolean; // when true, filter `projects` to video_engine_enabled === true before grouping
}
```

`video_engine_enabled` is already a field on `Project` (`panel/src/types/index.ts:1026`) but is **not** on `ProjectSummary` (the shape `useProjects()` / `GET /projects` returns today, `panel/src/types/index.ts:1081-1090`) — the backend task must add `video_engine_enabled: boolean` to `ProjectSummary` for this filter to work client-side. `RequestVideoDialog` passes `videoEngineOnly`.

### Placement

Insert the picker as the **first** field inside `RequestVideoDialog`'s `<div className="space-y-4">` (currently Occasion, Brief, Platforms — see `video-post-queue.tsx:346-386`), before Occasion, using the exact same `space-y-2` wrapper and `Label` pattern every other field in this dialog uses:

```tsx
<div className="space-y-2">
  <Label htmlFor="video-request-project">Project</Label>
  <ProjectSelector
    value={projectId}
    onChange={setProjectId}
    videoEngineOnly
    allowClear={false}
    placeholder="Select a project…"
  />
</div>
```

`projectId` is new dialog state (`useState<string | null>(null)`), reset in the same `onSuccess`/cancel paths that already reset `occasion`/`brief`/ `platforms` (`video-post-queue.tsx:308-311`).

### States

| State | Trigger | Visual |
|---|---|---|
| Loading | `useProjects()` still fetching | `ProjectSelector`'s own `disabled={disabled \|\| isLoading}` on the `Select` trigger — matches the dialog's existing pattern of disabling inputs while a mutation/query is in flight. No separate spinner needed; the trigger just reads inert. |
| Empty | Query resolved, zero projects have `video_engine_enabled: true` | Replace the `Select` with a one-line `<p className="text-xs text-muted-foreground">` reading "No projects have the video engine enabled — turn it on in a project's settings first." — same copy pattern as the existing "No projects exist yet" hint under the MegaTask checklist in `intake-form.tsx:176-180`. |
| Populated, unselected | Projects loaded, none chosen | Trigger shows the `placeholder` text, muted-foreground color (shadcn default `SelectValue` behavior — no override needed). |
| Selected | A project is chosen | Trigger shows `FolderGit2` icon + project name + cell `Badge`, exactly as `ProjectSelector` already renders it (`project-selector.tsx:98-108`). |

### Validation

`canSubmit` (`video-post-queue.tsx:330-333`) gains `projectId !== null` as a fourth condition alongside occasion/brief/platforms — the picker is required, matching every other field in this dialog.

---

## 2. Re-render control (`VideoPostRow`)

### Component

New `RerenderControl` sub-component, colocated in `video-post-queue.tsx` next to `VideoPostRow` (mirrors how `RequestVideoDialog` already sits next to `VideoPostQueue` in the same file):

```tsx
type RerenderState = "idle" | "loading" | "stale" | "error";

function RerenderControl({
  state,
  onRerender,
}: {
  state: RerenderState;
  onRerender: () => void;
}) { /* ... */ }
```

`state` is derived, not stored redundantly: `stale` when the draft's captions or occasion have been edited locally since the last successful render (compare against the same `edited*`/local-state pattern `VideoPostRow` already uses for captions, `video-post-queue.tsx:98-101`); `loading` while the re-render mutation is in flight; `error` when that mutation's last attempt failed; `idle` otherwise (freshly rendered, nothing pending, no error).

### Placement

Directly above the cut-switcher buttons (`video-post-queue.tsx:160-184`), right-aligned, same row as the cut buttons on `sm:` and up:

```
┌ VideoPostRow ─────────────────────────────────────────────┐
│  [Film] Video   [occasion badge]                            │
│  Title                                                       │
│  Script excerpt…                                             │
│                                                                │
│  [9:16] [1:1]                          [Re-render ⟳ idle] ←  │
│  ┌ Composition Preview Panel ───────────────────────────┐   │
│  │  iframe            │  captions                        │   │
│  └────────────────────┴───────────────────────────────────┘   │
│  …existing edit-caption blocks, Reject / Approve…            │
└────────────────────────────────────────────────────────────┘
```

### Visual spec per state

Same `Button` primitive already imported in this file, `size="sm"`, plus `lucide-react` icons already available in the codebase (`RefreshCw`, `AlertTriangle`, `CheckCircle2` — the last two already imported elsewhere in this file).

| State | Button `variant` | Icon | Label | Disabled | Extra |
|---|---|---|---|---|---|
| `idle` | `outline` | `RefreshCw` (static, no spin) | "Re-render" | No | none |
| `loading` | `outline` | `RefreshCw` with `className="animate-spin"` (exact pattern used for the navbar refresh button, `header.tsx:111`) | "Re-rendering…" | Yes | `aria-live="polite"` wrapper so screen readers announce the state change |
| `stale` | `default` (filled — draws the eye, matches how `bg-amber-500/10` gaps banner in `release-proposal-card.tsx:181` is used to flag "needs attention") with an added `text-amber-950 bg-amber-500 hover:bg-amber-500/90` override | `RefreshCw` | "Re-render (edited)" | No | A small `Badge variant="outline"` reading "stale" is not needed — the button label already carries the meaning; do not duplicate it in a second element |
| `error` | `destructive` | `AlertTriangle` | "Retry re-render" | No | `title` attribute carries the last error message (mirrors the disabled-cut-button `title` pattern at `video-post-queue.tsx:166-169`); a one-line `<p className="text-xs text-destructive">` under the button shows the same message so it is not tooltip-only |

State transitions: `idle`/`stale`/`error` → click → `loading` → mutation resolves → `idle` (success) or `error` (failure). Editing a caption or the occasion while in `idle` → `stale`. There is no user action that transitions directly out of `loading` except the mutation settling — the button stays `disabled` for the whole request, preventing double-submission (same re-entrancy concern already handled via `submittingRef` in `spawn-agent-dialog.tsx:40`).

---

## 3. Composition preview panel (`VideoPostRow`)

### Component

New `CompositionPreviewPanel` sub-component:

```tsx
function CompositionPreviewPanel({
  previewUrl,
  cut,
  captions,
}: {
  previewUrl: string; // the composition-HTML proxy route response, scoped to `cut`
  cut: VideoCut; // reuse the existing "vertical" | "square" type
  captions: { x?: string | null; tiktok?: string | null };
}) { /* ... */ }
```

It renders inside `VideoPostRow`, between the cut-switcher/re-render row and the existing MP4 `<video>` block, only when a live `previewUrl` is available (composition authored but not yet rendered, or re-rendering) — once the MP4 exists it stays visible as a lighter-weight way to sanity-check captions against the composition without scrubbing the video.

### Layout

A two-column grid at `md:` and up, stacked single column below it — the same collapse breakpoint already used for this row's own action buttons (`flex-col-reverse gap-2 pt-1 sm:flex-row`, `video-post-queue.tsx:260`), but `md:` here because the iframe needs more horizontal room than a button row:

```tsx
<div className="grid grid-cols-1 gap-3 rounded-lg border p-3 md:grid-cols-2">
  {/* left: iframe */}
  {/* right: captions */}
</div>
```

`rounded-lg border p-3` matches the existing `VideoPostRow` container's own `rounded-lg border p-4` (one step down in padding, since this is a nested block) — no new radius or border-color token.

### Left column: iframe

```tsx
<div className="flex items-center justify-center overflow-hidden rounded-md bg-muted">
  <iframe
    src={previewUrl}
    title={`${cut} composition preview`}
    sandbox="allow-scripts"
    className={cut === "vertical" ? "aspect-[9/16] w-full max-w-[180px]" : "aspect-square w-full max-w-[240px]"}
  />
</div>
```

- `sandbox="allow-scripts"` only — no `allow-same-origin`, no `allow-popups`, no network access implied (the compositions are already built offline-only per `motion/README.md`'s render constraints, so the sandboxed iframe cannot reach anything the composition doesn't already vendor).
- The composition's native canvas is 1080px wide (vertical: 1080×1920, square: 1080×1080, per `motion/README.md`); the iframe is displayed at a fixed max-width scaled thumbnail (180px vertical / 240px square) rather than 1:1, matching how the MP4 `<video>` below it is already constrained (`mx-auto max-h-96 w-full`, `video-post-queue.tsx:189`) — full-size inspection is not this panel's job, it is a fast sanity check.
- `bg-muted` fills any letterboxing while the iframe loads, mirroring the "missing cut" placeholder's `border-dashed` box use of the same muted surface (`video-post-queue.tsx:195-197`).
- Reuses the row's existing `cut` state (the same `vertical`/`square` toggle buttons drive both the iframe and the MP4 preview below it — no second switcher).

### Right column: captions

Read-only display of the same captions the composition's `captions.json` proposes (per `motion/README.md`'s `captions.json` schema) — this is a preview of what will be sent, not an edit surface (editing already happens in the existing textareas further down the card):

```tsx
<div className="space-y-2 text-sm">
  <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">X</p>
    <p className="whitespace-pre-wrap">{captions.x ?? "—"}</p>
  </div>
  <div>
    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">TikTok</p>
    <p className="whitespace-pre-wrap">{captions.tiktok ?? "—"}</p>
  </div>
</div>
```

Typography matches the "Drafted CHANGELOG" label pattern already used in `release-proposal-card.tsx:200-205` (`text-xs font-semibold uppercase tracking-wide text-muted-foreground` for the eyebrow label).

### Narrow-viewport behavior

Below `md:` (768px) the grid collapses to a single column: iframe first, captions second, each taking the full row width, `gap-3` between them (the `grid-cols-1` default already declared above — no separate mobile-only class needed). The iframe keeps its own `max-w-[180px]`/`max-w-[240px]` cap and centers via the parent's `flex items-center justify-center`, so it never stretches edge-to-edge on a narrow card even though the grid cell does. This matches how the existing cut-switcher buttons and action row already reflow from a row to a stacked column on narrow viewports (`flex-col-reverse gap-2 ... sm:flex-row`).

---

## Implementation checklist for the frontend developer

- [ ] Add `videoEngineOnly` prop to `ProjectSelector`; backend adds
      `video_engine_enabled` to `ProjectSummary`.
- [ ] `RequestVideoDialog`: add `projectId` state, the picker field (first
      in the form), include it in `canSubmit` and in the `requestVideo`
      mutation payload, reset it alongside the other fields.
- [ ] `RerenderControl`: derive `state` from local edit-dirty tracking +
      mutation status; four visual states per the table above.
- [ ] `CompositionPreviewPanel`: two-column grid, sandboxed iframe scaled
      by orientation, read-only caption display; only rendered when a
      `previewUrl` exists.
- [ ] No new Tailwind tokens, colors, or radii — every class above already
      exists in `video-post-queue.tsx` or `release-proposal-card.tsx`.

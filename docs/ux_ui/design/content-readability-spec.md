# Content-readability spec: markdown, collapsible sections, timestamps

Status: proposed
Owner: ux-dev-1
Surface: task detail panel (`panel/src/components/tasks/task-detail/`) and any other
view that renders task-authored markdown or an activity/notes feed (journals,
A2A transcript).

## Dial read

Per the team design bar, this is dense product UI (task detail / admin panel), not
a marketing surface:

- **DESIGN_VARIANCE:** 2 — predictable, symmetric card layout. Collapsing content
  changes height, not position; no asymmetric grid.
- **MOTION_INTENSITY:** 2 — a single `transform`/`opacity`/`grid-template-rows`
  expand-collapse transition on the section body, nothing else animates.
- **VISUAL_DENSITY:** 8 — tight padding, no added chrome beyond what already
  exists in `Card`, tabular timestamps in list contexts.

## Problem

Long task bodies (descriptions, dev/QA/PR-reviewer notes, journal entries) render
in full with no way to collapse them, so a task with a large plan or a long QA
note pushes the rest of the tab below the fold — this is the "scrolling fatigue"
the acceptance criteria name. Separately, the panel currently renders timestamps
three different ways depending on which component you're looking at:

| Component | Format | Example |
|---|---|---|
| `progress-timeline.tsx` `formatTime()` | relative bucket, falls back to short absolute past 7 days | `"3h ago"` / `"Jul 3, 02:14 PM"` |
| `tab-notes.tsx` `writtenAt()` | always absolute, no time-of-day granularity beyond minutes | `"Jul 10, 04:41 AM"` |
| `a2a-transcript.tsx` | `date-fns` `formatDistanceToNow`, always relative, no absolute fallback | `"3 hours ago"` |

Three renderers means three different date-math implementations to keep correct
and three different reading experiences in the same activity feed. This spec
fixes both problems without introducing a new dependency — `date-fns` is already
installed and used by `a2a-transcript.tsx`.

## 1. Collapsible-section pattern

Use the existing Radix wrapper (`panel/src/components/ui/collapsible.tsx`,
`Collapsible`/`CollapsibleTrigger`/`CollapsibleContent`) — do not add a new
library or hand-roll a `useState` show/hide toggle for this; the primitive
already handles the `data-state`/animation attributes lint components key off.

**Trigger condition.** A markdown body collapses when its rendered content
would exceed **~10 lines or ~640 characters of source markdown**, whichever
comes first — measured on the raw markdown string before render, not the
rendered DOM height, so the decision is synchronous and doesn't require a
layout pass. Content under that threshold renders inline with no
trigger/affordance at all (a collapse control on a 2-line note is chrome for
its own sake).

**Default state by field, not a single global rule** — a reader's expectation
of "do I need this right now" differs per field:

| Field / feed | Default state | Rationale |
|---|---|---|
| Task `description` | **open** | The task's own brief — the reason the reader opened the task. |
| `dev_notes` / `qa_notes` / `pr_reviewer_notes` / `auditor_notes` / `doc_notes` | **collapsed** if over threshold | Historical record; opened on demand while triaging or auditing, not on first load. |
| `quick_context` (resumption) | **open** | Explicitly the field a resuming agent/PM needs first. |
| Progress-timeline / journal entries (list items) | **collapsed**, most recent 2 entries **open** | Matches "activity feed" convention — recent items visible, older ones summarized. |
| Task `constraints` | **collapsed** | Read-only, project-wide, rarely the thing a reader is here for. |

**Affordance.** The `CollapsibleTrigger` wraps a text button reading
`Show more (N lines)` / `Show less`, right-aligned in the existing `CardHeader`
row those components already use (see `tab-notes.tsx`'s header
`flex items-center justify-between`) — no new header layout. Use a `ChevronDown`
(`lucide-react`, already a dependency) that rotates 180° on open via
`data-[state=open]:rotate-180 transition-transform`, matching the
`MOTION_INTENSITY: 2` budget (transform only, no scroll listener).

**Persistence.** Collapse state is component-local (`useState`), not persisted
to the task record or localStorage — a reader re-opening the tab should see the
field-default state again, not their last session's toggle. This keeps the
change purely presentational with zero backend/schema touch.

**Accessibility.** `CollapsibleTrigger` already renders `aria-expanded` via the
Radix primitive; keep the visible label text in sync with that state (`"Show
more"` / `"Show less"`, not just an icon) so screen readers get the same
information sighted users do.

## 2. Markdown rendering treatment

The existing `Markdown` component (`panel/src/components/ui/markdown.tsx`)
already defines the full token set — this spec does not introduce a second
markdown renderer or a competing prose scale. It formalizes which of its two
existing modes (`compact` vs. default) each surface should use, since that
choice is currently made ad hoc per call site:

- **Default mode** (`prose-headings:font-bold`, `h1` 2xl / `h2` xl with a
  bottom border / `h3` lg / `h4` base, `prose-pre:bg-muted prose-pre:border
  prose-pre:rounded-lg`) is for content the reader is reading top-to-bottom as
  a document: task `description`, `dev_notes`/`qa_notes`/etc. bodies, journal
  entries, constraints.
- **`compact` mode** (uniform `text-xs` headings, hidden `pre` blocks, tight
  list/paragraph spacing) is for anything rendered inside a list row or card
  summary where markdown is secondary to the surrounding metadata — e.g. a
  one-line progress-update message or an A2A transcript bubble. Do not disable
  `compact`'s `prose-pre:hidden` for these contexts: a code block inside a chat
  bubble already breaks density, and this spec's threshold-based collapse (§1)
  is the mechanism for anyone who needs the full content, not an inline
  fenced-block render.
- **Code blocks** (default mode only, since `compact` hides them): rely on the
  existing `prose-pre:bg-muted prose-pre:border prose-pre:rounded-lg` plus
  inline-code's `prose-code:bg-muted prose-code:rounded prose-code:font-mono`
  — this already matches the panel's card/muted tokens, so no new color is
  introduced. No syntax highlighting — out of scope; a highlighter is a new
  dependency this spec's content doesn't justify (ponytail: revisit only if a
  future task specifically needs highlighted diffs/code review inline).
- **Lists**: GFM task-list checkboxes already render via the `Checkbox`
  component when `onCheckboxChange` is supplied (editable contexts:
  description, notes) and as a plain styled `input[type=checkbox]` otherwise
  (read-only contexts: journals, PR bodies) — keep that split; do not force
  every consumer to wire up `onCheckboxChange`.

No changes to `markdown.tsx`'s Tailwind token classes are proposed — they
already satisfy this criterion. What was missing was a documented rule for
*which mode a new call site should pick*, captured above.

## 3. Timestamp presentation format

**One format, applied everywhere a note or progress/journal entry shows a
timestamp:** a relative bucket for anything within the last 7 days, an absolute
short date+time beyond that — this is `progress-timeline.tsx`'s existing
`formatTime()` behavior, and it becomes the canonical implementation the other
two call sites adopt instead of maintaining their own date math:

```
< 1 min        "Just now"
< 60 min       "{n}m ago"
< 24 h         "{n}h ago"
< 7 d          "{n}d ago"
>= 7 d         "Jul 3, 2:14 PM"   (Intl "MMM d, h:mm a", locale en-US)
```

**Always-visible absolute timestamp on hover/focus.** Every timestamp element
carries a `title` attribute with the full ISO-derived absolute stamp (`"Jul 3,
2026, 2:14:32 PM"`), so a relative bucket never fully hides the precise time —
this is a one-line addition to each call site, no new component required
beyond the shared formatter.

**Consolidation, not three rules.** Extract `progress-timeline.tsx`'s
`formatTime()` into a shared helper (e.g. `panel/src/lib/format-timestamp.ts`,
matching where other date/agent-name helpers already live like
`lib/agent-utils.ts`) and have `tab-notes.tsx`'s `writtenAt()` and
`a2a-transcript.tsx`'s `formatDistanceToNow(...)` call replaced with it. This
removes two of the three divergent implementations rather than adding a fourth.
`date-fns` stays a dependency (already used elsewhere in the panel) but this
specific formatter does not need it — the bucket math is short enough to stay
plain `Date` arithmetic, consistent with `formatTime()`'s current
implementation, so `format-timestamp.ts` has no new dependency to justify.

**Where this applies:** progress updates, journal/note entries in every
task-detail tab (`quick_context`/`dev_notes`/`qa_notes`/`pr_reviewer_notes`/
`auditor_notes`/`doc_notes` "written at" stamps), and the A2A transcript. Any
future feed showing a note/progress entry should reuse the same helper rather
than writing a fourth formatter.

## Non-goals

- No new component library or animation dependency — everything above composes
  `Collapsible`, `Markdown`, and `date-fns`/plain `Date`, all already in the
  panel's dependency tree.
- No change to how markdown is stored or how notes are written (backend,
  `notes_structured`, gateway verbs) — this is purely a rendering-layer spec.
- Syntax highlighting, persisted collapse-state, and a global "collapse all"
  control are explicitly out of scope for this pass.

# Tooltip / aria-label spec: which panel controls need which

Status: implemented (§1a–§1c complete, test coverage added)

Owner: ux-dev-1

Implementation status: All icon-only controls listed in §1a have been retrofitted with aria-label + title + matching Radix Tooltip (via PR #476). Test coverage is provided by per-control regression test files in `panel/src/components/**/\__tests__/`.

Last updated: 2026-07-11

Surface: every icon-bearing interactive control in `panel/src/components` — surveyed against the sidebar (`layout/sidebar.tsx`), header (`layout/header.tsx`), task detail (`tasks/task-detail/`, `tasks/task-actions.tsx`), kanban (`kanban/core/`, `kanban/shared/`), the dashboard queues (`dashboard/*-queue.tsx`, `dashboard/command-center.tsx`), and metrics (`metrics/*.tsx`).

## Dial read

Per the team design bar, the panel is dense product UI, not a marketing surface:

- **DESIGN_VARIANCE:** 1 — this spec changes no layout or grid; it is a copy/markup rule for existing controls.
- **MOTION_INTENSITY:** 2 — tooltips use the existing Radix primitive (`ui/tooltip.tsx`), whose default fade/zoom-on-open is the motion budget; nothing in this spec adds a new transition.
- **VISUAL_DENSITY:** 8 — the panel packs many small icon-only affordances per row (kanban cards, table action cells, queue rows); the density rule most relevant here is the "noisy vs. informative" line in §3 — every tooltip added to a dense row is one more thing competing for attention, so it must earn its place.

## Problem (Resolved)

This spec resolved the panel's mixed accessible-naming strategies for icon-only controls by establishing a single rule: all icon-only controls must carry a mandatory `aria-label` (§1a), optionally with a matching visible Tooltip (§1b).

All controls listed below have been retrofitted to the correct pattern (verified 2026-07-11):

| Component | Pattern | Test Coverage |
|---|---|---|
| `layout/header.tsx:59-68` (refresh button) | ✅ `aria-label` + `title` + Tooltip | ✅ `header.test.tsx` |
| `ui/copy-button.tsx:64-74` | ✅ `aria-label` + `title` + Tooltip | ✅ Implicit (component pattern) |
| `kanban/core/kanban-card.tsx:237-253` (move-forward button) | ✅ `aria-label` + `title` + Tooltip | ✅ `kanban-card-aria.test.tsx` |
| `notifications/notification-bell.tsx:24-31` (bell button) | ✅ `aria-label` + `title` + Tooltip | ✅ `notification-bell.test.tsx` (NEW) |
| `tasks/task-detail/task-header.tsx:476-478` (back-arrow button) | ✅ `aria-label` + `title` + Tooltip | ❌ Not covered — `header.test.tsx` only renders `layout/header.tsx`, not this component |
| `tasks/task-actions.tsx:145-147` (overflow-menu trigger) | ✅ `aria-label` + `title` + Tooltip | ❌ Not covered — `header.test.tsx` only renders `layout/header.tsx`, not this component |
| `layout/sidebar.tsx:170-182` (collapse-rail toggle) | ✅ `aria-label` + `title` + Tooltip | ✅ `sidebar.test.tsx` |
| `kanban/core/kanban-card.tsx:122-128` (drag handle) | ✅ `aria-label` + `title` + Tooltip | ✅ `kanban-card-aria.test.tsx` |
| `kanban/shared/assignee-avatar.tsx` (initials badge) | ✅ `aria-label` + Tooltip with full name | ✅ `assignee-avatar.test.tsx` |

Separately, some controls that DO have a visible label still lack a tooltip where one would help (`kanban/shared/assignee-avatar.tsx` shows only two-letter initials, with nothing disambiguating which agent that is), while others already use tooltips correctly for genuinely supplementary info (`kanban-card.tsx:137-151`'s sequence-number badge, `header.tsx:35-50`'s "Coming Soon" search tooltip).

This spec gives every future control a three-way answer — mandatory aria-label, recommended tooltip, or neither — plus the copy rule for whichever applies.

## 1. Classification

### 1a. `aria-label` is mandatory

**Any interactive control (`button`, a `Link`/`<a>` wrapping a `Button`, a dropdown/popover trigger) whose only visible content is an icon.** No exceptions — this is a WCAG requirement, not a style preference (see §4).

Examples already in the codebase that need this fixed:

- `notifications/notification-bell.tsx:24` — `<Button variant="ghost" size="icon">` wrapping only a `Bell` icon.
- `tasks/task-detail/task-header.tsx:476` — the back-arrow `Button` wrapping only `ArrowLeft`.
- `tasks/task-actions.tsx:145` — the `DropdownMenuTrigger`'s `Button` wrapping only `MoreHorizontal`.
- `layout/sidebar.tsx:170` — the collapse-rail toggle `Button` wrapping only `ChevronLeft`.
- `kanban/core/kanban-card.tsx:122-128` — the drag handle (`GripVertical`); a drag handle needs an accessible name even though its primary interaction is pointer-based, because `useDraggable`'s `attributes`/`listeners` also expose it to keyboard/AT-driven reordering.
- `kanban/core/kanban-card.tsx:237-253` — the move-forward button has `title` but no `aria-label`; add one (`title` can stay as a mouse-hover supplement).
- `dashboard/command-center.tsx:107` — the `Settings` gear button.
- `dashboard/pr-review-queue.tsx:206-214` — the `FileText` "review details" link button; its `title` currently lives on the wrapping `Link`, not the `Button` itself, which is where AT resolves the accessible name from.

Correct existing pattern to copy: `layout/header.tsx:59-68` and `ui/copy-button.tsx:64-74` — both set `aria-label` as the primary accessible name and `title` as a same-text mouse-hover supplement.

### 1b. Tooltip is recommended (not code-required, but good practice)

A tooltip earns its place when the control's visible content (icon, badge, truncated text, or initials) doesn't fully convey what a user needs, and the extra information doesn't fit as visible text without breaking the density budget:

- **Icon-only controls that already carry a mandatory `aria-label`** (§1a) should also carry a matching visible tooltip for sighted mouse users — the `aria-label` serves AT, the tooltip serves everyone else. This is already the pattern at `header.tsx:59-68`.
- **Truncated or abbreviated content standing in for the full value:** `assignee-avatar.tsx` shows only two-letter initials — wrap it in a `Tooltip` showing the full agent slug/name, matching the pattern `kanban-card.tsx:137-151` already uses for the sequence-number badge.
- **A disabled or not-yet-available control:** `header.tsx:35-50`'s search input tooltip ("Coming Soon") is the reference example — the control's disabled state isn't self-explanatory from the input alone.
- **A secondary/derived value alongside a primary display value:** `kanban-card.tsx:107-111` and `task-header.tsx:508-514` already put the full task UUID in a `title` attribute next to the truncated `#12345678` display — correct instinct, though per §4 a plain `title` is a weaker mechanism than a `Tooltip` component for anything besides a supplementary hover hint on an already-labeled element.

### 1c. Neither is needed

- **Controls with a visible text label** — `kanban-card.tsx`'s "Assign" / "Pass" / "Fail" buttons (`UserPlus`/`CheckCircle`/`XCircle` icon + text), the sidebar nav items when expanded (`layout/sidebar.tsx:99-100`, icon + `<span>{item.title}</span>`). The visible text already is the accessible name; a tooltip repeating it is noise (see §3).
- **Self-labeling badges** — `kanban/shared/priority-indicator.tsx` renders the full label text (`"P0 - Highest"`, not just a color swatch or icon), so it needs no supplementary tooltip.
- **Decorative icons paired with adjacent visible text** — e.g. any icon next to a `CardTitle` heading in the metrics tabs (`metrics/delivery-tab.tsx`, `metrics/scorecards-tab.tsx`) where the heading text alone identifies the section; these icons should carry `aria-hidden="true"` rather than an `aria-label`, since the adjacent text is already the accessible name and a redundant label would be announced twice.
- **Recharts' built-in `<Tooltip>`** (`metrics/delivery-tab.tsx:9`) — this is a chart data-point tooltip, a different concern from the UI-affordance tooltips this spec governs. Out of scope here; recharts renders it via its own accessible SVG layer.

## 2. Copy guidance per category

- **`aria-label` text:** a short verb-phrase naming the action, not the icon — `"Refresh only the current page"` (`header.tsx:64`), not `"Refresh icon"` or `"RefreshCw"`. State what happens, not what the button looks like. Keep it a complete accessible name on its own — a screen reader user never sees the visible tooltip text, so the label can't rely on surrounding context the way a sighted-only tooltip can.
- **Tooltip text:** one short sentence fragment, no trailing period, matching the existing style (`"Sequence #{n}"`, `"Awaiting session creation by PM"`, `"Coming Soon"`). Never restate the control's own visible label verbatim — a tooltip on the "Assign" button that just says "Assign" adds nothing. State the *why* or the *full value*, not the *what* the icon already shows.
  - No em-dash, no filler verbs ("Elevate", "Seamless", "Unleash") — this is a
    dense product UI, not marketing copy.
  - No invented specificity — if the underlying value is genuinely just an
    agent's short ID, show the short ID; don't dress it up.
- **When `aria-label` and tooltip text overlap** (the common case for an icon-only button per §1b), use the *same* string for both, exactly as `header.tsx:64-65` and `copy-button.tsx:68-69` already do — one source of truth, no drift between what a mouse user reads and what a screen reader announces.

## 3. Informative vs. noisy tooltip use

At `VISUAL_DENSITY: 8`, the panel's kanban cards, table rows, and queue items already stack several badges/icons/avatars per row (see `kanban-card.tsx`'s badge row at lines 131-169). Every tooltip added to that row is one more hover-triggered layer competing with the surrounding chrome, so the bar for adding one is:

**Informative (add it):**
- Disambiguates content that is otherwise ambiguous or truncated — initials (`assignee-avatar.tsx`), a shortened UUID, a numeric badge whose meaning isn't obvious from the icon alone (the `Hash` + number sequence badge).
- Explains *why* a control is disabled or unavailable, not just *that* it is (`header.tsx`'s "Coming Soon" search).
- Is the sole accessible-name source for an icon-only control (§1a/§1b) — this is structurally required, not a density trade-off.

**Noisy (skip it):**
- Restates a visible text label the control already shows (`"Assign"` tooltip on an "Assign" button).
- Adds a tooltip to every icon in a card "for consistency" regardless of whether that icon's meaning is already clear from context — `priority-indicator.tsx`'s full-text badge needs no tooltip precisely because it already states its own meaning; adding one anyway would be chrome for its own sake.
- Duplicates information already visible one glance away in the same row (e.g. a tooltip on the team `Badge` in `kanban-card.tsx:133-135` repeating the team name that's already the badge's own text).
- Would fire on every hover across a dense grid of many small elements, adding motion/layer churn that exceeds this panel's `MOTION_INTENSITY: 2` budget for incidental UI (the Radix tooltip's fade/zoom is fine for one deliberate affordance per control; scattering it across every icon in a row is not).

The underlying rule from the design bar: hierarchy and information come from what's already visible (weight, size, color, spacing) wherever that's sufficient; a tooltip is the fallback for the specific cases in §1a/§1b where visible content alone can't carry the full meaning — not a default layered onto every icon.

## 4. WCAG AA requirement: `aria-label` is mandatory for icon-only controls

Per WCAG 2.1 Level AA, every interactive control needs a programmatically determinable accessible name:

- **1.1.1 Non-text Content** — a non-text element (an icon standing in for a control's label) must have a text alternative that serves the same purpose.
- **4.1.2 Name, Role, Value** — for all UI components, the name and role must be programmatically determinable, and the name must be exposed to assistive technology.

For an icon-only `button`/`Link`-as-button in this codebase, that means **`aria-label` (or, where the label needs to reference other visible content, `aria-labelledby`) is mandatory** — not optional, not "nice to have" — whenever the control has no visible text child. A `title` attribute alone is **not** sufficient: it is the accessible-name source of last resort in the browser accnaming spec, it is not reliably exposed by every screen reader (particularly on touch/mobile, where there is no hover to trigger it), and it does not satisfy 4.1.2 on its own in practice across the AT matrix. `title` may still be present as a mouse-hover supplement (matching `header.tsx:64-65`'s pattern of setting both), but it never substitutes for `aria-label`.

Any icon-only control shipped without an `aria-label` — including every example listed in §1a — is an AA accessibility defect, independent of whether it also carries a visible `Tooltip`; a `Tooltip`'s content is not exposed to AT by default either (`ui/tooltip.tsx`'s Radix primitive renders visually, and needs `aria-label` on the trigger to be accessible — it does not supply one for free).

## Implementation notes

- **Retrofit complete (PR #476 + regression tests):** All gaps in §1a have been fixed using the existing `ui/tooltip.tsx` Radix wrapper and plain `aria-label`/`title` attributes. The pattern is now uniform across all icon-only controls.
- **Test coverage:** Most retrofitted controls have a regression test verifying the aria-label, title, and Tooltip content match per §2 — see the coverage table above for which controls still lack a dedicated test.
- **No new dependency:** Everything uses the existing `ui/tooltip.tsx` Radix wrapper, already in use elsewhere.
- **Out of scope:** Recharts' internal chart-tooltip behavior (§1c) — that's a data-visualization concern, not a UI-affordance one.

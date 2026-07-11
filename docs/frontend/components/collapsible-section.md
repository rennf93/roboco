# CollapsibleSection component

A reusable Card wrapper that enables independent collapse/expand of section content, allowing users to navigate long pages (like task-detail) without forcing continuous scrolling. Collapse/expand animations use only opacity and transform (no height/width), respecting prefers-reduced-motion globally.

## Purpose

When task-detail pages carry long descriptions, many notes, and detailed plans, users must scroll through all expanded content to reach later sections. `CollapsibleSection` wraps each logical section (Description, Constraints, Notes fields, Plan subsections) in a collapsible card so users can fold away irrelevant content and jump to what they need. The component supports both **controlled** (e.g., force-open while editing) and **uncontrolled** (stateless) modes.

## Files

| File | Role |
|------|------|
| `panel/src/components/tasks/task-detail/collapsible-section.tsx` | Component definition, `CollapsibleSectionProps` interface, state management. |
| `panel/src/components/tasks/task-detail/task-description.tsx` | Description and Constraints sections wrapped. Constraints styled with amber accent border/background + ShieldAlert icon for visual distinction. |
| `panel/src/components/tasks/task-detail/tab-notes.tsx` | Each editable note field (Description, Notes, Plan) wrapped. Edit/preview toggle is force-open while editing. |
| `panel/src/components/tasks/task-detail/tab-plan.tsx` | Approach, Sub-Tasks, Technical Considerations, Risks, and Open Questions sections wrapped. |
| `panel/src/app/globals.css` | Global `prefers-reduced-motion: reduce` override that disables all animations/transitions for users with reduced-motion enabled. |

## API

### `CollapsibleSectionProps`

```typescript
interface CollapsibleSectionProps {
  /** Card title content (icon + text + badges as needed) */
  title: ReactNode;
  
  /** Right-aligned header controls (edit/preview toggles, buttons) — always visible */
  actions?: ReactNode;
  
  /** Controlled open state (e.g. force-open while a section is mid-edit). Omit for uncontrolled. */
  open?: boolean;
  
  /**
   * Whether the (uncontrolled) section starts expanded. Takes precedence
   * over `content`-derived collapsing. Omit to let `content` decide, or to
   * default open when neither is given (so nothing visible today disappears).
   */
  defaultOpen?: boolean;
  
  /**
   * Plain-text representation of the section's body, used to derive
   * `defaultOpen` per the content-readability spec (~10 lines / ~640 chars)
   * when `defaultOpen` is not explicitly set. Ignored otherwise.
   */
  content?: string;
  
  /** Callback when the user toggles the section open/closed. */
  onOpenChange?: (open: boolean) => void;
  
  /** Tailwind class string applied to the outer Card element. */
  className?: string;
  
  /** Tailwind class string applied to the CardHeader (title + actions row). */
  headerClassName?: string;
  
  /** Content rendered inside CardContent when the section is open. */
  children: ReactNode;
}
```

### Component behavior

- **Uncontrolled mode** (omit `open` prop): component manages its own open state. `defaultOpen` determines initial state; if `defaultOpen` is omitted, the component uses `content`-derived collapsing (if `content` is provided), or defaults to `true` if neither is set. `onOpenChange` is called when the user clicks the toggle; internal state updates automatically.
- **Controlled mode** (`open` prop set): `onOpenChange` is called on toggle, but internal state is not updated; parent must update the `open` prop. Useful to force a section open while a user is editing (e.g., `open={isEditing || sectionOpen}`).
- **Content-driven defaultOpen** (new): when `content` is provided without an explicit `defaultOpen`, the component checks if the content exceeds the readability thresholds (~10 lines / ~640 characters, per `content-readability.ts`). If it does, the section defaults collapsed; otherwise, it defaults open. This keeps long lists/sections from forcing continuous scrolling. An explicit `defaultOpen` prop always takes precedence over this logic, maintaining backward compatibility with existing callers.
- **Title and actions**: title is always visible in the header; actions (right side) are also always visible, never collapsed away. This allows edit/preview toggles, save/cancel buttons, etc. to remain accessible.
- **ChevronDown icon**: rotates -90° when closed, 0° when open. Uses `transition-transform duration-200` so the rotation animates smoothly.

### Animation

Collapse/expand uses fade + slide from Tailwind CSS's `tw-animate-css` utilities:

```tsx
"duration-200 data-[state=closed]:animate-out data-[state=open]:animate-in",
"data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
"data-[state=closed]:slide-out-to-top-1 data-[state=open]:slide-in-from-top-1",
```

- **Duration**: 200ms
- **Animation type**: fade (opacity) + slide (translateY), both controlled via transform/opacity CSS properties only — no height/width animation, so layout does not reflow mid-animation.
- **Accessibility**: `prefers-reduced-motion: reduce` is handled globally in `panel/src/app/globals.css`, which sets `animation-duration` and `transition-duration` to 0.01ms for all elements when the user has enabled reduced motion in their OS settings. The section content still opens/closes; it just doesn't animate.

## How to use

Wrap any section content that should be collapsible:

```tsx
"use client";

import { useState } from "react";
import { CollapsibleSection } from "./collapsible-section";
import { FileText, Edit3 } from "lucide-react";

export function MySection() {
  const [sectionOpen, setSectionOpen] = useState(true);
  const [isEditing, setIsEditing] = useState(false);
  const sectionText = "Section content here."; // Plain-text representation

  return (
    <CollapsibleSection
      title={
        <>
          <FileText className="h-5 w-5" />
          Section Title
        </>
      }
      actions={
        <Button size="sm" variant="ghost" onClick={() => setIsEditing(true)}>
          <Edit3 className="h-4 w-4 mr-1" />
          Edit
        </Button>
      }
      content={sectionText}  // Optional: drive defaultOpen based on content length
      open={isEditing || sectionOpen}
      onOpenChange={setSectionOpen}
    >
      <p>{sectionText}</p>
    </CollapsibleSection>
  );
}
```

### Using content-driven defaultOpen

To automatically collapse long sections without explicit `defaultOpen`:

```tsx
const listText = items.map(item => item.title).join("\n");

<CollapsibleSection
  title="Long List"
  content={listText}  // Checked against ~10 lines / ~640 chars thresholds
>
  <ul>
    {items.map(item => (
      <li key={item.id}>{item.title}</li>
    ))}
  </ul>
</CollapsibleSection>
```

If `listText` exceeds the readability thresholds, the section defaults collapsed; otherwise, it defaults open. No explicit `defaultOpen` prop needed.

### Controlled vs. uncontrolled

**Uncontrolled (simple case):**

```tsx
<CollapsibleSection title="Notes" defaultOpen={true}>
  <p>Your notes content.</p>
</CollapsibleSection>
```

The component manages open state internally. `onOpenChange` is optional; if provided, it's called for logging/debugging, but state still updates automatically.

**Controlled (e.g., force-open while editing):**

```tsx
const [sectionOpen, setSectionOpen] = useState(true);
const [isEditing, setIsEditing] = useState(false);

<CollapsibleSection
  title="Notes"
  open={isEditing || sectionOpen}
  onOpenChange={setSectionOpen}
>
  {isEditing ? <textarea /> : <p>Rendered content.</p>}
</CollapsibleSection>
```

When `isEditing` is `true`, the section is forced open even if the user clicked to close it. This prevents an edit form from being hidden mid-interaction.

### Combined: controlled mode + content-driven initialization

For components that edit long content (like task notes), seed the initial collapsed/expanded state from content length, but use controlled mode to force-open during edit:

```tsx
const currentValue = task.dev_notes;
const [isEditing, setIsEditing] = useState(false);
const [sectionOpen, setSectionOpen] = useState(() =>
  !exceedsReadabilityThreshold(currentValue ?? ""),
);

<CollapsibleSection
  title="Developer Notes"
  content={currentValue ?? undefined}  // Drives content-readability check
  open={isEditing || sectionOpen}       // Controlled: force-open while editing
  onOpenChange={setSectionOpen}
>
  {isEditing ? <textarea value={currentValue} /> : <p>{currentValue}</p>}
</CollapsibleSection>
```

This pattern (used in `tab-notes.tsx`'s `EditableNoteCard`) ensures:
- Long notes default collapsed with an expand affordance
- Short notes default expanded (fully visible)
- Edit forms are always visible when editing, even if the user had collapsed the section
- User's collapse/expand choice persists across edit cycles (via `sectionOpen` state)

## Used in

The component is now applied across task-detail pages:

| Page / Component | Sections wrapped | Notes |
|---|---|---|
| `task-description.tsx` | Description, Constraints | Constraints section styled with amber border/background + ShieldAlert icon for visual distinction from authored content. |
| `tab-notes.tsx` | Each editable note field (dev_notes, qa_notes, doc_notes, etc.) | `EditableNoteCard` seeds initial `sectionOpen` from content length via `exceedsReadabilityThreshold`, passes `content={currentValue}` to CollapsibleSection, and forces open while editing via controlled `open={isEditing \|\| sectionOpen}`. Long notes default collapsed with expand affordance; short notes default expanded. |
| `tab-plan.tsx` | Approach, Sub-Tasks, Technical Considerations, Risks, Open Questions | Each sub-section independently collapsible. |
| `acceptance-criteria.tsx` | Full acceptance criteria list | Wrapped in CollapsibleSection with `content={criteriaText}`, so a long AC list defaults collapsed per content-readability spec. Forced open while adding/editing via controlled `open` prop. |
| `tab-progress.tsx` | Individual progress updates and checkpoints (via internal Radix Collapsible wrapper) | Each entry wrapped in a collapsible section; only the 2 most recent entries default open (gated by content length as well). Older entries default collapsed even if short, keeping task detail navigable without endless scrolling. |

## Design decisions

- **Content-driven defaultOpen (new)**: instead of always defaulting open (which forced tasks with long histories to be fully expanded), the component now checks content length against readability thresholds (~10 lines / ~640 characters) when `defaultOpen` is not explicitly set. Long content defaults collapsed, keeping task-detail pages navigable. The explicit `defaultOpen` prop always takes precedence, so existing callers (task-description, tab-notes, tab-plan) that pass `open={...}` are unaffected — the content-length check only applies to uncontrolled sections. This is the "content-readability spec" driving progress and AC collapse in tab-progress and acceptance-criteria.

- **Fade + slide animation only**: opacity and transform are GPU-accelerated and don't trigger layout reflow. Height/width animations are avoided because they force the browser to recalculate layout mid-animation, causing jank on slower devices and making the motion distracting.

- **Controlled + uncontrolled modes**: uncontrolled is the default for simple read-only sections (no extra state management needed), while controlled mode (via `open` prop) lets parent components force a section open during edit (the common case for tab-notes EditableNoteCard).

- **Always-visible actions**: the actions slot (buttons, toggles) is never collapsed, so users can always edit, delete, or perform actions on a section without expanding it first.

- **ChevronDown icon rotates, not replaces**: using a rotating icon is more intuitive and uses less real estate than swapping between two different icons (Chevron-Down vs. Chevron-Up).

- **Global prefers-reduced-motion override**: instead of checking `prefers-reduced-motion` in JavaScript (which is error-prone and scattered across components), a single global CSS rule ensures that **all** animations and transitions respect the user's OS setting. No component logic needed.

- **`CardTitle` inside the trigger**: the title is inside a styled `<button>` (the CollapsibleTrigger) so it's keyboard-accessible and screenreader-labeled. The button is full-width (flex-1) and text-left, so users can click anywhere on the title to toggle.

## Testing

The test suite covers:

- Rendering a section with title, children, and optional actions.
- Toggling the section open/closed on click.
- Checking `aria-expanded` attribute on the trigger button.
- Verifying that content is hidden when closed (not just visually; `CollapsibleContent` removes it from the DOM).
- Controlled vs. uncontrolled state management.
- Animation classes applied to `CollapsibleContent` based on open state.
- ChevronDown icon rotation (CSS class toggling).
- Always-visible actions slot (buttons not collapsed away).

See `panel/src/components/tasks/task-detail/__tests__/task-description.test.tsx` for integration tests covering:
- Independent collapse of Description and Constraints sections.
- Controlled open state while editing.
- Edit/preview toggle working alongside collapse behavior.

## Related work

- **Task detail: collapsible markdown sections + distinct Constraints styling** — this commit. Introduces `CollapsibleSection` and applies it to task-description.tsx, tab-notes.tsx, tab-plan.tsx. Constraints section gets distinct amber styling.

## Migration / rollout

For developers adding a new collapsible section to task-detail or other pages:

1. Import `CollapsibleSection` from `./collapsible-section` (adjust path as needed).
2. Wrap the section content, provide a `title` (can include icons, badges).
3. Optionally provide `actions` (buttons, toggles that should stay visible).
4. **For sections with potentially long content** (lists, histories): pass a plain-text `content` prop so the section automatically collapses if the content is long. This defers to the content-readability spec thresholds (~10 lines / ~640 chars).
5. For editable content, use the controlled pattern: `open={isEditing || sectionOpen}` to force-open while editing. The controlled `open` prop takes precedence over content-driven collapse.
6. No extra state management is needed for read-only sections; the component handles it internally.

Example (uncontrolled with content-driven collapse):

```tsx
const listText = items.map(item => item.name).join("\n");

<CollapsibleSection
  title="My List"
  content={listText}  // Drives defaultOpen based on length
>
  <ul>
    {items.map(item => (
      <li key={item.id}>{item.name}</li>
    ))}
  </ul>
</CollapsibleSection>
```

Example (controlled, forcing open during edit):

```tsx
const [sectionOpen, setSectionOpen] = useState(true);
const [isEditing, setIsEditing] = useState(false);

<CollapsibleSection
  title="My Section"
  open={isEditing || sectionOpen}  // Controlled: explicit `open` wins over content
  onOpenChange={setSectionOpen}
>
  {isEditing ? <textarea /> : <p>Content here.</p>}
</CollapsibleSection>
```

## Accessibility

- **`aria-expanded` attribute** on the trigger button communicates the open/closed state to screenreaders.
- **Keyboard support**: the trigger is a `<button>`, so it's focusable with Tab and activatable with Enter/Space.
- **`aria-hidden="true"` on the ChevronDown icon**: the icon is decorative; screenreaders skip it.
- **`prefers-reduced-motion` support**: animations are disabled globally for users with motion sensitivity, but the section still opens/closes (the content is not hidden, just instant).

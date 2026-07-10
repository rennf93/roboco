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
  
  /** Whether the (uncontrolled) section starts expanded. Defaults to true so nothing visible today disappears. */
  defaultOpen?: boolean;
  
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

- **Uncontrolled mode** (omit `open` prop): component manages its own open state. `defaultOpen` determines initial state (defaults to `true`). `onOpenChange` is called when the user clicks the toggle; internal state updates automatically.
- **Controlled mode** (`open` prop set): `onOpenChange` is called on toggle, but internal state is not updated; parent must update the `open` prop. Useful to force a section open while a user is editing (e.g., `open={isEditing || sectionOpen}`).
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
      open={isEditing || sectionOpen}
      onOpenChange={setSectionOpen}
    >
      <p>Section content here.</p>
    </CollapsibleSection>
  );
}
```

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

## Used in

The component is now applied across task-detail pages:

| Page / Component | Sections wrapped | Notes |
|---|---|---|
| `task-description.tsx` | Description, Constraints | Constraints section styled with amber border/background + ShieldAlert icon for visual distinction from authored content. |
| `tab-notes.tsx` | Each editable note field (Description, Notes, Plan) | Edit/preview toggle forced open while editing via `open={isEditing \|\| sectionOpen}`. |
| `tab-plan.tsx` | Approach, Sub-Tasks, Technical Considerations, Risks, Open Questions | Each sub-section independently collapsible. |

## Design decisions

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
4. For editable content, use the controlled pattern: `open={isEditing || sectionOpen}` to force-open while editing.
5. No extra state management is needed for read-only sections; the component handles it.

Example:

```tsx
const [sectionOpen, setSectionOpen] = useState(true);

<CollapsibleSection
  title="My Section"
  open={sectionOpen}
  onOpenChange={setSectionOpen}
>
  <p>Content here.</p>
</CollapsibleSection>
```

## Accessibility

- **`aria-expanded` attribute** on the trigger button communicates the open/closed state to screenreaders.
- **Keyboard support**: the trigger is a `<button>`, so it's focusable with Tab and activatable with Enter/Space.
- **`aria-hidden="true"` on the ChevronDown icon**: the icon is decorative; screenreaders skip it.
- **`prefers-reduced-motion` support**: animations are disabled globally for users with motion sensitivity, but the section still opens/closes (the content is not hidden, just instant).

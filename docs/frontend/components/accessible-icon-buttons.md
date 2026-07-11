# Accessible Icon-Only Controls (aria-label + Tooltip)

## Overview

Icon-only controls—buttons without visible text—require two layers of accessibility to be usable by all:

1. **aria-label** attribute for screen reader users  
2. **Visible Tooltip** (matching text) for mouse, keyboard, and screen reader users

Both layers must use identical text that describes the action or result.

## Pattern

All icon-only controls in the panel follow this structure:

```typescript
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";

const LABEL = "Open settings";

<TooltipProvider>
  <Tooltip>
    <TooltipTrigger asChild>
      <Button
        aria-label={LABEL}
        title={LABEL}
      >
        <Settings className="h-5 w-5" />
      </Button>
    </TooltipTrigger>
    <TooltipContent>{LABEL}</TooltipContent>
  </Tooltip>
</TooltipProvider>
```

### Why three attributes?

- **aria-label**: The accessible name for screen readers
- **title**: Browser native tooltip (fallback, appears on hover/focus)
- **TooltipContent**: Radix UI tooltip for consistent visual feedback

### Naming convention

Label text uses **active verbs** describing what happens when the control is clicked:

| Control | Label |
|---------|-------|
| Settings gear | "Open settings" |
| Notification bell | "View notifications" |
| Back arrow | "Go back to tasks list" |
| Collapse toggle | "Collapse sidebar" / "Expand sidebar" |
| Drag handle | "Drag to move task between columns" |
| Menu trigger | "Open task actions menu" |

Avoid passive voice ("Settings opened") or generic labels ("Button").

## State-dependent labels

When a control's action varies by state, compute the label dynamically:

```typescript
const toggleLabel = sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar";

<Button
  aria-label={toggleLabel}
  title={toggleLabel}
>
  {/* ... */}
</Button>
```

The label updates whenever state changes, keeping screen reader users informed.

## Truncated content (avatars, badges)

When an icon-only control displays shortened content (e.g., "FD1" for "Frontend Dev 1"), wrap in a tooltip showing the full value:

```typescript
<TooltipProvider>
  <Tooltip>
    <TooltipTrigger asChild>
      <Avatar>
        <AvatarFallback>{initials}</AvatarFallback>
      </Avatar>
    </TooltipTrigger>
    <TooltipContent>{fullName}</TooltipContent>
  </Tooltip>
</TooltipProvider>
```

## When NOT to use this pattern

**Do not** apply aria-label + tooltip to:

- **Text-labeled buttons** — the text is the label
- **Decorative icons** — non-interactive graphics (use `aria-hidden="true"` instead)
- **Self-labeling badges** — content + styling conveys meaning
- **Chart tooltips** — handled by the charting library (recharts, etc.)

## Implemented controls

The following 8 icon-only controls have been retrofitted:

1. **notification-bell.tsx** — "View notifications"
2. **task-header.tsx** (back button) — "Go back to tasks list"
3. **task-actions.tsx** (menu) — "Open task actions menu"
4. **sidebar.tsx** (collapse toggle) — "Collapse sidebar" / "Expand sidebar"
5. **kanban-card.tsx** (drag handle) — "Drag to move task between columns"
6. **kanban-card.tsx** (move-forward) — "Move forward" / "PM must activate this task first"
7. **command-center.tsx** (settings) — "Open settings"
8. **pr-review-queue.tsx** (details link) — "Review details"

Plus one avatar tooltip:

9. **assignee-avatar.tsx** — Shows full agent display name

## Testing

### Screen reader
1. Tab to the icon-only control
2. Verify the aria-label is announced
3. Focus should be visible and clear

### Mouse
1. Hover over the control
2. Tooltip appears with the same text as aria-label
3. Click triggers the expected action

### Keyboard
1. Tab to focus the control
2. title attribute provides a browser tooltip
3. Verify the label is consistent

### State changes
1. For conditional labels, verify the label updates when state changes
2. Tab away and back to re-announce the new label

## TooltipProvider scope

Each component wraps its controls in a local `<TooltipProvider>` (not a single app-root provider). This pattern:

- Keeps tooltip state scoped to the component
- Matches existing codebase patterns
- Simplifies DOM structure and reduces global state

If a future refactoring uses a root-level provider, the structure remains valid—only the wrapping changes, not the aria-label/title pattern.

## Resources

- [WCAG 2.1: Text Alternatives for Images](https://www.w3.org/WAI/WCAG21/Understanding/text-alternatives)
- [ARIA Authoring Practices: Buttons](https://www.w3.org/WAI/ARIA/apg/patterns/button/)
- [Radix UI Tooltip](https://www.radix-ui.com/docs/primitives/components/tooltip)
- Local test files: See `kanban-card-aria.test.tsx`, `sidebar.test.tsx`, `assignee-avatar.test.tsx`

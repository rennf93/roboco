# Prompter — UX/UI Design Deliverables

This directory contains the interaction design and confirmation flow specification for the **Prompter** feature (Phase 1). All patterns are mapped to the existing Panel design system so the Frontend Cell can implement them in parallel without inventing new visual language.

## Contents

| Document | Purpose |
|----------|---------|
| [`01-interaction-spec.md`](01-interaction-spec.md) | End-to-end chat → draft → review → confirm → launch flow, state machine, component mappings, error/loading patterns |
| [`02-confirmation-interstitial.md`](02-confirmation-interstitial.md) | Mandatory human-in-the-loop review modal: layout, copy, actions, un-bypassable guardrails |
| [`03-model-selector-ux.md`](03-model-selector-ux.md) | Model selector placement, defaults, and cognitive-load reduction |
| [`04-naming-and-navigation.md`](04-naming-and-navigation.md) | Naming alternatives to "Prompter" and sidebar nav placement |

## Design System Baseline

All screens are built from components already present in `panel/src/components/ui/` and `panel/src/components/layout/`:

- **Dialog** — `panel/src/components/ui/dialog.tsx` (Radix-based, animates in/out)
- **AlertDialog** — `panel/src/components/ui/alert-dialog.tsx` (for destructive/breaking confirmations)
- **Card** — `panel/src/components/ui/card.tsx` (sections, draft preview)
- **Tabs** — `panel/src/components/ui/tabs.tsx` (chat vs. draft review)
- **Select** — `panel/src/components/ui/select.tsx` (team, model, status)
- **Collapsible** — `panel/src/components/ui/collapsible.tsx` (advanced options drawer)
- **Button** — `panel/src/components/ui/button.tsx` (primary, outline, ghost, destructive)
- **Input / Textarea** — `panel/src/components/ui/input.tsx`, `panel/src/components/ui/textarea.tsx`
- **Badge** — `panel/src/components/ui/badge.tsx` (team labels, complexity indicators)
- **ScrollArea** — `panel/src/components/ui/scroll-area.tsx` (chat history, criteria list)
- **Skeleton** — `panel/src/components/ui/skeleton.tsx` (loading states)
- **Sidebar** — `panel/src/components/layout/sidebar.tsx` (navigation structure)

> **Rule**: No new visual language. Reuse existing tokens, spacing, and color variables (`bg-background`, `text-muted-foreground`, `border`, `shadow-sm`, etc.).

## Accessibility Baseline

- Focus trap inside dialogs on open (`focus-visible:ring-ring`)
- `aria-live="polite"` on chat message list for screen-reader announcements
- Keyboard: `Enter` to send, `Esc` to close modals, `Tab` cycles focus
- All icon-only buttons need `sr-only` text labels

## Version

Phase 1 — chat + draft-review + create/launch (no persistence/history).

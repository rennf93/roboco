# Prompter — Confirmation Interstitial Specification

## Principle

> **You decide what gets sent to the team.**

The confirmation interstitial is the un-bypassable human gate before any Prompter-generated task enters the RoboCo system. It must be impossible to skip by accident, by keyboard shortcut, or by API manipulation. The user must read, review, and explicitly confirm.

---

## Pattern: Review & Confirm Dialog

### Component

`Dialog` from `panel/src/components/ui/dialog.tsx` — **not** `AlertDialog`. We need the full content flexibility of `Dialog` (close button, scrollable body, custom footer) rather than the simplified action/cancel binary of `AlertDialog`.

**Size**: `DialogContent` with classes `sm:max-w-3xl lg:max-w-4xl max-h-[90vh] overflow-y-auto`.

This is the same large-dialog pattern used by `CreateTaskDialog` in `panel/src/components/tasks/create-task-dialog.tsx`.

---

## Layout

```
┌──────────────────────────────────────────────────────────────┐
│  Review & Confirm Task                              [×]       │
│  You decide what gets sent to the team.                      │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  Title *                                                     │
│  [________________________________________]                  │
│                                                              │
│  Description *                                               │
│  [                                        ]                  │
│  [ Markdown editor with preview toggle    ]                  │
│  [                                        ]                  │
│                                                              │
│  Acceptance Criteria *                                         │
│  ┌────────────────────────────────────────┐                  │
│  │ 1. [Criterion text………]            [×]  │                  │
│  │ 2. [Criterion text………]            [×]  │                  │
│  │ 3. [Criterion text………]            [×]  │                  │
│  │    [+ Add Criterion]                     │                  │
│  └────────────────────────────────────────┘                  │
│                                                              │
│  ┌──────────┬──────────┬──────────┬──────────┐             │
│  │ Team     │ Status   │ Priority │ Complex. │             │
│  │ [Select] │ [Select] │ [Select] │ [Select] │             │
│  └──────────┴──────────┴──────────┴──────────┘             │
│                                                              │
│  ┌─ Advanced Options ──────────────────────┐                │
│  │ Model selector (see model-selector-ux.md)  │                │
│  │ Assign to, Parent task, Project, Product │                │
│  └──────────────────────────────────────────┘                │
│                                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ ⚠️  This will create a real task and notify the team.  │  │
│  │     It cannot be undone from this screen.              │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│                                   [Cancel]  [Confirm & Launch]│
└──────────────────────────────────────────────────────────────┘
```

---

## Sections (Detailed)

### 1. Dialog Header

- **Title**: `DialogTitle` — "Review & Confirm Task"
- **Description**: `DialogDescription` — "You decide what gets sent to the team."

Both are required and always visible. The description is the human-agency anchor copy mandated by the Head of Marketing.

### 2. Title Field

- `Label` + `Input`
- Required (`*` indicator, `text-destructive` color)
- Validation: 5–200 characters (same rule as `CreateTaskDialog`)
- Error state: `border-destructive` + `text-xs text-destructive` message
- Pre-filled by LLM draft; user can edit inline.

### 3. Description Field

- Reuse `MarkdownEditor` from `panel/src/components/tasks/markdown-editor.tsx`
- Required, min 20 characters
- Preview toggle (Edit / Preview tabs using `Tabs` component)
- Pre-filled by LLM draft; user can edit inline.

### 4. Acceptance Criteria

- Reuse `AcceptanceCriteriaEditor` from `panel/src/components/tasks/acceptance-criteria-editor.tsx`
- Required, at least one criterion
- Numbered list with drag handles (if the existing editor supports reordering) or simple add/remove
- Pre-filled by LLM draft; user can add, edit, remove.

### 5. Metadata Grid

A 4-column grid (`grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4`) using `Select` components:

| Field | Component | Options | Default (from draft) |
|-------|-----------|---------|----------------------|
| Team | `Select` | `Object.values(Team)` | LLM suggestion |
| Status | `Select` | `PENDING`, `BACKLOG` | `PENDING` |
| Priority | `Select` | `P0`–`P3` | `P2` (Medium) |
| Complexity | `Select` | `LOW`, `MEDIUM`, `HIGH` | LLM suggestion |

All wrapped in `Label` + `SelectTrigger` + `SelectContent` + `SelectItem`.

### 6. Advanced Options Drawer

- `Collapsible` from `panel/src/components/ui/collapsible.tsx`
- Trigger: `Button` variant `ghost` with `ChevronRight` / `ChevronDown` icons
- Contents:
  - **Model selector** — see [`03-model-selector-ux.md`](03-model-selector-ux.md)
  - **Assign To** — `AgentSelector` (`panel/src/components/agents/agent-selector.tsx`)
  - **Parent Task** — `TaskSelector` (`panel/src/components/tasks/task-selector.tsx`)
  - **Project** — `ProjectSelector` (`panel/src/components/projects/project-selector.tsx`)
  - **Product** — `Select` from `CreateTaskDialog` product list

### 7. Warning Banner

A full-width `Alert` (if available; otherwise a `Card` with `border-destructive` or `bg-destructive/10`):

- Icon: `AlertTriangle` from `lucide-react`
- Text: "This will create a real task and notify the team. It cannot be undone from this screen."
- Purpose: prevents the "I thought this was just a preview" error.

### 8. Footer Actions

- `DialogFooter` with `flex-col-reverse sm:flex-row sm:justify-end gap-2`
- **Cancel** (`Button` variant `outline`): closes dialog, returns to chat draft preview. Does **not** discard the draft.
- **Confirm & Launch** (`Button` primary): submits to the task-creation API.
  - On click: button enters `disabled` state, text changes to "Launching…", spinner (use `Loader2` icon with `animate-spin`)
  - On success: dialog closes, chat shows success card
  - On error: button re-enables, error banner appears above footer

---

## Un-bypassable Guardrails

### UI Guardrails

1. **No keyboard shortcut** launches the task. `Enter` inside any field does **not** submit the form; only the explicit footer button does.
2. **No click-outside dismissal** when dirty. If the user has edited any field, clicking the overlay shows a secondary confirmation: "You have unsaved changes. Discard them?" (`AlertDialog` with "Keep Editing" / "Discard").
3. **Scroll requirement**: The dialog is tall enough that the footer may be below the fold on small screens. The warning banner is positioned **above** the footer so the user must scroll past it to reach the confirm button.

### API Guardrails (Frontend Contract)

- The frontend must **not** call the task-creation endpoint directly from the chat state. The only valid call path is:
  ```
  Chat → Review Modal (user opens) → Confirm Button (user clicks) → POST /tasks
  ```
- There is no `?skip_review=true` query param, no hidden route, and no keyboard bypass.
- Backend should reject any Prompter-originated task creation that does not include a `confirmed_by_human: true` flag in the payload (enforced by the Backend Cell; noted here for cross-cell alignment).

---

## Accessibility

- Focus trap: when dialog opens, focus moves to the Title `Input`.
- `aria-describedby` on the Confirm button pointing to the warning banner text.
- All `Select` triggers have visible `Label` associations (`htmlFor` + `id`).
- Error messages use `aria-live="assertive"` so screen readers announce validation failures immediately.

---

## Copy Reference

| Element | Copy |
|---------|------|
| Dialog title | "Review & Confirm Task" |
| Dialog subtitle | "You decide what gets sent to the team." |
| Warning banner | "This will create a real task and notify the team. It cannot be undone from this screen." |
| Cancel button | "Cancel" |
| Confirm button (idle) | "Confirm & Launch" |
| Confirm button (loading) | "Launching…" |
| Dirty-state discard prompt title | "Discard changes?" |
| Dirty-state discard prompt body | "You have unsaved changes. If you cancel, your edits will be lost." |
| Dirty-state keep button | "Keep Editing" |
| Dirty-state discard button | "Discard" |

---

## Component Inventory

| UI Element | File Path |
|------------|-----------|
| Dialog shell | `panel/src/components/ui/dialog.tsx` |
| Alert (warning banner) | `panel/src/components/ui/alert-dialog.tsx` or custom `Card` |
| Card | `panel/src/components/ui/card.tsx` |
| Input | `panel/src/components/ui/input.tsx` |
| Textarea / MarkdownEditor | `panel/src/components/tasks/markdown-editor.tsx` |
| Select | `panel/src/components/ui/select.tsx` |
| Label | `panel/src/components/ui/label.tsx` |
| Button | `panel/src/components/ui/button.tsx` |
| Collapsible | `panel/src/components/ui/collapsible.tsx` |
| Badge | `panel/src/components/ui/badge.tsx` |
| Tabs | `panel/src/components/ui/tabs.tsx` |
| AcceptanceCriteriaEditor | `panel/src/components/tasks/acceptance-criteria-editor.tsx` |
| AgentSelector | `panel/src/components/agents/agent-selector.tsx` |
| TaskSelector | `panel/src/components/tasks/task-selector.tsx` |
| ProjectSelector | `panel/src/components/projects/project-selector.tsx` |

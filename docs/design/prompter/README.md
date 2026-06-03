# Prompter — Chat Surface Design Specifications

**Last Updated:** 2026-06-03  
**Spec Version:** 1.0  
**Owner:** ux-dev-1  
**Status:** Ready for frontend implementation

---

## Purpose

This directory contains the complete design specification for the **Prompter page chat surface**. A frontend developer can implement the entire chat UI from these files alone, without a follow-up design meeting.

> **Scope boundary:** This spec covers the chat interface and conversation history panel. The task-draft editing panel, Create & Launch confirmation dialog, and spatial layout between chat and draft panels are covered by a companion spec owned by ux-dev-2.

---

## File Index

| File | Covers |
|---|---|
| `README.md` *(this file)* | Overview, layout, shared design tokens |
| `chat-interface.md` | Message bubbles, input area, typing indicator, empty state |
| `conversation-history.md` | History panel, list items, hover actions, delete flow |
| `model-selection.md` | Model selector trigger, dropdown, option items, disabled state |
| `interaction-states.md` | Default / hover / focus / active / disabled states for all elements |

---

## Page Layout — Prompter

```
┌─────────────────────────────────────────────────────────────────────┐
│  App Shell: top nav bar (48px, z-index 100)                         │
├──────────────────────┬──────────────────────────────────────────────┤
│                      │  Chat Header Bar (48px)                      │
│  Conversation        │  ┌──────────────────────────── [Model ▼] ┐  │
│  History Panel       │  │                                        │  │
│  (260px, collapsible)│  │  Message Thread Area                   │  │
│                      │  │  (flex column, scroll, padding 24px)   │  │
│  ─────────────────   │  │                                        │  │
│  [New Chat]          │  │                                        │  │
│                      │  └────────────────────────────────────────┘  │
│  Today               │  Input Composer Area (auto-height, max 180px)│
│  ┌──────────────────┐│  ┌───────────────────────────────[Send ↵]┐  │
│  │ Convo title  ... ││  │ textarea (auto-grow 1–6 rows)         │  │
│  │ Convo title  ... ││  └───────────────────────────────────────┘  │
│  └──────────────────┘│  ⌘↵ to send  (12px, neutral-400)           │
│                      │                                              │
│  Previous 7 Days     │                                              │
│  Older               │                                              │
└──────────────────────┴──────────────────────────────────────────────┘
```

### Layout Dimensions

| Region | Value |
|---|---|
| App shell / top nav | 48 px height, full width |
| Conversation history panel | 260 px width (collapsed: 0 px) |
| Panel collapse transition | 300 ms, `ease-in-out` |
| Chat header bar | 48 px height, flex row, align-center, space-between |
| Message thread area | flex-grow 1, overflow-y auto, padding 24 px 24 px 16 px |
| Input composer area | auto-height, min 52 px, max 180 px, padding 12 px 16 px |

---

## Shared Design Tokens

> These tokens are defined here and referenced by all spec files in this directory. Frontend should reconcile these names with the project's actual design token system at implementation time.

### Colour Tokens

| Token | Light Mode Value | Usage |
|---|---|---|
| `brand-500` | `#5B6CF6` | Primary actions, AI bubble border, active states |
| `brand-600` | `#4A5BE0` | Primary action hover, active history item border |
| `brand-100` | `#EEF0FE` | Active history item background |
| `brand-50`  | `#F5F6FF` | Subtle brand-tinted backgrounds |
| `surface-0` | `#FFFFFF` | Base background (dropdowns, modals) |
| `surface-50` | `#F9FAFB` | History panel background |
| `surface-100` | `#F3F4F6` | AI message bubble background |
| `surface-200` | `#E5E7EB` | Chip/badge background, hover surfaces |
| `surface-300` | `#D1D5DB` | Dividers, borders |
| `neutral-400` | `#9CA3AF` | Placeholder text, timestamps, keyboard hints |
| `neutral-500` | `#6B7280` | Secondary text |
| `neutral-700` | `#374151` | Primary body text |
| `neutral-900` | `#111827` | Headings, bold text |
| `danger-500` | `#EF4444` | Delete actions, error states |
| `danger-100` | `#FEE2E2` | Danger hover background |
| `focus-ring` | `#5B6CF6` | Keyboard focus outline colour (matches `brand-500`) |
| `user-bubble-bg` | `#5B6CF6` | User message bubble background (= `brand-500`) |
| `user-bubble-text` | `#FFFFFF` | User message bubble text |
| `ai-bubble-bg` | `#F3F4F6` | AI message bubble background (= `surface-100`) |
| `ai-bubble-text` | `#374151` | AI message text (= `neutral-700`) |

### Typography

| Role | Font | Weight | Size | Line Height | Tracking |
|---|---|---|---|---|---|
| Message body | System UI stack | 400 | 14 px / 0.875 rem | 1.5 (21 px) | 0 |
| Message sender label | System UI stack | 600 | 12 px / 0.75 rem | 1.4 | 0 |
| History item title | System UI stack | 400 | 14 px / 0.875 rem | 1.4 | 0 |
| History group heading | System UI stack | 600 | 11 px / 0.6875 rem | 1.3 | +0.05 em (uppercase) |
| Timestamp / hint | System UI stack | 400 | 12 px / 0.75 rem | 1.3 | 0 |
| Empty state headline | System UI stack | 600 | 20 px / 1.25 rem | 1.3 | −0.01 em |
| Empty state subtext | System UI stack | 400 | 14 px / 0.875 rem | 1.5 | 0 |
| Input textarea | System UI stack | 400 | 14 px / 0.875 rem | 1.5 | 0 |
| Chip label | System UI stack | 400 | 13 px / 0.8125 rem | 1.4 | 0 |
| Model selector trigger | System UI stack | 500 | 13 px / 0.8125 rem | 1 | 0 |
| Dropdown option | System UI stack | 400 | 14 px / 0.875 rem | 1 | 0 |

> **System UI stack:** `system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif`

### Spacing Scale

| Token | Value |
|---|---|
| `space-1` | 4 px |
| `space-2` | 8 px |
| `space-3` | 12 px |
| `space-4` | 16 px |
| `space-5` | 20 px |
| `space-6` | 24 px |
| `space-8` | 32 px |
| `space-10` | 40 px |

### Border Radius

| Token | Value | Usage |
|---|---|---|
| `radius-sm` | 4 px | User bubble tail corner, chip subtle corner |
| `radius-md` | 8 px | Dropdown panels, input borders |
| `radius-lg` | 12 px | AI bubble |
| `radius-xl` | 18 px | User/AI bubble primary corners |
| `radius-full` | 9999 px | Chips, badges, icon buttons |

### Elevation / Shadow

| Token | Value | Usage |
|---|---|---|
| `elevation-1` | `0 1px 3px rgba(0,0,0,0.08)` | Subtle card |
| `elevation-2` | `0 4px 12px rgba(0,0,0,0.10)` | Input composer |
| `elevation-3` | `0 8px 24px rgba(0,0,0,0.12)` | Dropdowns, model selector |

### Z-Index Scale

| Layer | Value |
|---|---|
| Message thread | 1 |
| Input composer | 10 |
| Typing indicator | 15 |
| History panel | 50 |
| App nav bar | 100 |
| Model selector dropdown | 200 |
| Modals / dialogs | 300 |

### Motion / Easing

| Token | Value | Usage |
|---|---|---|
| `ease-default` | `cubic-bezier(0.4, 0, 0.2, 1)` | General UI transitions |
| `ease-in` | `cubic-bezier(0.4, 0, 1, 1)` | Elements exiting the screen |
| `ease-out` | `cubic-bezier(0, 0, 0.2, 1)` | Elements entering the screen |
| `duration-fast` | `150 ms` | Hover colour changes, icon reveals |
| `duration-base` | `200 ms` | Height/opacity transitions (delete confirm) |
| `duration-slow` | `300 ms` | Panel slide (history collapse), dropdown open |
| `duration-typing` | `1200 ms` | Typing indicator full animation cycle |
| `typing-stagger` | `160 ms` | Per-dot delay in typing indicator |

---

## Accessibility Requirements

- All interactive elements are keyboard-operable.
- Focus order follows visual reading order (left-to-right, top-to-bottom).
- Focus ring: `2 px solid var(--focus-ring)` with `outline-offset: 2 px` on all focusable elements.
- Colour contrast: minimum 4.5:1 for normal text, 3:1 for large text (≥ 18 px regular or ≥ 14 px bold) and UI components.
- Typing indicator and suggested-prompt chips must not cause layout shift.
- All icon-only buttons must have `aria-label` and a visible tooltip on hover.
- Conversation history panel collapse must be announced via `aria-expanded` on the toggle button.
- `role="log"` and `aria-live="polite"` on the message thread container for screen-reader announcement of new messages.

---

## Related Specs

- `ux-dev-2` task — Task-draft panel, Create & Launch dialog, spatial layout: `docs/design/prompter/task-draft.md` *(pending)*
- Frontend implementation task: `fb83fada-1bf7-4e5a-b62b-1f18b1131735`
- Parent design task: `a2230a75-9730-4353-a45c-7a36c500d301`

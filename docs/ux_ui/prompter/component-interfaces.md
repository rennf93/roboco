# Prompter Component Interface Specification

**Version:** 1.0  
**Date:** 2026-06-06  
**Audience:** ux-dev-2 (implementation), fe-dev-* (page wiring)

---

## Overview

This document defines the TypeScript prop interfaces for the core Prompter components. These interfaces are contracts — they describe the data shapes, callbacks, and state machines that the frontend implementation must satisfy. They intentionally reuse types and patterns from the existing panel codebase (`panel/src/types/index.ts`, `panel/src/components/ui/*`, `panel/src/components/tasks/*`).

**Implementation location:** `panel/src/components/prompter/`

---

## Shared Types

```typescript
// panel/src/components/prompter/types.ts

import { Team, Complexity, TaskNature, TaskType, TaskStatus } from "@/types";

/**
 * Origin of a field's current value.
 */
export type FieldOrigin = "ai" | "human" | "edited";

/**
 * A single field in the draft-review panel with provenance tracking.
 */
export interface DraftField<T = string> {
  value: T;
  origin: FieldOrigin;
  originalValue: T; // The LLM's initial suggestion (for revert)
  error?: string;
}

/**
 * The complete structured task draft produced by the LLM.
 * Mirrors TaskCreate from @/types but wraps every field in DraftField
 * for provenance tracking.
 */
export interface TaskDraft {
  title: DraftField;
  description: DraftField;
  team: DraftField<Team>;
  priority: DraftField<number>;
  complexity: DraftField<Complexity>;
  nature: DraftField<TaskNature>;
  status: DraftField<TaskStatus>;
  task_type: DraftField<TaskType>;
  acceptance_criteria: DraftField<string[]>;
  // Contextual fields — always origin="human" unless LLM explicitly infers
  project_id?: DraftField<string | null>;
  product_id?: DraftField<string | null>;
  parent_task_id?: DraftField<string | null>;
  assigned_to?: DraftField<string | null>;
  dependency_ids?: DraftField<string[]>;
}

/**
 * A chat message in the prompter conversation.
 * Extends the mentor-chat pattern with draft-specific metadata.
 */
export interface PrompterMessage {
  id: string;
  role: "user" | "ai";
  content: string;
  timestamp: string;
  draftReady?: boolean; // If true, shows "Draft Ready" CTA in this message
}

/**
 * A persisted prompt history item.
 */
export interface PromptHistoryItem {
  id: string;
  firstMessage: string; // Truncated display text
  taskTitle?: string;   // If draft was launched
  taskId?: string;
  createdAt: string;
  updatedAt: string;
  isStarred: boolean;
  messages: PrompterMessage[]; // Full conversation for restore
  draft?: TaskDraft;            // Final draft if one was produced
}

/**
 * Prompter operational states.
 */
export type PrompterState =
  | "empty"
  | "chatting"
  | "drafting"
  | "reviewing"
  | "confirming"
  | "launched";

/**
 * Loading step for the drafting state.
 */
export type DraftingStep =
  | "understanding"
  | "defining_criteria"
  | "estimating"
  | "finalizing";
```

---

## Component: `PrompterPage`

**File:** `panel/src/app/(dashboard)/prompter/page.tsx`

The top-level page component. Manages global prompter state and orchestrates sub-components.

```typescript
interface PrompterPageProps {
  // No props — data fetched via hooks
}

// State machine managed internally:
//   state: PrompterState
//   messages: PrompterMessage[]
//   draft: TaskDraft | null
//   history: PromptHistoryItem[]
//   isHistoryOpen: boolean
//   draftingProgress: DraftingStep[]
```

**Implementation notes:**
- Uses `useState` for local state.
- Uses `usePrompter` hook (to be built by frontend) for API calls.
- Persists unfinished conversation to `localStorage` with key `roboco_prompter_draft`.
- On mount, checks `localStorage` and shows `AlertDialog`: "Resume previous conversation?" if draft exists.

---

## Component: `PrompterChat`

**File:** `panel/src/components/prompter/prompter-chat.tsx`

The chat interface. Heavily based on `panel/src/components/knowledge-base/mentor-chat.tsx` but adapted for task drafting.

```typescript
interface PrompterChatProps {
  messages: PrompterMessage[];
  isLoading: boolean;          // True while waiting for AI response
  isDrafting: boolean;         // True while LLM generates structured task
  onSendMessage: (content: string) => void;
  onDraftReady: () => void;    // User clicks "Draft Ready" CTA
  onNewChat: () => void;
  onToggleHistory: () => void;
  placeholder?: string;
  quickStartSuggestions?: string[];
}
```

**Behavior:**
- Renders empty state when `messages.length === 0`.
- Renders message list when messages exist.
- Shows `Loader2` skeletons when `isLoading`.
- Disables input when `isLoading || isDrafting`.
- Auto-scrolls to bottom on new messages (reuses `useRef` + `ScrollArea` pattern from `mentor-chat.tsx`).

---

## Component: `DraftReviewPanel`

**File:** `panel/src/components/prompter/draft-review-panel.tsx`

The structured task review interface. Reuses patterns from `panel/src/components/tasks/create-task-dialog.tsx`.

```typescript
interface DraftReviewPanelProps {
  draft: TaskDraft;
  chatContext: PrompterMessage[]; // Last N messages for context summary
  onChangeField: <K extends keyof TaskDraft>(
    field: K,
    value: TaskDraft[K]["value"]
  ) => void;
  onRevertField: <K extends keyof TaskDraft>(field: K) => void;
  onBackToChat: () => void;
  onLooksGood: () => void;
  onRegenerateDraft: () => void;
  confidence?: number; // 0–100, shown as "AI Confidence: X%"
  validationErrors?: Record<keyof TaskDraft, string>;
}
```

**Behavior:**
- Renders all fields from `TaskDraft` with origin badges.
- Inline editing: no "edit mode" — inputs are always editable.
- Tracks `onChange` to toggle `[AI]` → `[Edited]` badges.
- "Regenerate Draft" triggers `AlertDialog` warning about overwrite.
- "Looks Good" runs validation; if errors, auto-scrolls to first invalid field and shows global error banner.

---

## Component: `DraftFieldInput`

**File:** `panel/src/components/prompter/draft-field-input.tsx`

A wrapper around form inputs that adds the `[AI]` / `[Edited]` badge, error display, and revert button.

```typescript
interface DraftFieldInputProps<T = string> {
  label: string;
  field: DraftField<T>;
  onChange: (value: T) => void;
  onRevert: () => void;
  children: React.ReactNode; // The actual input/textarea/select component
  hint?: string;             // sr-only hint for screen readers
}
```

**Behavior:**
- Renders `label` + origin badge inline.
- Renders `children` (the actual input).
- Renders error message if `field.error` present.
- Shows revert button (ghost icon, `RotateCcw`) if `field.origin === "edited"`.
- Provides `hint` as `sr-only` text for screen readers.

---

## Component: `ConfirmationGate`

**File:** `panel/src/components/prompter/confirmation-gate.tsx`

The explicit human-confirmation modal. Two variants supported.

```typescript
interface ConfirmationGateProps {
  draft: TaskDraft;
  isOpen: boolean;
  variant?: "modal" | "inline"; // Default: "modal"
  onConfirm: () => void;
  onCancel: () => void;
}
```

**Behavior (modal variant):**
- Full `AlertDialog` with focus trap.
- Read-only summary of key draft fields.
- Three checkboxes; primary button disabled until all checked.
- Focus moves to first unchecked checkbox on open.

**Behavior (inline variant):**
- Sticky banner at top of `DraftReviewPanel`.
- Same checklist + confirm/cancel buttons.
- No focus trap; banner is part of normal document flow.

---

## Component: `PromptHistorySidebar`

**File:** `panel/src/components/prompter/prompt-history-sidebar.tsx`

The right-hand history panel. On mobile, wrapped in `Sheet`.

```typescript
interface PromptHistorySidebarProps {
  items: PromptHistoryItem[];
  isOpen: boolean;
  onClose: () => void;
  onSelectItem: (item: PromptHistoryItem) => void;
  onToggleStar: (itemId: string) => void;
  onNewChat: () => void;
  searchQuery?: string;
  onSearchChange?: (query: string) => void;
}
```

**Behavior:**
- Groups items by relative date: Today / Yesterday / Earlier.
- Renders starred items in a pinned "Starred Templates" section.
- Each row shows truncated first message, task title (if launched), relative timestamp.
- `Star` icon button toggles `isStarred`.
- On mobile, renders inside `Sheet` with `side="right"`.

---

## Component: `DraftingOverlay`

**File:** `panel/src/components/prompter/drafting-overlay.tsx`

The loading state while the LLM generates the structured task.

```typescript
interface DraftingOverlayProps {
  steps: DraftingStep[];         // Completed steps
  currentStep: DraftingStep;    // Step in progress
  onCancel: () => void;
}
```

**Behavior:**
- Dimmed overlay on chat view (`opacity-60 pointer-events-none` on chat).
- Centered card with animated icon and step checklist.
- Cancel button aborts the request.
- `aria-live="polite"` announces step progression.

---

## Component: `PrompterMessageBubble`

**File:** `panel/src/components/prompter/prompter-message-bubble.tsx`

A single chat message. Different styling for user vs AI.

```typescript
interface PrompterMessageBubbleProps {
  message: PrompterMessage;
  onDraftReady?: () => void; // Only used when message.draftReady === true
}
```

**Behavior:**
- User messages: right-aligned bubble, `bg-primary text-primary-foreground`.
- AI messages: left-aligned `Card`, `prose` markdown rendering.
- If `draftReady`, renders a prominent "Draft Ready — Review Now" button inside the AI message card.

---

## Hook: `usePrompter`

**File:** `panel/src/hooks/use-prompter.ts`

Proposed hook for frontend devs to connect the UI to the backend API.

```typescript
interface UsePrompterReturn {
  messages: PrompterMessage[];
  draft: TaskDraft | null;
  state: PrompterState;
  isLoading: boolean;
  isDrafting: boolean;
  sendMessage: (content: string) => Promise<void>;
  generateDraft: () => Promise<void>;
  createTask: (draft: TaskDraft) => Promise<{ taskId: string }>;
  reset: () => void;
}

// Usage:
// const prompter = usePrompter();
```

**Note:** This hook is a *suggestion* for the frontend implementation cell. The exact API shape will depend on the backend endpoint contract defined by be-dev-*.

---

## File Structure (Proposed)

```
panel/src/components/prompter/
├── index.ts                    # Barrel export
├── types.ts                    # Shared types above
├── prompter-chat.tsx           # PrompterChat component
├── draft-review-panel.tsx      # DraftReviewPanel component
├── draft-field-input.tsx       # DraftFieldInput wrapper
├── confirmation-gate.tsx       # ConfirmationGate component
├── prompt-history-sidebar.tsx  # PromptHistorySidebar component
├── drafting-overlay.tsx        # DraftingOverlay component
├── prompter-message-bubble.tsx # PrompterMessageBubble component
└── __tests__/
    └── prompter-chat.test.tsx  # Component tests (for fe-qa)
```

---

## Integration Points

### With Existing Components

| Prompter Component | Reuses From | Integration Notes |
|--------------------|-------------|-------------------|
| `PrompterChat` | `mentor-chat.tsx` | Copy layout patterns (ScrollArea, input positioning, auto-scroll). Replace mentor-specific metadata (sources, followups) with draft-ready CTA. |
| `DraftReviewPanel` | `create-task-dialog.tsx` | Use same field components (`Input`, `Select`, `MarkdownEditor`, `AcceptanceCriteriaEditor`, `DependencySelector`, `AgentSelector`, `ProjectSelector`). Wrap each in `DraftFieldInput`. |
| `ConfirmationGate` | `alert-dialog.tsx` | Use `AlertDialog`, `AlertDialogContent`, `AlertDialogHeader`, `AlertDialogTitle`, `AlertDialogDescription`, `AlertDialogAction`, `AlertDialogCancel`. |
| `PromptHistorySidebar` | `sheet.tsx` | Mobile wrapper uses `Sheet`, `SheetContent`, `SheetHeader`. Desktop is a plain `div.w-80`. |

### With Backend API (Proposed)

| Endpoint | Method | Body | Response |
|----------|--------|------|----------|
| `/api/prompter/chat` | POST | `{ messages: PrompterMessage[], conversation_id?: string }` | `{ message: PrompterMessage, conversation_id: string }` |
| `/api/prompter/draft` | POST | `{ conversation_id: string }` | `{ draft: TaskDraft, confidence?: number }` |
| `/api/prompter/history` | GET | — | `PromptHistoryItem[]` |
| `/api/prompter/history/:id` | PATCH | `{ isStarred: boolean }` | `{ success: boolean }` |

**Note:** These endpoints are speculative and must be ratified with the backend cell (be-dev-*). They are provided here so ux-dev-2 and fe-dev-* can align on data contracts.

---

## Acceptance Criteria for Implementation

- [ ] All components render without TypeScript errors against the interfaces above.
- [ ] `PrompterChat` handles empty state, message list, loading, and quick-start suggestions.
- [ ] `DraftReviewPanel` tracks origin state (`ai` → `edited`) for every field and supports revert.
- [ ] `ConfirmationGate` (modal variant) blocks interaction, requires all three checkboxes, and returns focus correctly.
- [ ] `PromptHistorySidebar` groups items by date, supports starring, and works in both desktop sidebar and mobile `Sheet` modes.
- [ ] `DraftingOverlay` announces progress to screen readers and supports cancellation.
- [ ] All components pass WCAG 2.1 AA requirements as defined in [`accessibility.md`](accessibility.md).

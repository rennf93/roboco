# Prompter — Interaction Specification

## Overview

The Prompter is a first-class Panel page that lets users author tasks via conversational LLM assistance rather than hand-writing specs. The journey is intentionally linear and un-bypassable:

```
Chat → Draft → Review → Confirm → Launch → Success
```

Each transition is explicit. There is no hidden auto-launch; the human is always the final gate before a task enters the system.

---

## Screen States

### 1. Chat Screen (Default)

**Purpose**: The user describes what they need in natural language. The LLM responds with clarifying questions and, when enough context is gathered, offers to generate a draft.

**Layout** (mapped to existing components):
- **Container**: Full page inside the dashboard layout (`(dashboard)/layout.tsx`)
- **Header row**:
  - Page title (H1): see [`04-naming-and-navigation.md`](04-naming-and-navigation.md)
  - Subtitle: "Describe what you need. The assistant will ask questions and draft a task for your team."
- **Chat area** (`Card` + custom flex column):
  - **Message list** (`ScrollArea`): user messages right-aligned, assistant messages left-aligned.
  - **Message bubbles**: `Card` with `py-3 px-4` and subtle background differentiation:
    - User: `bg-primary/10`
    - Assistant: `bg-muted`
  - **Typing indicator**: `Skeleton` pulse (3 lines) when assistant is generating.
- **Composer bar** (fixed to bottom of chat area):
  - `Textarea` (auto-resize, max 4 lines) with placeholder: "Describe the task you want to create..."
  - `Button` (primary, icon `Send`) labeled "Send"
  - Keyboard: `Enter` sends; `Shift+Enter` adds newline.
- **Empty state** (first visit):
  - Centered `Card` with illustration placeholder + suggested prompts:
    - "Add a dark-mode toggle to the panel"
    - "Write a design spec for a confirmation flow"
    - "Create a backend task to add OAuth2 login"

**Component references**:
- `panel/src/components/ui/card.tsx` — message bubbles
- `panel/src/components/ui/textarea.tsx` — composer input
- `panel/src/components/ui/button.tsx` — send button
- `panel/src/components/ui/scroll-area.tsx` — scrollable message list
- `panel/src/components/ui/skeleton.tsx` — typing indicator

**Accessibility**:
- `aria-live="polite"` on the message list so screen readers announce new assistant messages.
- Composer `Textarea` has `aria-label="Task description"`.

**Error states**:
- LLM error: assistant message styled as `Alert` variant `destructive` with text: "Something went wrong. Try rephrasing or try again later."
- Network error: toast via `sonner` (already in panel globals).

---

### 2. Draft Preview (Inline Transition)

**Purpose**: Once the LLM has enough context, it generates a structured task draft. The draft is presented inline in the chat as a special "proposal" message, not a separate page. This preserves conversational context.

**Trigger**: Assistant message ends with: *"I can draft a task based on what we discussed. Would you like to review it?"* + two quick actions.

**Layout**:
- **Proposal card** (full-width, `Card` with `border-primary`):
  - Header: `CardHeader` with `CardTitle` "Draft Task" and `Badge` showing suggested team.
  - Body (`CardContent`):
    - **Title** (bold, `text-lg`)
    - **Description** (truncated to 4 lines with fade-out; `Button` "Expand" to show full text in a `Dialog`)
    - **Acceptance criteria** (`ScrollArea`, max height 160px):
      - Numbered list using `Badge` variant `outline` for each item number.
    - **Metadata row** (flex, gap-4, `text-sm text-muted-foreground`):
      - Team: `Badge`
      - Complexity: `Badge` variant `secondary`
      - Nature: `Badge` variant `secondary`
  - Footer (`CardFooter`, justify-between):
    - `Button` variant `outline`: "Keep Chatting" (returns to free chat)
    - `Button` (primary): "Review & Confirm" (advances to confirmation interstitial)

**Component references**:
- `panel/src/components/ui/card.tsx` — proposal card
- `panel/src/components/ui/badge.tsx` — team, complexity, nature labels
- `panel/src/components/ui/dialog.tsx` — expand description
- `panel/src/components/ui/scroll-area.tsx` — criteria list

**Accessibility**:
- Proposal card is focusable (`tabIndex={0}`) and announces via `aria-live`.
- "Review & Confirm" button has `aria-describedby` pointing to a hidden span summarizing the draft title.

---

### 3. Confirmation Interstitial (Modal)

**Purpose**: The un-bypassable human gate. The user sees the full draft, can edit fields inline, and must explicitly confirm before the task is created.

**Behavior**: Opens as a **full-size Dialog** (`DialogContent` with `sm:max-w-3xl lg:max-w-4xl max-h-[90vh]`) so the user cannot miss it. This is not a sidebar or inline form; it interrupts the flow by design.

**Detailed spec**: see [`02-confirmation-interstitial.md`](02-confirmation-interstitial.md).

---

### 4. Launch / Success

**Purpose**: Provide clear feedback that the task has entered the system and give the user a next step.

**Layout** (inline in chat, replacing the proposal card):
- **Success card** (`Card` with `border-green-600` or `border-success` token if available):
  - Header: `CardHeader` with `CardTitle` "Task launched" + `Badge` "Pending"
  - Body: one-sentence summary: "Your task '*{title}*' has been created and routed to the **{team}** cell."
  - Footer (`CardFooter`, gap-2):
    - `Button` variant `ghost` + `Link` to `/tasks/{taskId}`: "View Task →"
    - `Button` variant `outline`: "Start Another" (resets chat to empty state)

**Component references**:
- `panel/src/components/ui/card.tsx`
- `panel/src/components/ui/badge.tsx`
- `panel/src/components/ui/button.tsx`

---

## State Machine

```text
[Empty] --user types--> [Chatting]

[Chatting] --assistant offers draft--> [DraftPreview]
[Chatting] --user keeps typing--> [Chatting]
[Chatting] --LLM error--> [Chatting] (error bubble appended)

[DraftPreview] --"Keep Chatting"--> [Chatting]
[DraftPreview] --"Review & Confirm"--> [ReviewModal]

[ReviewModal] --"Cancel"--> [DraftPreview] (modal closes, chat scrolls to proposal)
[ReviewModal] --user edits fields--> [ReviewModal] (dirty state)
[ReviewModal] --"Confirm & Launch"--> [Launching] (button loading)

[Launching] --API success--> [Success]
[Launching] --API error--> [ReviewModal] (error banner + button enabled)

[Success] --"Start Another"--> [Empty]
[Success] --"View Task"--> (navigate away)
```

---

## Loading Patterns

| State | Visual |
|-------|--------|
| Assistant thinking | `Skeleton` 3-line pulse inside assistant bubble |
| Draft generating | `Skeleton` card (title + 4 lines + criteria placeholder) |
| Launching | Primary button shows spinner + "Launching..." text |
| Navigating to task | Page transition handled by Next.js; no extra UX needed |

---

## Error Patterns

| Scenario | UI | Copy |
|----------|-----|------|
| LLM stream fails mid-chat | Toast + inline retry button | " assistant had a hiccup. [Retry]" |
| Draft generation fails | Inline alert inside chat | "Couldn’t draft a task right now. Keep chatting or try again." |
| Confirm & Launch API fails | Banner inside review modal | "We couldn’t create the task. Check your connection and try again." |
| Validation error (e.g. title too short) | Field-level error text | Same rules as `CreateTaskDialog` |

---

## Responsive Behavior

- **Desktop (>= 1024px)**: Chat and composer are centered in a max-width 880px column inside the dashboard layout.
- **Tablet (768–1023px)**: Same, max-width 720px.
- **Mobile (< 768px)**: Composer bar becomes sticky at bottom of viewport; chat scrolls above it. Review modal becomes bottom sheet (`Sheet` component) instead of center Dialog if needed, but since this is a dashboard application primarily used on desktop, Dialog is acceptable for MVP.

---

## Out of Scope (Phase 2)

- Conversation history / "My Prompts" list
- Persistent drafts across sessions
- Advanced model comparison side-by-side
- Rich media uploads in chat

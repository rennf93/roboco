# Prompter State Machine & Wireframes

**Version:** 1.0  
**Date:** 2026-06-06

---

## State Diagram

```
                    ┌─────────────┐
     ┌──────────────│   EMPTY     │
     │              └──────┬──────┘
     │                     │ user sends first message
     │                     ▼
     │              ┌─────────────┐
     │              │  CHATTING   │◄──────────────────────┐
     │              └──────┬──────┘                       │
     │                     │ user asks for draft /        │
     │                     │ LLM offers draft             │
     │                     ▼                              │
     │              ┌─────────────┐    user wants changes │
     │              │  DRAFTING   │───────────────────────┘
     │              │  (loading)  │
     │              └──────┬──────┘
     │                     │ LLM returns structured task
     │                     ▼
     │              ┌─────────────┐
     │              │  REVIEWING  │◄──────────────────────┐
     │              └──────┬──────┘    user clicks "Back    │
     │                     │ to Chat" (preserves context)   │
     │  user confirms      │                              │
     │  "Looks Good"       │ user edits fields             │
     │                     ▼                              │
     │              ┌─────────────┐    (editing is inline;  │
     └─────────────►│ CONFIRMING  │    state stays REVIEWING)│
                    └──────┬──────┘
                           │ user confirms "Create Task"
                           ▼
                    ┌─────────────┐
                    │   LAUNCHED  │───> redirect to task detail
                    └─────────────┘


    CHATTING ←──────┐
                    │ user opens history sidebar
                    ▼
           ┌────────────────┐
           │ HISTORY_VISIBLE│
           └───────┬────────┘
                   │ user selects template
                   └──────────────> CHATTING (template injected)
```

---

## State Definitions

### 1. EMPTY — Onboarding / Empty State

**Trigger:** User navigates to `/prompter` with no active conversation.

**Layout:**
- Full-width centered content inside the dashboard `main` area (`flex-1 overflow-auto bg-muted/30 p-6`).
- No sidebar chat history visible (history panel is closed by default).

**Visual Elements:**
1. **Hero icon:** `Sparkles` (or `Wand2`) from `lucide-react` at `h-16 w-16 text-primary/50` with a smaller `Zap` icon at `h-6 w-6 text-yellow-500 absolute -top-1 -right-1`.
2. **Headline:** "Prompter" — `text-xl font-semibold mb-2`
3. **Subheadline:** "Describe what you need in plain language. RoboCo's AI will draft a structured task for you. You'll review and confirm every detail before it enters the pipeline." — `text-muted-foreground mb-4 max-w-md`
4. **Capability badges:** Three `Badge variant="secondary"` items:
   - "Chat-to-task" (icon: `MessageCircle`)
   - "Editable drafts" (icon: `Pencil`)
   - "Human confirm" (icon: `ShieldCheck`)
5. **Quick-start suggestions:** A row of clickable `Button variant="outline" size="sm"` chips with common starters:
   - "Build a login API endpoint"
   - "Design a confirmation modal"
   - "Write QA guidelines for auth"
   - "Plan a migration from v1 to v2"
6. **Input area:** Fixed at bottom, same styling as `mentor-chat.tsx` input:
   - `Textarea` with `min-h-20 pr-12 resize-none`
   - Send `Button size="icon"` at `absolute bottom-2 right-2`
   - Placeholder: "Describe the task you want to create..."

**Interactions:**
- Clicking a quick-start suggestion populates the textarea and auto-sends.
- `Enter` (without Shift) submits the message.
- `Shift+Enter` inserts a newline.
- On submit, transition to **CHATTING**.

**Error Handling:**
- Empty submission is prevented (button disabled when `!input.trim()`).
- Rate-limit or backend error shows a `toast.error()` (reuses `sonner` pattern from panel).

---

### 2. CHATTING — Multi-Turn Conversation

**Trigger:** User has sent at least one message.

**Layout:**
- **Left column (main, ~75% width on desktop, 100% on mobile):** Chat messages + input
- **Right column (collapsible sidebar, ~25% width):** Prompt history (hidden by default, toggleable)

**Visual Elements:**
1. **Header bar:**
   - Left: `Sparkles` icon + "Prompter" label + optional `Badge variant="outline"` "Conversation active"
   - Right: `Button variant="ghost" size="sm"` with `RotateCcw` icon → "New Chat" (resets state to EMPTY)
2. **Message list:** `ScrollArea` with `flex-1 pr-4`, auto-scrolls to bottom on new messages.
   - **User messages:** Right-aligned bubble. `bg-primary text-primary-foreground rounded-lg px-4 py-2 max-w-[80%]`
   - **AI messages:** Left-aligned card. `Card > CardContent` with `prose prose-sm dark:prose-invert`. Contains:
     - Markdown-rendered content (reuses `Markdown` component)
     - Optional follow-up suggestion chips (`Button variant="outline" size="sm"`)
     - Optional "Draft Ready" call-to-action button when the LLM has generated enough context to propose a structured task
3. **Input area:** Same as EMPTY state but placeholder changes to "Continue the conversation or ask for a draft..."

**State Transitions:**
- User clicks "Draft Ready" CTA in an AI message → **DRAFTING**
- User explicitly types "generate draft" or similar intent → **DRAFTING**
- User clicks "New Chat" → **EMPTY** (with confirmation `AlertDialog` if messages exist)
- User toggles history sidebar → **HISTORY_VISIBLE**

**Loading States:**
- While waiting for AI response: Skeleton pulse at bottom of message list (three `Skeleton` lines, `h-4` at varying widths).
- Input textarea disabled with `opacity-50`.

**Error Handling:**
- LLM timeout/error: Inline error message in a `Card` with `variant="destructive"` border, containing:
  - Error description
  - "Retry" button
  - "Copy prompt for manual creation" fallback

---

### 3. DRAFTING — LLM Structured-Task Generation (Loading)

**Trigger:** User requests a draft; backend calls LLM to convert conversation into structured `TaskCreate` payload.

**Layout:**
- Chat view dims slightly (`opacity-60 pointer-events-none` on chat history).
- Centered loading overlay card.

**Visual Elements:**
1. **Loading card:** `Card` centered in viewport with:
   - Animated `Sparkles` icon with `animate-pulse`
   - Headline: "Drafting your task..."
   - Subheadline: "The AI is structuring your conversation into a formal task specification."
   - Inline progress steps (not a progress bar — a checklist that fills in):
     - [x] Understanding intent
     - [ ] Defining acceptance criteria
     - [ ] Estimating complexity
     - [ ] Finalizing structure
   - Cancel `Button variant="outline"` → aborts request, returns to **CHATTING**

**State Transitions:**
- LLM returns structured task JSON → **REVIEWING**
- User clicks Cancel → **CHATTING**
- Network/LLM error → Inline error card with retry/cancel options

**Accessibility:**
- Loading card receives `aria-live="polite"` so screen readers announce "Drafting your task, please wait."
- Cancel button is the only focusable element in the overlay (focus trap).

---

### 4. REVIEWING — Draft-Review Panel

**Trigger:** LLM has returned a structured task draft.

**Layout:**
- **Top section (~40% height):** Collapsible chat context summary (last 3 messages shown by default, expandable)
- **Bottom section (~60% height):** Draft-review panel — a structured form derived from `create-task-dialog.tsx` but with AI-suggestion affordances

**Visual Elements — Chat Context Summary:**
- `Collapsible` section labeled "Conversation Context"
- Shows last 3 user/AI message pairs as compact text snippets
- Expand to see full conversation
- Purpose: reminds the user *why* the draft looks the way it does

**Visual Elements — Draft-Review Panel:**
The panel is a `Card` with `p-6` containing an editable form. See [`confirmation-flow.md`](confirmation-flow.md) for exhaustive field-level specs.

High-level structure:
1. **Panel header:**
   - "Review Draft" — `text-lg font-semibold`
   - `Badge` showing draft confidence score (if backend provides it): "AI Confidence: 85%"
   - Action buttons: "Back to Chat" (secondary), "Looks Good" (primary)
2. **Field groups:** Each field is wrapped in a `div.space-y-2` with:
   - Label + AI-suggestion indicator (see confirmation-flow.md)
   - Editable input (reuses `Input`, `Textarea`, `Select`, `MarkdownEditor`, `AcceptanceCriteriaEditor`)
   - Inline validation message (same pattern as `create-task-dialog.tsx`)
3. **Bottom action bar:** Sticky footer inside the card with:
   - "Back to Chat" — returns to **CHATTING**, preserves all edits as draft state
   - "Looks Good" — transitions to **CONFIRMING**
   - "Regenerate Draft" — sends conversation back to LLM with "user requested regeneration" context

**State Transitions:**
- "Looks Good" → **CONFIRMING**
- "Back to Chat" → **CHATTING** (preserves draft edits in memory)
- "Regenerate Draft" → **DRAFTING**
- Edits happen inline; state stays **REVIEWING**

**Error Handling:**
- Validation errors (e.g., title < 5 chars) show `border-destructive` on the field + `text-xs text-destructive` message.
- "Looks Good" is disabled until validation passes.

---

### 5. CONFIRMING — Explicit Human Confirmation Gate

**Trigger:** User clicks "Looks Good" in REVIEWING state.

**This is the unmistakable confirmation moment mandated by Head of Marketing.**

**Layout:**
- Full-screen `AlertDialog` (not a sidebar, not a toast, not a banner).
- The dialog blocks all interaction until the user makes a deliberate choice.

**Visual Elements:**
1. **Dialog header:**
   - Icon: `ShieldCheck` at `h-8 w-8 text-primary` (reinforces trust/security)
   - Title: "Ready to create this task?"
   - Description: "You're about to launch a new task into the dev pipeline. This action creates the task, assigns it to a team, and opens it for agent claim."
2. **Task summary (non-editable, read-only):**
   - A compact `Card` inside the dialog showing:
     - **Title** (bold)
     - **Team** + **Priority** + **Complexity** as inline `Badge` items
     - **Description** truncated to 2 lines with `...` and a "Show full" expand link
     - **Acceptance Criteria** as a numbered list (max 3 shown, "+2 more" if exceeded)
   - Purpose: the user must *read* the summary before they can confirm. It is not a wall of text, but enough to catch obvious errors.
3. **Confirmation checklist (the deliberate action):**
   - A `Checkbox` list that the user must manually tick:
     - [ ] "I have reviewed the task title and description"
     - [ ] "I have checked the acceptance criteria"
     - [ ] "I understand this task will enter the dev pipeline"
   - The primary "Create Task" button is **disabled** until all three checkboxes are checked.
   - This is the "unmistakable" mechanism — it requires active cognitive engagement, not a reflexive click.
4. **Action buttons:**
   - "Create Task" — `AlertDialogAction` (primary, disabled until checkboxes complete)
   - "Go Back & Edit" — `AlertDialogCancel` (returns to **REVIEWING**)

**State Transitions:**
- All checkboxes checked + "Create Task" clicked → **LAUNCHED**
- "Go Back & Edit" → **REVIEWING**

**Accessibility:**
- Dialog receives `aria-modal="true"` and focus trap.
- On open, focus moves to the first unchecked checkbox (not the primary button — prevents accidental activation).
- Screen reader announces: "Confirmation dialog. Please review the task summary and confirm the checklist before creating the task."

---

### 6. LAUNCHED — Task Created

**Trigger:** User completes confirmation gate and backend creates the task.

**Layout:**
- Brief success state, then automatic redirect.

**Visual Elements:**
1. **Success toast:** `toast.success("Task created successfully")` with a link to the new task.
2. **Redirect:** After 1.5s, `router.push(/tasks/${newTaskId})` to the task detail page.
3. **Fallback:** If redirect fails, show a full-page success card with "View Task" and "Start New Prompt" buttons.

**State Transitions:**
- Automatic redirect to task detail → user leaves Prompter flow

---

### 7. HISTORY_VISIBLE — Prompt History Sidebar

**Trigger:** User opens the history sidebar while in CHATTING or REVIEWING state.

**Layout:**
- Right-hand sidebar, `w-80 border-l bg-background`, collapsible.
- On mobile: `Sheet` side drawer from the right.

**Visual Elements:**
1. **Sidebar header:**
   - "Prompt History" — `font-medium`
   - "New" `Button size="sm" variant="ghost"` to start a fresh conversation (resets to EMPTY)
2. **Search/filter:** `Input` with `Search` icon placeholder "Search past prompts..."
3. **History list:** `ScrollArea` with grouped items:
   - **Today** / **Yesterday** / **Earlier** group headers (`text-xs font-semibold text-muted-foreground uppercase`)
   - Each item is a `Button variant="ghost"` row showing:
     - First user message truncated to 40 chars
     - Resulting task title (if launched) or "Draft abandoned"
     - Timestamp (relative: "2h ago")
     - `Star` icon button to favorite a template
4. **Favorites section:** Collapsible "Starred Templates" at top with starred items pinned.

**Interactions:**
- Clicking a history item injects its first user message into the current chat as a new user message and appends the system context "(Based on previous prompt: ...)".
- Clicking the `Star` icon toggles favorite status.
- "New" button resets the entire prompter state to EMPTY.

**State Transitions:**
- Closing sidebar → returns to previous state (CHATTING or REVIEWING)
- Selecting template → **CHATTING** (with template injected)

---

## Responsive Behavior

### Desktop (≥1024px)
- Two-column layout: Chat (75%) + History sidebar (25%, collapsible)
- Draft-review panel is a two-column form: primary fields on left, advanced fields on right

### Tablet (768px–1023px)
- Single-column layout
- History sidebar becomes a `Sheet` drawer
- Draft-review panel stacks vertically

### Mobile (<768px)
- Single-column, full-width
- History sidebar is a `Sheet`
- Confirmation gate uses full-screen `AlertDialog` (max-width 100% with safe-area padding)
- Input textarea becomes `min-h-16` to save screen real estate
- Bottom action bar in REVIEWING becomes sticky with `safe-area-inset-bottom`

---

## Error & Edge-Case Matrix

| Scenario | State | UX Behavior |
|----------|-------|-------------|
| LLM returns malformed JSON | DRAFTING → REVIEWING transition | Show inline error: "The AI draft was incomplete. You can go back to chat to refine your request, or fill the form manually." + "Back to Chat" / "Edit Manually" buttons |
| User navigates away during CHATTING | Any | Auto-save conversation draft to localStorage; restore on return with `AlertDialog` "Resume previous conversation?" |
| Backend rate-limited | CHATTING or DRAFTING | Toast: "Prompter is temporarily rate-limited. Please wait X seconds." Disable input with countdown. |
| User edits a field, then regenerates draft | REVIEWING → DRAFTING | Show `AlertDialog`: "Regenerating will overwrite your edits. Continue?" Options: "Keep my edits and regenerate" / "Discard edits and regenerate" / "Cancel" |
| Task creation fails after confirmation | CONFIRMING → LAUNCHED | Show error toast + return to REVIEWING with a banner: "Task creation failed. Your draft is preserved. Retry?" |
| Network disconnect | Any | `offline-state.tsx` pattern (already exists in panel) + "Reconnecting..." banner |

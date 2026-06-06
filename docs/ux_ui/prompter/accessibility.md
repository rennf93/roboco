# Prompter Accessibility Specification

**Version:** 1.0  
**Date:** 2026-06-06  
**Standard:** WCAG 2.1 Level AA

---

## Overview

Prompter must be fully accessible to users who navigate by keyboard, use screen readers, or require reduced motion. Because the interface includes dynamically generated AI content, state transitions, and a mandatory confirmation gate, accessibility cannot be an afterthought — it must be baked into the interaction design.

---

## 1. Keyboard Navigation

### Global Shortcuts

| Shortcut | Action | Scope |
|----------|--------|-------|
| `Ctrl+K` (or `Cmd+K`) | Focus chat input | CHATTING, EMPTY |
| `Ctrl+Shift+H` | Toggle history sidebar | CHATTING, REVIEWING |
| `Ctrl+Enter` | Submit chat message | CHATTING, EMPTY |
| `Esc` | Close modal gate (if open) | CONFIRMING |
| `Esc` | Cancel loading / return to chat | DRAFTING |

### Focus Order by State

#### EMPTY State
1. Quick-start suggestion chips (left to right)
2. Chat textarea
3. Send button

#### CHATTING State
1. "New Chat" button (if toolbar visible)
2. Message list (focusable as a scroll region, not individual messages)
3. Chat textarea
4. Send button
5. History sidebar toggle (if visible)
6. History sidebar items (if sidebar open)

#### REVIEWING State
1. "Back to Chat" button
2. "Looks Good" button
3. "Regenerate Draft" button
4. Form fields (top to bottom, left to right)
5. Revert-to-AI buttons (inside each edited field)
6. "Removed Suggestions" collapsible (if present)

#### CONFIRMING State (Modal Gate)
1. Checkbox 1: "I have reviewed the task title and description"
2. Checkbox 2: "I have checked the acceptance criteria"
3. Checkbox 3: "I understand this task will enter the dev pipeline"
4. "Go Back & Edit" button
5. "Create Task" button

**Rationale:** Checkboxes come before buttons so the user must intentionally traverse them. The primary action is last to prevent accidental activation via rapid Tab presses.

### Focus Indicators

- All focusable elements use the existing `ring` token: `outline-ring/50` (`--ring: oklch(0.708 0 0)`).
- In dark mode, `--ring` becomes `oklch(0.556 0 0)` — both pass 3:1 contrast against adjacent backgrounds per WCAG 2.4.7 (Focus Visible).
- Custom focus styles:
  - Chat textarea: `ring-2 ring-primary` on focus.
  - AI-suggestion badge buttons: `ring-1 ring-primary` on focus.
  - Checkboxes in confirmation gate: `ring-2 ring-primary` on focus.

---

## 2. Screen-Reader Behavior

### Live Regions

Prompter uses dynamic content heavily. The following `aria-live` regions are required:

| Region | `aria-live` Value | Content |
|--------|-------------------|---------|
| `#prompter-status` | `polite` | "AI is drafting your task. Step 2 of 4: Defining acceptance criteria." |
| `#prompter-message-list` | `polite` | Announces when a new message is added: "New message from AI" |
| `#prompter-draft-changes` | `polite` | Announces when a field is edited: "Title edited by you." |
| `#prompter-errors` | `assertive` | Announces validation errors: "Title must be at least 5 characters." |

### Message Semantics

Each chat message must be a `role="article"` with:
- `aria-label="User message"` or `aria-label="AI message"`
- `aria-describedby` pointing to a timestamp (if timestamps are added)

**Example:**
```tsx
<div
  role="article"
  aria-label={msg.role === "user" ? "Your message" : "AI message"}
  className="..."
>
  ...
</div>
```

### AI-Generated Content Announcements

When the LLM streams or returns a full response, screen readers must announce it intelligently:

- **Streaming responses:** Do not announce every token. Instead, announce once when streaming begins (`aria-live="polite"`: "AI is responding") and once when it ends ("AI response complete").
- **Draft generation:** Announce the step progression via the status region (see `#prompter-status` above).
- **Draft ready:** Announce "Draft ready. Review panel open. 6 fields suggested by AI."

### Confirmation Gate Announcements

When the modal gate opens:
- `aria-live="assertive"`: "Confirmation dialog. Please review the task summary and confirm three checklist items before creating the task."
- When a checkbox is checked: `aria-live="polite"`: "Checklist item 1 of 3 confirmed."
- When all checkboxes are checked: `aria-live="polite"`: "All checklist items confirmed. Create Task button enabled."

### Form Field Labels

Every field in the draft-review panel must have an accessible label:

```tsx
// Good
<Label htmlFor="title">
  Title
  <span aria-label="AI suggested this value">...</span>
</Label>
<Input id="title" aria-describedby="title-error title-hint" />
<p id="title-hint" className="sr-only">
  AI suggested: Build login API endpoint
</p>
```

The `sr-only` hint provides the original AI-suggested value to screen-reader users even if the user has edited it.

---

## 3. Color & Contrast

### AI-Suggestion Badge Contrast

| Element | Light Mode | Dark Mode | WCAG AA Ratio |
|---------|------------|-----------|---------------|
| `[AI]` badge text | `text-muted-foreground` (OKLCH 0.556) | `text-muted-foreground` (OKLCH 0.708) | 4.5:1 ✓ |
| `[Edited]` badge text | `text-amber-600` | `text-amber-400` | 4.5:1 ✓ |
| Error text | `text-destructive` | `text-destructive` | 4.5:1 ✓ |

### Confirmation Gate Contrast

- Dialog overlay: `bg-black/50` on top of `bg-background` — sufficient visual separation.
- Checkboxes: Native browser checkbox with `accent-color` set to `var(--primary)`.
- Disabled "Create Task" button: `opacity-50` + `cursor-not-allowed` — not relying solely on color.

### Non-Color Differentiators

Per WCAG 1.4.1 (Use of Color), AI-suggested vs human-edited fields must never rely on color alone:

- `[AI]` badge: includes `Sparkles` icon + text "AI-suggested"
- `[Edited]` badge: includes `Pencil` icon + text "You edited this"
- Error state: includes `border-destructive` + icon + text message

---

## 4. Motion & Animation

### Reduced Motion (`prefers-reduced-motion`)

All animations in Prompter must respect `prefers-reduced-motion: reduce`.

| Animation | Default | Reduced Motion |
|-----------|---------|----------------|
| Chat message entrance | `fade-in slide-up 200ms` | `opacity 0→1 0ms` (instant) |
| Drafting overlay pulse | `animate-pulse` | Static icon, no pulse |
| Loading spinner | `animate-spin` | Static `Loader2` icon |
| Modal gate open | `zoom-in-95 fade-in 200ms` | Instant open |
| Sidebar slide | `slide-in-from-right 300ms` | Instant |

**Implementation:**
Use the existing Tailwind `motion-safe:` and `motion-reduce:` utilities:

```tsx
<div className="motion-safe:animate-pulse motion-reduce:opacity-80">
  ...
</div>
```

### Auto-Scroll Behavior

- Default: Chat auto-scrolls smoothly to new messages (`scroll-behavior: smooth`).
- Reduced motion: Auto-scroll jumps instantly (`scroll-behavior: auto`).

---

## 5. Cognitive Accessibility

### Plain Language

- All labels use sentence case, not title case: "Back to chat" not "Back To Chat".
- Confirmation gate description avoids jargon: "dev pipeline" is explained as "...and be claimable by agents".
- Error messages explain *why*, not just *what*: "Title must be at least 5 characters so agents have enough context" (if UX copy allows; otherwise stick to existing panel error style).

### Progressive Disclosure

- Advanced fields (Project, Product, Parent Task, Dependencies) are collapsed by default behind an `Advanced Options` collapsible — same pattern as `create-task-dialog.tsx`.
- The confirmation gate summary shows a maximum of 3 acceptance criteria with a "+N more" link — prevents cognitive overload.

### Consistent Patterns

- All buttons use the same variant semantics as the rest of the panel:
  - Primary action: `Button` (default variant)
  - Secondary action: `Button variant="outline"`
  - Destructive action: `Button variant="destructive"`
  - Tertiary action: `Button variant="ghost"`

---

## 6. Responsive Accessibility

### Mobile (<768px)

- Touch targets: All buttons and checkboxes must be ≥44×44px.
- Input font size: `text-base` (16px) minimum to prevent iOS zoom on focus.
- History sidebar: Uses `Sheet` component with `dismissible` behavior (swipe-to-close or overlay tap).
- Confirmation gate: Full-screen modal with `safe-area-inset-top` and `safe-area-inset-bottom` padding.

### Tablet (768px–1023px)

- Two-column draft-review panel collapses to single column.
- Focus order remains logical (top-to-bottom).

---

## 7. ARIA Reference Table

| Element | Role | ARIA Attributes |
|---------|------|-----------------|
| Chat container | `region` | `aria-label="Prompter chat"` |
| Message list | `log` | `aria-live="polite" aria-relevant="additions"` |
| User message bubble | `article` | `aria-label="Your message"` |
| AI message card | `article` | `aria-label="AI message"` |
| Draft-review panel | `form` | `aria-labelledby="review-draft-title"` |
| AI badge | `img` | `aria-label="AI suggested"` (or `aria-hidden` + label on parent) |
| Edited badge | `img` | `aria-label="You edited this"` |
| Confirmation dialog | `alertdialog` | `aria-modal="true" aria-labelledby="confirm-title"` |
| Loading overlay | `status` | `aria-live="polite" aria-busy="true"` |
| History sidebar | `complementary` | `aria-label="Prompt history"` |

---

## 8. Testing Checklist (for QA)

- [ ] All interactive elements reachable via Tab key
- [ ] Focus order is logical in every state (EMPTY, CHATTING, DRAFTING, REVIEWING, CONFIRMING)
- [ ] Focus trap works in CONFIRMING modal (Tab cycles within dialog)
- [ ] Screen reader announces "AI is responding" and "AI response complete"
- [ ] Screen reader announces field edit status changes ("Title edited by you")
- [ ] Color contrast passes 4.5:1 for all text and 3:1 for focus indicators
- [ ] `prefers-reduced-motion` disables or simplifies all animations
- [ ] Touch targets ≥44×44px on mobile
- [ ] No horizontal scroll at 320px viewport width
- [ ] Confirmation gate cannot be bypassed with keyboard (Enter on first checkbox does not submit)
- [ ] Error announcements use `aria-live="assertive"`

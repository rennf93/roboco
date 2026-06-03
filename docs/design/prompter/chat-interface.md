# Chat Interface — Design Specification

**Last Updated:** 2026-06-03  
**Spec Version:** 1.0  
**Owner:** ux-dev-1  
**References:** `README.md` for shared tokens

---

## 1. Message Thread Area

### 1.1 Container

```
┌─────────────────────────────────────────────────────────┐
│ Message Thread (flex column, gap 16px, overflow-y auto) │
│  padding: 24px 24px 16px 24px                           │
│  flex-grow: 1                                           │
│  role="log", aria-live="polite", aria-label="Chat"      │
└─────────────────────────────────────────────────────────┘
```

| Property | Value |
|---|---|
| Display | `flex`, `flex-direction: column` |
| Gap between bubbles | `16 px` (`space-4`) |
| Padding | `24 px 24 px 16 px 24 px` |
| Overflow | `overflow-y: auto` |
| Scroll behaviour | `scroll-behavior: smooth` |
| Background | `surface-0` |
| ARIA | `role="log"`, `aria-live="polite"`, `aria-label="Conversation"` |

---

### 1.2 Message Timestamp Separator

Timestamps are shown between messages when >10 minutes have elapsed since the previous message, or at the start of a new session.

```
──────────── Today at 14:32 ────────────
```

| Property | Value |
|---|---|
| Layout | Flex row, align-center, gap `8 px` |
| Lines | `1 px` solid `surface-300`, flex-grow 1 on each side |
| Text | 11 px, `neutral-400`, uppercase, tracking `+0.05 em` |
| Margin | `8 px 0` above and below the separator row |

---

## 2. Message Bubbles

### 2.1 Bubble Layout

Each message is a flex row. User messages are right-aligned; AI messages are left-aligned.

```
AI message row:
┌──────────────────────────────────────────────┐
│ [Avatar 28px]  ┌────────────────────────────┐│
│                │  AI response text here     ││
│                │  can span multiple lines   ││
│                └────────────────────────────┘│
└──────────────────────────────────────────────┘

User message row:
┌──────────────────────────────────────────────┐
│              ┌──────────────────────────────┐│
│              │ User message text here       ││
│              └──────────────────────────────┘│
└──────────────────────────────────────────────┘
```

| Property | User Bubble | AI Bubble |
|---|---|---|
| Alignment | `align-self: flex-end` | `align-self: flex-start` |
| Max width | 72% of thread container width | 72% of thread container width |
| Background | `user-bubble-bg` (`brand-500`) | `ai-bubble-bg` (`surface-100`) |
| Text colour | `user-bubble-text` (`#FFFFFF`) | `ai-bubble-text` (`neutral-700`) |
| Border radius | `18px 18px 4px 18px` | `4px 18px 18px 18px` |
| Padding | `10px 14px` | `12px 16px` |
| Font size | 14 px | 14 px |
| Line height | 1.5 | 1.5 |
| Font weight | 400 | 400 |
| Border | none | none |
| Box shadow | none | `elevation-1` |

> **Radius note:** The "tail" corner (bottom-right for user, bottom-left for AI) uses `radius-sm` (4 px) to give the visual impression of a speech tail pointing toward the sender. All other corners use `radius-xl` (18 px).

### 2.2 Avatar (AI messages only)

| Property | Value |
|---|---|
| Size | 28 × 28 px |
| Shape | Circle (`border-radius: 9999px`) |
| Background | `brand-500` gradient (45°, `brand-500` → `brand-600`) |
| Icon | RoboCo logo mark, 16 px, `#FFFFFF` |
| Alignment | `align-self: flex-end` (sits at bottom of bubble) |
| Margin-right | `8 px` |

User messages do **not** have an avatar.

### 2.3 Sender Label

Shown above the bubble, only on the first message in a consecutive run from the same sender.

| Property | Value |
|---|---|
| Text | `"You"` (user) or model name e.g. `"Claude 3.5 Sonnet"` (AI) |
| Font size | 12 px |
| Font weight | 600 |
| Colour | `neutral-500` |
| Margin-bottom | `4 px` |
| Visibility | Only on first message in a run; hidden if same sender as previous bubble |

### 2.4 Code Blocks inside AI Messages

When AI response contains code:

| Property | Value |
|---|---|
| Background | `neutral-900` |
| Text colour | `#FFFFFF` (with syntax highlighting per language token type) |
| Font | `'JetBrains Mono', 'Fira Code', 'Courier New', monospace` |
| Font size | 13 px |
| Line height | 1.6 |
| Padding | `12 px 16 px` |
| Border radius | `radius-md` (8 px) |
| Margin | `8 px 0` within bubble |
| Copy button | Appears top-right corner on hover; 28 px icon button, `surface-0` bg |
| Language tag | Top-left, 10 px, `neutral-400` |

### 2.5 Message Actions (hover)

On hover of any message bubble, a row of action icons appears above the bubble (user) or below the bubble (AI).

| Action | Icon | Tooltip |
|---|---|---|
| Copy | Clipboard 16 px | "Copy message" |
| Regenerate (AI only) | Refresh-CW 16 px | "Regenerate response" |
| Delete | Trash 16 px, `danger-500` | "Delete message" |

Action row properties:

| Property | Value |
|---|---|
| Container | Flex row, gap `4 px`, opacity 0 → 1 on bubble hover |
| Transition | `opacity 150 ms ease-default` |
| Alignment | Flex-end for user, flex-start for AI |
| Icon button size | 28 × 28 px |
| Icon button bg | `surface-0` on hover, transparent at rest |
| Icon button radius | `radius-full` |
| Margin | `4 px` above (user) or below (AI) the bubble |

---

## 3. Input Composer Area

```
┌─────────────────────────────────────────────────────────┐
│ Input Composer (padding 12px 16px, border-top surface-300) │
│                                                         │
│ ┌─────────────────────────────────────┬───────────────┐ │
│ │ textarea (auto-grow, 14px)          │  [Send ↵ 36px]│ │
│ │ placeholder: "Message RoboCo..."    │               │ │
│ └─────────────────────────────────────┴───────────────┘ │
│  ⌘↵ to send (12px neutral-400, left-aligned below row)  │
└─────────────────────────────────────────────────────────┘
```

### 3.1 Composer Container

| Property | Value |
|---|---|
| Background | `surface-0` |
| Border-top | `1 px solid surface-300` |
| Padding | `12 px 16 px` |
| Box-shadow | `elevation-2` (upward: `0 -4px 12px rgba(0,0,0,0.06)`) |
| Z-index | 10 |

### 3.2 Textarea

| Property | Value |
|---|---|
| Min height | 40 px (1 row) |
| Max height | 144 px (6 rows at 24 px/row) |
| Auto-grow | Yes — height increases with content until max |
| Overflow | `overflow-y: auto` when content exceeds max height |
| Scrollbar | Native, styled thin (`scrollbar-width: thin`) |
| Padding | `10 px 12 px` |
| Font size | 14 px |
| Line height | 1.5 (24 px) |
| Colour | `neutral-900` |
| Placeholder text | `"Message RoboCo..."` |
| Placeholder colour | `neutral-400` |
| Background | `surface-0` |
| Border | `1.5 px solid surface-300` |
| Border radius | `radius-md` (8 px) |
| Resize | `none` (auto-grow handles height; width is constrained by layout) |
| Focus ring | `2 px solid focus-ring`, `outline-offset: 2 px` (replaces border) |
| Tab behaviour | `Tab` moves focus (does **not** insert a tab character) |
| Submit shortcut | `⌘↵` (macOS) / `Ctrl+Enter` (Windows/Linux) |

### 3.3 Send Button

Positioned to the right of the textarea, vertically centred relative to the first row of text.

| Property | Value |
|---|---|
| Size | 36 × 36 px |
| Shape | `radius-full` |
| Icon | Arrow-up or Send icon, 18 px |
| Default state | Background `brand-500`, icon `#FFFFFF` |
| Hover state | Background `brand-600` |
| Active state | Background `brand-700` (darken 10%), scale `0.95` |
| Disabled state | Background `surface-200`, icon `neutral-400`, `cursor: not-allowed` |
| Disabled condition | Textarea is empty OR whitespace-only OR streaming in progress |
| Transition | `background-color 150 ms ease-default`, `transform 100 ms ease-in` |
| Margin-left | `8 px` from textarea |
| aria-label | `"Send message"` |
| Loading state | Spinner icon replaces send icon; button disabled while streaming |

### 3.4 Keyboard Shortcut Hint

Shown below the textarea row, left-aligned.

| Property | Value |
|---|---|
| Content (macOS) | `⌘↵ to send` |
| Content (Windows/Linux) | `Ctrl+↵ to send` |
| Detection | Use `navigator.platform` or `userAgent` to detect OS |
| Font size | 12 px |
| Colour | `neutral-400` |
| Margin-top | `6 px` |
| Visibility | Always visible (not conditional on focus) |

---

## 4. Typing Indicator

Shown in the message thread area after the user sends a message and before the AI begins streaming a response.

### 4.1 Appearance

```
[Avatar 28px]  ●  ●  ●
               ^  ^  ^
               dot dot dot
               (8px, brand-500/60%, brand-500/80%, brand-500)
```

| Property | Value |
|---|---|
| Container | Same layout as AI message bubble row |
| Avatar | Same as §2.2 (28 × 28 px) |
| Bubble bg | `ai-bubble-bg` (`surface-100`) |
| Bubble padding | `14 px 18 px` |
| Bubble border-radius | `4px 18px 18px 18px` (matches AI bubble) |
| Bubble min-width | `64 px` |

### 4.2 Dot Specification

| Property | Value |
|---|---|
| Dot count | 3 |
| Dot size | 8 × 8 px |
| Dot shape | Circle (`border-radius: 9999px`) |
| Dot colour | `brand-500` |
| Gap between dots | `4 px` |
| Container | Flex row, align-center, gap `4 px` |

### 4.3 Animation

Each dot animates vertically (translateY) in sequence.

```css
@keyframes typing-bounce {
  0%   { transform: translateY(0); opacity: 0.4; }
  33%  { transform: translateY(-6px); opacity: 1; }
  66%  { transform: translateY(0); opacity: 0.4; }
  100% { transform: translateY(0); opacity: 0.4; }
}
```

| Property | Value |
|---|---|
| Animation name | `typing-bounce` |
| Duration | `1200 ms` (full cycle) |
| Easing | `cubic-bezier(0.4, 0, 0.2, 1)` (`ease-default`) |
| Iteration | `infinite` |
| Fill mode | `both` |
| Dot 1 delay | `0 ms` |
| Dot 2 delay | `160 ms` |
| Dot 3 delay | `320 ms` |

> **Accessibility:** The typing indicator container should have `role="status"`, `aria-label="AI is typing"`, and `aria-live="polite"`. It disappears when streaming begins.

### 4.4 Transition In / Out

| Event | Behaviour |
|---|---|
| User submits message | Typing indicator fades in over `200 ms` |
| AI stream begins | Typing indicator fades out over `150 ms`, first token appears |
| Stream completes | No visual change on the typing indicator (it's already gone) |

---

## 5. Empty State

Shown in the message thread area when there are no messages in the current conversation (new chat or first visit).

### 5.1 Layout

```
┌──────────────────────────────────────────────────────────────┐
│                                                              │
│                         (spacer, flex-grow 1)               │
│                                                              │
│              ┌──────────────────────────────┐               │
│              │       [Logo mark 48px]        │               │
│              │                              │               │
│              │  What can I help you create  │               │
│              │         today?               │               │
│              │                              │               │
│              │ Describe a task, ask a       │               │
│              │ question, or pick a prompt   │               │
│              │ below to get started.        │               │
│              │                              │               │
│              │  ┌─────────┐ ┌───────────┐  │               │
│              │  │ Draft a │ │ Review my │  │               │
│              │  │ task    │ │ backlog   │  │               │
│              │  └─────────┘ └───────────┘  │               │
│              │  ┌──────────────┐ ┌───────┐ │               │
│              │  │ Explain the  │ │ Show  │ │               │
│              │  │ architecture │ │ stats │ │               │
│              │  └──────────────┘ └───────┘ │               │
│              └──────────────────────────────┘               │
│                                                              │
│                         (spacer, flex-grow 1)               │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### 5.2 Container

| Property | Value |
|---|---|
| Layout | Flex column, align-items center, justify-content center |
| Flex-grow | 1 (fills remaining thread area) |
| Padding | `48 px 24 px` |
| Max width of content | `480 px`, centered |

### 5.3 Logo Mark

| Property | Value |
|---|---|
| Size | 48 × 48 px |
| Style | RoboCo logo mark only (no wordmark) |
| Background | `brand-50` circle, `brand-500` icon fill |
| Shape | Circle, `radius-full` |
| Margin-bottom | `20 px` |

### 5.4 Headline Copy

| Property | Value |
|---|---|
| Text | `"What can I help you create today?"` |
| Font size | 20 px |
| Font weight | 600 |
| Colour | `neutral-900` |
| Text align | Center |
| Margin-bottom | `8 px` |

### 5.5 Subtext Copy

| Property | Value |
|---|---|
| Text | `"Describe a task, ask a question, or pick a prompt below to get started."` |
| Font size | 14 px |
| Font weight | 400 |
| Colour | `neutral-500` |
| Text align | Center |
| Margin-bottom | `24 px` |

### 5.6 Suggested-Prompt Chips

Optional: 2–4 chips shown in a flex-wrap row. On click, chip text populates the textarea.

```
┌─────────────────────┐ ┌──────────────────────┐
│  Draft a task spec  │ │  Review my backlog   │
└─────────────────────┘ └──────────────────────┘
┌─────────────────────────────────┐ ┌──────────┐
│  Explain the agent architecture │ │  Stats   │
└─────────────────────────────────┘ └──────────┘
```

#### Suggested chip content (default set)

| # | Label |
|---|---|
| 1 | `"Draft a task spec"` |
| 2 | `"Review my backlog"` |
| 3 | `"Explain the agent architecture"` |
| 4 | `"Show team stats"` |

#### Chip Design

| Property | Value |
|---|---|
| Background | `surface-200` |
| Text colour | `neutral-700` |
| Font size | 13 px |
| Font weight | 400 |
| Padding | `8 px 14 px` (`space-2` `space-3`) |
| Border radius | `radius-full` |
| Border | none |
| Container | Flex-wrap row, gap `8 px`, justify-content center |
| Cursor | `pointer` |
| Hover bg | `surface-300` |
| Hover transition | `background-color 150 ms ease-default` |
| Active bg | `brand-100` |
| Focus ring | `2 px solid focus-ring`, `outline-offset: 2 px` |
| On click | Populate textarea with chip label text; do not submit |

> **Copy note:** Chip labels should be sentence-case. Keep labels short (≤ 5 words). Chips are optional — if no suggested prompts are configured, the empty state renders without the chip row.

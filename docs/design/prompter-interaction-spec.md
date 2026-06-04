# Prompter — Interaction Design & Zero-State Experience Spec

**Version:** 1.0  
**Date:** 2026-06-04  
**Author:** UX/UI Cell (ux-dev-1)  
**Status:** Draft for Frontend Implementation

---

## Table of Contents

1. [Overview](#1-overview)
2. [Zero-State Experience](#2-zero-state-experience)
3. [Progressive Disclosure — Draft Panel](#3-progressive-disclosure--draft-panel)
4. [Confirmation Gate](#4-confirmation-gate)
5. [History / Library View](#5-history--library-view)
6. [Component Reference Map](#6-component-reference-map)
7. [MentorChat Adaptation](#7-mentorchat-adaptation)
8. [Step Count & Time Estimate](#8-step-count--time-estimate)
9. [Responsive & Accessibility Notes](#9-responsive--accessibility-notes)

---

## 1. Overview

The **Prompter** is a conversational task-authoring interface that lets humans create well-formed RoboCo tasks through a guided LLM dialogue rather than filling a traditional form. The user arrives, chats with an AI assistant, watches a structured draft panel build up on the right, reviews the complete task, and launches it into the system — all in under three minutes.

### Routes

| Route | Purpose |
|---|---|
| `/prompter` | Main chat entry point; zero-state on first load |
| `/prompter/[sessionId]` | Active chat session with progressive draft panel |
| `/prompter/[sessionId]/confirm` | Dedicated confirmation/review gate (route-level, not modal) |
| `/prompter/history` | Library of past sessions |

---

## 2. Zero-State Experience

> **Goal:** Never show a blank chat box. Users arriving at `/prompter` for the first time (or when no session is active) must immediately understand the purpose and have a fast on-ramp.

### 2.1 Page Layout (Zero State)

```
┌─────────────────────────────────────────────────────────────────┐
│  [Sidebar Nav]  │            PROMPTER                           │
│                 │                                               │
│                 │  ┌─────────────────────────────────────────┐ │
│                 │  │  [Hero Section — centered, ~40% height]  │ │
│                 │  │                                          │ │
│                 │  │  ✦  Describe what you need,             │ │
│                 │  │     we'll build the task spec for you.  │ │
│                 │  │                                          │ │
│                 │  │  ┌──────────┐ ┌──────────┐ ┌─────────┐ │ │
│                 │  │  │ Chip A   │ │ Chip B   │ │ Chip C  │ │ │
│                 │  │  └──────────┘ └──────────┘ └─────────┘ │ │
│                 │  │       [+ more prompt chips]              │ │
│                 │  └─────────────────────────────────────────┘ │
│                 │                                               │
│                 │  ┌─────────────────────────────────────────┐ │
│                 │  │  [Chat Input Area — bottom-anchored]     │ │
│                 │  │                                          │ │
│                 │  │  [Model Selector ▾]  [Text field    ] ▶ │ │
│                 │  └─────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

### 2.2 Value-Prop Headline

**Primary headline (h1, rendered with `text-3xl font-semibold tracking-tight`):**
> Describe what you need, we'll build the task spec for you.

**Subheadline (p, rendered with `text-muted-foreground text-base`):**
> Chat with the Prompter AI to turn your idea into a fully structured task — accepted, assigned, and running in minutes.

### 2.3 Example Prompt Chips

The chips serve as conversation starters. They are rendered as clickable `Button` elements with `variant="outline"` and `size="sm"` in a `flex flex-wrap gap-2 justify-center` container. Clicking a chip populates the chat input and auto-submits.

**Chip copy (minimum 3 required; all 6 ship in v1):**

| # | Chip label | Full prompt text on submit |
|---|---|---|
| 1 | 🔍 Research a new feature | "I need to research whether we should add [feature]. I want a task that audits what we have today, benchmarks alternatives, and produces a recommendation doc." |
| 2 | 🐛 Fix a bug in the UI | "There's a bug in our UI where [describe the problem]. I need a task to investigate the root cause and ship a fix." |
| 3 | 📊 Improve our dashboards | "I want to improve the dashboards by adding [metric/chart]. Task should cover data layer, component design, and QA." |
| 4 | 🤖 Add a new agent skill | "I want to give one of our agents a new capability: [skill]. Task should cover design, implementation, and integration tests." |
| 5 | 📝 Write documentation | "We need documentation for [feature]. Task should produce a user-facing guide, a developer reference, and a changelog entry." |
| 6 | ⚡ Performance improvement | "Our [page/service] is slow. I want a task to profile it, identify the top bottleneck, and ship a measurable improvement." |

> **Note (implementation):** Chips 4–6 are rendered in a second row; if viewport width < 768px, collapse to a horizontal scrollable row.

### 2.4 Model Selector Placement

The model selector sits **inline in the chat input bar**, to the left of the text field. It is always visible (including zero-state) so users can intentionally choose a model before starting.

**Default model:** Claude Sonnet (human-readable label; not raw model ID)

**Model selector component:** `Select` (shadcn/ui) with `size="sm"` and `w-48` width constraint.

**Model list display format:**

```
Claude Sonnet — fast and capable      ← default, pre-selected
Claude Opus — deep reasoning
Claude Haiku — quick and lightweight
```

> **⚠ Custom component callout:** The model selector needs a thin wrapper (`PrompterModelSelect`) around the shadcn/ui `Select` to: (a) fetch model options from `GET /api/prompter/models`, (b) map raw model IDs to human-readable labels and descriptions, and (c) persist the selection to session state. This is a custom component — flag for implementation.

---

## 3. Progressive Disclosure — Draft Panel

> **Goal:** As the conversation progresses, a structured draft panel appears and fills in automatically on the right side of the screen. Fields materialize turn-by-turn — the user never sees the full form upfront.

### 3.1 Layout (Active Session)

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Sidebar]  │    CHAT AREA (60%)       │   DRAFT PANEL (40%)        │
│             │                          │                             │
│             │  ┌────────────────────┐  │  ┌─────────────────────┐  │
│             │  │ [AI message]       │  │  │ Task Draft          │  │
│             │  │ [User message]     │  │  │ ─────────────────── │  │
│             │  │ [AI message]       │  │  │ Title: ...          │  │
│             │  │ ...                │  │  │ Team: ...           │  │
│             │  └────────────────────┘  │  │ Description: ...    │  │
│             │                          │  │ ACs: ...            │  │
│             │  [Model] [Input   ] ▶   │  │ ...                 │  │
│             │                          │  │ [Review & Launch ▶] │  │
│             │                          │  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

The draft panel is a `Sheet` (shadcn/ui, `side="right"`) on mobile/tablet, and an inline `div` with `w-[40%] border-l` on desktop (≥1024px). The panel uses `ScrollArea` from shadcn/ui to handle overflow.

### 3.2 Field Emergence Mapping

Each field in the draft panel appears only after the conversational turn that produces it. Fields are displayed with a subtle `animate-in fade-in-0 slide-in-from-right-2 duration-300` transition (Tailwind + shadcn animation utilities).

| Turn # | User/AI exchange covers | Draft panel field(s) that appear | API field populated |
|---|---|---|---|
| **Turn 0** (zero state) | — | *(Panel hidden; shown only after first AI reply)* | — |
| **Turn 1** | User describes the broad goal; AI asks clarifying questions about scope | **Title** (auto-suggested, editable) | `title` |
| **Turn 1** | AI infers or asks about team assignment | **Team** (dropdown, pre-filled from AI suggestion) | `team` |
| **Turn 2** | User and AI discuss the type and nature of work | **Task Type** + **Nature** | `task_type`, `nature` |
| **Turn 2** | AI generates initial description from the dialogue so far | **Description** (editable textarea, shows live char count; must reach 20 chars) | `description` |
| **Turn 3** | AI proposes 3–5 acceptance criteria; user can edit/add/remove | **Acceptance Criteria** (editable list, min 1 item) | `acceptance_criteria` |
| **Turn 3** | AI estimates complexity based on scope discussed | **Estimated Complexity** (Low / Medium / High selector) | `estimated_complexity` |
| **Turn 4** | User approves or refines; all fields green | **Review & Launch** CTA becomes active | *(triggers navigation to confirm route)* |

> **Progressive disclosure rule:** A field is rendered as a disabled skeleton (`Skeleton` component, shadcn/ui) with label visible but value greyed out until the relevant turn completes. Once populated, the skeleton is replaced with the actual value using the fade-in animation.

### 3.3 Inline Validation

All fields in the draft panel validate in real-time against the `TaskCreate` API schema:

| Field | Validation rule | Error display |
|---|---|---|
| `title` | Required, non-empty | Red border (`border-destructive`) + `FormMessage` below |
| `description` | Min 20 characters | Character counter (`text-muted-foreground text-xs`) turns red below 20 |
| `acceptance_criteria` | Non-empty list (≥1 item) | Inline warning above list |
| `team` | Required; must be valid team slug | `Select` shows error variant |
| `estimated_complexity` | Required (`Low`/`Medium`/`High`) | Shown as `RadioGroup` — none selected = red label |

The **Review & Launch** button in the draft panel stays `disabled` until all validations pass. Error state uses `Button` with `disabled` prop (shadcn/ui) — no custom disabled styles needed.

### 3.4 Live Editing in the Draft Panel

All draft panel fields are **editable inline** even while the chat continues. Edits to the draft panel do NOT restart the conversation — they update the in-memory draft state (Zustand store) and are reflected in the next AI turn context via the session's running summary.

---

## 4. Confirmation Gate

> **Goal:** A dedicated, route-level review step before launching. This is `/prompter/[sessionId]/confirm` — not a modal, not a browser `confirm()` dialog, not a drawer. Navigating here is intentional and reversible.

### 4.1 Route & Navigation

- **URL:** `/prompter/[sessionId]/confirm`
- **Triggered by:** Clicking "Review & Launch" in the draft panel (only enabled when all fields valid)
- **Browser back button:** Returns to `/prompter/[sessionId]` — session is preserved, no data loss
- **No URL-sharing leakage:** Session IDs are UUIDs; the confirm page is not indexable

### 4.2 Page Layout

```
┌───────────────────────────────────────────────────────────────┐
│  [Sidebar Nav]  │     CONFIRM YOUR TASK                       │
│                 │                                             │
│                 │  ┌───────────────────────────────────────┐ │
│                 │  │  SUMMARY CARD                          │ │
│                 │  │                                        │ │
│                 │  │  Title:       [Draft title text]       │ │
│                 │  │  Team:        [Team name]              │ │
│                 │  │  Type:        [Task type / Nature]     │ │
│                 │  │  Complexity:  [Low / Medium / High]    │ │
│                 │  │  Model used:  [Claude Sonnet]          │ │
│                 │  │                                        │ │
│                 │  │  Description (excerpt, 2 lines max):  │ │
│                 │  │  "[Description text…]"                 │ │
│                 │  │                                        │ │
│                 │  │  Acceptance Criteria (N items):        │ │
│                 │  │    ✓ Criterion 1                       │ │
│                 │  │    ✓ Criterion 2                       │ │
│                 │  │    ✓ Criterion 3                       │ │
│                 │  │    [+ N more]  ← collapsed if >3      │ │
│                 │  └───────────────────────────────────────┘ │
│                 │                                             │
│                 │  [← Back to editing]   [🚀 Launch Task →]  │
│                 │                                             │
└───────────────────────────────────────────────────────────────┘
```

### 4.3 Summary Card Specification

The summary card uses the shadcn/ui `Card` component (`CardHeader`, `CardContent`, `CardFooter`).

```
Card (className="max-w-2xl mx-auto shadow-md")
  CardHeader
    CardTitle  — "Review Your Task"
    CardDescription — "Check the details below, then launch or go back to refine."
  CardContent
    [Field rows using dl > dt + dd pattern, or a custom LabeledField layout]
    [Acceptance criteria list — ul with li items, collapsed after 3 with a Button to expand]
  CardFooter (className="flex justify-between items-center gap-4 pt-6")
    Button (variant="ghost", onClick → navigate back) — "← Back to editing"
    Button (variant="default", size="lg", onClick → POST /api/tasks) — "🚀 Launch Task"
```

**Summary card fields:**

| Label | Content | Component |
|---|---|---|
| Title | Full draft title text | `p` with `font-semibold` |
| Team | Team display name | `Badge` with `variant="secondary"` |
| Task Type | Task type + nature combined | `p` |
| Complexity | Complexity level | `Badge` with color class (see note below) |
| Model used | Human-readable model name | `p` with `text-muted-foreground` |
| Description | Full description in a `blockquote`-style box | `blockquote` with `border-l-2 pl-4 italic text-muted-foreground` |
| Acceptance Criteria | Numbered list; max 3 visible, rest collapsed | `ul` with Tailwind list styles; `Button variant="link"` to expand |

**Complexity badge color mapping:**

| Value | Badge class |
|---|---|
| Low | `bg-emerald-100 text-emerald-800` |
| Medium | `bg-amber-100 text-amber-800` |
| High | `bg-red-100 text-red-800` |

> **⚠ Custom component callout:** The complexity badge color mapping requires a thin wrapper (`ComplexityBadge`) since shadcn/ui `Badge` does not have built-in `Low/Medium/High` variants. This is a custom component — mark for implementation.

### 4.4 CTAs

| CTA | Label | Behavior | Component |
|---|---|---|---|
| Back to editing | `← Back to editing` | `router.back()` — returns to chat session, all draft state preserved | `Button` (`variant="ghost"`) |
| Launch task | `🚀 Launch Task` | POSTs to `POST /api/tasks` with the full `TaskCreate` payload; on success → redirect to task detail or back to `/prompter/history` | `Button` (`variant="default"`, `size="lg"`) with loading state (`disabled` + spinner) |

**Launch button loading state:**
- While awaiting API response: button shows `<Loader2 className="mr-2 h-4 w-4 animate-spin" />` icon + `"Launching…"` label
- On success: redirect (no success toast needed on confirm page — task detail shows the live task)
- On error: `toast({ variant: "destructive", title: "Launch failed", description: error.message })` — button returns to default state, session preserved

---

## 5. History / Library View

> **Goal:** Give users a library of all their Prompter sessions — past drafts, launched tasks, and abandoned attempts — so they can resume or fork previous work.

### 5.1 Route

**URL:** `/prompter/history`  
**Nav placement:** Secondary item under the Prompter section in the sidebar nav

### 5.2 Page Layout

```
┌─────────────────────────────────────────────────────────────────────┐
│  [Sidebar]  │   TASK DRAFTS & HISTORY                               │
│             │                                                         │
│             │  [Search input                    ] [+ New Prompter]   │
│             │                                                         │
│             │  ┌─────────────────────────────────────────────────┐  │
│             │  │ [Draft title]        [DRAFT badge]   5 min ago  │  │
│             │  │ Team: Frontend · Claude Sonnet                  │  │
│             │  │                [Re-open ↑]  [Fork ⑂]           │  │
│             │  ├─────────────────────────────────────────────────┤  │
│             │  │ [Task title]         [LAUNCHED badge] 2 d ago   │  │
│             │  │ Team: Backend · Claude Opus                     │  │
│             │  │                [View task ↗]  [Fork ⑂]         │  │
│             │  ├─────────────────────────────────────────────────┤  │
│             │  │ [Draft title]        [ABANDONED badge] 1 w ago  │  │
│             │  │ Team: UX/UI · Claude Haiku                      │  │
│             │  │                [Re-open ↑]  [Fork ⑂]           │  │
│             │  └─────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 5.3 Session List Card Specification

Each session is rendered as a `Card` with `p-4 flex flex-col gap-2 hover:bg-muted/50 transition-colors cursor-pointer`.

**Card content:**

| Element | Content | Component / Class |
|---|---|---|
| Draft title | Session title (or `"Untitled draft"` if not yet named) | `p` with `font-medium text-sm` |
| Status badge | `DRAFT` / `LAUNCHED` / `ABANDONED` | `Badge` with color class (see below) |
| Timestamp | Relative time (e.g. "5 min ago", "2 days ago") | `time` element with `text-xs text-muted-foreground` using `date-fns/formatDistanceToNow` |
| Metadata row | `Team: [name] · [Model name]` | `p` with `text-xs text-muted-foreground` |
| Actions | Re-open / Fork / View task | `Button` components (see below) |

**Status badge color mapping:**

| Status | Badge class |
|---|---|
| `draft` | `bg-yellow-100 text-yellow-800` |
| `launched` | `bg-emerald-100 text-emerald-800` |
| `abandoned` | `bg-gray-100 text-gray-600` |

> **⚠ Custom component callout:** Status badge colors require a thin `SessionStatusBadge` wrapper since shadcn/ui `Badge` does not have `draft/launched/abandoned` variants. Mark for implementation.

**Action buttons (right-aligned in card footer):**

| Session status | Available actions | Button spec |
|---|---|---|
| `draft` | Re-open, Fork | `Button variant="ghost" size="sm"` |
| `launched` | View task (links to task detail), Fork | `Button variant="ghost" size="sm"` |
| `abandoned` | Re-open, Fork | `Button variant="ghost" size="sm"` |

**Re-open:** Navigates to `/prompter/[sessionId]` — restores chat history and draft state.  
**Fork:** Creates a new session pre-populated with the current session's draft fields; navigates to `/prompter/[newSessionId]`. The forked session has status `draft` and a fresh conversation.  
**View task:** Opens `/tasks/[taskId]` in the same tab (task was launched from this session).

### 5.4 Search

A `Input` (shadcn/ui) with `placeholder="Search drafts…"` and a `Search` icon (lucide-react) at the left via `startAdornment`-equivalent pattern (`relative` wrapper + `absolute` icon positioning). Filters the session list client-side by title match.

> **⚠ Custom component callout:** The search input with icon requires a `SearchInput` wrapper using relative/absolute positioning since shadcn/ui `Input` does not natively support start adornments. Mark for implementation.

### 5.5 Empty State

When the user has no sessions yet (first-time visitor to `/prompter/history`):

```
┌─────────────────────────────────────────────────────────────────────┐
│                                                                       │
│                    [Icon: MessageSquarePlus, 48px]                   │
│                                                                       │
│                    No task drafts yet.                                │
│            Start a conversation to author your first task.            │
│                                                                       │
│                       [+ Start Prompter]                             │
│                                                                       │
└─────────────────────────────────────────────────────────────────────┘
```

**Empty state spec:**

| Element | Spec |
|---|---|
| Icon | `MessageSquarePlus` from lucide-react, `className="h-12 w-12 text-muted-foreground mx-auto mb-4"` |
| Heading | `"No task drafts yet."` — `p` with `text-lg font-medium text-foreground` |
| Subtext | `"Start a conversation to author your first task."` — `p` with `text-sm text-muted-foreground` |
| CTA | `Button variant="default"` with `"+ Start Prompter"` label → navigates to `/prompter` |
| Container | `div` with `flex flex-col items-center justify-center py-24 gap-2` |

---

## 6. Component Reference Map

All components used in the Prompter feature, mapped to their shadcn/ui names and Tailwind utilities.

### 6.1 shadcn/ui Components Used

| Component | shadcn/ui name | Usage in Prompter |
|---|---|---|
| `Button` | `Button` | Chips, CTAs (Launch, Back, Re-open, Fork), draft panel actions |
| `Card`, `CardHeader`, `CardContent`, `CardFooter`, `CardTitle`, `CardDescription` | `Card` | Confirmation summary card, session list cards |
| `Badge` | `Badge` | Status badges (draft/launched/abandoned), complexity, team |
| `Input` | `Input` | Chat text input, history search |
| `Select`, `SelectTrigger`, `SelectContent`, `SelectItem` | `Select` | Model selector, team selector in draft panel |
| `Textarea` | `Textarea` | Description field in draft panel (auto-resizing) |
| `ScrollArea` | `ScrollArea` | Chat message list, draft panel overflow, history list |
| `Sheet`, `SheetContent`, `SheetHeader`, `SheetTitle` | `Sheet` | Draft panel on mobile/tablet (side="right") |
| `Skeleton` | `Skeleton` | Progressive disclosure placeholder state for pending fields |
| `Tooltip`, `TooltipContent`, `TooltipTrigger`, `TooltipProvider` | `Tooltip` | Chip hover hints, model description |
| `RadioGroup`, `RadioGroupItem` | `RadioGroup` | Estimated complexity selector (Low/Medium/High) |
| `FormMessage` | `FormMessage` (react-hook-form integration) | Inline validation errors in draft panel |
| `Separator` | `Separator` | Divider between sessions in history list |

> All shadcn/ui components are installed via `npx shadcn-ui@latest add [component-name]`. Verify each is already installed in the panel project before adding.

### 6.2 Icons Used (lucide-react)

| Icon | Usage |
|---|---|
| `Send` | Chat submit button |
| `ChevronDown` | Model selector dropdown indicator |
| `Loader2` | Launch button loading spinner |
| `CheckCircle2` | Acceptance criteria list items |
| `MessageSquarePlus` | History empty state |
| `RotateCcw` | Re-open action |
| `GitFork` | Fork action |
| `ExternalLink` | View task action (opens task detail) |
| `Search` | History search input icon |
| `ArrowRight` | Review & Launch CTA arrow |

### 6.3 Custom Components (Not in shadcn/ui — Flagged for Implementation)

> **These components require custom implementation.** They are flagged here so the frontend developer can plan accordingly.

| Custom component | Wraps | Purpose | Estimated effort |
|---|---|---|---|
| `PrompterModelSelect` | shadcn/ui `Select` | Fetches models from API, maps IDs to labels, persists selection to session | Small — ~60 LOC |
| `ComplexityBadge` | shadcn/ui `Badge` | Applies `Low/Medium/High` color classes; not a built-in Badge variant | Tiny — ~20 LOC |
| `SessionStatusBadge` | shadcn/ui `Badge` | Applies `draft/launched/abandoned` color classes | Tiny — ~20 LOC |
| `SearchInput` | shadcn/ui `Input` | Adds left icon slot via relative/absolute positioning | Small — ~30 LOC |
| `PromptChip` | shadcn/ui `Button` | Chip appearance + auto-submit behavior on click | Small — ~40 LOC |
| `DraftField` | shadcn/ui `Skeleton` + target component | Renders skeleton → actual field transition with animation | Medium — ~80 LOC |
| `AccordionAC` | native `ul` / `li` + `Button` | Collapsible acceptance criteria list in confirm card | Small — ~50 LOC |

---

## 7. MentorChat Adaptation

> The existing `MentorChat` component (located in the panel codebase — multi-turn, auto-scroll, follow-up suggestions) is the **starting point** for the Prompter's chat UI. The Prompter does **not** create a new chat component from scratch; it extends MentorChat.

### 7.1 Known MentorChat API Surface (from codebase audit)

| Feature | Known behavior |
|---|---|
| Multi-turn | Maintains conversation history; each turn appended |
| Auto-scroll | Automatically scrolls to the latest message |
| Follow-up suggestions | Renders clickable suggestion chips after AI replies |
| Component type | React functional component; accepts props |

### 7.2 Props Extended on MentorChat for Prompter

The following props are **added** (not changed) to MentorChat when used in the Prompter context. The frontend cell should verify the exact prop interface in the component source and adjust as needed.

| New prop | Type | Purpose | Required |
|---|---|---|---|
| `sessionId` | `string \| null` | Prompter session UUID; `null` for zero-state; passed to streaming endpoint | Yes |
| `systemPrompt` | `string` | Prompter-specific system prompt for the LLM (task authoring guidance) | Yes |
| `onDraftUpdate` | `(draft: Partial<TaskDraft>) => void` | Callback invoked whenever the AI produces structured field extractions; drives draft panel state | Yes |
| `modelId` | `string` | Currently selected model ID; sent with each chat request | Yes |
| `onSessionCreate` | `(sessionId: string) => void` | Callback fired when the backend creates a new session; used to update the URL to `/prompter/[sessionId]` | Yes |

> **⚠ TBD for frontend:** Confirm whether MentorChat currently accepts `systemPrompt` as a prop or whether it is hardcoded. If hardcoded, the frontend cell must add the prop during the adaptation.

### 7.3 Slots / Render Props Added

| Slot | Purpose | Rendered where |
|---|---|---|
| `headerSlot` | Renders the `PrompterModelSelect` above the chat input area | Above the message list, right-aligned |
| `footerSlot` | Renders the example prompt chips in zero-state | Below the chat input, only when `sessionId === null` and no messages |

> **Implementation pattern:** If MentorChat does not support named slots natively, use a composition approach — wrap MentorChat in a `PrompterChat` component that renders the additional elements via JSX alongside `<MentorChat />`.

### 7.4 Styling Changes

| Change | Tailwind adjustment | Reason |
|---|---|---|
| Wider message bubble max-width | `max-w-[85%]` → `max-w-[90%]` | Chat area is now only 60% of screen width, narrower bubbles feel cramped |
| AI message background | `bg-muted` | Consistent with panel design system |
| Input row padding | `px-4 pb-4` | Matches Prompter's bottom-anchored input bar layout |
| Remove MentorChat page title | Hide any internal header/title | Prompter page renders its own page-level heading |

---

## 8. Step Count & Time Estimate

> **Goal:** The full flow from landing on `/prompter` to a launched task must take **under 3 minutes** for a prepared user.

### 8.1 Happy Path Flow — Steps & Timing

| Step # | User action / System response | Estimated time |
|---|---|---|
| 1 | User navigates to `/prompter` — zero state loads with headline + chips + model selector | 2 s |
| 2 | User clicks a prompt chip (or types a custom prompt) — first message auto-sent | 5 s |
| 3 | AI responds with clarifying questions (Turn 1); **Title** and **Team** fields appear in draft panel | 10–15 s |
| 4 | User replies to clarify scope and team (Turn 1 response) | 15 s |
| 5 | AI responds; **Task Type**, **Nature**, and **Description** appear (Turn 2) | 10–15 s |
| 6 | User reviews and adjusts description inline if needed; replies to continue | 15–20 s |
| 7 | AI proposes acceptance criteria; **ACs** and **Complexity** appear (Turn 3) | 10–15 s |
| 8 | User edits/approves ACs; all fields are green; **Review & Launch** button activates | 15–20 s |
| 9 | User clicks **Review & Launch** → navigates to `/prompter/[sessionId]/confirm` | 2 s |
| 10 | User reviews summary card — scans title, team, description, ACs | 15–20 s |
| 11 | User clicks **🚀 Launch Task** → API POST; redirect to history/task detail | 3–5 s |
| **Total** | | **~2 min 0 s – 2 min 35 s** |

### 8.2 Variance Scenarios

| Scenario | Extra time | Still < 3 min? |
|---|---|---|
| User types a custom prompt instead of using a chip | +10 s | ✅ Yes |
| User requests one edit turn (Turn 4 — refinement) | +25 s | ✅ Yes |
| User edits multiple AC items inline | +20 s | ✅ Yes |
| Slow network (2G equivalent, +1–2 s per LLM response) | +15 s | ✅ Yes |
| User re-reads and re-edits on confirm page | +30 s | ✅ Yes (at 3 min 05 s borderline) |

> **Design conclusion:** The default 3-turn happy path completes in ~2 minutes. Even with a 4th refinement turn and careful review, the flow stays within 3 minutes. The step count for the default happy path is **11 steps** end-to-end.

---

## 9. Responsive & Accessibility Notes

### 9.1 Breakpoints

| Breakpoint | Layout change |
|---|---|
| < 768px (mobile) | Draft panel becomes a `Sheet` (drawer, `side="right"`); toggled by a floating `Button` in the bottom-right corner. Sidebar nav collapses to hamburger. Prompt chips scroll horizontally. |
| 768px – 1023px (tablet) | Draft panel is a `Sheet` or a bottom `Sheet` (`side="bottom"`). Chat takes full width. |
| ≥ 1024px (desktop) | Side-by-side layout: chat 60%, draft panel 40%, both visible simultaneously. |

### 9.2 Accessibility

| Requirement | Implementation |
|---|---|
| Chat message live region | `aria-live="polite"` on the message list container |
| Chip buttons | `aria-label="Start with: [chip full prompt]"` |
| Draft panel fields | Proper `<label>` + `htmlFor` on all form inputs; use `FormLabel` from react-hook-form |
| Status badges | `aria-label="Status: [status]"` on badge elements |
| Skeleton loading | `aria-busy="true"` on skeleton containers; `aria-label="Loading [field name]"` |
| Confirm page launch button | `aria-describedby` pointing to a visually hidden summary count (e.g. "Launching task with 4 acceptance criteria for the Backend team") |
| Focus management | After clicking "Review & Launch", focus moves to the confirm page `<h1>` via `ref.focus()` |
| Color contrast | All badge color combos (`bg-yellow-100/text-yellow-800`, etc.) must pass WCAG AA 4.5:1 — verify with a contrast checker during implementation |

---

## Appendix: API Contracts Referenced

| Endpoint | Method | Used in |
|---|---|---|
| `/api/prompter/chat` | `POST` (streaming SSE) | Chat message sends, Turn responses |
| `/api/prompter/models` | `GET` | `PrompterModelSelect` populates model list |
| `/api/prompter/sessions` | `GET` | History/library view fetches session list |
| `/api/prompter/sessions` | `POST` | Creates new session on first message |
| `/api/prompter/sessions/[id]` | `GET` | Restores session state on re-open |
| `/api/prompter/sessions/[id]` | `PATCH` | Updates draft fields in session |
| `/api/tasks` | `POST` | Launch Task CTA on confirm page |

> API shapes are defined by the backend cell. This spec assumes the above endpoint structure; coordinate with `be-dev` if the routes differ.

---

*Spec authored by UX/UI Cell (ux-dev-1) for the RoboCo Prompter feature.*  
*Frontend implementation: fe-dev-1. Backend implementation: be-dev-1.*  
*Questions or amendments: DM `ux-pm` or post in `#uxui-cell`.*

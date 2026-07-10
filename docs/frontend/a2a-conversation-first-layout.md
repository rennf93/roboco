# A2A Conversation-First Layout: Identity Colors, Connection States, Transcript Motion, and Empty States

## Overview

The A2A page now features a conversation-first design with three major enhancements:

1. **Agent identity colors** — every agent is assigned a team-scoped color bucket (Backend, Frontend, UX/UI, Board, CEO, System)
2. **Collapsible context pane** (xl:+ breakpoint) — participant identity cards, linked task summary, and a no-task hint
3. **Live connection states** — four distinct visual states (connected, connecting, reconnecting, disconnected) with dismissable banners
4. **Transcript entrance motion** — new rows fade in with `transform`/`opacity` transitions, guarded by `prefers-reduced-motion`
5. **Split empty/error states** — distinct messaging for "no conversation selected", "selected but empty", and "fetch failed with retry"

This document covers the component API, integration, and usage patterns.

## Agent Team Colors

### Overview

Agent colors are scoped to **team** (cell), not per-agent, because it remains legible at 22-agent scale and never requires new tokens when a cell grows.

**File:** `panel/src/lib/agent-utils.ts`

### `AgentTeamColor` Type

```typescript
export type AgentTeamColor =
  | "backend"
  | "frontend"
  | "ux_ui"
  | "board"
  | "ceo"
  | "system";
```

### `getAgentTeamColor(agentId: string | null | undefined): AgentTeamColor`

Resolves an agent slug (or UUID) to its team color by inspecting the slug prefix. Unknown slugs fall back to `system` — color is a scanning aid, never critical, so unknown agents don't throw.

**Resolution rules:**

- `ceo` or `CEO` → `"ceo"`
- Slug starts with `be-` → `"backend"`
- Slug starts with `fe-` → `"frontend"`
- Slug starts with `ux-` → `"ux_ui"`
- `main-pm`, `product-owner`, `head-marketing`, `auditor` → `"board"`
- All others → `"system"`

**Example:**

```tsx
import { getAgentTeamColor } from "@/lib/agent-utils";

const color = getAgentTeamColor("fe-dev-1");
// Returns "frontend"

const fallback = getAgentTeamColor("unknown-slug");
// Returns "system"
```

### `TEAM_COLOR_CLASSES: Record<AgentTeamColor, string>`

Maps each team color to a pre-composed Tailwind class string with `bg-{color}/15`, `border-{color}/40`, and `text-{color}` weights for light and dark modes.

**Exported classes:**

```typescript
{
  backend:   "bg-blue-500/15 border-blue-500/40 text-blue-700 dark:text-blue-400",
  frontend:  "bg-violet-500/15 border-violet-500/40 text-violet-700 dark:text-violet-400",
  ux_ui:     "bg-fuchsia-500/15 border-fuchsia-500/40 text-fuchsia-700 dark:text-fuchsia-400",
  board:     "bg-amber-500/15 border-amber-500/40 text-amber-700 dark:text-amber-400",
  ceo:       "bg-primary/15 border-primary/40 text-primary",
  system:    "bg-slate-500/15 border-slate-500/40 text-slate-700 dark:text-slate-400",
}
```

**No new Tailwind tokens are introduced** — all colors reuse existing families already in the codebase.

**Usage:**

```tsx
import { getAgentTeamColor, TEAM_COLOR_CLASSES, cn } from "@/lib/agent-utils";

export function AgentBadge({ agentId }: { agentId: string }) {
  const teamColor = getAgentTeamColor(agentId);
  return (
    <div className={cn("p-2 rounded border", TEAM_COLOR_CLASSES[teamColor])}>
      {getAgentDisplayName(agentId)}
    </div>
  );
}
```

## Context Pane (xl:+ Collapsible)

The context pane renders participant identity cards, linked task summary, and a no-task hint at the `xl:` breakpoint and above. It is read-only and collapsible via a button in the page header.

**File:** `panel/src/components/a2a/a2a-context-pane.tsx`

### `A2AContextPane` Component

```typescript
interface A2AContextPaneProps {
  agentA: string;        // Participant slug
  agentB: string;        // Participant slug
  taskId: string | null; // Linked task UUID, or null when no task
}

export function A2AContextPane({ agentA, agentB, taskId }: A2AContextPaneProps)
```

### Rendered Sections

#### Identity Cards

Each participant renders as an `IdentityCard` subcomponent:

- **Avatar:** 9×9px circle with agent initials, colored by `TEAM_COLOR_CLASSES`
- **Name:** Agent display name from `getAgentDisplayName()`
- **Team badge:** Colored outline badge showing team (e.g. "frontend", "board")
- **Link target:** Clicking navigates to `/agents/{slug}`

#### Linked Task Summary

When `taskId` is provided:

- **Title:** Task name (truncated to one line)
- **Status badge:** Colored badge for task status (e.g. "in_progress", "completed")
- **View link:** "View task" hyperlink to `/tasks/{taskId}`
- **Skeleton loading state:** While the task is being fetched

When `taskId` is null or not provided:

- **No-task hint:** "This conversation has no linked task"

### Styling

- Light, minimal 3px padding per section
- Separator border below the "Context" header
- Smooth hover state on identity cards (light background tint)
- Uses existing `border-b` dividers and `space-y-` gap utilities

### Page Integration

The pane is integrated into the A2A page grid layout:

- **Below `xl:`** — hidden (full width for Roster and Stream columns)
- **`xl:` and above** — visible as a third column when open (localStorage-persisted toggle via the page header button)

**Layout grid (xl:+):**

```
Roster (col-span-3) | Stream (col-span-6) | Context (col-span-3)
```

When context pane is closed, grid falls back to:

```
Roster (col-span-4) | Stream (col-span-8)
```

## Connection State Rendering

The connection badge renders in the pane header, and a dismissable banner appears above the message list when reconnecting or disconnected.

**File:** `panel/src/components/a2a/a2a-connection-badge.tsx`
**Utils:** `panel/src/components/a2a/a2a-utils.ts`

### `ConnectionState` Type (from `@/lib/websocket/connection`)

```typescript
type ConnectionState = "connected" | "connecting" | "reconnecting" | "disconnected";
```

### `A2AConnectionBadge` Component

```typescript
export function A2AConnectionBadge({ state }: { state: ConnectionState })
```

**Rendering:**

- **Dot:** 2×2px circle with color based on state (see `connectionDotClasses()`)
- **Label:** Text label from `connectionStateLabel()`
- **Icon:** Spinner for `connecting`/`reconnecting`, WiFi-off for `disconnected`

**All four states render distinctly:**

| State | Dot | Label | Icon | Notes |
|-------|-----|-------|------|-------|
| `connected` | Emerald, static | "Live" | None | No motion (live conversation) |
| `connecting` | Amber, pulsing | "Connecting…" | Spinner | On first connect |
| `reconnecting` | Amber, pulsing | "Reconnecting…" | Spinner | After a drop; messages may be stale |
| `disconnected` | Muted, static | "Offline" | WiFi-off | No auto-recovery |

**Pulsing animation** (`animate-pulse`) is guarded by `motion-reduce:animate-none`, respecting user accessibility settings.

### `A2AConnectionBanner` Component

```typescript
interface A2AConnectionBannerProps {
  state: "reconnecting" | "disconnected";
  onDismiss: () => void;
}

export function A2AConnectionBanner({
  state,
  onDismiss,
}: A2AConnectionBannerProps)
```

**Rendering:**

- **Container:** Full-width strip above the message list
- **Background color:** Amber tint for reconnecting; destructive (red) tint for disconnected
- **Message:** "Reconnecting — messages may be out of date" or "Disconnected — reconnecting automatically"
- **Dismiss button:** X icon, right-aligned; fires `onDismiss` callback

The banner is **scoped to the stream pane** — not a page-level `OfflineState` — so multiple A2A tabs can render independent connection states.

### Helper Functions

**`connectionStateLabel(state: ConnectionState): string`**

Returns human-readable label for the connection badge.

**`connectionDotClasses(state: ConnectionState): string`**

Returns Tailwind classes for the connection dot:

- `connected`: `"bg-emerald-500"` (no pulse)
- `connecting` / `reconnecting`: `"bg-amber-500 animate-pulse motion-reduce:animate-none"`
- `disconnected`: `"bg-muted-foreground/40"` (muted static)

## Transcript Entrance Motion & Empty/Error States

The transcript component now supports motion-reduced entrance transitions, a scrolled-up "New messages" pill, and split empty/error states.

**File:** `panel/src/components/a2a/a2a-transcript.tsx`

### `A2ATranscript` Component

```typescript
interface A2ATranscriptProps {
  messages: A2AChatMessage[];
  isLoading: boolean;
  /** False when no conversation/pair is selected at all — distinguishes
   * "nothing to show yet" from "selected but empty" (design doc §5).
   * Defaults true (existing callers). */
  hasSelection?: boolean;
  /** True when the messages fetch itself failed — a scoped retry, not the
   * page-level OfflineState. */
  error?: boolean;
  onRetry?: () => void;
}

export function A2ATranscript({
  messages,
  isLoading,
  hasSelection = true,
  error = false,
  onRetry,
}: A2ATranscriptProps)
```

### Entrance Motion

New transcript rows render with `transform`/`opacity` only (no layout thrashing):

- **Initial state:** `opacity-0 scale-y-95 origin-bottom` (transparent, scaled down from bottom)
- **Animation:** One paint frame after the row renders, transitions to `opacity-100 scale-y-100` with `transition-[opacity,transform] duration-200`
- **prefers-reduced-motion guard:** Falls back to instant `opacity-100` and no scale transform

**Implementation:**

The component tracks message IDs via render-phase state (`seenIds`). Newly arrived IDs are flagged in `newRowIds` for one paint frame, then cleared. A `requestAnimationFrame` batches the class application to avoid layout thrashing.

**No external libraries required** — uses native CSS transitions.

### New Messages Pill

When the user scrolls up and new messages arrive, a dismissable "New messages ↓" pill appears above the transcript, prompting them to scroll to the bottom.

**Behavior:**

- **At bottom:** New rows animate in; pill is hidden
- **Scrolled up:** New rows don't animate in; pill appears instead, allowing the user to catch up at their own pace
- **On dismiss:** Pill vanishes but messages remain in the list
- **On scroll to bottom:** Pill hides and newly arrived rows resume animating in

### Empty & Error States

#### No Conversation Selected

When `hasSelection === false`:

```
┌──────────────────────────────┐
│                              │
│  [Messages icon]             │
│  "Select a conversation"     │
│                              │
└──────────────────────────────┘
```

#### Conversation Selected, No Messages

When `hasSelection === true`, `messages.length === 0`, and `error === false`:

```
┌──────────────────────────────┐
│                              │
│  [Messages icon]             │
│  "No messages yet"           │
│                              │
└──────────────────────────────┘
```

#### Fetch Error (Scoped Retry)

When `error === true`:

```
┌──────────────────────────────┐
│                              │
│  [Alert Triangle icon]       │
│  "Failed to load messages"   │
│  [Retry button]              │
│                              │
└──────────────────────────────┘
```

Clicking "Retry" fires the `onRetry()` callback; the consumer is responsible for re-fetching and clearing the `error` flag.

**This is a scoped, transcript-level retry** — not the page-level `OfflineState` that handles network down.

### Message Row Styling

Each message row uses the agent's team color via `TEAM_COLOR_CLASSES`:

- **Avatar:** Colored circle with initials
- **Sender name:** Rendered above the message
- **Timestamp:** Relative time (e.g. "2 minutes ago") from `date-fns`
- **Message body:** Rendered as Markdown via the `<Markdown>` component

## Page Integration

**File:** `panel/src/app/(dashboard)/a2a/page.tsx`

### Grid Layout

The page wires the new components into an `xl:`-responsive grid:

```tsx
<div className="grid grid-cols-4 xl:grid-cols-12 gap-4">
  {/* Roster (col-span-3 or col-span-4) */}
  {/* Stream (col-span-6 or col-span-8, grows when context is closed) */}
  {/* Context pane (col-span-3, hidden below xl:) */}
</div>
```

### Context Pane Toggle

A button in the page header (or Stream pane header) controls the context pane open/closed state, persisted via the `ui-store` zustand slice:

- **Key:** `a2aContextOpen` (boolean, localStorage-backed via zustand)
- **Button:** `PanelRightClose` / `PanelRightOpen` icon from lucide-react
- **Callback:** `setA2aContextOpen(!a2aContextOpen)`

### Connection Badge & Banner

The Stream pane header renders the `A2AConnectionBadge` with the current WebSocket state. When state is `reconnecting` or `disconnected`, the `A2AConnectionBanner` appears above the message list.

**Connection state** is managed via the `useWebSocket()` hook and passed down from page to Stream pane.

### Usage Example

```tsx
import { A2AContextPane } from "@/components/a2a/a2a-context-pane";
import { A2AConnectionBadge, A2AConnectionBanner } from "@/components/a2a/a2a-connection-badge";
import { A2ATranscript } from "@/components/a2a/a2a-transcript";

export function A2AStreamPane({
  agentA,
  agentB,
  taskId,
  messages,
  connectionState,
  isLoading,
  error,
  onRetry,
}: {
  agentA: string;
  agentB: string;
  taskId: string | null;
  messages: A2AChatMessage[];
  connectionState: ConnectionState;
  isLoading: boolean;
  error?: boolean;
  onRetry?: () => void;
}) {
  const [dismissedBanner, setDismissedBanner] = useState(false);

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between px-3 py-2 border-b">
        <h2 className="font-medium">
          {getAgentDisplayName(agentA)} ↔ {getAgentDisplayName(agentB)}
        </h2>
        <A2AConnectionBadge state={connectionState} />
      </div>

      {connectionState !== "connected" && !dismissedBanner && (
        <A2AConnectionBanner
          state={connectionState as "reconnecting" | "disconnected"}
          onDismiss={() => setDismissedBanner(true)}
        />
      )}

      <A2ATranscript
        messages={messages}
        isLoading={isLoading}
        hasSelection={true}
        error={error}
        onRetry={onRetry}
      />
    </div>
  );
}
```

## Testing

All new components and utilities are covered by unit tests:

- **`agent-utils.test.ts`** — `getAgentTeamColor()`, `TEAM_COLOR_CLASSES` access
- **`a2a-context-pane.test.tsx`** — Identity card rendering, linked task summary, no-task hint
- **`a2a-connection-badge.test.tsx`** — All four connection states, banner dismiss, icon presence
- **`a2a-utils.test.ts`** — `connectionStateLabel()`, `connectionDotClasses()`
- **`a2a-transcript.test.tsx`** — Empty states, entrance motion, new messages pill, error retry

Run tests with:

```bash
cd panel
pnpm test
```

## Accessibility & Responsiveness

### Breakpoints

- **Below `md:`** — Stack vertically, full width
- **`md:` to below `xl:`** — Roster + Stream side-by-side, context pane hidden
- **`xl:` and above** — Roster + Stream + Context (when open)

### Motion

All entrance animations respect `prefers-reduced-motion`:

- Transcript row entry: Falls back to instant `opacity-100`
- Connection dot pulse: Falls back to static `animate-none`
- New messages pill: No motion applied (fade in/out handled by CSS class application)

### Focus & Keyboard

- **Identity card links** — Keyboard-navigable to `/agents/{slug}` and `/tasks/{taskId}`
- **Banner dismiss button** — `aria-label="Dismiss"` for screen readers
- **Connection badge** — No interactive element (informational only)

## Migration & Breaking Changes

### `A2ATranscript` props

The `hasSelection` and `error` props are new but **backward compatible**:

- `hasSelection` defaults to `true` (existing behavior)
- `error` defaults to `false` (no error state)
- `onRetry` is optional (only called if implemented)

Old code continues to work:

```tsx
// Old code (still works)
<A2ATranscript messages={messages} isLoading={false} />

// New code (explicit states)
<A2ATranscript
  messages={messages}
  isLoading={false}
  hasSelection={selectedConversationId !== null}
  error={fetchError}
  onRetry={refetchMessages}
/>
```

## Design Notes

### Why Team Colors, Not Per-Agent?

Team scoping (Backend, Frontend, etc.) scales to 22 agents without visual noise and doesn't require adding new Tailwind tokens per agent. It maps to the organizational structure and is sufficient for scanning.

### Why `/15` and `/40` Opacity?

The `/15` background and `/40` border provide:

- Visual distinction without overwhelming
- Sufficient contrast for WCAG AA compliance
- Consistency with existing `PairAvatar` pulse treatment

### Why `transform`/`opacity` Only?

Layout-thrashing animations (animating `width`, `height`, `left`, `top`) cause forced reflows on every frame. `transform` and `opacity` changes are GPU-accelerated and don't force layout recalculation, keeping animations smooth.

## Future Enhancements

- Persist context pane width preference (currently only open/closed toggle)
- Add a "Topic" summary in the context pane (when `taskId` is null)
- Extend connection state to show "cached" mode (reading from local storage while reconnecting)

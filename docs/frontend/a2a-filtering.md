# A2A Page Filtering & Agent Identity

## Overview

The A2A (Agent-to-Agent) page now features a unified filter bar and consistent agent identity rendering across both the switchboard (org-chart view) and classic conversation list. Both views respond identically to filtering and pulse animations, creating a cohesive experience regardless of the active view.

## Filter Bar

The `A2AFilterBar` component renders above the switchboard/list content and provides two independent filtering controls:

### Component

**File:** `panel/src/components/a2a/a2a-filter-bar.tsx`

**Props:**

- `status` (`A2AStatusFilter`): Current status filter, either `"active"` or `"all"`
- `onStatusChange` (callback): Fires when the Active/All toggle changes
- `search` (string): Current search query
- `onSearchChange` (callback): Fires as the user types in the search input

**Rendering:**

- Search input with placeholder "Search agent or topic..." accepts free-text queries
- Two toggle buttons: "Active" narrows to conversations with live activity; "All" shows everything
- Compact styling (7px button height, 3px font size) to avoid crowding the view

**Usage:**

```tsx
import { A2AFilterBar } from "@/components/a2a/a2a-filter-bar";

<A2AFilterBar
  status={statusFilter}
  onStatusChange={setStatusFilter}
  search={search}
  onSearchChange={setSearch}
/>
```

## Filter Utilities

The `a2a-filter-utils.ts` module exports pure, testable filtering logic shared by both views.

**File:** `panel/src/components/a2a/a2a-filter-utils.ts`

### `A2AStatusFilter` Type

```typescript
type A2AStatusFilter = "active" | "all";
```

### `filterConversations()`

Narrows the conversation list to the matching subset based on status and search query.

**Parameters:**

- `conversations`: `ReadonlyArray<AdminConversationSummary>`
- `status`: `A2AStatusFilter` — `"active"` filters to `conversation.status === "active"`; `"all"` passes all
- `search`: `string` — free-text query (case-insensitive)

**Behavior:**

- Searches across both agent slugs (raw IDs), their display names (via `getAgentDisplayName`), and the topic
- Empty search passes all conversations
- Status filter is applied first, then search

**Example:**

```typescript
const filtered = filterConversations(
  conversations,
  "active",
  "backend qa"
);
// Returns only active conversations where one agent is Backend QA or mentions "backend qa"
```

### `filterPairs()`

Narrows the switchboard pairs to the matching subset based on status and search query.

**Parameters:**

- `pairs`: `ReadonlyArray<AdminPairSummary>`
- `status`: `A2AStatusFilter` — `"active"` filters to pairs that have a `conversation_id` (have A2A'd); `"all"` passes all
- `search`: `string` — free-text query (case-insensitive)

**Behavior:**

- Searches across both agent slugs (raw IDs) and their display names
- Empty search passes all pairs
- Status filter is applied first, then search
- Note: `AdminPairSummary` has no backend `status` field, so "active" is interpreted as "has a conversation"

**Example:**

```typescript
const filtered = filterPairs(pairs, "active", "auditor");
// Returns only pairs where at least one agent matches "auditor" and the pair has an active conversation
```

## Conversation List Updates

The conversation list now accepts a `pulses` prop and renders agent avatars.

**File:** `panel/src/components/a2a/a2a-conversation-list.tsx`

### `A2AConversationListProps`

**New prop:**

- `pulses` (`Record<string, number>`): Maps `pairKey(agent_a, agent_b)` to the epoch timestamp of the latest pulse frame. This is the same `pulses` map the switchboard uses, keyed identically, so a conversation row flashes in sync with its pair's card on the switchboard.

**Example:**

```tsx
<A2AConversationList
  conversations={filteredConversations}
  selectedId={selectedId}
  onSelect={handleSelect}
  isLoading={loadingConversations}
  pulses={pulses}  // New: from page state
/>
```

### `ConversationRow` Subcomponent

Each conversation renders as a `ConversationRow` that mirrors the switchboard's pair card styling:

- **Avatars:** Two `PairAvatar` components (initials, colors) matching `A2APairCard`
- **Pulse animation:** Uses `usePulseFlash()` to determine if the row should flash hot
- **Styling:** Emerald background + shadow while pulsing, matches switchboard
- **Selection state:** Bordered/highlighted when selected, same as switchboard selection

### Breaking Change

The `pulses` prop is **required**. If you're calling `A2AConversationList` from outside the A2A page, you must supply it:

```typescript
// Old code (will TypeScript error)
<A2AConversationList conversations={data} ... />

// New code
const pulses = { "be-dev-1|be-qa": 1700000000000 };
<A2AConversationList conversations={data} ... pulses={pulses} />
```

If you don't have a pulses map available, pass an empty object `{}` — rows won't flash, but selection/interaction will work normally.

## Pulse-Flash Hook

**File:** `panel/src/hooks/use-pulse-flash.ts`

The `usePulseFlash()` hook extracts the pulse-flash animation logic from inline state in `A2APairCard`, making it reusable across the switchboard and conversation list.

### `usePulseFlash(pulsedAt: number | null): boolean`

Returns `true` for one paint frame after `pulsedAt` changes to a non-null value, then `false`. The consumer's CSS `transition-duration` does the actual fade-out.

**How it works:**

1. Seeded to `null` (not the initial `pulsedAt`), so a component that *mounts* already carrying a live pulse still flashes hot
2. On render, if `pulsedAt !== lastSeenPulse`, updates `lastSeenPulse` and sets `isPulsing = true` if `pulsedAt !== null`
3. On the next paint frame (via `requestAnimationFrame`), flips `isPulsing` back to `false`
4. CSS transition handles the fade — `transition-duration: PAIR_PULSE_FADE_MS` applied to elements that conditionally render the hot styling

**Example:**

```tsx
import { usePulseFlash } from "@/hooks/use-pulse-flash";
import { PAIR_PULSE_FADE_MS } from "@/components/a2a/a2a-switchboard-utils";

export function MyPulsedRow({ pulsedAt }: { pulsedAt: number | null }) {
  const isPulsing = usePulseFlash(pulsedAt);

  return (
    <div
      className={cn("p-2", isPulsing && "bg-emerald-500/15")}
      style={{ transitionDuration: `${PAIR_PULSE_FADE_MS}ms` }}
    >
      {/* content */}
    </div>
  );
}
```

## A2A Page Integration

**File:** `panel/src/app/(dashboard)/a2a/page.tsx`

The page composes these pieces:

1. **Filter state:** Lifts `statusFilter` and `search` to page level
2. **Derived state:** Computes `filteredPairs` and `filteredConversations` via `useMemo` on each render
3. **Filter bar:** Mounts `A2AFilterBar` above the view content
4. **Synchronized pulses:** Both `A2ASwitchboard` and `A2AConversationList` receive the same `pulses` map, keyed identically, so pulse animations sync across views

**Code sketch:**

```tsx
const [statusFilter, setStatusFilter] = useState<A2AStatusFilter>("all");
const [search, setSearch] = useState("");

const filteredPairs = useMemo(
  () => filterPairs(pairs, statusFilter, search),
  [pairs, statusFilter, search]
);

const filteredConversations = useMemo(
  () => filterConversations(conversations, statusFilter, search),
  [conversations, statusFilter, search]
);

// Both views receive the same pulses map
<A2AFilterBar
  status={statusFilter}
  onStatusChange={setStatusFilter}
  search={search}
  onSearchChange={setSearch}
/>

{view === "switchboard" ? (
  <A2ASwitchboard pairs={filteredPairs} pulses={pulses} ... />
) : (
  <A2AConversationList conversations={filteredConversations} pulses={pulses} ... />
)}
```

## Testing

All filtering and pulse behavior is covered by tests:

- **`a2a-filter-utils.test.ts`:** `filterConversations()` and `filterPairs()` with status/search combinations
- **`a2a-filter-bar.test.tsx`:** Button pressed state, search input changes, status toggle callbacks
- **`a2a-conversation-list.test.tsx`:** Avatar rendering, pulse flash detection (tests the `data-pulsing` attribute)
- **`page.test.tsx`:** Filter bar renders, narrows switchboard independently, narrows classic list independently

Run tests with:

```bash
cd panel
pnpm test
```

## Design Notes

### Status Filter Semantics

The "active" filter has different semantics across views due to data availability:

- **Conversations:** `active` filters to `conversation.status === "active"` (backend-provided status)
- **Pairs:** `active` filters to pairs with a non-null `conversation_id` (have A2A'd at least once)

This is intentional and documented in code comments. If the backend adds an explicit `AdminPairSummary.status` field in the future, update `filterPairs()` to use it instead of the `conversation_id` heuristic.

### Pulse Animation Timing

The pulse flash is a brief, high-contrast alert (emerald bg + shadow) that fades over `PAIR_PULSE_FADE_MS` (200ms by default, defined in `a2a-switchboard-utils.ts`). If you're experiencing chop or seeing the animation cut off, check:

1. The `usePulseFlash()` hook is being called (not bypassed)
2. The consumer element has `transition-[background-color,box-shadow]` or equivalent CSS
3. The `transitionDuration` inline style matches the fade constant

## Migration Guide

### If you use `A2AConversationList` in another context:

**Before:**

```tsx
<A2AConversationList
  conversations={conversations}
  selectedId={selectedId}
  onSelect={handleSelect}
  isLoading={false}
/>
```

**After:**

```tsx
<A2AConversationList
  conversations={conversations}
  selectedId={selectedId}
  onSelect={handleSelect}
  isLoading={false}
  pulses={{}} // Add this prop (empty object if no pulses available)
/>
```

### If you extract pulse-flash logic into other components:

Import and use the `usePulseFlash()` hook instead of rolling your own state management:

```tsx
import { usePulseFlash } from "@/hooks/use-pulse-flash";
import { PAIR_PULSE_FADE_MS } from "@/components/a2a/a2a-switchboard-utils";

const isPulsing = usePulseFlash(pulsedAt);
// Now use isPulsing to conditionally render the hot styling
```

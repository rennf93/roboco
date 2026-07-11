# Conversation-first layout, agent identity, and live-stream affordances

Interaction spec for the pattern that makes a live conversation the primary
surface of a view, rather than a secondary panel bolted onto a data table: a
three-region layout, a team-color agent identity scheme that scales to the
full 22-agent roster, connection-state visual treatment, a new-message
arrival cue, and the loading/empty/error states a conversation panel needs.
Written so a frontend developer can implement directly from this document
without further design clarification.

## Scope and where this lives

This is a **pattern spec**, not a new page proposal. It extends the one
conversation surface RoboCo already ships,
`panel/src/app/(dashboard)/a2a/page.tsx`, plus its sub-components:

| Piece | Existing file it extends |
|---|---|
| Layout | `panel/src/app/(dashboard)/a2a/page.tsx` (currently a two-pane `grid-cols-12` layout) |
| Roster / list rail | `a2a-switchboard.tsx`, `a2a-pair-card.tsx`, `a2a-conversation-list.tsx` |
| Message stream | `a2a-transcript.tsx` |
| Agent identity | `panel/src/lib/agent-utils.ts` (`getAgentInitials`, `getAgentDisplayName`) |
| Connection state | `panel/src/hooks/use-websocket.ts` (`ConnectionState` = `"connecting" \| "connected" \| "reconnecting" \| "disconnected"`), `panel/src/components/layout/connection-status.tsx` |

Nothing here replaces the `/a2a` page's existing behavior (message fetch,
reply composer, switchboard/list toggle) — every section below is additive:
a third pane, a color layer on an existing avatar, a refined connection
badge, an entrance transition for new rows, and the states around the
stream when it has nothing (yet) to show. The same three-region composition
and identity/connection/arrival treatment apply to any future conversation
surface RoboCo adds (e.g. a unified agent-activity inbox) without
re-deriving the pattern.

**Design bar dial read:** dense product UI (a data-heavy live-ops surface
inside existing panel chrome), not a landing page — variance 2, motion 2-3,
density 7, the UX/UI cell's dashboard default. No new radius or shadow
tokens; color additions are a bounded, named palette (below), not ad hoc
hex values.

---

## 1. Conversation-first layout

### The three regions

A conversation-first surface is composed of three regions, always in this
order left-to-right, with the stream always the widest:

```
┌─ Roster (list rail) ─┬───── Stream (primary) ─────┬─ Context (collapsible) ─┐
│ conversation/agent    │  message-by-message,         │  participant summary,   │
│ list, search/filter,  │  oldest → newest, the        │  linked task, quick     │
│ activity indicator    │  widest region — this is     │  actions                │
│ per row               │  what the user came for      │                         │
└────────────────────────┴───────────────────────────────┴─────────────────────┘
```

- **Roster** is navigation: "which conversation am I looking at" — today's
  `A2ASwitchboard`/`A2AConversationList`.
- **Stream** is content: "what was said" — today's `A2ATranscript` plus the
  reply composer beneath it. This region gets the majority of horizontal
  space at every breakpoint that shows more than one region, because it is
  the primary surface, not a byproduct of the roster selection.
- **Context** is metadata: participant identity detail, the linked task
  (title, status, a link into `/tasks/{id}`), and any quick actions — a new
  region, collapsible, not present in the current implementation.

### Grid and breakpoints

Extends the existing `grid grid-cols-12 gap-4 lg:gap-6` container
(`a2a/page.tsx:260`) with one more breakpoint tier rather than replacing it:

| Breakpoint | Regions visible | Column split |
|---|---|---|
| `< lg` (mobile/tablet) | One region at a time, drill-in with the existing `ArrowLeft` back button (`a2a/page.tsx:248-258`) | `col-span-12` |
| `lg` – `< xl` | Roster + Stream (today's behavior, unchanged) | Roster `col-span-4`, Stream `col-span-8` |
| `xl`+ | Roster + Stream + Context | Roster `col-span-3`, Stream `col-span-6`, Context `col-span-3` |

The context pane is the new addition and is the one that collapses first —
it never appears below `xl`, and even at `xl`+ it is dismissible via a
header toggle (a `PanelRightClose`/`PanelRightOpen` icon button, `size="sm"
variant="ghost"`, matching the existing switchboard/list toggle buttons at
`a2a/page.tsx:275-296`) so a user who wants the stream at full width above
`xl` can still get it. Collapsed state persists in `localStorage`
(`roboco:conversation-context-open`, boolean), read once at mount — the same
persistence idiom already used for panel-width/theme preferences (avoids a
new state-management dependency).

### Context pane content

When open, the context pane shows, top to bottom:

1. Both participants' identity cards (avatar + name + team badge — see
   §2), each linking to `/agents/{slug}`.
2. The linked task, if any: title (truncated to one line), status `Badge`
   (reusing the same `variant` mapping already used at `a2a/page.tsx:337-344`),
   and a "View task" link.
3. A muted one-line hint when there is no linked task ("This conversation
   has no linked task"), matching the tone of the existing no-task composer
   message (`a2a/page.tsx:373-377`).

The context pane does not duplicate the reply composer or transcript — it
is read-only summary, never a second place to act on the conversation.

---

## 2. Agent identity affordance

### Why team color, not per-agent color

With 22 agents in the roster (`AGENT_UUIDS` in `agent-utils.ts`), a unique
hue per agent is not legible — nobody can hold 22 arbitrary colors in
working memory, and two similar hues (e.g. two blues for `be-dev-1` and
`fe-dev-1`) would read as "the same agent" at a glance. Colour is scoped to
the axis that actually matters for fast scanning — **which cell this agent
belongs to** — and individual identity within a team is carried by the
existing initials/code, not a second hue. This scales cleanly: adding a
23rd agent to an existing team changes zero colors; adding a whole new team
is the only case that needs a new bucket, and the palette below already has
headroom.

### The six buckets

A new pure function, `getAgentTeamColor(agentId: string | null | undefined):
AgentTeamColor`, colocated in `agent-utils.ts` next to `getAgentInitials`
(same module — it needs the same slug-resolution logic already there):

```ts
export type AgentTeamColor =
  | "backend"
  | "frontend"
  | "ux_ui"
  | "board"
  | "ceo"
  | "system";
```

Derived from the slug prefix (`be-*` → `backend`, `fe-*` → `frontend`,
`ux-*` → `ux_ui`, `main-pm`/`product-owner`/`head-marketing`/`auditor` →
`board`, `ceo`/`CEO` → `ceo`, `intake-*`/`secretary-*`/`pr-reviewer-*` →
`system`), with the same UUID-to-slug resolution `getAgentInitials` already
does via `resolveToSlug`.

| Bucket | Agents | Token classes (light / dark handled by existing `dark:` pairs already in the codebase's Tailwind v4 setup) |
|---|---|---|
| `backend` | be-pm, be-dev-1, be-dev-2, be-qa, be-doc | `bg-blue-500/15 border-blue-500/40 text-blue-700 dark:text-blue-400` |
| `frontend` | fe-pm, fe-dev-1, fe-dev-2, fe-qa, fe-doc | `bg-violet-500/15 border-violet-500/40 text-violet-700 dark:text-violet-400` |
| `ux_ui` | ux-pm, ux-dev-1, ux-dev-2, ux-qa, ux-doc | `bg-fuchsia-500/15 border-fuchsia-500/40 text-fuchsia-700 dark:text-fuchsia-400` |
| `board` | main-pm, product-owner, head-marketing, auditor | `bg-amber-500/15 border-amber-500/40 text-amber-700 dark:text-amber-400` |
| `ceo` | ceo | `bg-primary/15 border-primary/40 text-primary` (the app's own accent — the one human gets the app's own color, not a team bucket) |
| `system` | intake-1, secretary-1, pr-reviewer-1 | `bg-slate-500/15 border-slate-500/40 text-slate-700 dark:text-slate-400` |

Every value here is an existing Tailwind color family already used
elsewhere in the codebase for the same semantic weight (`amber` for
attention in `release-proposal-card.tsx:181`, `blue`/`violet`/`fuchsia` are
Tailwind defaults, no new tokens introduced) at the same `/15` background +
`/40` border opacity already established by the pulse-card treatment in
`a2a-pair-card.tsx:87`.

### Avatar composition

Extends the existing avatar circle (`PairAvatar` in `a2a-pair-card.tsx:20-31`,
and the inline avatar in `a2a-transcript.tsx:70-74`) with the team color as
`border` + `bg`, keeping the initials as the foreground content — the color
becomes a ring around identity, not a replacement for it:

```tsx
<div
  className={cn(
    "h-9 w-9 rounded-full border flex items-center justify-center shrink-0",
    TEAM_COLOR_CLASSES[getAgentTeamColor(agentId)],
  )}
  title={getAgentDisplayName(agentId)}
>
  <span className="text-[10px] font-bold tracking-tight">
    {getAgentInitials(agentId)}
  </span>
</div>
```

`TEAM_COLOR_CLASSES` is a `Record<AgentTeamColor, string>` map of the class
strings from the table above, exported alongside `getAgentTeamColor` so
every consumer (transcript rows, pair cards, roster rows, context pane
identity cards) applies the identical mapping — one source of truth, no
per-component re-derivation.

### Accessibility

Color is never the sole differentiator: the `title` attribute always
carries the full display name (already the case in `PairAvatar`), the
initials/code is always visible text inside the circle, and every place an
avatar appears the agent's display name renders as adjacent text (already
true in the transcript and pair card). A screen reader user gets the name
from the text content regardless of the color layer. All six token pairs
above meet WCAG AA (4.5:1) for the `text-*-700`/`text-*-400` foreground
against a `bg-*-500/15` fill over the app's `background`/`card` surface —
verify against the actual rendered surface at implementation time per the
design bar's contrast-audit rule, since a `/15` alpha fill's effective
contrast depends on what's behind it.

---

## 3. Live-stream connection states

### States

`ConnectionState` already has four values (`use-websocket.ts:17-21` via
`lib/websocket/connection.ts`); the spec covers all four, since
`"connecting"` (initial handshake) and `"reconnecting"` (recovering after a
drop) share one visual family with a different label:

| State | Dot | Label | Icon (header, inline) | Placement |
|---|---|---|---|---|
| `connected` | `bg-emerald-500`, static (no pulse) | "Live" | none needed — the dot + label is enough, matching the current `a2a/page.tsx:224-234` treatment minus the `animate-pulse` (see motion note below) | Inline in the pane header, next to the region title |
| `connecting` | `bg-amber-500`, `animate-pulse` | "Connecting…" | `Loader2` with `animate-spin`, `h-3 w-3` (matches `connection-status.tsx:35`) | Inline in the pane header |
| `reconnecting` | `bg-amber-500`, `animate-pulse` | "Reconnecting…" | `Loader2` with `animate-spin`, `h-3 w-3` | Inline in the pane header, **plus** a thin dismissable strip directly above the stream pane's message list: `bg-amber-500/10 border-b border-amber-500/30 text-amber-700 dark:text-amber-400 text-xs px-3 py-1.5` reading "Reconnecting — messages may be out of date" |
| `disconnected` | `bg-muted-foreground/40`, static | "Offline" | `WifiOff`, `h-3 w-3`, `text-muted-foreground` | Inline in the pane header, **plus** the same strip pattern as `reconnecting` but `bg-destructive/10 border-destructive/30 text-destructive`, reading "Disconnected — reconnecting automatically" |

The `connected`/`connecting`/`reconnecting` distinction matters because a
user watching a live conversation needs to know *why* nothing new is
arriving: `connected`-but-quiet means the conversation is genuinely idle;
`reconnecting`/`disconnected` means the stream itself is the problem, not
the conversation. Collapsing all three into one generic "not live" state
(as today's binary `isConnected ? "Live" : "Offline"` does) hides that
distinction.

The banner strip is scoped to the stream pane only, not a full-page
takeover — this is a live-connection hint, not an application-down state
(that's `OfflineState`, reserved for §5's error case where data can't load
at all).

### Motion note

The existing `animate-pulse` dot (`a2a/page.tsx:228`) is a Tailwind
keyframe that only animates `opacity`, so it already satisfies the "animate
transform/opacity only" rule — but it has no `prefers-reduced-motion` guard
today. Add one: wrap the pulsing states in `motion-reduce:animate-none`, so
a reduced-motion user gets a static dot at full opacity instead of the
pulse — the color and label alone still convey the state.

---

## 4. New-message arrival cue

### The cue

When a new message is appended to the stream (a `a2a.message` frame that
resolves to a new row after the existing invalidate-on-frame refetch,
`a2a/page.tsx:131-140`), the new row enters with a **transform + opacity
only** transition — no layout-affecting property, no scroll-listener-driven
animation, per the design bar's motion rule:

```tsx
className={cn(
  "flex gap-3 p-3 rounded-lg border bg-card transition-[opacity,transform] duration-200 ease-out",
  isNew ? "opacity-0 translate-y-1" : "opacity-100 translate-y-0",
)}
```

`isNew` is derived the same render-phase way `A2APairCard`'s `isPulsing`
already is (`a2a-pair-card.tsx:49-60`): compare the incoming message id
against a "last seen" set in render, flip to `false` on the next animation
frame via `requestAnimationFrame` inside a `useEffect` — no animation
library, matching the codebase's existing idiom for this exact kind of
one-shot entrance state.

The starting state (`opacity-0 translate-y-1`, i.e. 4px down) is applied
only for rows that mount already-new (a message arriving while the stream
is open); rows present at initial transcript load render straight to
`opacity-100 translate-y-0` with no transition, so opening a conversation
never shows every existing message animating in at once.

### Off-screen arrival (scrolled up)

When the user has scrolled up in the stream (not at the bottom) and a new
message arrives, do not auto-scroll and do not play the row-entrance
transition off-screen. Instead show a small pill anchored to the bottom of
the stream pane:

```tsx
<button
  className="absolute bottom-3 left-1/2 -translate-x-1/2 rounded-full bg-primary text-primary-foreground text-xs px-3 py-1 shadow-md transition-[opacity,transform] duration-200 ease-out"
  onClick={scrollToBottom}
>
  New messages ↓
</button>
```

— same transform/opacity-only constraint, appearing with the same
fade-and-rise-in treatment as the row cue. Clicking it scrolls to bottom
(smooth, CSS `scroll-behavior: smooth` — a browser-native scroll, not a
`scroll` event listener) and dismisses the pill.

### `prefers-reduced-motion`

Both cues drop the `translate-y`/`-translate-x` transform and the
`duration-200` transition under `motion-reduce:`, leaving only the
`opacity` state change applied instantly (`motion-reduce:transition-none
motion-reduce:translate-y-0`) — the row/pill still visually distinguishes
"just arrived" via a brief `bg-muted/50` background flash (a
non-transform, non-motion cue: a background-color change with its own
short `transition-colors duration-150`, exempt from the transform/opacity
restriction because a plain color transition is not motion) that fades to
the row's normal `bg-card` over 150ms, so reduced-motion users still get
an arrival signal without any movement.

---

## 5. Loading, empty, and error states

All three states are scoped to the stream pane's content area — the pane
chrome (header, region title, connection badge) stays mounted and stable
across state changes, only the message-list area swaps.

| State | Trigger | Treatment |
|---|---|---|
| **Loading** | Initial message fetch in flight (`loadingMessages` in `a2a/page.tsx`, `isLoading` prop already on `A2ATranscript`) | Existing skeleton rows (`A2ATranscript` lines 36-48) — 5 rows, each an avatar-shaped `Skeleton` circle + two text-line skeletons, matching final row shape so there's no layout shift on resolve. No new component needed, this already exists and is correct. |
| **Empty** | Fetch resolved, zero messages (`A2ATranscript` lines 51-60) | Existing centered icon + one-line text (`MessagesSquare`, opacity-50, `text-sm text-muted-foreground`). Extend the copy to be context-aware: "No messages in this conversation yet" when a conversation is selected (current copy, unchanged) vs. "Select a conversation to view messages" when nothing is selected yet (the roster-selected-nothing case, not currently distinguished) — same icon, same layout, only the string changes based on whether `selectedId`/`peekedPair` is set. |
| **Error** | The messages fetch itself errors (distinct from the page-level `isOffline` full-page case at `a2a/page.tsx:239-244`, which covers the *conversations list* failing to load) | A scoped inline state inside the stream pane, not a full-page `OfflineState`: centered `AlertTriangle` icon (`h-8 w-8 opacity-50 text-destructive`), "Couldn't load this conversation" text, and a `Button variant="outline" size="sm"` "Retry" that calls the existing `refetchMessages()`. Reuses the same centered-icon-plus-text layout shell as the empty state (same wrapper `div`, different icon/copy/action) so the three states read as one family, not three unrelated designs. |

The distinction between the page-level `OfflineState` (backend unreachable
entirely, `a2a/page.tsx:239-244`) and this pane-level error state (this one
conversation's message fetch failed, everything else on the page still
works) matters: a transient 500 on one conversation's messages should never
take over the whole page.

---

## Implementation checklist for the frontend developer

- [ ] Add a `context` region to the `/a2a` page's grid at `xl:`, with the
      persisted collapse toggle described in §1.
- [ ] Add `getAgentTeamColor` + `TEAM_COLOR_CLASSES` to `agent-utils.ts`;
      apply to `PairAvatar`, the transcript row avatar, and the new context
      pane identity cards.
- [ ] Extend the connection badge in `a2a/page.tsx` to render all four
      `ConnectionState` values distinctly (§3's table), including the
      dismissable reconnecting/disconnected strip above the stream.
- [ ] Add `motion-reduce:animate-none` to the existing pulsing connection
      dot.
- [ ] Add the transform/opacity new-row entrance transition to
      `A2ATranscript`'s row rendering, plus the "New messages ↓" pill for
      the scrolled-up case, both with `prefers-reduced-motion` fallbacks
      per §4.
- [ ] Split `A2ATranscript`'s empty state into conversation-selected vs.
      nothing-selected copy; add the new scoped error state for a failed
      messages fetch.
- [ ] No new Tailwind tokens beyond the six team-color families named in
      §2 — every other class already exists in `a2a-pair-card.tsx`,
      `a2a-transcript.tsx`, or `release-proposal-card.tsx`.

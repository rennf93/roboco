# WebSocket Reconnect Message-Loss Mitigation

## Problem Statement

The WebSocket connection at `panel/src/lib/websocket/connection.ts` (lines 91–119) has **no server-side message buffering or replay**. When a WebSocket drops and reconnects:

- Any frame published while disconnected is lost forever.
- Consumers have no built-in recovery mechanism; a missing notification or rate-limit update goes unnoticed until the next manual refresh.

This pattern is acceptable for real-time *state* (which is always available via REST), but unacceptable for *events* (e.g., task notifications) that must not be silently dropped.

## Solution: Consumer-Level Reconnect Fallback

Each hook that consumes event streams must implement one of two strategies:

### Strategy 1: REST Catch-Up (Blocking Events)

For hooks that deliver events that cannot be recovered by any other means (e.g., notifications), fetch unread items over REST the moment the socket recovers.

**Used by**: `useNotificationStream`

**Mechanism**:
1. Track whether the socket has ever connected (`everConnectedRef`).
2. On `state === "disconnected"` or `"reconnecting"`, set `hadGapRef.current = true`.
3. When `state === "connected"` AND we've connected before AND we just had a gap:
   - Call `GET /api/notifications?unread_only=true`.
   - Transform the REST response into notification frames.
   - Fold these into the local state.
4. Dedup ensures no notification is double-counted if it arrives both via catch-up and live WS.

**Trade-offs**:
- ✅ Guarantees no event is lost.
- ❌ REST catch-up is a point-in-time snapshot, not a byte-for-byte replay of every frame during the gap.
- ❌ Fetch failure is silently tolerated (live delivery resumes anyway).

### Strategy 2: REST Query Invalidation (State-Heavy Reads)

For hooks that primarily deliver *state* (which is always available via REST), invalidate React Query caches on reconnect so fresh data is fetched.

**Used by**: `useA2ALiveStream` (consumer side), rate-limit banner, usage overview panel

**Mechanism**:
1. Detect WS state change to `connected`.
2. Invalidate relevant React Query cache keys.
3. Components re-fetch fresh state over REST immediately.

**Locations**:
- `a2a/page.tsx:105–126` — Invalidates entire A2A query family on both per-frame and state-change.
- `rate-limit-banner.tsx` — Invalidates usage/rate-limit queries on reconnect.
- `usage-overview-panel.tsx` — Polls or invalidates on reconnect.

**Trade-offs**:
- ✅ Works for any state that's REST-queryable.
- ✅ Guaranteed to be fresh (database truth, not a cached snapshot).
- ❌ Not suitable for true events (things that happen once and are gone).

## Deduplication Strategy (useNotificationStream)

The notification stream uses a **newest-wins dedup** to prevent double-counting when a notification is delivered both via catch-up and live:

```typescript
// Input: [...cachedCatchup, ...liveMessages]
const notifications = useMemo(() => {
  const combined = [...catchup, ...messages];
  const seen = new Set<string>();
  const deduped: NotificationMessage[] = [];
  
  // Walk backward (newest first) and keep only the first (newest) of each id
  for (let i = combined.length - 1; i >= 0; i--) {
    const m = combined[i];
    if (m.type !== "notification") continue;
    const id = m.notification_id;
    if (id) {
      if (seen.has(id)) continue;  // Already seen (newer copy found)
      seen.add(id);
    }
    deduped.push(m);  // Keep oldest/latest unseen id
  }
  
  deduped.reverse();  // Restore arrival order
  return deduped;
}, [messages, catchup]);
```

**Key invariant**: Catch-up frames are placed **before** live frames in the combined array, so live frames never shadow an older cached copy. If a notification arrives live after being cached, the live copy (newer timestamp) is kept because the walk processes newest-first.

## Testing

Comprehensive tests in `panel/src/hooks/__tests__/use-notification-stream.test.tsx` cover:

1. **No fetch on mount**: Initial connection doesn't trigger a catch-up fetch.
2. **Fetch on reconnect**: A disconnect/reconnect cycle triggers `GET /notifications?unread_only=true`.
3. **Dedup on dual-delivery**: A notification caught-up AND received live is shown exactly once.
4. **Clear drops cache**: `clearMessages()` clears both live buffer and cached catch-up.

Run with:
```bash
pnpm test -- use-notification-stream.test.tsx
```

## Audit Results (2026-07-21)

### useNotificationStream
- **Status**: ❌ Defective → ✅ Fixed
- **Defect**: No fallback; notifications published during disconnect were lost.
- **Fix**: REST catch-up on reconnect (Strategy 1).
- **Tests**: Added full regression suite.

### useA2ALiveStream
- **Status**: ✅ Verified working
- **Fallback**: REST query invalidation in `a2a/page.tsx`.
- **Tests**: Existing tests in F083 (part of panel test suite).

### Rate-Limit Banner & Usage Overview
- **Status**: ✅ Verified working
- **Fallback**: REST resync in `rate-limit-banner.tsx` and `usage-overview-panel.tsx`.
- **Tests**: Existing tests in panel test suite.

## When to Add a New Reconnect Fallback

If you're adding a new WS-consuming hook:

1. **Ask**: "Is this event data (can only happen once) or state data (always available via REST)?"
2. **If event**: Implement REST catch-up (Strategy 1).
   - Endpoint: A REST query that returns unread/pending items of that type.
   - On reconnect: Fetch, transform, fold in, dedup.
3. **If state**: Implement query invalidation (Strategy 2).
   - On reconnect: Invalidate React Query cache keys related to this stream.
   - Components will auto-refetch on the next render.
4. **Always**: Write regression tests simulating disconnect/reconnect.

## Further Reading

- **Frontend hooks reference**: `panel/docs/frontend/hooks.md`
- **WebSocket connection**: `panel/src/lib/websocket/connection.ts`
- **Notification stream tests**: `panel/src/hooks/__tests__/use-notification-stream.test.tsx`
- **A2A live view**: `panel/src/app/(dashboard)/a2a/page.tsx`

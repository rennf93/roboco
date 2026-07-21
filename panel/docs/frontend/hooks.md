# Frontend Hooks Reference

This document covers the reusable React hooks exported from `@/hooks` (principally `panel/src/hooks/use-websocket.ts`), with emphasis on the WebSocket message stream patterns and reconnect handling.

## Overview

The panel's real-time coordination streams are built on shared, ref-counted WebSocket connections that fan messages to multiple subscribers. This architecture eliminates duplicate connections to the same endpoint and ensures consistent state across different components that consume the same stream.

### Connection Architecture

- **Shared connections**: Two consumers of the same endpoint (e.g., A2A live view + rate-limit banner both reading `/ws/system`) now share a single WebSocket connection instead of each opening their own.
- **Message fanning**: The shared connection broadcasts incoming messages to all registered subscribers.
- **State syncing**: New subscribers are immediately replayed the connection's current state (e.g., a component mounting mid-reconnection sees `connecting` instead of stale `disconnected`).

### Message Loss & Reconnect Handling

The WebSocket connection at `panel/src/lib/websocket/connection.ts` (lines 91–119) has **no server-side message buffering or replay**. When the socket drops and reconnects:

- Any frame published while disconnected is lost from the WebSocket stream forever.
- Specialized hooks like `useNotificationStream` implement **REST catch-up** to fill the gap: they fetch unread notifications at REST the moment the socket recovers, so no message is silently lost.

This is a point-in-time catch-up strategy, not a byte-for-byte replay — a deliberate trade-off documented in the task acceptance criteria.

---

## `useWebSocket<T>(endpoint, queryParams?, enabled?)`

The foundation hook for subscribing to any WebSocket endpoint. All other hooks (`useNotificationStream`, `useAgentStream`, `useA2ALiveStream`) build on top of it.

### Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `endpoint` | string | — | Path after `/ws/`, e.g., `/notifications/{agentId}` or `/system` |
| `queryParams` | `Record<string, string>` | `undefined` | Optional query string as an object, e.g., `{ viewer_id: "..." }` |
| `enabled` | boolean | `true` | Enable/disable the connection (useful for conditional subscriptions) |

### Return Value

```typescript
{
  state: ConnectionState;           // "disconnected", "connecting", "reconnecting", "connected"
  lastMessage: T | null;            // The most recent message
  messages: T[];                    // Ring buffer of ≤100 messages (STREAM_MAX_MESSAGES)
  disconnect: () => void;           // Manually tear down the connection
  clearMessages: () => void;        // Clear the buffer
  isConnected: boolean;             // Shorthand for state === "connected"
  isConnecting: boolean;            // Shorthand for state === "connecting" || "reconnecting"
}
```

### Example: Raw WebSocket Consumption

```tsx
import { useWebSocket } from "@/hooks";

export function MyAgentMonitor({ agentId }: { agentId: string }) {
  const { state, lastMessage, messages, isConnected } = useWebSocket(
    `/agents/${agentId}`,
    { viewer_id: CEO_AGENT_ID },
    !!agentId  // disable if agentId is falsy
  );

  return (
    <>
      <p>Connection: {state}</p>
      {isConnected && <p>Last update: {lastMessage?.timestamp}</p>}
      <ul>
        {messages.map((msg, i) => (
          <li key={i}>{JSON.stringify(msg)}</li>
        ))}
      </ul>
    </>
  );
}
```

---

## `useNotificationStream()`

Subscribes to **CEO notifications** via `/ws/notifications/{CEO_AGENT_ID}`. This is the only subscription actively using the REST catch-up fallback to guarantee no notification is lost during a reconnect.

### Return Value

```typescript
{
  state: ConnectionState;
  lastMessage: NotificationMessage | null;
  notifications: NotificationMessage[];    // Deduped by notification_id
  allMessages: NotificationMessage[];      // All messages from WS (raw)
  clearMessages: () => void;               // Clears both notifications AND cached REST catch-up batch
  isConnected: boolean;
  isConnecting: boolean;
}
```

### Reconnect Behavior (Key Change)

When the WebSocket reconnects (transitions from `disconnected` / `reconnecting` → `connected`):

1. **On initial connect**: No REST fetch occurs.
2. **On a real reconnect** (socket was up before, dropped, and recovered):
   - A `GET /api/notifications?unread_only=true` fetch fires immediately.
   - Unread notifications from this fetch are transformed into notification frames and held in local state.
   - These cached frames are merged with live frames **before** dedup.
   - The dedup logic ensures no notification appears twice (caught-up notification wins if also delivered live).

3. **Fetch failure**: Silently tolerated — live WS delivery resumes regardless. The catchup is best-effort.

### Deduplication Strategy

The `notifications` array is deduped by `notification_id` using a newest→oldest walk:

- Walk backward through `[...cachedCatchup, ...liveMessages]`.
- Keep only the first occurrence of each unique `notification_id` (newest wins).
- Restore arrival order.
- Notifications without an id (older frames) are always kept.

The cached catch-up batch is placed **ahead** of live frames so live delivery can never be shadowed by an older cached copy.

### Clearing Notifications

Calling `clearMessages()` clears:
- The live message buffer.
- The cached catch-up batch.

This prevents an immediate repopulation from cached state after a user clears the notification badge.

### Example

```tsx
import { useNotificationStream } from "@/hooks";

export function NotificationBell() {
  const { notifications, isConnected, clearMessages } = useNotificationStream();

  return (
    <>
      <button onClick={clearMessages}>
        Clear ({notifications.length})
      </button>
      {!isConnected && <span className="dot" title="offline" />}
    </>
  );
}
```

---

## `useAgentStream(agentId)`

Subscribes to live agent output (streaming work-in-progress) via `/ws/agents/{agentId}`.

### Parameters

| Parameter | Type | Description |
|-----------|------|-------------|
| `agentId` | string \| null | The agent UUID. Pass `null` to disable. |

### Return Value

```typescript
{
  state: ConnectionState;
  lastMessage: AgentStreamMessage | null;
  messages: AgentStreamMessage[];           // Raw frames
  streamChunks: string[];                   // Extracted chunk strings
  streamOutput: string;                     // All chunks concatenated
  clearMessages: () => void;
  isConnected: boolean;
  isConnecting: boolean;
}
```

### Message Type

```typescript
interface AgentStreamMessage {
  type: "connected" | "agent.stream";
  agent_id?: string;
  chunk?: string;                           // Output fragment
  watcher_count?: number;                   // Live viewer count
  timestamp?: string;
}
```

### Example

```tsx
import { useAgentStream } from "@/hooks";

export function AgentOutputPanel({ agentId }: { agentId: string }) {
  const { streamOutput, isConnecting, messages } = useAgentStream(agentId);

  return (
    <div className="output">
      {isConnecting && <em>Connecting…</em>}
      <code>{streamOutput}</code>
      <p className="meta">
        {messages.length} frames • {streamOutput.length} chars
      </p>
    </div>
  );
}
```

---

## `useA2ALiveStream()`

Subscribes to live **agent-to-agent** (A2A) messages via `/ws/system`. The system stream also carries rate-limit and usage events; this hook filters to `a2a.message` frames only.

### Return Value

```typescript
{
  state: ConnectionState;
  lastMessage: A2ASystemMessage | null;
  a2aMessages: A2ASystemMessage[];         // Filtered to type === "a2a.message"
  allMessages: A2ASystemMessage[];         // All frames (includes usage/rate-limit)
  clearMessages: () => void;
  isConnected: boolean;
  isConnecting: boolean;
}
```

### Message Type

```typescript
interface A2ASystemMessage {
  type: "connected" | "a2a.message";
  conversation_id?: string;
  message_id?: string;
  task_id?: string;
  from_agent?: string;
  to_agent?: string;
  skill?: string | null;
  body_excerpt?: string;                   // Capped; fetch full body via REST
  timestamp?: string;
}
```

### Important Note: Excerpt-Only Delivery

A2A frames carry a **capped excerpt**, not the full message body. Consumers must:

1. Listen to `a2a.message` frames.
2. Invalidate their **REST query** for the full message (e.g., `GET /api/a2a/conversations/{id}`).
3. Fetch the full body from the REST endpoint.

Do not render the `body_excerpt` as the message — it is metadata only.

### Reconnect Fallback

The A2A hook already has a working reconnect fallback at the consumer level: `a2a/page.tsx` (lines 105–126) invalidates the entire a2a query family both per-frame and on reconnection, ensuring no message is lost. No change was needed for this hook.

### Example

```tsx
import { useA2ALiveStream } from "@/hooks";
import { useQuery } from "@tanstack/react-query";

export function A2ALiveView() {
  const { a2aMessages, isConnected } = useA2ALiveStream();
  const { data: conversations } = useQuery({
    queryKey: ["a2a", "conversations"],
    // Auto-fetched when a2aMessages changes (invalidation on frame)
  });

  return (
    <div>
      <p>
        {isConnected ? "Connected" : "Offline"}
        {a2aMessages.length > 0 && " (live updates)"}
      </p>
      {/* Render conversations with full bodies from REST */}
    </div>
  );
}
```

---

## `useConnectionStatus()`

Tracks the connection state of **all active subscriptions** in a single hook. Useful for global connection indicators.

### Return Value

```typescript
{
  connections: Record<string, ConnectionState>;  // { [endpoint]: state }
  updateConnection: (id: string, state: ConnectionState) => void;
  removeConnection: (id: string) => void;
  hasActiveConnections: boolean;                 // Any connection is up/ing
  allConnected: boolean;                         // Every connection is connected
}
```

### Example: Global Status Indicator

```tsx
import { useConnectionStatus } from "@/hooks";

export function GlobalConnectionStatus() {
  const { hasActiveConnections, allConnected } = useConnectionStatus();

  return (
    <div className="status-badge">
      {allConnected && <span className="icon-check">Connected</span>}
      {hasActiveConnections && !allConnected && (
        <span className="icon-sync">Reconnecting…</span>
      )}
      {!hasActiveConnections && <span className="icon-offline">Offline</span>}
    </div>
  );
}
```

---

## Testing

The hooks come with comprehensive test coverage in `panel/src/hooks/__tests__/`:

- **`use-websocket.test.tsx`**: Core hook mechanics (shared connection, fan-out, state replay).
- **`use-notification-stream.test.tsx`**: REST catch-up verification (new; covers the reconnect fallback):
  - No fetch on initial connect.
  - Fetch + fold-in on a real reconnect.
  - Dedup prevents double-counting when the same notification arrives both via catch-up and live.
  - `clearMessages()` drops the cached catch-up batch.

Run tests with:

```bash
pnpm test
```

---

## Audited Hooks: Reconnect Coverage

Three hooks were audited for reconnect message-loss risk:

| Hook | Connection Type | Fallback | Status |
|------|-----------------|----------|--------|
| `useNotificationStream` | CEO notification stream | REST catch-up (`GET /notifications?unread_only=true`) | ✅ Hardened |
| `useA2ALiveStream` | A2A + system stream | REST query invalidation (a2a/page.tsx) | ✅ Verified |
| Rate-limit consumers | System stream (`/ws/system`) | REST resync (rate-limit-banner.tsx, usage-overview-panel.tsx) | ✅ Verified |

No defects were found in `useA2ALiveStream` or rate-limit consumption; the REST polling fallbacks were already in place and tested.

---

## Best Practices

1. **Always disable on falsy keys**: Pass a conditional `enabled` flag (third param) if your hook depends on a variable parameter. This prevents spurious connections and ensures cleanup.

   ```tsx
   const { messages } = useWebSocket(
     `/agents/${agentId}`,
     undefined,
     !!agentId  // disable if agentId is null/undefined
   );
   ```

2. **Invalidate REST queries on frame**: When receiving a frame (especially A2A excerpts), trigger a React Query invalidation to fetch fresh data:

   ```tsx
   const queryClient = useQueryClient();
   useEffect(() => {
     if (a2aMessages.length > 0) {
       queryClient.invalidateQueryData({ queryKey: ["a2a"] });
     }
   }, [a2aMessages, queryClient]);
   ```

3. **Don't hold onto stale messages**: The message buffer is bounded to 100 frames. Don't assume it's a complete history — treat it as a stream.

4. **Catch fetch failures gracefully**: All REST fallbacks (like the notification catch-up) are best-effort and fail silently. Live WS delivery is not guaranteed to block on fetch completion.

5. **Use `isConnecting` for UI feedback**: Show a loading state when `isConnecting` is true, not just when `!isConnected`.

   ```tsx
   {isConnecting && <Spinner />}
   {isConnected && <CheckMark />}
   ```

---

## Connection Types & States

### ConnectionState

```typescript
type ConnectionState = 
  | "disconnected"  // Not connected; attempting to reconnect or no connection attempt yet
  | "connecting"    // Initial connection attempt
  | "reconnecting"  // Reconnection after a close (watchdog/network failure)
  | "connected"     // Stable connection, messages flowing
```

### State Transitions

```
disconnected ──> connecting ──> connected
                                    ^
                                    │
                              (watchdog fires)
                                    │
                          reconnecting ──┘
```

On a reconnect transition (`reconnecting` → `connected`), hooks like `useNotificationStream` fire their REST catch-up fetch.

---

## Further Reading

- **Control panel README**: `panel/README.md`
- **WebSocket connection implementation**: `panel/src/lib/websocket/connection.ts`
- **API client**: `panel/src/lib/api/`
- **Notification types & components**: `panel/src/app/(dashboard)/notifications/`

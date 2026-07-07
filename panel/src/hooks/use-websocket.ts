"use client";

import { useEffect, useRef, useState, useCallback, useMemo } from "react";
import {
  WebSocketConnection,
  getWebSocketUrl,
} from "@/lib/websocket/connection";
import { CEO_AGENT_ID, STREAM_MAX_MESSAGES } from "@/lib/constants";

// Re-export ConnectionState type
export type { ConnectionState } from "@/lib/websocket/connection";
import type { ConnectionState } from "@/lib/websocket/connection";

// =============================================================================
// Types
// =============================================================================

export interface AgentStreamMessage {
  type: "connected" | "agent.stream";
  agent_id: string;
  chunk?: string;
  watcher_count?: number;
  timestamp?: string;
}

export interface NotificationMessage {
  type: "connected" | "notification";
  agent_id?: string;
  notification_id?: string;
  notification_type?: string;
  subject?: string;
  priority?: string;
  timestamp?: string;
}

export interface A2ASystemMessage {
  type: "connected" | "a2a.message";
  conversation_id?: string;
  message_id?: string;
  task_id?: string;
  from_agent?: string;
  to_agent?: string;
  skill?: string | null;
  body_excerpt?: string;
  timestamp?: string;
}

// =============================================================================
// Generic WebSocket Hook
// =============================================================================

// C4: ref-counted shared connection per URL. Two consumers of /ws/system (the
// A2A live view + the rate-limit banner) used to each open their own socket;
// now they share one. The shared conn fans out messages + state to a Set of
// subscribers. Module-level so separate trees share the same registry.
interface SharedConn {
  conn: WebSocketConnection;
  subscribers: Set<{
    onMessage: (data: unknown) => void;
    onStateChange: (state: ConnectionState) => void;
  }>;
}
const _sharedSockets = new Map<string, SharedConn>();

// Test-only: clear the module-level registry between tests so a prior test's
// un-unmounted socket doesn't leak into the next. No-op in production.
export function _resetSharedSocketsForTest() {
  for (const entry of _sharedSockets.values()) entry.conn.disconnect();
  _sharedSockets.clear();
}

function _dispatchMessage(url: string, data: unknown) {
  const entry = _sharedSockets.get(url);
  if (!entry) return;
  for (const sub of entry.subscribers) sub.onMessage(data);
}

function _dispatchState(url: string, state: ConnectionState) {
  const entry = _sharedSockets.get(url);
  if (!entry) return;
  for (const sub of entry.subscribers) sub.onStateChange(state);
}

export function useWebSocket<T>(
  endpoint: string,
  queryParams?: Record<string, string>,
  enabled: boolean = true,
) {
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<T | null>(null);
  const [messages, setMessages] = useState<T[]>([]);
  const connectionRef = useRef<WebSocketConnection | null>(null);

  // Memoize queryParams string to prevent unnecessary reconnects
  const queryString = queryParams
    ? new URLSearchParams(queryParams).toString()
    : "";

  useEffect(() => {
    // Don't connect if disabled or no endpoint
    if (!enabled || !endpoint) {
      return;
    }

    // Build URL
    const baseUrl = getWebSocketUrl();
    const url = baseUrl + endpoint + (queryString ? "?" + queryString : "");

    // Subscriber for this mount — its callbacks write THIS hook's React state.
    const subscriber = {
      onMessage: (data: unknown) => {
        const message = data as T;
        setLastMessage(message);
        setMessages((prev) => [
          ...prev.slice(-(STREAM_MAX_MESSAGES - 1)),
          message,
        ]);
      },
      onStateChange: setState,
    };

    let entry = _sharedSockets.get(url);
    if (entry) {
      // Reuse: attach to the existing conn's fan-out. Replay current state so
      // the new subscriber's UI doesn't sit on "disconnected" until the next
      // state change. Route through the subscriber callback (not setState
      // directly) — same path the conn's onStateChange uses, so the new
      // subscriber mirrors the existing subscribers' current view.
      entry.subscribers.add(subscriber);
      subscriber.onStateChange(entry.conn.getState());
    } else {
      // First subscriber for this URL — open the shared conn with fan-out
      // dispatchers that iterate the subscriber set.
      const conn = new WebSocketConnection({
        url,
        onMessage: (data) => _dispatchMessage(url, data),
        onStateChange: (s) => _dispatchState(url, s),
      });
      entry = { conn, subscribers: new Set([subscriber]) };
      _sharedSockets.set(url, entry);
      conn.connect();
    }
    connectionRef.current = entry.conn;

    // Cleanup on unmount or when dependencies change. Decrement the refcount;
    // only disconnect + drop the registry entry when the last subscriber
    // leaves. Always clear THIS subscriber's local snapshot (#79) so a dep
    // change can't surface another stream's stale buffer as live.
    return () => {
      const current = _sharedSockets.get(url);
      if (current) {
        current.subscribers.delete(subscriber);
        if (current.subscribers.size === 0) {
          current.conn.disconnect();
          _sharedSockets.delete(url);
        }
      }
      connectionRef.current = null;
      setMessages([]);
      setLastMessage(null);
      setState("disconnected");
    };
  }, [enabled, endpoint, queryString]); // Stable dependencies

  const disconnect = useCallback(() => {
    // ponytail: forced teardown of the shared conn. Rare (no current caller
    // exercises it — unmount goes through the effect-cleanup refcount path).
    // Tears down the socket for ALL subscribers of this URL; that's the
    // correct semantic for a manual "kill this stream" verb.
    connectionRef.current?.disconnect();
    connectionRef.current = null;
    setState("disconnected");
  }, []);

  const clearMessages = useCallback(() => {
    setMessages([]);
    setLastMessage(null);
  }, []);

  return {
    state,
    lastMessage,
    messages,
    disconnect,
    clearMessages,
    isConnected: state === "connected",
    isConnecting: state === "connecting" || state === "reconnecting",
  };
}

// =============================================================================
// Specialized Hooks
// =============================================================================

/**
 * Subscribe to an agent's output stream
 */
export function useAgentStream(agentId: string | null) {
  const {
    state,
    lastMessage,
    messages,
    clearMessages,
    isConnected,
    isConnecting,
  } = useWebSocket<AgentStreamMessage>(
    agentId ? "/agents/" + agentId : "",
    { viewer_id: CEO_AGENT_ID },
    !!agentId,
  );

  // Extract stream chunks
  const streamChunks = messages
    .filter((m) => m.type === "agent.stream" && m.chunk)
    .map((m) => m.chunk as string);

  // Combine chunks into full output
  const streamOutput = streamChunks.join("");

  return {
    state,
    lastMessage,
    messages,
    streamChunks,
    streamOutput,
    clearMessages,
    isConnected,
    isConnecting,
  };
}

/**
 * Subscribe to notifications for the CEO
 */
export function useNotificationStream() {
  const {
    state,
    lastMessage,
    messages,
    clearMessages,
    isConnected,
    isConnecting,
  } = useWebSocket<NotificationMessage>(
    "/notifications/" + CEO_AGENT_ID,
    undefined,
    true,
  );

  // Filter to notification events, de-duplicated by notification_id so a
  // stream replay (e.g. after a websocket reconnect) does not surface — or
  // count — the same notification twice. Walk newest→oldest keeping the most
  // recent copy of each id, then restore arrival order. Events without an id
  // (older payloads) are always kept.
  const notifications = useMemo(() => {
    const seen = new Set<string>();
    const deduped: NotificationMessage[] = [];
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.type !== "notification") continue;
      const id = m.notification_id;
      if (id) {
        if (seen.has(id)) continue;
        seen.add(id);
      }
      deduped.push(m);
    }
    deduped.reverse();
    return deduped;
  }, [messages]);

  return {
    state,
    lastMessage,
    notifications,
    allMessages: messages,
    clearMessages,
    isConnected,
    isConnecting,
  };
}

/**
 * Subscribe to live A2A traffic on the operator stream (`/ws/system`).
 *
 * The bridge publishes an `a2a.message` frame for every persisted
 * agent<->agent chat message. Frames carry a capped `body_excerpt` only —
 * consumers invalidate their REST queries for full bodies (the A2A live view
 * idiom), never render the excerpt as the message.
 */
export function useA2ALiveStream() {
  const {
    state,
    lastMessage,
    messages,
    clearMessages,
    isConnected,
    isConnecting,
  } = useWebSocket<A2ASystemMessage>("/system", undefined, true);

  // Filter to A2A frames (the system stream also carries rate-limit/usage).
  const a2aMessages = messages.filter((m) => m.type === "a2a.message");

  return {
    state,
    lastMessage,
    a2aMessages,
    allMessages: messages,
    clearMessages,
    isConnected,
    isConnecting,
  };
}

// =============================================================================
// Connection Status Hook (for UI indicators)
// =============================================================================

export function useConnectionStatus() {
  const [connections, setConnections] = useState<
    Record<string, ConnectionState>
  >({});

  const updateConnection = useCallback((id: string, state: ConnectionState) => {
    setConnections((prev) => ({ ...prev, [id]: state }));
  }, []);

  const removeConnection = useCallback((id: string) => {
    setConnections((prev) => {
      const next = { ...prev };
      delete next[id];
      return next;
    });
  }, []);

  const hasActiveConnections = Object.values(connections).some(
    (s) => s === "connected" || s === "connecting" || s === "reconnecting",
  );

  const allConnected = Object.values(connections).every(
    (s) => s === "connected",
  );

  return {
    connections,
    updateConnection,
    removeConnection,
    hasActiveConnections,
    allConnected,
  };
}

"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { 
  WebSocketConnection, 
  getWebSocketUrl 
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

export interface ChannelMessage {
  type: "connected" | "message.new" | "session.closed";
  channel_id?: string;
  message_id?: string;
  agent_id?: string;
  content?: string;
  message_type?: string;
  subscriber_count?: number;
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

// =============================================================================
// Generic WebSocket Hook
// =============================================================================

export function useWebSocket<T>(
  endpoint: string,
  queryParams?: Record<string, string>,
  enabled: boolean = true
) {
  const [state, setState] = useState<ConnectionState>("disconnected");
  const [lastMessage, setLastMessage] = useState<T | null>(null);
  const [messages, setMessages] = useState<T[]>([]);
  const connectionRef = useRef<WebSocketConnection | null>(null);

  // Memoize queryParams string to prevent unnecessary reconnects
  const queryString = queryParams ? new URLSearchParams(queryParams).toString() : "";

  useEffect(() => {
    // Don't connect if disabled or no endpoint
    if (!enabled || !endpoint) {
      return;
    }

    // Build URL
    const baseUrl = getWebSocketUrl();
    const url = baseUrl + endpoint + (queryString ? "?" + queryString : "");

    // Create connection
    const connection = new WebSocketConnection({
      url,
      onMessage: (data) => {
        const message = data as T;
        setLastMessage(message);
        setMessages((prev) => [...prev.slice(-(STREAM_MAX_MESSAGES - 1)), message]);
      },
      onStateChange: setState,
    });

    connectionRef.current = connection;
    connection.connect();

    // Cleanup on unmount or when dependencies change
    return () => {
      connection.disconnect();
      connectionRef.current = null;
    };
  }, [enabled, endpoint, queryString]); // Stable dependencies

  const disconnect = useCallback(() => {
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
  const { state, lastMessage, messages, clearMessages, isConnected, isConnecting } = 
    useWebSocket<AgentStreamMessage>(
      agentId ? "/agents/" + agentId : "",
      { viewer_id: CEO_AGENT_ID },
      !!agentId
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
 * Subscribe to a channel's message stream
 */
export function useChannelStream(channelId: string | null) {
  const { state, lastMessage, messages, clearMessages, isConnected, isConnecting } = 
    useWebSocket<ChannelMessage>(
      channelId ? "/channels/" + channelId : "",
      { agent_id: CEO_AGENT_ID },
      !!channelId
    );

  // Filter to only actual messages
  const channelMessages = messages.filter((m) => m.type === "message.new");

  return {
    state,
    lastMessage,
    channelMessages,
    allMessages: messages,
    clearMessages,
    isConnected,
    isConnecting,
  };
}

/**
 * Subscribe to notifications for the CEO
 */
export function useNotificationStream() {
  const { state, lastMessage, messages, clearMessages, isConnected, isConnecting } = 
    useWebSocket<NotificationMessage>(
      "/notifications/" + CEO_AGENT_ID,
      undefined,
      true
    );

  // Filter to only notification events
  const notifications = messages.filter((m) => m.type === "notification");

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

// =============================================================================
// Connection Status Hook (for UI indicators)
// =============================================================================

export function useConnectionStatus() {
  const [connections, setConnections] = useState<Record<string, ConnectionState>>({});

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
    (s) => s === "connected" || s === "connecting" || s === "reconnecting"
  );

  const allConnected = Object.values(connections).every((s) => s === "connected");

  return {
    connections,
    updateConnection,
    removeConnection,
    hasActiveConnections,
    allConnected,
  };
}

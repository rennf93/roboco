"use client";

import { useEffect, useRef } from "react";
import { useWebSocket } from "./use-websocket";
import { useRateLimitStore } from "@/store/rate-limit-store";
import type { RateLimitHitEvent, RateLimitLiftedEvent } from "@/types/rate-limits";

interface RateLimitWsMessage {
  type: string;
  provider?: string;
  affectedAgents?: string[];
  retryAfterSeconds?: number;
  timestamp?: string;
}

interface UseRateLimitWebSocketOptions {
  /** Called when the WebSocket reconnects after a disconnect */
  onReconnect?: () => void;
}

/**
 * Subscribes to RATE_LIMIT_HIT and RATE_LIMIT_LIFTED WebSocket events and
 * dispatches them to the useRateLimitStore. Accepts an optional onReconnect
 * callback that fires when the connection recovers from a reconnecting state.
 */
export function useRateLimitWebSocket(options: UseRateLimitWebSocketOptions = {}) {
  const { onReconnect } = options;
  const prevStateRef = useRef<string | null>(null);

  // getWebSocketUrl() already supplies the "/ws" base, so the endpoint is just
  // the path (matching the agents/channels/notifications hooks). Passing
  // "/ws/system" here produced the doubled "/ws/ws/system" URL.
  const { state, lastMessage } = useWebSocket<RateLimitWsMessage>(
    "/system",
    undefined,
    true
  );

  // Fire onReconnect when state transitions from reconnecting → connected
  useEffect(() => {
    if (prevStateRef.current === "reconnecting" && state === "connected") {
      onReconnect?.();
    }
    prevStateRef.current = state;
  }, [state, onReconnect]);

  // Handle incoming WS messages
  useEffect(() => {
    if (!lastMessage) return;

    const { hitRateLimit, liftRateLimit } = useRateLimitStore.getState();

    if (lastMessage.type === "RATE_LIMIT_HIT") {
      const event: RateLimitHitEvent = {
        type: "RATE_LIMIT_HIT",
        provider: lastMessage.provider ?? "unknown",
        affectedAgents: lastMessage.affectedAgents ?? [],
        retryAfterSeconds: lastMessage.retryAfterSeconds ?? 60,
        timestamp: lastMessage.timestamp ?? new Date().toISOString(),
      };
      hitRateLimit(event);
    } else if (lastMessage.type === "RATE_LIMIT_LIFTED") {
      const event: RateLimitLiftedEvent = {
        type: "RATE_LIMIT_LIFTED",
        provider: lastMessage.provider ?? "unknown",
        timestamp: lastMessage.timestamp ?? new Date().toISOString(),
      };
      liftRateLimit(event);
    }
  }, [lastMessage]);

  return { wsState: state };
}

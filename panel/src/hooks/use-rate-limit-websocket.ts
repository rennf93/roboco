"use client";

import { useEffect, useRef } from "react";
import { useWebSocket } from "./use-websocket";
import { useRateLimitStore } from "@/store/rate-limit-store";
import { useUsageStore } from "@/store/usage-store";
import type { RateLimitHitEvent, RateLimitLiftedEvent } from "@/types/rate-limits";

/**
 * Unified shape for all messages arriving on the /ws/system endpoint.
 * Fields are optional because different message types use different subsets.
 */
interface SystemWsMessage {
  type: string;
  // Rate-limit fields (RATE_LIMIT_HIT / RATE_LIMIT_LIFTED)
  provider?: string;
  affectedAgents?: string[];
  retryAfterSeconds?: number;
  // Usage fields (USAGE_UPDATE / USAGE_SNAPSHOT)
  key_metrics?: Record<string, unknown>;
  // Shared
  timestamp?: string;
}

interface UseRateLimitWebSocketOptions {
  /** Called when the WebSocket reconnects after a disconnect */
  onReconnect?: () => void;
}

/**
 * Subscribes to the /ws/system WebSocket (single shared instance mounted in
 * RateLimitBanner). Handles:
 *
 *  - RATE_LIMIT_HIT / RATE_LIMIT_LIFTED → dispatched to useRateLimitStore
 *  - USAGE_UPDATE / USAGE_SNAPSHOT     → dispatched to useUsageStore
 *
 * Also syncs the live WebSocket connection state into useUsageStore so that
 * other components (e.g. CommandCenter, KeyMetricsPanel) can read it without
 * creating a second /ws/system connection.
 *
 * Accepts an optional onReconnect callback that fires when the connection
 * recovers from a reconnecting state.
 */
export function useRateLimitWebSocket(options: UseRateLimitWebSocketOptions = {}) {
  const { onReconnect } = options;
  const prevStateRef = useRef<string | null>(null);

  // getWebSocketUrl() already supplies the "/ws" base, so the endpoint is just
  // the path (matching the agents/channels/notifications hooks). Passing
  // "/ws/system" here produced the doubled "/ws/ws/system" URL.
  const { state, lastMessage } = useWebSocket<SystemWsMessage>(
    "/system",
    undefined,
    true
  );

  // Sync WS connection state into useUsageStore for cross-component visibility.
  // This is the ONLY place wsState is written; no second useWebSocket call is needed.
  useEffect(() => {
    useUsageStore.getState().setWsState(state);
  }, [state]);

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
    const { setUsageData } = useUsageStore.getState();

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
    } else if (
      lastMessage.type === "USAGE_UPDATE" ||
      lastMessage.type === "USAGE_SNAPSHOT"
    ) {
      setUsageData({
        key_metrics: lastMessage.key_metrics ?? {},
        timestamp: lastMessage.timestamp,
      });
    }
  }, [lastMessage]);

  return { wsState: state };
}

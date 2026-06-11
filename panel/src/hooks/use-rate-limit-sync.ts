"use client";

import { useEffect, useCallback } from "react";
import { rateLimitsApi } from "@/lib/api/rate-limits";
import { useRateLimitStore } from "@/store/rate-limit-store";

/**
 * Calls GET /api/system/rate-limits on mount and passes results to syncFromApi.
 * Is a no-op (with console.warn) when the endpoint is unavailable.
 * Also exposes a sync() function that can be called on WS reconnect.
 */
export function useRateLimitSync() {
  const { syncFromApi } = useRateLimitStore();

  const sync = useCallback(async () => {
    try {
      const response = await rateLimitsApi.getRateLimits();
      syncFromApi(response);
    } catch (err) {
      // Endpoint unavailable — treat as no-op per acceptance criteria
      const status = (err as { response?: { status?: number } })?.response?.status;
      if (status === 404) {
        console.warn("[rate-limits] GET /api/system/rate-limits returned 404 — endpoint not available");
      } else {
        console.warn("[rate-limits] GET /api/system/rate-limits unavailable:", err);
      }
    }
  }, [syncFromApi]);

  // Sync on mount
  useEffect(() => {
    void sync();
  }, [sync]);

  return { sync };
}

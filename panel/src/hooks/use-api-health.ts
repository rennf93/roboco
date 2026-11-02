"use client";

import { useEffect, useState } from "react";

export type ApiHealthState = "checking" | "connected" | "disconnected";

const HEALTH_POLL_INTERVAL_MS = 30000;
const HEALTH_TIMEOUT_MS = 5000;

/**
 * Whether the orchestrator API answers its health check: "checking" until
 * the first probe settles, then re-probed every 30s. A non-2xx answer, a
 * network error, and a 5s timeout all read as "disconnected".
 */
export function useApiHealth(): ApiHealthState {
  const [state, setState] = useState<ApiHealthState>("checking");

  useEffect(() => {
    const checkConnection = async () => {
      try {
        // Health endpoint is at /health (not under /api)
        const response = await fetch("/health", {
          method: "GET",
          signal: AbortSignal.timeout(HEALTH_TIMEOUT_MS),
        });
        setState(response.ok ? "connected" : "disconnected");
      } catch {
        setState("disconnected");
      }
    };

    checkConnection();
    const interval = setInterval(checkConnection, HEALTH_POLL_INTERVAL_MS);

    return () => clearInterval(interval);
  }, []);

  return state;
}

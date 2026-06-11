"use client";

import { useEffect, useState, useCallback } from "react";
import { AlertTriangle } from "lucide-react";
import { useRateLimitStore } from "@/store/rate-limit-store";
import { useRateLimitSync } from "@/hooks/use-rate-limit-sync";
import { useRateLimitWebSocket } from "@/hooks/use-rate-limit-websocket";
import type { RateLimitEntry } from "@/types/rate-limits";

// =============================================================================
// Countdown row for a single rate-limited provider
// =============================================================================

function RateLimitRow({ entry }: { entry: RateLimitEntry }) {
  const [secondsLeft, setSecondsLeft] = useState<number>(() => {
    const remaining = Math.ceil(
      (new Date(entry.resumeAt).getTime() - Date.now()) / 1000
    );
    return Math.max(0, remaining);
  });

  useEffect(() => {
    if (secondsLeft <= 0) return;

    const id = setInterval(() => {
      setSecondsLeft((prev) => {
        const next = prev - 1;
        if (next <= 0) {
          clearInterval(id);
          return 0;
        }
        return next;
      });
    }, 1000);

    return () => clearInterval(id);
  }, [secondsLeft]);

  const agentCount = entry.affectedAgents.length;

  return (
    <div className="flex items-center gap-3 px-4 py-2 bg-amber-50 border-b border-amber-300 last:border-b-0">
      <AlertTriangle className="h-4 w-4 text-amber-600 shrink-0" />
      <span className="text-sm font-medium text-amber-900">
        {entry.provider}
      </span>
      {agentCount > 0 && (
        <span className="text-sm text-amber-700">
          {agentCount} agent{agentCount !== 1 ? "s" : ""} affected
        </span>
      )}
      <span className="text-sm text-amber-700">
        {secondsLeft}s
      </span>
      <span className="text-sm text-amber-800 font-medium ml-auto">
        operations paused — resuming automatically
      </span>
    </div>
  );
}

// =============================================================================
// Main banner component
// =============================================================================

export function RateLimitBanner() {
  const limits = useRateLimitStore((state) => state.limits);

  // Sync hook — calls GET /api/system/rate-limits on mount and exposes sync()
  const { sync } = useRateLimitSync();

  // Called when the WS reconnects — re-sync state from API
  const handleReconnect = useCallback(() => {
    void sync();
  }, [sync]);

  // WS hook — subscribes to RATE_LIMIT_HIT / RATE_LIMIT_LIFTED events
  useRateLimitWebSocket({ onReconnect: handleReconnect });

  // Nothing to show when no providers are rate-limited
  if (limits.size === 0) {
    return null;
  }

  const entries = Array.from(limits.values());

  return (
    <div
      className="border-b border-amber-300 bg-amber-50"
      role="alert"
      aria-live="polite"
      aria-label="Rate limit notifications"
    >
      {entries.map((entry) => (
        <RateLimitRow key={entry.provider} entry={entry} />
      ))}
    </div>
  );
}

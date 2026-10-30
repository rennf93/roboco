"use client";

import { TriangleAlert } from "lucide-react";
import { useDecisionsStatus } from "@/hooks/use-providers";

/**
 * Small amber warning banner for the AI Providers screen (mirrors the
 * RateLimitBanner / MaintenanceBanner row idiom: a full-width amber strip
 * with an icon + one line of copy). Renders NOTHING unless the Decisions
 * OpenRouter fallback tier is opted in but unauthenticated - the correct
 * empty state, so an operator who never touched Decisions sees nothing.
 *
 * The failure it surfaces is a silent one server-side: with the tier opted in
 * and no key stored, every fallback call fails closed at call time, which
 * looks like "Decisions is broken" when it is really "Decisions is
 * unconfigured". The key itself is never displayed (the status endpoint
 * returns booleans only); the fix lives in the OpenRouter key row below.
 */
export function DecisionsStatusBanner() {
  const { data } = useDecisionsStatus();

  if (!data) return null;
  const { decisions_enabled, openrouter_opted_in, openrouter_key_present } =
    data;
  if (!openrouter_opted_in || openrouter_key_present) return null;

  return (
    <div
      className="flex items-center gap-3 px-4 py-2 bg-amber-100 border border-amber-400 rounded-md dark:bg-amber-950/60 dark:border-amber-800"
      role="alert"
      aria-label="Decisions OpenRouter fallback tier missing API key"
    >
      <TriangleAlert
        className="h-4 w-4 text-amber-700 dark:text-amber-400 shrink-0"
        aria-hidden="true"
      />
      <span className="text-sm text-amber-900 dark:text-amber-200">
        Decisions: the OpenRouter fallback tier is opted in but no OpenRouter
        API key is configured. Save your key in the OpenRouter row below or the
        tier fails closed at call time.
        {!decisions_enabled &&
          " The Decisions master flag is currently off, so nothing calls this tier yet."}
      </span>
    </div>
  );
}

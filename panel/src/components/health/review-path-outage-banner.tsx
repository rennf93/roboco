"use client";

import { TriangleAlert } from "lucide-react";
import { formatAbsoluteTimestamp } from "@/lib/utils";
import { useReviewPathOutage } from "@/hooks/use-review-path-outage";

/**
 * Read-only banner for an active review-path tool outage (evidence /
 * git-readonly reads failing across tasks). Rendered while the backend
 * reports the outage active; it disappears on recovery. Surfacing only —
 * remediation is a PM/CEO decision, so there are deliberately no actions
 * here. Quiet when the backend health surface hasn't landed yet.
 */
export function ReviewPathOutageBanner() {
  const outage = useReviewPathOutage();

  if (!outage?.active) {
    return null;
  }

  return (
    <div
      className="border-b border-red-300 bg-red-50 dark:border-red-800 dark:bg-red-950"
      role="alert"
      aria-live="polite"
      aria-label="Review-path tool outage"
    >
      <div className="flex items-center gap-3 px-4 py-2">
        <TriangleAlert className="h-4 w-4 text-red-600 dark:text-red-400 shrink-0" />
        <span className="text-sm font-medium text-red-900 dark:text-red-200">
          Review-path tools outage
        </span>
        {outage.window_start && (
          <span className="text-sm text-red-700 dark:text-red-300">
            since {formatAbsoluteTimestamp(outage.window_start)}
          </span>
        )}
        <span className="text-sm text-red-800 dark:text-red-200 font-medium ml-auto">
          evidence and git reads may fail — reviews resume automatically on
          recovery
        </span>
      </div>
    </div>
  );
}

"use client";

import { Loader2, CheckCircle2, PenLine } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/**
 * Visual states for the task draft panel.
 *
 * Two states are required so QA can verify they are distinguishable
 * without relying on color alone — each state uses a distinct icon + label.
 *
 *  - "still-refining":        amber border + spinner icon + "Refining…" label
 *  - "draft-ready-for-review": green  border + check  icon + "Ready for Review" label
 */
export type DraftState = "still-refining" | "draft-ready-for-review";

interface DraftStatusBadgeProps {
  state: DraftState;
  className?: string;
}

export function DraftStatusBadge({ state, className }: DraftStatusBadgeProps) {
  if (state === "still-refining") {
    return (
      <Badge
        className={cn(
          // Amber border + muted background — distinguishable by shape/icon even in greyscale
          "border-amber-500 bg-amber-50 text-amber-700 dark:bg-amber-950 dark:text-amber-300",
          className
        )}
        variant="outline"
        aria-label="Draft is still being refined"
      >
        {/* Spinning icon provides motion cue independent of color */}
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
        <PenLine className="h-3 w-3" aria-hidden="true" />
        Refining…
      </Badge>
    );
  }

  return (
    <Badge
      className={cn(
        // Green border + muted background — check icon distinguishes from spinner above
        "border-green-600 bg-green-50 text-green-700 dark:bg-green-950 dark:text-green-300",
        className
      )}
      variant="outline"
      aria-label="Draft is ready for review"
    >
      {/* Static check icon — no motion, clearly different from the spinner */}
      <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
      Ready for Review
    </Badge>
  );
}

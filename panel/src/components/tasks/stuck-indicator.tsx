"use client";

import { Hourglass } from "lucide-react";

import {
  formatActiveTime,
  useStuckState,
} from "@/hooks/use-stuck-state";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Task } from "@/types";

/**
 * Read-only stuck indicator driven by the wedge ledger: active time since the
 * last progress-fingerprint movement plus the recorded actor/verb cycle.
 * Renders nothing on a healthy task. Surfacing only — remediation decisions
 * stay with the PM/CEO, so there are deliberately no actions here.
 */
export function StuckIndicator({ task }: { task: Task }) {
  const { isStuck, activeTimeSinceProgress, wedgeCycle, reasons } =
    useStuckState(task);

  if (!isStuck) return null;

  const activeTime = formatActiveTime(activeTimeSinceProgress);

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          data-testid="stuck-indicator"
          className="inline-flex h-7 shrink-0 cursor-default items-center gap-1 rounded-md border border-red-300 bg-red-100 px-2 text-xs font-medium text-red-800 dark:border-red-800 dark:bg-red-900 dark:text-red-300"
        >
          <Hourglass className="h-3 w-3" />
          stuck
          {activeTime ? ` · ${activeTime} since progress` : ""}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        <p>
          {reasons.join(" · ")}
          {activeTime ? ` — active time since last progress movement: ${activeTime}` : ""}
          .
        </p>
        {wedgeCycle.length > 0 && (
          <div className="mt-1 space-y-0.5">
            <p className="font-medium">Wedge cycle:</p>
            {wedgeCycle.map((entry, i) => (
              <p key={i} className="text-xs">
                {entry.actor} → {entry.verb} · {entry.timestamp}
              </p>
            ))}
          </div>
        )}
        <p className="mt-1 text-xs opacity-80">
          Read-only — resolution is a PM/CEO decision.
        </p>
      </TooltipContent>
    </Tooltip>
  );
}

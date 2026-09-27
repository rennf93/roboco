"use client";

import { TaskStatus, type Task, type WedgeCycleEntry } from "@/types";

export interface StuckState {
  /** True when the task carries open wedge strikes OR is blocked for a human. */
  isStuck: boolean;
  /** Active time since the last progress-fingerprint movement (contract field). */
  activeTimeSinceProgress: string | number | null;
  openWedgeStrikes: number;
  wedgeCycle: WedgeCycleEntry[];
  /** Why the task is stuck — drives the indicator's tooltip. */
  reasons: string[];
}

// "Blocked for human" = the dispatcher must skip this task because only a
// human can resolve it (blocker_resolver_type="human" on a blocked status).
export function isTaskBlockedForHuman(task: Task): boolean {
  return (
    task.status === TaskStatus.BLOCKED && task.blocker_resolver_type === "human"
  );
}

// Pure stuck-state derivation from the task payload's wedge-ledger fields.
// Read-only by design: this surfaces state for the PM/CEO, it never acts.
export function deriveStuckState(task: Task | null | undefined): StuckState {
  const openWedgeStrikes = task?.open_wedge_strikes ?? 0;
  const wedgeCycle = task?.wedge_cycle ?? [];
  const activeTimeSinceProgress = task?.active_time_since_progress ?? null;
  const blockedForHuman = task ? isTaskBlockedForHuman(task) : false;

  const reasons: string[] = [];
  if (openWedgeStrikes > 0) {
    reasons.push(
      `${openWedgeStrikes} open wedge strike${openWedgeStrikes === 1 ? "" : "s"}`,
    );
  }
  if (blockedForHuman) {
    reasons.push("blocked for human resolution");
  }

  return {
    isStuck: openWedgeStrikes > 0 || blockedForHuman,
    activeTimeSinceProgress,
    openWedgeStrikes,
    wedgeCycle,
    reasons,
  };
}

// Active time since last progress movement. The backend may send seconds
// (number) or a pre-formatted string; display strings verbatim, format
// seconds as a coarse "1d 2h 3m" clock.
export function formatActiveTime(
  value: string | number | null | undefined,
): string | null {
  if (value == null || value === "") return null;
  if (typeof value === "string") return value;
  let seconds = Math.max(0, Math.floor(value));
  const days = Math.floor(seconds / 86400);
  seconds -= days * 86400;
  const hours = Math.floor(seconds / 3600);
  seconds -= hours * 3600;
  const minutes = Math.floor(seconds / 60);
  seconds -= minutes * 60;
  const parts: string[] = [];
  if (days) parts.push(`${days}d`);
  if (hours) parts.push(`${hours}h`);
  if (minutes) parts.push(`${minutes}m`);
  if (!parts.length) parts.push(`${seconds}s`);
  return parts.slice(0, 2).join(" ");
}

/**
 * Stuck state for a task payload already fetched by the list/detail queries —
 * no second fetch. Feeds the read-only StuckIndicator on cards and detail.
 */
export function useStuckState(task: Task | null | undefined): StuckState {
  return deriveStuckState(task);
}

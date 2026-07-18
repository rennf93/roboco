"use client";

import { MobileTaskBoard } from "@/components/tasks/mobile-task-board";

/** Cockpit Board tab — thin wrapper so every tab has its own file under
 * components/tg/ (per the per-tab-file convention); the board itself lives
 * in components/tasks since it's a general read-only task view, not
 * Mini-App-specific. */
export function TgBoardTab() {
  return <MobileTaskBoard />;
}

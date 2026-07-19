"use client";

import { useEffect, useState } from "react";
import { MobileTaskBoard } from "@/components/tasks/mobile-task-board";
import { TgTaskSheet } from "@/components/tg/tg-task-sheet";
import { isTgDemoMode } from "@/lib/telegram/demo";
import type { Task } from "@/types";

/** Cockpit Board tab — the shared read-only board plus the tap-through
 * task sheet (status, ACs, open findings, PR link). Demo mode swaps in the
 * canned fixture list, lazily imported so it stays out of the prod bundle. */
export function TgBoardTab() {
  const [selected, setSelected] = useState<Task | null>(null);
  const [demoTasks, setDemoTasks] = useState<Task[] | undefined>(undefined);

  useEffect(() => {
    if (!isTgDemoMode()) return;
    void import("@/lib/telegram/demo-data").then((m) =>
      setDemoTasks(m.DEMO_TASKS),
    );
  }, []);

  return (
    <>
      <MobileTaskBoard tasks={demoTasks} onTaskPress={setSelected} />
      <TgTaskSheet task={selected} onClose={() => setSelected(null)} />
    </>
  );
}

"use client";

import { useState } from "react";
import { TaskStatus, Team } from "@/types";
import { KanbanBoard } from "../core/kanban-board";

const PM_COLUMNS = [
  { id: "backlog", status: TaskStatus.BACKLOG, title: "Backlog", color: "bg-slate-50 dark:bg-slate-900" },
  { id: "incoming", status: TaskStatus.PENDING, title: "Pending", color: "bg-gray-100 dark:bg-gray-800" },
  { id: "assigned", status: TaskStatus.CLAIMED, title: "Assigned", color: "bg-blue-50 dark:bg-blue-950" },
  { id: "in-progress", status: TaskStatus.IN_PROGRESS, title: "In Progress", color: "bg-blue-100 dark:bg-blue-900" },
  { id: "blocked", status: TaskStatus.BLOCKED, title: "Blocked", color: "bg-red-100 dark:bg-red-900" },
  { id: "qa", status: TaskStatus.AWAITING_QA, title: "In QA", color: "bg-yellow-50 dark:bg-yellow-950" },
  { id: "docs", status: TaskStatus.AWAITING_DOCUMENTATION, title: "In Docs", color: "bg-purple-50 dark:bg-purple-950" },
  { id: "pm-review", status: TaskStatus.AWAITING_PM_REVIEW, title: "PM Review", color: "bg-orange-50 dark:bg-orange-950" },
  { id: "done", status: TaskStatus.COMPLETED, title: "Done", color: "bg-green-50 dark:bg-green-950" },
];

interface PmKanbanProps {
  initialTeam?: Team;
}

export function PmKanban({ initialTeam }: PmKanbanProps) {
  const [team, setTeam] = useState<Team | undefined>(initialTeam);

  return (
    <KanbanBoard
      title="PM Kanban"
      description="Project management overview"
      columns={PM_COLUMNS}
      teamFilter={team}
      onTeamChange={(t) => setTeam(t === "all" ? undefined : t)}
    />
  );
}

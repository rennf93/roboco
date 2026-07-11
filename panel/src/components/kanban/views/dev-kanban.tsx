"use client";

import { useState } from "react";
import { TaskStatus, Team } from "@/types";
import { KanbanBoard } from "../core/kanban-board";

const DEV_COLUMNS = [
  {
    id: "backlog",
    status: TaskStatus.BACKLOG,
    title: "Backlog",
    color: "bg-slate-50 dark:bg-slate-900/40",
  },
  {
    id: "pending",
    status: TaskStatus.PENDING,
    title: "Ready",
    color: "bg-gray-100 dark:bg-gray-900/40",
  },
  {
    id: "assigned",
    status: TaskStatus.CLAIMED,
    title: "Assigned",
    color: "bg-blue-50 dark:bg-blue-950/40",
  },
  {
    id: "in-progress",
    status: TaskStatus.IN_PROGRESS,
    title: "In Progress",
    color: "bg-blue-100 dark:bg-blue-900/40",
  },
  {
    id: "blocked",
    status: TaskStatus.BLOCKED,
    title: "Blocked",
    color: "bg-red-50 dark:bg-red-950/40",
  },
  {
    id: "verifying",
    status: TaskStatus.VERIFYING,
    title: "Verifying",
    color: "bg-purple-50 dark:bg-purple-950/40",
  },
  {
    id: "qa-review",
    status: TaskStatus.AWAITING_QA,
    title: "QA Review",
    color: "bg-yellow-50 dark:bg-yellow-950/40",
  },
  {
    id: "done",
    status: TaskStatus.COMPLETED,
    title: "Done",
    color: "bg-green-50 dark:bg-green-950/40",
  },
];

interface DevKanbanProps {
  initialTeam?: Team;
}

export function DevKanban({ initialTeam }: DevKanbanProps) {
  const [team, setTeam] = useState<Team | undefined>(initialTeam);

  return (
    <KanbanBoard
      title="Dev Kanban"
      description="Developer workflow from backlog to completion"
      columns={DEV_COLUMNS}
      teamFilter={team}
      onTeamChange={(t) => setTeam(t === "all" ? undefined : t)}
    />
  );
}

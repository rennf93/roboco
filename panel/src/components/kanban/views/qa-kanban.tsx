"use client";

import { useState } from "react";
import { TaskStatus, Team } from "@/types";
import { KanbanBoard } from "../core/kanban-board";

const QA_COLUMNS = [
  { id: "awaiting", status: TaskStatus.AWAITING_QA, title: "Awaiting Review", color: "bg-yellow-100 dark:bg-yellow-900" },
  { id: "verifying", status: TaskStatus.VERIFYING, title: "In Review", color: "bg-blue-100 dark:bg-blue-900" },
  { id: "passed", status: TaskStatus.AWAITING_DOCUMENTATION, title: "Passed", color: "bg-green-50 dark:bg-green-950" },
  { id: "failed", status: TaskStatus.NEEDS_REVISION, title: "Failed", color: "bg-red-50 dark:bg-red-950" },
];

interface QaKanbanProps {
  initialTeam?: Team;
}

export function QaKanban({ initialTeam }: QaKanbanProps) {
  const [team, setTeam] = useState<Team | undefined>(initialTeam);

  return (
    <KanbanBoard
      title="QA Kanban"
      description="Quality assurance review workflow"
      columns={QA_COLUMNS}
      teamFilter={team}
      onTeamChange={(t) => setTeam(t === "all" ? undefined : t)}
      showQaActions
    />
  );
}

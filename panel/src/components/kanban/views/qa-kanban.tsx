"use client";

import { useState } from "react";
import { TaskStatus, Team } from "@/types";
import { KanbanBoard } from "../core/kanban-board";

const QA_COLUMNS = [
  {
    id: "awaiting",
    status: TaskStatus.AWAITING_QA,
    title: "Awaiting Review",
    color: "bg-yellow-100 dark:bg-yellow-900/40",
  },
  {
    id: "verifying",
    status: TaskStatus.VERIFYING,
    title: "In Review",
    color: "bg-blue-100 dark:bg-blue-900/40",
  },
  {
    id: "passed",
    status: TaskStatus.AWAITING_DOCUMENTATION,
    title: "Passed",
    color: "bg-green-50 dark:bg-green-950/40",
  },
  {
    id: "failed",
    status: TaskStatus.NEEDS_REVISION,
    title: "Failed",
    color: "bg-red-50 dark:bg-red-950/40",
  },
];

interface QaKanbanProps {
  initialTeam?: Team;
  // Controlled team filter (e.g. shared with the Tasks List tab via URL
  // state). Passing onTeamChange switches this view from its own internal
  // team state to the caller's — omit both to keep the uncontrolled
  // initialTeam-only behavior the standalone /kanban page still relies on.
  team?: Team;
  onTeamChange?: (team: Team | undefined) => void;
}

export function QaKanban({
  initialTeam,
  team: controlledTeam,
  onTeamChange,
}: QaKanbanProps) {
  const [localTeam, setLocalTeam] = useState<Team | undefined>(initialTeam);
  const isControlled = onTeamChange !== undefined;
  const team = isControlled ? controlledTeam : localTeam;

  return (
    <KanbanBoard
      title="QA Kanban"
      description="Quality assurance review workflow"
      columns={QA_COLUMNS}
      teamFilter={team}
      onTeamChange={(t) => {
        const next = t === "all" ? undefined : t;
        if (isControlled) onTeamChange(next);
        else setLocalTeam(next);
      }}
      showQaActions
    />
  );
}

"use client";

import { useState } from "react";
import { TaskStatus, Team } from "@/types";
import { KanbanBoard } from "../core/kanban-board";

// The in-path PR-review gate: a reviewer checks each assembled cell->root /
// root->master PR before the PM merges. A card sits in "Awaiting Review" until
// a reviewer pr_passes it on to PM Review or pr_fails it back for changes.
const PR_REVIEW_COLUMNS = [
  {
    id: "awaiting",
    status: TaskStatus.AWAITING_PR_REVIEW,
    title: "Awaiting Review",
    color: "bg-teal-100 dark:bg-teal-900/40",
  },
  {
    id: "passed",
    status: TaskStatus.AWAITING_PM_REVIEW,
    title: "Passed",
    color: "bg-green-50 dark:bg-green-950/40",
  },
  {
    id: "changes",
    status: TaskStatus.NEEDS_REVISION,
    title: "Changes Requested",
    color: "bg-red-50 dark:bg-red-950/40",
  },
];

interface PrReviewKanbanProps {
  initialTeam?: Team;
  // Controlled team filter (e.g. shared with the Tasks List tab via URL
  // state). Passing onTeamChange switches this view from its own internal
  // team state to the caller's — omit both to keep the uncontrolled
  // initialTeam-only behavior the standalone /kanban page still relies on.
  team?: Team;
  onTeamChange?: (team: Team | undefined) => void;
}

export function PrReviewKanban({
  initialTeam,
  team: controlledTeam,
  onTeamChange,
}: PrReviewKanbanProps) {
  const [localTeam, setLocalTeam] = useState<Team | undefined>(initialTeam);
  const isControlled = onTeamChange !== undefined;
  const team = isControlled ? controlledTeam : localTeam;

  return (
    <KanbanBoard
      title="PR Review Kanban"
      description="In-path PR-review gate for assembled PRs"
      columns={PR_REVIEW_COLUMNS}
      teamFilter={team}
      onTeamChange={(t) => {
        const next = t === "all" ? undefined : t;
        if (isControlled) onTeamChange(next);
        else setLocalTeam(next);
      }}
    />
  );
}

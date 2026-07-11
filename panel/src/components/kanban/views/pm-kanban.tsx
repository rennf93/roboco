"use client";

import { useState } from "react";
import { TaskStatus, Team } from "@/types";
import { KanbanBoard } from "../core/kanban-board";

// The management board carries a column for every lifecycle state a human
// overseer may need to drive a task into — including the recovery states
// (paused / needs-revision / awaiting-CEO / cancelled) that automatic flow
// never lands on. Dragging a card issues an admin status override, so the CEO
// can recover a wedged task straight from the board. (verifying is omitted: a
// transient dev-internal self-check state, not a destination a human sets.)
const PM_COLUMNS = [
  {
    id: "backlog",
    status: TaskStatus.BACKLOG,
    title: "Backlog",
    color: "bg-slate-50 dark:bg-slate-900/40",
  },
  {
    id: "incoming",
    status: TaskStatus.PENDING,
    title: "Pending",
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
    color: "bg-red-100 dark:bg-red-900/40",
  },
  {
    id: "paused",
    status: TaskStatus.PAUSED,
    title: "Paused",
    color: "bg-amber-50 dark:bg-amber-950/40",
  },
  {
    id: "qa",
    status: TaskStatus.AWAITING_QA,
    title: "In QA",
    color: "bg-yellow-50 dark:bg-yellow-950/40",
  },
  {
    id: "needs-revision",
    status: TaskStatus.NEEDS_REVISION,
    title: "Needs Revision",
    color: "bg-rose-50 dark:bg-rose-950/40",
  },
  {
    id: "docs",
    status: TaskStatus.AWAITING_DOCUMENTATION,
    title: "In Docs",
    color: "bg-purple-50 dark:bg-purple-950/40",
  },
  {
    id: "pr-review",
    status: TaskStatus.AWAITING_PR_REVIEW,
    title: "PR Review",
    color: "bg-teal-50 dark:bg-teal-950/40",
  },
  {
    id: "pm-review",
    status: TaskStatus.AWAITING_PM_REVIEW,
    title: "PM Review",
    color: "bg-orange-50 dark:bg-orange-950/40",
  },
  {
    id: "ceo-approval",
    status: TaskStatus.AWAITING_CEO_APPROVAL,
    title: "CEO Approval",
    color: "bg-indigo-50 dark:bg-indigo-950/40",
  },
  {
    id: "done",
    status: TaskStatus.COMPLETED,
    title: "Done",
    color: "bg-green-50 dark:bg-green-950/40",
  },
  {
    id: "cancelled",
    status: TaskStatus.CANCELLED,
    title: "Cancelled",
    color: "bg-zinc-100 dark:bg-zinc-900/40",
  },
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

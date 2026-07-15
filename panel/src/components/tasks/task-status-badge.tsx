import { TaskStatus } from "@/types";
import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";

const statusColors: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "bg-slate-500",
  [TaskStatus.PENDING]: "bg-gray-500",
  [TaskStatus.CLAIMED]: "bg-blue-400",
  [TaskStatus.IN_PROGRESS]: "bg-blue-600",
  [TaskStatus.BLOCKED]: "bg-red-500",
  [TaskStatus.PAUSED]: "bg-yellow-500",
  [TaskStatus.VERIFYING]: "bg-purple-500",
  [TaskStatus.NEEDS_REVISION]: "bg-orange-500",
  [TaskStatus.AWAITING_QA]: "bg-yellow-600",
  [TaskStatus.AWAITING_DOCUMENTATION]: "bg-indigo-500",
  [TaskStatus.AWAITING_PR_REVIEW]: "bg-teal-600",
  [TaskStatus.AWAITING_PM_REVIEW]: "bg-orange-600",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "bg-amber-600",
  [TaskStatus.COMPLETED]: "bg-green-500",
  [TaskStatus.CANCELLED]: "bg-gray-400",
};

const statusDescriptions: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "PM setup phase — dependencies or session not ready yet.",
  [TaskStatus.PENDING]: "Ready for work — the orchestrator can spawn an agent.",
  [TaskStatus.CLAIMED]: "An agent has locked this task.",
  [TaskStatus.IN_PROGRESS]: "Active development in progress.",
  [TaskStatus.BLOCKED]: "An external dependency is blocking progress.",
  [TaskStatus.PAUSED]: "Temporarily stopped; can resume.",
  [TaskStatus.VERIFYING]: "The developer is self-verifying their work.",
  [TaskStatus.NEEDS_REVISION]:
    "QA / PR-review / PM / CEO requested changes — back with the dev.",
  [TaskStatus.AWAITING_QA]: "Submitted for QA review (PR already open).",
  [TaskStatus.AWAITING_DOCUMENTATION]:
    "Documentation phase — the documenter is writing docs.",
  [TaskStatus.AWAITING_PR_REVIEW]:
    "PR-review gate: a reviewer checks the assembled PR before the PM merges.",
  [TaskStatus.AWAITING_PM_REVIEW]: "Docs complete; the PM is reviewing and merging.",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "Escalated for the CEO's final approval.",
  [TaskStatus.COMPLETED]: "Terminal — work done and merged.",
  [TaskStatus.CANCELLED]: "Terminal — work cancelled.",
};

interface TaskStatusBadgeProps {
  status: TaskStatus;
}

/** Plain-language explanation for a task lifecycle state. Reused by the
 * shared badge and by inline status renderers (kanban, task header) so the
 * canonical text lives in one place. Empty for an unknown status. */
export function taskStatusDescription(status: TaskStatus): string {
  return statusDescriptions[status] ?? "";
}

export function TaskStatusBadge({ status }: TaskStatusBadgeProps) {
  return (
    <HelpTip label={statusDescriptions[status]}>
      <Badge className={`${statusColors[status] ?? "bg-slate-600"} text-white`}>
        {status.replace(/_/g, " ")}
      </Badge>
    </HelpTip>
  );
}
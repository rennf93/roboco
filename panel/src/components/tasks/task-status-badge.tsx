import { TaskStatus } from "@/types";
import { Badge } from "@/components/ui/badge";

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
  [TaskStatus.AWAITING_PM_REVIEW]: "bg-orange-600",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "bg-amber-600",
  [TaskStatus.COMPLETED]: "bg-green-500",
  [TaskStatus.CANCELLED]: "bg-gray-400",
  [TaskStatus.QUARANTINED]: "bg-pink-500",
};

interface TaskStatusBadgeProps {
  status: TaskStatus;
}

export function TaskStatusBadge({ status }: TaskStatusBadgeProps) {
  return (
    <Badge className={statusColors[status] + " text-white"}>
      {status.replace(/_/g, " ")}
    </Badge>
  );
}

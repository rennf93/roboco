"use client";

import { Badge } from "@/components/ui/badge";
import { HelpTip } from "@/components/ui/help-tip";
import { taskStatusDescription } from "@/components/tasks/task-status-badge";
import { TaskStatus } from "@/types";
import { AlertTriangle } from "lucide-react";

export function BlockedBadge() {
  return (
    <HelpTip label={taskStatusDescription(TaskStatus.BLOCKED)}>
      <Badge variant="destructive" className="text-xs gap-1">
        <AlertTriangle className="h-3 w-3" />
        Blocked
      </Badge>
    </HelpTip>
  );
}

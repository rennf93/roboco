"use client";

import { GitBranch, GitPullRequest, FileCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Task, TaskStatus } from "@/types";

interface GitStatusBadgeProps {
  task: Task;
  compact?: boolean;
}

export function GitStatusBadge({ task, compact = true }: GitStatusBadgeProps) {
  // All tasks follow git workflow - show relevant status

  // Show PR badge with status (highest priority)
  if (task.pr_number) {
    return (
      <Badge className="gap-1 text-xs bg-purple-500/10 text-purple-600 dark:text-purple-400">
        <GitPullRequest className="h-3 w-3" />
        PR #{task.pr_number}
      </Badge>
    );
  }

  // Parallel phase indicators (for AWAITING_DOCUMENTATION)
  if (task.status === TaskStatus.AWAITING_DOCUMENTATION) {
    return (
      <div className="flex gap-1">
        <Badge
          variant={task.docs_complete ? "default" : "outline"}
          className={`gap-1 text-xs ${
            task.docs_complete
              ? "bg-green-500/10 text-green-600 dark:text-green-400"
              : "text-muted-foreground"
          }`}
        >
          <FileCheck className="h-3 w-3" />
          {compact ? "" : "Docs"}
        </Badge>
        <Badge
          variant={task.pr_created ? "default" : "outline"}
          className={`gap-1 text-xs ${
            task.pr_created
              ? "bg-green-500/10 text-green-600 dark:text-green-400"
              : "text-muted-foreground"
          }`}
        >
          <GitPullRequest className="h-3 w-3" />
          {compact ? "" : "PR"}
        </Badge>
      </div>
    );
  }

  // Show branch badge (when branch exists but no PR yet)
  if (task.branch_name) {
    return (
      <Badge variant="outline" className="gap-1 text-xs">
        <GitBranch className="h-3 w-3" />
        {compact ? "Branch" : task.branch_name}
      </Badge>
    );
  }

  // Git task without branch yet
  return (
    <Badge variant="outline" className="gap-1 text-xs text-muted-foreground">
      <GitBranch className="h-3 w-3" />
      {compact ? "Git" : "No branch"}
    </Badge>
  );
}

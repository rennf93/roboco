"use client";

import type { ReactNode } from "react";
import { GitBranch, GitPullRequest, FileCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Task, TaskStatus } from "@/types";
import { branchUrl, pullUrl } from "@/lib/repo-url";

interface GitStatusBadgeProps {
  task: Task;
  compact?: boolean;
  /** Project git_url — used to build the clickable branch / PR links. */
  repoUrl?: string | null;
}

/**
 * Wrap a badge in an external link when a URL is available, otherwise render it
 * plain. The badge keeps its exact look; the link only adds the click target
 * (and a subtle hover). The parent row's click handler ignores `<a>` clicks, so
 * opening a branch/PR never also toggles the row.
 */
function MaybeLink({
  href,
  children,
}: {
  href: string | null;
  children: ReactNode;
}) {
  if (!href) return <>{children}</>;
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      title="Open in GitHub"
      className="inline-flex transition-opacity hover:opacity-80"
    >
      {children}
    </a>
  );
}

export function GitStatusBadge({
  task,
  compact = true,
  repoUrl,
}: GitStatusBadgeProps) {
  // All tasks follow git workflow - show relevant status

  // Show PR badge with status (highest priority)
  if (task.pr_number) {
    return (
      <MaybeLink href={task.pr_url ?? pullUrl(repoUrl, task.pr_number)}>
        <Badge className="gap-1 text-xs bg-purple-500/10 text-purple-600 dark:text-purple-400">
          <GitPullRequest className="h-3 w-3" />
          PR #{task.pr_number}
        </Badge>
      </MaybeLink>
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
      <MaybeLink href={branchUrl(repoUrl, task.branch_name)}>
        <Badge variant="outline" className="gap-1 text-xs">
          <GitBranch className="h-3 w-3" />
          {compact ? "Branch" : task.branch_name}
        </Badge>
      </MaybeLink>
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

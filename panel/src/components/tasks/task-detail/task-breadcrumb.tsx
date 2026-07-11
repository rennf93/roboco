"use client";

import { Task } from "@/types";
import { useTask } from "@/hooks/use-tasks";
import { Skeleton } from "@/components/ui/skeleton";
import { ChevronRight } from "lucide-react";
import Link from "next/link";

interface TaskBreadcrumbProps {
  task: Task;
}

// Renders "Parent title >" above the task title whenever this task has a
// parent — absent (returns null) for a root task. Only the immediate parent
// is shown; deeper ancestry is reachable by following the chain one hop at a
// time, matching how the rest of the panel represents task hierarchy.
export function TaskBreadcrumb({ task }: TaskBreadcrumbProps) {
  const parentId = task.parent_task_id;
  const { data: parent, isLoading } = useTask(parentId ?? "");

  if (!parentId) return null;

  return (
    <nav
      aria-label="Parent task"
      className="flex items-center gap-1.5 text-sm text-muted-foreground min-w-0"
    >
      {isLoading || !parent ? (
        <Skeleton className="h-4 w-32" />
      ) : (
        <Link
          href={`/tasks/${parent.id}`}
          prefetch={false}
          className="truncate hover:text-foreground hover:underline"
          title={parent.title}
        >
          {parent.title}
        </Link>
      )}
      <ChevronRight className="h-3.5 w-3.5 shrink-0" />
      <span className="truncate text-foreground/70" title={task.title}>
        {task.title}
      </span>
    </nav>
  );
}

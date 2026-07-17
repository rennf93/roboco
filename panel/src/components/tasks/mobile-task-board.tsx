"use client";

import { useMemo, useState } from "react";
import { useTasks } from "@/hooks/use-tasks";
import { TaskStatus, type Task } from "@/types";
import { TaskStatusBadge } from "@/components/tasks/task-status-badge";
import { getAgentDisplayName } from "@/lib/agent-utils";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronDown, ListTodo } from "lucide-react";
import { cn } from "@/lib/utils";

// Active-work-first display order (mirrors the lifecycle doc's left-to-right
// flow) rather than the enum's declaration order.
const STATUS_ORDER: TaskStatus[] = [
  TaskStatus.IN_PROGRESS,
  TaskStatus.BLOCKED,
  TaskStatus.NEEDS_REVISION,
  TaskStatus.VERIFYING,
  TaskStatus.AWAITING_QA,
  TaskStatus.AWAITING_DOCUMENTATION,
  TaskStatus.AWAITING_PR_REVIEW,
  TaskStatus.AWAITING_PM_REVIEW,
  TaskStatus.AWAITING_CEO_APPROVAL,
  TaskStatus.PAUSED,
  TaskStatus.CLAIMED,
  TaskStatus.PENDING,
  TaskStatus.BACKLOG,
  TaskStatus.COMPLETED,
  TaskStatus.CANCELLED,
];

// Open by default: the actionable half of the lifecycle. Terminal and
// not-yet-started sections start collapsed to keep the first scroll short.
const DEFAULT_OPEN = new Set<TaskStatus>([
  TaskStatus.IN_PROGRESS,
  TaskStatus.BLOCKED,
  TaskStatus.NEEDS_REVISION,
  TaskStatus.AWAITING_CEO_APPROVAL,
]);

function TaskRow({ task }: { task: Task }) {
  return (
    <div className="flex items-center justify-between gap-2 border-t px-3 py-2 first:border-t-0">
      <div className="min-w-0">
        <p className="truncate text-sm">{task.title}</p>
        <p className="truncate text-xs text-muted-foreground">
          {getAgentDisplayName(task.assigned_to)}
        </p>
      </div>
      <TaskStatusBadge status={task.status} />
    </div>
  );
}

function StatusSection({
  status,
  tasks,
  defaultOpen,
}: {
  status: TaskStatus;
  tasks: Task[];
  defaultOpen: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="rounded-lg border"
    >
      <CollapsibleTrigger className="flex w-full items-center justify-between gap-2 px-3 py-2.5 text-left">
        <span className="text-sm font-medium">
          {status.replace(/_/g, " ")}{" "}
          <span className="text-muted-foreground">({tasks.length})</span>
        </span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 transition-transform",
            open && "rotate-180",
          )}
        />
      </CollapsibleTrigger>
      <CollapsibleContent>
        {tasks.map((t) => (
          <TaskRow key={t.id} task={t} />
        ))}
      </CollapsibleContent>
    </Collapsible>
  );
}

/**
 * Read-only phone-cockpit task board: every task grouped by status into
 * collapsible sections, compact rows (title, assignee, status pill). No
 * drag-and-drop — that's the desktop kanban columns' job; this is a
 * glance-and-tap surface for the /tg Mini App.
 */
export function MobileTaskBoard() {
  const { data, isLoading } = useTasks({ limit: 200 });

  const grouped = useMemo(() => {
    const byStatus = new Map<TaskStatus, Task[]>();
    for (const task of data ?? []) {
      const list = byStatus.get(task.status);
      if (list) list.push(task);
      else byStatus.set(task.status, [task]);
    }
    return STATUS_ORDER.filter((s) => byStatus.has(s)).map((s) => ({
      status: s,
      tasks: byStatus.get(s)!,
    }));
  }, [data]);

  if (isLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-11 w-full" />
        ))}
      </div>
    );
  }

  if (grouped.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-10 text-center text-muted-foreground">
        <ListTodo className="h-8 w-8 opacity-50" />
        <p className="text-sm">No tasks</p>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {grouped.map(({ status, tasks }) => (
        <StatusSection
          key={status}
          status={status}
          tasks={tasks}
          defaultOpen={DEFAULT_OPEN.has(status)}
        />
      ))}
    </div>
  );
}

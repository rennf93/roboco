"use client";

import { Task, TaskStatus } from "@/types";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { KanbanCard } from "./kanban-card";
import { useDroppable } from "@dnd-kit/core";
import { cn } from "@/lib/utils";

interface KanbanColumnProps {
  id: string;
  title: string;
  status: TaskStatus;
  tasks: Task[];
  color: string;
  isLoading: boolean;
  onAction?: (action: string, taskId: string) => void;
  showQaActions?: boolean;
  /** Extra Tailwind classes forwarded to the root element (e.g. w-full for mobile). */
  className?: string;
}

export function KanbanColumn({
  id: _id,
  title,
  status,
  tasks,
  color,
  isLoading,
  onAction,
  showQaActions,
  className,
}: KanbanColumnProps) {
  void _id; // Reserved for future use
  const { setNodeRef, isOver } = useDroppable({
    id: status,
  });

  return (
    <div
      ref={setNodeRef}
      className={cn(
        "flex flex-col rounded-lg p-3 w-72 shrink-0 sm:w-80",
        color,
        isOver && "ring-2 ring-primary ring-offset-2",
        className,
      )}
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="font-semibold text-sm text-gray-800 dark:text-gray-100">{title}</h3>
        <Badge variant="secondary" className="dark:bg-gray-700 dark:text-gray-100">
          {isLoading ? "..." : tasks.length}
        </Badge>
      </div>
      <ScrollArea className="flex-1 max-h-[calc(100vh-280px)]">
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
            <Skeleton className="h-24" />
          </div>
        ) : tasks.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-sm">
            No tasks
          </div>
        ) : (
          <div className="space-y-2 pr-2">
            {tasks.map((task) => (
              <KanbanCard
                key={task.id}
                task={task}
                onAction={onAction}
                showQaActions={showQaActions && (status === TaskStatus.AWAITING_QA || status === TaskStatus.VERIFYING)}
              />
            ))}
          </div>
        )}
      </ScrollArea>
    </div>
  );
}

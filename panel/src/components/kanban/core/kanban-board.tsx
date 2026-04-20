"use client";

import { Task, TaskStatus, Team } from "@/types";
import { useTasks, useTaskLifecycle, useUpdateTask } from "@/hooks/use-tasks";
import { KanbanColumn } from "./kanban-column";
import { Button } from "@/components/ui/button";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { RefreshCw } from "lucide-react";
import { toast } from "sonner";
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { useState } from "react";
import { KanbanCard } from "./kanban-card";

interface ColumnConfig {
  id: string;
  status: TaskStatus;
  title: string;
  color: string;
}

interface KanbanBoardProps {
  title: string;
  description?: string;
  columns: ColumnConfig[];
  teamFilter?: Team;
  onTeamChange?: (team: Team | "all") => void;
  showQaActions?: boolean;
}

export function KanbanBoard({
  title,
  description,
  columns,
  teamFilter,
  onTeamChange,
  showQaActions,
}: KanbanBoardProps) {
  const { data: tasks, isLoading, refetch } = useTasks(
    teamFilter ? { team: teamFilter } : undefined
  );
  const lifecycle = useTaskLifecycle();
  const updateTask = useUpdateTask();
  const [activeTask, setActiveTask] = useState<Task | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    })
  );

  // Group tasks by status
  const tasksByStatus = columns.reduce((acc, col) => {
    acc[col.status] = (tasks || []).filter((t) => t.status === col.status);
    return acc;
  }, {} as Record<TaskStatus, Task[]>);

  const handleDragStart = (event: DragStartEvent) => {
    const taskId = event.active.id as string;
    const task = tasks?.find((t) => t.id === taskId);
    if (task) {
      setActiveTask(task);
    }
  };

  const handleDragEnd = async (event: DragEndEvent) => {
    setActiveTask(null);

    const { active, over } = event;
    if (!over) return;

    const taskId = active.id as string;
    const newStatus = over.id as TaskStatus;

    const task = tasks?.find((t) => t.id === taskId);
    if (!task || task.status === newStatus) return;

    // Prevent moving backlog tasks without proper activation
    if (task.status === TaskStatus.BACKLOG) {
      toast.error("Backlog tasks must be activated by PM first");
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId,
        updates: { status: newStatus },
      });
      toast.success(`Task moved to ${newStatus.replace(/_/g, " ")}`);
      refetch();
    } catch {
      toast.error("Failed to move task");
    }
  };

  const handleAction = async (action: string, taskId: string) => {
    try {
      switch (action) {
        case "move-forward":
          // Determine next action based on current status
          const task = tasks?.find((t) => t.id === taskId);
          if (!task) return;

          switch (task.status) {
            case TaskStatus.BACKLOG:
              // BACKLOG tasks need session before activation - PM only
              toast.info("Backlog tasks must be activated by PM with a session");
              break;
            case TaskStatus.PENDING:
              await lifecycle.claim.mutateAsync(taskId);
              toast.success("Task claimed");
              break;
            case TaskStatus.CLAIMED:
              await lifecycle.start.mutateAsync(taskId);
              toast.success("Task started");
              break;
            case TaskStatus.IN_PROGRESS:
              await lifecycle.submitQa.mutateAsync({ taskId });
              toast.success("Submitted for QA");
              break;
            case TaskStatus.BLOCKED:
              await lifecycle.unblock.mutateAsync(taskId);
              toast.success("Task unblocked");
              break;
            case TaskStatus.AWAITING_QA:
              await lifecycle.passQa.mutateAsync({ taskId });
              toast.success("QA passed");
              break;
            case TaskStatus.AWAITING_DOCUMENTATION:
              await lifecycle.complete.mutateAsync(taskId);
              toast.success("Task completed");
              break;
          }
          break;
        case "pass-qa":
          await lifecycle.passQa.mutateAsync({ taskId });
          toast.success("QA passed");
          break;
        case "fail-qa":
          await lifecycle.failQa.mutateAsync({ taskId });
          toast.success("QA failed - returned to developer");
          break;
      }
      refetch();
    } catch {
      toast.error("Action failed");
    }
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
          {description && (
            <p className="text-muted-foreground">{description}</p>
          )}
        </div>
        <div className="flex items-center gap-2">
          {onTeamChange && (
            <Select
              value={teamFilter || "all"}
              onValueChange={(v) => onTeamChange(v as Team | "all")}
            >
              <SelectTrigger className="w-auto min-w-28">
                <SelectValue placeholder="Team" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Teams</SelectItem>
                {Object.values(Team).map((team) => (
                  <SelectItem key={team} value={team}>
                    {team.replace(/_/g, " ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Button variant="outline" onClick={() => refetch()}>
            <RefreshCw className="h-4 w-4 mr-2" />
            Refresh
          </Button>
        </div>
      </div>

      {/* Board */}
      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        <div className="flex gap-4 overflow-x-auto pb-4">
          {columns.map((col) => (
            <KanbanColumn
              key={col.id}
              id={col.id}
              title={col.title}
              status={col.status}
              tasks={tasksByStatus[col.status] || []}
              color={col.color}
              isLoading={isLoading}
              onAction={handleAction}
              showQaActions={showQaActions}
            />
          ))}
        </div>
        <DragOverlay>
          {activeTask ? (
            <KanbanCard task={activeTask} isDragging />
          ) : null}
        </DragOverlay>
      </DndContext>
    </div>
  );
}

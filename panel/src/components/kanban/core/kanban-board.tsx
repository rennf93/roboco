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
import { RequiredNotesDialog } from "@/components/tasks/task-detail/task-action-dialogs";

type NotesActionKind = "pass-qa" | "fail-qa" | "complete";

interface PendingNotesAction {
  kind: NotesActionKind;
  taskId: string;
}

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
  const [pendingNotesAction, setPendingNotesAction] = useState<PendingNotesAction | null>(null);

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
              setPendingNotesAction({ kind: "pass-qa", taskId });
              return; // Dialog collects the required note
            case TaskStatus.AWAITING_DOCUMENTATION:
              setPendingNotesAction({ kind: "complete", taskId });
              return; // Dialog collects the required justification
          }
          break;
        case "pass-qa":
          setPendingNotesAction({ kind: "pass-qa", taskId });
          return; // Dialog collects the required note
        case "fail-qa":
          setPendingNotesAction({ kind: "fail-qa", taskId });
          return; // Dialog collects the required note
      }
      refetch();
    } catch {
      toast.error("Action failed");
    }
  };

  const handleNotesConfirm = async (text: string) => {
    if (!pendingNotesAction) return;
    const { kind, taskId } = pendingNotesAction;
    try {
      switch (kind) {
        case "pass-qa":
          await lifecycle.passQa.mutateAsync({ taskId, qaNotes: text });
          toast.success("QA passed");
          break;
        case "fail-qa":
          await lifecycle.failQa.mutateAsync({ taskId, qaNotes: text });
          toast.success("QA failed - returned to developer");
          break;
        case "complete":
          await lifecycle.complete.mutateAsync({ taskId, justification: text });
          toast.success("Task completed");
          break;
      }
      setPendingNotesAction(null);
      refetch();
    } catch {
      toast.error("Action failed");
    }
  };

  const notesDialogConfig: Record<
    NotesActionKind,
    {
      title: string;
      description: string;
      label: string;
      placeholder: string;
      minChars: number;
      confirmLabel: string;
      destructive?: boolean;
      isPending: boolean;
    }
  > = {
    "pass-qa": {
      title: "Pass QA",
      description:
        "Record the QA review outcome. This note is the permanent audit record and is required.",
      label: "QA notes",
      placeholder: "Verified against acceptance criteria; passing because...",
      minChars: 20,
      confirmLabel: "Pass QA",
      isPending: lifecycle.passQa.isPending,
    },
    "fail-qa": {
      title: "Fail QA",
      description:
        "Record what failed QA and what needs to change. This note is the permanent audit record and is required.",
      label: "QA notes",
      placeholder: "Failing QA because...",
      minChars: 20,
      confirmLabel: "Fail QA",
      destructive: true,
      isPending: lifecycle.failQa.isPending,
    },
    complete: {
      title: "Approve & Complete",
      description:
        "Record why this work is approved and complete. This note is the permanent audit record and is required.",
      label: "Completion justification",
      placeholder: "Approving and completing because...",
      minChars: 20,
      confirmLabel: "Approve & Complete",
      isPending: lifecycle.complete.isPending,
    },
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

      {pendingNotesAction && (
        <RequiredNotesDialog
          open={true}
          onOpenChange={(open) => {
            if (!open) setPendingNotesAction(null);
          }}
          onConfirm={handleNotesConfirm}
          {...notesDialogConfig[pendingNotesAction.kind]}
        />
      )}
    </div>
  );
}

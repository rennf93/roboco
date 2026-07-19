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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { HelpTip } from "@/components/ui/help-tip";
import { ChevronLeft, ChevronRight } from "lucide-react";
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KanbanCard } from "./kanban-card";
import { RequiredNotesDialog } from "@/components/tasks/task-detail/task-action-dialogs";
import { skippedPreconditions } from "./bypass-preconditions";
import { usePageRefresh } from "@/hooks";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";

type NotesActionKind = "pass-qa" | "fail-qa" | "complete";

interface PendingNotesAction {
  kind: NotesActionKind;
  taskId: string;
}

// A drag that would skip material lifecycle preconditions (completing with no
// PR, QA-bypassing, finishing docs that aren't complete, …) is held for an
// explicit override confirmation. The admin status-override is intentional
// and stays intact — this only makes the bypass visible instead of silent.
interface PendingOverride {
  task: Task;
  newStatus: TaskStatus;
  skipped: string[];
}

// Terminal/hatch states the server-side PATCH override guard refuses without
// an explicit `force: true` (the bypass is deliberate + audited). A kanban
// drag is the admin override surface, so a move into one of these must carry
// the acknowledgement or the backend rejects with 400.
const HATCH_OVERRIDE_STATES: ReadonlySet<TaskStatus> = new Set([
  TaskStatus.COMPLETED,
  TaskStatus.AWAITING_QA,
  TaskStatus.AWAITING_PM_REVIEW,
]);

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
  const {
    data: tasks,
    isLoading,
    refetch,
  } = useTasks(teamFilter ? { team: teamFilter } : undefined);
  const lifecycle = useTaskLifecycle();
  const updateTask = useUpdateTask();
  const [activeTask, setActiveTask] = useState<Task | null>(null);

  // Latest tasks snapshot for handleAction below, read via ref instead of a
  // closure — React Query hands back a new array reference on every refetch
  // even when the underlying rows are unchanged, which would otherwise bust
  // handleAction's identity (and, through it, KanbanCard's memoization) every
  // 30s regardless of whether anything actually changed.
  const tasksRef = useRef<Task[]>([]);
  tasksRef.current = tasks || [];

  // useMutation's mutate/mutateAsync are bound once per observer and stay
  // referentially stable across renders (query-core binds them in the
  // constructor) — pulling them out lets handleAction's useCallback below
  // stay stable even though `lifecycle` itself is a fresh object every render.
  const claimMutateAsync = lifecycle.claim.mutateAsync;
  const startMutateAsync = lifecycle.start.mutateAsync;
  const submitQaMutateAsync = lifecycle.submitQa.mutateAsync;
  const unblockMutateAsync = lifecycle.unblock.mutateAsync;

  const { register, unregister } = usePageRefresh();

  useEffect(() => {
    const cb = () => {
      void refetch();
    };
    register(cb);
    return () => unregister(cb);
  }, [register, unregister, refetch]);
  const [pendingNotesAction, setPendingNotesAction] =
    useState<PendingNotesAction | null>(null);
  const [pendingOverride, setPendingOverride] =
    useState<PendingOverride | null>(null);
  const [activeColumnIndex, setActiveColumnIndex] = useState(0);

  const sensors = useSensors(
    useSensor(PointerSensor, {
      activationConstraint: {
        distance: 8,
      },
    }),
  );

  // Group tasks by status. Memoized so each column's `tasks` prop keeps a
  // stable reference across renders that don't actually change the data —
  // required for KanbanColumn/KanbanCard's React.memo to skip re-rendering.
  const tasksByStatus = useMemo(
    () =>
      columns.reduce(
        (acc, col) => {
          acc[col.status] = (tasks || []).filter(
            (t) => t.status === col.status,
          );
          return acc;
        },
        {} as Record<TaskStatus, Task[]>,
      ),
    [tasks, columns],
  );

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

    // When QA actions are active, dragging to QA transition columns must go
    // through the notes/audit dialog — do NOT fire the mutation directly.
    if (showQaActions) {
      if (newStatus === TaskStatus.NEEDS_REVISION) {
        setPendingNotesAction({ kind: "fail-qa", taskId });
        return;
      }
      if (newStatus === TaskStatus.AWAITING_DOCUMENTATION) {
        setPendingNotesAction({ kind: "pass-qa", taskId });
        return;
      }
    }

    // A drag on this board routes the status move through the admin
    // status-override, which bypasses the in-band lifecycle validator. When
    // that override would skip material preconditions the panel can detect
    // (no open PR, docs not complete, not self-verified, non-terminal
    // subtasks, …), hold the move for an explicit confirmation that surfaces
    // exactly what's being skipped. The override stays — this only makes the
    // bypass visible instead of silent.
    const skipped = skippedPreconditions(task, newStatus, tasks || []);
    if (skipped.length > 0) {
      setPendingOverride({ task, newStatus, skipped });
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId,
        updates: {
          status: newStatus,
          force: HATCH_OVERRIDE_STATES.has(newStatus),
        },
      });
      toast.success(`Task moved to ${newStatus.replace(/_/g, " ")}`);
      refetch();
    } catch {
      toast.error("Failed to move task");
    }
  };

  const handleOverrideConfirm = async () => {
    if (!pendingOverride) return;
    const { task, newStatus } = pendingOverride;
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: {
          status: newStatus,
          force: HATCH_OVERRIDE_STATES.has(newStatus),
        },
      });
      toast.success(`Task moved to ${newStatus.replace(/_/g, " ")}`);
      refetch();
    } catch {
      toast.error("Failed to move task");
    } finally {
      setPendingOverride(null);
    }
  };

  const handleAction = useCallback(
    async (action: string, taskId: string) => {
      try {
        switch (action) {
          case "move-forward":
            // Determine next action based on current status
            const task = tasksRef.current.find((t) => t.id === taskId);
            if (!task) return;

            switch (task.status) {
              case TaskStatus.BACKLOG:
                // BACKLOG tasks need session before activation - PM only
                toast.info(
                  "Backlog tasks must be activated by PM with a session",
                );
                break;
              case TaskStatus.PENDING:
                await claimMutateAsync(taskId);
                toast.success("Task claimed");
                break;
              case TaskStatus.CLAIMED:
                await startMutateAsync(taskId);
                toast.success("Task started");
                break;
              case TaskStatus.IN_PROGRESS:
                await submitQaMutateAsync({ taskId });
                toast.success("Submitted for QA");
                break;
              case TaskStatus.BLOCKED:
                await unblockMutateAsync(taskId);
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
    },
    [
      claimMutateAsync,
      startMutateAsync,
      submitQaMutateAsync,
      unblockMutateAsync,
      refetch,
    ],
  );

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
    <div className="flex h-full flex-col gap-6">
      {/* Header */}
      <div className="flex items-center justify-between shrink-0">
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
              <Tooltip>
                <TooltipTrigger asChild>
                  <SelectTrigger className="w-auto min-w-28">
                    <SelectValue placeholder="Team" />
                  </SelectTrigger>
                </TooltipTrigger>
                <TooltipContent>Filter the board by team</TooltipContent>
              </Tooltip>
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
        </div>
      </div>

      {/* Board */}
      <DndContext
        sensors={sensors}
        onDragStart={handleDragStart}
        onDragEnd={handleDragEnd}
      >
        {/* Mobile: single-column view with prev/next navigator (hidden on sm+) */}
        <div className="sm:hidden flex flex-1 min-h-0 flex-col">
          <div className="flex items-center gap-2 mb-4 shrink-0">
            {/* Buttons wrapped in a span, not tipped directly: disabled sets
                pointer-events:none on the Button and would swallow hover. */}
            <HelpTip label="Swipe to the previous lifecycle stage — each column is one task status">
              <span
                className="shrink-0"
                tabIndex={activeColumnIndex === 0 ? undefined : 0}
              >
                <Button
                  variant="outline"
                  size="icon"
                  className="min-h-11 min-w-11 shrink-0"
                  onClick={() => setActiveColumnIndex((i) => Math.max(0, i - 1))}
                  disabled={activeColumnIndex === 0}
                  aria-label="Previous column"
                >
                  <ChevronLeft className="h-5 w-5" />
                </Button>
              </span>
            </HelpTip>
            <p className="flex-1 text-center text-sm font-semibold">
              <span className="text-muted-foreground font-normal text-xs mr-1">
                {activeColumnIndex + 1}/{columns.length}
              </span>
              {columns[activeColumnIndex]?.title}
            </p>
            <HelpTip label="Swipe to the next lifecycle stage — each column is one task status">
              <span
                className="shrink-0"
                tabIndex={
                  activeColumnIndex === columns.length - 1 ? undefined : 0
                }
              >
                <Button
                  variant="outline"
                  size="icon"
                  className="min-h-11 min-w-11 shrink-0"
                  onClick={() =>
                    setActiveColumnIndex((i) =>
                      Math.min(columns.length - 1, i + 1),
                    )
                  }
                  disabled={activeColumnIndex === columns.length - 1}
                  aria-label="Next column"
                >
                  <ChevronRight className="h-5 w-5" />
                </Button>
              </span>
            </HelpTip>
          </div>
          {columns[activeColumnIndex] && (
            <KanbanColumn
              key={columns[activeColumnIndex].id}
              id={columns[activeColumnIndex].id}
              title={columns[activeColumnIndex].title}
              status={columns[activeColumnIndex].status}
              tasks={tasksByStatus[columns[activeColumnIndex].status] || []}
              color={columns[activeColumnIndex].color}
              isLoading={isLoading}
              onAction={handleAction}
              showQaActions={showQaActions}
              className="w-full sm:w-full"
            />
          )}
        </div>

        {/* Desktop: horizontal scrolling layout (shown at sm+, i.e. >= 640px) */}
        <div className="hidden sm:flex gap-4 overflow-x-auto pb-4 flex-1 min-h-0">
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
          {activeTask ? <KanbanCard task={activeTask} isDragging /> : null}
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

      <AlertDialog
        open={pendingOverride !== null}
        onOpenChange={(open) => {
          if (!open) setPendingOverride(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Override lifecycle preconditions?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This move routes through the admin status-override, which skips
              the in-band lifecycle gate. The following preconditions would be
              skipped:
            </AlertDialogDescription>
          </AlertDialogHeader>
          <ul className="text-sm text-muted-foreground list-disc pl-6 space-y-1">
            {pendingOverride?.skipped.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={handleOverrideConfirm}>
              Override &amp; move
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

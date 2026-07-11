"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useTaskLifecycle, useDeleteTask } from "@/hooks/use-tasks";
import { Task, TaskStatus } from "@/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
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
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  MoreHorizontal,
  Play,
  Pause,
  CheckCircle,
  XCircle,
  Pencil,
  Trash2,
  Clock,
} from "lucide-react";
import { toast } from "sonner";
import { EditTaskDialog } from "./edit-task-dialog";
import { RequiredNotesDialog } from "./task-detail/task-action-dialogs";

interface TaskActionsProps {
  task: Task;
  showEdit?: boolean;
  showDelete?: boolean;
  redirectOnDelete?: boolean;
}

export function TaskActions({
  task,
  showEdit = true,
  showDelete = true,
  redirectOnDelete = false,
}: TaskActionsProps) {
  const router = useRouter();
  const lifecycle = useTaskLifecycle();
  const deleteTask = useDeleteTask();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [completeOpen, setCompleteOpen] = useState(false);
  const [cancelOpen, setCancelOpen] = useState(false);

  const handleAction = async (action: string) => {
    try {
      switch (action) {
        case "claim":
          await lifecycle.claim.mutateAsync(task.id);
          toast.success("Task claimed");
          break;
        case "start":
          await lifecycle.start.mutateAsync(task.id);
          toast.success("Task started");
          break;
        case "pause":
          await lifecycle.pause.mutateAsync(task.id);
          toast.success("Task paused");
          break;
        case "resume":
          await lifecycle.resume.mutateAsync(task.id);
          toast.success("Task resumed");
          break;
        case "complete":
          setCompleteOpen(true);
          break;
        case "cancel":
          setCancelOpen(true);
          break;
        case "reopen":
          await lifecycle.reopen.mutateAsync(task.id);
          toast.success("Task reopened");
          break;
        case "unblock":
          await lifecycle.unblock.mutateAsync(task.id);
          toast.success("Task unblocked");
          break;
      }
    } catch {
      toast.error("Action failed: " + action);
    }
  };

  const handleComplete = async (justification: string) => {
    try {
      await lifecycle.complete.mutateAsync({ taskId: task.id, justification });
      toast.success("Task completed");
      setCompleteOpen(false);
    } catch {
      toast.error("Action failed: complete");
    }
  };

  const handleCancel = async (reason: string) => {
    try {
      await lifecycle.cancel.mutateAsync({ taskId: task.id, reason });
      toast.success("Task cancelled");
      setCancelOpen(false);
    } catch {
      toast.error("Action failed: cancel");
    }
  };

  const handleDelete = async () => {
    try {
      await deleteTask.mutateAsync(task.id);
      toast.success("Task deleted");
      setDeleteOpen(false);
      if (redirectOnDelete) {
        router.push("/tasks");
      }
    } catch {
      toast.error("Failed to delete task");
    }
  };

  // Check if task can be edited (everything except completed - preserve finished work history)
  const canEdit = task.status !== TaskStatus.COMPLETED;

  // Delete is always available - user decides what to clean up
  const canDelete = true;

  // Check if task is in backlog (needs PM activation)
  const isBacklog = task.status === TaskStatus.BACKLOG;

  const actionsLabel = "Open task actions menu";

  return (
    <>
      <DropdownMenu>
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="icon"
                  aria-label={actionsLabel}
                  title={actionsLabel}
                >
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
            </TooltipTrigger>
            <TooltipContent>{actionsLabel}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
        <DropdownMenuContent align="end">
          {/* Edit option */}
          {showEdit && canEdit && (
            <DropdownMenuItem onClick={() => setEditOpen(true)}>
              <Pencil className="h-4 w-4 mr-2" />
              Edit
            </DropdownMenuItem>
          )}

          {/* Backlog status info */}
          {isBacklog && (
            <>
              <DropdownMenuItem disabled className="text-muted-foreground">
                <Clock className="h-4 w-4 mr-2" />
                Awaiting PM Activation
              </DropdownMenuItem>
              <DropdownMenuSeparator />
            </>
          )}

          {/* Lifecycle actions */}
          {task.status === TaskStatus.PENDING && (
            <DropdownMenuItem onClick={() => handleAction("claim")}>
              <Play className="h-4 w-4 mr-2" />
              Claim
            </DropdownMenuItem>
          )}
          {task.status === TaskStatus.CLAIMED && (
            <DropdownMenuItem onClick={() => handleAction("start")}>
              <Play className="h-4 w-4 mr-2" />
              Start
            </DropdownMenuItem>
          )}
          {task.status === TaskStatus.IN_PROGRESS && (
            <DropdownMenuItem onClick={() => handleAction("pause")}>
              <Pause className="h-4 w-4 mr-2" />
              Pause
            </DropdownMenuItem>
          )}
          {task.status === TaskStatus.PAUSED && (
            <DropdownMenuItem onClick={() => handleAction("resume")}>
              <Play className="h-4 w-4 mr-2" />
              Resume
            </DropdownMenuItem>
          )}
          {task.status === TaskStatus.BLOCKED && (
            <DropdownMenuItem onClick={() => handleAction("unblock")}>
              <Play className="h-4 w-4 mr-2" />
              Unblock
            </DropdownMenuItem>
          )}
          {task.status === TaskStatus.CANCELLED && (
            <DropdownMenuItem onClick={() => handleAction("reopen")}>
              <Play className="h-4 w-4 mr-2" />
              Reopen
            </DropdownMenuItem>
          )}

          {task.status !== TaskStatus.COMPLETED &&
            task.status !== TaskStatus.CANCELLED &&
            task.status !== TaskStatus.BACKLOG && (
              <>
                <DropdownMenuSeparator />

                <DropdownMenuItem onClick={() => handleAction("complete")}>
                  <CheckCircle className="h-4 w-4 mr-2" />
                  Complete
                </DropdownMenuItem>
                <DropdownMenuItem
                  onClick={() => handleAction("cancel")}
                  className="text-orange-600"
                >
                  <XCircle className="h-4 w-4 mr-2" />
                  Cancel
                </DropdownMenuItem>
              </>
            )}

          {/* Cancel for backlog tasks (allowed - can remove from backlog) */}
          {isBacklog && (
            <DropdownMenuItem
              onClick={() => handleAction("cancel")}
              className="text-orange-600"
            >
              <XCircle className="h-4 w-4 mr-2" />
              Cancel
            </DropdownMenuItem>
          )}

          {/* Delete option */}
          {showDelete && canDelete && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setDeleteOpen(true)}
                className="text-red-600"
              >
                <Trash2 className="h-4 w-4 mr-2" />
                Delete
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      {/* Edit Dialog */}
      <EditTaskDialog task={task} open={editOpen} onOpenChange={setEditOpen} />

      {/* Complete Dialog */}
      <RequiredNotesDialog
        open={completeOpen}
        onOpenChange={setCompleteOpen}
        onConfirm={handleComplete}
        isPending={lifecycle.complete.isPending}
        title="Approve & Complete"
        description="Record why this work is approved and complete. This note is the permanent audit record and is required."
        label="Completion justification"
        placeholder="Approving and completing because..."
        minChars={20}
        confirmLabel="Approve & Complete"
      />

      {/* Cancel Dialog */}
      <RequiredNotesDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        onConfirm={handleCancel}
        isPending={lifecycle.cancel.isPending}
        title="Cancel Task"
        description="Record why this task is being cancelled. This note is the permanent audit record and is required."
        label="Cancellation reason"
        placeholder="Cancelling because..."
        minChars={10}
        confirmLabel="Cancel Task"
        destructive
      />

      {/* Delete Confirmation */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Task?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &quot;{task.title}&quot;. This action
              cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-red-600 hover:bg-red-700"
            >
              {deleteTask.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

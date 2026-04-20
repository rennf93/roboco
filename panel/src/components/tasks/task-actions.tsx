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
import { MoreHorizontal, Play, Pause, CheckCircle, XCircle, Pencil, Trash2, Clock, MessageSquare } from "lucide-react";
import { toast } from "sonner";
import { EditTaskDialog } from "./edit-task-dialog";

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
          await lifecycle.complete.mutateAsync(task.id);
          toast.success("Task completed");
          break;
        case "cancel":
          await lifecycle.cancel.mutateAsync(task.id);
          toast.success("Task cancelled");
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
  const hasSessions = task.sessions && task.sessions.length > 0;

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="icon">
            <MoreHorizontal className="h-4 w-4" />
          </Button>
        </DropdownMenuTrigger>
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
              {!hasSessions && (
                <DropdownMenuItem disabled className="text-muted-foreground text-xs">
                  <MessageSquare className="h-4 w-4 mr-2" />
                  Needs session created
                </DropdownMenuItem>
              )}
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
              <DropdownMenuItem onClick={() => handleAction("cancel")} className="text-orange-600">
                <XCircle className="h-4 w-4 mr-2" />
                Cancel
              </DropdownMenuItem>
            </>
          )}

          {/* Cancel for backlog tasks (allowed - can remove from backlog) */}
          {isBacklog && (
            <DropdownMenuItem onClick={() => handleAction("cancel")} className="text-orange-600">
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
      <EditTaskDialog
        task={task}
        open={editOpen}
        onOpenChange={setEditOpen}
      />

      {/* Delete Confirmation */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete Task?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete &quot;{task.title}&quot;. This action cannot be undone.
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

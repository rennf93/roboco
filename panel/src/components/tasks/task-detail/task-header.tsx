"use client";

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { Task, TaskStatus, Team } from "@/types";
import { useDeleteTask, useUpdateTask, useTaskValidTransitions } from "@/hooks/use-tasks";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
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
  ArrowLeft,
  MoreVertical,
  Play,
  Pause,
  CheckCircle,
  XCircle,
  AlertTriangle,
  Trash2,
  GitBranch,
  GitMerge,
  GitPullRequest,
  FileCheck,
  Send,
  ThumbsUp,
  ThumbsDown,
} from "lucide-react";
import { toast } from "sonner";
import Link from "next/link";
import { TaskTypeBadge } from "../task-type-badge";

// Status badge colors
const statusColors: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "bg-slate-100 text-slate-600 dark:bg-slate-800 dark:text-slate-400",
  [TaskStatus.PENDING]: "bg-gray-100 text-gray-700 dark:bg-gray-800 dark:text-gray-300",
  [TaskStatus.CLAIMED]: "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-300",
  [TaskStatus.IN_PROGRESS]: "bg-blue-200 text-blue-800 dark:bg-blue-800 dark:text-blue-200",
  [TaskStatus.BLOCKED]: "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-300",
  [TaskStatus.PAUSED]: "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-300",
  [TaskStatus.VERIFYING]: "bg-purple-100 text-purple-700 dark:bg-purple-900 dark:text-purple-300",
  [TaskStatus.NEEDS_REVISION]: "bg-orange-100 text-orange-700 dark:bg-orange-900 dark:text-orange-300",
  [TaskStatus.AWAITING_QA]: "bg-yellow-200 text-yellow-800 dark:bg-yellow-800 dark:text-yellow-200",
  [TaskStatus.AWAITING_DOCUMENTATION]: "bg-indigo-100 text-indigo-700 dark:bg-indigo-900 dark:text-indigo-300",
  [TaskStatus.AWAITING_PM_REVIEW]: "bg-orange-200 text-orange-800 dark:bg-orange-800 dark:text-orange-200",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "bg-amber-200 text-amber-800 dark:bg-amber-800 dark:text-amber-200",
  [TaskStatus.COMPLETED]: "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-300",
  [TaskStatus.CANCELLED]: "bg-gray-200 text-gray-600 dark:bg-gray-700 dark:text-gray-400",
};

const statusLabels: Record<TaskStatus, string> = {
  [TaskStatus.BACKLOG]: "Backlog",
  [TaskStatus.PENDING]: "Pending",
  [TaskStatus.CLAIMED]: "Claimed",
  [TaskStatus.IN_PROGRESS]: "In Progress",
  [TaskStatus.BLOCKED]: "Blocked",
  [TaskStatus.PAUSED]: "Paused",
  [TaskStatus.VERIFYING]: "Verifying",
  [TaskStatus.NEEDS_REVISION]: "Needs Revision",
  [TaskStatus.AWAITING_QA]: "Awaiting QA",
  [TaskStatus.AWAITING_DOCUMENTATION]: "Awaiting Docs",
  [TaskStatus.AWAITING_PM_REVIEW]: "PM Review",
  [TaskStatus.AWAITING_CEO_APPROVAL]: "CEO Approval",
  [TaskStatus.COMPLETED]: "Completed",
  [TaskStatus.CANCELLED]: "Cancelled",
};

interface TaskHeaderProps {
  task: Task;
  onAction?: (action: string) => void;
}

export function TaskHeader({ task, onAction }: TaskHeaderProps) {
  const router = useRouter();
  const deleteTask = useDeleteTask();
  const updateTask = useUpdateTask();
  // Fetch valid next statuses from GET /tasks/{id}/valid-transitions.
  // Falls back to [] while loading or on error — the Select is disabled during loading.
  const { data: validTransitionsData, isLoading: isTransitionsLoading } = useTaskValidTransitions(task.id, task.status);
  // Exclude the current status: it is always rendered first (below), so a stale
  // cache or a backend list that re-includes it would duplicate the item. Radix
  // Select requires unique item values, so a duplicate also garbles the trigger
  // label (it renders as e.g. "Completed Completed").
  const nextStatuses: TaskStatus[] = (validTransitionsData ?? []).filter(
    (s) => s !== task.status
  );
  // God-mode: the panel always acts as the CEO/operator, so the dropdown also
  // offers every OTHER status as a forced admin override — letting the CEO
  // recover a task wedged in a state with no valid in-band move (e.g. a task
  // stuck in `blocked` whose PR can never merge, or reopening a `cancelled`
  // task). These route through the audited admin-override path, not lifecycle
  // verbs. See handleStatusChange.
  const overrideStatuses: TaskStatus[] = Object.values(TaskStatus).filter(
    (s) => s !== task.status && !nextStatuses.includes(s)
  );
  const [deleteOpen, setDeleteOpen] = useState(false);

  // Inline editing states
  const [editingTitle, setEditingTitle] = useState(false);
  const [localTitleValue, setLocalTitleValue] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);

  // Display prop value when not editing, local value when editing
  const titleValue = editingTitle ? localTitleValue : task.title;
  const setTitleValue = (value: string) => setLocalTitleValue(value);

  // Start editing - copy current prop value to local state
  const startEditingTitle = () => {
    setLocalTitleValue(task.title);
    setEditingTitle(true);
  };

  // Focus input when editing starts
  useEffect(() => {
    if (editingTitle && titleInputRef.current) {
      titleInputRef.current.focus();
      titleInputRef.current.select();
    }
  }, [editingTitle]);

  const handleAction = (action: string) => {
    onAction?.(action);
  };

  const handleDelete = async () => {
    try {
      await deleteTask.mutateAsync(task.id);
      toast.success("Task deleted");
      setDeleteOpen(false);
      router.push("/tasks");
    } catch {
      toast.error("Failed to delete task");
    }
  };

  const handleTitleSave = async () => {
    if (!titleValue.trim() || titleValue === task.title) {
      setTitleValue(task.title);
      setEditingTitle(false);
      return;
    }

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { title: titleValue.trim() },
      });
      setEditingTitle(false);
    } catch {
      toast.error("Failed to update title");
      setTitleValue(task.title);
    }
  };

  const handleTitleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter") {
      handleTitleSave();
    } else if (e.key === "Escape") {
      setTitleValue(task.title);
      setEditingTitle(false);
    }
  };

  const handleStatusChange = async (newStatus: TaskStatus) => {
    // Skip if same status
    if (newStatus === task.status) return;

    // Map a target status to the lifecycle action that achieves it in-band.
    const statusToAction: Partial<Record<TaskStatus, string>> = {
      [TaskStatus.PENDING]: "reopen", // From cancelled
      [TaskStatus.CLAIMED]: "claim",
      [TaskStatus.IN_PROGRESS]: "start",
      [TaskStatus.BLOCKED]: "block",
      [TaskStatus.PAUSED]: "pause",
      [TaskStatus.VERIFYING]: "verify",
      [TaskStatus.AWAITING_QA]: "submit-qa",
      [TaskStatus.AWAITING_DOCUMENTATION]: "pass-qa",
      [TaskStatus.COMPLETED]: "complete",
      [TaskStatus.CANCELLED]: "cancel",
    };

    const action = statusToAction[newStatus];
    // Prefer the real lifecycle action when this is a valid in-band transition —
    // it runs the proper side effects (e.g. `complete` merges the PR).
    if (action && nextStatuses.includes(newStatus) && onAction) {
      onAction(action);
      return;
    }

    // God-mode override: force ANY status, even from a state with no valid
    // in-band move (a task wedged in `blocked` whose PR can never merge, or
    // reopening a `cancelled` task). Audited via PATCH /tasks/{id} {status} ->
    // admin_set_status. No PR merge / lifecycle side effects fire — this is a
    // pure, operator-driven state correction the CEO is entitled to make.
    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { status: newStatus },
      });
      toast.success(`Status forced to ${statusLabels[newStatus]}`);
    } catch {
      toast.error(`Failed to set status to ${statusLabels[newStatus]}`);
    }
  };

  const handleTeamChange = async (newTeam: Team) => {
    if (newTeam === task.team) return;

    try {
      await updateTask.mutateAsync({
        taskId: task.id,
        updates: { team: newTeam },
      });
      toast.success("Team updated");
    } catch {
      toast.error("Failed to update team");
    }
  };

  // Determine available lifecycle actions based on current status
  const getAvailableActions = () => {
    const actions: Array<{ label: string; action: string; icon?: React.ReactNode }> = [];

    switch (task.status) {
      case TaskStatus.PENDING:
        actions.push({ label: "Claim Task", action: "claim", icon: <Play className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.CLAIMED:
        actions.push({ label: "Start Work", action: "start", icon: <Play className="h-4 w-4 mr-2" /> });
        // PM can create branch for tasks without branch (all tasks follow git workflow)
        if (!task.branch_name) {
          actions.push({ label: "Create Branch", action: "create-branch", icon: <GitBranch className="h-4 w-4 mr-2" /> });
        }
        break;
      case TaskStatus.IN_PROGRESS:
        actions.push({ label: "Pause", action: "pause", icon: <Pause className="h-4 w-4 mr-2" /> });
        actions.push({ label: "Mark Blocked", action: "block", icon: <AlertTriangle className="h-4 w-4 mr-2" /> });
        // Create PR action for tasks with branch but no PR
        if (task.branch_name && !task.pr_number) {
          actions.push({ label: "Create PR", action: "create-pr", icon: <GitPullRequest className="h-4 w-4 mr-2" /> });
        }
        actions.push({ label: "Self Verify", action: "verify", icon: <CheckCircle className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.BLOCKED:
        actions.push({ label: "Unblock", action: "unblock", icon: <Play className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.PAUSED:
        actions.push({ label: "Resume", action: "resume", icon: <Play className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.VERIFYING:
        actions.push({ label: "Submit for QA", action: "submit-qa", icon: <CheckCircle className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.AWAITING_QA:
        actions.push({ label: "Pass QA", action: "pass-qa", icon: <CheckCircle className="h-4 w-4 mr-2" /> });
        actions.push({ label: "Fail QA", action: "fail-qa", icon: <XCircle className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.AWAITING_DOCUMENTATION:
        // Parallel phase - show status and available actions
        if (!task.docs_complete) {
          actions.push({ label: "Mark Docs Complete", action: "docs-complete", icon: <FileCheck className="h-4 w-4 mr-2" /> });
        }
        // Only show submit for PM review when both docs and PR are ready
        if (task.docs_complete && task.pr_created) {
          actions.push({ label: "Submit for PM Review", action: "submit-pm-review", icon: <Send className="h-4 w-4 mr-2" /> });
        }
        break;
      case TaskStatus.AWAITING_PM_REVIEW:
        actions.push({ label: "Approve & Complete", action: "complete", icon: <ThumbsUp className="h-4 w-4 mr-2" /> });
        actions.push({ label: "Escalate to CEO", action: "escalate-to-ceo", icon: <Send className="h-4 w-4 mr-2" /> });
        actions.push({ label: "Request Changes", action: "request-changes", icon: <ThumbsDown className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.AWAITING_CEO_APPROVAL:
        actions.push({ label: "Approve & Merge", action: "approve-and-merge", icon: <ThumbsUp className="h-4 w-4 mr-2" /> });
        actions.push({ label: "Request Changes", action: "ceo-reject", icon: <ThumbsDown className="h-4 w-4 mr-2" /> });
        break;
      case TaskStatus.CANCELLED:
        actions.push({ label: "Reopen Task", action: "reopen", icon: <Play className="h-4 w-4 mr-2" /> });
        break;
    }

    // Cancel is always available for non-terminal states
    if (task.status !== TaskStatus.COMPLETED && task.status !== TaskStatus.CANCELLED) {
      actions.push({ label: "Cancel Task", action: "cancel", icon: <XCircle className="h-4 w-4 mr-2" /> });
    }

    // Merge PR is available whenever task.pr_number is set and the task is not in a terminal state
    if (task.pr_number && task.status !== TaskStatus.COMPLETED && task.status !== TaskStatus.CANCELLED) {
      actions.push({ label: "Merge PR", action: "merge-pr", icon: <GitMerge className="h-4 w-4 mr-2" /> });
    }

    return actions;
  };

  const actions = getAvailableActions();

  return (
    <div className="flex items-center justify-between border-b pb-4">
      <div className="flex items-center gap-4">
        <Link href="/tasks">
          <Button variant="ghost" size="icon">
            <ArrowLeft className="h-5 w-5" />
          </Button>
        </Link>
        <div>
          {/* Title, Status, and Team - all on same row */}
          <div className="flex items-center gap-2 flex-wrap">
            {editingTitle ? (
              <div className="flex items-center gap-2">
                <Input
                  ref={titleInputRef}
                  value={titleValue}
                  onChange={(e) => setTitleValue(e.target.value)}
                  onKeyDown={handleTitleKeyDown}
                  onBlur={handleTitleSave}
                  className="text-2xl font-bold h-auto py-1 px-2 w-full"
                  disabled={updateTask.isPending}
                />
              </div>
            ) : (
              <h1
                className="text-2xl font-bold cursor-pointer hover:bg-muted/50 px-2 py-1 -mx-2 rounded transition-colors"
                onClick={startEditingTitle}
                title="Click to edit"
              >
                Task #{task.id}: {task.title}
              </h1>
            )}

            {/* Status Dropdown — only current status + valid next statuses from backend */}
            <Select value={task.status} onValueChange={(v) => handleStatusChange(v as TaskStatus)}>
              <SelectTrigger
                className={`w-auto h-7 text-xs font-medium border-0 ${statusColors[task.status]}`}
                disabled={isTransitionsLoading}
              >
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {/* Always render the current status first so the trigger value is always present */}
                <SelectItem key={task.status} value={task.status}>
                  <span className={`px-2 py-0.5 rounded ${statusColors[task.status]}`}>
                    {statusLabels[task.status]}
                  </span>
                </SelectItem>
                {/* nextStatuses sourced exclusively from useTaskValidTransitions
                    (GET /tasks/{id}/valid-transitions) — no local fallback array */}
                {nextStatuses.map((status) => (
                  <SelectItem key={status} value={status}>
                    <span className={`px-2 py-0.5 rounded ${statusColors[status]}`}>
                      {statusLabels[status]}
                    </span>
                  </SelectItem>
                ))}
                {/* God-mode: every remaining status as an audited admin
                    override (no valid in-band transition). Marked "force" so
                    the operator knows it bypasses the normal lifecycle. */}
                {overrideStatuses.map((status) => (
                  <SelectItem key={status} value={status}>
                    <span className={`px-2 py-0.5 rounded ${statusColors[status]}`}>
                      {statusLabels[status]}
                    </span>
                    <span className="ml-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                      force
                    </span>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Team Dropdown - same row */}
            <span className="text-muted-foreground">|</span>
            <Select value={task.team} onValueChange={(v) => handleTeamChange(v as Team)}>
              <SelectTrigger className="w-auto h-7 text-sm text-muted-foreground border-0 bg-transparent hover:bg-muted/50 px-2">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {Object.values(Team).map((team) => (
                  <SelectItem key={team} value={team}>
                    {team.replace(/_/g, " ")} Team
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Task Type Badge */}
            {task.task_type && (
              <>
                <span className="text-muted-foreground">|</span>
                <TaskTypeBadge type={task.task_type} />
              </>
            )}
          </div>
        </div>
      </div>

      {/* Actions Menu */}
      <div className="flex items-center gap-2">
        {actions.length > 0 && (
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="outline">
                Actions
                <MoreVertical className="h-4 w-4 ml-2" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end">
              {/* Lifecycle actions (non-cancel) */}
              {actions
                .filter((a) => a.action !== "cancel")
                .map((action) => (
                  <DropdownMenuItem
                    key={action.action}
                    onClick={() => handleAction(action.action)}
                  >
                    {action.icon}
                    {action.label}
                  </DropdownMenuItem>
                ))}

              {/* Cancel action */}
              {actions.some((a) => a.action === "cancel") && (
                <>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => handleAction("cancel")}
                    className="text-orange-600"
                  >
                    <XCircle className="h-4 w-4 mr-2" />
                    Cancel Task
                  </DropdownMenuItem>
                </>
              )}

              {/* Delete option */}
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setDeleteOpen(true)}
                className="text-red-600"
              >
                <Trash2 className="h-4 w-4 mr-2" />
                Delete Task
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        )}
      </div>

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
    </div>
  );
}

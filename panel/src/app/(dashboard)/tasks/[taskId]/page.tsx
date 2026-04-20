"use client";

import { use, useState } from "react";
import { useTask, useTaskLifecycle } from "@/hooks/use-tasks";
import { useProject } from "@/hooks/use-projects";
import { useCreateBranch, useCreatePR } from "@/hooks/use-git";
import { TaskHeader, TaskMetadata, TaskTabs } from "@/components/tasks/task-detail";
import {
  EscalateToCeoDialog,
  CeoRejectDialog,
  CreateBranchDialog,
  CreatePRDialog,
} from "@/components/tasks/task-detail/task-action-dialogs";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { toast } from "sonner";

interface TaskDetailPageProps {
  params: Promise<{ taskId: string }>;
}

export default function TaskDetailPage({ params }: TaskDetailPageProps) {
  const { taskId } = use(params);
  const { data: task, isLoading, error, refetch } = useTask(taskId);
  const { data: project } = useProject(task?.project_id ?? "");
  const lifecycle = useTaskLifecycle();
  const createBranch = useCreateBranch();
  const createPR = useCreatePR();

  // Dialog states
  const [escalateDialogOpen, setEscalateDialogOpen] = useState(false);
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [branchDialogOpen, setBranchDialogOpen] = useState(false);
  const [prDialogOpen, setPrDialogOpen] = useState(false);

  const handleAction = async (action: string) => {
    if (!task) return;

    try {
      switch (action) {
        case "claim":
          await lifecycle.claim.mutateAsync(task.id);
          toast.success("Task claimed successfully");
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
        case "block":
          await lifecycle.block.mutateAsync({ taskId: task.id });
          toast.success("Task marked as blocked");
          break;
        case "unblock":
          await lifecycle.unblock.mutateAsync(task.id);
          toast.success("Task unblocked");
          break;
        case "verify":
          await lifecycle.verify.mutateAsync(task.id);
          toast.success("Task self-verified");
          break;
        case "submit-qa":
          await lifecycle.submitQa.mutateAsync({ taskId: task.id });
          toast.success("Task submitted for QA");
          break;
        case "pass-qa":
          await lifecycle.passQa.mutateAsync({ taskId: task.id });
          toast.success("Task passed QA");
          break;
        case "fail-qa":
          await lifecycle.failQa.mutateAsync({ taskId: task.id });
          toast.success("Task failed QA");
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
        // Git workflow actions
        case "docs-complete":
          await lifecycle.docsComplete.mutateAsync(task.id);
          toast.success("Documentation marked complete");
          break;
        case "submit-pm-review":
          await lifecycle.submitPmReview.mutateAsync(task.id);
          toast.success("Submitted for PM review");
          break;
        case "ceo-approve":
          await lifecycle.ceoApprove.mutateAsync({ taskId: task.id });
          toast.success("Task approved and completed");
          break;
        case "ceo-reject":
          setRejectDialogOpen(true);
          return; // Don't refetch yet, dialog will handle it
        case "escalate-to-ceo":
          setEscalateDialogOpen(true);
          return; // Don't refetch yet, dialog will handle it
        case "request-changes":
          await lifecycle.failQa.mutateAsync({ taskId: task.id, qaNotes: "Changes requested by PM" });
          toast.success("Changes requested");
          break;
        case "create-branch":
          if (!project) {
            toast.error("Project not found - cannot create branch");
            return;
          }
          setBranchDialogOpen(true);
          return; // Don't refetch yet, dialog will handle it
        case "create-pr":
          if (!project) {
            toast.error("Project not found - cannot create PR");
            return;
          }
          setPrDialogOpen(true);
          return; // Don't refetch yet, dialog will handle it
        default:
          console.warn("Unknown action:", action);
      }
      refetch();
    } catch (err) {
      toast.error("Failed to " + action.replace("-", " ") + " task");
      console.error(err);
    }
  };

  // Dialog handlers
  const handleEscalateToCeo = async (reason: string) => {
    if (!task) return;
    try {
      await lifecycle.escalateToCeo.mutateAsync({ taskId: task.id, reason });
      toast.success("Escalated to CEO");
      setEscalateDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to escalate to CEO");
      console.error(err);
    }
  };

  const handleCeoReject = async (notes: string) => {
    if (!task) return;
    try {
      await lifecycle.ceoReject.mutateAsync({ taskId: task.id, notes });
      toast.success("Changes requested");
      setRejectDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to request changes");
      console.error(err);
    }
  };

  const handleCreateBranch = async (branchType: string) => {
    if (!task || !project) return;
    try {
      await createBranch.mutateAsync({
        project_slug: project.slug,
        task_id: task.id,
        branch_type: branchType as "feature" | "bug" | "chore" | "docs" | "hotfix",
        agent_id: "ceo", // CEO is creating the branch from the panel
      });
      toast.success("Branch created successfully");
      setBranchDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to create branch");
      console.error(err);
    }
  };

  const handleCreatePR = async (title: string, body: string) => {
    if (!task || !project) return;
    try {
      const result = await createPR.mutateAsync({
        project_slug: project.slug,
        task_id: task.id,
        title,
        body,
        agent_id: "ceo", // CEO is creating the PR from the panel
      });
      toast.success(`PR #${result.pr_number} created successfully`);
      setPrDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to create PR");
      console.error(err);
    }
  };

  // Loading state
  if (isLoading) {
    return (
      <div className="space-y-6">
        <div className="flex items-center gap-4">
          <Skeleton className="h-10 w-10" />
          <div className="space-y-2">
            <Skeleton className="h-8 w-64" />
            <Skeleton className="h-4 w-32" />
          </div>
        </div>
        <div className="grid grid-cols-4 gap-4">
          {[...Array(8)].map((_, i) => (
            <Skeleton key={i} className="h-24" />
          ))}
        </div>
        <Skeleton className="h-48" />
        <Skeleton className="h-96" />
      </div>
    );
  }

  // Error state
  if (error || !task) {
    return (
      <div className="space-y-6">
        <Link href="/tasks">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="h-4 w-4 mr-2" />
            Back to Tasks
          </Button>
        </Link>

        <Card>
          <CardContent className="pt-6">
            <div className="text-center py-12">
              <AlertTriangle className="h-16 w-16 mx-auto mb-4 text-destructive" />
              <h2 className="text-xl font-semibold mb-2">Task Not Found</h2>
              <p className="text-muted-foreground mb-6">
                {error?.message ?? "The task you're looking for doesn't exist or has been deleted."}
              </p>
              <div className="flex justify-center gap-4">
                <Button variant="outline" onClick={() => refetch()}>
                  <RefreshCw className="h-4 w-4 mr-2" />
                  Retry
                </Button>
                <Link href="/tasks">
                  <Button>View All Tasks</Button>
                </Link>
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <TaskHeader task={task} onAction={handleAction} />

      {/* Metadata Cards */}
      <TaskMetadata task={task} />

      {/* Tabbed Content */}
      <TaskTabs task={task} />

      {/* Action Dialogs */}
      <EscalateToCeoDialog
        open={escalateDialogOpen}
        onOpenChange={setEscalateDialogOpen}
        onConfirm={handleEscalateToCeo}
        isPending={lifecycle.escalateToCeo.isPending}
      />

      <CeoRejectDialog
        open={rejectDialogOpen}
        onOpenChange={setRejectDialogOpen}
        onConfirm={handleCeoReject}
        isPending={lifecycle.ceoReject.isPending}
      />

      <CreateBranchDialog
        open={branchDialogOpen}
        onOpenChange={setBranchDialogOpen}
        onConfirm={handleCreateBranch}
        isPending={createBranch.isPending}
        taskId={task.id}
      />

      <CreatePRDialog
        open={prDialogOpen}
        onOpenChange={setPrDialogOpen}
        onConfirm={handleCreatePR}
        isPending={createPR.isPending}
        defaultTitle={task.title}
      />
    </div>
  );
}

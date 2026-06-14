"use client";

import { use, useState } from "react";
import axios from "axios";
import { useTask, useTaskLifecycle } from "@/hooks/use-tasks";
import { useProject } from "@/hooks/use-projects";
import { useCreateBranch, useCreatePR, useMergePR } from "@/hooks/use-git";
import { Team, TaskStatus } from "@/types";
import { TaskHeader, TaskMetadata, TaskTabs } from "@/components/tasks/task-detail";
import { ApproveAndStartButton } from "@/components/tasks/approve-and-start-button";
import {
  EscalateToCeoDialog,
  ApproveAndMergeDialog,
  CeoApproveDialog,
  CeoRejectDialog,
  CreateBranchDialog,
  CreatePRDialog,
  RequiredNotesDialog,
} from "@/components/tasks/task-detail/task-action-dialogs";
import { Skeleton } from "@/components/ui/skeleton";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AlertTriangle, ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

interface TaskDetailPageProps {
  params: Promise<{ taskId: string }>;
}

export default function TaskDetailPage({ params }: TaskDetailPageProps) {
  const { taskId } = use(params);
  const router = useRouter();
  const { data: task, isLoading, error, refetch } = useTask(taskId);
  const { data: project } = useProject(task?.project_id ?? "");
  const lifecycle = useTaskLifecycle();
  const createBranch = useCreateBranch();
  const createPR = useCreatePR();
  const mergePR = useMergePR();

  // Dialog states
  const [escalateDialogOpen, setEscalateDialogOpen] = useState(false);
  const [approveAndMergeDialogOpen, setApproveAndMergeDialogOpen] = useState(false);
  const [approveDialogOpen, setApproveDialogOpen] = useState(false);
  const [rejectDialogOpen, setRejectDialogOpen] = useState(false);
  const [branchDialogOpen, setBranchDialogOpen] = useState(false);
  const [prDialogOpen, setPrDialogOpen] = useState(false);
  const [cancelDialogOpen, setCancelDialogOpen] = useState(false);
  const [passQaDialogOpen, setPassQaDialogOpen] = useState(false);
  const [failQaDialogOpen, setFailQaDialogOpen] = useState(false);
  const [docsCompleteDialogOpen, setDocsCompleteDialogOpen] = useState(false);
  const [submitPmReviewDialogOpen, setSubmitPmReviewDialogOpen] = useState(false);
  const [completeDialogOpen, setCompleteDialogOpen] = useState(false);

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
          setPassQaDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required note
        case "fail-qa":
          setFailQaDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required note
        case "complete":
          setCompleteDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required justification
        case "cancel":
          setCancelDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required reason
        case "reopen":
          await lifecycle.reopen.mutateAsync(task.id);
          toast.success("Task reopened");
          break;
        // Git workflow actions
        case "docs-complete":
          setDocsCompleteDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required note
        case "submit-pm-review":
          setSubmitPmReviewDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required note
        case "approve-and-merge":
          setApproveAndMergeDialogOpen(true);
          return; // Don't refetch yet — dialog handles confirmation
        case "ceo-approve":
          setApproveDialogOpen(true);
          return; // Don't refetch yet — dialog collects the required note
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
        case "merge-pr":
          if (!project) {
            toast.error("Project not found - cannot merge PR");
            return;
          }
          if (!task.pr_number) {
            toast.error("No PR number found on this task");
            return;
          }
          await mergePR.mutateAsync({
            project_slug: project.slug,
            pr_number: task.pr_number,
            task_id: task.id,
            agent_id: "ceo", // CEO is merging the PR from the panel
          });
          toast.success(`PR #${task.pr_number} merged successfully`);
          break;
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

  const handleApproveAndMerge = async () => {
    if (!task) return;
    try {
      await lifecycle.approveAndMerge.mutateAsync(task.id);
      toast.success("Task approved and PR merged");
      setApproveAndMergeDialogOpen(false);
      refetch();
    } catch (err) {
      if (axios.isAxiosError(err)) {
        const detail = (err.response?.data as { detail?: string } | undefined)?.detail ?? "";
        if (typeof detail === "string" && detail.startsWith("NO_PR")) {
          toast.error("No PR found for this task. Create a pull request before merging.");
        } else if (typeof detail === "string" && detail.startsWith("Merge failed")) {
          toast.error("Merge failed: " + (detail.slice("Merge failed".length).replace(/^[: ]+/, "") || "the merge could not be completed"));
        } else {
          toast.error("Failed to approve and merge task");
        }
      } else {
        toast.error("Failed to approve and merge task");
      }
      console.error(err);
    }
  };

  const handleCeoApprove = async (notes: string) => {
    if (!task) return;
    try {
      await lifecycle.ceoApprove.mutateAsync({ taskId: task.id, notes });
      toast.success("Task approved and completed");
      setApproveDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to approve task");
      console.error(err);
    }
  };

  const handleCancel = async (reason: string) => {
    if (!task) return;
    try {
      await lifecycle.cancel.mutateAsync({ taskId: task.id, reason });
      toast.success("Task cancelled");
      setCancelDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to cancel task");
      console.error(err);
    }
  };

  const handlePassQa = async (notes: string) => {
    if (!task) return;
    try {
      await lifecycle.passQa.mutateAsync({ taskId: task.id, qaNotes: notes });
      toast.success("Task passed QA");
      setPassQaDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to pass QA");
      console.error(err);
    }
  };

  const handleFailQa = async (notes: string) => {
    if (!task) return;
    try {
      await lifecycle.failQa.mutateAsync({ taskId: task.id, qaNotes: notes });
      toast.success("Task failed QA");
      setFailQaDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to fail QA");
      console.error(err);
    }
  };

  const handleDocsComplete = async (notes: string) => {
    if (!task) return;
    try {
      await lifecycle.docsComplete.mutateAsync({ taskId: task.id, notes });
      toast.success("Documentation marked complete");
      setDocsCompleteDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to mark documentation complete");
      console.error(err);
    }
  };

  const handleSubmitPmReview = async (notes: string) => {
    if (!task) return;
    try {
      await lifecycle.submitPmReview.mutateAsync({ taskId: task.id, notes });
      toast.success("Submitted for PM review");
      setSubmitPmReviewDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to submit for PM review");
      console.error(err);
    }
  };

  const handleComplete = async (justification: string) => {
    if (!task) return;
    try {
      await lifecycle.complete.mutateAsync({ taskId: task.id, justification });
      toast.success("Task completed");
      setCompleteDialogOpen(false);
      refetch();
    } catch (err) {
      toast.error("Failed to complete task");
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

      {/* CEO gate #1: Approve & Start a board-reviewed task. After the Board
          finishes, the orchestrator sets board_review_complete and sends the
          CEO an approval notification, but the task STAYS pending (its pending
          state is what drives Main PM dispatch on approval) — so we gate on
          PENDING here, NOT on awaiting_ceo_approval (the unrelated end-of-work
          ceo-approve flow). Gate on exactly the orchestrator's own criterion:
          pending + board_review_complete. The earlier team===BOARD / product-
          scoped predicate was too narrow — it hid this button for project-
          scoped intake tasks (which carry a project_id, team=the lead cell,
          and no product_id), leaving the CEO with no way to approve them.
          approve_and_start re-targets the task to the Main PM (team → main_pm)
          without changing status, so exclude team===MAIN_PM to hide the button
          once it's been approved. */}
      {task.status === TaskStatus.PENDING &&
        task.board_review_complete === true &&
        task.team !== Team.MAIN_PM && (
          <div className="flex justify-end gap-2">
            {task.team === Team.BOARD && (
              <Button
                variant="outline"
                onClick={() => router.push(`/prompter?redraft=${task.id}`)}
              >
                Re-draft with board feedback
              </Button>
            )}
            <ApproveAndStartButton task={task} />
          </div>
        )}

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

      <ApproveAndMergeDialog
        open={approveAndMergeDialogOpen}
        onOpenChange={setApproveAndMergeDialogOpen}
        onConfirm={handleApproveAndMerge}
        isPending={lifecycle.approveAndMerge.isPending}
      />

      <CeoApproveDialog
        open={approveDialogOpen}
        onOpenChange={setApproveDialogOpen}
        onConfirm={handleCeoApprove}
        isPending={lifecycle.ceoApprove.isPending}
      />

      <CeoRejectDialog
        open={rejectDialogOpen}
        onOpenChange={setRejectDialogOpen}
        onConfirm={handleCeoReject}
        isPending={lifecycle.ceoReject.isPending}
      />

      <RequiredNotesDialog
        open={cancelDialogOpen}
        onOpenChange={setCancelDialogOpen}
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

      <RequiredNotesDialog
        open={passQaDialogOpen}
        onOpenChange={setPassQaDialogOpen}
        onConfirm={handlePassQa}
        isPending={lifecycle.passQa.isPending}
        title="Pass QA"
        description="Record the QA review outcome. This note is the permanent audit record and is required."
        label="QA notes"
        placeholder="Verified against acceptance criteria; passing because..."
        minChars={20}
        confirmLabel="Pass QA"
      />

      <RequiredNotesDialog
        open={failQaDialogOpen}
        onOpenChange={setFailQaDialogOpen}
        onConfirm={handleFailQa}
        isPending={lifecycle.failQa.isPending}
        title="Fail QA"
        description="Record what failed QA and what needs to change. This note is the permanent audit record and is required."
        label="QA notes"
        placeholder="Failing QA because..."
        minChars={20}
        confirmLabel="Fail QA"
        destructive
      />

      <RequiredNotesDialog
        open={docsCompleteDialogOpen}
        onOpenChange={setDocsCompleteDialogOpen}
        onConfirm={handleDocsComplete}
        isPending={lifecycle.docsComplete.isPending}
        title="Mark Docs Complete"
        description="Record what documentation was written. This note is the permanent audit record and is required."
        label="Documentation notes"
        placeholder="Documented the following..."
        minChars={20}
        confirmLabel="Mark Docs Complete"
      />

      <RequiredNotesDialog
        open={submitPmReviewDialogOpen}
        onOpenChange={setSubmitPmReviewDialogOpen}
        onConfirm={handleSubmitPmReview}
        isPending={lifecycle.submitPmReview.isPending}
        title="Submit for PM Review"
        description="Record the summary for PM review. This note is the permanent audit record and is required."
        label="Review notes"
        placeholder="Submitting for PM review; summary..."
        minChars={20}
        confirmLabel="Submit for PM Review"
      />

      <RequiredNotesDialog
        open={completeDialogOpen}
        onOpenChange={setCompleteDialogOpen}
        onConfirm={handleComplete}
        isPending={lifecycle.complete.isPending}
        title="Approve & Complete"
        description="Record why this work is approved and complete. This note is the permanent audit record and is required."
        label="Completion justification"
        placeholder="Approving and completing because..."
        minChars={20}
        confirmLabel="Approve & Complete"
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

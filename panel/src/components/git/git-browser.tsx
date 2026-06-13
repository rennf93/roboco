"use client";

import { useCallback, Suspense } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useProjects } from "@/hooks/use-projects";
import {
  useGitStatus,
  useGitLog,
  useGitBranches,
  useGitDiff,
  useGitOperations,
} from "@/hooks/use-git";
import { BranchType } from "@/types/git";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { OfflineState } from "@/components/ui/offline-state";
import { GitStatusPanel } from "./git-status-panel";
import { GitBranchPanel } from "./git-branch-panel";
import { GitLogPanel } from "./git-log-panel";
import { GitDiffViewer } from "./git-diff-viewer";
import { GitActionsPanel } from "./git-actions-panel";
import { GitBranch, RefreshCw, FolderGit2 } from "lucide-react";
import { toast } from "sonner";

function GitBrowserContent() {
  const router = useRouter();
  const searchParams = useSearchParams();

  // Read state from URL
  const projectSlug = searchParams.get("project") || "";
  const taskId = searchParams.get("task") || "";

  // Fetch projects
  const { data: projects, isLoading: loadingProjects, error: projectsError, refetch: refetchProjects } = useProjects();

  // Git hooks - only enabled when project is selected
  const { data: status, isLoading: loadingStatus, refetch: refetchStatus } = useGitStatus(projectSlug, taskId, !!projectSlug);
  const { data: log, isLoading: loadingLog, refetch: refetchLog } = useGitLog(projectSlug, 20, undefined, !!projectSlug);
  const { data: branches, isLoading: loadingBranches, refetch: refetchBranches } = useGitBranches(projectSlug, true, !!projectSlug);
  const { data: stagedDiff, isLoading: loadingStagedDiff } = useGitDiff(projectSlug, true, undefined, !!projectSlug);
  const { data: unstagedDiff, isLoading: loadingUnstagedDiff } = useGitDiff(projectSlug, false, undefined, !!projectSlug);

  // Git operations
  const { commit, push, createBranch, checkout, createPR, mergePR } = useGitOperations();

  // Update URL params
  const updateParams = useCallback(
    (updates: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      Object.entries(updates).forEach(([key, value]) => {
        if (value) {
          params.set(key, value);
        } else {
          params.delete(key);
        }
      });
      const query = params.toString();
      router.push(query ? `/git?${query}` : "/git");
    },
    [router, searchParams]
  );

  const handleProjectChange = useCallback(
    (slug: string) => {
      updateParams({ project: slug || null, task: null });
    },
    [updateParams]
  );

  const handleRefresh = () => {
    refetchStatus();
    refetchLog();
    refetchBranches();
  };

  // Operation handlers
  // Note: agent_id is "ceo" because this panel is used by the CEO
  const handleCheckout = async (branch: string) => {
    try {
      await checkout.mutateAsync({
        project_slug: projectSlug,
        branch,
        agent_id: "ceo",
      });
      toast.success(`Checked out ${branch}`);
    } catch {
      toast.error("Failed to checkout branch");
    }
  };

  const handleCreateBranch = async (branchType: BranchType, branchTaskId: string) => {
    try {
      const result = await createBranch.mutateAsync({
        project_slug: projectSlug,
        task_id: branchTaskId,
        branch_type: branchType,
        agent_id: "ceo",
      });
      toast.success(`Created branch ${result.branch_name}`);
    } catch {
      toast.error("Failed to create branch");
    }
  };

  const handleCommit = async (message: string) => {
    try {
      const result = await commit.mutateAsync({
        project_slug: projectSlug,
        message,
        task_id: taskId || "manual",
        agent_id: "ceo",
      });
      toast.success(`Committed: ${result.commit_hash.slice(0, 7)}`);
    } catch {
      toast.error("Failed to commit");
    }
  };

  const handlePush = async (force?: boolean) => {
    try {
      const result = await push.mutateAsync({
        project_slug: projectSlug,
        task_id: taskId || "manual",
        agent_id: "ceo",
        force,
      });
      toast.success(`Pushed ${result.commits_pushed} commits to ${result.branch}`);
    } catch {
      toast.error("Failed to push");
    }
  };

  const handleCreatePR = async (title: string, body: string) => {
    try {
      const result = await createPR.mutateAsync({
        project_slug: projectSlug,
        task_id: taskId || "manual",
        title,
        body,
        agent_id: "ceo",
      });
      toast.success(
        <span>
          Created PR #{result.pr_number}:{" "}
          <a href={result.pr_url} target="_blank" rel="noopener noreferrer" className="underline">
            View
          </a>
        </span>
      );
    } catch {
      toast.error("Failed to create PR");
    }
  };

  const handleMergePR = async (prNumber: number) => {
    try {
      const result = await mergePR.mutateAsync({
        project_slug: projectSlug,
        pr_number: prNumber,
        task_id: taskId || "manual",
        agent_id: "ceo",
      });
      toast.success(`Merged PR #${result.pr_number} → ${result.target_branch}`);
    } catch {
      toast.error("Failed to merge PR");
    }
  };

  // Check offline
  const isOffline = projectsError && (
    projectsError.message?.includes("Network Error") ||
    (projectsError as { code?: string })?.code === "ERR_NETWORK"
  );

  if (isOffline) {
    return (
      <OfflineState
        title="Cannot Connect to Git Service"
        description="Start the RoboCo orchestrator to access git operations."
        onRetry={() => refetchProjects()}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Git Operations</h1>
          <p className="text-muted-foreground">
            Manage repositories, branches, and commits
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Project Selector */}
          <Select value={projectSlug} onValueChange={handleProjectChange}>
            <SelectTrigger className="w-64">
              <FolderGit2 className="h-4 w-4 mr-2" />
              <SelectValue placeholder="Select a project..." />
            </SelectTrigger>
            <SelectContent>
              {loadingProjects ? (
                <div className="p-2">
                  <Skeleton className="h-8 w-full" />
                </div>
              ) : (
                projects?.map((p) => (
                  <SelectItem key={p.id} value={p.slug}>
                    {p.name}
                  </SelectItem>
                ))
              )}
            </SelectContent>
          </Select>
          {projectSlug && (
            <Button variant="outline" onClick={handleRefresh}>
              <RefreshCw className="h-4 w-4 mr-2" />
              Refresh
            </Button>
          )}
        </div>
      </div>

      {/* No Project Selected */}
      {!projectSlug && (
        <Card>
          <CardContent className="py-16 text-center">
            <GitBranch className="h-16 w-16 mx-auto mb-4 text-muted-foreground/50" />
            <h3 className="text-lg font-medium mb-2">Select a Project</h3>
            <p className="text-sm text-muted-foreground">
              Choose a project from the dropdown to view git status and perform operations
            </p>
          </CardContent>
        </Card>
      )}

      {/* Git Dashboard */}
      {projectSlug && (
        <div className="grid grid-cols-12 gap-6">
          {/* Left Column - Status & Actions */}
          <div className="col-span-12 lg:col-span-3 space-y-4">
            <GitStatusPanel status={status} isLoading={loadingStatus} />
            <GitActionsPanel
              status={status}
              projectSlug={projectSlug}
              taskId={taskId}
              agentId="pm"
              onCommit={handleCommit}
              onPush={handlePush}
              onCreatePR={handleCreatePR}
              onMergePR={handleMergePR}
              isCommitting={commit.isPending}
              isPushing={push.isPending}
              isCreatingPR={createPR.isPending}
              isMerging={mergePR.isPending}
            />
          </div>

          {/* Middle Column - Branches & Log */}
          <div className="col-span-12 lg:col-span-4 space-y-4">
            <GitBranchPanel
              branches={branches}
              isLoading={loadingBranches}
              onCheckout={handleCheckout}
              onCreateBranch={handleCreateBranch}
              isCheckingOut={checkout.isPending}
              isCreating={createBranch.isPending}
            />
            <GitLogPanel log={log} isLoading={loadingLog} />
          </div>

          {/* Right Column - Diff Viewer */}
          <div className="col-span-12 lg:col-span-5">
            <GitDiffViewer
              stagedDiff={stagedDiff}
              unstagedDiff={unstagedDiff}
              isLoadingStaged={loadingStagedDiff}
              isLoadingUnstaged={loadingUnstagedDiff}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// Loading skeleton
function GitBrowserSkeleton() {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <Skeleton className="h-9 w-48 mb-2" />
          <Skeleton className="h-5 w-64" />
        </div>
        <Skeleton className="h-10 w-64" />
      </div>
      <div className="grid grid-cols-12 gap-6">
        <div className="col-span-12 lg:col-span-3 space-y-4">
          <Skeleton className="h-48 w-full" />
          <Skeleton className="h-40 w-full" />
        </div>
        <div className="col-span-12 lg:col-span-4 space-y-4">
          <Skeleton className="h-64 w-full" />
          <Skeleton className="h-80 w-full" />
        </div>
        <div className="col-span-12 lg:col-span-5">
          <Skeleton className="h-96 w-full" />
        </div>
      </div>
    </div>
  );
}

// Wrap in Suspense for useSearchParams
export function GitBrowser() {
  return (
    <Suspense fallback={<GitBrowserSkeleton />}>
      <GitBrowserContent />
    </Suspense>
  );
}

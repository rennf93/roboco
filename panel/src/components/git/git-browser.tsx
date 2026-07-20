"use client";

import { Suspense } from "react";
import { Card, CardContent } from "@/components/ui/card";
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
import { GitBranch, FolderGit2 } from "lucide-react";
import { useGitBrowser } from "@/hooks/use-git-browser";
import { HelpTip } from "@/components/ui/help-tip";

function GitBrowserContent() {
  const {
    projectSlug,
    taskId,
    projects,
    loadingProjects,
    defaultBranch,
    status,
    loadingStatus,
    log,
    loadingLog,
    branches,
    loadingBranches,
    stagedDiff,
    loadingStagedDiff,
    unstagedDiff,
    loadingUnstagedDiff,
    isOffline,
    refresh,
    handleProjectChange,
    handleCheckout,
    handleCreateBranch,
    handleCommit,
    handlePush,
    handleCreatePR,
    handleMergePR,
    handlePull,
    handleFetch,
    handleRebase,
    handleCleanupBranches,
    isCommitting,
    isPushing,
    isCreatingPR,
    isMerging,
    isPulling,
    isFetching,
    isRebasing,
    isCheckingOut,
    isCreatingBranch,
    isCleaningUpBranches,
  } = useGitBrowser();

  if (isOffline) {
    return (
      <OfflineState
        title="Cannot Connect to Git Service"
        description="Start the RoboCo orchestrator to access git operations."
        onRetry={() => void refresh()}
      />
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Git Operations</h1>
          <p className="text-muted-foreground">
            Manage repositories, branches, and commits
          </p>
        </div>
        <div className="flex items-center gap-2">
          {/* Project Selector */}
          <HelpTip label="Switches every panel below (status, branches, log, diff) to this project's clone on disk.">
            <span className="w-full sm:w-64">
              <Select value={projectSlug} onValueChange={handleProjectChange}>
                <SelectTrigger className="w-full sm:w-64">
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
            </span>
          </HelpTip>
        </div>
      </div>

      {/* No Project Selected */}
      {!projectSlug && (
        <Card>
          <CardContent className="py-16 text-center">
            <GitBranch className="h-16 w-16 mx-auto mb-4 text-muted-foreground/50" />
            <h3 className="text-lg font-medium mb-2">Select a Project</h3>
            <p className="text-sm text-muted-foreground">
              Choose a project from the dropdown to view git status and perform
              operations
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
              defaultBranch={defaultBranch}
              onCommit={handleCommit}
              onPush={handlePush}
              onCreatePR={handleCreatePR}
              onMergePR={handleMergePR}
              onPull={handlePull}
              onFetch={handleFetch}
              onRebase={handleRebase}
              onCleanupBranches={handleCleanupBranches}
              isCommitting={isCommitting}
              isPushing={isPushing}
              isCreatingPR={isCreatingPR}
              isMerging={isMerging}
              isPulling={isPulling}
              isFetching={isFetching}
              isRebasing={isRebasing}
              isCleaningUpBranches={isCleaningUpBranches}
            />
          </div>

          {/* Middle Column - Branches & Log */}
          <div className="col-span-12 lg:col-span-4 space-y-4">
            <GitBranchPanel
              branches={branches}
              isLoading={loadingBranches}
              onCheckout={handleCheckout}
              onCreateBranch={handleCreateBranch}
              isCheckingOut={isCheckingOut}
              isCreating={isCreatingBranch}
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

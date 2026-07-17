"use client";

import { useCallback, useEffect, useRef } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { toast } from "sonner";
import { useProjects } from "@/hooks/use-projects";
import {
  useGitStatus,
  useGitLog,
  useGitBranches,
  useGitDiff,
  useGitOperations,
} from "@/hooks/use-git";
import { usePageRefresh } from "@/hooks/use-page-refresh";
import { getErrorMessage } from "@/lib/api/client";
import type { BranchType } from "@/types/git";

export interface UseGitBrowserResult {
  projectSlug: string;
  taskId: string;
  projects: ReturnType<typeof useProjects>["data"];
  loadingProjects: boolean;
  status: ReturnType<typeof useGitStatus>["data"];
  loadingStatus: boolean;
  log: ReturnType<typeof useGitLog>["data"];
  loadingLog: boolean;
  branches: ReturnType<typeof useGitBranches>["data"];
  loadingBranches: boolean;
  stagedDiff: ReturnType<typeof useGitDiff>["data"];
  loadingStagedDiff: boolean;
  unstagedDiff: ReturnType<typeof useGitDiff>["data"];
  loadingUnstagedDiff: boolean;
  isOffline: boolean;
  refresh: () => Promise<void>;
  handleProjectChange: (slug: string) => void;
  handleCheckout: (branch: string) => Promise<void>;
  handleCreateBranch: (
    branchType: BranchType,
    branchTaskId: string,
  ) => Promise<void>;
  handleCommit: (message: string) => Promise<void>;
  handlePush: (force?: boolean) => Promise<void>;
  handleCreatePR: (title: string, body: string) => Promise<void>;
  handleMergePR: (prNumber: number) => Promise<void>;
  handlePull: () => Promise<void>;
  handleFetch: () => Promise<void>;
  handleRebase: (targetBranch: string) => Promise<void>;
  handleCleanupBranches: () => Promise<void>;
  isCommitting: boolean;
  isPushing: boolean;
  isCreatingPR: boolean;
  isMerging: boolean;
  isPulling: boolean;
  isFetching: boolean;
  isRebasing: boolean;
  isCheckingOut: boolean;
  isCreatingBranch: boolean;
  isCleaningUpBranches: boolean;
}

/**
 * Fetches all data and binds all actions for the Git browser page.
 *
 * Keeping this logic out of `GitBrowserContent` lets the component remain
 * presentational and avoids the `thin_components` architectural-convention
 * warning.
 */
export function useGitBrowser(): UseGitBrowserResult {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { register, unregister, refresh } = usePageRefresh();

  const projectSlug = searchParams.get("project") || "";
  const taskId = searchParams.get("task") || "";

  const {
    data: projects,
    isLoading: loadingProjects,
    error: projectsError,
    refetch: refetchProjects,
  } = useProjects();

  const {
    data: status,
    isLoading: loadingStatus,
    refetch: refetchStatus,
  } = useGitStatus(projectSlug, taskId, !!projectSlug);

  const {
    data: log,
    isLoading: loadingLog,
    refetch: refetchLog,
  } = useGitLog(projectSlug, 20, undefined, !!projectSlug);

  const {
    data: branches,
    isLoading: loadingBranches,
    refetch: refetchBranches,
  } = useGitBranches(projectSlug, true, !!projectSlug);

  const { data: stagedDiff, isLoading: loadingStagedDiff } = useGitDiff(
    projectSlug,
    true,
    undefined,
    !!projectSlug,
  );

  const { data: unstagedDiff, isLoading: loadingUnstagedDiff } = useGitDiff(
    projectSlug,
    false,
    undefined,
    !!projectSlug,
  );

  // Register all active git queries with the page-scoped refresh button.
  useEffect(() => {
    const callbacks = [
      () => void refetchProjects(),
      () => void refetchStatus(),
      () => void refetchLog(),
      () => void refetchBranches(),
    ];
    callbacks.forEach((cb) => register(cb));
    return () => callbacks.forEach((cb) => unregister(cb));
  }, [
    register,
    unregister,
    refetchProjects,
    refetchStatus,
    refetchLog,
    refetchBranches,
  ]);

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
    [router, searchParams],
  );

  const handleProjectChange = useCallback(
    (slug: string) => {
      updateParams({ project: slug || null, task: null });
    },
    [updateParams],
  );

  const {
    commit,
    push,
    createBranch,
    checkout,
    createPR,
    mergePR,
    pull,
    fetch,
    rebase,
    cleanupBranches,
  } = useGitOperations();

  // Resume point for a capped stale-branch sweep, per project.
  const cleanupCursorRef = useRef<{ slug: string; cursor: string } | null>(null);

  const handleCheckout = useCallback(
    async (branch: string) => {
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
    },
    [projectSlug, checkout],
  );

  const handleCreateBranch = useCallback(
    async (branchType: BranchType, branchTaskId: string) => {
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
    },
    [projectSlug, createBranch],
  );

  const handleCommit = useCallback(
    async (message: string) => {
      try {
        const result = await commit.mutateAsync({
          project_slug: projectSlug,
          message,
          task_id: taskId || undefined,
          agent_id: "ceo",
        });
        toast.success(`Committed: ${result.commit_hash.slice(0, 7)}`);
      } catch {
        toast.error("Failed to commit");
      }
    },
    [projectSlug, taskId, commit],
  );

  const handlePush = useCallback(
    async (force?: boolean) => {
      try {
        const result = await push.mutateAsync({
          project_slug: projectSlug,
          task_id: taskId || undefined,
          agent_id: "ceo",
          force,
        });
        toast.success(
          `Pushed ${result.commits_pushed} commits to ${result.branch}`,
        );
      } catch {
        toast.error("Failed to push");
      }
    },
    [projectSlug, taskId, push],
  );

  const handleCreatePR = useCallback(
    async (title: string, body: string) => {
      try {
        const result = await createPR.mutateAsync({
          project_slug: projectSlug,
          task_id: taskId || undefined,
          title,
          body,
          agent_id: "ceo",
        });
        toast.success(`Created PR #${result.pr_number}: ${result.pr_url}`);
      } catch {
        toast.error("Failed to create PR");
      }
    },
    [projectSlug, taskId, createPR],
  );

  const handleMergePR = useCallback(
    async (prNumber: number) => {
      try {
        const result = await mergePR.mutateAsync({
          project_slug: projectSlug,
          pr_number: prNumber,
          task_id: taskId || undefined,
          agent_id: "ceo",
        });
        toast.success(
          `Merged PR #${result.pr_number} → ${result.target_branch}`,
        );
      } catch {
        toast.error("Failed to merge PR");
      }
    },
    [projectSlug, taskId, mergePR],
  );

  const handlePull = useCallback(async () => {
    try {
      const result = await pull.mutateAsync({
        project_slug: projectSlug,
        task_id: taskId || undefined,
      });
      toast.success(`Pulled: now on ${result.current_branch}`);
    } catch {
      toast.error("Failed to pull from remote");
    }
  }, [projectSlug, taskId, pull]);

  const handleFetch = useCallback(async () => {
    try {
      const result = await fetch.mutateAsync({
        project_slug: projectSlug,
        task_id: taskId || undefined,
      });
      toast.success(`Fetched: now on ${result.current_branch}`);
    } catch {
      toast.error("Failed to fetch from remote");
    }
  }, [projectSlug, taskId, fetch]);

  const handleRebase = useCallback(
    async (targetBranch: string) => {
      try {
        const result = await rebase.mutateAsync({
          project_slug: projectSlug,
          target_branch: targetBranch,
          task_id: taskId || undefined,
          agent_id: "ceo",
        });
        if (result.conflict) {
          toast.warning(
            `Rebase conflicts in: ${result.conflicted_files.join(", ") || "unknown files"}`,
          );
        } else {
          toast.success("Rebase completed successfully");
        }
      } catch (error) {
        toast.error(getErrorMessage(error));
      }
    },
    [projectSlug, taskId, rebase],
  );

  const handleCleanupBranches = useCallback(async () => {
    try {
      // Resume a capped sweep from where the last click stopped — without
      // the cursor the backend re-scans the identical first window forever.
      const cursor =
        cleanupCursorRef.current?.slug === projectSlug
          ? cleanupCursorRef.current.cursor
          : undefined;
      const result = await cleanupBranches.mutateAsync({
        project_slug: projectSlug,
        ...(cursor ? { after_cursor: cursor } : {}),
      });
      cleanupCursorRef.current =
        result.truncated && result.next_cursor
          ? { slug: projectSlug, cursor: result.next_cursor }
          : null;
      const truncatedNote = result.truncated
        ? " (cap reached — click again to continue where it stopped)"
        : "";
      toast.success(
        `Cleaned up branches: ${result.remote_deleted} remote, ` +
          `${result.local_deleted} local, ${result.skipped} skipped, ` +
          `${result.errors} errors${truncatedNote}`,
      );
    } catch (error) {
      toast.error(getErrorMessage(error));
    }
  }, [projectSlug, cleanupBranches]);

  const isOffline =
    !!projectsError &&
    (projectsError.message?.includes("Network Error") ||
      (projectsError as { code?: string }).code === "ERR_NETWORK");

  return {
    projectSlug,
    taskId,
    projects,
    loadingProjects,
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
    isCommitting: commit.isPending,
    isPushing: push.isPending,
    isCreatingPR: createPR.isPending,
    isMerging: mergePR.isPending,
    isPulling: pull.isPending,
    isFetching: fetch.isPending,
    isRebasing: rebase.isPending,
    isCheckingOut: checkout.isPending,
    isCreatingBranch: createBranch.isPending,
    isCleaningUpBranches: cleanupBranches.isPending,
  };
}

/**
 * Git React Query Hooks
 *
 * React Query hooks for Git operations.
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { gitApi } from "@/lib/api/git";
import type {
  GitStatusResponse,
  GitLogResponse,
  GitBranchListResponse,
  GitDiffResponse,
  GitFileContentResponse,
  GitCommitRequest,
  GitCommitResponse,
  GitPushRequest,
  GitPushResponse,
  GitCreateBranchRequest,
  GitCreateBranchResponse,
  GitCheckoutRequest,
  GitCheckoutResponse,
  GitCreatePRRequest,
  GitCreatePRResponse,
  GitMergePRRequest,
  GitMergePRResponse,
  GitPullRequest,
  GitPullResponse,
  GitFetchRequest,
  GitFetchResponse,
  GitRebaseRequest,
  GitRebaseResponse,
  GitBranchCleanupRequest,
  GitBranchCleanupResponse,
} from "@/types/git";

// =============================================================================
// Query Keys
// =============================================================================

export const gitKeys = {
  all: ["git"] as const,
  status: (projectSlug: string) =>
    [...gitKeys.all, "status", projectSlug] as const,
  log: (projectSlug: string, limit?: number, branch?: string) =>
    [...gitKeys.all, "log", projectSlug, { limit, branch }] as const,
  branches: (projectSlug: string, includeRemote?: boolean) =>
    [...gitKeys.all, "branches", projectSlug, { includeRemote }] as const,
  diff: (projectSlug: string, staged?: boolean, filePath?: string) =>
    [...gitKeys.all, "diff", projectSlug, { staged, filePath }] as const,
  file: (branch: string, path: string, line?: number, context?: number) =>
    [...gitKeys.all, "file", branch, path, { line, context }] as const,
};

// =============================================================================
// Query Hooks
// =============================================================================

/**
 * Get git status for a project
 */
export function useGitStatus(
  projectSlug: string,
  taskId?: string,
  enabled: boolean = true,
) {
  return useQuery<GitStatusResponse>({
    queryKey: gitKeys.status(projectSlug),
    queryFn: () => gitApi.getStatus(projectSlug, taskId),
    enabled: enabled && !!projectSlug,
    staleTime: 10000, // 10 seconds - status changes frequently
    refetchInterval: 30000, // Auto-refresh every 30s
  });
}

/**
 * Get git log for a project
 */
export function useGitLog(
  projectSlug: string,
  limit: number = 10,
  branch?: string,
  enabled: boolean = true,
) {
  return useQuery<GitLogResponse>({
    queryKey: gitKeys.log(projectSlug, limit, branch),
    queryFn: () => gitApi.getLog(projectSlug, limit, branch),
    enabled: enabled && !!projectSlug,
    staleTime: 30000, // 30 seconds
  });
}

/**
 * Get git branches for a project
 */
export function useGitBranches(
  projectSlug: string,
  includeRemote: boolean = false,
  enabled: boolean = true,
) {
  return useQuery<GitBranchListResponse>({
    queryKey: gitKeys.branches(projectSlug, includeRemote),
    queryFn: () => gitApi.getBranches(projectSlug, includeRemote),
    enabled: enabled && !!projectSlug,
    staleTime: 60000, // 1 minute
  });
}

/**
 * Get git diff for a project
 */
export function useGitDiff(
  projectSlug: string,
  staged: boolean = false,
  filePath?: string,
  enabled: boolean = true,
) {
  return useQuery<GitDiffResponse>({
    queryKey: gitKeys.diff(projectSlug, staged, filePath),
    queryFn: () => gitApi.getDiff(projectSlug, staged, filePath),
    enabled: enabled && !!projectSlug,
    staleTime: 10000, // 10 seconds - diff can change frequently
  });
}

/**
 * Read a file at a branch tip, sliced around an optional line.
 */
export function useGitFile(
  branch: string | null | undefined,
  path: string | null | undefined,
  line: number | null | undefined,
  context: number = 10,
  enabled: boolean = true,
) {
  return useQuery<GitFileContentResponse>({
    queryKey: gitKeys.file(branch ?? "", path ?? "", line ?? undefined, context),
    queryFn: () => gitApi.getFile(branch!, path!, line ?? undefined, context),
    enabled: enabled && !!branch && !!path,
    staleTime: 60000, // 1 minute — branch content is stable
    retry: false, // a 404 (file gone) shouldn't retry forever
  });
}

// =============================================================================
// Mutation Hooks
// =============================================================================

/**
 * Create a git commit
 */
export function useGitCommit() {
  const queryClient = useQueryClient();

  return useMutation<GitCommitResponse, Error, GitCommitRequest>({
    mutationFn: (request) => gitApi.commit(request),
    onSuccess: (_, variables) => {
      // Invalidate status and log after commit
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "log", variables.project_slug],
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "diff", variables.project_slug],
      });
    },
  });
}

/**
 * Push commits to remote
 */
export function useGitPush() {
  const queryClient = useQueryClient();

  return useMutation<GitPushResponse, Error, GitPushRequest>({
    mutationFn: (request) => gitApi.push(request),
    onSuccess: (_, variables) => {
      // Invalidate status after push
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
    },
  });
}

/**
 * Create a task branch
 */
export function useCreateBranch() {
  const queryClient = useQueryClient();

  return useMutation<GitCreateBranchResponse, Error, GitCreateBranchRequest>({
    mutationFn: (request) => gitApi.createBranch(request),
    onSuccess: (_, variables) => {
      // Invalidate branches after creating a new one
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "branches", variables.project_slug],
      });
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
    },
  });
}

/**
 * Checkout a branch
 */
export function useCheckout() {
  const queryClient = useQueryClient();

  return useMutation<GitCheckoutResponse, Error, GitCheckoutRequest>({
    mutationFn: (request) => gitApi.checkout(request),
    onSuccess: (_, variables) => {
      // Invalidate everything for this project after checkout
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "log", variables.project_slug],
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "diff", variables.project_slug],
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "branches", variables.project_slug],
      });
    },
  });
}

/**
 * Create a pull request
 */
export function useCreatePR() {
  const queryClient = useQueryClient();

  return useMutation<GitCreatePRResponse, Error, GitCreatePRRequest>({
    mutationFn: (request) => gitApi.createPR(request),
    onSuccess: (_, variables) => {
      // Invalidate status after PR creation
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
      // Also invalidate tasks since PR creation updates task state
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

/**
 * Merge a pull request
 */
export function useMergePR() {
  const queryClient = useQueryClient();

  return useMutation<GitMergePRResponse, Error, GitMergePRRequest>({
    mutationFn: (request) => gitApi.mergePR(request),
    onSuccess: (_, variables) => {
      // Invalidate branches after merge
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "branches", variables.project_slug],
      });
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
      // Also invalidate tasks since merge updates task state
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
    },
  });
}

/**
 * Pull latest changes from remote
 */
export function useGitPull() {
  const queryClient = useQueryClient();

  return useMutation<GitPullResponse, Error, GitPullRequest>({
    mutationFn: (request) => gitApi.pull(request),
    onSuccess: (_, variables) => {
      // Invalidate status and log after pull
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "log", variables.project_slug],
      });
    },
  });
}

/**
 * Fetch refs from remote without merging
 */
export function useGitFetch() {
  const queryClient = useQueryClient();

  return useMutation<GitFetchResponse, Error, GitFetchRequest>({
    mutationFn: (request) => gitApi.fetch(request),
    onSuccess: (_, variables) => {
      // Invalidate status after fetch
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
    },
  });
}

/**
 * Rebase current branch onto remote (destructive — rewrites history)
 */
export function useGitRebase() {
  const queryClient = useQueryClient();

  return useMutation<GitRebaseResponse, Error, GitRebaseRequest>({
    mutationFn: (request) => gitApi.rebase(request),
    onSuccess: (_, variables) => {
      // Invalidate everything after rebase since history has changed
      queryClient.invalidateQueries({
        queryKey: gitKeys.status(variables.project_slug),
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "log", variables.project_slug],
      });
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "diff", variables.project_slug],
      });
    },
  });
}

/**
 * Sweep a project's terminal-task branches (remote + local, PM/CEO only)
 */
export function useCleanupBranches() {
  const queryClient = useQueryClient();

  return useMutation<
    GitBranchCleanupResponse,
    Error,
    GitBranchCleanupRequest
  >({
    mutationFn: (request) => gitApi.cleanupBranches(request),
    onSuccess: (_, variables) => {
      // Invalidate branches — the sweep may have deleted several.
      queryClient.invalidateQueries({
        queryKey: [...gitKeys.all, "branches", variables.project_slug],
      });
    },
  });
}

// =============================================================================
// Bundled Hook for Git Operations
// =============================================================================

/**
 * Bundled hook for all git write operations
 */
export function useGitOperations() {
  const commit = useGitCommit();
  const push = useGitPush();
  const createBranch = useCreateBranch();
  const checkout = useCheckout();
  const createPR = useCreatePR();
  const mergePR = useMergePR();
  const pull = useGitPull();
  const fetch = useGitFetch();
  const rebase = useGitRebase();
  const cleanupBranches = useCleanupBranches();

  return {
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
  };
}

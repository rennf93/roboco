/**
 * Git API Client
 *
 * API functions for Git operations (branches, commits, PRs).
 */

import api from "./client";
import { isMockMode } from "@/lib/mock-data";
import type {
  GitStatusResponse,
  GitLogResponse,
  GitBranchListResponse,
  GitDiffResponse,
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
} from "@/types/git";

// =============================================================================
// Mock Data
// =============================================================================

const mockGitStatus: GitStatusResponse = {
  project_slug: "roboco",
  current_branch: "feature/backend/abc12345",
  has_changes: true,
  staged_files: ["src/api/routes/tasks.py"],
  unstaged_files: ["src/services/task_service.py"],
  untracked_files: [],
  ahead: 2,
  behind: 0,
};

const mockGitLog: GitLogResponse = {
  project_slug: "roboco",
  branch: "feature/backend/abc12345",
  commits: [
    {
      hash: "abc123456789abcdef",
      short_hash: "abc1234",
      message: "[abc12345] Add task status endpoint",
      author: "backend-dev",
      date: new Date().toISOString(),
    },
  ],
};

const mockBranches: GitBranchListResponse = {
  project_slug: "roboco",
  current_branch: "feature/backend/abc12345",
  branches: [
    { name: "main", is_current: false, is_remote: false, last_commit: "def456" },
    { name: "feature/backend/abc12345", is_current: true, is_remote: false, last_commit: "abc123" },
  ],
};

// =============================================================================
// API Client
// =============================================================================

export const gitApi = {
  // ===========================================================================
  // READ-ONLY OPERATIONS
  // ===========================================================================

  /**
   * Get git status for a project
   */
  getStatus: async (projectSlug: string, taskId?: string): Promise<GitStatusResponse> => {
    if (isMockMode()) {
      return { ...mockGitStatus, project_slug: projectSlug };
    }
    const { data } = await api.get<GitStatusResponse>("/git/status", {
      params: { project_slug: projectSlug, _task_id: taskId },
    });
    return data;
  },

  /**
   * Get git log for a project
   */
  getLog: async (
    projectSlug: string,
    limit: number = 10,
    branch?: string
  ): Promise<GitLogResponse> => {
    if (isMockMode()) {
      return { ...mockGitLog, project_slug: projectSlug };
    }
    const { data } = await api.get<GitLogResponse>("/git/log", {
      params: { project_slug: projectSlug, limit, branch },
    });
    return data;
  },

  /**
   * Get git branches for a project
   */
  getBranches: async (
    projectSlug: string,
    includeRemote: boolean = false
  ): Promise<GitBranchListResponse> => {
    if (isMockMode()) {
      return { ...mockBranches, project_slug: projectSlug };
    }
    const { data } = await api.get<GitBranchListResponse>("/git/branches", {
      params: { project_slug: projectSlug, include_remote: includeRemote },
    });
    return data;
  },

  /**
   * Get git diff for a project
   */
  getDiff: async (
    projectSlug: string,
    staged: boolean = false,
    filePath?: string
  ): Promise<GitDiffResponse> => {
    if (isMockMode()) {
      return {
        project_slug: projectSlug,
        staged,
        file_path: filePath || null,
        diff: "- old line\n+ new line",
        files_changed: 1,
      };
    }
    const { data } = await api.get<GitDiffResponse>("/git/diff", {
      params: { project_slug: projectSlug, staged, file_path: filePath },
    });
    return data;
  },

  // ===========================================================================
  // WRITE OPERATIONS
  // ===========================================================================

  /**
   * Create a git commit
   */
  commit: async (request: GitCommitRequest): Promise<GitCommitResponse> => {
    if (isMockMode()) {
      return {
        commit_hash: "abc123456789abcdef",
        message: `[${request.task_id.slice(0, 8)}] ${request.message}`,
        files_changed: request.files?.length || 1,
        insertions: 10,
        deletions: 5,
      };
    }
    const { data } = await api.post<GitCommitResponse>("/git/commit", request);
    return data;
  },

  /**
   * Push commits to remote
   */
  push: async (request: GitPushRequest): Promise<GitPushResponse> => {
    if (isMockMode()) {
      return {
        branch: "feature/backend/abc12345",
        commits_pushed: 2,
        remote: "origin",
        ready_for_pr: true,
      };
    }
    const { data } = await api.post<GitPushResponse>("/git/push", request);
    return data;
  },

  /**
   * Create a task branch (PM only)
   */
  createBranch: async (request: GitCreateBranchRequest): Promise<GitCreateBranchResponse> => {
    if (isMockMode()) {
      return {
        branch_name: `${request.branch_type}/backend/${request.task_id.slice(0, 8)}`,
        created_from: request.parent_branch || "main",
        project_slug: request.project_slug,
      };
    }
    const { data } = await api.post<GitCreateBranchResponse>("/git/branch/create", request);
    return data;
  },

  /**
   * Checkout a branch
   */
  checkout: async (request: GitCheckoutRequest): Promise<GitCheckoutResponse> => {
    if (isMockMode()) {
      return {
        branch: request.branch,
        project_slug: request.project_slug,
      };
    }
    const { data } = await api.post<GitCheckoutResponse>("/git/checkout", request);
    return data;
  },

  /**
   * Create a pull request
   */
  createPR: async (request: GitCreatePRRequest): Promise<GitCreatePRResponse> => {
    if (isMockMode()) {
      return {
        pr_number: 42,
        pr_url: "https://github.com/org/repo/pull/42",
        title: request.title,
        source_branch: "feature/backend/abc12345",
        target_branch: "main",
      };
    }
    const { data } = await api.post<GitCreatePRResponse>("/git/pr/create", request);
    return data;
  },

  /**
   * Merge a pull request (PM only)
   */
  mergePR: async (request: GitMergePRRequest): Promise<GitMergePRResponse> => {
    if (isMockMode()) {
      return {
        pr_number: request.pr_number,
        merged: true,
        merge_commit: "def456789abcdef",
        target_branch: "main",
      };
    }
    const { data } = await api.post<GitMergePRResponse>("/git/pr/merge", request);
    return data;
  },
};

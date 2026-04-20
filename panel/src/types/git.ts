/**
 * Git API Types
 *
 * Type definitions for Git operations API.
 */

// =============================================================================
// Response Types
// =============================================================================

export interface GitStatusResponse {
  project_slug: string;
  current_branch: string;
  has_changes: boolean;
  staged_files: string[];
  unstaged_files: string[];
  untracked_files: string[];
  ahead: number;
  behind: number;
}

export interface CommitInfo {
  hash: string;
  short_hash: string;
  message: string;
  author: string;
  date: string;
}

export interface GitLogResponse {
  project_slug: string;
  branch: string;
  commits: CommitInfo[];
}

export interface BranchInfo {
  name: string;
  is_current: boolean;
  is_remote: boolean;
  last_commit: string | null;
}

export interface GitBranchListResponse {
  project_slug: string;
  current_branch: string;
  branches: BranchInfo[];
}

export interface GitDiffResponse {
  project_slug: string;
  staged: boolean;
  file_path: string | null;
  diff: string;
  files_changed: number;
}

export interface GitCommitResponse {
  commit_hash: string;
  message: string;
  files_changed: number;
  insertions: number;
  deletions: number;
}

export interface GitPushResponse {
  branch: string;
  commits_pushed: number;
  remote: string;
  ready_for_pr: boolean;
}

export interface GitCreateBranchResponse {
  branch_name: string;
  created_from: string;
  project_slug: string;
}

export interface GitCheckoutResponse {
  branch: string;
  project_slug: string;
}

export interface GitCreatePRResponse {
  pr_number: number;
  pr_url: string;
  title: string;
  source_branch: string;
  target_branch: string;
}

export interface GitMergePRResponse {
  pr_number: number;
  merged: boolean;
  merge_commit: string | null;
  target_branch: string;
}

// =============================================================================
// Request Types
// =============================================================================

export interface GitCommitRequest {
  project_slug: string;
  message: string;
  task_id: string;
  agent_id: string;
  files?: string[] | null;
}

export interface GitPushRequest {
  project_slug: string;
  task_id: string;
  agent_id: string;
  force?: boolean;
}

export type BranchType = "feature" | "bug" | "chore" | "docs" | "hotfix";

export interface GitCreateBranchRequest {
  project_slug: string;
  task_id: string;
  branch_type: BranchType;
  agent_id: string;
  parent_branch?: string | null;
}

export interface GitCheckoutRequest {
  project_slug: string;
  branch: string;
  agent_id: string;
}

export interface GitCreatePRRequest {
  project_slug: string;
  task_id: string;
  title: string;
  body: string;
  agent_id: string;
}

export type MergeMethod = "merge" | "squash" | "rebase";

export interface GitMergePRRequest {
  project_slug: string;
  pr_number: number;
  task_id: string;
  merge_method?: MergeMethod;
  agent_id: string;
}

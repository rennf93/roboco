"""
RoboCo Templates Package.

Provides structured templates for git workflow, commit messages, and PRs.
"""

from roboco.templates.git import (
    BRANCH_TYPES,
    COMMIT_TYPES,
    MAX_TASK_DEPTH,
    BranchNameError,
    CommitContext,
    CommitMessageError,
    InternalPRContext,
    RootPRContext,
    SubtaskInfo,
    build_branch_name,
    build_commit_message,
    build_pr_body_internal,
    build_pr_body_root,
    build_pr_title_internal,
    build_pr_title_root,
    get_root_task_id,
)

__all__ = [
    "BRANCH_TYPES",
    "COMMIT_TYPES",
    "MAX_TASK_DEPTH",
    "BranchNameError",
    "CommitContext",
    "CommitMessageError",
    "InternalPRContext",
    "RootPRContext",
    "SubtaskInfo",
    "build_branch_name",
    "build_commit_message",
    "build_pr_body_internal",
    "build_pr_body_root",
    "build_pr_title_internal",
    "build_pr_title_root",
    "get_root_task_id",
]

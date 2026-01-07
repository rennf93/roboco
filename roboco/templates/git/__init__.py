"""
Git Templates Package.

Provides templates for branch naming, commit messages, and PR bodies.
"""

from roboco.templates.git.branch import (
    BranchNameError,
    build_branch_name,
    get_root_task_id,
)
from roboco.templates.git.commit import (
    CommitContext,
    CommitMessageError,
    build_commit_message,
)
from roboco.templates.git.constants import BRANCH_TYPES, COMMIT_TYPES, MAX_TASK_DEPTH
from roboco.templates.git.pr_internal import (
    InternalPRContext,
    build_pr_body_internal,
    build_pr_title_internal,
)
from roboco.templates.git.pr_root import (
    RootPRContext,
    SubtaskInfo,
    build_pr_body_root,
    build_pr_title_root,
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

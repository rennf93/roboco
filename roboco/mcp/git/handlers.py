"""
Git MCP Server Handlers

Handler functions for git operations. Each handler:
1. Validates permissions and state
2. Calls the internal API
3. Returns formatted response with guidance
"""

from dataclasses import dataclass
from typing import Any

from roboco.mcp.utils import (
    ApiClient,
    format_error_response,
    format_success_response,
)


@dataclass
class GitContext:
    """Common context for git operations."""

    client: ApiClient
    project_slug: str
    agent_id: str


# =============================================================================
# READ-ONLY HANDLERS
# =============================================================================


async def handle_git_status(
    client: ApiClient,
    project_slug: str,
    task_id: str | None,
    _agent_id: str,
) -> dict[str, Any]:
    """Handle git status request."""
    params: dict[str, Any] = {"project_slug": project_slug}
    if task_id:
        params["task_id"] = task_id

    resp = await client.get("/git/status", params=params)
    if not resp.ok:
        return format_error_response(
            "GIT_STATUS_FAILED",
            "Failed to get git status",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    return format_success_response(
        data,
        guidance=_get_status_guidance(data),
        next_step="COMMIT" if data.get("has_changes") else None,
    )


def _get_status_guidance(data: dict[str, Any]) -> str:
    """Generate guidance based on git status."""
    staged = data.get("staged_files", [])
    unstaged = data.get("unstaged_files", [])
    untracked = data.get("untracked_files", [])

    if not staged and not unstaged and not untracked:
        return "Working tree is clean. No changes to commit."

    parts = []
    if staged:
        parts.append(f"{len(staged)} staged file(s) ready to commit")
    if unstaged:
        parts.append(f"{len(unstaged)} modified file(s) not staged")
    if untracked:
        parts.append(f"{len(untracked)} untracked file(s)")

    guidance = ". ".join(parts) + "."

    if staged:
        guidance += "\nUse roboco_git_commit() to create a commit."
    elif unstaged or untracked:
        guidance += (
            "\nUse roboco_git_commit() with files parameter to stage and commit."
        )

    return guidance


async def handle_git_log(
    client: ApiClient,
    project_slug: str,
    limit: int,
    branch: str | None,
    _agent_id: str,
) -> dict[str, Any]:
    """Handle git log request."""
    # Enforce max limit
    max_limit = 50
    limit = min(limit, max_limit)

    params: dict[str, Any] = {"project_slug": project_slug, "limit": limit}
    if branch:
        params["branch"] = branch

    resp = await client.get("/git/log", params=params)
    if not resp.ok:
        return format_error_response(
            "GIT_LOG_FAILED",
            "Failed to get git log",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    return format_success_response(
        data,
        guidance=f"Showing {len(data.get('commits', []))} recent commits.",
    )


async def handle_git_branch_list(
    client: ApiClient,
    project_slug: str,
    include_remote: bool,
    _agent_id: str,
) -> dict[str, Any]:
    """Handle git branch list request."""
    params: dict[str, Any] = {
        "project_slug": project_slug,
        "include_remote": include_remote,
    }

    resp = await client.get("/git/branches", params=params)
    if not resp.ok:
        return format_error_response(
            "GIT_BRANCH_LIST_FAILED",
            "Failed to list branches",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    branches = data.get("branches", [])
    current = data.get("current_branch", "unknown")

    return format_success_response(
        data,
        guidance=f"Current branch: {current}. {len(branches)} total branch(es).",
    )


async def handle_git_diff(
    client: ApiClient,
    project_slug: str,
    staged: bool,
    file_path: str | None,
    _agent_id: str,
) -> dict[str, Any]:
    """Handle git diff request."""
    params: dict[str, Any] = {"project_slug": project_slug, "staged": staged}
    if file_path:
        params["file_path"] = file_path

    resp = await client.get("/git/diff", params=params)
    if not resp.ok:
        return format_error_response(
            "GIT_DIFF_FAILED",
            "Failed to get diff",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    diff_type = "staged" if staged else "unstaged"

    return format_success_response(
        data,
        guidance=f"Showing {diff_type} changes."
        + (" No changes." if not data.get("diff") else ""),
    )


# =============================================================================
# DEVELOPER HANDLERS
# =============================================================================


async def handle_git_commit(
    ctx: GitContext,
    message: str,
    task_id: str,
    files: list[str] | None,
) -> dict[str, Any]:
    """Handle git commit request."""
    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "message": message,
        "task_id": task_id,
        "agent_id": ctx.agent_id,
    }
    if files:
        payload["files"] = files

    resp = await ctx.client.post("/git/commit", json=payload)
    if not resp.ok:
        return format_error_response(
            "GIT_COMMIT_FAILED",
            "Failed to create commit",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check you are on a task branch and have staged changes.",
        )

    data = resp.json()
    commit_hash = data.get("commit_hash", "unknown")[:8]
    files_changed = data.get("files_changed", 0)

    return format_success_response(
        data,
        guidance=f"Commit {commit_hash} created with {files_changed} file(s).\n"
        "Use roboco_git_push() when ready to push to remote.",
        next_step="PUSH",
    )


async def handle_git_push(
    client: ApiClient,
    project_slug: str,
    task_id: str,
    force: bool,
    agent_id: str,
) -> dict[str, Any]:
    """Handle git push request."""
    payload: dict[str, Any] = {
        "project_slug": project_slug,
        "task_id": task_id,
        "agent_id": agent_id,
        "force": force,
    }

    resp = await client.post("/git/push", json=payload)
    if not resp.ok:
        return format_error_response(
            "GIT_PUSH_FAILED",
            "Failed to push",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check branch is not protected and you have commits to push.",
        )

    data = resp.json()
    branch = data.get("branch", "unknown")
    commits_pushed = data.get("commits_pushed", 0)

    return format_success_response(
        data,
        guidance=f"Pushed {commits_pushed} commit(s) to {branch}.\n"
        "Continue working or create a PR when ready.",
        next_step="CREATE_PR" if data.get("ready_for_pr") else None,
    )


async def handle_git_create_pr(
    ctx: GitContext,
    task_id: str,
    title: str,
    body: str,
) -> dict[str, Any]:
    """Handle PR creation request."""
    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "task_id": task_id,
        "title": title,
        "body": body,
        "agent_id": ctx.agent_id,
    }

    resp = await ctx.client.post("/git/pr/create", json=payload)
    if not resp.ok:
        return format_error_response(
            "PR_CREATE_FAILED",
            "Failed to create PR",
            {"status": resp.status_code, "detail": resp.text},
            hint="Ensure QA passed and you have pushed commits.",
        )

    data = resp.json()
    pr_url = data.get("pr_url", "")
    pr_number = data.get("pr_number", 0)

    return format_success_response(
        data,
        guidance=f"PR #{pr_number} created: {pr_url}\n"
        "The PM will review and merge when ready.",
        next_step="AWAIT_MERGE",
    )


# =============================================================================
# PM HANDLERS
# =============================================================================


async def handle_git_create_branch(
    ctx: GitContext,
    task_id: str,
    branch_type: str,
    parent_branch: str | None,
) -> dict[str, Any]:
    """Handle branch creation request (PM only)."""
    valid_types = {"feature", "bug", "chore", "docs", "hotfix"}
    if branch_type not in valid_types:
        return format_error_response(
            "INVALID_BRANCH_TYPE",
            f"Branch type must be one of: {', '.join(valid_types)}",
            {"provided": branch_type},
        )

    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "task_id": task_id,
        "branch_type": branch_type,
        "agent_id": ctx.agent_id,
    }
    if parent_branch:
        payload["parent_branch"] = parent_branch

    resp = await ctx.client.post("/git/branch/create", json=payload)
    if not resp.ok:
        return format_error_response(
            "BRANCH_CREATE_FAILED",
            "Failed to create branch",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    branch_name = data.get("branch_name", "unknown")

    return format_success_response(
        data,
        guidance=f"Branch '{branch_name}' created.\n"
        "Assign the task to a developer who will work on this branch.",
        next_step="ASSIGN_TASK",
    )


async def handle_git_checkout(
    client: ApiClient,
    project_slug: str,
    branch: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle branch checkout request."""
    payload: dict[str, Any] = {
        "project_slug": project_slug,
        "branch": branch,
        "agent_id": agent_id,
    }

    resp = await client.post("/git/checkout", json=payload)
    if not resp.ok:
        return format_error_response(
            "CHECKOUT_FAILED",
            f"Failed to checkout branch '{branch}'",
            {"status": resp.status_code, "detail": resp.text},
        )

    data = resp.json()
    return format_success_response(
        data,
        guidance=f"Switched to branch '{branch}'.",
    )


async def handle_git_merge_pr(
    ctx: GitContext,
    pr_number: int,
    task_id: str,
    merge_method: str,
) -> dict[str, Any]:
    """Handle PR merge request (PM only)."""
    valid_methods = {"merge", "squash", "rebase"}
    if merge_method not in valid_methods:
        return format_error_response(
            "INVALID_MERGE_METHOD",
            f"Merge method must be one of: {', '.join(valid_methods)}",
            {"provided": merge_method},
        )

    payload: dict[str, Any] = {
        "project_slug": ctx.project_slug,
        "pr_number": pr_number,
        "task_id": task_id,
        "merge_method": merge_method,
        "agent_id": ctx.agent_id,
    }

    resp = await ctx.client.post("/git/pr/merge", json=payload)
    if not resp.ok:
        return format_error_response(
            "PR_MERGE_FAILED",
            f"Failed to merge PR #{pr_number}",
            {"status": resp.status_code, "detail": resp.text},
            hint="Check all approvals are in place and there are no conflicts.",
        )

    data = resp.json()
    merged_into = data.get("target_branch", "unknown")
    commit_hash = data.get("merge_commit", "unknown")[:8]

    return format_success_response(
        data,
        guidance=f"PR #{pr_number} merged into {merged_into} (commit {commit_hash}).\n"
        "The work session is now complete.",
        next_step="COMPLETE_TASK",
    )

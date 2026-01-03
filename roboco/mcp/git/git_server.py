"""
Git MCP Server

Exposes git operations to Claude Code agents with built-in
enforcement of branch policies and access controls.

Tools (Developer):
- roboco_git_status: Check branch and working tree status
- roboco_git_commit: Create a commit with message
- roboco_git_push: Push commits to remote
- roboco_git_diff: View staged/unstaged changes

Tools (Developer - PR workflow):
- roboco_git_create_pr: Create a PR for the current branch

Tools (PM - Branch management):
- roboco_git_create_branch: Create a task branch
- roboco_git_checkout: Switch to a branch
- roboco_git_merge_pr: Merge a PR (approve and merge)

Tools (All - Read-only):
- roboco_git_log: View recent commits
- roboco_git_branch_list: List branches
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.mcp.git.handlers import (
    handle_git_branch_list,
    handle_git_checkout,
    handle_git_commit,
    handle_git_create_branch,
    handle_git_create_pr,
    handle_git_diff,
    handle_git_log,
    handle_git_merge_pr,
    handle_git_push,
    handle_git_status,
)
from roboco.mcp.utils import ApiClient


def _register_readonly_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register read-only git tools available to all agents."""

    @mcp.tool()
    async def roboco_git_status(
        project_slug: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Check git status for a project.

        Shows current branch, staged changes, unstaged changes, and untracked files.

        Args:
            project_slug: Project identifier (e.g., 'roboco', 'roboco-panel')
            task_id: Optional task ID for context

        Returns:
            Git status with branch info and file changes
        """
        return await handle_git_status(client, project_slug, task_id, agent_id)

    @mcp.tool()
    async def roboco_git_log(
        project_slug: str,
        limit: int = 10,
        branch: str | None = None,
    ) -> dict[str, Any]:
        """
        View recent git commits.

        Args:
            project_slug: Project identifier
            limit: Number of commits to show (default 10, max 50)
            branch: Branch to show commits from (default: current)

        Returns:
            List of commits with hash, message, author, date
        """
        return await handle_git_log(client, project_slug, limit, branch, agent_id)

    @mcp.tool()
    async def roboco_git_branch_list(
        project_slug: str,
        include_remote: bool = False,
    ) -> dict[str, Any]:
        """
        List git branches.

        Args:
            project_slug: Project identifier
            include_remote: Include remote branches

        Returns:
            List of branches with current branch marked
        """
        return await handle_git_branch_list(
            client, project_slug, include_remote, agent_id
        )

    @mcp.tool()
    async def roboco_git_diff(
        project_slug: str,
        staged: bool = False,
        file_path: str | None = None,
    ) -> dict[str, Any]:
        """
        View git diff (changes).

        Args:
            project_slug: Project identifier
            staged: If True, show staged changes; otherwise unstaged
            file_path: Optional specific file to diff

        Returns:
            Diff output with file changes
        """
        return await handle_git_diff(client, project_slug, staged, file_path, agent_id)


def _register_developer_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register developer git tools."""

    @mcp.tool()
    async def roboco_git_commit(
        project_slug: str,
        message: str,
        task_id: str,
        files: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Create a git commit.

        ENFORCEMENT:
        - Must be on a task branch (not main/master)
        - Commit message should reference the task
        - You must be assigned to the task

        Args:
            project_slug: Project identifier
            message: Commit message (will be prefixed with task ID)
            task_id: Task ID this commit is for
            files: Optional list of files to stage; if None, stages all

        Returns:
            Commit details with hash and files changed
        """
        from roboco.mcp.git.handlers import GitContext

        ctx = GitContext(client=client, project_slug=project_slug, agent_id=agent_id)
        return await handle_git_commit(ctx, message, task_id, files)

    @mcp.tool()
    async def roboco_git_push(
        project_slug: str,
        task_id: str,
        force: bool = False,
    ) -> dict[str, Any]:
        """
        Push commits to remote.

        ENFORCEMENT:
        - Must be on a task branch (not main/master)
        - Protected branches cannot be pushed to directly

        Args:
            project_slug: Project identifier
            task_id: Task ID for validation
            force: Force push (use with caution, PM approval may be needed)

        Returns:
            Push result with remote branch info
        """
        return await handle_git_push(client, project_slug, task_id, force, agent_id)

    @mcp.tool()
    async def roboco_git_create_pr(
        project_slug: str,
        task_id: str,
        title: str,
        body: str,
    ) -> dict[str, Any]:
        """
        Create a Pull Request for the current branch.

        ENFORCEMENT:
        - Task must be in AWAITING_DOCUMENTATION status (QA passed)
        - You must be the developer assigned to the task
        - PR will target the parent task's branch or main

        Args:
            project_slug: Project identifier
            task_id: Task ID this PR is for
            title: PR title
            body: PR description (include what was done, testing notes)

        Returns:
            PR details with URL and number
        """
        from roboco.mcp.git.handlers import GitContext

        ctx = GitContext(client=client, project_slug=project_slug, agent_id=agent_id)
        return await handle_git_create_pr(ctx, task_id, title, body)


def _register_pm_branch_tools(mcp: FastMCP, client: ApiClient, agent_id: str) -> None:
    """Register PM branch management tools."""

    @mcp.tool()
    async def roboco_git_create_branch(
        project_slug: str,
        task_id: str,
        branch_type: str,
        parent_branch: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a task branch (PM only).

        Branch naming follows: {type}/{team}/{task_id}[/{subtask_id}]

        ENFORCEMENT:
        - Only PMs can create task branches
        - Branch type must be: feature, bug, chore, docs, hotfix
        - Branch is created from parent_branch or default branch

        Args:
            project_slug: Project identifier
            task_id: Task ID for the branch
            branch_type: One of: feature, bug, chore, docs, hotfix
            parent_branch: Branch to create from (default: main)

        Returns:
            Created branch info with checkout instructions
        """
        from roboco.mcp.git.handlers import GitContext

        ctx = GitContext(client=client, project_slug=project_slug, agent_id=agent_id)
        return await handle_git_create_branch(ctx, task_id, branch_type, parent_branch)

    @mcp.tool()
    async def roboco_git_checkout(
        project_slug: str,
        branch: str,
    ) -> dict[str, Any]:
        """
        Switch to a branch.

        Args:
            project_slug: Project identifier
            branch: Branch name to checkout

        Returns:
            Checkout result with current branch
        """
        return await handle_git_checkout(client, project_slug, branch, agent_id)

    @mcp.tool()
    async def roboco_git_merge_pr(
        project_slug: str,
        pr_number: int,
        task_id: str,
        merge_method: str = "squash",
    ) -> dict[str, Any]:
        """
        Merge a Pull Request (PM only).

        ENFORCEMENT:
        - Only the appropriate PM can merge (Cell PM for subtasks, Main PM for parent)
        - For CEO approval tasks, only CEO can merge to main
        - All required approvals must be in place

        Args:
            project_slug: Project identifier
            pr_number: PR number to merge
            task_id: Task ID for validation
            merge_method: One of: merge, squash, rebase (default: squash)

        Returns:
            Merge result with final commit hash
        """
        from roboco.mcp.git.handlers import GitContext

        ctx = GitContext(client=client, project_slug=project_slug, agent_id=agent_id)
        return await handle_git_merge_pr(ctx, pr_number, task_id, merge_method)


def create_git_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Git MCP server for a specific agent.

    Tools are registered based on role:
    - All agents: read-only tools (status, log, branch list, diff)
    - Developers: commit, push, create PR
    - PMs: create branch, checkout, merge PR

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server with role-appropriate tools
    """
    from roboco.agents_config import get_agent_role

    mcp = FastMCP(f"roboco-git-{agent_id}", json_response=True)
    client = ApiClient(agent_id)
    role = get_agent_role(agent_id)

    # Read-only tools available to ALL agents
    _register_readonly_tools(mcp, client, agent_id)

    # Role-specific tool registration
    if role == "developer":
        _register_developer_tools(mcp, client, agent_id)

    elif role in ("cell_pm", "main_pm"):
        # PMs get both developer tools and branch management
        _register_developer_tools(mcp, client, agent_id)
        _register_pm_branch_tools(mcp, client, agent_id)

    elif role in ("product_owner", "head_marketing", "auditor", "ceo"):
        # Board/Management: same as Main PM
        _register_developer_tools(mcp, client, agent_id)
        _register_pm_branch_tools(mcp, client, agent_id)

    # QA and Documenter: only read-only tools (already registered)

    return mcp


if __name__ == "__main__":
    import sys

    _MIN_ARGS = 2
    if len(sys.argv) < _MIN_ARGS:
        print("Usage: python git_server.py <agent_id>")
        sys.exit(1)

    agent_id_cli = sys.argv[1]
    server = create_git_mcp_server(agent_id_cli)
    server.run()

"""Read-only git tools available to all roles.

Write operations (commit, push, branch, PR open/merge) go through the
gateway intent verbs in roboco-flow. This server only exposes the four
read-only views agents need to reason about workspace state: status,
log, diff, and branch list.

Endpoint shapes mirror /api/git/* on the orchestrator. The panel uses
the same endpoints, so they stay even though the agent-side write tools
are gone.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

ORCHESTRATOR_URL = os.environ.get(
    "ROBOCO_ORCHESTRATOR_URL",
    "http://roboco-orchestrator:8000",
)
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]

_HEADERS = {"X-Agent-ID": AGENT_ID, "X-Agent-Role": AGENT_ROLE}
_TIMEOUT = 15

mcp = FastMCP("roboco-git-readonly")


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET against the orchestrator with the agent's identity headers."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.get(
            f"{ORCHESTRATOR_URL}{path}", headers=_HEADERS, params=params
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


@mcp.tool()
def roboco_git_status(project_slug: str) -> dict[str, Any]:
    """Read-only: current git status of your workspace.

    Args:
        project_slug: Project slug (e.g. "roboco").

    Returns:
        Current branch, staged/unstaged/untracked files, ahead/behind counts.
    """
    return _get("/api/git/status", {"project_slug": project_slug})


@mcp.tool()
def roboco_git_log(
    project_slug: str,
    limit: int = 10,
    branch: str | None = None,
) -> dict[str, Any]:
    """Read-only: recent commits on the named branch (default: current).

    Args:
        project_slug: Project slug.
        limit: Number of commits to return (max 50).
        branch: Branch to inspect; defaults to the current checked-out branch.

    Returns:
        List of commits with hash, short_hash, message, author, date.
    """
    params: dict[str, Any] = {"project_slug": project_slug, "limit": limit}
    if branch is not None:
        params["branch"] = branch
    return _get("/api/git/log", params)


@mcp.tool()
def roboco_git_diff(
    project_slug: str,
    staged: bool = False,
    file_path: str | None = None,
) -> dict[str, Any]:
    """Read-only: diff of your workspace against the index.

    Args:
        project_slug: Project slug.
        staged: If True, show staged changes; otherwise show unstaged.
        file_path: Optional path to scope the diff to a single file.

    Returns:
        Diff text plus files_changed count.
    """
    params: dict[str, Any] = {"project_slug": project_slug, "staged": staged}
    if file_path is not None:
        params["file_path"] = file_path
    return _get("/api/git/diff", params)


@mcp.tool()
def roboco_git_branch_list(
    project_slug: str,
    include_remote: bool = False,
) -> dict[str, Any]:
    """Read-only: list local (and optionally remote) branches.

    Args:
        project_slug: Project slug.
        include_remote: If True, also include remote-tracking branches.

    Returns:
        Branches with current branch marked.
    """
    return _get(
        "/api/git/branches",
        {"project_slug": project_slug, "include_remote": include_remote},
    )


if __name__ == "__main__":
    mcp.run()

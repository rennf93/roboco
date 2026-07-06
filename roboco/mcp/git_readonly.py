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

from roboco.agents_config import get_agent_team

ORCHESTRATOR_URL = os.environ.get(
    "ROBOCO_ORCHESTRATOR_URL",
    "http://roboco-orchestrator:8000",
)
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]


def _headers() -> dict[str, str]:
    """Identity + HMAC token headers for the orchestrator git reads.

    The git routes sit behind the same ``ROBOCO_AGENT_AUTH_REQUIRED`` gate as
    the rest of ``/api/`` — a static ``{X-Agent-ID, X-Agent-Role}`` dict 401s
    with "Missing X-Agent-Token" once auth is armed. Built per call (token is
    stable per container, but mirroring flow/do/server keeps the pattern).
    """
    headers = {"X-Agent-ID": AGENT_ID, "X-Agent-Role": AGENT_ROLE}
    team = get_agent_team(AGENT_ID)
    if team:
        headers["X-Agent-Team"] = team
    token = os.environ.get("ROBOCO_AGENT_TOKEN")
    if token:
        headers["X-Agent-Token"] = token
    return headers


_TIMEOUT = 15

mcp = FastMCP("roboco-git-readonly")

# Char cap for diff text returned into the agent's context (~5K tokens). Kept
# local (not imported from the gateway) so this MCP stays dependency-light in
# the agent container. The HTTP route itself stays uncapped — the panel's diff
# viewer reads it whole.
_DIFF_CAP_CHARS = 20_000


def _get(path: str, params: dict[str, Any]) -> dict[str, Any]:
    """GET against the orchestrator with the agent's identity headers."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.get(
            f"{ORCHESTRATOR_URL}{path}", headers=_headers(), params=params
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


def _cap_diff(result: dict[str, Any]) -> dict[str, Any]:
    """Truncate an oversized diff for context embedding; annotate the cut."""
    diff = result.get("diff")
    if isinstance(diff, str) and len(diff) > _DIFF_CAP_CHARS:
        omitted = len(diff) - _DIFF_CAP_CHARS
        result["diff"] = (
            diff[:_DIFF_CAP_CHARS]
            + f"\n… [diff truncated: {omitted} chars omitted — scope with"
            " file_path to read a single file's diff in full]"
        )
        result["diff_truncated"] = True
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
    return _cap_diff(_get("/api/git/diff", params))


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

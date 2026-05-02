"""roboco-do MCP server — smart-wrapped content tools.

Forwards to /api/v2/do/* on the orchestrator. Tools are not role-scoped
(any agent role can use them), so the path is fixed (no role segment).
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
_TIMEOUT = 30

mcp = FastMCP("roboco-do")


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST a request to the orchestrator and return the JSON envelope."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            f"{ORCHESTRATOR_URL}{path}",
            headers=_HEADERS,
            json=body,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


@mcp.tool()
def commit(message: str, files: list[str] | None = None) -> dict[str, Any]:
    """Make a git commit. [task-id] prefix auto-applied. Validates message."""
    return _post("/api/v2/do/commit", {"message": message, "files": files})


@mcp.tool()
def note(text: str, scope: str = "note", task_id: str | None = None) -> dict[str, Any]:
    """Write a journal entry. scope in note|decision|reflect|learning|struggle."""
    return _post("/api/v2/do/note", {"text": text, "scope": scope, "task_id": task_id})


@mcp.tool()
def say(channel: str, text: str, task_id: str | None = None) -> dict[str, Any]:
    """Post to a channel. task_id auto-injected if you have an active task."""
    return _post(
        "/api/v2/do/say",
        {"channel": channel, "text": text, "task_id": task_id},
    )


@mcp.tool()
def dm(
    recipient: str,
    text: str,
    task_id: str | None = None,
    skill: str | None = None,
) -> dict[str, Any]:
    """A2A message. Auto-creates conversation; auto-resolves skill if needed."""
    return _post(
        "/api/v2/do/dm",
        {"recipient": recipient, "text": text, "task_id": task_id, "skill": skill},
    )


@mcp.tool()
def evidence(task_id: str) -> dict[str, Any]:
    """Inspect a task's PR diff, commits, files. Fetches dev branch into workspace."""
    return _post("/api/v2/do/evidence", {"task_id": task_id})


if __name__ == "__main__":
    mcp.run()

"""roboco-do MCP server — smart-wrapped content tools.

Forwards to /api/v2/do/* on the orchestrator. Tools are role-scoped at *spawn*
time: the orchestrator writes ``do_tools`` into the per-agent manifest and we
register only those names on this server. The orchestrator's API is not
role-scoped here (any allowed role can call commit/note/say/dm/notify/evidence),
so the path is fixed (no role segment). Per-tool role gates (e.g., notify
restricting to PMs/Board) live inside the gateway verbs.

If the manifest is missing or unreadable (local test runs without the bind
mount) the full registry is registered as a failsafe and a warning is logged.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import httpx
import structlog
from mcp.server.fastmcp import FastMCP

ORCHESTRATOR_URL = os.environ.get(
    "ROBOCO_ORCHESTRATOR_URL",
    "http://roboco-orchestrator:8000",
)
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]

_TIMEOUT = 30

mcp = FastMCP("roboco-do")
log = structlog.get_logger()


def _build_headers() -> dict[str, str]:
    """Build per-call headers including a fresh X-Correlation-ID.

    Mirrors flow_server: each MCP call mints its own correlation id so the
    orchestrator's middleware can bind it to structlog and the audit row,
    and the envelope echoes it back to the agent.
    """
    return {
        "X-Agent-ID": AGENT_ID,
        "X-Agent-Role": AGENT_ROLE,
        "X-Correlation-ID": str(uuid.uuid4()),
    }


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST a request to the orchestrator and return the JSON envelope."""
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            f"{ORCHESTRATOR_URL}{path}",
            headers=_build_headers(),
            json=body,
        )
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result


def commit(message: str, files: list[str] | None = None) -> dict[str, Any]:
    """Make a git commit. [task-id] prefix auto-applied. Validates message."""
    return _post("/api/v2/do/commit", {"message": message, "files": files})


def note(text: str, scope: str = "note", task_id: str | None = None) -> dict[str, Any]:
    """Write a journal entry. scope in note|decision|reflect|learning|struggle."""
    return _post("/api/v2/do/note", {"text": text, "scope": scope, "task_id": task_id})


def say(channel: str, text: str, task_id: str | None = None) -> dict[str, Any]:
    """Post to a channel. task_id auto-injected if you have an active task."""
    return _post(
        "/api/v2/do/say",
        {"channel": channel, "text": text, "task_id": task_id},
    )


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


def notify(
    target: str,
    text: str,
    priority: str = "normal",
    task_id: str | None = None,
) -> dict[str, Any]:
    """Send a formal ack-required notification (PMs and Board only).

    Distinct from say (channel post) and dm (informal A2A): notify creates
    a notification the recipient must acknowledge. priority in
    normal|high|urgent. task_id auto-injected from active task when omitted.
    """
    return _post(
        "/api/v2/do/notify",
        {
            "target": target,
            "text": text,
            "priority": priority,
            "task_id": task_id,
        },
    )


def evidence(task_id: str) -> dict[str, Any]:
    """Inspect a task's PR diff, commits, files. Fetches dev branch into workspace."""
    return _post("/api/v2/do/evidence", {"task_id": task_id})


# ---------- Tool registry ----------
#
# Maps the tool name an agent calls (matches manifest entries and the
# orchestrator's API path) to the Python implementation.

_TOOLS: dict[str, Any] = {
    "commit": commit,
    "note": note,
    "say": say,
    "dm": dm,
    "notify": notify,
    "evidence": evidence,
}


def _load_manifest_do_tools() -> list[str] | None:
    """Read the spawn manifest and return its ``do_tools`` list.

    Returns ``None`` when the manifest is missing or unreadable so callers can
    fall back to registering the full tool set. Never raises.
    """
    manifest_path = Path(
        os.environ.get("ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"),
    )
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "do_server: cannot read manifest",
            path=str(manifest_path),
            error=str(exc),
        )
        return None
    do_tools = manifest.get("do_tools")
    if not isinstance(do_tools, list):
        log.warning(
            "do_server: manifest missing do_tools list",
            path=str(manifest_path),
        )
        return None
    return [str(verb) for verb in do_tools]


def _register_tools() -> list[str]:
    """Register MCP tools according to the manifest, or all tools as a failsafe.

    Returns the list of tool names actually registered.
    """
    allowed = _load_manifest_do_tools()
    if allowed is None:
        log.warning(
            "do_server: manifest unavailable; registering all do tools",
            role=AGENT_ROLE,
        )
        names = list(_TOOLS)
    else:
        unknown = [verb for verb in allowed if verb not in _TOOLS]
        if unknown:
            log.warning(
                "do_server: manifest references unknown do tools",
                role=AGENT_ROLE,
                missing=sorted(unknown),
            )
        names = [verb for verb in allowed if verb in _TOOLS]

    for verb in names:
        mcp.tool(name=verb)(_TOOLS[verb])
    log.info(
        "do_server: registered tools",
        role=AGENT_ROLE,
        tools=sorted(names),
    )
    return names


_REGISTERED_TOOLS = _register_tools()


if __name__ == "__main__":
    mcp.run()

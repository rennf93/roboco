"""roboco-flow MCP server — exposes intent verbs to agents.

Tools are role-scoped via the agent's spawn manifest. The MCP server
registers all dev verbs unconditionally; the orchestrator's API rejects
verbs that don't match the agent's role. Phase 1 ships dev verbs;
Phases 2-4 add QA, doc, PM, board verbs.
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

mcp = FastMCP("roboco-flow")


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


def _role_path(verb: str) -> str:
    """Build the role-scoped /api/v2/flow/<role>/<verb> path."""
    return f"/api/v2/flow/{AGENT_ROLE}/{verb}"


# ---------- Dev verbs (Phase 1) ----------


@mcp.tool()
def give_me_work() -> dict[str, Any]:
    """Get your current task or report idle. Returns task + context_briefing."""
    return _post(_role_path("give_me_work"), {})


@mcp.tool()
def i_will_work_on(task_id: str, plan: str | None = None) -> dict[str, Any]:
    """Claim/start/recover a task. Works for pending, claimed, needs_revision."""
    return _post(_role_path("i_will_work_on"), {"task_id": task_id, "plan": plan})


@mcp.tool()
def i_have_committed(message: str) -> dict[str, Any]:
    """Record that you made a commit. Auto-creates progress entry."""
    return _post(_role_path("i_have_committed"), {"message": message})


@mcp.tool()
def i_am_done(task_id: str, notes: str = "") -> dict[str, Any]:
    """Submit work for QA. Runs verify/push/PR/submit-qa as needed."""
    return _post(_role_path("i_am_done"), {"task_id": task_id, "notes": notes})


@mcp.tool()
def i_am_blocked(task_id: str, reason: str) -> dict[str, Any]:
    """Escalate to PM. Logs a struggle journal entry."""
    return _post(_role_path("i_am_blocked"), {"task_id": task_id, "reason": reason})


@mcp.tool()
def i_am_idle() -> dict[str, Any]:
    """Report no more work. Soft-blocks if you have unread A2A/mentions."""
    return _post(_role_path("i_am_idle"), {})


# ---------- QA verbs (Phase 2) ----------


@mcp.tool()
def claim_review(task_id: str) -> dict[str, Any]:
    """QA: claim a task for review. Returns PR diff + evidence inline."""
    return _post(_role_path("claim_review"), {"task_id": task_id})


@mcp.tool(name="pass")
def pass_review(task_id: str, notes: str) -> dict[str, Any]:
    """QA: accept the work. notes >= 80 chars; journal:learning required."""
    return _post(_role_path("pass"), {"task_id": task_id, "notes": notes})


@mcp.tool(name="fail")
def fail_review(task_id: str, issues: list[str]) -> dict[str, Any]:
    """QA: reject the work with issues. Each issue should be concrete and actionable."""
    return _post(_role_path("fail"), {"task_id": task_id, "issues": issues})


# ---------- Future-phase verbs are NOT registered here ----------
# Phase 3 will add claim_doc_task, i_documented, triage, unblock, complete, escalate_up.
# Phase 4 will add escalate_to_ceo.


def _validate_role_compatibility() -> None:
    """Warn if the manifest references verbs we haven't implemented yet."""
    import json
    from pathlib import Path

    import structlog

    log = structlog.get_logger()
    manifest_path = Path(
        os.environ.get("ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"),
    )
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning(
            "flow_server: cannot read manifest",
            path=str(manifest_path),
            error=str(exc),
        )
        return
    role = manifest.get("role", AGENT_ROLE)
    flow_tools = set(manifest.get("flow_tools", []))
    implemented = {
        "give_me_work",
        "i_will_work_on",
        "i_have_committed",
        "i_am_done",
        "i_am_blocked",
        "i_am_idle",
        "claim_review",
        "pass",
        "fail",  # Phase 2
    }
    missing = flow_tools - implemented
    if missing:
        log.warning(
            "flow_server: manifest references unimplemented verbs",
            role=role,
            missing=sorted(missing),
        )


if __name__ == "__main__":
    _validate_role_compatibility()
    mcp.run()

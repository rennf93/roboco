"""roboco-flow MCP server — exposes intent verbs to agents.

Tools are role-scoped via the agent's spawn manifest. The orchestrator writes
``/app/tool-manifest.json`` into each spawned agent container with that role's
``flow_tools`` array; this module reads the manifest at import time and only
registers the verbs listed there. If the manifest is missing or unreadable
(e.g. local test runs without the bind mount) the full registry is registered
as a failsafe and a warning is logged.

The orchestrator's API still rejects verbs that don't match the agent's role
(defence-in-depth), but per-agent registration prevents the model from ever
*seeing* an off-role verb in its tool palette.
"""

from __future__ import annotations

import json
import os
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

_HEADERS = {"X-Agent-ID": AGENT_ID, "X-Agent-Role": AGENT_ROLE}
_TIMEOUT = 30

mcp = FastMCP("roboco-flow")
log = structlog.get_logger()


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


# ---------- Dev verbs ----------


def give_me_work() -> dict[str, Any]:
    """Get your current task or report idle. Returns task + context_briefing."""
    return _post(_role_path("give_me_work"), {})


def i_will_work_on(task_id: str, plan: str | None = None) -> dict[str, Any]:
    """Claim/start/recover a task. Works for pending, claimed, needs_revision."""
    return _post(_role_path("i_will_work_on"), {"task_id": task_id, "plan": plan})


def i_have_committed(message: str) -> dict[str, Any]:
    """Record that you made a commit. Auto-creates progress entry."""
    return _post(_role_path("i_have_committed"), {"message": message})


def submit_for_qa(task_id: str) -> dict[str, Any]:
    """Push your branch and open a PR. Run after your last commit, before i_am_done."""
    return _post(_role_path("submit_for_qa"), {"task_id": task_id})


def i_am_done(task_id: str, notes: str = "") -> dict[str, Any]:
    """Submit for QA. Strict — PR must be open (call submit_for_qa first)."""
    return _post(_role_path("i_am_done"), {"task_id": task_id, "notes": notes})


def i_am_blocked(task_id: str, reason: str) -> dict[str, Any]:
    """Escalate to PM. Logs a struggle journal entry."""
    return _post(_role_path("i_am_blocked"), {"task_id": task_id, "reason": reason})


def unclaim(task_id: str) -> dict[str, Any]:
    """Release this claim back to pending. Branch survives; task is unassigned."""
    return _post(_role_path("unclaim"), {"task_id": task_id})


def resume(task_id: str) -> dict[str, Any]:
    """Resume a paused task. Transitions paused → in_progress for the assignee."""
    return _post(_role_path("resume"), {"task_id": task_id})


def i_am_idle() -> dict[str, Any]:
    """Report no more work. Soft-blocks if you have unread A2A/mentions."""
    return _post(_role_path("i_am_idle"), {})


# ---------- QA verbs ----------


def claim_review(task_id: str) -> dict[str, Any]:
    """QA: claim a task for review. Returns PR diff + evidence inline."""
    return _post(_role_path("claim_review"), {"task_id": task_id})


def pass_review(task_id: str, notes: str) -> dict[str, Any]:
    """QA: accept the work. notes >= 80 chars; journal:learning required."""
    return _post(_role_path("pass"), {"task_id": task_id, "notes": notes})


def fail_review(task_id: str, issues: list[str]) -> dict[str, Any]:
    """QA: reject the work with issues. Each issue should be concrete and actionable."""
    return _post(_role_path("fail"), {"task_id": task_id, "issues": issues})


# ---------- Doc verbs ----------


def claim_doc_task(task_id: str) -> dict[str, Any]:
    """Doc: claim a task in awaiting_documentation state."""
    return _post(_role_path("claim_doc_task"), {"task_id": task_id})


def i_documented(task_id: str, notes: str, files: list[str]) -> dict[str, Any]:
    """Doc: mark documentation complete. files=['<doc-path>', ...]."""
    return _post(
        _role_path("i_documented"),
        {"task_id": task_id, "notes": notes, "files": files},
    )


# ---------- PM verbs ----------
# Cell PM + Main PM share: triage, unblock, complete, escalate_up


def triage() -> dict[str, Any]:
    """PM: get the most important task to act on next."""
    return _post(_role_path("triage"), {})


def triage_all() -> dict[str, Any]:
    """Main PM: triage across all teams."""
    return _post(_role_path("triage_all"), {})


def unblock(task_id: str, restore: bool = True) -> dict[str, Any]:
    """PM: unblock a task. restore=True (default) restores pre_block_state."""
    return _post(_role_path("unblock"), {"task_id": task_id, "restore": restore})


def complete(task_id: str, notes: str) -> dict[str, Any]:
    """PM: complete a task. Cell PM auto-merges PR; Main PM opens PR + escalates."""
    return _post(_role_path("complete"), {"task_id": task_id, "notes": notes})


def escalate_up(task_id: str, reason: str) -> dict[str, Any]:
    """PM/Doc/Dev: escalate to your role's escalation target."""
    return _post(_role_path("escalate_up"), {"task_id": task_id, "reason": reason})


# ---------- Board + Auditor verbs ----------
# Board (PO + Head Marketing) + Main PM share: escalate_to_ceo
# Auditor uses triage (already registered above) for read-only anomaly surfacing.


def escalate_to_ceo(task_id: str, reason: str) -> dict[str, Any]:
    """Board / Main PM: escalate a strategic task to CEO for final approval."""
    return _post(_role_path("escalate_to_ceo"), {"task_id": task_id, "reason": reason})


# ---------- Cell PM + Main PM extras ----------
# i_will_plan, delegate, submit_up, give_me_work — restore the pre-Phase-4
# PM lifecycle so PMs can drive parent tasks instead of stalling.


def i_will_plan(task_id: str, plan: str) -> dict[str, Any]:
    """PM: claim+start a pending parent task with a one-paragraph plan."""
    return _post(_role_path("i_will_plan"), {"task_id": task_id, "plan": plan})


def delegate(
    parent_task_id: str, title: str, description: str, body: dict
) -> dict[str, Any]:
    """PM: create a subtask of parent_task_id.

    Required body keys: ``assigned_to``, ``team``. Optional: ``task_type``,
    ``acceptance_criteria``, ``estimated_complexity``.
    """
    payload: dict[str, Any] = {
        "parent_task_id": parent_task_id,
        "title": title,
        "description": description,
    }
    payload.update(body)
    return _post(_role_path("delegate"), payload)


def submit_up(task_id: str, notes: str) -> dict[str, Any]:
    """Cell PM: bubble a finished cell-scope task up to the Main PM."""
    return _post(_role_path("submit_up"), {"task_id": task_id, "notes": notes})


# ---------- Tool registry ----------
#
# Maps the verb name an agent calls (matches manifest entries and the
# orchestrator's role-scoped API path) to the Python implementation.
# ``pass`` and ``fail`` are reserved keywords, so their Python implementations
# are renamed but registered under the original names.

_TOOLS: dict[str, Any] = {
    # dev
    "give_me_work": give_me_work,
    "i_will_work_on": i_will_work_on,
    "i_have_committed": i_have_committed,
    "submit_for_qa": submit_for_qa,
    "i_am_done": i_am_done,
    "i_am_blocked": i_am_blocked,
    "unclaim": unclaim,
    "resume": resume,
    "i_am_idle": i_am_idle,
    # qa
    "claim_review": claim_review,
    "pass": pass_review,
    "fail": fail_review,
    # doc
    "claim_doc_task": claim_doc_task,
    "i_documented": i_documented,
    # pm
    "triage": triage,
    "triage_all": triage_all,
    "unblock": unblock,
    "complete": complete,
    "escalate_up": escalate_up,
    "i_will_plan": i_will_plan,
    "delegate": delegate,
    "submit_up": submit_up,
    # board / main pm
    "escalate_to_ceo": escalate_to_ceo,
}


def _load_manifest_flow_tools() -> list[str] | None:
    """Read the spawn manifest and return its ``flow_tools`` list.

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
            "flow_server: cannot read manifest",
            path=str(manifest_path),
            error=str(exc),
        )
        return None
    flow_tools = manifest.get("flow_tools")
    if not isinstance(flow_tools, list):
        log.warning(
            "flow_server: manifest missing flow_tools list",
            path=str(manifest_path),
        )
        return None
    return [str(verb) for verb in flow_tools]


def _register_tools() -> list[str]:
    """Register MCP tools according to the manifest, or all tools as a failsafe.

    Returns the list of verb names actually registered.
    """
    allowed = _load_manifest_flow_tools()
    if allowed is None:
        log.warning(
            "flow_server: manifest unavailable; registering all flow verbs",
            role=AGENT_ROLE,
        )
        names = list(_TOOLS)
    else:
        unknown = [verb for verb in allowed if verb not in _TOOLS]
        if unknown:
            log.warning(
                "flow_server: manifest references unimplemented verbs",
                role=AGENT_ROLE,
                missing=sorted(unknown),
            )
        names = [verb for verb in allowed if verb in _TOOLS]

    for verb in names:
        mcp.tool(name=verb)(_TOOLS[verb])
    log.info(
        "flow_server: registered tools",
        role=AGENT_ROLE,
        tools=sorted(names),
    )
    return names


_REGISTERED_TOOLS = _register_tools()


if __name__ == "__main__":
    mcp.run()

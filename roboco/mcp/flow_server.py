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
# Where the per-agent SDK server lives (per-container loopback). The
# flow server POSTs /verb/attempted here so the per-verb circuit breaker
# can record rejections and tell us when to substitute circuit_open.
SDK_URL = os.environ.get("ROBOCO_SDK_URL", "http://localhost:9000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]

_TIMEOUT = 30
# Tight timeout for SDK loopback — the SDK is a local sidecar; anything
# slower than 2s is unhealthy and the gateway path must not stall on it.
_SDK_TIMEOUT = 2.0

# Envelope error kinds that count toward the per-verb circuit breaker.
# Mirrors agent_sdk.server._CIRCUIT_REJECTION_KINDS; the SDK is the
# authoritative side, but we filter here too so we only emit one POST
# for kinds the SDK will actually count.
_CIRCUIT_REJECTION_KINDS: frozenset[str] = frozenset(
    {"tracing_gap", "invalid_state", "not_authorized", "incomplete_input"}
)

mcp = FastMCP("roboco-flow")
log = structlog.get_logger()


def _build_headers() -> dict[str, str]:
    """Build per-call headers including a fresh X-Correlation-ID.

    The agent runtime is the first hop, so we mint a UUID per MCP call.
    The orchestrator's ``CorrelationIdMiddleware`` will accept this as the
    inbound id and bind it to the structlog context for the request, so
    every log line and the audit row carry the same id and the agent
    receives it back on the envelope.
    """
    return {
        "X-Agent-ID": AGENT_ID,
        "X-Agent-Role": AGENT_ROLE,
        "X-Correlation-ID": str(uuid.uuid4()),
    }


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    """POST a request to the orchestrator and return the JSON envelope.

    The orchestrator returns the standardized envelope on both success
    (2xx) and rejection (4xx). The MCP-side bridge surfaces the envelope
    in either case so agents see ``remediate`` / ``missing`` even on a
    4xx response. Only raises if the response has no parseable body
    (e.g., a 5xx with HTML error page or a network failure).

    Rejection envelopes (error in _CIRCUIT_REJECTION_KINDS) are forwarded
    to the local SDK's /verb/attempted so the per-verb circuit breaker
    can track them. If the SDK reports the breaker is now open, the
    original rejection is REPLACED with the circuit_open envelope before
    being returned to the agent — preventing further hammering on a verb
    that won't succeed. Successful (ok) envelopes never touch the SDK.
    """
    with httpx.Client(timeout=_TIMEOUT) as client:
        response = client.post(
            f"{ORCHESTRATOR_URL}{path}",
            headers=_build_headers(),
            json=body,
        )
        try:
            payload: dict[str, Any] = response.json()
        except (ValueError, json.JSONDecodeError):
            # No JSON body (HTML error page, empty body, etc). Surface the
            # status as a synthetic envelope so the agent gets a remediate
            # hint instead of a Python traceback.
            return {
                "error": "transport_error",
                "message": (
                    f"orchestrator returned HTTP {response.status_code}"
                    f" with no JSON body for {path}"
                ),
                "remediate": (
                    "check that the orchestrator is up and the route exists;"
                    " contact the human operator if this persists"
                ),
                "missing": [],
            }
    # Outside the orchestrator client context so the SDK call is its own
    # connection — keeps semantics independent and timeouts separated.
    return _record_and_check_circuit(path, body, payload)


def _verb_from_path(path: str) -> str:
    """Extract the verb name from a role-scoped flow path.

    ``/api/v1/flow/<role>/<verb>`` → ``<verb>``. Returns the original
    path if it doesn't match the expected shape (defensive — the breaker
    falls open downstream when the verb is unrecognized).
    """
    return path.rsplit("/", 1)[-1]


def _record_and_check_circuit(
    path: str,
    body: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Forward a gateway rejection to the SDK breaker; maybe substitute.

    For successful (ok) envelopes this is a no-op — only rejections of
    kind tracing_gap / invalid_state / not_authorized / incomplete_input
    are reported. When the SDK responds with ``open=true`` we replace the
    original rejection with the wire-format ``circuit_open`` envelope so
    the agent stops retrying.

    Best-effort: SDK unreachable, slow, or malformed response → return
    the original payload. The breaker is a safety net; it must never
    break the gateway path.
    """
    # Gateway envelopes use a string `error` (kind); RobocoError-derived
    # exceptions surface a dict-shaped error via FastAPI's middleware. Only
    # string kinds count toward the breaker; dicts pass straight through.
    rejection_kind = payload.get("error")
    if not isinstance(rejection_kind, str):
        return payload
    if rejection_kind not in _CIRCUIT_REJECTION_KINDS:
        return payload

    verb = _verb_from_path(path)
    task_id = body.get("task_id")
    try:
        with httpx.Client(timeout=_SDK_TIMEOUT) as client:
            resp = client.post(
                f"{SDK_URL}/verb/attempted",
                json={
                    "verb": verb,
                    "task_id": str(task_id) if task_id is not None else None,
                    "rejection_kind": rejection_kind,
                },
            )
            status = resp.json()
    except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as exc:
        # Fail open: agent sees the original rejection. Log so operators
        # notice an SDK that's down — the gateway keeps working.
        log.warning(
            "flow_server: SDK /verb/attempted unreachable; breaker bypassed",
            verb=verb,
            task_id=task_id,
            error=str(exc),
        )
        return payload

    if status.get("open") and isinstance(status.get("circuit_envelope"), dict):
        circuit_env: dict[str, Any] = status["circuit_envelope"]
        log.info(
            "flow_server: circuit_open substituted for rejection",
            verb=verb,
            task_id=task_id,
            attempts=status.get("attempts"),
            limit=status.get("limit"),
        )
        return circuit_env
    return payload


# Board route serves Product Owner + Head Marketing under one prefix.
# AgentRole values that map to a different URL segment go here; everything
# else passes through unchanged so route prefix == role name (developer,
# qa, documenter, cell_pm, main_pm, auditor).
_ROLE_TO_ROUTE_PREFIX: dict[str, str] = {
    "product_owner": "board",
    "head_marketing": "board",
}
_ROUTE_PREFIX = _ROLE_TO_ROUTE_PREFIX.get(AGENT_ROLE, AGENT_ROLE)


def _role_path(verb: str) -> str:
    """Build the role-scoped /api/v1/flow/<route>/<verb> path."""
    return f"/api/v1/flow/{_ROUTE_PREFIX}/{verb}"


# ---------- Dev verbs ----------


def give_me_work() -> dict[str, Any]:
    """Get your current task or report idle. Returns task + context_briefing."""
    return _post(_role_path("give_me_work"), {})


def i_will_work_on(
    task_id: str,
    plan: str | None = None,
    steps: list[dict[str, str]] | None = None,
    technical_considerations: list[str] | None = None,
    risks: list[dict[str, str]] | None = None,
    open_questions: list[dict[str, str | bool]] | None = None,
) -> dict[str, Any]:
    """Claim/start/recover a task. Works for pending, claimed, needs_revision.

    On a FRESH claim a developer authors the SAME rich plan a PM does, so the
    task's Plan tab is fully populated for audit/tracing — the gateway's
    ``_dev_plan_gate`` rejects a thin one. Re-entry / recovery claims
    (already-claimed, in_progress, needs_revision) do NOT re-supply any of
    this; the gateway short-circuits before the gate.

    Args:
        task_id: UUID of the task you are claiming.
        plan: 2-4 sentences (>= 150 chars) describing HOW you will implement
            this. Doubles as the plan's "Approach".
        steps: Ordered execution checklist — list of
            ``{"title": "...", "description": "..."}`` with every description
            substantive. Becomes the plan's sub-tasks AND the progress
            checklist (completing a step advances %).
        technical_considerations: Bullet list (strings) of architectural /
            library / approach notes.
        risks: List of ``{"risk": "...", "mitigation": "..."}`` entries.
        open_questions: Optional list of ``{"question": "...",
            "answered": false}`` entries.
    """
    return _post(
        _role_path("i_will_work_on"),
        {
            "task_id": task_id,
            "plan": plan,
            "steps": steps or [],
            "technical_considerations": technical_considerations or [],
            "risks": risks or [],
            "open_questions": open_questions or [],
        },
    )


def open_pr(task_id: str) -> dict[str, Any]:
    """Push your branch and open a PR.

    Atomic: validates ALL preconditions (assignee, commits, no-prior-PR)
    BEFORE running any git side effects. After this verb returns success,
    call ``i_am_done(task_id, notes='...')`` to actually submit for QA.
    Renamed from ``submit_for_qa`` (2026-05-08) — the old name suggested
    this verb advanced the lifecycle, but it only opens the PR.
    """
    return _post(_role_path("open_pr"), {"task_id": task_id})


def i_am_done(task_id: str, notes: str = "") -> dict[str, Any]:
    """Submit for QA. Strict — PR must be open (call open_pr first)."""
    return _post(_role_path("i_am_done"), {"task_id": task_id, "notes": notes})


def i_am_blocked(
    task_id: str,
    reason: str,
    blocker_type: str | None = None,
    what_needed: str | None = None,
) -> dict[str, Any]:
    """Escalate to PM. Logs a struggle journal entry.

    Args:
        task_id: UUID of the task you're stuck on.
        reason: One paragraph describing the blocker.
        blocker_type: One of ``external`` | ``internal`` | ``question`` |
            ``dependency``. Optional but strongly preferred — the PM
            triages by class. Pre-gateway parity.
        what_needed: Concrete description of what would unblock the
            task. Pre-gateway parity.
    """
    return _post(
        _role_path("i_am_blocked"),
        {
            "task_id": task_id,
            "reason": reason,
            "blocker_type": blocker_type,
            "what_needed": what_needed,
        },
    )


def unclaim(task_id: str) -> dict[str, Any]:
    """Release this claim back to pending. Branch survives; task is unassigned."""
    return _post(_role_path("unclaim"), {"task_id": task_id})


def reassign(task_id: str, new_assignee: str) -> dict[str, Any]:
    """Cell PM: hand a claimed/in_progress task to another dev in your own cell.

    The branch is keyed to the task, so the work-in-progress survives;
    `new_assignee` is a developer slug in your cell (e.g. `be-dev-2`).
    """
    return _post(
        _role_path("reassign"), {"task_id": task_id, "new_assignee": new_assignee}
    )


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


def i_will_plan(
    task_id: str,
    plan: str,
    approach: str = "",
    sub_tasks: list[dict[str, str]] | None = None,
    technical_considerations: list[str] | None = None,
    risks: list[dict[str, str]] | None = None,
    open_questions: list[dict[str, str | bool]] | None = None,
) -> dict[str, Any]:
    """PM: claim+start a pending parent task with a structured plan.

    Args:
        task_id: UUID of the task you are planning.
        plan: One-paragraph narrative (the agent-facing summary).
        approach: 2-4 sentences describing the high-level approach for the
            Plan tab. Required for non-trivial tasks; empty string is allowed
            but produces an unpopulated Plan view.
        sub_tasks: Decomposition of this task into sub-units, each
            ``{"title": "...", "description": "..."}``. The gateway assigns
            stable ids + order server-side. Populates the Plan tab's
            Sub-Tasks section. Pre-gateway parity.
        technical_considerations: Bullet list of architectural / library /
            constraint notes. Each item is a single string.
        risks: List of {"risk": "...", "mitigation": "..."} entries.
        open_questions: List of {"question": "...", "answered": false} entries.
    """
    return _post(
        _role_path("i_will_plan"),
        {
            "task_id": task_id,
            "plan": plan,
            "approach": approach,
            "sub_tasks": sub_tasks or [],
            "technical_considerations": technical_considerations or [],
            "risks": risks or [],
            "open_questions": open_questions or [],
        },
    )


def delegate(
    parent_task_id: str,
    title: str,
    description: str,
    assigned_to: str,
    team: str,
    task_type: str,
    nature: str,
    acceptance_criteria: list[str],
    estimated_complexity: str = "medium",
) -> dict[str, Any]:
    """PM: create a subtask of parent_task_id.

    Args:
        parent_task_id: UUID of the parent task.
        title: Short imperative title.
        description: Multi-paragraph description with context (>=20 chars).
        assigned_to: Agent slug receiving the task (e.g. "be-dev-1").
        team: One of "backend" | "frontend" | "ux_ui" | "board" | "main_pm".
        task_type: One of "code" | "documentation" | "research" | "planning"
            | "design" | "administrative".
        nature: One of "technical" | "non_technical".
        acceptance_criteria: Non-empty list of verifiable outcome strings.
        estimated_complexity: One of "low" | "medium" | "high". Default "medium".
    """
    return _post(
        _role_path("delegate"),
        {
            "parent_task_id": parent_task_id,
            "title": title,
            "description": description,
            "assigned_to": assigned_to,
            "team": team,
            "task_type": task_type,
            "nature": nature,
            "acceptance_criteria": acceptance_criteria,
            "estimated_complexity": estimated_complexity,
        },
    )


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
    "open_pr": open_pr,
    "i_am_done": i_am_done,
    "i_am_blocked": i_am_blocked,
    "unclaim": unclaim,
    "reassign": reassign,
    "resume": resume,
    "i_am_idle": i_am_idle,
    # qa — keys are the public MCP tool names (what agents see and prompts
    # advertise). `pass`/`fail` are Python keywords so the IntentSpec uses
    # `pass_review`/`fail_review` internally; the public-name mapping below
    # bridges the two so the manifest's IntentSpec entries register as
    # `mcp__roboco-flow__pass` / `mcp__roboco-flow__fail`.
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


# IntentSpec verb names → MCP public tool names. The IntentSpec layer uses
# Python-friendly identifiers (no reserved keywords); the MCP layer exposes
# the user-facing verb name. Dogfooding surfaced this gap: the manifest carried
# `pass_review`/`fail_review` (IntentSpec names) but flow_server only had
# `pass`/`fail` keys, so QA's tools were silently dropped at registration.
_INTENT_TO_PUBLIC: dict[str, str] = {
    "pass_review": "pass",
    "fail_review": "fail",
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
    """Register MCP tools according to the manifest. Fails loud if absent.

    The manifest is the role-authoritative tool list. Falling back to
    all-verbs registration (the previous behaviour) caused PMs to see
    developer/QA verbs and call them at wrong URLs (404s). We now refuse
    to start without the manifest.

    Returns the list of verb names actually registered.
    """
    allowed = _load_manifest_flow_tools()
    if allowed is None:
        manifest_path = os.environ.get(
            "ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"
        )
        msg = (
            f"flow_server: manifest unavailable at {manifest_path};"
            f" refusing to register all-verbs fallback (would let"
            f" {AGENT_ROLE!r} call off-role verbs at wrong URLs)."
            f" Check that the orchestrator wrote the manifest to its"
            f" /app/manifests/ directory and that the agent container"
            f" has the bind-mount."
        )
        log.error("flow_server: manifest missing", role=AGENT_ROLE, path=manifest_path)
        raise RuntimeError(msg)
    public = [_INTENT_TO_PUBLIC.get(verb, verb) for verb in allowed]
    unknown = [verb for verb in public if verb not in _TOOLS]
    if unknown:
        log.warning(
            "flow_server: manifest references unimplemented verbs",
            role=AGENT_ROLE,
            missing=sorted(unknown),
        )
    names = [verb for verb in public if verb in _TOOLS]

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

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
    """POST a request to the orchestrator and return the JSON envelope.

    Mirrors flow_server._post: surfaces the orchestrator's envelope on
    both 2xx and 4xx so the agent always sees ``remediate``. Only
    fabricates a transport_error envelope when the body is unparseable.
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
        return payload


def commit(message: str, files: list[str] | None = None) -> dict[str, Any]:
    """Make a git commit. [task-id] prefix auto-applied. Validates message."""
    return _post("/api/v2/do/commit", {"message": message, "files": files})


def note(
    text: str,
    scope: str = "note",
    task_id: str | None = None,
    title: str | None = None,
    context: str | None = None,
    options: list[str] | None = None,
    chosen: str | None = None,
    rationale: str | None = None,
    consequences: str | None = None,
    what_done: str | None = None,
    what_learned: str | None = None,
    what_struggled: str | None = None,
    next_steps: str | None = None,
) -> dict[str, Any]:
    """Write a journal entry. scope in note|decision|reflect|learning|struggle.

    ``text`` is always the short summary (one paragraph max). For ``decision``
    and ``reflect`` scopes, fill the scope-specific structured fields so the
    panel renders them as named sections — pre-gateway parity:

    - decision: ``context`` (the situation), ``options`` (list of strings,
      one per alternative considered), ``chosen`` (the alternative you took),
      ``rationale`` (why), ``consequences`` (what this commits us to)
    - reflect: ``what_done`` (literal output), ``what_learned`` (new info),
      ``what_struggled`` (where you got stuck), ``next_steps`` (follow-ups)

    Other scopes (note / learning / struggle) just need ``text``.
    """
    return _post(
        "/api/v2/do/note",
        {
            "text": text,
            "scope": scope,
            "task_id": task_id,
            "title": title,
            "context": context,
            "options": options,
            "chosen": chosen,
            "rationale": rationale,
            "consequences": consequences,
            "what_done": what_done,
            "what_learned": what_learned,
            "what_struggled": what_struggled,
            "next_steps": next_steps,
        },
    )


def say(channel: str, text: str, task_id: str | None = None) -> dict[str, Any]:
    """Post to a channel. task_id auto-injected if you have an active task.

    Args:
        channel: Channel slug WITHOUT leading `#`. Valid values:
            - Cell channels: `backend-cell`, `frontend-cell`, `uxui-cell`
            - Cross-cell: `dev-all`, `qa-all`, `pm-all`, `doc-all`
            - Management: `main-pm-board`, `board-private`
            - Broadcast: `announcements`, `all-hands`
            Write access varies by role — gateway returns `not_authorized` if
            you cannot write to the requested channel; the error lists which
            channels you can write to.
        text: Message body.
        task_id: Optional; auto-filled from your active task if omitted.
    """
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
    """A2A message. Auto-creates conversation; auto-resolves skill if needed.

    Args:
        recipient: Target agent slug (e.g. `be-pm`, `be-dev-1`, `ceo`).
        text: Message body.
        task_id: Optional; auto-filled from your active task if omitted.
        skill: Optional skill slug to scope the conversation.
    """
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


# ---------- Wave 1 — pre-gateway parity ----------


def progress(task_id: str, message: str, percentage: int) -> dict[str, Any]:
    """Append a narrative progress update to YOUR active task.

    Args:
        task_id: UUID of the task you're working on.
        message: One-paragraph summary of what just landed.
        percentage: 0..100 inclusive. Rough completion estimate; bump it as
            you make progress so PM/QA can see velocity.

    Populates the panel's Progress tab. Use this in addition to ``commit``
    — commits are git refs; progress is narrative.
    """
    return _post(
        "/api/v2/do/progress",
        {"task_id": task_id, "message": message, "percentage": percentage},
    )


def open_session(
    task_id: str,
    channel: str,
    topic: str,
    relationship_type: str = "discussion",
    group_id: str | None = None,
) -> dict[str, Any]:
    """PM creates a discussion session linked to a task.

    Args:
        task_id: UUID of the task this session discusses.
        channel: Channel slug without `#` (e.g. ``backend-cell``).
        topic: Short topic for the session (≤200 chars).
        relationship_type: ``discussion`` | ``planning`` | ``review`` |
            ``retrospective``.
        group_id: Optional UUID to place the session under a specific group.

    Populates the panel's Sessions tab. Only PM-or-up roles can open sessions
    — devs / QA / docs participate via channels and DMs.
    """
    return _post(
        "/api/v2/do/open_session",
        {
            "task_id": task_id,
            "channel": channel,
            "topic": topic,
            "relationship_type": relationship_type,
            "group_id": group_id,
        },
    )


def link_session(
    session_id: str,
    task_id: str,
    is_primary: bool = False,
    relationship_type: str = "discussion",
) -> dict[str, Any]:
    """Link an existing session to a task (idempotent).

    Use when an existing discussion now covers a new task too. You must
    own the task you're linking; cross-agent linking is denied.
    """
    return _post(
        "/api/v2/do/link_session",
        {
            "session_id": session_id,
            "task_id": task_id,
            "is_primary": is_primary,
            "relationship_type": relationship_type,
        },
    )


def notify_list(
    unread_only: bool = True,
    pending_ack_only: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    """Read your notification inbox.

    Call this when ``i_am_idle()`` soft-blocks you with an "unread A2A or
    @mentions" message — list, read each one with ``notify_get``, ack with
    ``notify_ack``, then idle again.
    """
    return _post(
        "/api/v2/do/notify_list",
        {
            "unread_only": unread_only,
            "pending_ack_only": pending_ack_only,
            "limit": limit,
        },
    )


def notify_get(notification_id: str) -> dict[str, Any]:
    """Read one notification (marks it as read)."""
    return _post(
        "/api/v2/do/notify_get",
        {"notification_id": notification_id},
    )


def notify_ack(notification_id: str) -> dict[str, Any]:
    """Acknowledge a notification you've handled.

    The gateway tracks who has acked which notification — required for
    ``requires_ack=true`` notifications before ``i_am_idle`` will let you
    exit cleanly.
    """
    return _post(
        "/api/v2/do/notify_ack",
        {"notification_id": notification_id},
    )


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
    "progress": progress,
    "open_session": open_session,
    "link_session": link_session,
    "notify_list": notify_list,
    "notify_get": notify_get,
    "notify_ack": notify_ack,
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
    """Register MCP tools according to the manifest. Fails loud if absent.

    Mirrors flow_server's behaviour: refuse to start if the manifest is
    missing rather than silently exposing the full do-tool set (which
    includes ``commit`` — the role-gate would reject it server-side, but
    the agent shouldn't see it on its tool palette in the first place).

    Returns the list of tool names actually registered.
    """
    allowed = _load_manifest_do_tools()
    if allowed is None:
        manifest_path = os.environ.get(
            "ROBOCO_TOOL_MANIFEST_PATH", "/app/tool-manifest.json"
        )
        msg = (
            f"do_server: manifest unavailable at {manifest_path};"
            f" refusing to register all-tools fallback for role"
            f" {AGENT_ROLE!r}. Check the orchestrator manifest mount."
        )
        log.error("do_server: manifest missing", role=AGENT_ROLE, path=manifest_path)
        raise RuntimeError(msg)
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

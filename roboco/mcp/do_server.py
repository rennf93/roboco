"""roboco-do MCP server — smart-wrapped content tools.

Forwards to /api/v1/do/* on the orchestrator. Tools are role-scoped at *spawn*
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
# Per-agent SDK loopback for the per-verb circuit breaker.
SDK_URL = os.environ.get("ROBOCO_SDK_URL", "http://localhost:9000")
AGENT_ID = os.environ["ROBOCO_AGENT_ID"]
AGENT_ROLE = os.environ["ROBOCO_AGENT_ROLE"]

_TIMEOUT = 30
# Tight timeout for SDK loopback — local sidecar; gateway path must not stall.
_SDK_TIMEOUT = 2.0

# Envelope error kinds that count toward the per-verb circuit breaker.
# Mirrors flow_server._CIRCUIT_REJECTION_KINDS — agent_sdk.server is the
# authoritative side; the same set must be applied here so the do-server
# (content tools) gets the same protection as flow-server (intent verbs).
# Dogfooding surfaced the gap: `note(scope='decision')` looped 8 times
# returning `incomplete_input` with no breaker.
_CIRCUIT_REJECTION_KINDS: frozenset[str] = frozenset(
    {"tracing_gap", "invalid_state", "not_authorized", "incomplete_input"}
)

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

    Rejection envelopes (error in _CIRCUIT_REJECTION_KINDS) are forwarded
    to the local SDK's /verb/attempted so the per-verb circuit breaker
    can track them. If the SDK reports open, the original rejection is
    REPLACED with circuit_open. Dogfooding surfaced the gap: do-server had
    no breaker and `note(scope='decision')` looped 8 times returning
    incomplete_input.
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
    # Outside the orchestrator client so the SDK call is its own connection.
    return _record_and_check_circuit(path, body, payload)


def _verb_from_path(path: str) -> str:
    """Extract the verb name from a do-server path.

    ``/api/v1/do/<verb>`` → ``<verb>``. Returns the original path if it
    doesn't match the expected shape (defensive — breaker falls open
    downstream when the verb is unrecognized).
    """
    return path.rsplit("/", 1)[-1]


def _record_and_check_circuit(
    path: str,
    body: dict[str, Any],
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Forward a content-tool rejection to the SDK breaker; maybe substitute.

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
    # exceptions surface a dict-shaped error via FastAPI's middleware
    # (a TypeError on `dict in frozenset`). Defend against the
    # dict shape — only string kinds count toward the breaker, dicts pass
    # straight through.
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
        log.warning(
            "do_server: SDK /verb/attempted unreachable; breaker bypassed",
            verb=verb,
            task_id=task_id,
            error=str(exc),
        )
        return payload

    if status.get("open") and isinstance(status.get("circuit_envelope"), dict):
        circuit_env: dict[str, Any] = status["circuit_envelope"]
        log.info(
            "do_server: circuit_open substituted for rejection",
            verb=verb,
            task_id=task_id,
            attempts=status.get("attempts"),
            limit=status.get("limit"),
        )
        return circuit_env
    return payload


def commit(message: str, files: list[str] | None = None) -> dict[str, Any]:
    """Make a git commit. [task-id] prefix auto-applied. Validates message."""
    return _post("/api/v1/do/commit", {"message": message, "files": files})


def note(
    text: str,
    scope: str = "note",
    task_id: str | None = None,
    title: str | None = None,
    context: str = "",
    options: list[dict[str, str]] | dict[str, str] | None = None,
    chosen: str = "",
    rationale: str = "",
    consequences: list[str] | str | None = None,
    what_done: str = "",
    what_learned: str = "",
    what_struggled: str = "",
    next_steps: list[str] | str | None = None,
) -> dict[str, Any]:
    """Write a journal entry. scope in note|decision|reflect|learning|struggle.

    ``text`` is always the short summary (one paragraph max). For ``decision``
    and ``reflect`` scopes the structured fields are RECOMMENDED — fill what
    you can. The note is always recorded; missing narrative fields default to
    a visible placeholder rather than being rejected.

    - decision: ``context`` (situation), ``options`` (list of dicts
      ``{name, pros, cons}`` — a single dict is accepted), ``chosen`` (which
      option), ``rationale`` (why), ``consequences`` (list of strings — what
      this commits us to; a single string is accepted)
    - reflect: ``what_done`` (literal output), ``what_learned`` (new info),
      ``what_struggled`` (where you got stuck), ``next_steps`` (list of
      follow-up strings; a single string is accepted)

    List-typed fields (``options``, ``consequences``, ``next_steps``) tolerate
    a lone value — pass either a list or a single item.

    Other scopes (note / learning / struggle) just need ``text``.
    """
    return _post(
        "/api/v1/do/note",
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
        "/api/v1/do/say",
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
        "/api/v1/do/dm",
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
        "/api/v1/do/notify",
        {
            "target": target,
            "text": text,
            "priority": priority,
            "task_id": task_id,
        },
    )


def evidence(task_id: str) -> dict[str, Any]:
    """Inspect a task's PR diff, commits, files. Fetches dev branch into workspace."""
    return _post("/api/v1/do/evidence", {"task_id": task_id})


# ---------- Wave 1 — pre-gateway parity ----------


def progress(
    task_id: str,
    message: str,
    plan_step: str | None = None,
    percentage: int | None = None,
) -> dict[str, Any]:
    """Record progress on YOUR active task — the % is computed for you.

    Your plan's steps (sub_tasks) ARE the progress checklist. As you
    FINISH each step, call this with ``plan_step`` set to that step's id
    or its 1-based order; it is marked complete and the percentage is
    derived from completed/total — you do NOT set the percentage and
    cannot game it.

    You may ALSO post a narrative update WITHOUT ``plan_step`` for an
    important mid-step milestone (it documents the "why" and carries the
    current derived %). Keep these to meaningful moments — not every
    tool call.

    Args:
        task_id: UUID of the task you're working on.
        message: One-paragraph summary of what just landed.
        plan_step: The sub_task id (or its 1-based order) you just
            COMPLETED. Omit for a narrative-only milestone update.
        percentage: Ignored when the task has a plan checklist (the norm).
            Only used as a fallback for tasks with no sub_tasks.

    Populates the panel's Progress tab. Use in addition to ``commit`` —
    commits are git refs; progress maps to your plan.
    """
    body: dict[str, Any] = {"task_id": task_id, "message": message}
    if plan_step is not None:
        body["plan_step"] = plan_step
    if percentage is not None:
        body["percentage"] = percentage
    return _post("/api/v1/do/progress", body)


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
        "/api/v1/do/open_session",
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
        "/api/v1/do/link_session",
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
        "/api/v1/do/notify_list",
        {
            "unread_only": unread_only,
            "pending_ack_only": pending_ack_only,
            "limit": limit,
        },
    )


def notify_get(notification_id: str) -> dict[str, Any]:
    """Read one notification (marks it as read)."""
    return _post(
        "/api/v1/do/notify_get",
        {"notification_id": notification_id},
    )


def notify_ack(notification_id: str) -> dict[str, Any]:
    """Acknowledge a notification you've handled.

    The gateway tracks who has acked which notification — required for
    ``requires_ack=true`` notifications before ``i_am_idle`` will let you
    exit cleanly.
    """
    return _post(
        "/api/v1/do/notify_ack",
        {"notification_id": notification_id},
    )


def channels() -> dict[str, Any]:
    """List the channel slugs you can read / write.

    Use this BEFORE ``say(channel=...)`` if you're unsure of the slug —
    inventing slugs returns ``Channel not found``. Returns
    ``{writable: [...], readable: [...]}``.
    """
    return _post("/api/v1/do/channels", {})


def pr_update(
    task_id: str,
    title: str | None = None,
    body: str | None = None,
    reviewers: list[str] | None = None,
) -> dict[str, Any]:
    """Update an existing PR's title, body, and/or requested reviewers.

    Use after ``open_pr`` when you need to correct the title/body or
    request a reviewer. At least one of ``title``, ``body``, or
    ``reviewers`` must be provided — passing all three None is rejected
    with ``invalid_state`` before any GitHub call.

    Args:
        task_id: UUID of the task whose PR you're editing.
        title: Replacement PR title (omit to leave unchanged).
        body: Replacement PR body markdown (omit to leave unchanged).
        reviewers: List of agent slugs to request as reviewers (e.g.
            ``["be-dev-2", "be-qa"]``). The gateway maps slugs to GitHub
            usernames where the project records that mapping, otherwise
            the slugs are forwarded as-is.

    Authorization: caller must be the task's assignee OR a PM on the
    task's team (cell_pm same-team, or main_pm cross-team). Anyone else
    receives ``not_authorized``.
    """
    return _post(
        "/api/v1/do/pr_update",
        {
            "task_id": task_id,
            "title": title,
            "body": body,
            "reviewers": reviewers,
        },
    )


def read_messages() -> dict[str, Any]:
    """Mark all your unread A2A direct messages as read.

    Call this when ``i_am_idle()`` soft-blocks you on unread A2A — it clears
    your direct-message inbox so you can idle. Notifications are separate: use
    ``notify_list`` / ``notify_get`` / ``notify_ack`` for those.
    """
    return _post("/api/v1/do/read_messages", {})


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
    "channels": channels,
    "pr_update": pr_update,
    "read_messages": read_messages,
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

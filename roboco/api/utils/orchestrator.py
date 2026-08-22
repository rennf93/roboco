"""
Orchestrator Route Helpers

Route-glue helpers backing roboco/api/routes/orchestrator.py.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Cookie, Header, HTTPException, status

from roboco.agents_config import CEO_AGENT_ID, _resolve_to_slug, verify_agent_token
from roboco.api.auth.backend import SESSION_COOKIE_NAME
from roboco.api.auth.session import resolve_session_user
from roboco.api.deps import _check_agent_auth_token, get_db, require_ceo_role
from roboco.config import settings
from roboco.db.base import get_db_context
from roboco.db.tables import TaskTable
from roboco.services.task import get_task_service


# Orchestrator control routes (spawn / stop / resolve-wait / mark-waiting,
# plus the read-only status views) are operator/CEO control surfaces — any
# client that could reach the API could previously spawn, stop, or
# manipulate any agent's runtime state. The guard mirrors the panel-token
# approach used by the WebSocket streams (DB-free): it binds the presented
# ``X-Agent-ID`` to a verified HMAC token and asserts the role is CEO. In
# dev (header-trust) mode a missing token is a no-op (the panel/operator
# flow keeps working), but a presented-but-forged token is still rejected —
# the same contract as the v1 flow role guards and the do router. CEO is the
# sole operator role; agents (developers/QA/PMs) drive the orchestrator via
# MCP verbs, not these HTTP routes, so a developer token is correctly 403'd
# here. The CEO role check itself delegates to ``require_ceo_role`` (#25 —
# the single source of truth shared with the release routes).
async def _require_ceo(
    x_agent_id: Annotated[str, Header(alias="X-Agent-ID")],
    x_agent_role: Annotated[str, Header(alias="X-Agent-Role")],
    x_agent_team: Annotated[str | None, Header(alias="X-Agent-Team")] = None,
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
    session_cookie: Annotated[str | None, Cookie(alias=SESSION_COOKIE_NAME)] = None,
) -> None:
    # Bind the role header to a verified token BEFORE trusting it (same
    # defense-in-depth contract as the v1 flow role guards in _role_dep.py).
    if not settings.cloud_auth_enabled:
        _check_agent_auth_token(x_agent_id, x_agent_role, x_agent_team, x_agent_token)
        require_ceo_role(x_agent_role, action="control the orchestrator")
        return
    # cloud_auth on: CEO HMAC token OR CEO session cookie (panel path).
    if x_agent_token:
        if not verify_agent_token(x_agent_token, CEO_AGENT_ID, "ceo", ""):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid X-Agent-Token — signature mismatch.",
            )
        require_ceo_role(x_agent_role, action="control the orchestrator")
        return
    async for db in get_db():
        user = await resolve_session_user(session_cookie, db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=(
                    "Cloud auth is enabled — a valid session "
                    "or agent token is required."
                ),
            )
        return


def _validated_agent_id(agent_id: str) -> str:
    """Reject an ``agent_id`` that could traverse a filesystem path downstream,
    then normalize it to the canonical slug the runtime addresses containers by.

    ``agent_id`` is an opaque slug / uuid the orchestrator assigns, but it is a
    request path parameter and flows into per-agent paths (e.g. the grok usage
    dir). Reject every traversal vector — empty, ``.`` / ``..``, a ``/`` or
    ``\\`` separator, or an embedded NUL — at the HTTP boundary with 422 before
    it reaches any path. Explicit guards (not a regex) so CodeQL models this as a
    path-injection barrier; the runtime ``_grok_usage_dir`` repeats the check as
    defense in depth for non-HTTP callers.

    A caller (e.g. the panel) may pass an agent's DB UUID instead of its slug —
    ``_resolve_to_slug`` maps it to the canonical slug so the runtime container
    (named ``roboco-agent-{slug}``) and instance registry are addressed
    consistently regardless of which identifier form was sent. An unknown UUID
    (not in the seed map) passes through unchanged, same as today.
    """
    if (
        not agent_id
        or agent_id in {".", ".."}
        or "/" in agent_id
        or "\\" in agent_id
        or "\x00" in agent_id
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid agent_id",
        )
    return _resolve_to_slug(agent_id)


def _build_manual_spawn_prompt(task: TaskTable, ceo_note: str | None) -> str:
    """Build the initial prompt for a CEO-triggered manual (panel) spawn.

    Mirrors the tone of dispatcher-built prompts (e.g. ``_build_pr_review_prompt``
    in the orchestrator): point the agent at the task by id/title/status and
    trust the gateway envelope's ``next`` / ``remediate`` to guide the actual
    claim verb, rather than enumerating per-role verbs here.
    """
    lines = [
        "You were manually spawned by the CEO to work a specific task.",
        "",
        f"TASK ID: {task.id}",
        f"TITLE: {task.title}",
        f"STATUS: {task.status.value}",
        "",
        "Claim it with the claim verb appropriate to your role and this "
        "task's current state, then proceed. Trust the gateway envelope's "
        "`next` / `remediate` fields to guide you rather than guessing.",
    ]
    if ceo_note:
        lines += ["", "== CEO NOTE ==", ceo_note]
    return "\n".join(lines)


async def _resolve_manual_spawn_prompt(
    task_id: str | None, ceo_message: str | None
) -> str | None:
    """Best-effort task-aware prompt for a manual panel spawn.

    Falls back to ``ceo_message`` unchanged (current behavior) on any lookup
    failure — bad ``task_id``, DB hiccup, task not found. Enrichment must
    never block a spawn the CEO already asked for; ``spawn_agent``'s own
    readiness gate is the real gatekeeper for an invalid/not-ready task.
    """
    if not task_id:
        return ceo_message
    try:
        async with get_db_context() as db:
            task = await get_task_service(db).get(UUID(task_id))
    except Exception:
        return ceo_message
    if task is None:
        return ceo_message
    return _build_manual_spawn_prompt(task, ceo_message)

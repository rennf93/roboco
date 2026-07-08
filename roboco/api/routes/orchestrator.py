"""
Orchestrator Routes

API endpoints for managing the Agent Orchestrator.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, status
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.agents_config import CEO_AGENT_ID, verify_agent_token
from roboco.api.auth.backend import SESSION_COOKIE_NAME
from roboco.api.auth.session import resolve_session_user
from roboco.api.deps import (
    _check_agent_auth_token,
    get_db,
    get_orchestrator,
    require_ceo_role,
    set_orchestrator,
)
from roboco.api.schemas.orchestrator import (
    AgentStatusResponse,
    OrchestratorStatusResponse,
    ResolveWaitRequest,
    SpawnAgentRequest,
    SpawnAgentResponse,
    WaitingAgentResponse,
)
from roboco.config import settings
from roboco.db.base import get_db_context
from roboco.db.tables import TaskTable
from roboco.runtime import AgentState
from roboco.runtime.orchestrator import AgentReadinessError
from roboco.security import guard_deco, prompt_injection_validator
from roboco.services.task import get_task_service

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]


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


router = APIRouter(dependencies=[Depends(_require_ceo)])

# Re-export set_orchestrator for bootstrap code
__all__ = ["router", "set_orchestrator"]


def _validated_agent_id(agent_id: str) -> str:
    """Reject an ``agent_id`` that could traverse a filesystem path downstream.

    ``agent_id`` is an opaque slug / uuid the orchestrator assigns, but it is a
    request path parameter and flows into per-agent paths (e.g. the grok usage
    dir). Reject every traversal vector — empty, ``.`` / ``..``, a ``/`` or
    ``\\`` separator, or an embedded NUL — at the HTTP boundary with 422 before
    it reaches any path. Explicit guards (not a regex) so CodeQL models this as a
    path-injection barrier; the runtime ``_grok_usage_dir`` repeats the check as
    defense in depth for non-HTTP callers.
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
    return agent_id


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "/status",
    response_model=OrchestratorStatusResponse,
    summary="Get orchestrator status",
    description="Get the overall status of the orchestrator and all agents.",
)
@guard_deco.rate_limit(requests=60, window=60)
async def get_status() -> OrchestratorStatusResponse:
    """Get orchestrator status."""
    orchestrator = get_orchestrator()
    summary = orchestrator.get_status_summary()

    agents = [
        AgentStatusResponse(
            agent_id=a["agent_id"],
            state=a["state"],
            task_id=a["task_id"],
            error_count=a["error_count"],
            started_at=datetime.fromisoformat(a["started_at"])
            if a["started_at"]
            else None,
            waiting_for=None,  # Will be filled from waiting records
        )
        for a in summary["agents"]
    ]

    # Add waiting_for info
    waiting = orchestrator.get_waiting_agents()
    for agent in agents:
        if agent.agent_id in waiting:
            agent.waiting_for = waiting[agent.agent_id].waiting_for

    return OrchestratorStatusResponse(
        total_agents=summary["total"],
        by_state=summary["by_state"],
        waiting_count=summary["waiting_count"],
        agents=agents,
    )


@router.get(
    "/agents/{agent_id}",
    response_model=AgentStatusResponse,
    summary="Get agent status",
    description="Get the status of a specific agent.",
)
async def get_agent_status(agent_id: str) -> AgentStatusResponse:
    """Get status of a specific agent."""
    agent_id = _validated_agent_id(agent_id)
    orchestrator = get_orchestrator()
    instance = orchestrator.get_instance(agent_id)

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} not found",
        )

    waiting = orchestrator.get_waiting_agents()
    waiting_for = waiting[agent_id].waiting_for if agent_id in waiting else None

    return AgentStatusResponse(
        agent_id=instance.agent_id,
        state=instance.state.value,
        task_id=instance.current_task_id,
        error_count=instance.error_count,
        started_at=instance.started_at,
        waiting_for=waiting_for,
    )


@router.get(
    "/waiting",
    response_model=list[WaitingAgentResponse],
    summary="Get waiting agents",
    description="Get all agents in WAITING_LONG state.",
)
async def get_waiting_agents() -> list[WaitingAgentResponse]:
    """Get all waiting agents."""
    orchestrator = get_orchestrator()
    waiting = orchestrator.get_waiting_agents()

    return [
        WaitingAgentResponse(
            agent_id=record.agent_id,
            task_id=record.task_id,
            waiting_for=record.waiting_for,
            waiting_since=record.waiting_since,
            context=record.context,
        )
        for record in waiting.values()
    ]


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


@router.post(
    "/agents/{agent_id}/spawn",
    response_model=SpawnAgentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Spawn agent",
    description="Spawn a Claude Code instance for an agent.",
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.block_clouds()
@guard_deco.usage_monitor(max_calls=30, window=3600)
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def spawn_agent(
    agent_id: str,
    data: SpawnAgentRequest | None = None,
) -> SpawnAgentResponse:
    """Spawn an agent."""
    agent_id = _validated_agent_id(agent_id)
    orchestrator = get_orchestrator()
    task_id = data.task_id if data else None
    ceo_message = data.initial_prompt if data else None
    prompt = await _resolve_manual_spawn_prompt(task_id, ceo_message)

    # Pre-check for already-running signaling (see return below). Snapshot the
    # instance identity BEFORE calling spawn_agent, which silently reuses a
    # running instance rather than erroring — dispatchers rely on that no-op
    # contract, so it stays untouched here.
    pre_existing = orchestrator.get_instance(agent_id)
    pre_active = pre_existing is not None and pre_existing.state not in (
        AgentState.OFFLINE,
        AgentState.WAITING_LONG,
    )
    pre_existing_id = getattr(pre_existing, "id", None)

    try:
        instance = await orchestrator.spawn_agent(
            agent_id=agent_id,
            initial_prompt=prompt,
            task_id=task_id,
            model=data.model if data else None,
            spawned_by="api.orchestrator.spawn",
        )
    except AgentReadinessError as e:
        # Expected, well-formed refusal (role/state mismatch, unmet
        # dependency, missing readiness criteria) — not a server crash.
        # 409 keeps it out of 5xx alerting and lets the panel surface the
        # real reason instead of a generic "server error".
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to spawn agent: {e}",
        ) from e

    # already_running: the pre-existing instance was active AND spawn_agent
    # handed back that exact same instance (identity, not state, since a
    # freshly-launched instance can share the same STARTING state as a
    # short-circuited one). AgentInstance.id is a fresh uuid4 per constructed
    # object, so equality here means no new instance was built.
    # ponytail: identity-compare across a pre/post HTTP-handler snapshot, not
    # inside the orchestrator's own spawn lock — a genuinely simultaneous
    # double-fire that races both pre-checks before either inserts its
    # instance can still slip through undetected here. The client-side
    # dedupe guard (SpawnAgentDialog) is the actual fix for that race;
    # upgrade this to an orchestrator-native signal if that ever proves
    # insufficient.
    already_running = (
        pre_active
        and pre_existing_id is not None
        and getattr(instance, "id", None) == pre_existing_id
    )

    return SpawnAgentResponse(
        agent_id=instance.agent_id,
        state=instance.state.value,
        task_id=instance.current_task_id,
        error_count=instance.error_count,
        started_at=instance.started_at,
        waiting_for=None,
        already_running=already_running,
    )


@router.post(
    "/agents/{agent_id}/stop",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop agent",
    description="Stop a running agent.",
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.block_clouds()
async def stop_agent(agent_id: str, graceful: bool = True) -> None:
    """Stop an agent."""
    agent_id = _validated_agent_id(agent_id)
    orchestrator = get_orchestrator()
    await orchestrator.stop_agent(agent_id, graceful=graceful)


@router.post(
    "/agents/{agent_id}/resolve-wait",
    response_model=AgentStatusResponse,
    summary="Resolve wait",
    description="Resolve a WAITING_LONG condition and respawn the agent.",
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.block_clouds()
async def resolve_wait(
    agent_id: str,
    data: ResolveWaitRequest,
) -> AgentStatusResponse:
    """Resolve a wait condition."""
    agent_id = _validated_agent_id(agent_id)
    orchestrator = get_orchestrator()

    instance = await orchestrator.resolve_wait(agent_id, data.resolution)

    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent {agent_id} is not in WAITING_LONG state",
        )

    return AgentStatusResponse(
        agent_id=instance.agent_id,
        state=instance.state.value,
        task_id=instance.current_task_id,
        error_count=instance.error_count,
        started_at=instance.started_at,
        waiting_for=None,
    )


@router.post(
    "/agents/{agent_id}/mark-waiting",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark waiting",
    description="Mark an agent as WAITING_LONG and terminate it.",
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.block_clouds()
async def mark_waiting(
    agent_id: str,
    waiting_for: str,
    task_id: str | None = None,
) -> None:
    """Mark an agent as waiting long."""
    agent_id = _validated_agent_id(agent_id)
    orchestrator = get_orchestrator()
    await orchestrator.mark_waiting_long(
        agent_id=agent_id,
        waiting_for=waiting_for,
        task_id=task_id,
    )

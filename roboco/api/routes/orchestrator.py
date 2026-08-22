"""
Orchestrator Routes

API endpoints for managing the Agent Orchestrator.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.api.deps import get_orchestrator, set_orchestrator
from roboco.api.schemas.orchestrator import (
    AgentStatusResponse,
    OrchestratorStatusResponse,
    ResolveWaitRequest,
    SpawnAgentRequest,
    SpawnAgentResponse,
    WaitingAgentResponse,
)
from roboco.api.utils.orchestrator import (
    _require_ceo,
    _resolve_manual_spawn_prompt,
    _validated_agent_id,
)
from roboco.runtime import AgentState
from roboco.runtime.orchestrator import AgentReadinessError
from roboco.security import guard_deco, prompt_injection_validator

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]

router = APIRouter(dependencies=[Depends(_require_ceo)])

# Re-export set_orchestrator for bootstrap code
__all__ = ["router", "set_orchestrator"]


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
    await orchestrator.stop_agent(
        agent_id, graceful=graceful, stop_reason="stop_agent_api"
    )


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

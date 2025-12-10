"""
Orchestrator Routes

API endpoints for managing the Agent Orchestrator.
"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from roboco.runtime import AgentOrchestrator, AgentState

router = APIRouter()

# Global orchestrator instance (set by bootstrap)
_orchestrator: AgentOrchestrator | None = None


def set_orchestrator(orchestrator: AgentOrchestrator) -> None:
    """Set the global orchestrator instance."""
    global _orchestrator
    _orchestrator = orchestrator


def get_orchestrator() -> AgentOrchestrator:
    """Get the global orchestrator instance."""
    if _orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized",
        )
    return _orchestrator


# =============================================================================
# Response Models
# =============================================================================


class AgentStatusResponse(BaseModel):
    """Status of a single agent."""

    agent_id: str
    state: str
    task_id: str | None
    error_count: int
    started_at: datetime | None
    waiting_for: str | None


class OrchestratorStatusResponse(BaseModel):
    """Overall orchestrator status."""

    total_agents: int
    by_state: dict[str, int]
    waiting_count: int
    agents: list[AgentStatusResponse]


class WaitingAgentResponse(BaseModel):
    """Agent in WAITING_LONG state."""

    agent_id: str
    task_id: str | None
    waiting_for: str
    waiting_since: datetime
    context: dict[str, Any]


class SpawnAgentRequest(BaseModel):
    """Request to spawn an agent."""

    agent_id: str
    initial_prompt: str | None = None
    task_id: str | None = None
    model: str | None = None


class ResolveWaitRequest(BaseModel):
    """Request to resolve a wait condition."""

    resolution: dict[str, Any] = Field(default_factory=dict)


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "/status",
    response_model=OrchestratorStatusResponse,
    summary="Get orchestrator status",
    description="Get the overall status of the orchestrator and all agents.",
)
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
    response_model=AgentStatusResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Spawn agent",
    description="Spawn a Claude Code instance for an agent.",
)
async def spawn_agent(
    agent_id: str,
    data: SpawnAgentRequest | None = None,
) -> AgentStatusResponse:
    """Spawn an agent."""
    orchestrator = get_orchestrator()

    try:
        instance = await orchestrator.spawn_agent(
            agent_id=agent_id,
            initial_prompt=data.initial_prompt if data else None,
            task_id=data.task_id if data else None,
            model=data.model if data else None,
        )
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

    return AgentStatusResponse(
        agent_id=instance.agent_id,
        state=instance.state.value,
        task_id=instance.current_task_id,
        error_count=instance.error_count,
        started_at=instance.started_at,
        waiting_for=None,
    )


@router.post(
    "/agents/{agent_id}/stop",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Stop agent",
    description="Stop a running agent.",
)
async def stop_agent(agent_id: str, graceful: bool = True) -> None:
    """Stop an agent."""
    orchestrator = get_orchestrator()
    await orchestrator.stop_agent(agent_id, graceful=graceful)


@router.post(
    "/agents/{agent_id}/resolve-wait",
    response_model=AgentStatusResponse,
    summary="Resolve wait",
    description="Resolve a WAITING_LONG condition and respawn the agent.",
)
async def resolve_wait(
    agent_id: str,
    data: ResolveWaitRequest,
) -> AgentStatusResponse:
    """Resolve a wait condition."""
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
async def mark_waiting(
    agent_id: str,
    waiting_for: str,
    task_id: str | None = None,
) -> None:
    """Mark an agent as waiting long."""
    orchestrator = get_orchestrator()
    await orchestrator.mark_waiting_long(
        agent_id=agent_id,
        waiting_for=waiting_for,
        task_id=task_id,
    )

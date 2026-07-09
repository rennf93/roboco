"""
Orchestrator API Schemas

Request/response models for agent orchestrator endpoints.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


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


class SpawnAgentResponse(AgentStatusResponse):
    """Response to a spawn request.

    ``already_running`` is True when the spawn was a no-op because the agent
    was already active — the route detected a pre-existing active instance
    whose identity matches the instance returned by ``spawn_agent`` (which
    silently reuses a running instance rather than erroring, so dispatchers
    keep their existing no-op semantics). False for a genuine new spawn.
    """

    already_running: bool = False


class ResolveWaitRequest(BaseModel):
    """Request to resolve a wait condition."""

    resolution: dict[str, Any] = Field(default_factory=dict)

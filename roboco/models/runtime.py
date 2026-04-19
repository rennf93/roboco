"""
Runtime Models

Domain types for the agent orchestrator system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class OrchestratorAgentState(str, Enum):
    """Agent lifecycle states in the orchestrator."""

    OFFLINE = "offline"
    STARTING = "starting"
    ACTIVE = "active"
    WAITING_SHORT = "waiting_short"  # Polling, agent still running
    WAITING_LONG = "waiting_long"  # Terminated, will respawn on event
    IDLE = "idle"
    STOPPING = "stopping"


@dataclass
class SpawnGitContext:
    """Git context passed when spawning an agent for a task."""

    project_slug: str | None = None
    branch_name: str | None = None


@dataclass
class OrchestratorAgentConfig:
    """Configuration for an agent in the orchestrator."""

    agent_id: str
    blueprint_path: Path
    model: str = "sonnet"  # sonnet, opus, haiku
    mcp_config_path: Path | None = None
    working_directory: Path | None = None
    # Git context for tasks requiring git workflow
    git_context: SpawnGitContext | None = None


@dataclass
class AgentInstance:
    """A running Claude Code agent instance (Docker container)."""

    id: UUID = field(default_factory=uuid4)
    agent_id: str = ""
    state: OrchestratorAgentState = OrchestratorAgentState.OFFLINE
    container_id: str | None = None  # Docker container ID
    config: OrchestratorAgentConfig | None = None
    started_at: datetime | None = None
    last_activity: datetime | None = None
    current_task_id: str | None = None
    error_count: int = 0
    waiting_for: str | None = None  # For WAITING_LONG state
    waiting_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id:
            self.id = uuid4()


@dataclass
class WaitingRecord:
    """Tracks what a WAITING_LONG agent is waiting for."""

    agent_id: str
    task_id: str | None
    waiting_for: str  # "blocker_resolution", "qa_result", "answer", "assignment"
    waiting_since: datetime
    context: dict[str, Any] = field(default_factory=dict)


# Model mapping for cost optimization
MODEL_MAP: dict[str, str] = {
    "opus": "claude-opus-4-7",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


# Default model by role
ROLE_MODEL_MAP: dict[str, str] = {
    "developer": "sonnet",
    "qa": "sonnet",
    "documenter": "haiku",
    "cell_pm": "sonnet",
    "main_pm": "sonnet",
    "auditor": "sonnet",
    "product_owner": "opus",
    "head_marketing": "opus",
    "ceo": "opus",
}

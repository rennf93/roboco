"""
Runtime Models

Domain types for the agent orchestrator system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class OrchestratorAgentState(StrEnum):
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
    # Short id (task id[:8]) of the task whose per-task worktree the agent
    # must edit in. Set only for tasks that carry a branch (a real worktree
    # exists under {clone_root}/.worktrees/{task_short_id}/); branchless
    # coordination roots leave it None so the spawn cwd falls back to the
    # clone root.
    task_short_id: str | None = None


@dataclass
class OrchestratorAgentConfig:
    """Configuration for an agent in the orchestrator."""

    agent_id: str
    blueprint_path: Path
    model: str = "sonnet"  # sonnet, opus, haiku, or any ollama-cloud tag
    mcp_config_path: Path | None = None
    working_directory: Path | None = None
    # Orchestrator-assigned Claude Code session id (passed to the agent CLI as
    # --session-id) so the agent's transcript can be located by id at finalize,
    # regardless of which project/cwd dir Claude Code writes it to.
    claude_session_id: str | None = None
    # Git context for tasks requiring git workflow
    git_context: SpawnGitContext | None = None
    # Pre-rendered SessionStart briefing mounted as /app/briefing.md
    briefing_path: Path | None = None
    # Provider routing, resolved from `model_assignments` at spawn.
    # provider_type drives `--model` CLI translation:
    # `"anthropic"` → short-name lookup through MODEL_MAP,
    # anything else (currently `"ollama_cloud"`) → pass `model` verbatim.
    # provider_base_url + provider_auth_token are both NULL for the
    # Anthropic default (container uses mounted ~/.claude credentials).
    provider_type: str = "anthropic"
    provider_base_url: str | None = None
    provider_auth_token: str | None = None


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
    # UUID of the agent_spawn_sessions row created at spawn time.
    # Used by _finalize_spawn_session for a direct-by-id lookup instead of a
    # fragile (agent_slug, ended_at IS NULL) query.
    usage_session_id: UUID | None = None

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
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}


# Default model by role
ROLE_MODEL_MAP: dict[str, str] = {
    "developer": "sonnet",
    "qa": "sonnet",
    "documenter": "haiku",
    "cell_pm": "sonnet",
    "main_pm": "opus",
    "auditor": "opus",
    "product_owner": "opus",
    "head_marketing": "opus",
    "ceo": "opus",
    # Intake interviewer — reads real code and drafts the spec; needs to be sharp.
    "prompter": "opus",
    # Secretary — carries CEO authority; needs strong judgment.
    "secretary": "opus",
}

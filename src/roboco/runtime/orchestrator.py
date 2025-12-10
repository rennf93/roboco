"""
Agent Orchestrator

Manages Claude Code instances for all RoboCo agents.
Handles spawning, monitoring, health checks, and graceful shutdown.
"""

import asyncio
import contextlib
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import structlog

from roboco.config import settings

logger = structlog.get_logger()


# =============================================================================
# AGENT STATE
# =============================================================================


class AgentState(str, Enum):
    """Agent lifecycle states."""

    OFFLINE = "offline"
    STARTING = "starting"
    ACTIVE = "active"
    WAITING_SHORT = "waiting_short"  # Polling, agent still running
    WAITING_LONG = "waiting_long"  # Terminated, will respawn on event
    IDLE = "idle"
    STOPPING = "stopping"


# =============================================================================
# AGENT CONFIGURATION
# =============================================================================


@dataclass
class AgentConfig:
    """Configuration for an agent."""

    agent_id: str
    blueprint_path: Path
    model: str = "sonnet"  # sonnet, opus, haiku
    mcp_config_path: Path | None = None
    working_directory: Path | None = None


# Model mapping for cost optimization
MODEL_MAP = {
    "sonnet": "claude-sonnet-4-20250514",
    "opus": "claude-opus-4-20250514",
    "haiku": "claude-haiku-4-20250514",
}


# Default model by role
ROLE_MODEL_MAP = {
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


# =============================================================================
# AGENT INSTANCE
# =============================================================================


@dataclass
class AgentInstance:
    """A running Claude Code agent instance."""

    id: UUID = field(default_factory=uuid4)
    agent_id: str = ""
    state: AgentState = AgentState.OFFLINE
    process: asyncio.subprocess.Process | None = None
    config: AgentConfig | None = None
    started_at: datetime | None = None
    last_activity: datetime | None = None
    current_task_id: str | None = None
    error_count: int = 0
    waiting_for: str | None = None  # For WAITING_LONG state
    waiting_context: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.id:
            self.id = uuid4()


# =============================================================================
# WAITING RECORD
# =============================================================================


@dataclass
class WaitingRecord:
    """Tracks what a WAITING_LONG agent is waiting for."""

    agent_id: str
    task_id: str | None
    waiting_for: str  # "blocker_resolution", "qa_result", "answer", "assignment"
    waiting_since: datetime
    context: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# ORCHESTRATOR
# =============================================================================


class AgentOrchestrator:
    """
    Manages Claude Code instances for all agents.

    Responsibilities:
    - Spawn agents with correct blueprints
    - Monitor health (heartbeat, errors)
    - Handle waiting states and respawning
    - Provide status API
    - Cost-efficient on-demand spawning
    """

    def __init__(
        self,
        blueprints_dir: Path | None = None,
        mcp_config_dir: Path | None = None,
    ):
        self.blueprints_dir = blueprints_dir or Path("agents/blueprints")
        self.mcp_config_dir = mcp_config_dir or Path(".mcp")

        self._instances: dict[str, AgentInstance] = {}
        self._waiting_records: dict[str, WaitingRecord] = {}
        self._health_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the orchestrator."""
        self._running = True
        self._health_task = asyncio.create_task(self._health_loop())
        logger.info("Orchestrator started")

    async def stop(self) -> None:
        """Stop the orchestrator and all agents."""
        self._running = False

        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task

        # Stop all agents
        for agent_id in list(self._instances.keys()):
            await self.stop_agent(agent_id)

        logger.info("Orchestrator stopped")

    # =========================================================================
    # AGENT SPAWNING
    # =========================================================================

    async def spawn_agent(
        self,
        agent_id: str,
        initial_prompt: str | None = None,
        task_id: str | None = None,
        model: str | None = None,
    ) -> AgentInstance:
        """
        Spawn a Claude Code instance for an agent.

        Args:
            agent_id: Agent identifier (e.g., "be-dev-1")
            initial_prompt: Optional initial prompt
            task_id: Optional task ID being worked on
            model: Override model selection

        Returns:
            AgentInstance handle
        """
        async with self._lock:
            # Check if already running
            if agent_id in self._instances:
                existing = self._instances[agent_id]
                if existing.state not in (AgentState.OFFLINE, AgentState.WAITING_LONG):
                    logger.warning(
                        "Agent already running",
                        agent_id=agent_id,
                        state=existing.state,
                    )
                    return existing

            # Get blueprint path
            blueprint_path = self._get_blueprint_path(agent_id)
            if not blueprint_path.exists():
                raise FileNotFoundError(f"Blueprint not found: {blueprint_path}")

            # Generate MCP config
            mcp_config_path = await self._generate_mcp_config(agent_id)

            # Determine model
            if not model:
                role = self._get_agent_role(agent_id)
                model = ROLE_MODEL_MAP.get(role, "sonnet")

            # Create config
            config = AgentConfig(
                agent_id=agent_id,
                blueprint_path=blueprint_path,
                model=model,
                mcp_config_path=mcp_config_path,
            )

            # Create instance
            instance = AgentInstance(
                agent_id=agent_id,
                state=AgentState.STARTING,
                config=config,
                current_task_id=task_id,
            )

            self._instances[agent_id] = instance

        # Spawn the process
        try:
            process = await self._spawn_process(config, initial_prompt)
            instance.process = process
            instance.state = AgentState.ACTIVE
            instance.started_at = datetime.utcnow()
            instance.last_activity = datetime.utcnow()

            logger.info(
                "Agent spawned",
                agent_id=agent_id,
                model=model,
                task_id=task_id,
            )

            return instance

        except Exception as e:
            instance.state = AgentState.OFFLINE
            instance.error_count += 1
            logger.error(
                "Failed to spawn agent",
                agent_id=agent_id,
                error=str(e),
            )
            raise

    async def _spawn_process(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
    ) -> asyncio.subprocess.Process:
        """Spawn the actual Claude Code process."""
        cmd = [
            "claude",
            "--model",
            MODEL_MAP.get(config.model, config.model),
            "--system-prompt-file",
            str(config.blueprint_path),
            "--output-format",
            "stream-json",
        ]

        if config.mcp_config_path:
            cmd.extend(["--mcp-config", str(config.mcp_config_path)])

        if initial_prompt:
            cmd.extend(["-p", initial_prompt])

        env = os.environ.copy()
        env["ROBOCO_AGENT_ID"] = config.agent_id

        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=config.working_directory,
        )

        return process

    async def _generate_mcp_config(self, agent_id: str) -> Path:
        """Generate MCP config for an agent with embedded agent_id."""
        config = {
            "mcpServers": {
                "roboco-task": {
                    "command": "python",
                    "args": [
                        "-m",
                        "roboco.mcp.task_server",
                        agent_id,
                    ],
                },
                "roboco-message": {
                    "command": "python",
                    "args": [
                        "-m",
                        "roboco.mcp.message_server",
                        agent_id,
                    ],
                },
                "roboco-notify": {
                    "command": "python",
                    "args": [
                        "-m",
                        "roboco.mcp.notify_server",
                        agent_id,
                    ],
                },
                "roboco-journal": {
                    "command": "python",
                    "args": [
                        "-m",
                        "roboco.mcp.journal_server",
                        agent_id,
                    ],
                },
            }
        }

        # Write to temp file
        config_path = Path(tempfile.gettempdir()) / f"roboco-mcp-{agent_id}.json"
        config_path.write_text(json.dumps(config, indent=2))

        return config_path

    def _get_blueprint_path(self, agent_id: str) -> Path:
        """Get blueprint path for an agent."""
        # Map agent_id to blueprint
        role = self._get_agent_role(agent_id)
        team = self._get_agent_team(agent_id)

        if team == "backend":
            cell_dir = "backend"
        elif team == "frontend":
            cell_dir = "frontend"
        elif team == "uxui":
            cell_dir = "ux_ui"
        else:
            cell_dir = "board"

        blueprint_file = f"{role.replace('_', '-')}.md"

        return self.blueprints_dir / cell_dir / blueprint_file

    def _get_agent_role(self, agent_id: str) -> str:
        """Get role from agent_id."""
        role_map = {
            "be-dev-1": "be-dev",
            "be-dev-2": "be-dev",
            "fe-dev-1": "fe-dev",
            "fe-dev-2": "fe-dev",
            "ux-dev": "ux-dev",
            "be-qa": "be-qa",
            "fe-qa": "fe-qa",
            "ux-qa": "ux-qa",
            "be-pm": "be-pm",
            "fe-pm": "fe-pm",
            "ux-pm": "ux-pm",
            "be-doc": "be-documenter",
            "fe-doc": "fe-documenter",
            "ux-doc": "ux-documenter",
            "main-pm": "main-pm",
            "product-owner": "product-owner",
            "head-marketing": "head-marketing",
            "auditor": "auditor",
        }
        return role_map.get(agent_id, agent_id)

    def _get_agent_team(self, agent_id: str) -> str | None:
        """Get team from agent_id."""
        if agent_id.startswith("be-"):
            return "backend"
        if agent_id.startswith("fe-"):
            return "frontend"
        if agent_id.startswith("ux-"):
            return "uxui"
        return None

    # =========================================================================
    # AGENT STOPPING
    # =========================================================================

    async def stop_agent(self, agent_id: str, graceful: bool = True) -> None:
        """Stop an agent."""
        async with self._lock:
            if agent_id not in self._instances:
                return

            instance = self._instances[agent_id]

            if instance.process and instance.process.returncode is None:
                instance.state = AgentState.STOPPING

                if graceful:
                    # Send interrupt
                    instance.process.terminate()
                    try:
                        await asyncio.wait_for(
                            instance.process.wait(),
                            timeout=10.0,
                        )
                    except TimeoutError:
                        instance.process.kill()
                        await instance.process.wait()
                else:
                    instance.process.kill()
                    await instance.process.wait()

            instance.state = AgentState.OFFLINE
            instance.process = None

            logger.info("Agent stopped", agent_id=agent_id)

    # =========================================================================
    # WAITING STATE MANAGEMENT
    # =========================================================================

    async def mark_waiting_long(
        self,
        agent_id: str,
        waiting_for: str,
        task_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        """
        Mark an agent as WAITING_LONG and terminate.

        The agent will be respawned when the wait condition is resolved.
        """
        record = WaitingRecord(
            agent_id=agent_id,
            task_id=task_id,
            waiting_for=waiting_for,
            waiting_since=datetime.utcnow(),
            context=context or {},
        )

        self._waiting_records[agent_id] = record

        # Stop the agent
        await self.stop_agent(agent_id)

        # Update state
        if agent_id in self._instances:
            self._instances[agent_id].state = AgentState.WAITING_LONG
            self._instances[agent_id].waiting_for = waiting_for
            self._instances[agent_id].waiting_context = context or {}

        logger.info(
            "Agent marked as waiting_long",
            agent_id=agent_id,
            waiting_for=waiting_for,
            task_id=task_id,
        )

    async def resolve_wait(
        self,
        agent_id: str,
        resolution: dict[str, Any],
    ) -> AgentInstance | None:
        """
        Resolve a wait condition and respawn the agent.

        Args:
            agent_id: The waiting agent
            resolution: Details about the resolution

        Returns:
            Respawned AgentInstance or None
        """
        if agent_id not in self._waiting_records:
            return None

        record = self._waiting_records[agent_id]
        del self._waiting_records[agent_id]

        # Generate resume prompt
        resume_prompt = self._generate_resume_prompt(record, resolution)

        # Respawn
        return await self.spawn_agent(
            agent_id=agent_id,
            initial_prompt=resume_prompt,
            task_id=record.task_id,
        )

    def _generate_resume_prompt(
        self,
        record: WaitingRecord,
        resolution: dict[str, Any],
    ) -> str:
        """Generate a resume prompt for a respawning agent."""
        if record.waiting_for == "blocker_resolution":
            return f"""
You were working on TASK-{record.task_id} and got blocked.
The blocker has been resolved: {resolution.get("details", "Resolved")}

Resume by:
1. Reading your checkpoint from .tasks/active/TASK-{record.task_id}/
2. Call roboco_task_unblock("{record.task_id}")
3. Continue from where you left off
"""

        elif record.waiting_for == "qa_result":
            if resolution.get("passed"):
                return f"""
TASK-{record.task_id} has passed QA review.
The task is now awaiting documentation.
You may return to scanning for new work with roboco_task_scan().
"""
            else:
                return f"""
TASK-{record.task_id} needs revision based on QA feedback.
QA notes: {resolution.get("notes", "See task for details")}

Resume by:
1. Reading the QA feedback
2. Updating your TODOs to address each issue
3. Making the fixes
4. Re-submitting for QA
"""

        elif record.waiting_for == "answer":
            return f"""
You asked a question about TASK-{record.task_id}:
Your question: {record.context.get("question", "Unknown")}
Answer received: {resolution.get("answer", "Unknown")}

Resume by incorporating this information and continuing from where you stopped.
"""

        elif record.waiting_for == "assignment":
            return f"""
You have been assigned a new task: TASK-{resolution.get("task_id")}

Start by:
1. Call roboco_task_get("{resolution.get("task_id")}") to get details
2. Follow the standard workflow: UNDERSTAND → PLAN → EXECUTE → VERIFY → NOTES
"""

        else:
            return f"Resuming. Wait condition '{record.waiting_for}' resolved."

    # =========================================================================
    # HEALTH MONITORING
    # =========================================================================

    async def _health_loop(self) -> None:
        """Background health check loop."""
        while self._running:
            try:
                await asyncio.sleep(30)  # Check every 30 seconds
                await self._check_health()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Health check error", error=str(e))

    async def _check_health(self) -> None:
        """Check health of all running agents."""
        for agent_id, instance in list(self._instances.items()):
            if instance.state not in (AgentState.ACTIVE, AgentState.WAITING_SHORT):
                continue

            if instance.process is None:
                continue

            # Check if process died
            if instance.process.returncode is not None:
                logger.warning(
                    "Agent process died",
                    agent_id=agent_id,
                    returncode=instance.process.returncode,
                )
                instance.state = AgentState.OFFLINE
                instance.error_count += 1

                # Auto-restart if not too many errors
                if instance.error_count < 3:
                    logger.info("Auto-restarting agent", agent_id=agent_id)
                    await self.spawn_agent(
                        agent_id=agent_id,
                        task_id=instance.current_task_id,
                    )

    # =========================================================================
    # STATUS API
    # =========================================================================

    def get_state(self, agent_id: str) -> AgentState:
        """Get current state of an agent."""
        if agent_id not in self._instances:
            return AgentState.OFFLINE
        return self._instances[agent_id].state

    def get_instance(self, agent_id: str) -> AgentInstance | None:
        """Get instance for an agent."""
        return self._instances.get(agent_id)

    def get_all_instances(self) -> dict[str, AgentInstance]:
        """Get all agent instances."""
        return dict(self._instances)

    def get_waiting_agents(self) -> dict[str, WaitingRecord]:
        """Get all waiting agents."""
        return dict(self._waiting_records)

    def get_status_summary(self) -> dict[str, Any]:
        """Get summary of all agent states."""
        summary = {
            "total": len(self._instances),
            "by_state": {},
            "waiting_count": len(self._waiting_records),
            "agents": [],
        }

        for state in AgentState:
            count = sum(1 for i in self._instances.values() if i.state == state)
            if count > 0:
                summary["by_state"][state.value] = count

        for agent_id, instance in self._instances.items():
            summary["agents"].append(
                {
                    "agent_id": agent_id,
                    "state": instance.state.value,
                    "task_id": instance.current_task_id,
                    "error_count": instance.error_count,
                    "started_at": instance.started_at.isoformat()
                    if instance.started_at
                    else None,
                }
            )

        return summary

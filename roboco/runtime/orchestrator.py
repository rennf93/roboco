"""
Agent Orchestrator

Manages Claude Code containers for all RoboCo agents.
Handles spawning, monitoring, health checks, and graceful shutdown.

The orchestrator is the BRAIN of the system:
- Checks for work BEFORE spawning agents (no wasteful spawns)
- Claims tasks on behalf of agents before spawning
- Agents receive their assignment at spawn time
- Agents scan for more work after completing a task
- Agents only call roboco_agent_idle() when truly no work remains
"""

import asyncio
import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from fastapi import status as http_status

from roboco.config import settings
from roboco.models.runtime import (
    MODEL_MAP,
    ROLE_MODEL_MAP,
    AgentInstance,
    OrchestratorAgentConfig,
    OrchestratorAgentState,
    WaitingRecord,
)

logger = structlog.get_logger()

# Re-export for backwards compatibility
AgentState = OrchestratorAgentState
AgentConfig = OrchestratorAgentConfig

# Docker configuration
AGENT_IMAGE = "roboco-agent"
AGENT_NETWORK = "roboco_default"

# When running in a container, we need host paths for volume mounts.
# These can be overridden via environment variables.
CLAUDE_AUTH_HOST_PATH = os.environ.get(
    "ROBOCO_HOST_CLAUDE_DIR",
    str(Path.home() / ".claude"),
)
PROJECT_HOST_PATH = os.environ.get("ROBOCO_HOST_PROJECT_DIR", "")
DATA_HOST_PATH = os.environ.get("ROBOCO_HOST_DATA_DIR", "")


# =============================================================================
# ORCHESTRATOR
# =============================================================================


class AgentOrchestrator:
    """
    Manages Claude Code containers for all agents.

    Responsibilities:
    - Spawn agents as Docker containers
    - Monitor health via docker inspect
    - Handle waiting states and respawning
    - Provide status API
    - Cost-efficient on-demand spawning
    """

    def __init__(
        self,
        blueprints_dir: Path | None = None,
        mcp_config_dir: Path | None = None,
        project_root: Path | None = None,
        dispatcher_interval: int = 30,
    ):
        self.blueprints_dir = blueprints_dir or Path("agents/blueprints")
        self.mcp_config_dir = mcp_config_dir or Path(".mcp")
        self.project_root = project_root or Path.cwd()
        self.dispatcher_interval = dispatcher_interval

        self._instances: dict[str, AgentInstance] = {}
        self._waiting_records: dict[str, WaitingRecord] = {}
        self._health_task: asyncio.Task | None = None
        self._dispatcher_task: asyncio.Task | None = None
        self._running = False
        self._lock = asyncio.Lock()

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the orchestrator."""
        self._running = True

        # Ensure agent image is built
        await self._ensure_agent_image()

        # Start background tasks
        self._health_task = asyncio.create_task(self._health_loop())
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())

        logger.info(
            "Orchestrator started",
            dispatcher_interval=self.dispatcher_interval,
        )

    async def stop(self) -> None:
        """Stop the orchestrator and all agents."""
        self._running = False

        # Cancel background tasks
        if self._health_task:
            self._health_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._health_task

        if self._dispatcher_task:
            self._dispatcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher_task

        # Stop all agents
        for agent_id in list(self._instances.keys()):
            await self.stop_agent(agent_id)

        logger.info("Orchestrator stopped")

    async def _ensure_agent_image(self) -> None:
        """Ensure the agent Docker image is built."""
        # Check if image exists
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            AGENT_IMAGE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0:
            logger.info("Building agent Docker image...")

            # Determine paths - use host paths when running in container
            if PROJECT_HOST_PATH:
                # Running in container - use host paths for Docker build
                dockerfile_path = f"{PROJECT_HOST_PATH}/docker/agent.Dockerfile"
                build_context = PROJECT_HOST_PATH
            else:
                # Running on host
                dockerfile_path = str(self.project_root / "docker" / "agent.Dockerfile")
                build_context = str(self.project_root)

            proc = await asyncio.create_subprocess_exec(
                "docker",
                "build",
                "-t",
                AGENT_IMAGE,
                "-f",
                dockerfile_path,
                build_context,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to build agent image: {stderr.decode()}")
            logger.info("Agent Docker image built successfully")

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
        Spawn a Claude Code container for an agent.

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

        # Spawn the container
        try:
            container_id = await self._spawn_container(config, initial_prompt)
            instance.container_id = container_id
            instance.state = AgentState.ACTIVE
            instance.started_at = datetime.now(UTC)
            instance.last_activity = datetime.now(UTC)

            logger.info(
                "Agent spawned",
                agent_id=agent_id,
                container_id=container_id[:12],
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

    async def _spawn_container(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
    ) -> str:
        """Spawn a Docker container for the agent."""
        container_name = f"roboco-agent-{config.agent_id}"

        # Remove existing container if any
        await self._remove_container(container_name)

        # Determine host paths for volume mounts
        # When running in a container, use PROJECT_HOST_PATH; otherwise use local paths
        if not config.mcp_config_path:
            raise RuntimeError("MCP config path not set")

        if PROJECT_HOST_PATH:
            # Running inside orchestrator container - use host paths
            blueprints_host = f"{PROJECT_HOST_PATH}/agents/blueprints"
            claude_host = CLAUDE_AUTH_HOST_PATH
            mcp_config_host = (
                f"{DATA_HOST_PATH}/mcp-configs/{config.mcp_config_path.name}"
            )
        else:
            # Running directly on host
            blueprints_host = str(self.blueprints_dir.absolute())
            claude_host = CLAUDE_AUTH_HOST_PATH
            mcp_config_host = str(config.mcp_config_path)

        # Build docker run command
        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            AGENT_NETWORK,
            # Mount Claude auth (needs write access for debug logs)
            "-v",
            f"{claude_host}:/home/agent/.claude",
            # Mount blueprints
            "-v",
            f"{blueprints_host}:/app/agents/blueprints:ro",
            # Mount MCP config
            "-v",
            f"{mcp_config_host}:/app/mcp-config.json:ro",
            # Environment
            "-e",
            f"ROBOCO_AGENT_ID={config.agent_id}",
            "-e",
            "ROBOCO_API_URL=http://roboco-orchestrator:8000",
            # The image
            AGENT_IMAGE,
            # Claude Code arguments
            "--model",
            MODEL_MAP.get(config.model, config.model),
            "--system-prompt-file",
            f"/app/agents/blueprints/{self._get_blueprint_rel_path(config.agent_id)}",
            "--mcp-config",
            "/app/mcp-config.json",
            "--output-format",
            "stream-json",
            "--verbose",
            # Always provide a prompt (required for non-interactive mode)
            # NOTE: With the smart dispatcher, agents should ALWAYS receive
            # a task assignment at spawn time. This default indicates a bug.
            "-p",
            initial_prompt
            or (
                "ERROR: You were spawned without a task assignment. "
                "This is a bug in the orchestrator. "
                "Call roboco_agent_idle() immediately to shutdown."
            ),
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Failed to start container: {stderr.decode()}")

        container_id = stdout.decode().strip()
        return container_id

    async def _remove_container(self, container_name: str) -> None:
        """Remove a container if it exists."""
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "rm",
            "-f",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

    async def _generate_mcp_config(self, agent_id: str) -> Path:
        """Generate MCP config for an agent."""
        # MCP servers run inside the container, connect to API via network
        # Explicitly use Environment Variables to avoid issues with containerized agents
        mcp_env = {
            "ROBOCO_API_URL": settings.internal_api_url,
            "ROBOCO_AGENT_ID": agent_id,
        }

        config = {
            "mcpServers": {
                "roboco-task": {
                    "command": "uv",
                    "args": ["run", "python", "-m", "roboco.mcp.task_server", agent_id],
                    "env": mcp_env,
                },
                "roboco-message": {
                    "command": "uv",
                    "args": [
                        "run",
                        "python",
                        "-m",
                        "roboco.mcp.message_server",
                        agent_id,
                    ],
                    "env": mcp_env,
                },
                "roboco-notify": {
                    "command": "uv",
                    "args": [
                        "run",
                        "python",
                        "-m",
                        "roboco.mcp.notify_server",
                        agent_id,
                    ],
                    "env": mcp_env,
                },
                "roboco-journal": {
                    "command": "uv",
                    "args": [
                        "run",
                        "python",
                        "-m",
                        "roboco.mcp.journal_server",
                        agent_id,
                    ],
                    "env": mcp_env,
                },
            }
        }

        # Write to shared config directory (mounted in both orchestrator and agents)
        # When running in container: /app/mcp-configs -> host's ./data/mcp-configs
        # When running on host: use temp directory
        if DATA_HOST_PATH:
            # Running in container - use shared mounted directory
            config_dir = Path("/app/mcp-configs")
            config_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Running on host - use temp directory
            config_dir = Path(tempfile.gettempdir())

        config_path = config_dir / f"roboco-mcp-{agent_id}.json"
        config_path.write_text(json.dumps(config, indent=2))

        return config_path

    def _get_blueprint_path(self, agent_id: str) -> Path:
        """Get blueprint path for an agent."""
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

    def _get_blueprint_rel_path(self, agent_id: str) -> str:
        """Get relative blueprint path for container mount."""
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
        return f"{cell_dir}/{blueprint_file}"

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
        """Stop an agent container."""
        async with self._lock:
            if agent_id not in self._instances:
                return

            instance = self._instances[agent_id]

            if instance.container_id:
                instance.state = AgentState.STOPPING
                container_name = f"roboco-agent-{agent_id}"

                if graceful:
                    # Graceful stop with timeout
                    proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "stop",
                        "-t",
                        "10",
                        container_name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()
                else:
                    # Force kill
                    proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "kill",
                        container_name,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await proc.wait()

                # Remove container
                await self._remove_container(container_name)

            instance.state = AgentState.OFFLINE
            instance.container_id = None

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
            waiting_since=datetime.now(UTC),
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

            if instance.container_id is None:
                continue

            # Check if container is still running
            container_name = f"roboco-agent-{agent_id}"
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "inspect",
                "-f",
                "{{.State.Running}}",
                container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()

            is_running = stdout.decode().strip() == "true"

            if not is_running:
                cid = instance.container_id[:12] if instance.container_id else None
                logger.warning(
                    "Agent container stopped",
                    agent_id=agent_id,
                    container_id=cid,
                )
                instance.state = AgentState.OFFLINE
                instance.error_count += 1
                instance.container_id = None

                # Auto-restart if not too many errors
                max_retries = 3
                if instance.error_count < max_retries:
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
        by_state: dict[str, int] = {}
        agents: list[dict[str, Any]] = []

        for state in AgentState:
            count = sum(1 for i in self._instances.values() if i.state == state)
            if count > 0:
                by_state[state.value] = count

        for agent_id, instance in self._instances.items():
            cid = instance.container_id[:12] if instance.container_id else None
            agents.append(
                {
                    "agent_id": agent_id,
                    "state": instance.state.value,
                    "container_id": cid,
                    "task_id": instance.current_task_id,
                    "error_count": instance.error_count,
                    "started_at": instance.started_at.isoformat()
                    if instance.started_at
                    else None,
                }
            )

        return {
            "total": len(self._instances),
            "by_state": by_state,
            "waiting_count": len(self._waiting_records),
            "agents": agents,
        }

    # =========================================================================
    # SMART DISPATCHER - API HELPERS
    # =========================================================================

    @property
    def _api_url(self) -> str:
        """Get the internal API URL for task/notification queries."""
        return settings.internal_api_url

    def _is_agent_active(self, agent_id: str) -> bool:
        """Check if an agent is currently running."""
        if agent_id not in self._instances:
            return False
        return self._instances[agent_id].state == AgentState.ACTIVE

    def _select_agent_for_cell(self, cell: str, role: str) -> str | None:
        """
        Select the best available agent for a cell and role.

        Prefers agents that are not currently active.
        For developers, uses round-robin among candidates.
        """
        prefix_map = {"backend": "be", "frontend": "fe", "uxui": "ux"}
        prefix = prefix_map.get(cell)
        if not prefix:
            return None

        # Build candidate list based on role
        if role == "dev":
            if prefix == "ux":
                candidates = ["ux-dev"]
            else:
                candidates = [f"{prefix}-dev-1", f"{prefix}-dev-2"]
        elif role == "qa":
            candidates = [f"{prefix}-qa"]
        elif role == "doc":
            candidates = [f"{prefix}-doc"]
        elif role == "pm":
            candidates = [f"{prefix}-pm"]
        else:
            return None

        # Prefer non-active agents
        for agent_id in candidates:
            if not self._is_agent_active(agent_id):
                return agent_id

        # All active - return first (task will queue for them via scan)
        return candidates[0]

    async def _claim_task_for_agent(
        self,
        client: httpx.AsyncClient,
        task_id: str,
        agent_id: str,
    ) -> bool:
        """Claim a task on behalf of an agent before spawning."""
        try:
            resp = await client.post(
                f"{self._api_url}/tasks/{task_id}/claim",
                json={"agent_id": agent_id},
            )
            if resp.status_code == http_status.HTTP_200_OK:
                logger.info(
                    "Task claimed for agent",
                    task_id=task_id,
                    agent_id=agent_id,
                )
                return True
            logger.warning(
                "Failed to claim task",
                task_id=task_id,
                agent_id=agent_id,
                status=resp.status_code,
            )
        except Exception as e:
            logger.error("Claim task error", task_id=task_id, error=str(e))
        return False

    async def _fetch_tasks(
        self,
        client: httpx.AsyncClient,
        status: str | list[str],
        team: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch tasks by status and optional team filter."""
        params: dict[str, Any] = {}
        if isinstance(status, list):
            params["status"] = ",".join(status)
        else:
            params["status"] = status
        if team:
            params["team"] = team

        try:
            resp = await client.get(f"{self._api_url}/tasks", params=params)
            if resp.status_code == http_status.HTTP_200_OK:
                result: list[dict[str, Any]] = resp.json()
                return result
        except Exception as e:
            logger.error("Fetch tasks error", status=status, error=str(e))
        return []

    async def _fetch_notifications(
        self,
        client: httpx.AsyncClient,
        notification_type: str,
        unacknowledged: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch notifications by type."""
        params: dict[str, Any] = {
            "type": notification_type,
            "pending_ack_only": str(unacknowledged).lower(),
        }
        try:
            resp = await client.get(
                f"{self._api_url}/notifications",
                params=params,
            )
            if resp.status_code == http_status.HTTP_200_OK:
                data = resp.json()
                items: list[dict[str, Any]] = data.get("items", [])
                return items
        except Exception as e:
            logger.error(
                "Fetch notifications error",
                notification_type=notification_type,
                error=str(e),
            )
        return []

    # =========================================================================
    # SMART DISPATCHER - MAIN LOOP
    # =========================================================================

    async def _dispatcher_loop(self) -> None:
        """
        Main dispatcher loop - periodically checks for work and spawns agents.

        This is the BRAIN of the orchestrator. It:
        1. Queries for tasks needing work (pending, awaiting_qa, etc.)
        2. Queries for events needing attention (blockers, escalations)
        3. Spawns appropriate agents with task assignments
        """
        while self._running:
            try:
                await asyncio.sleep(self.dispatcher_interval)
                await self._dispatch_all_work()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Dispatcher loop error", error=str(e))

    async def _dispatch_all_work(self) -> None:
        """Run all dispatchers to check for and assign work."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Task-based dispatchers (check task statuses)
            await self._dispatch_dev_work(client)
            await self._dispatch_qa_work(client)
            await self._dispatch_doc_work(client)
            await self._dispatch_marketing_work(client)

            # Event-based dispatchers (check blockers, notifications)
            await self._dispatch_blocker_work(client)
            await self._dispatch_escalation_work(client)
            await self._dispatch_approval_work(client)

            # Scheduled dispatchers
            await self._dispatch_audit_work(client)

    # =========================================================================
    # SMART DISPATCHER - TASK-BASED DISPATCHERS
    # =========================================================================

    async def _dispatch_dev_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch development work to developers.

        Monitors: pending, needs_revision tasks
        Spawns: be-dev-1, be-dev-2, fe-dev-1, fe-dev-2, ux-dev
        """
        # Get tasks needing dev attention
        tasks = await self._fetch_tasks(client, ["pending", "needs_revision"])

        for task in tasks:
            # Skip already claimed/assigned tasks
            if task.get("assigned_to"):
                continue

            team = task.get("team")
            if team not in ["backend", "frontend", "uxui"]:
                continue

            # Select best agent for this task
            agent_id = self._select_agent_for_cell(team, "dev")
            if not agent_id:
                continue

            # If agent is already active, just claim for them
            # They'll pick it up on their next roboco_task_scan()
            if self._is_agent_active(agent_id):
                await self._claim_task_for_agent(client, task["id"], agent_id)
            # Claim and spawn with assignment
            elif await self._claim_task_for_agent(client, task["id"], agent_id):
                await self.spawn_agent(
                    agent_id=agent_id,
                    task_id=task["id"],
                    initial_prompt=self._build_dev_prompt(task),
                )

    async def _dispatch_qa_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch QA work to QA agents.

        Monitors: awaiting_qa tasks
        Spawns: be-qa, fe-qa, ux-qa
        """
        tasks = await self._fetch_tasks(client, "awaiting_qa")

        for task in tasks:
            team = task.get("team")
            if team not in ["backend", "frontend", "uxui"]:
                continue

            agent_id = self._select_agent_for_cell(team, "qa")
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                # QA already running, they'll pick up on scan
                continue

            # Spawn QA agent with task assignment
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_qa_prompt(task),
            )
            # Only spawn one QA at a time per cell
            break

    async def _dispatch_doc_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch documentation work to documenters.

        Monitors: awaiting_documentation tasks
        Spawns: be-doc, fe-doc, ux-doc
        """
        tasks = await self._fetch_tasks(client, "awaiting_documentation")

        for task in tasks:
            team = task.get("team")
            if team not in ["backend", "frontend", "uxui"]:
                continue

            agent_id = self._select_agent_for_cell(team, "doc")
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                continue

            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_doc_prompt(task),
            )
            break

    async def _dispatch_marketing_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch marketing work to head-marketing.

        Monitors: pending tasks with team=marketing
        Spawns: head-marketing
        """
        tasks = await self._fetch_tasks(client, "pending", team="marketing")

        for task in tasks:
            # Skip already claimed/assigned tasks
            if task.get("assigned_to"):
                continue

            if self._is_agent_active("head-marketing"):
                # Already running, they'll pick up on scan
                continue

            await self.spawn_agent(
                agent_id="head-marketing",
                task_id=task["id"],
                initial_prompt=self._build_marketing_prompt(task),
            )
            break

    # =========================================================================
    # SMART DISPATCHER - EVENT-BASED DISPATCHERS
    # =========================================================================

    async def _dispatch_blocker_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch blocker resolution to Cell PMs.

        Monitors: blocked tasks
        Spawns: be-pm, fe-pm, ux-pm
        """
        tasks = await self._fetch_tasks(client, "blocked")

        for task in tasks:
            team = task.get("team")
            if team not in ["backend", "frontend", "uxui"]:
                continue

            agent_id = self._select_agent_for_cell(team, "pm")
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                continue

            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_pm_blocker_prompt(task),
            )
            break

    async def _dispatch_escalation_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch escalations to appropriate managers.

        Monitors: escalation notifications (unacknowledged)
        Spawns: be-pm, fe-pm, ux-pm, main-pm, product-owner, head-marketing
        """
        notifications = await self._fetch_notifications(client, "escalation")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                valid_targets = [
                    "be-pm",
                    "fe-pm",
                    "ux-pm",
                    "main-pm",
                    "product-owner",
                    "head-marketing",
                ]
                if agent_id not in valid_targets:
                    continue

                if self._is_agent_active(agent_id):
                    continue

                await self.spawn_agent(
                    agent_id=agent_id,
                    initial_prompt=self._build_escalation_prompt(notif),
                )
                break

    async def _dispatch_approval_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch approval requests to approvers.

        Monitors: approval notifications (unacknowledged)
        Spawns: product-owner, head-marketing, main-pm
        """
        notifications = await self._fetch_notifications(client, "approval")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                if agent_id not in ["product-owner", "head-marketing", "main-pm"]:
                    continue

                if self._is_agent_active(agent_id):
                    continue

                await self.spawn_agent(
                    agent_id=agent_id,
                    initial_prompt=self._build_approval_prompt(notif),
                )
                break

    async def _dispatch_audit_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch audit work to the auditor.

        Monitors: quality alert notifications
        Spawns: auditor

        Note: Periodic scheduled audits can be added here in the future.
        """
        alerts = await self._fetch_notifications(client, "alert")

        for alert in alerts:
            targets = alert.get("to_agents", [])
            if "auditor" in targets and not self._is_agent_active("auditor"):
                await self.spawn_agent(
                    agent_id="auditor",
                    initial_prompt=self._build_audit_prompt(alert),
                )
                return

        # TODO: Add scheduled periodic audits
        # Check last audit time, spawn if overdue

    # =========================================================================
    # SMART DISPATCHER - PROMPT BUILDERS
    # =========================================================================

    def _build_dev_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a developer with an assigned task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        status = task.get("status", "unknown")
        team = task.get("team", "unknown")

        return f"""You have been assigned a development task.

TASK ID: {task_id}
TITLE: {title}
STATUS: {status}
TEAM: {team}

This task is already CLAIMED for you. Begin work immediately:

1. Call roboco_task_get("{task_id}") for full details and acceptance criteria
2. Follow the workflow: UNDERSTAND → PLAN → EXECUTE → VERIFY → SUBMIT QA
3. When task is submitted for QA, call roboco_task_scan() to check for more work
4. If more work is assigned to you, continue working
5. If no more work, call roboco_agent_idle() to shutdown gracefully

Do NOT scan for work first - your task is already assigned. Begin now.
"""

    def _build_qa_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a QA agent."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        assigned_to = task.get("assigned_to", "unknown")
        team = task.get("team", "unknown")

        return f"""A task is ready for QA review.

TASK ID: {task_id}
TITLE: {title}
DEVELOPER: {assigned_to}
TEAM: {team}

Begin QA review:

1. Call roboco_task_get("{task_id}") for full details and acceptance criteria
2. Review the implementation against ALL acceptance criteria
3. Test the changes thoroughly
4. Call roboco_task_qa_pass() with notes if approved
   OR roboco_task_qa_fail() with specific issues if rejected
5. Call roboco_task_scan() to check for more QA work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_doc_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a documenter."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        return f"""A task is ready for documentation.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}

Begin documentation:

1. Call roboco_task_get("{task_id}") for full details and dev handoff notes
2. Create or update documentation based on what was implemented
3. Ensure code comments, README updates, API docs as needed
4. Call roboco_task_complete("{task_id}") when documentation is done
5. Call roboco_task_scan() to check for more documentation work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_marketing_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for head-marketing with a marketing task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        description = task.get("description", "No description")

        return f"""You have been assigned a marketing task.

TASK ID: {task_id}
TITLE: {title}
DESCRIPTION: {description}

Begin work:

1. Call roboco_task_get("{task_id}") for full details and acceptance criteria
2. Execute the marketing task (content, campaigns, research, etc.)
3. Coordinate with Product Owner or Main PM if needed
4. Call roboco_task_complete("{task_id}") when done
5. Call roboco_task_scan() to check for more marketing work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_pm_blocker_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a Cell PM handling a blocker."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        assigned_to = task.get("assigned_to", "unknown")
        blocker = task.get("blocker", {})
        reason = blocker.get("reason", "Unknown")
        what_needed = blocker.get("what_needed", "Unknown")

        return f"""A task in your cell is BLOCKED and needs your attention.

TASK ID: {task_id}
TITLE: {title}
ASSIGNED TO: {assigned_to}
BLOCKER REASON: {reason}
WHAT'S NEEDED: {what_needed}

Your job:

1. Understand the blocker by reviewing task details
2. Communicate with the blocked developer if needed
3. Resolve the blocker (coordinate resources, make decisions, escalate if needed)
4. Once resolved, the developer can call roboco_task_unblock()
5. Call roboco_task_scan() to check for other blocked tasks in your cell
6. If no more blockers, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_escalation_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an escalation."""
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        subject = notification.get("subject", "No subject")
        priority = notification.get("priority", "normal")
        body = notification.get("body", "No details provided")

        return f"""You have received an ESCALATION that requires your attention.

FROM: {from_agent}
SUBJECT: {subject}
PRIORITY: {priority}

DETAILS:
{body}

Your job:

1. Acknowledge the notification with roboco_notify_ack("{notif_id}")
2. Assess the escalation and determine action needed
3. Communicate decisions via appropriate channels
4. If this requires further escalation, use roboco_escalate()
5. When resolved, call roboco_task_scan() for other work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_approval_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an approval request."""
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        subject = notification.get("subject", "No subject")
        related_task_id = notification.get("related_task_id", "None")
        body = notification.get("body", "No details provided")

        return f"""You have received an APPROVAL REQUEST.

FROM: {from_agent}
SUBJECT: {subject}
RELATED TASK: {related_task_id}

REQUEST:
{body}

Your job:

1. Review the approval request carefully
2. If related to a task, call roboco_task_get() for context
3. Make your decision and communicate it
4. Acknowledge with roboco_notify_ack("{notif_id}")
5. Call roboco_task_scan() for other work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_audit_prompt(self, alert: dict[str, Any] | None = None) -> str:
        """Build initial prompt for the auditor."""
        if alert:
            subject = alert.get("subject", "Quality issue detected")
            body = alert.get("body", "Review system quality metrics")

            return f"""QUALITY ALERT triggered your attention.

ALERT: {subject}
DETAILS: {body}

Your job:

1. Investigate the quality issue
2. Review relevant channels and task history (you have read access to all)
3. Compile your findings
4. Report to CEO via appropriate channel
5. Call roboco_agent_idle() when complete
"""

        return """Periodic AUDIT requested.

Your job:

1. Review recent activity across all cells
2. Check quality metrics (QA pass/fail rates, blocker frequency, etc.)
3. Identify any concerns or patterns
4. Compile audit report for CEO
5. Call roboco_agent_idle() when complete
"""

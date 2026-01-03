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
from typing import Any, ClassVar

import httpx
import structlog
from fastapi import status as http_status

from roboco.agents.factories._base import compose_prompt
from roboco.agents_config import ALL_DOCS, get_agent_role, get_agent_team
from roboco.config import settings
from roboco.models import AgentRole, Team
from roboco.models.runtime import (
    MODEL_MAP,
    ROLE_MODEL_MAP,
    AgentInstance,
    OrchestratorAgentConfig,
    OrchestratorAgentState,
    SpawnGitContext,
    WaitingRecord,
)
from roboco.seeds.initial_data import AGENT_UUIDS

logger = structlog.get_logger()

# Reverse mapping: UUID -> slug
UUID_TO_SLUG = {uuid: slug for slug, uuid in AGENT_UUIDS.items()}

# Re-export for backwards compatibility
AgentState = OrchestratorAgentState
AgentConfig = OrchestratorAgentConfig

# Docker configuration
AGENT_NETWORK = "roboco_default"
AGENT_BASE_IMAGE = "roboco-agent-base"

# Role -> Image mapping
# Specialized images extend the base with role-specific tools
AGENT_IMAGES: dict[str, str] = {
    # Backend
    "be-dev-1": "roboco-agent-dev-be",
    "be-dev-2": "roboco-agent-dev-be",
    "be-qa": "roboco-agent-qa-be",
    "be-pm": "roboco-agent-pm",
    "be-doc": "roboco-agent-doc",
    # Frontend
    "fe-dev-1": "roboco-agent-dev-fe",
    "fe-dev-2": "roboco-agent-dev-fe",
    "fe-qa": "roboco-agent-qa-fe",
    "fe-pm": "roboco-agent-pm",
    "fe-doc": "roboco-agent-doc",
    # UX/UI
    "ux-dev-1": "roboco-agent-ux",
    "ux-dev-2": "roboco-agent-ux",
    "ux-qa": "roboco-agent-ux",  # Uses same as dev for now
    "ux-pm": "roboco-agent-pm",
    "ux-doc": "roboco-agent-doc",
    # Board
    "main-pm": "roboco-agent-pm",
    "product-owner": "roboco-agent-pm",
    "head-marketing": "roboco-agent-pm",
    "auditor": "roboco-agent-pm",
}


def get_agent_image(agent_id: str) -> str:
    """Get the Docker image for an agent."""
    return AGENT_IMAGES.get(agent_id, AGENT_BASE_IMAGE)


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

        # Ensure agent Claude settings have MCP tools allowed
        self._ensure_agent_claude_settings()

        # Start background tasks
        self._health_task = asyncio.create_task(self._health_loop())
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())

        logger.info(
            "Orchestrator started",
            dispatcher_interval=self.dispatcher_interval,
            internal_api_url=self._api_url,
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

    def get_running_agents(self) -> set[str]:
        """Get set of currently running agent IDs."""
        return set(self._instances.keys())

    async def _ensure_agent_image(self, agent_id: str | None = None) -> None:
        """Ensure the agent Docker images are built.

        Builds base image first, then specialized image if agent_id provided.
        """
        # Determine build context
        if PROJECT_HOST_PATH:
            build_context = PROJECT_HOST_PATH
            docker_dir = f"{PROJECT_HOST_PATH}/docker"
        else:
            build_context = str(self.project_root)
            docker_dir = str(self.project_root / "docker")

        # Always ensure base image exists
        await self._build_image_if_missing(
            AGENT_BASE_IMAGE,
            f"{docker_dir}/agent-base.Dockerfile",
            build_context,
        )

        # Build specialized image if agent specified
        if agent_id:
            image = get_agent_image(agent_id)
            if image != AGENT_BASE_IMAGE:
                # Map image name to dockerfile
                dockerfile_map = {
                    "roboco-agent-pm": "agent-pm.Dockerfile",
                    "roboco-agent-dev-be": "agent-dev-be.Dockerfile",
                    "roboco-agent-dev-fe": "agent-dev-fe.Dockerfile",
                    "roboco-agent-qa-be": "agent-qa-be.Dockerfile",
                    "roboco-agent-qa-fe": "agent-qa-fe.Dockerfile",
                    "roboco-agent-doc": "agent-doc.Dockerfile",
                    "roboco-agent-ux": "agent-ux.Dockerfile",
                }
                dockerfile = dockerfile_map.get(image)
                if dockerfile:
                    await self._build_image_if_missing(
                        image,
                        f"{docker_dir}/{dockerfile}",
                        build_context,
                    )

    async def _build_image_if_missing(
        self, image_name: str, dockerfile_path: str, build_context: str
    ) -> None:
        """Build a Docker image if it doesn't exist."""
        # Check if image exists
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            image_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode != 0:
            logger.info("Building Docker image...", image=image_name)
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "build",
                "-t",
                image_name,
                "-f",
                dockerfile_path,
                build_context,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(
                    f"Failed to build image {image_name}: {stderr.decode()}"
                )
            logger.info("Docker image built successfully", image=image_name)

    def _ensure_agent_claude_settings(self) -> None:
        """
        Ensure agent Claude settings have RoboCo MCP tools pre-allowed.

        This prevents agents from needing interactive permission approval
        for essential MCP tools like roboco_agent_idle.
        """
        # RoboCo MCP tools that should always be allowed for agents
        roboco_allowed_tools = [
            # Task management - always needed
            "mcp__roboco-task__*",
            # Messaging - always needed for communication
            "mcp__roboco-message__*",
            # Notifications - always needed
            "mcp__roboco-notify__*",
            # Journal - always needed for reflection
            "mcp__roboco-journal__*",
            # Knowledge base/RAG - needed for research
            "mcp__roboco-optimal__*",
            # Git - branch management, commits, PRs
            # Role-based permissions enforced at handler level
            "mcp__roboco-git__*",
            # Agent-to-Agent protocol - cross-cell coordination
            "mcp__roboco-a2a__*",
            # Test tools - run tests, lint, format
            "mcp__roboco-test__*",
            # File operations for documenters and developers
            # Note: // prefix = absolute path (container paths like /app/docs)
            "Write(//app/docs/**)",
            "Write(//app/CHANGELOG.md)",
            "Write(//app/README.md)",
            "Edit(//app/docs/**)",
            "Edit(//app/CHANGELOG.md)",
            "Edit(//app/README.md)",
        ]

        # Path to agent Claude settings (shared across all agents)
        # Always use CLAUDE_AUTH_HOST_PATH - agents mount from this location
        claude_dir = Path(CLAUDE_AUTH_HOST_PATH)

        settings_path = claude_dir / "settings.json"

        # Load existing settings or create new
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
            except json.JSONDecodeError:
                settings = {}
        else:
            settings = {}

        # Ensure permissions structure exists
        if "permissions" not in settings:
            settings["permissions"] = {}
        if "allow" not in settings["permissions"]:
            settings["permissions"]["allow"] = []

        # Add RoboCo tools if not already present
        existing_allow = set(settings["permissions"]["allow"])
        tools_added = []
        for tool in roboco_allowed_tools:
            if tool not in existing_allow:
                settings["permissions"]["allow"].append(tool)
                tools_added.append(tool)

        # Only write if we added tools
        if tools_added:
            claude_dir.mkdir(parents=True, exist_ok=True)
            settings_path.write_text(json.dumps(settings, indent=2))
            logger.info(
                "Updated agent Claude settings with allowed MCP tools",
                tools_added=tools_added,
            )

    # =========================================================================
    # AGENT SPAWNING
    # =========================================================================

    async def spawn_agent(
        self,
        agent_id: str,
        initial_prompt: str | None = None,
        task_id: str | None = None,
        model: str | None = None,
        git_context: SpawnGitContext | None = None,
    ) -> AgentInstance:
        """
        Spawn a Claude Code container for an agent.

        Args:
            agent_id: Agent identifier (e.g., "be-dev-1")
            initial_prompt: Optional initial prompt
            task_id: Optional task ID being worked on
            model: Override model selection
            git_context: Optional git context (project_slug, branch_name)

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

            # Generate composed prompt (replaces static blueprints)
            blueprint_path = self._generate_composed_prompt(agent_id)

            # Ensure agent Claude settings have MCP tools allowed
            self._ensure_agent_claude_settings()

            # Ensure agent-specific Docker image is built
            await self._ensure_agent_image(agent_id)

            # Generate MCP config with git context if available
            mcp_config_path = await self._generate_mcp_config(agent_id, git_context)

            # Determine model using canonical role name from agents_config
            if not model:
                canonical_role = get_agent_role(agent_id)
                model = ROLE_MODEL_MAP.get(canonical_role, "sonnet")

            # Create config
            config = AgentConfig(
                agent_id=agent_id,
                blueprint_path=blueprint_path,
                model=model,
                mcp_config_path=mcp_config_path,
                git_context=git_context,
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
            docs_host = f"{PROJECT_HOST_PATH}/docs"
            claude_host = CLAUDE_AUTH_HOST_PATH
            mcp_config_host = (
                f"{DATA_HOST_PATH}/mcp-configs/{config.mcp_config_path.name}"
            )
            # Generated prompts are in /app/prompts-generated inside orchestrator
            # but need host path for agent container mount
            prompt_host = (
                f"{DATA_HOST_PATH}/prompts-generated/{config.agent_id}-prompt.md"
            )
        else:
            # Running directly on host
            blueprints_host = str(self.blueprints_dir.absolute())
            docs_host = str(self.blueprints_dir.parent / "docs")
            claude_host = CLAUDE_AUTH_HOST_PATH
            mcp_config_host = str(config.mcp_config_path)
            # Generated prompts in temp dir
            prompt_host = str(
                Path(tempfile.gettempdir())
                / "roboco-prompts"
                / f"{config.agent_id}-prompt.md"
            )

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
            # Mount generated system prompt (composed from layers at runtime)
            "-v",
            f"{prompt_host}:/app/system-prompt.md:ro",
            # Mount blueprints (legacy, kept for reference)
            "-v",
            f"{blueprints_host}:/app/agents/blueprints:ro",
            # Mount docs directory
            # - Documenters get write access to create/update docs
            # - All other roles get read-only access
            "-v",
            f"{docs_host}:/app/docs{'' if config.agent_id in ALL_DOCS else ':ro'}",
            # Mount MCP config
            "-v",
            f"{mcp_config_host}:/app/mcp-config.json:ro",
            # Environment
            "-e",
            f"ROBOCO_AGENT_ID={config.agent_id}",
            "-e",
            "ROBOCO_API_URL=http://roboco-orchestrator:8000",
        ]

        # Add git context environment variables if available
        if config.git_context:
            if config.git_context.project_slug:
                cmd.extend(
                    ["-e", f"ROBOCO_PROJECT_SLUG={config.git_context.project_slug}"]
                )
            if config.git_context.branch_name:
                cmd.extend(["-e", f"ROBOCO_BRANCH={config.git_context.branch_name}"])

        # Continue building command
        cmd.extend(
            [
                # The image (role-specific)
                get_agent_image(config.agent_id),
                # Claude Code arguments
                "--model",
                MODEL_MAP.get(config.model, config.model),
                "--system-prompt-file",
                "/app/system-prompt.md",
                "--mcp-config",
                "/app/mcp-config.json",
                "--output-format",
                "stream-json",
                "--verbose",
                # Always provide a prompt (required for non-interactive mode)
                # If no task assignment provided, agent should follow standard workflow:
                # SCAN for work -> CLAIM if available -> or IDLE if no work
                "-p",
                initial_prompt
                or (
                    "You may have been spawned without a specific task assignment. "
                    "Follow your standard workflow:\n\n"
                    "1. Call `roboco_task_scan()` to find work for your role\n"
                    "2. If tasks found, claim with `roboco_task_claim(task_id)` "
                    "and begin: UNDERSTAND -> PLAN -> EXECUTE -> VERIFY -> HANDOFF\n"
                    "3. If no tasks available, call `roboco_agent_idle()` "
                    "to shutdown gracefully\n\n"
                    "Start now by scanning for work."
                ),
            ]
        )

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

    async def _generate_mcp_config(
        self,
        agent_id: str,
        git_context: SpawnGitContext | None = None,
    ) -> Path:
        """Generate MCP config for an agent.

        All agents get access to these MCP servers:
        - roboco-task: Task management
        - roboco-message: Channel messaging
        - roboco-journal: Personal journaling
        - roboco-notify: Notifications (read for all, send for PMs)
        - roboco-optimal: Knowledge base, RAG, semantic search
        - roboco-git: Git operations (role-based at handler level)
        - roboco-a2a: Agent-to-Agent protocol
        - roboco-test: Test/lint/format tools

        Git context is passed to MCP servers so git tools can use defaults.
        """
        # MCP servers run inside agent containers, need to connect via Docker network
        if PROJECT_HOST_PATH:
            api_url = "http://roboco-orchestrator:8000"
        else:
            api_url = f"http://127.0.0.1:{settings.port}"

        mcp_env: dict[str, str] = {
            "ROBOCO_API_URL": api_url,
            "ROBOCO_AGENT_ID": agent_id,
        }

        # Add git context if available
        if git_context:
            if git_context.project_slug:
                mcp_env["ROBOCO_PROJECT_SLUG"] = git_context.project_slug
            if git_context.branch_name:
                mcp_env["ROBOCO_BRANCH"] = git_context.branch_name

        # Base MCP servers - all agents get these
        mcp_servers: dict[str, dict[str, Any]] = {
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

        # Notify server - everyone can READ notifications, only PMs can SEND
        # (permission check happens at handler level)
        mcp_servers["roboco-notify"] = {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "roboco.mcp.notify_server",
                agent_id,
            ],
            "env": mcp_env,
        }

        # Optimal server - knowledge base, RAG, semantic search
        # All agents can search; indexing permissions checked at API level
        mcp_servers["roboco-optimal"] = {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "roboco.mcp.optimal_server",
                agent_id,
            ],
            "env": mcp_env,
        }

        # Git server - branch management, commits, PRs
        # Role-based permissions enforced at handler level:
        # - All agents: read-only (status, log, diff, branch list)
        # - Developers: commit, push, create PR
        # - PMs: create branch, checkout, merge PR
        mcp_servers["roboco-git"] = {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "roboco.mcp.git.git_server",
                agent_id,
            ],
            "env": mcp_env,
        }

        # A2A server - Agent-to-Agent protocol for cross-cell coordination
        # All agents can discover and request help from other agents
        mcp_servers["roboco-a2a"] = {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "roboco.mcp.a2a_server",
                agent_id,
            ],
            "env": mcp_env,
        }

        # Test server - run tests, lint, format, typecheck, build
        # Role-based permissions enforced at handler level
        mcp_servers["roboco-test"] = {
            "command": "uv",
            "args": [
                "run",
                "python",
                "-m",
                "roboco.mcp.test.test_server",
                agent_id,
            ],
            "env": mcp_env,
        }

        config: dict[str, Any] = {"mcpServers": mcp_servers}

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

    def _generate_composed_prompt(self, agent_id: str) -> Path:
        """Generate composed system prompt for an agent.

        Uses the layered prompt composition system:
        base.md + roles/{role}.md + teams/{team}.md + identities/{agent}.md

        Returns:
            Path to the generated prompt file
        """
        # Get role and team from canonical config
        role_str = get_agent_role(agent_id)
        team_str = get_agent_team(agent_id)

        # Convert to enums
        role_enum = AgentRole(role_str) if role_str else None
        team_enum = Team(team_str) if team_str else None

        if not role_enum:
            raise ValueError(f"Unknown role for agent: {agent_id}")

        # Compose the prompt from layers
        prompt_content = compose_prompt(role_enum, team_enum, agent_id)

        # Determine output directory
        if PROJECT_HOST_PATH:
            # Running in container - use shared directory that maps to host
            config_dir = Path("/app/prompts-generated")
            config_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Running directly on host
            config_dir = Path(tempfile.gettempdir()) / "roboco-prompts"
            config_dir.mkdir(parents=True, exist_ok=True)

        # Write to file
        prompt_path = config_dir / f"{agent_id}-prompt.md"
        prompt_path.write_text(prompt_content)

        logger.debug(
            "Generated composed prompt",
            agent_id=agent_id,
            role=role_str,
            team=team_str,
            path=str(prompt_path),
            size=len(prompt_content),
        )

        return prompt_path

    def _get_blueprint_path(self, agent_id: str) -> Path:
        """Get blueprint path for an agent.

        DEPRECATED: Use _generate_composed_prompt() instead.
        Kept for backwards compatibility.
        """
        role = self._get_blueprint_role(agent_id)
        team = self._get_agent_team(agent_id)

        if team == "backend":
            cell_dir = "backend"
        elif team == "frontend":
            cell_dir = "frontend"
        elif team == "ux_ui":
            cell_dir = "ux_ui"
        else:
            cell_dir = "board"

        blueprint_file = f"{role.replace('_', '-')}.md"
        return self.blueprints_dir / cell_dir / blueprint_file

    def _get_blueprint_rel_path(self, agent_id: str) -> str:
        """Get relative blueprint path for container mount."""
        role = self._get_blueprint_role(agent_id)
        team = self._get_agent_team(agent_id)

        if team == "backend":
            cell_dir = "backend"
        elif team == "frontend":
            cell_dir = "frontend"
        elif team == "ux_ui":
            cell_dir = "ux_ui"
        else:
            cell_dir = "board"

        blueprint_file = f"{role.replace('_', '-')}.md"
        return f"{cell_dir}/{blueprint_file}"

    def _get_blueprint_role(self, agent_id: str) -> str:
        """Get blueprint-specific role name from agent_id (used for file paths)."""
        role_map = {
            "be-dev-1": "be-dev",
            "be-dev-2": "be-dev",
            "fe-dev-1": "fe-dev",
            "fe-dev-2": "fe-dev",
            "ux-dev-1": "ux-dev",
            "ux-dev-2": "ux-dev",
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

    # Static team mappings for management agents (ROUTING purposes)
    # NOTE: This differs from agents_config.get_agent_team() intentionally.
    # agents_config returns None for management (no team for permissions).
    # This map returns routing categories for dispatcher task assignment.
    _AGENT_TEAM_MAP: ClassVar[dict[str, str]] = {
        "main-pm": "main_pm",
        "product-owner": "board",
        "auditor": "board",
        "head-marketing": "marketing",
    }

    def _get_agent_team(self, agent_id: str) -> str | None:
        """Get team from agent_id."""
        # Check static mappings first
        if agent_id in self._AGENT_TEAM_MAP:
            return self._AGENT_TEAM_MAP[agent_id]

        # Check cell prefixes
        prefix_map = {"be-": "backend", "fe-": "frontend", "ux-": "ux_ui"}
        for prefix, team in prefix_map.items():
            if agent_id.startswith(prefix):
                return team
        return None

    def _resolve_agent_slug(self, agent_id_or_uuid: str) -> str:
        """Resolve agent UUID to slug. Returns input if already a slug."""
        # Check if it's a known UUID and convert to slug
        if agent_id_or_uuid in UUID_TO_SLUG:
            return UUID_TO_SLUG[agent_id_or_uuid]
        # Already a slug or unknown UUID
        return agent_id_or_uuid

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
        prefix_map = {"backend": "be", "frontend": "fe", "ux_ui": "ux"}
        prefix = prefix_map.get(cell)
        if not prefix:
            return None

        # Build candidate list based on role
        if role == "dev":
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
        # If multiple statuses, make separate requests and combine results
        statuses = status if isinstance(status, list) else [status]
        all_tasks: list[dict[str, Any]] = []

        for single_status in statuses:
            params: dict[str, Any] = {"status": single_status}
            if team:
                params["team"] = team

            try:
                resp = await client.get(f"{self._api_url}/tasks", params=params)
                if resp.status_code == http_status.HTTP_200_OK:
                    tasks: list[dict[str, Any]] = resp.json()
                    all_tasks.extend(tasks)
            except Exception as e:
                logger.error(
                    "Fetch tasks error", status=single_status, team=team, error=str(e)
                )

        return all_tasks

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
    # SMART ROUTING - TASK CLASSIFICATION
    # =========================================================================

    # Keywords that indicate strategic/board-level tasks
    _BOARD_KEYWORDS = frozenset(
        {
            "roadmap",
            "architecture",
            "security",
            "budget",
            "hiring",
            "strategy",
            "vision",
            "milestone",
            "release",
            "launch",
        }
    )

    # Keywords that indicate PM coordination is needed
    _PM_KEYWORDS = frozenset(
        {
            "coordinate",
            "integration",
            "cross-team",
            "sync",
            "planning",
            "milestone",
            "dependencies",
            "review",
        }
    )

    # Keywords that indicate cross-cell work (requires Main PM)
    _CROSS_CELL_KEYWORDS = frozenset(
        {
            "all teams",
            "all cells",
            "every team",
            "every cell",
            "all departments",
            "cross-cell",
            "company-wide",
            "organization-wide",
            "backend and frontend",
            "frontend and backend",
            "all three",
        }
    )

    def _has_board_keywords(self, text: str) -> bool:
        """Check if text contains board-level keywords."""
        return any(kw in text for kw in self._BOARD_KEYWORDS)

    def _has_pm_keywords(self, text: str) -> bool:
        """Check if text contains PM coordination keywords."""
        return any(kw in text for kw in self._PM_KEYWORDS)

    def _has_cross_cell_keywords(self, text: str) -> bool:
        """Check if text indicates work spanning multiple cells."""
        return any(kw in text for kw in self._CROSS_CELL_KEYWORDS)

    # Direct team-to-routing mappings (explicit assignments bypass keyword analysis)
    _TEAM_ROUTING_MAP: ClassVar[dict[str, str]] = {
        "main_pm": "main_pm",
        "board": "board",
        "marketing": "marketing",
    }

    def _classify_task_routing(self, task: dict[str, Any]) -> str:
        """
        Classify a task for routing based on team, complexity, and keywords.

        Returns one of: "board", "main_pm", "cell_pm", "dev", "marketing"
        """
        team = task.get("team")

        # Explicit team assignment takes precedence
        if team in self._TEAM_ROUTING_MAP:
            return self._TEAM_ROUTING_MAP[team]

        # For cell teams, use keyword/complexity analysis
        title = (task.get("title") or "").lower()
        description = (task.get("description") or "").lower()
        text = f"{title} {description}"
        complexity = task.get("estimated_complexity", "medium").lower()

        # Board-level keywords → Board
        if self._has_board_keywords(text):
            return "board"

        # Cross-cell keywords (e.g., "all teams") → Main PM (regardless of complexity)
        if self._has_cross_cell_keywords(text):
            return "main_pm"

        # High complexity or cross-team → Main PM
        if complexity in ("high", "critical") or not team or team == "all":
            return "main_pm"

        # PM keywords or medium complexity → Cell PM
        if self._has_pm_keywords(text) or complexity == "medium":
            return "cell_pm"

        # Low complexity, single team → Direct to dev
        return "dev"

    # Team to PM mapping for routing
    _TEAM_PM_MAP: ClassVar[dict[str, str]] = {
        "backend": "be-pm",
        "frontend": "fe-pm",
        "ux_ui": "ux-pm",
    }

    def _get_routing_target(self, routing: str, task: dict[str, Any]) -> str | None:
        """
        Resolve a routing decision to a specific agent slug.

        Args:
            routing: One of "board", "main_pm", "cell_pm", "dev", "marketing"
            task: The task being routed

        Returns:
            Agent slug (e.g., "main-pm", "be-pm", "be-dev-1") or None
        """
        team = task.get("team")

        # Static routing targets
        static_targets = {
            "board": "product-owner",
            "main_pm": "main-pm",
            "marketing": "head-marketing",
        }
        if routing in static_targets:
            return static_targets[routing]

        # Cell PM routing - requires team lookup
        if routing == "cell_pm":
            return self._TEAM_PM_MAP.get(team, "main-pm") if team else "main-pm"

        # Dev routing - requires agent selection
        if routing == "dev" and team:
            return self._select_agent_for_cell(team, "dev")

        return None

    def _build_main_pm_triage_prompt(self, task: dict[str, Any]) -> str:
        """Build prompt for MAIN PM to triage and distribute to Cell PMs."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        complexity = task.get("complexity", "medium")
        description = task.get("description", "")

        return f"""You are the MAIN PM at RoboCo. This task is assigned to YOU.

TASK: {task_id}
TITLE: {title}
COMPLEXITY: {complexity}
DESCRIPTION: {description[:500]}

YOUR JOB: Either work on this yourself OR distribute to Cell PMs.
You do NOT assign to developers directly - Cell PMs manage their teams.

== WHO YOU ASSIGN TO ==

- Backend work → be-pm (who manages be-dev-1, be-dev-2)
- Frontend work → fe-pm (who manages fe-dev-1, fe-dev-2)
- UX/UI work → ux-pm (who manages ux-dev-1, ux-dev-2)

🚨 NEVER assign to be-dev-1, fe-dev-1, ux-dev-1, ux-dev-2 directly. ONLY to Cell PMs.

== WHEN TO WORK ON IT YOURSELF ==

Work on the task yourself if it's:
- PM work (validation, coordination, planning, reviews)
- Communication tasks (announcements, status updates)
- Something you can do directly without code changes
- Cross-cell coordination that doesn't need delegation

If it makes sense for YOU to do it - just do it!

== MAIN PM WORKFLOW ==

1. GET TASK DETAILS
   roboco_task_get("{task_id}")

2. DECIDE: Keep or delegate?
   - Validation/coordination → Keep for yourself
   - Development work → Delegate to Cell PM(s)

3A. IF KEEPING: Work on it directly
   - roboco_task_plan("{task_id}", ...)
   - roboco_task_start("{task_id}")
   - Do the work
   - roboco_task_submit_pm_review("{task_id}")

3B. IF DELEGATING: Create tasks for Cell PMs
   For each cell that needs work:

   roboco_task_create(
     title="Cell-specific task title",
     description="What needs to be done",
     team="backend",  # or "frontend" or "ux_ui"
     acceptance_criteria=["criterion 1", "criterion 2"],
     assigned_to="be-pm",  # Cell PM, NOT developer!
     status="backlog"
   )

   Then: roboco_task_activate(task_id) for each task

4. LOG YOUR DECISION
   roboco_journal_decision(data)

5. FINISH
   roboco_agent_idle()

== CRITICAL RULES ==
- NEVER assign directly to developers (be-dev-1, fe-dev-1, etc.)
- Cell PMs delegate to their developers - that's THEIR job, not yours
- For cross-cell work: create a task for EACH relevant cell
- Validation tasks stay with you

Start now: roboco_task_get("{task_id}")
"""

    def _build_pm_triage_prompt(self, task: dict[str, Any]) -> str:
        """Build prompt for CELL PM to triage and delegate a task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        complexity = task.get("complexity", "medium")
        team = task.get("team", "unknown")

        # Build team-specific info
        channel = f"{team}-cell" if team != "ux_ui" else "uxui-cell"
        dev_map = {
            "backend": ("be-dev-1", "be-dev-2"),
            "frontend": ("fe-dev-1", "fe-dev-2"),
            "ux_ui": ("ux-dev-1", "ux-dev-2"),
        }
        devs = dev_map.get(team, ("be-dev-1",))
        primary_dev = devs[0]
        dev_options = " or ".join(devs)

        return f"""You are the PM for {team} team. This task is assigned to YOU.

TASK: {task_id}
TITLE: {title}
COMPLEXITY: {complexity}
TEAM: {team}

YOUR JOB: Break down this task, create subtasks, and delegate to developers.
You do NOT code. You coordinate and assign. Developers do the actual work.

== IMPORTANT: PLAN vs SUBTASKS ==

These are TWO DIFFERENT THINGS:

1. PLAN = Your PM approach (HOW to do the task)
   - Created with roboco_task_plan()
   - Just a checklist/strategy attached to the task
   - NOT work items

2. SUBTASKS = Real child tasks (WHAT to do)
   - Created with roboco_task_create(parent_task_id=...)
   - Actual tasks in the database that devs claim and work on
   - Parent task DEPENDS on these completing

For any non-trivial task, you MUST create BOTH:
- A plan (your approach)
- Subtasks (the actual work items for devs)

== TASK LIFECYCLE ==

1. You create subtasks with parent_task_id
2. Devs work on subtasks, complete them
3. When ALL subtasks are done → You get respawned
4. You close the parent task

== PM WORKFLOW ==

1. GET TASK DETAILS
   roboco_task_get("{task_id}")
   Read: description, acceptance criteria, blockers.

2. CREATE YOUR PLAN
   roboco_task_plan("{task_id}", ...) with:
   - approach: Your PM strategy for this task
   - steps: High-level phases (NOT the subtasks)
   - risks: Concerns or blockers

3. LOG YOUR DECISION
   roboco_journal_decision(data) with:
   - title: "PM triage: {{short title}}"
   - context, options, chosen, rationale, task_id

4. CREATE SUBTASKS (for medium/complex tasks)
   For each piece of work, create a REAL subtask:

   roboco_task_create(
     title="Specific subtask title",
     description="What the dev needs to do",
     team="{team}",
     acceptance_criteria=["criterion 1", "criterion 2"],
     parent_task_id="{task_id}",  # REQUIRED - links to parent
     assigned_to="{primary_dev}"  # Assign to a dev
   )

   Create 2-5 subtasks that cover all the work.
   Available developers: {dev_options}

5. START PARENT TASK
   roboco_task_start("{task_id}")
   This puts the parent in "in_progress" while subtasks are worked on.

6. NOTIFY TEAM
   roboco_message_send(data) to "{channel}":
   - content: Task overview, subtasks created, who's assigned
   - message_type: "action"

7. FINISH
   roboco_agent_idle()

== FOR TRIVIAL TASKS ONLY ==

If task is truly trivial (single file change, obvious fix):
- Skip subtasks, just assign directly:
  roboco_task_assign("{task_id}", "{primary_dev}")
- Do NOT call roboco_task_start (dev will do it)

== CRITICAL RULES ==
- NEVER keep tasks for yourself - you delegate, devs execute
- Subtasks MUST have parent_task_id="{task_id}"
- Subtasks MUST have assigned_to (a dev slug like "{primary_dev}")
- When in doubt, create subtasks - it's better to over-structure

Start now: roboco_task_get("{task_id}")
"""

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
        # Orchestrator uses SYSTEM role for internal API calls
        # Using a well-known UUID for the orchestrator identity
        headers = {
            "X-Agent-ID": "00000000-0000-0000-0000-000000000000",
            "X-Agent-Role": "system",
        }
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            # PM triage first - routes new tasks to appropriate level
            await self._dispatch_pm_work(client)

            # PM closure - check parent tasks ready to close
            await self._dispatch_pm_closure_work(client)

            # Task-based dispatchers (check task statuses)
            # Dev work only picks up pre-assigned tasks now
            await self._dispatch_dev_work(client)
            await self._dispatch_qa_work(client)
            await self._dispatch_doc_work(client)
            await self._dispatch_pm_review_work(client)
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

    async def _dispatch_pm_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM triage work - routes new tasks to appropriate level.

        This is the FIRST dispatcher called - it classifies unassigned tasks
        and routes them to Board, Main PM, Cell PM, or directly to devs.
        Also handles already-assigned pending tasks for PM agents.

        Monitors: pending tasks (both assigned and unassigned)
        Spawns: product-owner, main-pm, be-pm, fe-pm, ux-pm (or devs for simple)
        """
        # Get pending tasks
        tasks = await self._fetch_tasks(client, "pending")

        # PM-level agents that can have direct assignments
        # NOTE: Only actual PMs, not board members (product-owner, etc.)
        # Board members are handled by their dedicated dispatch methods
        pm_agents = {
            "main-pm",
            "be-pm",
            "fe-pm",
            "ux-pm",
        }

        for task in tasks:
            assigned_to = task.get("assigned_to")

            # Handle already-assigned tasks for PM agents
            if assigned_to:
                agent_slug = self._resolve_agent_slug(assigned_to)
                if agent_slug in pm_agents and not self._is_agent_active(agent_slug):
                    logger.info(
                        "Spawning assigned PM agent",
                        task_id=task.get("id"),
                        agent_id=agent_slug,
                    )
                    # Use Main PM prompt for main-pm, Cell PM prompt for others
                    pm_prompt = (
                        self._build_main_pm_triage_prompt(task)
                        if agent_slug == "main-pm"
                        else self._build_pm_triage_prompt(task)
                    )
                    await self.spawn_agent(
                        agent_id=agent_slug,
                        task_id=task["id"],
                        initial_prompt=pm_prompt,
                    )
                continue

            # Classify the task
            routing = self._classify_task_routing(task)
            agent_id = self._get_routing_target(routing, task)

            if not agent_id:
                logger.warning(
                    "No routing target found",
                    task_id=task.get("id"),
                    routing=routing,
                )
                continue

            logger.info(
                "Routing task",
                task_id=task.get("id"),
                routing=routing,
                agent_id=agent_id,
            )

            # If target agent is already active, claim for them
            if self._is_agent_active(agent_id):
                await self._claim_task_for_agent(client, task["id"], agent_id)
                continue

            # Claim and spawn with appropriate prompt
            if await self._claim_task_for_agent(client, task["id"], agent_id):
                # Use appropriate prompt based on agent type
                if routing == "dev":
                    prompt = self._build_dev_prompt(task)
                elif routing == "main_pm" or agent_id == "main-pm":
                    prompt = self._build_main_pm_triage_prompt(task)
                else:
                    prompt = self._build_pm_triage_prompt(task)

                await self.spawn_agent(
                    agent_id=agent_id,
                    task_id=task["id"],
                    initial_prompt=prompt,
                )

    async def _dispatch_pm_closure_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM closure work - check parent tasks ready to close.

        When all subtasks of a parent task are completed, spawn the PM
        to review and close the parent task.

        Monitors: tasks with completed subtasks but parent still open
        Spawns: be-pm, fe-pm, ux-pm, main-pm (based on parent team)
        """
        # Find parent tasks that might have children ready for closure
        # Include "paused" - PM pauses while waiting, respawned when subtasks done
        # Include "awaiting_pm_review" - parent awaiting review when children done
        parent_statuses = ["claimed", "in_progress", "paused", "awaiting_pm_review"]

        for status in parent_statuses:
            tasks = await self._fetch_tasks(client, status)

            for task in tasks:
                task_id = task.get("id")
                if not task_id:
                    continue

                # Check if this task has any descendants (children, grandchildren, etc.)
                descendants = await self._fetch_all_descendants(client, task_id)
                if not descendants:
                    continue  # Not a parent task

                # Check if all descendants are in terminal states
                all_complete = all(
                    st.get("status") in ("completed", "cancelled") for st in descendants
                )

                if not all_complete:
                    continue  # Not ready for closure

                # Parent has all subtasks completed - spawn PM to close
                team = task.get("team")
                if team in ["backend", "frontend", "ux_ui"]:
                    pm_id = self._TEAM_PM_MAP.get(team, "be-pm")
                else:
                    # main_pm, board, or no team → Main PM handles closure
                    pm_id = "main-pm"

                if self._is_agent_active(pm_id):
                    continue  # PM already working

                logger.info(
                    "Parent task ready for closure",
                    task_id=task_id,
                    descendants_count=len(descendants),
                    pm_id=pm_id,
                )

                prompt = self._build_pm_closure_prompt(task, descendants)
                await self.spawn_agent(
                    agent_id=pm_id,
                    task_id=task_id,
                    initial_prompt=prompt,
                )

    async def _fetch_subtasks(
        self, client: httpx.AsyncClient, parent_id: str
    ) -> list[dict[str, Any]]:
        """Fetch direct subtasks for a parent task."""
        try:
            resp = await client.get(
                f"{self._api_url}/tasks",
                params={"parent_task_id": parent_id},
            )
            if resp.status_code == http_status.HTTP_200_OK:
                data = resp.json()
                tasks = data.get("tasks", data) if isinstance(data, dict) else data
                return list(tasks) if tasks else []
        except Exception as e:
            logger.warning(
                "Failed to fetch subtasks", parent_id=parent_id, error=str(e)
            )
        return []

    async def _fetch_all_descendants(
        self, client: httpx.AsyncClient, task_id: str
    ) -> list[dict[str, Any]]:
        """Fetch ALL descendants (children, grandchildren, etc.) recursively.

        Uses the /tasks/{id}/descendants endpoint which does BFS traversal.
        """
        try:
            resp = await client.get(f"{self._api_url}/tasks/{task_id}/descendants")
            if resp.status_code == http_status.HTTP_200_OK:
                data = resp.json()
                # Endpoint returns list directly
                return list(data) if data else []
        except Exception as e:
            logger.warning("Failed to fetch descendants", task_id=task_id, error=str(e))
        return []

    def _build_pm_closure_prompt(
        self, task: dict[str, Any], subtasks: list[dict[str, Any]]
    ) -> str:
        """Build prompt for PM to review and close a parent task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        subtask_summary = "\n".join(
            f"  - {st.get('title', 'Untitled')} ({st.get('status', 'unknown')})"
            for st in subtasks
        )

        channel = f"{team}-cell" if team != "ux_ui" else "uxui-cell"

        return f"""You are reviewing a parent task for closure.

TASK: {task_id}
TITLE: {title}
TEAM: {team}

ALL SUBTASKS COMPLETED:
{subtask_summary}

== YOUR PM CLOSURE WORKFLOW ==

1. REVIEW
   Call roboco_task_get("{task_id}") to review the parent task.
   Check: Were all acceptance criteria met by the subtasks?

2. ASSESS SUBTASKS
   Review each subtask's completion notes and outcomes.
   Were there any issues, learnings, or concerns?

3. JOURNAL (log closure decision)
   Call roboco_journal_decision(data) with:
   - title: "Task closure: {title}"
   - context: Summary of what was accomplished
   - options: Close vs Needs refinement
   - chosen: Your decision
   - rationale: Why
   - task_id: "{task_id}"

4. COMMUNICATE
   Call roboco_message_send(data) to #{channel}:
   - Announce task completion or any follow-up needed

5. CLOSE OR REFINE
   - If all criteria met: Call roboco_task_complete("{task_id}")
   - If needs more work: Create new subtasks with parent_task_id

6. FINISH
   Call roboco_agent_idle()

Begin with step 1: roboco_task_get("{task_id}")
"""

    def _get_prompt_for_agent(self, agent_slug: str, task: dict[str, Any]) -> str:
        """Get the appropriate prompt based on agent role."""
        role = get_agent_role(agent_slug)
        if role == "developer":
            return self._build_dev_prompt(task)
        elif role == "documenter":
            return self._build_doc_prompt(task)
        elif role == "qa":
            return self._build_qa_prompt(task)
        else:
            # PM or other - use dev prompt as fallback
            return self._build_dev_prompt(task)

    async def _dispatch_dev_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch assigned pending work to the assigned agent.

        NOTE: This handles PRE-ASSIGNED tasks (assigned by PM) and
        needs_revision tasks. New unassigned pending tasks are handled by
        _dispatch_pm_work() which routes them through the PM hierarchy.

        Monitors: assigned pending tasks, needs_revision tasks
        Spawns: Any assigned agent (dev, doc, qa) with appropriate prompt
        """
        # Get tasks needing attention
        tasks = await self._fetch_tasks(client, ["pending", "needs_revision"])

        for task in tasks:
            team = task.get("team")
            if team not in ["backend", "frontend", "ux_ui"]:
                continue

            assigned_to = task.get("assigned_to")
            # Resolve UUID to slug for agent operations
            agent_slug = self._resolve_agent_slug(assigned_to) if assigned_to else None

            # For needs_revision, spawn the assigned dev to fix
            if task.get("status") == "needs_revision" and agent_slug:
                if not self._is_agent_active(agent_slug):
                    await self.spawn_agent(
                        agent_id=agent_slug,
                        task_id=task["id"],
                        initial_prompt=self._build_dev_prompt(task),
                    )
                continue

            # For pending tasks that ARE already assigned (by PM),
            # spawn the assigned agent with the appropriate prompt
            if agent_slug and not self._is_agent_active(agent_slug):
                await self.spawn_agent(
                    agent_id=agent_slug,
                    task_id=task["id"],
                    initial_prompt=self._get_prompt_for_agent(agent_slug, task),
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
            if team not in ["backend", "frontend", "ux_ui"]:
                continue

            assigned_to = task.get("assigned_to")

            # If already assigned, check if that agent is running
            if assigned_to:
                assigned_slug = self._resolve_agent_slug(assigned_to)
                if self._is_agent_active(assigned_slug):
                    # Agent is running, they'll handle it
                    continue
                # Agent not running - spawn them to continue
                await self.spawn_agent(
                    agent_id=assigned_slug,
                    task_id=task["id"],
                    initial_prompt=self._build_qa_prompt(task),
                )
                continue

            # Unassigned task - select QA agent for this team
            agent_id = self._select_agent_for_cell(team, "qa")
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                # QA already running, they'll pick up on scan
                continue

            # Claim the task for QA agent BEFORE spawning
            if not await self._claim_task_for_agent(client, task["id"], agent_id):
                logger.warning(
                    "Failed to claim awaiting_qa task for QA",
                    task_id=task["id"],
                    agent_id=agent_id,
                )
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
            if team not in ["backend", "frontend", "ux_ui"]:
                continue

            assigned_to = task.get("assigned_to")

            # If already assigned, check if that agent is running
            if assigned_to:
                assigned_slug = self._resolve_agent_slug(assigned_to)
                if self._is_agent_active(assigned_slug):
                    continue
                # Agent not running - spawn them to continue
                await self.spawn_agent(
                    agent_id=assigned_slug,
                    task_id=task["id"],
                    initial_prompt=self._build_doc_prompt(task),
                )
                continue

            # Unassigned task - select documenter for this team
            agent_id = self._select_agent_for_cell(team, "doc")
            if not agent_id:
                continue

            if self._is_agent_active(agent_id):
                continue

            # Claim the task for documenter BEFORE spawning
            if not await self._claim_task_for_agent(client, task["id"], agent_id):
                logger.warning(
                    "Failed to claim awaiting_documentation task for doc",
                    task_id=task["id"],
                    agent_id=agent_id,
                )
                continue

            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=self._build_doc_prompt(task),
            )
            break

    async def _dispatch_pm_review_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM review work to cell PMs or Main PM.

        Monitors: awaiting_pm_review tasks
        Spawns: be-pm, fe-pm, ux-pm, main-pm
        """
        tasks = await self._fetch_tasks(client, "awaiting_pm_review")

        for task in tasks:
            team = task.get("team")
            assigned_to = task.get("assigned_to")

            # If already assigned, check if that agent is running
            if assigned_to:
                assigned_slug = self._resolve_agent_slug(assigned_to)
                if self._is_agent_active(assigned_slug):
                    continue
                # Agent not running - spawn them to continue
                await self.spawn_agent(
                    agent_id=assigned_slug,
                    task_id=task["id"],
                    initial_prompt=self._build_pm_review_prompt(task),
                )
                continue

            # Unassigned task - select PM based on team
            # Cell tasks go to Cell PM, cross-cell/main_pm tasks go to Main PM
            if team in ["backend", "frontend", "ux_ui"]:
                pm_id = self._TEAM_PM_MAP.get(team, "be-pm")
            else:
                # main_pm, board, or no team → Main PM handles it
                pm_id = "main-pm"

            if self._is_agent_active(pm_id):
                continue

            # Claim the task for PM BEFORE spawning
            if not await self._claim_task_for_agent(client, task["id"], pm_id):
                logger.warning(
                    "Failed to claim awaiting_pm_review task for PM",
                    task_id=task["id"],
                    agent_id=pm_id,
                )
                continue

            await self.spawn_agent(
                agent_id=pm_id,
                task_id=task["id"],
                initial_prompt=self._build_pm_review_prompt(task),
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
            if team not in ["backend", "frontend", "ux_ui"]:
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
4. Call roboco_task_docs_complete("{task_id}") when documentation is done
5. Call roboco_task_scan() to check for more documentation work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

    def _build_pm_review_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for PM to review and complete a task."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        return f"""A task is awaiting your PM review for final completion.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}

This task has passed QA and documentation. Review and complete:

1. Call roboco_task_get("{task_id}") to review the task details
2. Verify dev_notes, QA notes, and documentation are satisfactory
3. If this task has subtasks, verify all subtasks are completed
4. Call roboco_task_complete("{task_id}") to finalize the task
5. Call roboco_task_scan() to check for more tasks needing review
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

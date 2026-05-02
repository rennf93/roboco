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
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import httpx

if TYPE_CHECKING:
    from roboco.services.llm import AgentRoute
import structlog
from fastapi import status as http_status

from roboco.agents.factories._base import compose_prompt
from roboco.agents_config import (
    ALL_DOCS,
    get_agent_role,
    get_agent_team,
    get_escalation_target,
)
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

# Complete list of MCP tools that trigger traceability reminders.
# Post-gateway: every state-changing verb agents call routes through
# roboco-flow (intent verbs) or roboco-do (content tools). Read-only git
# views (roboco-git-readonly) and KB queries (roboco-optimal) emit one
# additional trigger because they are the inputs PMs/devs cite when they
# justify their next move.
TRACEABILITY_TRIGGER_TOOLS: list[str] = [
    # === Intent verbs (all role-scoped lifecycle transitions) ===
    "mcp__roboco-flow__*",
    # === Content tools (commit/push/PR + journal/notify/message) ===
    "mcp__roboco-do__*",
    # === KB Tools ===
    "mcp__roboco-optimal__roboco_ask_mentor",
]


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


@dataclass(frozen=True)
class _SlaBreach:
    """Per-(role, state) SLA breach payload for _escalate_sla_breach."""

    task_id: str
    role: str
    status: str
    age_seconds: int
    sla_seconds: int


def _read_project_slug(task: dict[str, Any]) -> str | None:
    """Extract project slug from a task payload shape-tolerantly."""
    slug = task.get("project_slug")
    if slug:
        return str(slug)
    project = task.get("project") or {}
    inner = project.get("slug") if isinstance(project, dict) else None
    return str(inner) if inner else None


def _resolve_agent_cli_model(provider_type: str, model: str) -> str:
    """Translate an agent model name to the string Claude Code expects.

    For the Anthropic provider, short names (``opus|sonnet|haiku``) are
    translated through ``MODEL_MAP`` as they always were.  For non-Anthropic
    providers (currently Ollama Cloud) the model identifier is passed verbatim
    so raw tags like ``kimi-k2.6:cloud`` reach the Ollama-side integration
    intact.

    Extracted as a module-level function so both the ``--model`` CLI arg
    builder and the ``CLAUDE_CODE_SUBAGENT_MODEL`` env-var injector can call
    the same logic without referencing the class by name inside a staticmethod.
    """
    if provider_type == "anthropic":
        return MODEL_MAP.get(model, model)
    return model


# =============================================================================
# SPAWN MANIFEST — per-developer tool manifest mounting (Phase 1)
# =============================================================================

# Phase 4: every role gets a gateway manifest. The legacy briefing path is gone.
GATEWAY_ENABLED_ROLES: frozenset[str] = frozenset(
    {
        "developer",
        "qa",
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
    }
)


def _build_manifest_for_agent(agent_id: str, model: str) -> Path | None:
    """Write a SpawnManifest for developer-role agents; return the host path.

    Returns ``None`` for roles outside ``GATEWAY_ENABLED_ROLES`` so callers
    can skip the manifest mount entirely without extra branching.

    Args:
        agent_id: Agent slug (e.g. ``be-dev-1``).
        model:    Resolved model name passed to ``SpawnInputs.agent_model``.

    Returns:
        Absolute host path to the written JSON file, or ``None``.
    """
    from uuid import UUID

    from roboco.runtime.spawn_manifest import (
        SpawnInputs,
        build_for_role,
        write_manifest,
    )

    role = get_agent_role(agent_id) or "developer"
    if role not in GATEWAY_ENABLED_ROLES:
        return None

    team = get_agent_team(agent_id) or "backend"
    # UUID for the agent comes from the seeded AGENT_UUIDS map (slug -> UUID
    # string).  Fall back to uuid4 for unknown agents so the function stays
    # callable in tests without seeded data.
    raw_uuid = AGENT_UUIDS.get(agent_id)
    agent_uuid = UUID(raw_uuid) if raw_uuid else __import__("uuid").uuid4()

    workspace_path = Path(settings.workspaces_root) / "roboco" / team / agent_id

    manifest = build_for_role(
        SpawnInputs(
            agent_id=agent_uuid,
            role=role,
            team=team,
            workspace_path=workspace_path,
            agent_model=model,
        )
    )

    host_dir = Path(settings.manifest_host_dir)
    host_path = host_dir / f"{agent_id}.json"
    write_manifest(manifest, host_path)
    return host_path


# =============================================================================
# GATEWAY PRE-SPAWN CHECK (gated behind settings.gateway_enabled)
# =============================================================================


async def _count_recent_spawns_for_task(
    db_session: Any,
    task_id: Any,
    cutoff: datetime,
) -> int:
    """Count recent SPAWN decisions for ``task_id`` since ``cutoff``."""
    from sqlalchemy import select

    from roboco.db.tables import GatewayTriggerTable

    result = await db_session.execute(
        select(GatewayTriggerTable).where(
            GatewayTriggerTable.task_id == task_id,
            GatewayTriggerTable.created_at >= cutoff,
            GatewayTriggerTable.decision == "spawn",
        )
    )
    return len(result.scalars().all())


async def _count_recent_spawns_for_role(
    db_session: Any,
    target_role: str,
    cutoff: datetime,
) -> int:
    """Count recent SPAWN decisions for ``target_role`` since ``cutoff``."""
    from sqlalchemy import select

    from roboco.db.tables import GatewayTriggerTable

    result = await db_session.execute(
        select(GatewayTriggerTable).where(
            GatewayTriggerTable.target_role == target_role,
            GatewayTriggerTable.created_at >= cutoff,
            GatewayTriggerTable.decision == "spawn",
        )
    )
    return len(result.scalars().all())


async def _record_trigger_decision(
    db_session: Any,
    task_id: Any,
    trigger_kind: str,
    target_role: str,
    decision: Any,
) -> None:
    """Persist a gateway trigger decision row."""
    from uuid import uuid4 as _uuid4

    from roboco.db.tables import GatewayTriggerTable

    row = GatewayTriggerTable(
        id=_uuid4(),
        trigger_kind=trigger_kind,
        task_id=task_id,
        target_role=target_role,
        decision=decision.outcome.value,
        decision_reason=decision.reason,
    )
    db_session.add(row)
    await db_session.flush()


async def gateway_pre_spawn_check(
    *,
    task_id: str | None,
    trigger_kind: str,
    target_role: str,
) -> tuple[str, str]:
    """Consult trigger_filter before spawning a container.

    Returns a ``(outcome, reason)`` tuple where ``outcome`` is one of
    ``"spawn"``, ``"queue"``, or ``"drop"``.

    When ``settings.gateway_enabled`` is False (the default in Phase 0) this
    function returns immediately with ``("spawn", "gateway disabled")`` so the
    existing legacy dispatch path is **completely unchanged**.
    """
    if not settings.gateway_enabled:
        return "spawn", "gateway disabled (legacy path)"

    from roboco.db.base import get_session_factory
    from roboco.services.gateway.trigger_filter import (
        Decision,
        SpawnConfig,
        SpawnDecision,
        TriggerContext,
        TriggerKind,
        decide_spawn,
    )

    cutoff = datetime.now(tz=UTC) - timedelta(seconds=settings.spawn_cooldown_seconds)
    role_cutoff = datetime.now(tz=UTC) - timedelta(seconds=60)

    # When no task_id we cannot query counts; allow (no-task spawns like idle PMs).
    if task_id is None:
        return SpawnDecision.SPAWN, "no task_id — no-task spawn, skip gate"

    try:
        from sqlalchemy import select as _select

        from roboco.db.tables import TaskTable as _TaskTable

        factory = get_session_factory()
        async with factory() as db:
            recent_for_task = await _count_recent_spawns_for_task(db, task_id, cutoff)
            recent_for_role = await _count_recent_spawns_for_role(
                db, target_role, role_cutoff
            )

            # Load the lightweight task proxy needed by is_stale / decide_spawn.
            task_result = await db.execute(
                _select(_TaskTable).where(_TaskTable.id == task_id)
            )
            task_row = task_result.scalars().first()

            if task_row is None:
                return SpawnDecision.SPAWN, "task not found in DB — allow by default"

            trigger = TriggerContext(
                kind=TriggerKind(trigger_kind),
                skill=None,
                recent_spawns_for_task=recent_for_task,
                recent_spawns_for_role=recent_for_role,
            )
            config = SpawnConfig(
                cooldown_seconds=settings.spawn_cooldown_seconds,
                role_rate_per_minute=settings.role_spawn_rate_per_minute,
                claim_stale_seconds=settings.claim_stale_seconds,
            )
            decision: Decision = decide_spawn(
                task=task_row, trigger=trigger, config=config
            )

            await _record_trigger_decision(
                db, task_id, trigger_kind, target_role, decision
            )
            await db.commit()

        return decision.outcome.value, decision.reason

    except Exception as exc:
        # Gateway errors must never block a spawn — degrade gracefully.
        logger.warning(
            "Gateway pre-spawn check failed; defaulting to spawn",
            task_id=task_id,
            trigger_kind=trigger_kind,
            error=str(exc),
        )
        return "spawn", f"gateway error (degraded): {exc}"


class AgentReadinessError(Exception):
    """Raised when spawn_agent refuses to spawn because the task isn't ready.

    The pre-flight gate auto-blocks the offending task before raising, so the
    dispatcher doesn't keep retrying. Callers should log and move on.
    """


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
        self._sweeper_task: asyncio.Task | None = None
        # Strong refs for fire-and-forget audit writes. Without this, the
        # event loop only weak-refs the Task and may GC it before it
        # commits — audit_log was silently empty because of this.
        self._bg_tasks: set[asyncio.Task[None]] = set()
        # Wake-up signal for the dispatcher. Set() by API routes immediately
        # after status transitions so the dispatcher reacts in milliseconds
        # instead of waiting for the next 30-second tick.
        self._dispatch_wake: asyncio.Event = asyncio.Event()
        self._running = False
        self._lock = asyncio.Lock()
        # Per-tick set of task_ids already handled by an earlier
        # dispatcher. Reset at the start of every _dispatch_all_work.
        # Consumed via `self._mark_task_handled` / `_is_task_handled`.
        self._tick_handled_tasks: set[str] = set()
        # Respawn circuit breaker: per (agent_slug, task_id), tracks how
        # many times we've spawned without the task status changing. A PM
        # that gets re-spawned on the same pending task with no progress
        # is in a loop — without this gate the orchestrator re-spawns every
        # tick forever (seen in production on 2026-04-22).
        self._pm_respawn_tracker: dict[tuple[str, str], dict[str, Any]] = {}

    # =========================================================================
    # LIFECYCLE
    # =========================================================================

    async def start(self) -> None:
        """Start the orchestrator."""
        self._running = True

        # Ensure agent image is built
        await self._ensure_agent_image()

        # Restore any WaitingRecord rows left by a prior orchestrator run so
        # agents that were WAITING_LONG at shutdown can still be resolved.
        await self.restore_waiting_records()

        # Note: Per-agent settings are now generated at spawn time
        # via _generate_agent_settings() - no shared settings needed

        # Start background tasks
        self._health_task = asyncio.create_task(self._health_loop())
        self._dispatcher_task = asyncio.create_task(self._dispatcher_loop())
        self._sweeper_task = asyncio.create_task(self._sweeper_loop())

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

        if self._sweeper_task:
            self._sweeper_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._sweeper_task

        # Stop all agents
        for agent_id in list(self._instances.keys()):
            await self.stop_agent(agent_id)

        logger.info("Orchestrator stopped")

    def get_running_agents(self) -> set[str]:
        """Get set of currently running agent IDs."""
        return set(self._instances.keys())

    def is_agent_busy(self, agent_id: str) -> bool:
        """
        Check if agent has active work.

        An agent is busy if:
        1. They're in the running instances, AND
        2. They have a task claimed/in_progress/verifying

        Note: This is a lightweight check based on instance status.
        For full busy detection, the event handler queries the database.
        """
        if agent_id not in self._instances:
            return False
        instance = self._instances[agent_id]
        # If agent is running with a task, they're busy
        return instance.current_task_id is not None

    def queue_priority_work(self, agent_id: str, work: dict[str, Any]) -> None:
        """
        Queue priority work for an agent.

        This is a placeholder for future priority queue functionality.
        Currently logs the request for observability.
        """
        logger.info(
            "Priority work queued (not yet implemented)",
            agent_id=agent_id,
            work_type=work.get("type", "unknown"),
        )

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

    # =========================================================================
    # PER-AGENT SETTINGS GENERATION
    # =========================================================================

    def _get_role_permissions(
        self, role: str, workspace_path: str, cell_workspace_path: str
    ) -> dict[str, list[str]]:
        """Get role-specific allow/deny lists for Claude Code tools.

        Post-gateway shape: every state-changing operation an agent can
        perform routes through ``mcp__roboco-flow__*`` (intent verbs) or
        ``mcp__roboco-do__*`` (content tools — commit, push, PR, journal,
        notify, message), both granted to every role via ``base_allow``.
        Role-specific configuration here only governs file IO (Write/Edit
        scoping) plus a small handful of legacy native-tool denies that
        remain meaningful for weak models. Read-only git lives in
        ``mcp__roboco-git-readonly__*``.

        Args:
            role: Agent role (developer, qa, documenter, cell_pm, main_pm, etc.)
            workspace_path: Path to agent's own workspace directory
            cell_workspace_path: Path to cell's workspace root (for QA/Docs access)

        Returns:
            Dict with 'allow' and 'deny' lists for Claude Code permissions
        """
        # workspace_path: /data/workspaces/{project}/{team}/{agent}
        # cell_workspace_path: /data/workspaces/{project}/{team}
        configs: dict[str, dict[str, list[str]]] = {
            "developer": {
                "allow": [
                    f"Write({workspace_path}/**)",
                    f"Edit({workspace_path}/**)",
                ],
                "deny": [],
            },
            "qa": {
                # QA reads code + the open PR via the gateway; never edits.
                "allow": [],
                "deny": [
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "documenter": {
                "allow": [
                    f"Write({cell_workspace_path}/**)",
                    f"Edit({cell_workspace_path}/**)",
                    "Write(/app/docs/**)",
                    "Edit(/app/docs/**)",
                    "Write(/app/CHANGELOG.md)",
                    "Edit(/app/CHANGELOG.md)",
                    "Write(/app/README.md)",
                    "Edit(/app/README.md)",
                ],
                "deny": [],
            },
            "cell_pm": {
                # PMs coordinate; they open + merge PRs through the gateway
                # but never author code. Edit/Write are denied so weaker
                # models can't read the subtask title imperatively and
                # start editing source — they have to decompose into a dev
                # subtask. Devs are the only role that authors code.
                "allow": [],
                "deny": [
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "main_pm": {
                # Same reasoning as cell_pm — Main PM sits between CEO and
                # cell PMs; the work product is coordination + review, not
                # commits or edits. Code work routes Main PM → Cell PM →
                # Dev only.
                "allow": [],
                "deny": [
                    "Bash(git commit:*)",
                    "Bash(git push:*)",
                    "Write(*)",
                    "Edit(*)",
                ],
            },
            "product_owner": {
                "allow": [
                    f"Write({workspace_path}/**)",
                    f"Edit({workspace_path}/**)",
                ],
                "deny": [],
            },
            "head_marketing": {
                "allow": [
                    f"Write({workspace_path}/**)",
                    f"Edit({workspace_path}/**)",
                ],
                "deny": [],
            },
            "auditor": {
                # Auditor is read-only across the org — observes, never edits.
                "allow": [],
                "deny": [
                    "Write(*)",
                    "Edit(*)",
                ],
            },
        }

        if role not in configs:
            logger.warning(
                "No Claude Code permissions configured for role; "
                "agent will be limited to base_allow/base_deny.",
                role=role,
            )
        return configs.get(role, {"allow": [], "deny": []})

    def _generate_agent_settings(
        self,
        agent_id: str,
        role: str,
        workspace_path: str,
        cell_workspace_path: str,
    ) -> Path:
        """Generate per-agent Claude Code settings file with role-specific permissions.

        This replaces the shared settings approach. Each agent gets their own
        settings.json with:
        - Base MCP tools allowed for all agents
        - Role-specific tool permissions
        - Explicit deny list blocking native git/file operations

        Args:
            agent_id: Agent identifier (e.g., "be-dev-1")
            role: Agent role (e.g., "developer")
            workspace_path: Path to agent's own workspace directory
            cell_workspace_path: Path to cell's workspace root (for QA/Docs)

        Returns:
            Path to the generated settings file
        """
        # Base MCP tools for all agents. Post-gateway every role gets the
        # full intent-verb + content-tool surface; the orchestrator-side
        # API rejects verbs/tools the agent's role isn't authorized for,
        # so granting `*` here is safe.
        base_allow = [
            "mcp__roboco-flow__*",
            "mcp__roboco-do__*",
            "mcp__roboco-optimal__*",
            "mcp__roboco-git-readonly__*",
            "Read(*)",  # All agents can read any file
        ]

        # Base denials for all agents - block native tools + sensitive reads.
        # The Read/Bash denies below are critical: without them an agent can
        # read `.git/config` (which, pre-fix, had the PAT embedded in the
        # remote URL) or `~/.gitconfig` and exfiltrate project secrets.
        # We also block direct curl/wget to github.com — any git-remote op
        # must go through the orchestrator's git service, which injects the
        # token via bearer header at subprocess time rather than exposing it.
        base_deny = [
            # Block ALL native git commands - must use roboco_git_* tools
            "Bash(git:*)",
            # Block file ops outside workspace (role-specific allows override)
            "Write(*)",
            "Edit(*)",
            # Block reads of credential stores, anywhere on the FS
            "Read(**/.git/config)",
            "Read(**/.gitconfig)",
            "Read(/etc/gitconfig)",
            "Read(~/.netrc)",
            "Read(**/.git-credentials)",
            # Block direct GitHub API/wire access — agents must use
            # roboco_git_* MCP tools so secrets + traceability stay on the
            # orchestrator side.
            "Bash(curl:*github.com*)",
            "Bash(curl:*api.github.com*)",
            "Bash(wget:*github.com*)",
            "Bash(wget:*api.github.com*)",
            # Same idea for cat-ing credential files in a subshell
            "Bash(cat:*.git/config*)",
            "Bash(cat:*.gitconfig*)",
            "Bash(cat:*.git-credentials*)",
            # Block reading env vars that might leak secrets
            "Bash(env:*)",
            "Bash(printenv:*)",
        ]

        # Get role-specific permissions
        role_config = self._get_role_permissions(
            role, workspace_path, cell_workspace_path
        )

        # Combine base + role-specific.
        # defaultMode=bypassPermissions lets unlisted operations proceed
        # without an interactive prompt (which would hang a non-TTY agent
        # container). Explicit deny rules still apply.
        settings: dict[str, Any] = {
            "permissions": {
                "defaultMode": "bypassPermissions",
                "allow": base_allow + role_config["allow"],
                "deny": base_deny + role_config["deny"],
            },
            "hooks": {
                # Start SDK server on session start (for A2A communication)
                "SessionStart": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/sdk-startup-hook.sh",
                            }
                        ]
                    }
                ],
                # Guard Bash: block shell-level git/curl/wget/env patterns
                # that the matcher-based `permissions.deny` can't catch
                # (e.g. `cd X && git fetch`). Redirects agents to the MCP
                # equivalents instead of bloating prompts with rules.
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/bash-guard-hook.sh",
                            }
                        ],
                    },
                ],
                "PostToolUse": [
                    # Traceability reminders (context-aware, runs on specific tools)
                    {
                        "matcher": "|".join(TRACEABILITY_TRIGGER_TOOLS),
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/traceability-hook.sh",
                            }
                        ],
                    },
                    # Check for incoming A2A messages after each tool use
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/a2a-check-hook.sh",
                            }
                        ],
                    },
                    # Per-session budget counter + loop detector. Shared SDK
                    # state lets this hook emit [Budget]/[Loop]/[Halt]
                    # reminders that the orchestrator's kill-switch corroborates.
                    {
                        "matcher": "*",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/post-tool-budget-hook.sh",
                            }
                        ],
                    },
                ],
                # Stop guard: refuse silent exits unless a terminal tool was
                # just called (idle/substitute/escalate/pause/...). Second
                # attempt auto-substitutes via SDK so the task doesn't rot.
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/stop-hook.sh",
                            }
                        ]
                    }
                ],
                # Prompt-injection guard — rejects turns that look like
                # another agent's content trying to override our rules.
                "UserPromptSubmit": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/user-prompt-hook.sh",
                            }
                        ]
                    }
                ],
                # Snapshot budget / terminal state before compact so the
                # next session resumes with continuity.
                "PreCompact": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/pre-compact-hook.sh",
                            }
                        ]
                    }
                ],
                # Post-mortem: write a reflect-journal entry summarising the
                # session (tools called, halt/loop triggered, last tool).
                "SessionEnd": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": "/app/scripts/session-end-hook.sh",
                            }
                        ]
                    }
                ],
            },
        }

        # Write to per-agent settings file
        # When running in container: write to /app/agent-settings (mounted to host)
        # When running on host: use temp directory
        if DATA_HOST_PATH:
            settings_dir = Path("/app/agent-settings")
        else:
            settings_dir = Path(tempfile.gettempdir()) / "roboco-agent-settings"

        settings_dir.mkdir(parents=True, exist_ok=True)
        settings_path = settings_dir / f"{agent_id}-settings.json"

        # Handle case where Docker auto-created a directory instead of a file
        if settings_path.is_dir():
            shutil.rmtree(settings_path)

        settings_path.write_text(json.dumps(settings, indent=2))

        logger.debug(
            "Generated per-agent settings",
            agent_id=agent_id,
            role=role,
            settings_path=str(settings_path),
            allow_count=len(settings["permissions"]["allow"]),
            deny_count=len(settings["permissions"]["deny"]),
        )

        return settings_path

    # =========================================================================
    # AGENT SPAWNING
    # =========================================================================

    def _task_git_context(self, task: dict[str, Any]) -> SpawnGitContext | None:
        """Build SpawnGitContext from a task dict for workspace mounting.

        Without this, spawned agents fall back to project_slug="default"
        and get a Write/Edit permission lock to /data/workspaces/default/...
        which does not exist, so the agent's file tools fail.
        """
        project_slug = task.get("project_slug")
        if not project_slug:
            return None
        return SpawnGitContext(
            project_slug=project_slug,
            branch_name=task.get("branch_name"),
        )

    def _fire_audit(
        self,
        *,
        event_type: str,
        agent_slug: str,
        task_id: str | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Emit an agent-lifecycle audit event without blocking the caller.

        Strong-refs the Task so it isn't garbage-collected before it
        commits to `audit_log`. Silently skips if there's no running loop
        (e.g. sync unit tests).
        """
        import contextlib as _ctx

        from roboco.services.audit import get_audit_service

        with _ctx.suppress(RuntimeError):
            bg = asyncio.get_running_loop().create_task(
                get_audit_service().log_agent_event(
                    event_type=event_type,
                    agent_slug=agent_slug,
                    task_id=task_id,
                    details=details or {},
                    severity=severity,
                )
            )
            self._bg_tasks.add(bg)
            bg.add_done_callback(self._bg_tasks.discard)

    async def _git_context_default_project(self) -> SpawnGitContext | None:
        """Return git context for the 'default' project when no task is known.

        Used by no-task spawns (idle PM, scanner-only agents). Picks the
        first active project in the DB — the common case is a single-project
        deployment, where this resolves to the correct slug; for multi-
        project deployments the caller should pass task_id to disambiguate.
        """
        from sqlalchemy import select

        from roboco.db.base import get_db_context
        from roboco.db.tables import ProjectTable

        try:
            async with get_db_context() as db:
                result = await db.execute(
                    select(ProjectTable.slug, ProjectTable.default_branch)
                    .where(ProjectTable.is_active.is_(True))
                    .order_by(ProjectTable.created_at.asc())
                    .limit(1)
                )
                row = result.first()
                if row is None:
                    return None
                slug, default_branch = row
                if not slug:
                    return None
                return SpawnGitContext(
                    project_slug=slug,
                    branch_name=default_branch,
                )
        except Exception as e:
            logger.warning(
                "Could not derive default project git context",
                error=str(e),
            )
            return None

    async def _git_context_from_task_id(self, task_id: str) -> SpawnGitContext | None:
        """Load a task by ID and derive git context for spawning.

        Used by `spawn_agent` when called without an explicit git_context
        (e.g. the /agents/{slug}/spawn API endpoint). Without this, agents
        spawned via that endpoint get project_slug="default" and their
        workspace mount points at a path that doesn't exist.
        """
        from sqlalchemy import select

        from roboco.db.base import get_db_context
        from roboco.db.tables import ProjectTable, TaskTable

        try:
            async with get_db_context() as db:
                result = await db.execute(
                    select(TaskTable.branch_name, ProjectTable.slug)
                    .select_from(TaskTable)
                    .join(ProjectTable, TaskTable.project_id == ProjectTable.id)
                    .where(TaskTable.id == task_id)
                )
                row = result.first()
                if row is None:
                    return None
                branch_name, project_slug = row
                if not project_slug:
                    return None
                return SpawnGitContext(
                    project_slug=project_slug,
                    branch_name=branch_name,
                )
        except Exception as e:
            logger.warning(
                "Could not derive git context from task_id",
                task_id=task_id,
                error=str(e),
            )
            return None

    async def _safe_spawn(
        self,
        *,
        agent_id: str,
        task_id: str | None = None,
        initial_prompt: str | None = None,
        git_context: SpawnGitContext | None = None,
        context_label: str = "dispatcher",
    ) -> AgentInstance | None:
        """Spawn an agent, absorbing errors so one bad spawn doesn't abort the
        rest of the dispatcher's loop.

        Each dispatcher iterates many tasks; if `spawn_agent` raised, the
        remaining tasks were skipped until the next tick. This wrapper logs
        and returns None on failure so siblings still get dispatched.

        When ``settings.gateway_enabled`` is True the gateway pre-spawn check
        runs first; a QUEUE or DROP outcome skips the container launch.
        When the flag is False (Phase 0 default) this block is a single
        boolean test and the legacy path is completely unchanged.
        """
        # Gateway gate — zero-cost when gateway_enabled=False (Phase 0 default).
        target_role = get_agent_role(agent_id) or "unknown"
        # Map context_label to one of the TriggerKind string values; unknown
        # labels fall back to "scan" which is the least-specific kind.
        trigger_kind_map = {
            "a2a": "a2a",
            "escalation": "escalation",
            "notification": "notification",
        }
        trigger_kind = trigger_kind_map.get(context_label, "scan")

        outcome, reason = await gateway_pre_spawn_check(
            task_id=task_id,
            trigger_kind=trigger_kind,
            target_role=target_role,
        )
        if outcome != "spawn":
            logger.info(
                "Gateway pre-spawn check suppressed spawn",
                agent_id=agent_id,
                task_id=task_id,
                outcome=outcome,
                reason=reason,
            )
            return None

        try:
            return await self.spawn_agent(
                agent_id=agent_id,
                task_id=task_id,
                initial_prompt=initial_prompt,
                git_context=git_context,
            )
        except Exception as e:
            logger.error(
                "Spawn failed during dispatch; continuing with next task",
                context=context_label,
                agent_id=agent_id,
                task_id=task_id,
                error=str(e),
            )
            return None

    async def _resolve_spawn_git_context(
        self,
        git_context: SpawnGitContext | None,
        task_id: str | None,
    ) -> SpawnGitContext | None:
        """Auto-derive git context if the caller didn't supply one."""
        if git_context is not None and git_context.project_slug:
            return git_context
        derived: SpawnGitContext | None = None
        if task_id:
            derived = await self._git_context_from_task_id(task_id)
        if derived is None:
            derived = await self._git_context_default_project()
        return derived if derived is not None else git_context

    def _existing_running_instance(self, agent_id: str) -> AgentInstance | None:
        """Return the running instance for agent_id, or None if it can be respawned."""
        existing = self._instances.get(agent_id)
        if existing is None:
            return None
        if existing.state in (AgentState.OFFLINE, AgentState.WAITING_LONG):
            return None
        logger.warning(
            "Agent already running",
            agent_id=agent_id,
            state=existing.state,
        )
        return existing

    def _resolve_project_slug(
        self,
        git_context: SpawnGitContext | None,
        agent_id: str,
        task_id: str | None,
    ) -> str:
        """Pull project_slug from context, or fall back to 'default' with a warning."""
        project_slug = (
            git_context.project_slug
            if git_context and git_context.project_slug
            else None
        )
        if not project_slug:
            logger.warning(
                "Spawning agent without project_slug; workspace fallback used. "
                "Agent file tools will be locked to a nonexistent path.",
                agent_id=agent_id,
                task_id=task_id,
            )
            project_slug = "default"
        return project_slug

    async def _prepare_agent_spawn(
        self,
        agent_id: str,
        task_id: str | None,
        model: str | None,
        git_context: SpawnGitContext | None,
    ) -> tuple[AgentConfig, AgentInstance, Path | None]:
        """Build AgentConfig + AgentInstance and surface per-agent settings path."""
        blueprint_path = self._generate_composed_prompt(agent_id)
        canonical_role = get_agent_role(agent_id)
        team = get_agent_team(agent_id)

        # Resolve the provider route for this agent. Caller-supplied `model`
        # wins (dispatcher overrides, tests). Otherwise the routing service
        # resolves (agent_slug | role | global) assignments, falling back
        # internally to `ROLE_MODEL_MAP` when no rows exist — so a fresh
        # deployment with an empty `model_assignments` table behaves exactly
        # as before.
        route = await self._resolve_agent_route(agent_id)
        if not model:
            model = route.model_name

        project_slug = self._resolve_project_slug(git_context, agent_id, task_id)
        workspace_path = f"/data/workspaces/{project_slug}/{team}/{agent_id}"
        cell_workspace_path = f"/data/workspaces/{project_slug}/{team}"

        agent_settings_path = self._generate_agent_settings(
            agent_id, canonical_role, workspace_path, cell_workspace_path
        )

        briefing_path = await self._write_agent_briefing(
            agent_id, task_id, workspace_path
        )

        await self._ensure_agent_image(agent_id)
        mcp_config_path = await self._generate_mcp_config(agent_id, git_context)

        config = AgentConfig(
            agent_id=agent_id,
            blueprint_path=blueprint_path,
            model=model,
            mcp_config_path=mcp_config_path,
            git_context=git_context,
            briefing_path=briefing_path,
            provider_type=route.provider_type.value,
            provider_base_url=route.base_url,
            provider_auth_token=route.auth_token,
        )
        instance = AgentInstance(
            agent_id=agent_id,
            state=AgentState.STARTING,
            config=config,
            current_task_id=task_id,
        )
        self._instances[agent_id] = instance
        return config, instance, agent_settings_path

    async def _launch_spawn(
        self,
        task_id: str | None,
        config: AgentConfig,
        instance: AgentInstance,
        initial_prompt: str | None,
        agent_settings_path: Path | None,
    ) -> AgentInstance:
        """Launch the container and emit spawn audit events.

        `agent_id` was dropped as a redundant parameter — `config.agent_id`
        is the same value and was always the caller's source.
        """
        agent_slug = config.agent_id
        try:
            container_id = await self._spawn_container(
                config, initial_prompt, agent_settings_path
            )
            instance.container_id = container_id
            instance.state = AgentState.ACTIVE
            instance.started_at = datetime.now(UTC)
            instance.last_activity = datetime.now(UTC)

            logger.info(
                "Agent spawned",
                agent_id=agent_slug,
                container_id=container_id[:12],
                model=config.model,
                task_id=task_id,
            )

            self._fire_audit(
                event_type="agent.spawned",
                agent_slug=agent_slug,
                task_id=task_id,
                details={
                    "container_id": container_id[:12],
                    "model": config.model,
                },
            )
            return instance
        except Exception as e:
            instance.state = AgentState.OFFLINE
            instance.error_count += 1
            logger.error(
                "Failed to spawn agent",
                agent_id=agent_slug,
                error=str(e),
            )
            self._fire_audit(
                event_type="agent.spawn_failed",
                agent_slug=agent_slug,
                task_id=task_id,
                details={"error": str(e)},
                severity="error",
            )
            raise

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

        Raises:
            AgentReadinessError: task is not spawn-ready (missing criteria,
                missing git token, no branch plan, role mismatch). The task
                is auto-blocked before we raise so the dispatcher doesn't
                keep retrying.
        """
        # Pre-flight: refuse to spawn if the task isn't ready. Auto-block
        # on refusal so the dispatcher doesn't keep spinning a container
        # that will immediately fail (wasted image pull + startup tokens).
        readiness_reason = await self._readiness_gate(agent_id, task_id)
        if readiness_reason:
            raise AgentReadinessError(
                f"spawn refused for {agent_id} (task={task_id}): {readiness_reason}"
            )

        # Auto-derive git_context when the caller didn't supply one. Two
        # paths:
        #   (a) task_id present  → look up the task's project;
        #   (b) no task_id       → fall back to the sole active project (or
        #                          the first one if there are multiple).
        # Without (b), no-task spawns (e.g. idle PM bootstrapping) hit the
        # "workspace fallback used" path and get mounted at
        # /data/workspaces/default/... which doesn't exist.
        git_context = await self._resolve_spawn_git_context(git_context, task_id)

        async with self._lock:
            existing = self._existing_running_instance(agent_id)
            if existing is not None:
                return existing
            config, instance, agent_settings_path = await self._prepare_agent_spawn(
                agent_id, task_id, model, git_context
            )
        # Record the task as handled so later dispatchers in the same
        # tick don't act on it again. Safe even if _launch_spawn fails
        # — the next tick starts fresh.
        self._mark_task_handled(task_id)
        return await self._launch_spawn(
            task_id,
            config,
            instance,
            initial_prompt,
            agent_settings_path,
        )

    def _resolve_host_paths(
        self, config: AgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        """Compute host mount paths for both containerized and host runtime."""
        mcp_name = config.mcp_config_path.name if config.mcp_config_path else ""
        if PROJECT_HOST_PATH:
            return {
                "blueprints": f"{PROJECT_HOST_PATH}/agents/blueprints",
                "docs": f"{PROJECT_HOST_PATH}/docs",
                "workspaces": f"{DATA_HOST_PATH}/workspaces",
                "claude": CLAUDE_AUTH_HOST_PATH,
                "mcp_config": f"{DATA_HOST_PATH}/mcp-configs/{mcp_name}",
                "prompt": (
                    f"{DATA_HOST_PATH}/prompts-generated/{config.agent_id}-prompt.md"
                ),
                "settings": (
                    f"{DATA_HOST_PATH}/agent-settings/{config.agent_id}-settings.json"
                    if agent_settings_path
                    else None
                ),
                "briefing": (
                    f"{DATA_HOST_PATH}/briefings/{config.agent_id}.md"
                    if config.briefing_path
                    else None
                ),
            }
        return {
            "blueprints": str(self.blueprints_dir.absolute()),
            "docs": str(self.blueprints_dir.parent / "docs"),
            "workspaces": str(Path(settings.workspaces_root)),
            "claude": CLAUDE_AUTH_HOST_PATH,
            "mcp_config": str(config.mcp_config_path),
            "prompt": str(
                Path(tempfile.gettempdir())
                / "roboco-prompts"
                / f"{config.agent_id}-prompt.md"
            ),
            "settings": str(agent_settings_path) if agent_settings_path else None,
            "briefing": (str(config.briefing_path) if config.briefing_path else None),
        }

    @staticmethod
    def _build_mount_args(
        container_name: str, config: AgentConfig, hosts: dict[str, str | None]
    ) -> list[str]:
        """Compose `docker run -v/-e` mount + env args for the agent."""
        cmd: list[str] = [
            "docker",
            "run",
            "-d",
            "--name",
            container_name,
            "--network",
            AGENT_NETWORK,
            # Mount Claude auth directory (for API keys, etc.)
            "-v",
            f"{hosts['claude']}:/home/agent/.claude",
        ]

        settings_host = hosts.get("settings")
        if settings_host:
            cmd.extend(["-v", f"{settings_host}:/home/agent/.claude/settings.json:ro"])

        briefing_host = hosts.get("briefing")
        if briefing_host:
            cmd.extend(["-v", f"{briefing_host}:/app/briefing.md:ro"])

        docs_ro = "" if config.agent_id in ALL_DOCS else ":ro"
        role = get_agent_role(config.agent_id) or "developer"
        cmd.extend(
            [
                "-v",
                f"{hosts['prompt']}:/app/system-prompt.md:ro",
                "-v",
                f"{hosts['blueprints']}:/app/agents/blueprints:ro",
                "-v",
                f"{hosts['docs']}:/app/docs{docs_ro}",
                "-v",
                f"{hosts['workspaces']}:/data/workspaces",
                "-v",
                f"{hosts['mcp_config']}:/app/mcp-config.json:ro",
                "-e",
                f"ROBOCO_AGENT_ID={config.agent_id}",
                "-e",
                f"ROBOCO_AGENT_ROLE={role}",
                "-e",
                "ROBOCO_API_URL=http://roboco-orchestrator:8000",
                "-e",
                "ROBOCO_SDK_PORT=9000",
                "-e",
                "ROBOCO_SDK_URL=http://localhost:9000",
                "-e",
                f"ROBOCO_AGENT_TOOL_CALL_WARN={settings.agent_tool_call_warn}",
                "-e",
                f"ROBOCO_AGENT_TOOL_CALL_HALT={settings.agent_tool_call_halt}",
                "-e",
                f"ROBOCO_AGENT_LOOP_THRESHOLD={settings.agent_loop_threshold}",
                "-e",
                f"ROBOCO_AGENT_LOOP_WINDOW={settings.agent_loop_window}",
                "-e",
                f"ROBOCO_AGENT_STOP_ATTEMPT_ALLOWANCE={settings.agent_stop_attempt_allowance}",
            ]
        )
        # Provider routing: only inject ANTHROPIC_* env vars when the
        # resolved provider is non-Anthropic (i.e. Ollama Cloud). For the
        # Anthropic default path both fields are None and Claude Code
        # inside the container continues to use its mounted ~/.claude
        # credentials — preserving legacy behaviour byte-for-byte.
        if config.provider_base_url:
            cmd.extend(["-e", f"ANTHROPIC_BASE_URL={config.provider_base_url}"])
        if config.provider_auth_token:
            cmd.extend(["-e", f"ANTHROPIC_AUTH_TOKEN={config.provider_auth_token}"])
        # Subagent model override: Claude Code's Task (Agent) tool defaults to
        # claude-haiku-4-5-20251001 regardless of the parent's --model flag.
        # When the parent runs on a non-Anthropic provider (e.g. Ollama Cloud),
        # that default model is unreachable and subagent dispatch fails.
        # CLAUDE_CODE_SUBAGENT_MODEL is a Claude Code env var (verified in
        # v2.1.123 binary) that short-circuits the default selection so the
        # spawned sub-task uses the same model as the parent agent.
        subagent_model = _resolve_agent_cli_model(config.provider_type, config.model)
        cmd.extend(["-e", f"CLAUDE_CODE_SUBAGENT_MODEL={subagent_model}"])
        # Spawn manifest + gateway flag — developer role only in Phase 1.
        # _build_manifest_for_agent writes the JSON file to the host and
        # returns the path; other roles get None and the gateway flag stays off.
        manifest_host_path = _build_manifest_for_agent(config.agent_id, subagent_model)
        if manifest_host_path:
            cmd.extend(
                [
                    "-v",
                    f"{manifest_host_path}:/app/tool-manifest.json:ro",
                    "-e",
                    "ROBOCO_GATEWAY_ENABLED=true",
                    "-e",
                    "ROBOCO_TOOL_MANIFEST_PATH=/app/tool-manifest.json",
                ]
            )
        else:
            cmd.extend(["-e", "ROBOCO_GATEWAY_ENABLED=false"])
        return cmd

    @staticmethod
    def _append_agent_auth_env(cmd: list[str], config: AgentConfig) -> None:
        """Append agent HMAC token env var to the docker run cmd."""
        # Agent HMAC auth token — bound to (agent_id, role, team). The
        # API middleware refuses requests whose headers don't match the
        # token, which stops one agent on the Docker network from
        # spoofing another agent's role. Token is stable per agent as
        # long as the secret doesn't rotate, so it's fine to compute at
        # spawn time and inject once.
        from roboco.agents_config import (
            get_agent_role as _get_role,
        )
        from roboco.agents_config import (
            get_agent_team as _get_team,
        )
        from roboco.agents_config import (
            issue_agent_token,
        )

        _role = _get_role(config.agent_id)
        _team = _get_team(config.agent_id) or ""
        _token = issue_agent_token(config.agent_id, _role, _team)
        cmd.extend(["-e", f"ROBOCO_AGENT_TOKEN={_token}"])

    @staticmethod
    def _append_git_context_env(cmd: list[str], config: AgentConfig) -> None:
        """Append git-context env vars to the docker run cmd."""
        if not config.git_context:
            return
        if config.git_context.project_slug:
            cmd.extend(["-e", f"ROBOCO_PROJECT_SLUG={config.git_context.project_slug}"])
        if config.git_context.branch_name:
            cmd.extend(["-e", f"ROBOCO_BRANCH={config.git_context.branch_name}"])

    @staticmethod
    def _default_spawn_prompt() -> str:
        """Fallback prompt when the caller provided none."""
        return (
            "You may have been spawned without a specific task assignment. "
            "Follow your standard workflow:\n\n"
            "1. Call `roboco_task_scan()` to find work for your role\n"
            "2. If tasks found, claim with `roboco_task_claim(task_id)` "
            "and begin: UNDERSTAND -> PLAN -> EXECUTE -> VERIFY -> HANDOFF\n"
            "3. If no tasks available, call `roboco_agent_idle()` "
            "to shutdown gracefully\n\n"
            "Start now by scanning for work."
        )

    @classmethod
    def _append_image_and_claude_args(
        cls, cmd: list[str], config: AgentConfig, initial_prompt: str | None
    ) -> None:
        """Append the image + Claude Code CLI args to the docker run cmd.

        `--tools` explicitly enumerates the built-in tools loaded at session
        start. Without it, Claude CLI's default behavior leaves Edit/Write
        in the deferred pool, so an agent that doesn't reliably call
        ToolSearch (e.g. weaker non-Anthropic models routed via
        Ollama-cloud) ends up unable to modify any file. The set below is
        the minimum every agent role needs:
          - Read/Write/Edit  : file IO inside the workspace
          - Bash             : shell commands (gated by bash-guard hook)
          - Grep/Glob        : code navigation
          - Task             : sub-agent dispatch (used for ToolSearch and
                               other delegated jobs)
          - TodoWrite        : per-session planning
        Permissions still gate *which* paths Edit/Write can touch (see
        `_get_role_permissions`), so this is purely about loading vs
        denying.
        """
        cmd.extend(
            [
                get_agent_image(config.agent_id),
                "--model",
                cls._resolve_cli_model(config),
                "--system-prompt-file",
                "/app/system-prompt.md",
                "--mcp-config",
                "/app/mcp-config.json",
                "--tools",
                "Read,Write,Edit,Bash,Grep,Glob,Task,TodoWrite",
                "--output-format",
                "stream-json",
                "--verbose",
                "-p",
                initial_prompt or cls._default_spawn_prompt(),
            ]
        )

    @staticmethod
    def _resolve_cli_model(config: AgentConfig) -> str:
        """Return the string to pass to `claude --model`."""
        return _resolve_agent_cli_model(config.provider_type, config.model)

    async def _spawn_container(
        self,
        config: AgentConfig,
        initial_prompt: str | None = None,
        agent_settings_path: Path | None = None,
    ) -> str:
        """Spawn a Docker container for the agent.

        Args:
            config: Agent configuration
            initial_prompt: Optional initial prompt for the agent
            agent_settings_path: Path to per-agent Claude settings file
        """
        container_name = f"roboco-agent-{config.agent_id}"
        await self._remove_container(container_name)

        if not config.mcp_config_path:
            raise RuntimeError("MCP config path not set")

        hosts = self._resolve_host_paths(config, agent_settings_path)
        cmd = self._build_mount_args(container_name, config, hosts)
        self._append_agent_auth_env(cmd, config)
        self._append_git_context_env(cmd, config)
        self._append_image_and_claude_args(cmd, config, initial_prompt)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(f"Failed to start container: {stderr.decode()}")

        return stdout.decode().strip()

    async def _remove_container(self, container_name: str) -> None:
        """Remove a container if it exists, dumping its logs to disk first.

        Docker deletes the container's json-file log when we `docker rm`, so
        before removal we copy the current log to /data/logs/agents/{slug}/
        with a timestamp. That gives us persistent history across respawns
        without needing an entrypoint wrapper inside the agent image.
        """
        # Check the container actually exists before trying to dump logs;
        # _remove_container is routinely called pre-spawn to clear stale
        # containers, and on first spawn there's nothing to dump.
        inspect = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "--format={{.Id}}",
            container_name,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        exists = (await inspect.wait()) == 0

        if exists:
            slug = container_name.removeprefix("roboco-agent-")
            log_dir = Path("/data/logs/agents") / slug
            try:
                log_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
                log_path = log_dir / f"{timestamp}.log"
                with log_path.open("wb") as out:
                    dump_proc = await asyncio.create_subprocess_exec(
                        "docker",
                        "logs",
                        container_name,
                        stdout=out,
                        stderr=out,
                    )
                    await dump_proc.wait()
                if log_path.stat().st_size == 0:
                    log_path.unlink(missing_ok=True)
            except Exception as e:
                logger.warning(
                    "Could not dump container logs before removal",
                    container=container_name,
                    error=str(e),
                )

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

        Post-gateway: every state-changing tool routes through one of two
        servers, and read-only views go through two more:

        - roboco-flow         intent verbs (lifecycle transitions)
        - roboco-do           content tools (commit, push, PR, journal,
                              notify, message)
        - roboco-git-readonly status, log, diff, branch list
        - roboco-optimal      knowledge base, RAG, semantic search
        - roboco-docs         documentation file management (panel docs)

        The agent's role is asserted by the orchestrator API on every
        verb/tool call, so all roles get the same MCP surface from this
        registration; verbs the agent's role can't run return a
        not-authorized error rather than 404. Git context is forwarded
        only as a fallback for tools that resolve project/branch from env.
        """
        # MCP servers run inside agent containers, need to connect via Docker network
        if PROJECT_HOST_PATH:
            api_url = "http://roboco-orchestrator:8000"
        else:
            api_url = f"http://127.0.0.1:{settings.port}"

        agent_role = get_agent_role(agent_id) or ""
        # Gateway v2 endpoints declare X-Agent-ID as Annotated[UUID, Header(...)],
        # so the MCP server has to forward the agent's UUID — not the slug — or
        # every gateway call 422s on header parse. Resolve via AGENT_UUIDS map;
        # if the slug isn't in the map (custom agents), fall back to the slug
        # and let the API surface the unknown-agent error.
        agent_uuid = AGENT_UUIDS.get(agent_id, agent_id)

        mcp_env: dict[str, str] = {
            "ROBOCO_API_URL": api_url,
            "ROBOCO_ORCHESTRATOR_URL": api_url,
            "ROBOCO_AGENT_ID": agent_uuid,
            "ROBOCO_AGENT_ROLE": agent_role,
        }

        # Add git context if available
        if git_context:
            if git_context.project_slug:
                mcp_env["ROBOCO_PROJECT_SLUG"] = git_context.project_slug
            if git_context.branch_name:
                mcp_env["ROBOCO_BRANCH"] = git_context.branch_name

        mcp_servers: dict[str, dict[str, Any]] = {
            # Intent verbs — every role-scoped lifecycle transition.
            "roboco-flow": {
                "command": "uv",
                "args": ["run", "python", "-m", "roboco.mcp.flow_server"],
                "env": mcp_env,
            },
            # Content tools — commit, push, PR, journal, notify, message.
            "roboco-do": {
                "command": "uv",
                "args": ["run", "python", "-m", "roboco.mcp.do_server"],
                "env": mcp_env,
            },
            # Read-only git views — status, log, diff, branches.
            "roboco-git-readonly": {
                "command": "uv",
                "args": ["run", "python", "-m", "roboco.mcp.git_readonly"],
                "env": mcp_env,
            },
            # Knowledge base — RAG / semantic search / ask_mentor.
            "roboco-optimal": {
                "command": "uv",
                "args": [
                    "run",
                    "python",
                    "-m",
                    "roboco.mcp.optimal_server",
                    agent_id,
                ],
                "env": mcp_env,
            },
        }

        # Docs server — documentation file management. Registered only for
        # roles that touch panel docs; handlers still enforce per-role
        # access so the surface is fail-closed.
        docs_roles = (
            "documenter",
            "cell_pm",
            "main_pm",
            "product_owner",
            "head_marketing",
        )
        if agent_role in docs_roles:
            mcp_servers["roboco-docs"] = {
                "command": "uv",
                "args": [
                    "run",
                    "python",
                    "-m",
                    "roboco.mcp.docs_server",
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

    async def _readiness_gate(self, agent_id: str, task_id: str | None) -> str | None:
        """Return a reason string if the spawn must be refused, else None.

        Checks run only when a task is being spawned for. No-task spawns
        (idle PM bootstrap, etc.) are always ready. On any refusal that
        represents a persistent problem we auto-block the task so the
        dispatcher stops retrying — the PM sees the block notification.
        """
        if not task_id:
            return None

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                task_or_reason = await self._readiness_fetch_task(client, task_id)
                if isinstance(task_or_reason, str):
                    return task_or_reason
                task = task_or_reason

                persistent = self._readiness_check_task(agent_id, task)
                if persistent is not None:
                    return await self._readiness_block(client, task_id, persistent)

                project_slug = _read_project_slug(task)
                token_reason = await self._readiness_check_git_token(project_slug)
                if token_reason is not None:
                    return await self._readiness_block(client, task_id, token_reason)
        except httpx.HTTPError as e:
            # Transient — retry on next dispatch without auto-blocking.
            return f"readiness check HTTP error: {e}"

        return None

    async def _readiness_fetch_task(
        self, client: httpx.AsyncClient, task_id: str
    ) -> dict[str, Any] | str:
        """Fetch the task or return a reason string.

        404 → "task not found" (caller should auto-block).
        Other non-200s → transient; caller returns the reason verbatim
        without auto-blocking so the dispatcher can retry next tick.
        """
        resp = await client.get(f"{self._api_url}/tasks/{task_id}")
        if resp.status_code == http_status.HTTP_404_NOT_FOUND:
            await self._readiness_block(client, task_id, "task not found")
            return "task not found"
        if resp.status_code != http_status.HTTP_200_OK:
            return f"task-fetch returned {resp.status_code}"
        task = resp.json()
        return task if isinstance(task, dict) else "task payload not an object"

    @staticmethod
    def _readiness_check_task(agent_id: str, task: dict[str, Any]) -> str | None:
        """Return a persistent blocker reason on the task itself, else None."""
        status = task.get("status", "")
        role = get_agent_role(agent_id) or ""

        criteria = task.get("acceptance_criteria") or []
        if isinstance(criteria, str):
            criteria = [criteria] if criteria.strip() else []
        if not criteria:
            return "missing acceptance_criteria"

        if not _read_project_slug(task):
            return "task has no project"

        if status in {"claimed", "in_progress", "verifying"} and not task.get(
            "branch_name"
        ):
            return f"state={status} but branch_name is unset"

        role_mismatch: dict[str, str | set[str]] = {
            "awaiting_qa": "qa",
            "awaiting_documentation": "documenter",
            "awaiting_pm_review": {"cell_pm", "main_pm"},
            "awaiting_ceo_approval": "ceo",
        }
        required = role_mismatch.get(status)
        if required is None:
            return None
        ok = role in required if isinstance(required, set) else role == required
        if ok:
            return None
        return (
            f"state={status} requires role in {required!r} "
            f"but agent {agent_id} is {role!r}"
        )

    @staticmethod
    async def _readiness_check_git_token(project_slug: str | None) -> str | None:
        """Ensure the project has a decryptable git token, else blocker reason."""
        if not project_slug:
            return "task has no project"
        from roboco.db.base import get_session_factory
        from roboco.services.project import get_project_service

        session_factory = get_session_factory()
        async with session_factory() as db:
            project_svc = get_project_service(db)
            try:
                token = await project_svc.get_decrypted_token_by_slug(project_slug)
            except Exception as e:
                return f"project '{project_slug}' git-token decrypt failed: {e}"
        if not token:
            return f"project '{project_slug}' has no git token configured"
        return None

    async def _readiness_block(
        self, client: httpx.AsyncClient, task_id: str, reason: str
    ) -> str:
        """Auto-block the task and return the human-readable reason."""
        await self._auto_block_task(client, task_id, f"readiness: {reason}")
        return reason

    async def _resolve_agent_route(self, agent_id: str) -> "AgentRoute":
        """Resolve (provider, model) for `agent_id` via `ModelRoutingService`.

        Errors are contained: any DB/session failure degrades to a legacy
        Anthropic-default AgentRoute so spawn never stalls on routing.
        """
        from roboco.db.base import get_session_factory
        from roboco.models.base import ModelProvider
        from roboco.models.runtime import MODEL_MAP
        from roboco.services.llm import (
            AgentRoute,
            get_model_routing_service,
        )

        try:
            factory = get_session_factory()
            async with factory() as db:
                router = get_model_routing_service(db)
                return await router.resolve_for_agent(agent_id)
        except Exception as e:  # pragma: no cover
            role = get_agent_role(agent_id) or ""
            short = ROLE_MODEL_MAP.get(role, "sonnet")
            logger.warning(
                "Model routing resolve failed; using legacy Anthropic path",
                agent_id=agent_id,
                error=str(e),
            )
            return AgentRoute(
                provider_id=None,
                provider_type=ModelProvider.ANTHROPIC,
                base_url=None,
                auth_token=None,
                model_name=MODEL_MAP.get(short, short),
            )

    _TOOL_LOAD_CACHE: ClassVar[dict[str, str]] = {}

    def _build_tool_load_block(self, role: str) -> str:
        """Build the mandatory first-action ToolSearch directive.

        Weak models consistently skip the role prompt's `Load on spawn
        (one ToolSearch select: call)` line — then trip on "Edit exists
        but is not enabled in this context" when they try to edit a
        file, or "No such tool available: mcp__roboco-flow__…" when
        they try to act. Hoisting the directive into the briefing's
        first block (with the exact query string inline) pulls the
        bootstrap into the prompt-most-salient position.

        Returns empty string if we can't locate the role file — the
        briefing still works without it.
        """
        if role in self._TOOL_LOAD_CACHE:
            return self._TOOL_LOAD_CACHE[role]
        block = self._read_tool_load_from_role_prompt(role)
        self._TOOL_LOAD_CACHE[role] = block
        return block

    def _read_tool_load_from_role_prompt(self, role: str) -> str:
        """Parse the `Load on spawn` line out of the role prompt."""
        role_file = self.project_root / "agents" / "prompts" / "roles" / f"{role}.md"
        if not role_file.exists():
            return ""
        try:
            text = role_file.read_text()
        except OSError:
            return ""
        marker = "## Load on spawn"
        idx = text.find(marker)
        if idx < 0:
            return ""
        # After the marker, the next line starts with a backtick-quoted list.
        tail = text[idx + len(marker) :]
        tick_start = tail.find("`")
        tick_end = tail.find("`", tick_start + 1)
        if tick_start < 0 or tick_end < 0:
            return ""
        tool_list = tail[tick_start + 1 : tick_end].strip()
        if not tool_list:
            return ""
        return (
            "## First action required\n"
            "Before any other tool call, run ToolSearch to enable the tools\n"
            "your role needs. Copy this verbatim as your first action:\n"
            "\n"
            f'```\nToolSearch(query="select:{tool_list}")\n```\n'
            "\n"
            "Skipping this step results in 'tool exists but is not enabled\n"
            "in this context' errors that waste tool-call budget.\n"
            "\n"
        )

    async def _write_agent_briefing(
        self,
        agent_id: str,
        task_id: str | None,
        workspace_path: str,
    ) -> Path | None:
        """Write a compact task briefing to be read by SessionStart hook.

        The briefing saves the agent from burning its first 2-3 tool calls on
        `roboco_task_scan` + `roboco_task_get`. If `task_id` is known we fetch
        the task and include title, status, branch, and acceptance criteria.
        On fetch failure we still emit the role-level part (role, escalation
        target, terminal tools, workspace path) — strictly better than nothing.
        """
        role = get_agent_role(agent_id) or "agent"
        team = get_agent_team(agent_id) or "-"
        escalate_to = get_escalation_target(agent_id) or "main-pm"

        tool_load_block = self._build_tool_load_block(role)
        task_block = ""
        if task_id:
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(f"{self._api_url}/tasks/{task_id}")
                if resp.status_code == http_status.HTTP_200_OK:
                    task = resp.json()
                    criteria_list = task.get("acceptance_criteria") or []
                    if isinstance(criteria_list, str):
                        criteria_list = [criteria_list]
                    criteria = (
                        "\n".join(f"- {c}" for c in criteria_list)
                        if criteria_list
                        else "- (none listed — ask PM before proceeding)"
                    )
                    branch = task.get("branch_name") or "(to be created)"
                    project_slug = task.get("project_slug") or "(unset — ask PM)"
                    task_block = (
                        "\n## Current task\n"
                        f"- **ID:** `{task.get('id', task_id)}`\n"
                        f"- **Title:** {task.get('title', '(untitled)')}\n"
                        f"- **Status:** {task.get('status', 'unknown')}\n"
                        f"- **Type:** {task.get('task_type', 'unknown')}\n"
                        f"- **Project slug:** `{project_slug}` "
                        "(pass this as `project_slug=` on every git/task tool)\n"
                        f"- **Branch:** `{branch}`\n"
                        "\n### Acceptance criteria\n"
                        f"{criteria}\n"
                    )
            except Exception as e:
                logger.debug(
                    "Briefing task-fetch failed — falling back to role-only",
                    agent_id=agent_id,
                    task_id=task_id,
                    error=str(e),
                )

        content = (
            f"# Session briefing — {agent_id}\n"
            "\n"
            f"{tool_load_block}"
            "## You are\n"
            f"- **Agent:** `{agent_id}`\n"
            f"- **Role:** {role}\n"
            f"- **Team:** {team}\n"
            f"- **Escalate to:** `{escalate_to}`\n"
            f"- **Workspace:** `{workspace_path}`\n"
            f"{task_block}"
            "\n## Terminal tools (how to exit cleanly)\n"
            "- `roboco_agent_idle()` — no work remaining\n"
            "- `roboco_task_substitute(reason=...)` — release the task\n"
            "- `roboco_task_escalate(reason=...)` — escalate up the chain\n"
            "- `roboco_task_pause(checkpoint=...)` — save progress, resume later\n"
            "- Role handoffs: `roboco_task_submit_qa()`, `_qa_pass/fail()`, "
            "`_docs_complete()`, `_complete()`\n"
            "\n"
            "A Stop without a terminal tool will be rejected; a second Stop\n"
            "auto-substitutes with `reason='stopped_without_transition'`.\n"
            "\n"
            "## Budget\n"
            f"Soft-warn at {settings.agent_tool_call_warn} tool calls, "
            f"hard cap at {settings.agent_tool_call_halt}. Loops — same "
            f"tool+args {settings.agent_loop_threshold}x within "
            f"{settings.agent_loop_window} calls — are flagged; stop and "
            "escalate instead of retrying.\n"
        )

        if PROJECT_HOST_PATH:
            briefings_dir = Path("/app/briefings")
        else:
            briefings_dir = Path(tempfile.gettempdir()) / "roboco-briefings"
        briefings_dir.mkdir(parents=True, exist_ok=True)

        path = briefings_dir / f"{agent_id}.md"
        path.write_text(content)
        logger.debug(
            "Wrote agent briefing",
            agent_id=agent_id,
            path=str(path),
            has_task=bool(task_block),
        )
        return path

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

    def _mark_task_handled(self, task_id: str | None) -> None:
        """Record that `task_id` was acted on earlier in this dispatch tick."""
        if task_id:
            self._tick_handled_tasks.add(task_id)

    def _is_task_handled_this_tick(self, task_id: str | None) -> bool:
        """True if a prior dispatcher already handled this task this tick."""
        return bool(task_id and task_id in self._tick_handled_tasks)

    def _is_parallel_phase_claim(
        self, task: dict[str, Any], dev_uuid: str | None
    ) -> bool:
        """True if a `claimed` task is actually in the doc/PR parallel phase.

        The `original_developer:` quick_context marker is set pre-QA by
        `submit_for_qa`, so it alone cannot distinguish a QA-claimed
        awaiting_qa task (wrong) from a doc-claimed awaiting_documentation
        task (right). Require the claimant to be a documenter.
        """
        if not dev_uuid:
            return False
        claimed_by = task.get("claimed_by")
        if not claimed_by:
            return False
        claimed_slug = self._resolve_agent_slug(claimed_by)
        return bool(claimed_slug) and "doc" in claimed_slug

    async def _respawn_dev_for_pr_half(
        self, task: dict[str, Any], dev_uuid: str | None
    ) -> None:
        """Respawn the original developer if they still owe the PR half.

        `pr_number` is set by the PR-create handler as soon as GitHub
        confirms the PR, even if the status-gated `pr_created` flag never
        flips — without that second check we'd respawn the dev forever
        after they've already created the PR (the handler refuses to set
        pr_created=True when the doc's claim moved status out of
        awaiting_documentation).
        """
        if not dev_uuid or task.get("pr_created") or task.get("pr_number"):
            return
        dev_slug = self._resolve_agent_slug(dev_uuid)
        if not dev_slug or self._is_agent_active(dev_slug):
            return
        await self.spawn_agent(
            agent_id=dev_slug,
            task_id=task["id"],
            initial_prompt=self._build_dev_prompt(task),
            git_context=self._task_git_context(task),
        )

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
        The record is mirrored to `waiting_records` in Postgres so a later
        orchestrator restart can still resolve the wait.
        """
        record = WaitingRecord(
            agent_id=agent_id,
            task_id=task_id,
            waiting_for=waiting_for,
            waiting_since=datetime.now(UTC),
            context=context or {},
        )

        self._waiting_records[agent_id] = record
        await self._persist_waiting_record(record)

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

    async def _persist_waiting_record(self, record: WaitingRecord) -> None:
        """Upsert a WaitingRecord into the waiting_records table."""
        try:
            from uuid import UUID as _UUID

            from sqlalchemy import delete

            from roboco.db.base import get_session_factory
            from roboco.db.tables import WaitingRecordTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                # One record per agent; delete prior then insert.
                await db.execute(
                    delete(WaitingRecordTable).where(
                        WaitingRecordTable.agent_id == record.agent_id
                    )
                )
                row = WaitingRecordTable(
                    agent_id=record.agent_id,
                    task_id=(_UUID(record.task_id) if record.task_id else None),
                    waiting_for=record.waiting_for,
                    waiting_since=record.waiting_since,
                    context=record.context,
                )
                db.add(row)
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to persist waiting record",
                agent_id=record.agent_id,
                error=str(e),
            )

    async def _delete_waiting_record(self, agent_id: str) -> None:
        """Delete a persisted waiting record when its wait resolves."""
        try:
            from sqlalchemy import delete

            from roboco.db.base import get_session_factory
            from roboco.db.tables import WaitingRecordTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                await db.execute(
                    delete(WaitingRecordTable).where(
                        WaitingRecordTable.agent_id == agent_id
                    )
                )
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to delete waiting record",
                agent_id=agent_id,
                error=str(e),
            )

    async def restore_waiting_records(self) -> int:
        """Load persisted waiting records into memory on orchestrator start.

        Call this from `start()` so agents marked WAITING_LONG before the
        previous orchestrator exited can still be resolved.
        """
        try:
            from sqlalchemy import select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import WaitingRecordTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                rows = await db.execute(select(WaitingRecordTable))
                count = 0
                for row in rows.scalars().all():
                    self._waiting_records[row.agent_id] = WaitingRecord(
                        agent_id=row.agent_id,
                        task_id=str(row.task_id) if row.task_id else None,
                        waiting_for=row.waiting_for,
                        waiting_since=row.waiting_since,
                        context=dict(row.context or {}),
                    )
                    count += 1
                if count:
                    logger.info(
                        "Restored waiting records from database",
                        count=count,
                    )
                return count
        except Exception as e:
            logger.error("Failed to restore waiting records", error=str(e))
            return 0

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
        await self._delete_waiting_record(agent_id)

        # Generate resume prompt
        resume_prompt = self._generate_resume_prompt(record, resolution)

        # Preserve the original git_context from the prior instance so the
        # respawned agent keeps the same workspace mount path.
        prior = self._instances.get(agent_id)
        prior_git_context = prior.config.git_context if prior and prior.config else None

        # Respawn
        return await self.spawn_agent(
            agent_id=agent_id,
            initial_prompt=resume_prompt,
            task_id=record.task_id,
            git_context=prior_git_context,
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

    async def _sweeper_loop(self) -> None:
        """Background sweeper for session timeouts and stale notifications.

        Addresses two silent-failure surfaces:
        - SessionTable.timeout_seconds / max_time_window were never enforced;
          sessions stayed ACTIVE forever.
        - NotificationTable.expires_at existed but no job ever acted on it.

        Runs on its own interval so a slow sweep can't delay agent dispatch.
        """
        sweep_interval = 60  # seconds
        while self._running:
            try:
                await asyncio.sleep(sweep_interval)
                await self._run_sweep()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Sweeper loop error", error=str(e))

    async def _run_sweep(self) -> None:
        """Run one pass of session + notification sweepers."""
        from roboco.db.base import get_session_factory
        from roboco.services.messaging import get_messaging_service
        from roboco.services.notification_delivery import (
            get_notification_delivery_service,
        )

        session_factory = get_session_factory()
        async with session_factory() as db:
            msg_svc = get_messaging_service(db)
            try:
                closed = await msg_svc.sweep_timed_out_sessions()
                if closed:
                    await db.commit()
            except Exception as e:
                await db.rollback()
                logger.warning("Session sweep failed", error=str(e))

            deliv_svc = get_notification_delivery_service(db)
            try:
                expired = await deliv_svc.sweep_expired_notifications()
                if expired:
                    await db.commit()
            except Exception as e:
                await db.rollback()
                logger.warning("Notification sweep failed", error=str(e))

        # Budget kill-switch — runs every sweep. Any agent whose SDK reports
        # halt=true has breached its per-session tool-call cap; terminate the
        # container so the next dispatcher tick doesn't waste tokens on the
        # same session.
        await self._sweep_budget_exceeded()

    async def _sweep_budget_exceeded(self) -> None:
        """Stop agents whose per-session SDK budget reports halt=true.

        Each agent's SDK server is reachable at
        `http://roboco-agent-{agent_id}:9000/budget/status` on the shared
        agent network. A budget-exceeded agent gets a forced stop with a
        `budget_exceeded` reason; the task is already being auto-substituted
        by the post-tool hook on the agent side.
        """
        if not self._instances:
            return
        async with httpx.AsyncClient(timeout=3.0) as client:
            for agent_id, instance in list(self._instances.items()):
                if instance.state not in (
                    AgentState.ACTIVE,
                    AgentState.WAITING_SHORT,
                ):
                    continue
                url = f"http://roboco-agent-{agent_id}:9000/budget/status"
                try:
                    resp = await client.get(url)
                    if resp.status_code != http_status.HTTP_200_OK:
                        continue
                    data = resp.json()
                except Exception:
                    # SDK unreachable / not yet started / container gone —
                    # either benign or covered by health loop.
                    continue
                if not data.get("halt"):
                    continue
                logger.warning(
                    "Agent budget exceeded; terminating container",
                    agent_id=agent_id,
                    total_calls=data.get("total"),
                    halt_threshold=data.get("halt_threshold"),
                )
                try:
                    await self.stop_agent(agent_id, graceful=True)
                except Exception as e:
                    logger.warning(
                        "Failed to stop budget-exceeded agent",
                        agent_id=agent_id,
                        error=str(e),
                    )

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
                        git_context=(
                            instance.config.git_context if instance.config else None
                        ),
                    )
                elif instance.error_count == max_retries:
                    # Exactly at the threshold — escalate once to humans so a
                    # stranded agent doesn't die silently. Subsequent crashes
                    # stay quiet to avoid notification spam.
                    logger.error(
                        "Agent exceeded max restart attempts; escalating",
                        agent_id=agent_id,
                        error_count=instance.error_count,
                        task_id=instance.current_task_id,
                    )
                    await self._notify_agent_stranded(
                        agent_id=agent_id,
                        error_count=instance.error_count,
                        task_id=instance.current_task_id,
                    )

    async def _notify_agent_stranded(
        self,
        agent_id: str,
        error_count: int,
        task_id: str | None,
    ) -> None:
        """Create a notification for humans when an agent can't be restarted.

        Posts a high-priority notification addressed to the auditor and CEO.
        Fire-and-forget: the agent is already dead; don't let our own failure
        stop the health loop.
        """
        try:
            from sqlalchemy import select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentTable, NotificationTable
            from roboco.models.base import (
                AgentRole,
                NotificationPriority,
                NotificationType,
            )
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )
            from roboco.utils.converters import require_uuid

            session_factory = get_session_factory()
            async with session_factory() as db:
                orch_agent = await db.execute(
                    select(AgentTable).where(AgentTable.role == AgentRole.AUDITOR)
                )
                auditor = orch_agent.scalar_one_or_none()
                ceo_result = await db.execute(
                    select(AgentTable).where(AgentTable.role == AgentRole.CEO)
                )
                ceo = ceo_result.scalar_one_or_none()
                recipients = [a.id for a in (auditor, ceo) if a is not None]
                if not recipients:
                    logger.warning(
                        "No auditor/ceo found for stranded-agent notification",
                        agent_id=agent_id,
                    )
                    return
                from_agent = auditor.id if auditor else ceo.id  # type: ignore[union-attr]
                notification = NotificationTable(
                    type=NotificationType.ALERT,
                    priority=NotificationPriority.HIGH,
                    from_agent=from_agent,
                    to_agents=recipients,
                    subject=f"Agent stranded: {agent_id}",
                    body=(
                        f"Agent '{agent_id}' exceeded max restart attempts "
                        f"({error_count}) and will not auto-recover. "
                        f"Task: {task_id or 'none'}. Manual intervention needed."
                    ),
                    requires_ack=True,
                )
                db.add(notification)
                await db.flush()
                delivery = get_notification_delivery_service(db)
                await delivery.deliver(require_uuid(notification.id))
                await db.commit()
        except Exception as e:
            logger.error(
                "Failed to send stranded-agent notification",
                agent_id=agent_id,
                error=str(e),
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

    async def _check_parent_branch_ready(
        self, client: httpx.AsyncClient, task_id: str, parent_id: str
    ) -> str | None:
        """Verify the parent task has a branch; auto-block + return msg if not."""
        parent_resp = await client.get(f"{self._api_url}/tasks/{parent_id}")
        if not parent_resp.is_success:
            return None
        parent = parent_resp.json()
        if parent.get("branch_name"):
            return None
        await self._auto_block_task(
            client,
            task_id,
            "Parent task must be claimed first to create its branch",
        )
        return f"Task {task_id} waiting for parent branch"

    async def _check_dev_needs_subtasks(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> str | None:
        """Block non-trivial root tasks routed to a dev without subtasks."""
        complexity = task.get("estimated_complexity", "low")
        parent_task_id = task.get("parent_task_id")
        if complexity not in ("medium", "high", "critical") or parent_task_id:
            return None
        task_id = task.get("id")
        try:
            resp = await client.get(f"{self._api_url}/tasks/{task_id}/subtasks")
            subtasks = resp.json() if resp.is_success else []
        except Exception:
            subtasks = []
        if subtasks:
            return None
        await self._auto_block_task(
            client,
            str(task_id),
            f"Task complexity is {complexity} but no subtasks. "
            "Cell PM must break down work first.",
        )
        return (
            f"Task {task_id} is {complexity} complexity "
            "without subtasks - Cell PM must break it down"
        )

    async def _validate_task_for_spawn(
        self,
        client: httpx.AsyncClient,
        task: dict,
        agent_slug: str,
    ) -> str | None:
        """
        Validate task is ready for agent spawn.

        Returns None if valid, or error message if task cannot proceed.
        This prevents spawning agents on tasks that are missing prerequisites.
        """
        from roboco.agents_config import get_agent_role

        task_id = task.get("id")
        if not task_id:
            return "Task missing ID"
        min_description_len = 10

        description = (task.get("description") or "").strip()
        if len(description) < min_description_len:
            return (
                f"Task {task_id} has inadequate description ({len(description)} chars)"
            )

        if not task.get("project_id"):
            await self._auto_block_task(client, task_id, "Task needs project_id")
            return f"Task {task_id} needs project"

        parent_id = task.get("parent_task_id")
        if parent_id:
            err = await self._check_parent_branch_ready(client, task_id, parent_id)
            if err:
                return err

        logger.info("Task ready for hierarchical branch creation", task_id=task_id)

        if get_agent_role(agent_slug) == "developer":
            err = await self._check_dev_needs_subtasks(client, task)
            if err:
                return err

        return None  # All validations passed

    async def _auto_block_task(
        self, client: httpx.AsyncClient, task_id: str, reason: str
    ) -> None:
        """Auto-block a task that cannot proceed due to missing prerequisites."""
        try:
            await client.patch(
                f"{self._api_url}/tasks/{task_id}",
                json={
                    "status": "blocked",
                    "dev_notes": f"[AUTO-BLOCKED] {reason}",
                },
            )
            logger.info(
                "Auto-blocked task with missing prerequisites",
                task_id=task_id,
                reason=reason,
            )
        except Exception as e:
            logger.error(
                "Failed to auto-block task",
                task_id=task_id,
                error=str(e),
            )

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
            "type_filter": notification_type,
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

    @staticmethod
    def _route_by_task_type(task_type: str, team: str | None) -> str | None:
        """Route based on task_type field alone; returns None if no match."""
        cell_teams = ("backend", "frontend", "ux_ui")
        if task_type in ("planning", "research", "administrative"):
            return "cell_pm" if team in cell_teams else "main_pm"
        if task_type == "design" and team not in ("backend", "frontend"):
            return "cell_pm"
        return None

    def _classify_code_task(self, task: dict[str, Any]) -> str:
        """Classify a generic `code` task via keyword/complexity heuristics."""
        team = task.get("team")
        title = (task.get("title") or "").lower()
        description = (task.get("description") or "").lower()
        text = f"{title} {description}"
        complexity = task.get("estimated_complexity", "medium").lower()

        if self._has_board_keywords(text):
            return "board"

        if (
            self._has_cross_cell_keywords(text)
            or complexity in ("high", "critical")
            or not team
            or team == "all"
        ):
            return "main_pm"

        if self._has_pm_keywords(text) or complexity == "medium":
            return "cell_pm"

        return "dev"

    def _classify_task_routing(self, task: dict[str, Any]) -> str:
        """
        Classify a task for routing based on task_type, team, complexity, and keywords.

        Returns one of: "board", "main_pm", "cell_pm", "dev", "marketing"
        """
        team = task.get("team")
        task_type = task.get("task_type", "code")

        # Task type takes precedence for non-code work
        by_type = self._route_by_task_type(task_type, team)
        if by_type:
            return by_type
        if team in self._TEAM_ROUTING_MAP:
            return self._TEAM_ROUTING_MAP[team]

        return self._classify_code_task(task)

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

NEVER assign to be-dev-1, fe-dev-1, ux-dev-1, ux-dev-2 directly. ONLY to Cell PMs.

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

    def trigger_dispatch(self) -> None:
        """Wake the dispatcher up immediately for a single pass.

        Called by API routes right after a task status transition so the
        orchestrator reacts in milliseconds — e.g. a PM creates a subtask
        and the assignee spawns within a second instead of after the next
        30-second poll. Safe to call multiple times; the Event coalesces.
        """
        self._dispatch_wake.set()

    async def _dispatcher_loop(self) -> None:
        """
        Main dispatcher loop - periodically checks for work and spawns agents.

        This is the BRAIN of the orchestrator. It:
        1. Queries for tasks needing work (pending, awaiting_qa, etc.)
        2. Queries for events needing attention (blockers, escalations)
        3. Spawns appropriate agents with task assignments

        Hybrid timing: a poll (dispatcher_interval) guarantees progress even
        without external signals, while `_dispatch_wake` lets API routes
        kick the loop for immediate reactions after status transitions.
        """
        while self._running:
            try:
                # Wait either for an explicit wake signal or the poll timeout,
                # whichever comes first. asyncio.wait_for re-raises
                # TimeoutError when the poll window expires, which we treat
                # as "run dispatch anyway".
                import contextlib

                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._dispatch_wake.wait(),
                        timeout=self.dispatcher_interval,
                    )
                self._dispatch_wake.clear()
                await self._dispatch_all_work()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Dispatcher loop error", error=str(e))

    async def _dispatch_all_work(self) -> None:
        """Run all dispatchers to check for and assign work.

        Each dispatcher is isolated: if one raises (e.g., a transient API
        error), the rest still run in this tick instead of waiting for the
        next one.

        `_tick_handled_tasks` gives downstream dispatchers a way to
        skip tasks that an earlier dispatcher already acted on this
        tick. Order-dependent bugs (like the Fix-B scenario where
        `_dispatch_qa_work` claimed for QA and the next dispatcher
        re-spawned the dev on the same claimed row) are defanged by
        early dispatchers marking the task handled.
        """
        self._tick_handled_tasks = set()
        # Orchestrator uses SYSTEM role for internal API calls
        # Using a well-known UUID for the orchestrator identity
        headers = {
            "X-Agent-ID": "00000000-0000-0000-0000-000000000000",
            "X-Agent-Role": "system",
        }
        dispatchers: list[tuple[str, Any]] = []
        async with httpx.AsyncClient(timeout=30.0, headers=headers) as client:
            dispatchers = [
                ("pm_work", self._dispatch_pm_work(client)),
                ("pm_closure_work", self._dispatch_pm_closure_work(client)),
                ("dev_work", self._dispatch_dev_work(client)),
                ("qa_work", self._dispatch_qa_work(client)),
                ("doc_work", self._dispatch_doc_work(client)),
                ("pm_review_work", self._dispatch_pm_review_work(client)),
                ("marketing_work", self._dispatch_marketing_work(client)),
                ("blocker_work", self._dispatch_blocker_work(client)),
                ("escalation_work", self._dispatch_escalation_work(client)),
                ("approval_work", self._dispatch_approval_work(client)),
                ("a2a_work", self._dispatch_a2a_work(client)),
                ("audit_work", self._dispatch_audit_work(client)),
                ("detect_stuck_tasks", self._detect_stuck_tasks(client)),
            ]
            for name, coro in dispatchers:
                try:
                    await coro
                except Exception as e:
                    logger.error(
                        "Dispatcher raised; continuing with next dispatcher",
                        dispatcher=name,
                        error=str(e),
                    )

    # =========================================================================
    # SMART DISPATCHER - TASK-BASED DISPATCHERS
    # =========================================================================

    _PM_AGENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "main-pm",
            "be-pm",
            "fe-pm",
            "ux-pm",
        }
    )

    _PM_RESPAWN_MAX_UNPRODUCTIVE = 3

    def _pm_respawn_should_gate(self, agent_slug: str, task: dict[str, Any]) -> bool:
        """Return True when the respawn should be skipped (loop detected).

        Tracks (agent_slug, task_id) -> count of consecutive spawns where
        the task's status did not advance. When the task status changes,
        the counter resets. Once the count hits the threshold, the spawn
        is skipped and a warning logged; operators must intervene.
        """
        task_id = task.get("id")
        if not task_id:
            return False
        key = (agent_slug, task_id)
        current_status = task.get("status")
        record = self._pm_respawn_tracker.get(key)
        if record is None or record.get("last_status") != current_status:
            self._pm_respawn_tracker[key] = {
                "count": 1,
                "last_status": current_status,
            }
            return False
        record["count"] += 1
        if record["count"] > self._PM_RESPAWN_MAX_UNPRODUCTIVE:
            logger.warning(
                "PM respawn loop detected — skipping spawn",
                agent_id=agent_slug,
                task_id=task_id,
                task_status=current_status,
                spawn_attempts=record["count"],
                threshold=self._PM_RESPAWN_MAX_UNPRODUCTIVE,
                hint=(
                    "Agent repeatedly spawned without advancing task state. "
                    "Investigate prompt/schema drift or escalate manually."
                ),
            )
            return True
        return False

    async def _handle_pm_assigned_task(
        self, task: dict[str, Any], assigned_to: str
    ) -> None:
        """Spawn an already-assigned PM agent if it isn't running."""
        agent_slug = self._resolve_agent_slug(assigned_to)
        if agent_slug not in self._PM_AGENTS or self._is_agent_active(agent_slug):
            return
        if self._pm_respawn_should_gate(agent_slug, task):
            return
        logger.info(
            "Spawning assigned PM agent",
            task_id=task.get("id"),
            agent_id=agent_slug,
        )
        pm_prompt = (
            self._build_main_pm_triage_prompt(task)
            if agent_slug == "main-pm"
            else self._build_pm_triage_prompt(task)
        )
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=pm_prompt,
            git_context=self._task_git_context(task),
        )

    def _pm_spawn_prompt(
        self, routing: str, agent_id: str, task: dict[str, Any]
    ) -> str:
        """Pick the correct prompt for a classified spawn."""
        if routing == "dev":
            return self._build_dev_prompt(task)
        if routing == "main_pm" or agent_id == "main-pm":
            return self._build_main_pm_triage_prompt(task)
        return self._build_pm_triage_prompt(task)

    async def _route_unassigned_pm_task(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> None:
        """Classify and route an unassigned pending task to its target agent."""
        routing = self._classify_task_routing(task)
        agent_id = self._get_routing_target(routing, task)

        if not agent_id:
            logger.warning(
                "No routing target found",
                task_id=task.get("id"),
                routing=routing,
            )
            return

        # Don't auto-claim back to the creator. A PM that just created this
        # task is about to assign it (e.g. be-pm creating a code subtask to
        # hand to be-dev-1 one tool-call later). Racing in and claiming for
        # the PM hijacks the delegation — the PM ends up owning a code task
        # it never intended to work on itself. Skip this tick and let the
        # next dispatch pick it up once assigned_to is set, OR re-evaluate
        # when we have a clearer signal the creator won't route it.
        created_by = task.get("created_by")
        if created_by:
            creator_slug = self._resolve_agent_slug(str(created_by))
            if creator_slug == agent_id:
                logger.info(
                    "Skipping auto-claim: routing target is the creator",
                    task_id=task.get("id"),
                    creator=creator_slug,
                    routing=routing,
                )
                return

        logger.info(
            "Routing task",
            task_id=task.get("id"),
            routing=routing,
            agent_id=agent_id,
        )

        if self._is_agent_active(agent_id):
            await self._claim_task_for_agent(client, task["id"], agent_id)
            return

        if await self._claim_task_for_agent(client, task["id"], agent_id):
            prompt = self._pm_spawn_prompt(routing, agent_id, task)
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=task["id"],
                initial_prompt=prompt,
                git_context=self._task_git_context(task),
            )

    async def _dispatch_pm_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch PM triage work - routes new tasks to appropriate level.

        This is the FIRST dispatcher called - it classifies unassigned tasks
        and routes them to Board, Main PM, Cell PM, or directly to devs.
        Also handles already-assigned pending tasks for PM agents.

        Monitors: pending tasks (both assigned and unassigned)
        Spawns: product-owner, main-pm, be-pm, fe-pm, ux-pm (or devs for simple)
        """
        tasks = await self._fetch_tasks(client, "pending")

        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            assigned_to = task.get("assigned_to")
            if assigned_to:
                await self._handle_pm_assigned_task(task, assigned_to)
                continue

            await self._route_unassigned_pm_task(client, task)

    @staticmethod
    def _all_descendants_terminal(descendants: list[dict[str, Any]]) -> bool:
        """Every descendant in a closure-complete state?"""
        return all(st.get("status") in ("completed", "cancelled") for st in descendants)

    @staticmethod
    def _already_promoted_for_closure(task: dict[str, Any]) -> bool:
        """Skip closure respawn when PR+status show task has moved up."""
        return bool(
            task.get("pr_number")
            and task.get("status")
            in ("awaiting_pm_review", "awaiting_ceo_approval", "completed")
        )

    def _closure_pm_for_team(self, team: str | None) -> str:
        """Pick the PM that owns closure for a given team."""
        if team in ("backend", "frontend", "ux_ui"):
            return self._TEAM_PM_MAP.get(team, "be-pm")
        return "main-pm"

    async def _maybe_spawn_pm_closure(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> None:
        """If this parent task is ready for closure, spawn its PM."""
        task_id = task.get("id")
        if not task_id:
            return

        descendants = await self._fetch_all_descendants(client, task_id)
        if not descendants:
            return
        if not self._all_descendants_terminal(descendants):
            return
        if self._already_promoted_for_closure(task):
            return

        pm_id = self._closure_pm_for_team(task.get("team"))
        if self._is_agent_active(pm_id):
            return

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
            git_context=self._task_git_context(task),
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
                await self._maybe_spawn_pm_closure(client, task)

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

        is_root = not task.get("parent_task_id")
        project_slug = task.get("project_slug", "")
        pr_target = (
            "master (root PR — CEO merges)" if is_root else "your parent task's branch"
        )
        submit_call = (
            "roboco_task_escalate_to_ceo" if is_root else "roboco_task_submit_pm_review"
        )
        is_root_arg = "True" if is_root else "False"

        return f"""You are closing a parent task. All subtasks are terminal — your job is to promote the merged work one level up the hierarchy.

TASK: {task_id}
TITLE: {title}
TEAM: {team}
PROJECT: {project_slug}
ROOT TASK: {"yes" if is_root else "no"}

SUBTASK SUMMARY:
{subtask_summary}

== PM CLOSURE WORKFLOW ==

1. REVIEW AGGREGATE
   - roboco_task_get("{task_id}") — confirm every acceptance criterion.
   - roboco_git_diff("{project_slug}") — review YOUR branch's aggregate diff (all merged subtask work).
   - If any subtask PR is still open, review + merge it now:
     roboco_git_merge_pr("{project_slug}", <pr_number>, "<subtask_id>", "squash")
     then roboco_task_complete("<subtask_id>").
   - Needs rework on a subtask: roboco_task_pm_reject("<subtask_id>", notes="specific feedback").

2. OPEN YOUR PR → {pr_target}
   - roboco_git_create_pr("{project_slug}", "{task_id}", is_root_pr={is_root_arg})
   - This sets pr_created on your task. For non-root, targets the parent task's branch automatically.

3. JOURNAL
   - roboco_journal_decision: title "Closure: {title}", chosen, rationale, task_id="{task_id}".

4. SUBMIT UP
   - {submit_call}("{task_id}")
   - Non-root: your parent PM reviews + merges, then handles their level.
   - Root: CEO (human) reviews + merges master — you do NOT merge to master.

5. IDLE
   - roboco_agent_idle()

Start with step 1.
"""  # noqa: E501

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
        Dispatch assigned work to the assigned agent.

        NOTE: This handles PRE-ASSIGNED tasks (assigned by PM),
        needs_revision tasks, and in_progress tasks where agent is not active
        (e.g., after unblock). New unassigned pending tasks are handled by
        _dispatch_pm_work() which routes them through the PM hierarchy.

        Monitors: assigned pending tasks, needs_revision tasks, orphaned in_progress
        Spawns: Any assigned agent (dev, doc, qa) with appropriate prompt
        """
        # Get tasks needing attention. Includes:
        # - `claimed` — PM-delegated claims where the assignee was never spawned
        # - `blocked` — but only when another agent can resolve (see below)
        # `pending`, `needs_revision`, `in_progress` are the classic cases.
        tasks = await self._fetch_tasks(
            client,
            ["pending", "claimed", "needs_revision", "in_progress", "blocked"],
        )

        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            await self._dev_dispatch_one(client, task)

    @staticmethod
    def _resolve_dev_owner_uuid(task: dict[str, Any]) -> str | None:
        """Pick the right owner UUID for dev dispatch based on status."""
        status = task.get("status")
        if status in ("claimed", "blocked"):
            return task.get("claimed_by") or task.get("assigned_to")
        return task.get("assigned_to")

    async def _respawn_dev_if_inactive(
        self, task: dict[str, Any], agent_slug: str
    ) -> None:
        """Respawn a dev agent on an existing task when it isn't running."""
        if self._is_agent_active(agent_slug):
            return
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=self._build_dev_prompt(task),
            git_context=self._task_git_context(task),
        )

    async def _spawn_pending_dev(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        agent_slug: str,
    ) -> None:
        """Validate and spawn a dev agent for a pending, pre-assigned task."""
        if self._is_agent_active(agent_slug):
            return
        validation_issue = await self._validate_task_for_spawn(client, task, agent_slug)
        if validation_issue:
            logger.warning(
                "Skipping spawn due to validation failure",
                task_id=task["id"],
                agent=agent_slug,
                reason=validation_issue,
            )
            return
        await self.spawn_agent(
            agent_id=agent_slug,
            task_id=task["id"],
            initial_prompt=self._get_prompt_for_agent(agent_slug, task),
            git_context=self._task_git_context(task),
        )

    @staticmethod
    def _is_hitl_blocked(task: dict[str, Any]) -> bool:
        """HITL-blocked tasks wait for human resolution; skip respawn."""
        return (
            task.get("status") == "blocked"
            and task.get("blocker_resolver_type") == "human"
        )

    async def _handle_dev_existing_owner(
        self, task: dict[str, Any], status: str, agent_slug: str
    ) -> None:
        """Respawn existing dev for needs_revision / in_progress / claimed / blocked."""
        if status in (
            "in_progress",
            "claimed",
            "blocked",
        ) and not self._is_agent_active(agent_slug):
            logger.info(
                "Respawning agent for orphaned task",
                task_id=task["id"],
                agent=agent_slug,
                status=status,
            )
        await self._respawn_dev_if_inactive(task, agent_slug)

    async def _dev_dispatch_one(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> None:
        """Dispatch a single task from `_dispatch_dev_work`'s fetch set."""
        team = task.get("team")
        if team not in ["backend", "frontend", "ux_ui"]:
            return

        if self._is_hitl_blocked(task):
            logger.debug(
                "Skipping HITL-blocked task; waiting for human",
                task_id=task["id"],
            )
            return

        status = task.get("status")
        owner_uuid = self._resolve_dev_owner_uuid(task)
        agent_slug = self._resolve_agent_slug(owner_uuid) if owner_uuid else None

        if agent_slug and status in (
            "needs_revision",
            "in_progress",
            "claimed",
            "blocked",
        ):
            await self._handle_dev_existing_owner(task, status, agent_slug)
            return

        # Pending tasks pre-assigned by PM.
        if agent_slug:
            await self._spawn_pending_dev(client, task, agent_slug)

    async def _spawn_assigned_qa(self, task: dict[str, Any], assigned_to: str) -> bool:
        """If task.assigned_to is a QA slug, spawn/skip-if-running; else False.

        Returns True when the dispatch decision for this task was
        handled at the assignee level (spawned or already running).
        Returns False when the assigned_to is NOT a QA agent — caller
        then falls through to the unassigned-select path.
        """
        assigned_slug = self._resolve_agent_slug(assigned_to)
        if not assigned_slug or "qa" not in assigned_slug:
            logger.warning(
                "awaiting_qa task assigned to non-QA slug; reassigning via QA pool",
                task_id=task["id"],
                assigned_slug=assigned_slug,
            )
            return False
        if self._is_agent_active(assigned_slug):
            return True
        await self.spawn_agent(
            agent_id=assigned_slug,
            task_id=task["id"],
            initial_prompt=self._build_qa_prompt(task),
            git_context=self._task_git_context(task),
        )
        return True

    async def _dispatch_qa_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch QA work to QA agents.

        Monitors: awaiting_qa tasks
        Spawns: be-qa, fe-qa, ux-qa
        """
        tasks = await self._fetch_tasks(client, "awaiting_qa")

        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            team = task.get("team")
            if team not in ["backend", "frontend", "ux_ui"]:
                continue

            assigned_to = task.get("assigned_to")
            if assigned_to and await self._spawn_assigned_qa(task, assigned_to):
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
                git_context=self._task_git_context(task),
            )
            # Only spawn one QA at a time per cell
            break

    async def _dispatch_doc_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch documentation + developer work during the parallel
        awaiting_documentation phase.

        `awaiting_documentation` requires BOTH docs_complete=True AND
        pr_created=True to advance to awaiting_pm_review. Doc writes the
        docs; original developer pushes and creates the PR. Whoever
        finishes last triggers the state transition. Previously this
        dispatcher only spawned the documenter — if the documenter
        finished first, the task would sit indefinitely with pr_created=
        False and nothing would spawn the dev to finish the other half.

        Monitors: awaiting_documentation tasks
        Spawns:
            - documenter (be-doc, fe-doc, ux-doc) if docs_complete=False
            - original_developer if pr_created=False (tracked in
              quick_context as "original_developer:<uuid>")
        """
        from roboco.services.task import extract_original_developer

        # Fetch both `awaiting_documentation` and `claimed` because the
        # doc's claim transitions status from awaiting_documentation →
        # claimed. Without including `claimed` we'd miss tasks where doc
        # already grabbed it but pr_created is still false (dev hasn't
        # pushed/created PR yet). The `original_developer:` marker in
        # quick_context identifies tasks that are actually in the parallel
        # phase vs unrelated claimed tasks.
        tasks = await self._fetch_tasks(client, ["awaiting_documentation", "claimed"])
        for task in tasks:
            if self._is_task_handled_this_tick(task.get("id")):
                continue
            await self._doc_dispatch_one(client, task, extract_original_developer)

    async def _auto_assign_doc(
        self, client: httpx.AsyncClient, task: dict[str, Any], team: str
    ) -> None:
        """
        Auto-select and spawn a documenter for an unassigned awaiting_documentation task
        """
        agent_id = self._select_agent_for_cell(team, "doc")
        if not agent_id or self._is_agent_active(agent_id):
            return

        if not await self._claim_task_for_agent(client, task["id"], agent_id):
            logger.warning(
                "Failed to claim awaiting_documentation task for doc",
                task_id=task["id"],
                agent_id=agent_id,
            )
            return

        await self.spawn_agent(
            agent_id=agent_id,
            task_id=task["id"],
            initial_prompt=self._build_doc_prompt(task),
            git_context=self._task_git_context(task),
        )

    async def _doc_dispatch_one(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        extract_original_developer: Any,
    ) -> None:
        """Process a single task for `_dispatch_doc_work`."""
        team = task.get("team")
        if team not in ["backend", "frontend", "ux_ui"]:
            return

        quick_context = task.get("quick_context") or ""
        dev_uuid = extract_original_developer(quick_context)
        status = task.get("status")

        # Only consider `claimed` tasks actually in the doc/PR parallel
        # phase. See `_is_parallel_phase_claim` docstring for the why.
        if status == "claimed" and not self._is_parallel_phase_claim(task, dev_uuid):
            return

        # Developer half: push + create PR
        await self._respawn_dev_for_pr_half(task, dev_uuid)

        # Documenter half: write docs
        if task.get("docs_complete"):
            return

        if await self._respawn_doc_if_assigned(task):
            return

        # Auto-assign a documenter only when still in awaiting_documentation.
        if status != "awaiting_documentation":
            return

        await self._auto_assign_doc(client, task, team)

    async def _respawn_doc_if_assigned(self, task: dict[str, Any]) -> bool:
        """If task is assigned to an inactive documenter, respawn them.

        Returns True when the task is already assigned (whether or not a
        respawn happened) so the caller can stop processing. Returns
        False when the task is unassigned so the caller can auto-select
        a documenter for it.
        """
        assigned_to = task.get("assigned_to")
        if not assigned_to:
            return False
        assigned_slug = self._resolve_agent_slug(assigned_to)
        if self._is_agent_active(assigned_slug):
            return True
        if assigned_slug and "doc" in assigned_slug:
            await self.spawn_agent(
                agent_id=assigned_slug,
                task_id=task["id"],
                initial_prompt=self._build_doc_prompt(task),
                git_context=self._task_git_context(task),
            )
        return True

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
                    git_context=self._task_git_context(task),
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
                git_context=self._task_git_context(task),
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
                git_context=self._task_git_context(task),
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
                git_context=self._task_git_context(task),
            )
            break

    async def _dispatch_escalation_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch escalations to appropriate managers.

        Monitors: escalation notifications (unacknowledged)
        Spawns: be-pm, fe-pm, ux-pm, main-pm, product-owner, head-marketing
        """
        notifications = await self._fetch_notifications(client, "blocker_escalation")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                # Resolve UUID to slug - to_agents contains UUIDs from database
                agent_slug = self._resolve_agent_slug(str(agent_id))

                valid_targets = [
                    "be-pm",
                    "fe-pm",
                    "ux-pm",
                    "main-pm",
                    "product-owner",
                    "head-marketing",
                ]
                if agent_slug not in valid_targets:
                    continue

                if self._is_agent_active(agent_slug):
                    continue

                await self.spawn_agent(
                    agent_id=agent_slug,
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
                # Resolve UUID to slug - to_agents contains UUIDs from database
                agent_slug = self._resolve_agent_slug(str(agent_id))

                if agent_slug not in ["product-owner", "head-marketing", "main-pm"]:
                    continue

                if self._is_agent_active(agent_slug):
                    continue

                await self.spawn_agent(
                    agent_id=agent_slug,
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
            # Resolve UUIDs to slugs and check if auditor is a target
            target_slugs = [self._resolve_agent_slug(str(t)) for t in targets]
            if "auditor" in target_slugs and not self._is_agent_active("auditor"):
                await self.spawn_agent(
                    agent_id="auditor",
                    initial_prompt=self._build_audit_prompt(alert),
                )
                return

        # TODO: Add scheduled periodic audits
        # Check last audit time, spawn if overdue

    async def _detect_stuck_tasks(self, client: httpx.AsyncClient) -> None:
        """
        Detect and auto-block tasks that are stuck.

        This is a proactive enforcement mechanism that finds tasks which
        have been pending without progress and have prerequisite issues.
        Runs every dispatcher cycle but only takes action on truly stuck tasks.

        CEO-approved timeout: 10 minutes
        """
        STUCK_THRESHOLD_MINUTES = 10  # CEO-approved threshold

        tasks = await self._fetch_tasks(client, "pending")

        for task in tasks:
            age = self._get_task_age(task)
            if age is None or age < timedelta(minutes=STUCK_THRESHOLD_MINUTES):
                continue

            issues = self._check_stuck_conditions(task)
            issues.extend(await self._check_dev_subtask_issue(client, task))

            if issues:
                task_id = task.get("id")
                if not task_id:
                    continue
                age_mins = int(age.total_seconds() // 60)
                reason = f"Task stuck for {age_mins} minutes: " + ", ".join(issues)
                await self._auto_block_task(client, task_id, reason)
                logger.warning(
                    "Auto-blocked stuck task",
                    task_id=task_id,
                    age_minutes=age_mins,
                    issues=issues,
                )

        # Per-(role, state) SLA check. Independent from the pending-task
        # sweep above — different states, different action (escalate vs
        # auto-block).
        await self._detect_sla_exceeded(client)

    async def _detect_sla_exceeded(self, client: httpx.AsyncClient) -> None:
        """Auto-escalate tasks that exceeded their per-role SLA.

        Uses ROLE_STATE_SLA_KEYS in enforcement/task_lifecycle.py. Dev tasks
        stuck in `in_progress`/`verifying`, QA tasks in `claimed`, doc tasks
        in `claimed`, and cell-PM tasks in `claimed` all get a soft bump so
        work doesn't silently rot.
        """
        from roboco.enforcement.task_lifecycle import (
            ROLE_STATE_SLA_KEYS,
            sla_seconds_for,
        )

        # Fetch each (role, state) combo we care about. One API call per
        # unique status so we don't fan out pointlessly.
        statuses = sorted({state for _, state in ROLE_STATE_SLA_KEYS})
        for status in statuses:
            try:
                tasks = await self._fetch_tasks(client, status)
            except Exception as e:
                logger.debug(
                    "SLA sweep fetch failed; skipping status",
                    status=status,
                    error=str(e),
                )
                continue
            for task in tasks:
                assigned = task.get("assigned_to")
                if not assigned:
                    continue
                assigned_slug = self._resolve_agent_slug(assigned)
                role = get_agent_role(assigned_slug or "")
                sla = sla_seconds_for(role, status)
                if sla is None:
                    continue
                age = self._time_in_state(task)
                if age is None or age.total_seconds() < sla:
                    continue
                task_id = task.get("id")
                if not task_id:
                    continue
                await self._escalate_sla_breach(
                    client,
                    _SlaBreach(
                        task_id=str(task_id),
                        role=role or "",
                        status=status,
                        age_seconds=int(age.total_seconds()),
                        sla_seconds=sla,
                    ),
                )

    def _time_in_state(self, task: dict[str, Any]) -> timedelta | None:
        """Approximate time in current state via task.updated_at.

        Not perfect — any field update bumps `updated_at`, not just status
        changes — but it's the coarse signal we have, and it under-counts
        (biased toward "agent is working") rather than over-counts, which
        matches the soft-SLA intent.
        """
        updated_at = task.get("updated_at") or task.get("created_at")
        if not updated_at:
            return None
        try:
            if updated_at.endswith("Z"):
                updated_at = updated_at[:-1] + "+00:00"
            parsed = datetime.fromisoformat(updated_at)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return datetime.now(UTC) - parsed
        except (ValueError, TypeError):
            return None

    async def _escalate_sla_breach(
        self, client: httpx.AsyncClient, breach: _SlaBreach
    ) -> None:
        """Record SLA breach in dev_notes and nudge state forward.

        We don't force a state transition here — the MCP lifecycle rules are
        still authoritative. We log, annotate the task, and notify the
        assignee's escalation target. The agent's next spawn picks up the
        updated notes and usually self-escalates.
        """
        age_mins = breach.age_seconds // 60
        sla_mins = breach.sla_seconds // 60
        note = (
            f"[SLA] role={breach.role} status={breach.status} "
            f"time_in_state={age_mins}m sla={sla_mins}m. "
            "Escalating — agent should call roboco_task_escalate "
            "or roboco_task_substitute."
        )
        try:
            await client.patch(
                f"{self._api_url}/tasks/{breach.task_id}",
                json={"dev_notes": note},
            )
            logger.warning(
                "SLA breach noted on task",
                task_id=breach.task_id,
                role=breach.role,
                status=breach.status,
                age_minutes=age_mins,
                sla_minutes=sla_mins,
            )
        except Exception as e:
            logger.debug(
                "SLA breach annotation failed",
                task_id=breach.task_id,
                error=str(e),
            )

    def _get_task_age(self, task: dict[str, Any]) -> timedelta | None:
        """Parse task created_at and return age, or None if unparseable."""
        created_at_str = task.get("created_at")
        if not created_at_str:
            return None
        try:
            if created_at_str.endswith("Z"):
                created_at_str = created_at_str[:-1] + "+00:00"
            created_at = datetime.fromisoformat(created_at_str)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
            return datetime.now(UTC) - created_at
        except (ValueError, TypeError):
            return None

    _MIN_DESCRIPTION_LEN = 10

    def _check_stuck_conditions(self, task: dict[str, Any]) -> list[str]:
        """Check for common stuck conditions (git, description)."""
        issues: list[str] = []
        if not task.get("branch_name"):
            issues.append("Task missing branch_name")
        description = (task.get("description") or "").strip()
        if len(description) < self._MIN_DESCRIPTION_LEN:
            issues.append("Empty or inadequate description")
        return issues

    async def _check_dev_subtask_issue(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> list[str]:
        """Check if complex dev task is missing subtasks."""
        from roboco.agents_config import get_agent_role

        assigned_to = task.get("assigned_to")
        if not assigned_to:
            return []

        agent_slug = self._resolve_agent_slug(assigned_to)
        if not agent_slug or get_agent_role(agent_slug) != "developer":
            return []

        complexity = task.get("estimated_complexity", "low")
        is_low_complexity = complexity not in ("medium", "high", "critical")
        if is_low_complexity or task.get("parent_task_id"):
            return []

        try:
            resp = await client.get(f"{self._api_url}/tasks/{task.get('id')}/subtasks")
            subtasks = resp.json() if resp.is_success else []
        except Exception:
            subtasks = []

        if not subtasks:
            return [f"{complexity} complexity task without subtasks"]
        return []

    async def _dispatch_a2a_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch A2A (Agent-to-Agent) requests to target agents.

        Monitors: a2a_request notifications (unacknowledged)
        Spawns: Any agent that is the target of an A2A request

        This is a fallback mechanism - primary A2A routing happens via events.
        If the event-based spawn fails, these notifications will be picked up here.
        """
        notifications = await self._fetch_notifications(client, "a2a_request")

        for notif in notifications:
            targets = notif.get("to_agents", [])

            for agent_id in targets:
                # Resolve UUID to slug - to_agents contains UUIDs from database
                agent_slug = self._resolve_agent_slug(str(agent_id))

                if self._is_agent_active(agent_slug):
                    # Agent is online - SDK handles A2A delivery directly
                    # No action needed here, SDK server receives messages
                    continue

                # Agent is offline - spawn them with A2A context
                await self.spawn_agent(
                    agent_id=agent_slug,
                    initial_prompt=self._build_a2a_prompt(notif),
                )
                break

    # =========================================================================
    # SMART DISPATCHER - PROMPT BUILDERS
    # =========================================================================

    def _get_workflow_state(
        self,
        status: str,
        has_plan: bool,
    ) -> str:
        """Determine developer workflow state from task attributes.

        Args:
            status: Task status (claimed, in_progress, needs_revision, etc.)
            has_plan: Whether task has a plan submitted

        Returns:
            Workflow state string (NEEDS_PLAN, READY_TO_START, EXECUTING, etc.)
        """
        # Direct status mappings
        status_map = {
            "in_progress": "EXECUTING",
            "needs_revision": "REVISION_REQUIRED",
            "verifying": "VERIFYING",
        }

        if status in status_map:
            return status_map[status]

        # Handle claimed status with sub-states
        if status == "claimed":
            if not has_plan:
                return "NEEDS_PLAN"
            return "READY_TO_START"

        return status.upper()

    def _get_workflow_instructions(self, state: str, task_id: str) -> str:
        """Get workflow instructions for the given state.

        Args:
            state: Workflow state (NEEDS_PLAN, READY_TO_START, etc.)
            task_id: Task ID for tool call examples

        Returns:
            Markdown-formatted instructions for the current state
        """
        instructions = {
            "NEEDS_PLAN": f"""## NEXT STEP: Submit Plan

You MUST submit a plan before starting work.

Call roboco_task_plan("{task_id}", {{
    "approach": "Your implementation strategy",
    "sub_tasks": [
        {{"title": "Step 1", "description": "First action"}},
        {{"title": "Step 2", "description": "Next action"}}
    ],
    "risks": ["Potential issues"],
    "open_questions": ["Clarifications needed"]
}})

You CANNOT call roboco_task_start() until plan is submitted.
""",
            "READY_TO_START": f"""## NEXT STEP: Start Work

Your plan is approved. Call roboco_task_start("{task_id}") to begin.

Then proceed to execute your sub_tasks using roboco_git_* tools.
""",
            "EXECUTING": """## IN PROGRESS

Continue development. REQUIRED GATES before you can submit for QA
(enforced server-side - ignoring them returns 400):
1. roboco_git_commit() - at least one commit on this task
2. roboco_git_push() - push the branch
3. roboco_git_create_pr() - open the PR; task.pr_number MUST be set
4. roboco_task_progress() - at least one progress update (percent + note)

In addition:
- roboco_journal_* to log decisions/learnings as you work
- When all acceptance criteria are met AND steps 1-4 are done:
  roboco_task_submit_verification() then roboco_task_submit_qa().

QA will REJECT the task if pr_number is not set - don't ask QA to
review unpushed or un-PR'd work.
""",
            "REVISION_REQUIRED": f"""## REVISION REQUESTED

QA or PM requested changes:
1. Call roboco_task_get("{task_id}") to see feedback
2. Call roboco_task_claim("{task_id}") to reclaim
3. Update plan if needed: roboco_task_plan()
4. Call roboco_task_start("{task_id}") to resume
""",
            "VERIFYING": """## SELF-VERIFICATION

Run quality checks and verify against acceptance criteria:
1. Run tests, lint, type checks
2. Review changes with roboco_git_diff()
3. Confirm the PR is OPEN on GitHub (roboco_task_get → pr_number not null)
4. Confirm you have at least one roboco_task_progress() update
3. If all good: roboco_task_submit_qa()
4. If issues found: fix and commit
""",
        }
        return instructions.get(
            state, f'Call roboco_task_get("{task_id}") to check status.'
        )

    def _build_dev_prompt(self, task: dict[str, Any]) -> str:
        """Build state-aware initial prompt for a developer."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        status = task.get("status", "unknown")

        # Determine workflow state based on task attributes
        has_plan = bool(task.get("plan"))
        workflow_state = self._get_workflow_state(status, has_plan)
        instructions = self._get_workflow_instructions(workflow_state, task_id)

        return f"""You have been assigned a development task.

TASK ID: {task_id}
TITLE: {title}
STATUS: {status}
WORKFLOW STATE: {workflow_state}

{instructions}

Start by calling roboco_task_get("{task_id}") for full details.
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

    def _build_a2a_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an A2A (Agent-to-Agent) request."""
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        body = notification.get("body", "No message provided")
        related_task_id = notification.get("related_task_id")
        metadata = notification.get("metadata", {})
        skill = metadata.get("skill", "general")
        urgent = metadata.get("urgent", False)

        urgency_note = "**URGENT** - This request has priority.\n\n" if urgent else ""
        task_note = f"RELATED TASK: {related_task_id}\n" if related_task_id else ""

        return f"""You have received an A2A (Agent-to-Agent) REQUEST.

{urgency_note}FROM: {from_agent}
SKILL: {skill}
{task_note}
REQUEST:
{body}

Your job:

1. Acknowledge the notification with roboco_notify_ack("{notif_id}")
2. Process the request using your {skill} capabilities
3. Respond to {from_agent} using roboco_agent_request()
4. If you need task context, call roboco_task_get("{related_task_id or "task_id"}")
5. When done, call roboco_task_scan() for other work
6. If no more work, call roboco_agent_idle() to shutdown gracefully
"""

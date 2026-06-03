"""
Agent Orchestrator

Manages Claude Code containers for all RoboCo agents.
Handles spawning, monitoring, health checks, and graceful shutdown.

The orchestrator is the BRAIN of the system:
- Checks for work BEFORE spawning agents (no wasteful spawns)
- Claims tasks on behalf of agents before spawning
- Agents receive their assignment at spawn time
- Agents scan for more work after completing a task
- Agents only call i_am_idle() when truly no work remains
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
    from collections.abc import Callable

    from roboco.services.llm import AgentRoute
    from roboco.services.task import TaskService
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
from roboco.foundation import identity as _foundation
from roboco.foundation.identity import CELL_TEAMS
from roboco.foundation.policy.agent_loop import DEFAULT_BUDGET as _AGENT_LOOP_BUDGET
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


def _is_coordination_task(task: dict[str, Any]) -> bool:
    """True for a board/fan-out task that carries a product but no repo of its own.

    Such a task does no git work itself: its cell subtasks each resolve a real
    project from the product's cell->project map (see TaskCreate's
    project-or-product invariant and migration 018). It therefore has no
    project_slug, branch_name, or git token, and must NOT be git-gated at the
    spawn-readiness or stuck-detection checks the way a code task is. A task with
    neither a project nor a product is genuinely unroutable and stays gated.
    """
    return not task.get("project_id") and bool(task.get("product_id"))


# A branch is auto-created only at CLAIM (the claimed->in_progress transition).
# Before that — while a task is still pending/backlog awaiting first dispatch —
# it legitimately has no branch_name, so the readiness / stuck / spawn checks
# must NOT treat a missing branch as a defect. These are the only states where
# a code task is expected to already own a branch.
_BRANCH_EXPECTED_STATES: frozenset[str] = frozenset(
    {"claimed", "in_progress", "verifying"}
)


def _branch_is_expected(task: dict[str, Any]) -> bool:
    """True iff this task should already have a branch_name.

    A branch only exists at/after claim, and a coordination/fan-out task never
    gets one (it does no git of its own). Gating the "missing branch_name"
    readiness/stuck condition on this predicate stops the orchestrator from
    auto-blocking a never-claimed PENDING code task that simply hasn't reached
    the claim transition yet (issue #18: a pending task sat 13min, auto-blocked
    every 30s, never dispatched).
    """
    if _is_coordination_task(task):
        return False
    return str(task.get("status") or "") in _BRANCH_EXPECTED_STATES


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


def _agent_workspace_path(project_slug: str, team: str, agent_id: str) -> str:
    """Per-agent workspace path inside the container.

    Mirrors the bind-mount layout: the host's workspaces dir is mounted at
    /data/workspaces (orchestrator.py mount args), so each agent's clone lives
    at /data/workspaces/<project>/<team>/<agent>. Used by both
    _get_role_permissions (Edit/Write allowlist) and _build_mount_args
    (docker ``-w`` flag) so the cwd matches the allowlist scope.
    """
    return f"/data/workspaces/{project_slug}/{team}/{agent_id}"


def _cell_workspace_path(project_slug: str, team: str) -> str:
    """Cell-level workspace path (documenter scope).

    Same rationale as ``_agent_workspace_path``; documenters work at the cell
    branch, not a per-agent dev branch.
    """
    return f"/data/workspaces/{project_slug}/{team}"


def _resolve_project_slug_from_git_context(
    git_context: "SpawnGitContext | None",
) -> str:
    """Extract project_slug from git_context, falling back to 'default'.

    Module-level counterpart to the instance method ``_resolve_project_slug``.
    Called by static / classmethod contexts (e.g. ``_build_mount_args``) that
    cannot access ``self``. The fallback warning is omitted here because the
    instance method already logs it when the full spawn path runs; this helper
    is only for the mount-args path where the agent_id/task_id context is not
    available.
    """
    if git_context and git_context.project_slug:
        return git_context.project_slug
    return "default"


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

    # Two paths in play:
    #   - orchestrator-internal: where the file is written inside the
    #     orchestrator container (settings.manifest_host_dir). The compose
    #     volume mount makes this dir visible on the host.
    #   - host-side: what the docker daemon needs for the bind-mount into
    #     the spawned agent. Computed via DATA_HOST_PATH translation.
    write_dir = Path(settings.manifest_host_dir)
    write_path = write_dir / f"{agent_id}.json"
    write_manifest(manifest, write_path)
    if DATA_HOST_PATH:
        return Path(f"{DATA_HOST_PATH}/manifests/{agent_id}.json")
    return write_path


# =============================================================================
# GATEWAY PRE-SPAWN CHECK (trigger_filter spawn cooldown)
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

    The trigger_filter spawn cooldown runs unconditionally for every spawn.
    """
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
        # Board agents (Product Owner / Head of Marketing) get exactly ONE
        # review pass per assigned task: they have no verb to claim, plan,
        # delegate, or complete, so a respawn cannot advance the task and would
        # just loop. Tracks (agent_slug, task_id) already dispatched.
        self._board_dispatched: set[tuple[str, str]] = set()
        # Cluster C5: a board review is a two-reviewer gate — BOTH the Product
        # Owner and the Head of Marketing must review a board/coordination task
        # before it is handed to the CEO for Approve & Start. Once both have
        # finished (dispatched-and-no-longer-active), the orchestrator emits ONE
        # formal CEO notification per task. Tracks task_ids already notified so
        # the signal fires exactly once.
        self._board_review_ceo_notified: set[str] = set()
        # Stale-claim reaper config. Wave C3 (2026-05-12): sourced from
        # stale_claim_reap_seconds (default 600) rather than
        # claim_stale_seconds (default 180). The two settings are now
        # distinct: claim_stale_seconds drives trigger_filter (spawn
        # queueing); stale_claim_reap_seconds drives the reaper.
        # Smoke run 3 showed agents reaped at 180s while actively retrying
        # rejected verbs — LLM inference routinely exceeds that window.
        # Tests bypass `__init__` via `__new__` and set _claim_heartbeat_ttl
        # directly; production never uses _task_svc from __init__.
        self._claim_heartbeat_ttl: int = settings.stale_claim_reap_seconds

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

        # Self-heal: roll back orphan claims left over from a prior crash
        # (audit P2-8). Tasks that show CLAIMED/IN_PROGRESS but have NO
        # branch_name set indicate _finalize_claim flushed the status before
        # branch creation failed in a pre-P0-7 run. Without this, the next
        # claim attempt fails non-idempotent on `git checkout -b`.
        await self._reconcile_orphan_claims_on_startup()

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
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
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
                    f"Write(/{cell_workspace_path}/**)",
                    f"Edit(/{cell_workspace_path}/**)",
                    "Write(//app/docs/**)",
                    "Edit(//app/docs/**)",
                    "Write(//app/CHANGELOG.md)",
                    "Edit(//app/CHANGELOG.md)",
                    "Write(//app/README.md)",
                    "Edit(//app/README.md)",
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
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
                ],
                "deny": [],
            },
            "head_marketing": {
                "allow": [
                    f"Write(/{workspace_path}/**)",
                    f"Edit(/{workspace_path}/**)",
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
            # NOTE: Write/Edit are intentionally NOT globally denied here.
            # Claude Code evaluates rules deny -> ask -> allow and the first
            # match wins, so a deny ALWAYS beats a more-specific allow (the
            # glob syntax has no negation). A global Write(*)/Edit(*) here
            # therefore unconditionally shadowed the per-role,
            # workspace-scoped Write/Edit allows below — every agent (devs
            # included) was unable to edit ANY file and fell back to
            # destructive bash redirection (clobbering real files). Roles
            # that must NOT write (qa, cell_pm, main_pm, auditor) carry
            # their own Write(*)/Edit(*) deny in _get_role_permissions.
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

        The gateway pre-spawn check runs first; a QUEUE or DROP outcome skips
        the container launch.
        """
        # Gateway pre-spawn cooldown gate.
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
        team = get_agent_team(agent_id) or "backend"

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
        workspace_path = _agent_workspace_path(project_slug, team, agent_id)
        cell_workspace_path = _cell_workspace_path(project_slug, team)

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
        AgentOrchestrator._append_claude_json_mount(cmd, hosts)
        AgentOrchestrator._append_optional_host_mounts(cmd, hosts)
        role = get_agent_role(config.agent_id) or "developer"
        cmd.extend(AgentOrchestrator._core_volume_and_env_args(config, hosts, role))
        AgentOrchestrator._append_provider_env(cmd, config)
        subagent_model = _resolve_agent_cli_model(config.provider_type, config.model)
        cmd.extend(["-e", f"CLAUDE_CODE_SUBAGENT_MODEL={subagent_model}"])
        AgentOrchestrator._append_manifest_args(cmd, config, subagent_model)
        AgentOrchestrator._append_workspace_cwd(cmd, config)
        return cmd

    @staticmethod
    def _append_claude_json_mount(cmd: list[str], hosts: dict[str, str | None]) -> None:
        """Mount host's ~/.claude.json sibling FILE if present (audit D-48)."""
        claude_dir = hosts["claude"]
        if not claude_dir:
            return
        claude_json_host = f"{claude_dir.rstrip('/')}.json"
        if Path(claude_json_host).exists():
            cmd.extend(["-v", f"{claude_json_host}:/home/agent/.claude.json"])

    @staticmethod
    def _append_optional_host_mounts(
        cmd: list[str], hosts: dict[str, str | None]
    ) -> None:
        """Mount agent settings.json and briefing.md when their hosts exist."""
        settings_host = hosts.get("settings")
        if settings_host:
            cmd.extend(["-v", f"{settings_host}:/home/agent/.claude/settings.json:ro"])
        briefing_host = hosts.get("briefing")
        if briefing_host:
            cmd.extend(["-v", f"{briefing_host}:/app/briefing.md:ro"])

    @staticmethod
    def _core_volume_and_env_args(
        config: AgentConfig, hosts: dict[str, str | None], role: str
    ) -> list[str]:
        """The always-on -v/-e block (prompt, blueprints, docs, workspaces, env)."""
        docs_ro = "" if config.agent_id in ALL_DOCS else ":ro"
        return [
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

    @staticmethod
    def _append_provider_env(cmd: list[str], config: AgentConfig) -> None:
        """Inject ANTHROPIC_* env only on non-Anthropic providers."""
        # Provider routing: only inject ANTHROPIC_* env vars when the
        # resolved provider is non-Anthropic (i.e. Ollama Cloud). For the
        # Anthropic default path both fields are None and Claude Code
        # inside the container continues to use its mounted ~/.claude
        # credentials — preserving legacy behaviour byte-for-byte.
        if config.provider_base_url:
            cmd.extend(["-e", f"ANTHROPIC_BASE_URL={config.provider_base_url}"])
        if config.provider_auth_token:
            cmd.extend(["-e", f"ANTHROPIC_AUTH_TOKEN={config.provider_auth_token}"])

    @staticmethod
    def _append_manifest_args(
        cmd: list[str], config: AgentConfig, subagent_model: str
    ) -> None:
        """Write the spawn manifest and flip the gateway flag."""
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

    _ROLES_WITH_AGENT_WORKSPACE: ClassVar[frozenset[str]] = frozenset(
        {"developer", "product_owner", "head_marketing"}
    )
    _ROLES_WITH_CELL_WORKSPACE: ClassVar[frozenset[str]] = frozenset({"documenter"})

    @staticmethod
    def _append_workspace_cwd(cmd: list[str], config: AgentConfig) -> None:
        """Set the container -w to the agent or cell workspace by role."""
        # Pre-gateway parity (Wave A2+A3, 2026-05-12). Set the container's cwd
        # to the agent's task workspace so Edit/Write resolve to paths that
        # match _get_role_permissions allowlist, and `git add` operates inside
        # the workspace clone. Without this, container WORKDIR (/app from the
        # Dockerfile) shadows the workspace and every file op fails.
        #
        # Mirror the workspace-path selection in _get_role_permissions exactly:
        # - developer / product_owner / head_marketing: per-agent workspace
        # - documenter: cell workspace
        # - qa / cell_pm / main_pm / auditor: no write workspace → omit -w
        role = get_agent_role(config.agent_id) or "developer"
        team = get_agent_team(config.agent_id) or ""
        project = _resolve_project_slug_from_git_context(config.git_context)
        if role in AgentOrchestrator._ROLES_WITH_AGENT_WORKSPACE:
            cmd.extend(["-w", _agent_workspace_path(project, team, config.agent_id)])
        elif role in AgentOrchestrator._ROLES_WITH_CELL_WORKSPACE:
            cmd.extend(["-w", _cell_workspace_path(project, team)])

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
            "1. Call `give_me_work()` to find work for your role\n"
            "2. Begin the assigned task (its details arrive in the "
            "response): UNDERSTAND -> PLAN -> EXECUTE -> VERIFY -> HANDOFF\n"
            "3. If no tasks available, call `i_am_idle()` "
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
                "--strict-mcp-config",
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
        # Gateway v1 endpoints declare X-Agent-ID as Annotated[UUID, Header(...)],
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
            # #179: every MCP server is launched as `uv run python -m
            # roboco.mcp.<server>` by Claude Code, with cwd = the agent's
            # WORKSPACE (not /app). Without this, `uv run` resolves a
            # cwd-relative `.venv` (≠ the baked /app/.venv), ignores the
            # image's VIRTUAL_ENV with a warning, and RE-SYNCS the full
            # dependency set (torch/lancedb/pyarrow/scipy, ~350MB) into a
            # fresh venv on every spawn — masked by a warm uv wheel cache,
            # but on a cold cache (first spawn after an image rebuild) the
            # download takes minutes and the MCP servers never come up
            # before the agent burns its budget. Pinning the project env
            # to the pre-baked venv makes `uv run` reuse it instantly,
            # regardless of cwd.
            "UV_PROJECT_ENVIRONMENT": "/app/.venv",
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

                # Skip the git-token gate for coordination tasks — they have no
                # project of their own, so there's no token to require.
                if not _is_coordination_task(task):
                    project_slug = _read_project_slug(task)
                    token_reason = await self._readiness_check_git_token(project_slug)
                    if token_reason is not None:
                        return await self._readiness_block(
                            client, task_id, token_reason
                        )
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
    @staticmethod
    def _readiness_check_acceptance_criteria(task: dict[str, Any]) -> str | None:
        """Return blocker reason for missing acceptance criteria, else None."""
        criteria = task.get("acceptance_criteria") or []
        if isinstance(criteria, str):
            criteria = [criteria] if criteria.strip() else []
        if not criteria:
            return "missing acceptance_criteria"
        return None

    @staticmethod
    def _readiness_check_role_for_status(
        agent_id: str, role: str, status: str
    ) -> str | None:
        """Verify agent role matches the role expected for the task status.

        Handoff states are role-specific. Dev-owned states (in_progress,
        verifying, needs_revision, paused, blocked) are restricted to
        developer/documenter to defang the smoke-8 bug where QA got
        respawned on a `needs_revision` task via the crash-restart path
        and immediately hit ``role 'qa' may not claim from status
        'needs_revision'`` at the gateway.
        """
        role_mismatch: dict[str, str | set[str]] = {
            "awaiting_qa": "qa",
            "awaiting_documentation": "documenter",
            "awaiting_pm_review": {"cell_pm", "main_pm"},
            "awaiting_ceo_approval": "ceo",
            # Dev-owned states — only developer/documenter may claim or
            # resume work here. PMs / QA spawning on these is a misroute.
            "needs_revision": {"developer", "documenter"},
            "verifying": {"developer", "documenter"},
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

    def _readiness_check_task(self, agent_id: str, task: dict[str, Any]) -> str | None:
        """Return a persistent blocker reason on the task itself, else None."""
        status = task.get("status", "")
        role = get_agent_role(agent_id) or ""

        if reason := self._readiness_check_acceptance_criteria(task):
            return reason
        # A coordination task (product, no repo of its own) does no git: skip the
        # project-slug and branch-name gates that only apply to code tasks.
        if not _is_coordination_task(task):
            if not _read_project_slug(task):
                return "task has no project"
            # Branch is auto-created at claim, so only states at/after claim are
            # expected to own one. _branch_is_expected centralizes this gate so
            # the readiness and stuck-detection paths agree (#18).
            if _branch_is_expected(task) and not task.get("branch_name"):
                return f"state={status} but branch_name is unset"
        return self._readiness_check_role_for_status(agent_id, role, status)

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
    _VERB_SERVER_CACHE: ClassVar[dict[str, str]] = {}

    # Per-role built-in tools, enumerated in the briefing so the agent
    # knows exactly what it has. These are pre-loaded at spawn via the
    # Claude Code `--tools` flag and gated only by the per-role
    # permission rules — NOT by ToolSearch (MCP-only; never gates
    # built-ins). Mirrors the system-prompt layer's _ROLE_BUILTIN_TOOLS
    # in roboco/agents/factories/_base.py — kept in sync because the
    # briefing and the system prompt are independent code paths.
    _COMMON_BUILTIN_TOOLS: ClassVar[tuple[str, ...]] = (
        "Read",
        "Bash",
        "Grep",
        "Glob",
        "Task",
        "TodoWrite",
    )
    _ROLE_BUILTIN_TOOLS: ClassVar[dict[str, tuple[str, ...]]] = {
        "developer": (*_COMMON_BUILTIN_TOOLS, "Edit", "Write"),
        "documenter": (*_COMMON_BUILTIN_TOOLS, "Edit", "Write"),
        "qa": _COMMON_BUILTIN_TOOLS,
        "main_pm": _COMMON_BUILTIN_TOOLS,
        "cell_pm": _COMMON_BUILTIN_TOOLS,
        "product_owner": _COMMON_BUILTIN_TOOLS,
        "head_marketing": _COMMON_BUILTIN_TOOLS,
        "auditor": _COMMON_BUILTIN_TOOLS,
    }

    def _build_tool_load_block(self, role: str) -> str:
        """Briefing block affirming the role's built-in tools are ready.

        Built-in tools are pre-loaded at spawn via the Claude Code
        `--tools` flag and gated only by the per-role permission rules.
        ToolSearch is MCP-only and never gates built-ins — an earlier
        revision instructed agents to "run ToolSearch to activate
        Edit/Write", which was false (ToolSearch is not even callable
        here), so weak models chased a nonexistent tool and fell back to
        destructive shell file-writes. This states the tools are live and
        steers away from that failure. Cached per role.
        """
        if role in self._TOOL_LOAD_CACHE:
            return self._TOOL_LOAD_CACHE[role]
        tools = self._ROLE_BUILTIN_TOOLS.get(role)
        if not tools:
            block = ""
        else:
            tool_list = ", ".join(tools)
            edit_line = (
                "Make file changes with Edit/Write — never rewrite a "
                "whole file via shell redirection (>, heredoc, tee); "
                "that destroys content and is unnecessary.\n"
                if "Edit" in tools
                else "You read and review; you do not author files.\n"
            )
            block = (
                "## Your tools are ready\n"
                "\n"
                f"Loaded and available now: {tool_list}. Use them "
                "directly. Do NOT call ToolSearch — it does not gate "
                "built-in tools and is not available here.\n"
                f"{edit_line}"
                "\n"
            )
        self._TOOL_LOAD_CACHE[role] = block
        return block

    # Roles whose containers also mount the docs MCP server. Mirrors the
    # gating in the MCP-server registration so the verb-server map stays
    # accurate without re-deriving it.
    _DOCS_SERVER_ROLES: ClassVar[tuple[str, ...]] = (
        "documenter",
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
    )

    def _build_verb_server_block(self, role: str) -> str:
        """Briefing block: which MCP server hosts each verb + key preconditions.

        Agents fumble their first move — raw bash/http/shell-git, calling
        ``evidence`` on roboco-flow when it lives on roboco-do, omitting the
        ``nature`` argument on ``delegate``, or skipping the required journal
        note before claiming. The role docs cover this but agents cannot read
        them at spawn, so the map is generated here from the role's actual
        manifest (``get_role_config``) and stays accurate as the spec changes.
        Cached per role.
        """
        from roboco.services.gateway.role_config import ROLE_CONFIGS, get_role_config

        if role in self._VERB_SERVER_CACHE:
            return self._VERB_SERVER_CACHE[role]
        if role not in ROLE_CONFIGS:
            self._VERB_SERVER_CACHE[role] = ""
            return ""

        cfg = get_role_config(role)
        lines = [
            "## Which MCP server hosts each verb",
            "",
            "Call the verb on the right server — the server name is the MCP",
            "tool prefix (`mcp__<server>__<verb>`). Never reach for raw bash,",
            "raw http, or shell git (`git commit`/`push`/`checkout`); the",
            "bash-guard blocks them. Use these verbs instead:",
            "",
            f"- **roboco-flow** (intent verbs): {', '.join(cfg.flow_tools)}",
            f"- **roboco-do** (content tools): {', '.join(cfg.do_tools)}",
            "- **roboco-git-readonly** (read-only git): roboco_git_status,"
            " roboco_git_log, roboco_git_diff, roboco_git_branch_list",
            "- **roboco-optimal** (knowledge base): roboco_ask_mentor,"
            " roboco_kb_search",
        ]
        if role in self._DOCS_SERVER_ROLES:
            lines.append(
                "- **roboco-docs** (project docs files): roboco_docs_read,"
                " roboco_docs_write, roboco_docs_list"
            )

        preconditions = [
            "`evidence` lives on roboco-do, NOT roboco-flow — inspect a task"
            " there before acting on it.",
        ]
        if "i_will_work_on" in cfg.flow_tools:
            preconditions.append(
                "note(scope='decision') is REQUIRED before i_will_work_on —"
                " log your approach first or the claim is rejected."
            )
        if "i_will_plan" in cfg.flow_tools:
            preconditions.append(
                "note(scope='decision') is REQUIRED before i_will_plan /"
                " complete / escalate — log the decision first."
            )
        if "delegate" in cfg.flow_tools:
            preconditions.append(
                "delegate requires `nature` (one of: technical |"
                " non_technical) — omitting it is rejected."
            )

        lines.append("")
        lines.append("### Key preconditions")
        lines.extend(f"- {p}" for p in preconditions)
        lines.append("")
        lines.append("")

        block = "\n".join(lines)
        self._VERB_SERVER_CACHE[role] = block
        return block

    @staticmethod
    def _format_task_briefing_block(task_id: str, task: dict[str, Any]) -> str:
        """Build the ``## Current task`` markdown block from a fetched task."""
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
        return (
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

    async def _fetch_task_for_briefing(
        self, agent_id: str, task_id: str
    ) -> dict[str, Any] | None:
        """Best-effort GET /tasks/{id}; returns task dict or None on failure."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._api_url}/tasks/{task_id}")
            if resp.status_code == http_status.HTTP_200_OK:
                payload: dict[str, Any] = resp.json()
                return payload
        except Exception as e:
            logger.debug(
                "Briefing task-fetch failed — falling back to role-only",
                agent_id=agent_id,
                task_id=task_id,
                error=str(e),
            )
        return None

    async def _write_agent_briefing(
        self,
        agent_id: str,
        task_id: str | None,
        workspace_path: str,
    ) -> Path | None:
        """Write a compact task briefing to be read by SessionStart hook.

        The briefing saves the agent from burning its first 2-3 tool calls on
        `give_me_work` (whose Envelope already carries the task details). If
        `task_id` is known we fetch
        the task and include title, status, branch, and acceptance criteria.
        On fetch failure we still emit the role-level part (role, escalation
        target, terminal tools, workspace path) — strictly better than nothing.
        """
        role = get_agent_role(agent_id) or "agent"
        team = get_agent_team(agent_id) or "-"
        escalate_to = get_escalation_target(agent_id) or "main-pm"

        tool_load_block = self._build_tool_load_block(role)
        verb_server_block = self._build_verb_server_block(role)
        task_block = ""
        if task_id:
            task = await self._fetch_task_for_briefing(agent_id, task_id)
            if task is not None:
                task_block = self._format_task_briefing_block(task_id, task)

        content = (
            f"# Session briefing — {agent_id}\n"
            "\n"
            f"{tool_load_block}"
            f"{verb_server_block}"
            "## You are\n"
            f"- **Agent:** `{agent_id}`\n"
            f"- **Role:** {role}\n"
            f"- **Team:** {team}\n"
            f"- **Escalate to:** `{escalate_to}`\n"
            f"- **Workspace:** `{workspace_path}`\n"
            f"{task_block}"
            "\n## Terminal tools (how to exit cleanly)\n"
            "- `i_am_idle()` — no work remaining (every role)\n"
            "- `i_am_blocked(task_id, reason, ...)` — stuck (developer)\n"
            "- `unclaim(task_id)` — release a claim back to the pool\n"
            "- Role handoffs:\n"
            "  - developer → `i_am_done(task_id, notes)` (submit for QA)\n"
            "  - qa → `pass(task_id, notes)` / `fail(task_id, issues)`\n"
            "  - documenter → `i_documented(task_id, notes, files)`\n"
            "  - cell_pm → `complete(task_id, notes)` / `submit_up(...)`"
            " / `escalate_up(...)`\n"
            "  - main_pm → `complete(...)` / `escalate_to_ceo(...)`\n"
            "\n"
            "A Stop without a terminal tool will be rejected; a second Stop\n"
            "auto-substitutes the task so it can be picked up elsewhere.\n"
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

    # Slug -> team string for ROUTING purposes. Derived from
    # foundation.AGENTS so adding/renaming an agent edits exactly one
    # file (foundation/identity.py). The dispatcher relies on this for
    # task assignment routing categories.
    _AGENT_TEAM_MAP: ClassVar[dict[str, str]] = {
        slug: row.team.value for slug, row in _foundation.AGENTS.items()
    }

    def _get_agent_team(self, agent_id: str) -> str | None:
        """Get team from agent_id. Returns None for unknown slugs."""
        try:
            return _foundation.team_for_slug(agent_id).value
        except KeyError:
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
        `open_pr`, so it alone cannot distinguish a QA-claimed
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
2. Call unblock("{record.task_id}")
3. Continue from where you left off
"""

        elif record.waiting_for == "qa_result":
            if resolution.get("passed"):
                return f"""
TASK-{record.task_id} has passed QA review.
The task is now awaiting documentation.
You may return to scanning for new work with give_me_work().
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
1. Review the task details provided in your briefing / context_briefing
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

    @staticmethod
    async def _inspect_container_state(
        container_name: str,
    ) -> tuple[bool, int | None]:
        """Return (is_running, exit_code) from `docker inspect`.

        exit_code is None when the output is missing or unparseable; the
        caller treats None as a crash for safety (smoke-8 fix).
        """
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}} {{.State.ExitCode}}",
            container_name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await proc.communicate()
        parts = stdout.decode().strip().split()
        is_running = bool(parts) and parts[0] == "true"
        try:
            exit_code = int(parts[1]) if len(parts) > 1 and parts[1] else None
        except ValueError:
            exit_code = None
        return is_running, exit_code

    async def _handle_stopped_container(
        self, agent_id: str, instance: Any, exit_code: int | None
    ) -> None:
        """Update state + auto-restart only when the exit was non-zero.

        Smoke-8 evidence: graceful exits (exit 0 — agent called i_am_idle)
        were treated as crashes by the old logic. The health check bumped
        error_count and respawned the agent with the prior task_id even if
        the task had since moved into a state the role can't claim from
        (e.g. QA → needs_revision). Now: clean exits reset error_count and
        do nothing; non-zero exits keep the existing crash-retry behaviour.
        """
        cid = instance.container_id[:12] if instance.container_id else None
        graceful = exit_code == 0
        if graceful:
            logger.info(
                "Agent container exited gracefully",
                agent_id=agent_id,
                container_id=cid,
                exit_code=exit_code,
            )
        else:
            logger.warning(
                "Agent container stopped unexpectedly",
                agent_id=agent_id,
                container_id=cid,
                exit_code=exit_code,
            )
        instance.state = AgentState.OFFLINE
        instance.container_id = None
        if graceful:
            instance.error_count = 0
            return
        instance.error_count += 1
        max_retries = 3
        if instance.error_count < max_retries:
            logger.info("Auto-restarting crashed agent", agent_id=agent_id)
            await self.spawn_agent(
                agent_id=agent_id,
                task_id=instance.current_task_id,
                git_context=(instance.config.git_context if instance.config else None),
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

    async def _check_health(self) -> None:
        """Check health of all running agents."""
        for agent_id, instance in list(self._instances.items()):
            if instance.state not in (AgentState.ACTIVE, AgentState.WAITING_SHORT):
                continue
            if instance.container_id is None:
                continue
            is_running, exit_code = await self._inspect_container_state(
                f"roboco-agent-{agent_id}"
            )
            if not is_running:
                await self._handle_stopped_container(agent_id, instance, exit_code)

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
        """Verify the parent task has a branch; auto-block + return msg if not.

        Race window: the PM's `i_will_plan` claims the parent (transitions
        status -> in_progress, sets assigned_to) and then `_finalize_claim`
        creates the branch via `_ensure_branch_for_task`. Both actions land
        in the same DB transaction but a child dev's spawn dispatch can fire
        microseconds before that transaction commits and see branch_name=None.
        Without retry we'd auto-block the child unnecessarily.

        When the parent is clearly mid-claim (in_progress + assigned_to set)
        re-fetch up to 3 times with a 250ms delay before giving up. Total
        worst-case wait is 750ms — well inside the dispatcher's tick budget
        and only paid when the race actually triggers. Real misses (parent
        still pending or unassigned) auto-block immediately as before.
        """
        parent_resp = await client.get(f"{self._api_url}/tasks/{parent_id}")
        if not parent_resp.is_success:
            return None
        parent = parent_resp.json()
        if parent.get("branch_name"):
            return None

        # A coordination/fan-out parent (product, no repo of its own) never gets
        # a branch: the child resolves its own real project and cuts from that
        # project's default branch, not from the parent. Blocking the child on a
        # branch the parent will never have wedges the cell↔Main-PM loop (#17).
        if _is_coordination_task(parent):
            return None

        if parent.get("status") == "in_progress" and parent.get("assigned_to"):
            for _ in range(3):
                await asyncio.sleep(0.25)
                parent_resp = await client.get(f"{self._api_url}/tasks/{parent_id}")
                if not parent_resp.is_success:
                    continue
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
        if complexity not in ("medium", "high") or parent_task_id:
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

        # A coordination task carries a product instead of a repo; only a task
        # with neither is genuinely unroutable.
        if not task.get("project_id") and not _is_coordination_task(task):
            await self._auto_block_task(
                client, task_id, "Task needs a project_id or product_id"
            )
            return f"Task {task_id} needs a project or product"

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

    async def _auto_resume_paused_parent(
        self, client: httpx.AsyncClient, task_id: str
    ) -> None:
        """Resume a paused parent right before its PM is respawned for closure.

        #170: a PM auto-pauses its owned parent on i_am_idle (by design,
        so the closure dispatcher knows to respawn it). Pre-gateway the
        parent was resumed at respawn so the PM landed actionable; the
        gateway refactor dropped that, so the respawned PM had to issue
        ``resume()`` itself — which weak models (minimax) reliably fail,
        wedging the whole chain (smoke-15). Restore the auto-resume:
        paused -> in_progress before spawn so the PM can directly
        submit_up / complete / escalate. Best-effort; a resume failure
        must not block the spawn (the PM can still resume manually).
        """
        try:
            await client.patch(
                f"{self._api_url}/tasks/{task_id}",
                json={"status": "in_progress"},
            )
            logger.info(
                "Auto-resumed paused parent for PM closure respawn",
                task_id=task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to auto-resume paused parent",
                task_id=task_id,
                error=str(e),
            )

    async def _auto_recover_blocked_parent(
        self, client: httpx.AsyncClient, task_id: str
    ) -> None:
        """Recover a blocked parent right before its PM is respawned for closure.

        #177: symmetric to ``_auto_resume_paused_parent`` (#170). The
        closure dispatcher only reaches this point once every descendant
        is terminal, so a parent still ``blocked`` here is an errant /
        stale block (e.g. a child's i_am_blocked propagated, or a PM
        blocked it and never unblocked) — the real dependency is already
        done. #170 auto-resumed only ``paused`` parents, so a ``blocked``
        one wedged the whole chain forever: the respawned PM cannot
        submit_up / complete a blocked parent and must first ``unblock``
        it (needs journal:decision), which weak models never reliably do
        (smoke-19/this run wedged exactly here). ``blocked -> in_progress``
        is lifecycle-valid — it is precisely what ``unblock(restore=True)``
        performs. Best-effort; a failure must not block the spawn (the PM
        can still ``unblock`` manually).
        """
        try:
            await client.patch(
                f"{self._api_url}/tasks/{task_id}",
                json={"status": "in_progress"},
            )
            logger.info(
                "Auto-recovered blocked parent for PM closure respawn",
                task_id=task_id,
            )
        except Exception as e:
            logger.error(
                "Failed to auto-recover blocked parent",
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
        cell_teams = tuple(
            sorted(t.value for t in CELL_TEAMS)
        )  # ("backend", "frontend", "ux_ui")
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

        # A code task that belongs to a CELL is implementation work and routes
        # WITHIN that cell — never to the board, and never escalated to main_pm
        # by keyword. A dev task whose description says "Create & Launch" or
        # "auth/security" is still a dev task; letting the board/main_pm
        # keyword heuristics fire on it is how a cell code task ended up
        # "reviewed" by the board and a PM ended up owning (and deadlocking) a
        # dev code task. The strategic heuristics below apply only to
        # team-less / "all" top-level tasks.
        cell_teams = frozenset(t.value for t in CELL_TEAMS)
        if team in cell_teams:
            if self._has_pm_keywords(text) or complexity == "high":
                return "cell_pm"
            return "dev"

        if self._has_board_keywords(text):
            return "board"

        if (
            self._has_cross_cell_keywords(text)
            or complexity == "high"
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

YOUR JOB: Break this down and delegate to Cell PMs. You do NOT implement
code. You do NOT assign directly to developers — Cell PMs manage their
teams. For purely-PM work (validation, announcements, cross-cell sync) you
may keep the task and work it via your gateway verbs.

== DELEGATION TARGETS ==

- Backend work → be-pm (who delegates to be-dev-1 / be-dev-2)
- Frontend work → fe-pm (who delegates to fe-dev-1 / fe-dev-2)
- UX/UI work → ux-pm (who delegates to ux-dev-1 / ux-dev-2)

NEVER assign to a dev slug from this seat — only Cell PM slugs.

== TOOLS ==

Gateway verbs (already loaded):
- evidence(task_id="{task_id}")            — inspect the task
- triage_all()                             — see what's pending across cells
- note(text, scope='decision', task_id="{task_id}")
    REQUIRED before i_will_plan / complete / escalate
- i_will_plan(task_id="{task_id}", plan="<your detailed plan as a string>")
    claim + record plan + start your own root task
- delegate(parent_task_id="{task_id}", title=..., description=...,
           assigned_to=<one of "be-pm" / "fe-pm" / "ux-pm">,
           team=<one of "backend" / "frontend" / "ux_ui">,
           task_type=<one of "code" / "documentation" / "research" /
                            "planning" / "design" / "administrative">,
           acceptance_criteria=[...],
           estimated_complexity=<one of "low" / "medium" / "high">)
    creates a subtask under your root and assigns it to a Cell PM.
    Use the EXACT enum strings above — invented values like
    "development" or "small" are rejected by the gateway. Repeat
    once per cell that needs work.
- unblock(task_id, restore=True)
- complete(task_id="{task_id}", notes=...)  for root awaiting_pm_review
- escalate_to_ceo(task_id="{task_id}", reason=...) for root tasks
- say(channel, text), dm(recipient, text)
- i_am_idle() — when delegated and waiting

== WORKFLOW ==

1. evidence(task_id="{task_id}")
2. note(scope='decision', task_id="{task_id}",
        text="<plan summary: cells X/Y get subtasks A/B>")
3. i_will_plan(task_id="{task_id}",
               plan="<detailed plan: scope, cell breakdown, sequencing, risks>")
4. delegate(parent_task_id="{task_id}", title="Backend slice of <root>",
            description="What be-pm should coordinate.",
            assigned_to="be-pm", team="backend", task_type="code",
            acceptance_criteria=["c1", "c2"], estimated_complexity="medium")
   — repeat per cell that needs work. ONE subtask per cell; the Cell PM
   breaks it down further.
5. say("#main-pm-board", "Delegated <root> to be-pm/fe-pm — see subtasks")
6. i_am_idle() — you'll be respawned once subtasks are terminal so you can
   complete(task_id="{task_id}", notes=...) or escalate_to_ceo on the root.

== RULES ==

- Never `commit`, never write code, never run `git`. PMs coordinate.
- Never assign a code subtask directly to a developer slug — always to a Cell PM.
- delegate / complete / escalate will fail unless you've logged a journal
  decision for this task — read the `remediate` field on errors.

Start now: evidence(task_id="{task_id}")
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

YOUR JOB: Break this down into concrete subtasks and delegate each to a
developer in your cell. You do NOT code. You do NOT run git. You coordinate.

Available developers in your cell: {dev_options}

== TOOLS ==

Gateway verbs (already loaded):
- evidence(task_id="{task_id}")           — read PR + commits + diff
- triage()                                 — see what your cell needs next
- note(text, scope='decision', task_id="{task_id}")
    REQUIRED before i_will_plan / unblock / complete / escalate
- i_will_plan(task_id="{task_id}", plan="<detailed plan as a string>")
    claim + record plan + start your own cell-PM task
- delegate(parent_task_id="{task_id}", title=..., description=...,
           assigned_to=<dev slug in your cell, e.g. "be-dev-1">,
           team="{team}",
           task_type=<one of "code" / "documentation" / "research" /
                            "planning" / "design" / "administrative">,
           acceptance_criteria=[...],
           estimated_complexity=<one of "low" / "medium" / "high">)
    creates a subtask under your cell-PM task and assigns it to a developer.
    Use the EXACT enum strings above — invented values like
    "development" or "small" are rejected by the gateway.
    Repeat 2 to 5 times for focused subtasks.
- unblock(task_id, restore=True)
    when a dev signals i_am_blocked
- complete(task_id, notes)
    review a SUBTASK in awaiting_pm_review (auto-merges its leaf PR)
- submit_up(task_id="{task_id}", notes=...)
    when YOUR OWN cell-PM task's subtasks are all terminal: opens cell-level
    PR up to Main PM's branch and transitions to awaiting_pm_review.
- escalate_up(task_id, reason)            — to Main PM
- say("{channel}", text), dm(recipient, text)
- i_am_idle() — when delegated and waiting

== WORKFLOW ==

1. evidence(task_id="{task_id}")
2. note(scope='decision', task_id="{task_id}",
        text="<approach>; subtasks: A→{primary_dev}, B→...")
3. i_will_plan(task_id="{task_id}",
               plan="<detailed plan: scope, subtask breakdown, sequencing, risks>")
4. delegate(parent_task_id="{task_id}", title="Add login endpoint",
            description="Implement POST /login that issues a session token.",
            assigned_to="{primary_dev}", team="{team}", task_type="code",
            acceptance_criteria=["c1", "c2"], estimated_complexity="medium")
   — repeat 2 to 5 times for focused subtasks under your cell-PM task.
5. say("{channel}", "Broke down <task>: subtasks created and assigned")
6. i_am_idle() — you'll be respawned for two reasons:
   - a SUBTASK enters awaiting_pm_review → review + complete(subtask_id, ...)
   - all subtasks terminal → submit_up(task_id="{task_id}", notes=...) on YOUR task

== RULES ==

- Never `commit`, never write code, never run `git`. PMs coordinate.
- Subtasks MUST go to a developer slug in YOUR cell, not another cell's PM.
- delegate / complete / submit_up / escalate will fail unless you've logged
  a journal decision for the relevant task — read the `remediate` field.

Start now: evidence(task_id="{task_id}")
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

    async def _reconcile_orphan_claims_on_startup(self) -> None:
        """Roll back tasks left in CLAIMED/IN_PROGRESS without a branch.

        A task in CLAIMED/IN_PROGRESS with ``branch_name IS NULL`` is an
        orphan: ``_finalize_claim`` flushed the status before branch creation
        failed (or before the P0-7 rollback fix landed). The next claim then
        fails non-idempotent on ``git checkout -b`` because the on-disk
        branch may exist while the DB state is stale.

        Opens its own session via the factory; the logic itself lives in
        ``_reconcile_with_service`` so tests can drive it against an
        injected session without the factory dance. Best-effort: if
        reconciliation fails, log and continue — startup must not be
        blocked by a single bad row.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.task import TaskService

        factory = get_session_factory()
        try:
            async with factory() as db:
                svc = TaskService(db)
                await self._reconcile_with_service(svc)
                await db.commit()
        except Exception as exc:
            logger.error("startup reconcile failed; continuing", error=str(exc))

    async def _reconcile_with_service(self, svc: "TaskService") -> None:
        """Inner reconcile loop, parameterised by the TaskService to use.

        Same shape as ``_reap_with_service`` — extracted so tests can
        bypass ``get_session_factory`` and drive the logic directly.
        """
        from roboco.utils.converters import require_uuid

        candidates = await svc.list_in_progress_or_claimed()
        orphans = [t for t in candidates if not t.branch_name]
        if not orphans:
            logger.info("startup reconcile: no orphan claims")
            return
        for t in orphans:
            task_id = require_uuid(t.id)
            try:
                await svc.unclaim_for_reaper(task_id)
                logger.warning(
                    "startup reconcile: orphan claim rolled back",
                    task_id=str(task_id),
                    had_status=str(t.status),
                )
            except Exception as exc:
                logger.error(
                    "startup reconcile: rollback failed",
                    task_id=str(t.id),
                    error=str(exc),
                )

    async def _reap_stale_claims(self) -> None:
        """Release claimed/in_progress tasks whose holder hasn't heart-beat in TTL.

        Closes the "dead container squats task forever" failure mode that
        the schema hinted at (``last_heartbeat_at`` since migration 006) but
        no code enforced. The runtime decision (cutoff, iteration) lives
        here in the orchestrator; the actual UPDATE statements live in
        ``TaskService.unclaim_for_reaper``.

        Opens a fresh per-tick session — short-lived because the reaper
        runs on every dispatch cycle and the work is cheap (one SELECT
        plus N UPDATEs for the typically-empty stale set). Tests that
        need to inject a mock service do so by building an instance via
        ``__new__`` (bypassing this method) and calling
        ``_reap_with_service`` directly.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.task import TaskService

        factory = get_session_factory()
        async with factory() as db:
            svc = TaskService(db)
            await self._reap_with_service(svc)
            await db.commit()

    async def _reap_with_service(self, svc: "TaskService") -> None:
        """Inner reap loop, parameterized by the TaskService to use.

        Wraps each ``unclaim_for_reaper`` in try/except so a single bad row
        doesn't abort the dispatch tick — the reaper must keep ticking even
        if one task's release somehow fails.
        """
        from roboco.utils.converters import require_uuid

        cutoff = datetime.now(UTC) - timedelta(seconds=self._claim_heartbeat_ttl)
        candidates = await svc.list_in_progress_or_claimed()
        for t in candidates:
            ts = t.last_heartbeat_at
            if ts is None or ts < cutoff:
                task_id = require_uuid(t.id)
                try:
                    await svc.unclaim_for_reaper(task_id)
                    logger.warning(
                        "stale claim reaped",
                        task_id=str(task_id),
                        last_heartbeat=ts.isoformat() if ts else None,
                    )
                except Exception as exc:
                    logger.error(
                        "stale-claim reap failed; continuing",
                        task_id=str(task_id),
                        error=str(exc),
                    )

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

        The stale-claim reaper runs first, before any dispatcher tries to
        spawn an agent for a task whose previous holder is dead. Without
        this ordering, the spawn pass could race against a stale claim and
        skip work the reaper would have freed in the same tick.
        """
        self._tick_handled_tasks = set()

        # Free any tasks whose claim went stale before the spawn pass runs.
        # Wrapped because a reaper failure must not block dispatch — the
        # next tick will retry.
        try:
            await self._reap_stale_claims()
        except Exception as e:
            logger.error("Stale-claim reaper failed; continuing tick", error=str(e))

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
                (
                    "claimed_without_agent",
                    self._dispatch_claimed_without_agent(client),
                ),
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

    # Board reviewers. They advise — review + record requirements + escalate —
    # but do not build or delegate. Dispatched once per assigned board task.
    _BOARD_AGENTS: ClassVar[frozenset[str]] = frozenset(
        {
            "product-owner",
            "head-marketing",
        }
    )

    # Use foundation's default; keep the local name for back-compat.
    _PM_RESPAWN_MAX_UNPRODUCTIVE = _AGENT_LOOP_BUDGET.pm_respawn_max_unproductive

    async def _pm_respawn_should_gate(
        self, agent_slug: str, task: dict[str, Any]
    ) -> bool:
        """Return True when the respawn should be skipped (loop detected).

        Tracks (agent_slug, task_id) -> count of consecutive spawns where
        the task's status did not advance. When the task status changes,
        the counter resets. Once the count hits the threshold, the spawn
        is skipped and a warning logged; operators must intervene.

        Tracing-gap reset (Task 13)
        ---------------------------
        With the gateway claim-time gates installed, a rule-following PM
        will hit ``PARENT_NOT_CLAIMED`` (a ``tracing_gap`` envelope) and
        the prompt will tell it to call the prerequisite verb first.
        Each retry leaves the task status unchanged but the agent IS
        making progress through the verb chain. Counting that as a
        strike kills rule-followers.

        Solution: before incrementing on a same-status spawn, check
        ``audit_log`` for a ``gateway.rejected`` row tagged
        ``reason == "tracing_gap"`` from this (agent, task) since the
        last check. If found, reset the counter — the agent followed
        the rules, not stuck.

        Audit lookup is best-effort: any failure falls through to the
        legacy strike behavior so audit problems don't break the gate.
        """
        task_id = task.get("id")
        if not task_id:
            return False
        key = (agent_slug, task_id)
        current_status = task.get("status")
        record = self._pm_respawn_tracker.get(key)
        now = datetime.now(UTC)
        if record is None or record.get("last_status") != current_status:
            self._pm_respawn_tracker[key] = {
                "count": 1,
                "last_status": current_status,
                "last_check": now,
            }
            return False
        # Same status as last spawn — could be a stuck loop OR a
        # rule-following retry. Consult audit before counting.
        if await self._pm_made_rule_following_retry(agent_slug, task_id, record):
            record["count"] = 1
            record["last_check"] = now
            return False
        record["count"] += 1
        record["last_check"] = now
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

    async def _pm_made_rule_following_retry(
        self,
        agent_slug: str,
        task_id: str,
        record: dict[str, Any],
    ) -> bool:
        """Did the agent emit a ``tracing_gap`` envelope since the last check?

        Returns ``False`` for unknown slugs (defensive — the audit query
        needs an agent UUID, and we'd rather fall through to the legacy
        strike behavior than crash). Returns ``False`` if the audit
        lookup raises — observability must never block the gate.
        """
        agent_uuid_str = AGENT_UUIDS.get(agent_slug)
        if not agent_uuid_str:
            return False
        from uuid import UUID

        try:
            agent_uuid = UUID(agent_uuid_str)
            task_uuid = UUID(task_id)
        except (ValueError, TypeError):
            return False
        since = record.get("last_check") or datetime.now(UTC)

        from roboco.services.audit import get_audit_service

        audit = get_audit_service()
        try:
            return await audit.has_recent_tracing_gap(
                agent_id=agent_uuid,
                task_id=task_uuid,
                since=since,
            )
        except Exception as exc:
            logger.debug(
                "audit.has_recent_tracing_gap failed; falling back to strike count",
                agent_slug=agent_slug,
                task_id=task_id,
                error=str(exc),
            )
            return False

    async def _handle_pm_assigned_task(
        self, task: dict[str, Any], assigned_to: str
    ) -> None:
        """Spawn an already-assigned PM agent if it isn't running."""
        agent_slug = self._resolve_agent_slug(assigned_to)
        if agent_slug not in self._PM_AGENTS or self._is_agent_active(agent_slug):
            return
        if await self._pm_respawn_should_gate(agent_slug, task):
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

    async def _handle_board_assigned_task(
        self, task: dict[str, Any], assigned_to: str
    ) -> None:
        """Review an assigned board task with the FULL board (PO + HoM), ONCE each.

        A board/coordination task — especially one with a UI / user-facing
        dimension — must be reviewed by BOTH the Product Owner AND the Head of
        Marketing before it is handed to the CEO. The task is assigned to one
        board agent, but the review is a two-reviewer gate, so this dispatches
        both regardless of which one ``assigned_to`` names.

        Board roles advise: they can triage, record notes, and discuss, but have
        NO verb to claim, plan, delegate, or complete. A respawn cannot advance
        the task — it would just loop — so dispatch is one-shot per (agent, task).
        The board reviews and records requirements; the CEO then approves and
        hands the task to Main PM for delegation to the cells.

        Once BOTH reviewers have finished (each dispatched and no longer active),
        the board-review handoff fires: the task is flagged board-reviewed and a
        single formal CEO notification is emitted so Approve & Start is an
        actionable signal rather than buried chatter.
        """
        # `assigned_to` only gates that this IS a board task; the review itself
        # always involves the whole board, not just the named assignee.
        if self._resolve_agent_slug(assigned_to) not in self._BOARD_AGENTS:
            return
        task_id = str(task.get("id"))
        for board_slug in sorted(self._BOARD_AGENTS):
            await self._dispatch_board_reviewer(board_slug, task_id, task)
        await self._maybe_handoff_board_review_to_ceo(task_id)

    async def _dispatch_board_reviewer(
        self, board_slug: str, task_id: str, task: dict[str, Any]
    ) -> None:
        """One-shot spawn of a single board reviewer for a board task.

        Skips when the reviewer is already running or has already been
        dispatched for this task (board roles have no progression verb, so a
        respawn would loop). Records the (agent, task) pair so the
        review-completion detector can tell which reviewers have run.
        """
        if self._is_agent_active(board_slug):
            return
        key = (board_slug, task_id)
        if key in self._board_dispatched:
            return
        self._board_dispatched.add(key)
        logger.info(
            "Spawning board agent for review",
            task_id=task_id,
            agent_id=board_slug,
        )
        await self.spawn_agent(
            agent_id=board_slug,
            task_id=task["id"],
            initial_prompt=self._build_board_prompt(task),
            git_context=self._task_git_context(task),
        )

    def _board_review_complete(self, task_id: str) -> bool:
        """True once EVERY board reviewer has reviewed and gone idle.

        A reviewer has finished when it was dispatched for this task
        (``_board_dispatched``) and is no longer running (``_is_agent_active``).
        Both PO and HoM must satisfy this before the task is handoff-ready.
        """
        return all(
            (board_slug, task_id) in self._board_dispatched
            and not self._is_agent_active(board_slug)
            for board_slug in self._BOARD_AGENTS
        )

    async def _maybe_handoff_board_review_to_ceo(self, task_id: str) -> None:
        """Unlock the CEO's Approve & Start gate when the board review is done.

        Two one-shot effects fire once BOTH board reviewers have finished:
          1. Persist ``board_review_complete`` on the task. The task stays
             pending (its pending state is what hands it to Main PM on approval),
             so this flag is the only thing that makes the CEO's Approve & Start
             button appear — it never shows on a board task the board hasn't
             finished reviewing.
          2. Emit an ack-required APPROVAL notification to the CEO. Board agents
             only post channel dialogue + journal notes during review, which
             left the CEO with no actionable signal; this is that signal.

        Fires at most once per task; a failure clears the guard so a later tick
        retries, and never blocks the dispatch loop.
        """
        if task_id in self._board_review_ceo_notified:
            return
        if not self._board_review_complete(task_id):
            return
        self._board_review_ceo_notified.add(task_id)
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.notification import NotificationService
        from roboco.services.task import TaskService

        try:
            async with get_db_context() as db:
                await TaskService(db).mark_board_review_complete(UUID(task_id))
                await db.commit()
            await NotificationService().send_board_review_complete_notification(
                task_id=task_id,
            )
        except Exception as exc:
            # Don't wedge dispatch on a failure; allow a retry by clearing the
            # one-shot guard so a later tick can re-run the handoff.
            self._board_review_ceo_notified.discard(task_id)
            logger.warning(
                "Failed to hand board-review completion to CEO",
                task_id=task_id,
                error=str(exc),
            )
            return
        logger.info(
            "Board review complete — CEO Approve & Start unlocked",
            task_id=task_id,
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

        # Board work is a two-reviewer gate (PO + Head of Marketing), not a
        # single-assignee claim. Routing only ever names one board agent
        # (product-owner), so claiming + spawning that one here would leave the
        # Head of Marketing out (finding #4). Delegate to the board handler,
        # which dispatches BOTH reviewers one-shot and leaves the task pending
        # for the CEO's Approve & Start. ``agent_id`` is the routed board slug.
        if routing == "board":
            await self._handle_board_assigned_task(task, agent_id)
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
                if self._resolve_agent_slug(assigned_to) in self._BOARD_AGENTS:
                    await self._handle_board_assigned_task(task, assigned_to)
                else:
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

    @staticmethod
    def _coerce_heartbeat(value: Any) -> datetime | None:
        """Normalize ``last_heartbeat_at`` to an aware UTC datetime.

        The dispatcher reads tasks via the HTTP API, which serializes
        datetimes as ISO-8601 strings; direct service callers (and tests)
        may pass ``datetime`` objects. Anything else is treated as
        absent so a malformed value can't accidentally arm the gate.
        """
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value)
            except ValueError:
                return None
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        return None

    def _is_recently_paused(self, task: dict[str, Any]) -> bool:
        """A paused task whose heartbeat is fresher than the stale cutoff.

        Closes the ``i_am_idle`` vs closure-respawn race (audit C12):
        ``i_am_idle`` auto-pauses in-flight tasks and then sets the agent
        IDLE. If the dispatcher ticks between those two writes it sees a
        paused parent and would spawn the closure PM against a session
        that is mid-shutdown. A fresh ``last_heartbeat_at`` (newer than
        ``settings.claim_stale_seconds``) is the signal that the agent
        was alive moments ago and a respawn now would race the existing
        session. Genuinely-stale paused tasks (or tasks with no heartbeat
        recorded) fall through and follow the regular closure path.
        """
        if task.get("status") != "paused":
            return False
        last_hb = self._coerce_heartbeat(task.get("last_heartbeat_at"))
        if last_hb is None:
            return False
        cutoff = datetime.now(UTC) - timedelta(seconds=self._claim_heartbeat_ttl)
        return last_hb > cutoff

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

        if self._is_recently_paused(task):
            logger.debug(
                "Skipping closure spawn for recently-paused parent",
                task_id=task_id,
                last_heartbeat_at=task.get("last_heartbeat_at"),
            )
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

        # #170: the parent auto-paused when its PM idled (by design). Resume
        # it before respawn so the PM lands actionable (in_progress) and can
        # directly submit_up / complete / escalate — pre-gateway behaviour the
        # gateway refactor dropped, which wedged smoke-15 (minimax never
        # issued resume() itself).
        # #177: a parent that is `blocked` at closure (all descendants
        # terminal) is an errant/stale block — recover it symmetrically so
        # the chain can't wedge forever waiting for a PM to manually unblock.
        parent_status = task.get("status")
        if parent_status == "paused":
            await self._auto_resume_paused_parent(client, task_id)
        elif parent_status == "blocked":
            await self._auto_recover_blocked_parent(client, task_id)

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
        """Prompt for PM closing their own parent task (subtasks terminal)."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        subtask_summary = "\n".join(
            f"  - {st.get('title', 'Untitled')} ({st.get('status', 'unknown')})"
            for st in subtasks
        )

        is_root = not task.get("parent_task_id")
        project_slug = task.get("project_slug", "")

        if is_root:
            target_line = (
                "submit_up promotes to awaiting_ceo_approval; the CEO reviews "
                "and merges to master. You do NOT merge to master yourself."
            )
            submit_step = (
                f'4. submit_up(task_id="{task_id}",\n'
                '       notes="<aggregate summary: what shipped across the '
                'cells, evidence, risk callouts>")\n'
                "   — promotes to awaiting_ceo_approval. "
                "CEO is the final approver."
            )
        else:
            target_line = (
                "submit_up opens your cell-level PR into the parent task's "
                "branch and transitions you to awaiting_pm_review for the "
                "parent PM."
            )
            submit_step = (
                f'4. submit_up(task_id="{task_id}",\n'
                '       notes="<cell summary: what your cell shipped, '
                'evidence>")\n'
                "   — opens cell-level PR up to the parent's branch and "
                "transitions to awaiting_pm_review."
            )

        return f"""You are closing YOUR OWN parent task. All subtasks are
terminal — promote the merged work one level up the hierarchy.

TASK: {task_id}
TITLE: {title}
TEAM: {team}
PROJECT: {project_slug}
ROOT TASK: {"yes" if is_root else "no"}

SUBTASK SUMMARY:
{subtask_summary}

PROMOTION TARGET: {target_line}

== PM CLOSURE WORKFLOW ==

1. evidence(task_id="{task_id}")
   — review aggregate state, every acceptance criterion, and each
   subtask's terminal status. Returns the inline diff for your branch
   (all merged subtask work).

2. If any subtask is still in awaiting_pm_review, review + close it FIRST:
   - APPROVE leaf: complete(task_id="<subtask_id>",
                            notes="<merge rationale>")
     (auto-merges the leaf PR into your cell branch).
   - NEEDS REWORK: leave a clear note(scope='decision',
     task_id="<subtask_id>", text="...") and rely on the dispatcher to
     respawn the dev for revision.

3. note(scope='decision', task_id="{task_id}",
        text="Closure: {title} — <rationale, AC coverage, risks>")
   — REQUIRED before submit_up().

{submit_step}

5. i_am_idle()

Never `commit`, never write code, never run `git`. PMs coordinate.
"""

    def _get_prompt_for_agent(self, agent_slug: str, task: dict[str, Any]) -> str:
        """Get the prompt appropriate to the agent's ACTUAL role (#19).

        A respawn must hand each role the prompt it can act on — a PM or board
        agent handed the developer prompt is told to write code and call verbs
        it does not own. Reuses the same per-role prompt builders the role
        dispatchers use so a respawn matches a fresh dispatch:

          developer      → dev prompt
          qa             → QA prompt
          documenter     → doc prompt
          cell_pm        → cell-PM triage prompt
          main_pm        → main-PM triage prompt
          product_owner  → board-review prompt
          head_marketing → marketing prompt for a marketing task, else board
          auditor        → audit prompt

        Unknown roles fall back to the dev prompt (safe default for an
        executable task).
        """
        role = get_agent_role(agent_slug)
        # head_marketing is the one role whose prompt depends on the task, so it
        # is resolved before the static role→builder table.
        if role == "head_marketing":
            if task.get("team") == "marketing":
                return self._build_marketing_prompt(task)
            return self._build_board_prompt(task)
        builders: dict[str, Callable[[dict[str, Any]], str]] = {
            "developer": self._build_dev_prompt,
            "qa": self._build_qa_prompt,
            "documenter": self._build_doc_prompt,
            "cell_pm": self._build_pm_triage_prompt,
            "main_pm": self._build_main_pm_triage_prompt,
            "product_owner": self._build_board_prompt,
            "auditor": lambda _task: self._build_audit_prompt(),
        }
        builder = builders.get(role, self._build_dev_prompt)
        return builder(task)

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

        # Role/task_type mismatch guard (audit D-49). The dispatcher
        # previously trusted whatever ``assigned_to`` named, so a
        # documentation task accidentally assigned to a developer agent
        # would silently spawn the dev. Reject the dispatch if the
        # assignee's role doesn't match the task type — the PM that
        # mis-assigned needs to fix it before any agent runs.
        # Tasks owned by PM/board/QA roles aren't this dispatcher's lane;
        # `_dispatch_pm_work` and the QA-pool path own them. Silently skip
        # so the warning only fires on actual dev/doc misassignments.
        if agent_slug:
            assignee_role = get_agent_role(agent_slug)
            if assignee_role not in ("developer", "documenter", "unknown"):
                return
            if not self._dev_dispatch_role_matches(task, agent_slug):
                logger.warning(
                    "dev dispatch: role/task_type mismatch — skipping spawn",
                    task_id=task.get("id"),
                    task_type=task.get("task_type"),
                    assignee_slug=agent_slug,
                    assignee_role=assignee_role,
                )
                return

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

    @staticmethod
    def _dev_dispatch_role_matches(task: dict[str, Any], agent_slug: str) -> bool:
        """Return True if the assignee role matches the task's task_type.

        Dev dispatcher only spawns developer-role agents. A doc/qa task
        assigned to a dev (or vice versa) should be flagged, not silently
        spawned. Returns True when the type is unknown or the assignee role
        is unknown — the validation runs as a guard, not a strict gate, so
        an unknown classification doesn't block work that would otherwise
        proceed.
        """
        role = get_agent_role(agent_slug)
        if role == "unknown":
            return True
        task_type = task.get("task_type")
        if task_type == "documentation":
            return role == "documenter"
        # `code` / `research` / `planning` / `administrative` / `design` all
        # route through dev or PM; only the doc-task case is unambiguous.
        return role == "developer"

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

    def _blocker_resolver_slug(self, task: dict[str, Any]) -> str | None:
        """Pick the agent that should be dispatched to unblock ``task``.

        The unblock content gate (note/unblock) is assignee-only: the
        dispatched agent must be the task's CURRENT ``assigned_to``, or its
        required pre-unblock decision note returns not_authorized and the
        orchestrator respawns it forever (#17 livelock — a task escalated to
        Main PM kept respawning the ex-assignee cell PM, which could not author
        the note). So whenever the blocked task carries an assignee that is a
        PM or board role, dispatch THAT assignee. Only a task with no PM/board
        assignee (e.g. still held by the dev who raised i_am_blocked) falls back
        to the cell PM for its team.
        """
        assignee_uuid = task.get("assigned_to") or task.get("claimed_by")
        if assignee_uuid:
            assignee_slug = self._resolve_agent_slug(str(assignee_uuid))
            if assignee_slug in self._PM_AGENTS or assignee_slug in self._BOARD_AGENTS:
                return assignee_slug
        team = task.get("team")
        if team not in ("backend", "frontend", "ux_ui"):
            return None
        return self._select_agent_for_cell(team, "pm")

    async def _dispatch_blocker_work(self, client: httpx.AsyncClient) -> None:
        """
        Dispatch blocker resolution to the task's current unblock authority.

        Monitors: blocked tasks
        Spawns: the task's current PM/board assignee, else the cell PM
        """
        tasks = await self._fetch_tasks(client, "blocked")

        for task in tasks:
            # HITL-blocked tasks wait for a human; never spawn an agent on them.
            if self._is_hitl_blocked(task):
                continue

            agent_id = self._blocker_resolver_slug(task)
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

    def _claimed_task_needs_agent(self, task: dict[str, Any]) -> str | None:
        """Return the assignee slug to (re)spawn for an agentless claimed task.

        #19: a task left CLAIMED/IN_PROGRESS with an assignee but no running
        container (e.g. a reassignment that didn't spawn) is invisibly stuck —
        only PENDING tasks get fresh dispatch, and the heartbeat reaper can't
        see it because the claim seeded a fresh heartbeat. Returns the assignee
        slug when the task has sat past the grace window with no active agent;
        ``None`` when it is healthy, too fresh, or HITL-blocked.
        """
        if self._is_hitl_blocked(task):
            return None
        owner_uuid = task.get("assigned_to") or task.get("claimed_by")
        if not owner_uuid:
            return None
        agent_slug = self._resolve_agent_slug(str(owner_uuid))
        # The assignee is running, and on THIS task — healthy.
        instance = self._instances.get(agent_slug)
        if instance is not None and instance.state == AgentState.ACTIVE:
            return None
        # Grace window: a just-claimed task whose spawn is still in flight must
        # not be churned. _time_in_state under-counts (any update bumps it),
        # which biases toward "agent is working" — exactly the safe direction.
        age = self._time_in_state(task)
        grace = settings.claimed_no_agent_grace_seconds
        if age is None or age.total_seconds() < grace:
            return None
        return agent_slug

    async def _dispatch_claimed_without_agent(self, client: httpx.AsyncClient) -> None:
        """(Re)spawn or release claimed/in_progress tasks that have no agent (#19).

        Net for the invisible-stuck case the other dispatchers miss: a task
        held CLAIMED/IN_PROGRESS by an assignee with no running container. If
        the assignee is a known spawnable agent, respawn it on the task; if not
        (unknown slug — e.g. a stale UUID), release the claim to PENDING so the
        normal routing reclaims it with a role match.

        Throttle: spawns at most ONE container per tick (``break`` after the
        first respawn), matching every sibling dispatcher. A restart leaves
        many agentless claims at once; without the cap this single tick would
        burst-spawn a container for every one of them. The release-to-pending
        path spawns nothing, so it does not consume the per-tick spawn budget
        and keeps draining stale claims.
        """
        tasks = await self._fetch_tasks(client, ["claimed", "in_progress"])
        for task in tasks:
            task_id = task.get("id")
            if self._is_task_handled_this_tick(task_id):
                continue
            agent_slug = self._claimed_task_needs_agent(task)
            if agent_slug is None:
                continue
            if get_agent_role(agent_slug) in (None, "unknown"):
                # Unknown assignee — no agent to spawn; release for re-dispatch.
                await self._release_claim_to_pending(str(task_id))
                continue
            logger.warning(
                "Claimed/in_progress task has no running agent; respawning assignee",
                task_id=task_id,
                agent=agent_slug,
                status=task.get("status"),
            )
            await self.spawn_agent(
                agent_id=agent_slug,
                task_id=str(task_id),
                initial_prompt=self._get_prompt_for_agent(agent_slug, task),
                git_context=self._task_git_context(task),
            )
            break

    async def _release_claim_to_pending(self, task_id: str) -> None:
        """Release a stuck claim back to PENDING via the lifecycle-safe path.

        Reuses ``TaskService.unclaim_for_reaper`` (claimed/in_progress ->
        pending, clears assignee + work session) so the state machine records
        the transition rather than a raw status PATCH. Opens its own short-lived
        session, mirroring ``_reap_stale_claims``.
        """
        from roboco.db.base import get_session_factory
        from roboco.services.task import TaskService
        from roboco.utils.converters import require_uuid

        try:
            factory = get_session_factory()
            async with factory() as db:
                svc = TaskService(db)
                await svc.unclaim_for_reaper(require_uuid(task_id))
                await db.commit()
            logger.warning(
                "Released agentless claim to pending for re-dispatch",
                task_id=task_id,
            )
        except Exception as exc:
            logger.error(
                "Failed to release agentless claim; will retry next tick",
                task_id=task_id,
                error=str(exc),
            )

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

    async def _check_sla_for_task(
        self,
        client: httpx.AsyncClient,
        task: dict[str, Any],
        status: str,
    ) -> None:
        """Check one task's SLA; escalate if exceeded. No-ops on missing data."""
        from roboco.enforcement.task_lifecycle import sla_seconds_for

        assigned = task.get("assigned_to")
        if not assigned:
            return
        assigned_slug = self._resolve_agent_slug(assigned)
        role = get_agent_role(assigned_slug or "")
        sla = sla_seconds_for(role, status)
        if sla is None:
            return
        age = self._time_in_state(task)
        if age is None or age.total_seconds() < sla:
            return
        task_id = task.get("id")
        if not task_id:
            return
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

    async def _detect_sla_exceeded(self, client: httpx.AsyncClient) -> None:
        """Auto-escalate tasks that exceeded their per-role SLA.

        Uses ROLE_STATE_SLA_KEYS in enforcement/task_lifecycle.py. Dev tasks
        stuck in `in_progress`/`verifying`, QA tasks in `claimed`, doc tasks
        in `claimed`, and cell-PM tasks in `claimed` all get a soft bump so
        work doesn't silently rot.
        """
        from roboco.enforcement.task_lifecycle import ROLE_STATE_SLA_KEYS

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
                await self._check_sla_for_task(client, task, status)

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
            "Escalating — agent should call escalate_up() "
            "or unclaim()."
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
        # A branch only exists once a task is claimed; a coordination task does
        # no git at all. A pending, never-claimed code task therefore has no
        # branch by design — flagging that here auto-blocked tasks before their
        # first dispatch (#18). Only flag a missing branch when the task is in a
        # state where it should already own one.
        if not task.get("branch_name") and _branch_is_expected(task):
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
        is_low_complexity = complexity not in ("medium", "high")
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
            "NEEDS_PLAN": f"""## NEXT STEP: Claim + Plan + Start

Call i_will_work_on(task_id="{task_id}",
    plan="<approach, ordered steps, risks, open questions>").

This single verb claims the task, records your plan, and transitions
to in_progress.
""",
            "READY_TO_START": f"""## NEXT STEP: Start Work

Call i_will_work_on(task_id="{task_id}", plan="<your plan as a string>")
to begin.
""",
            "EXECUTING": """## IN PROGRESS

Continue development. Required gates before i_am_done() will succeed
(enforced server-side — `remediate` tells you what's missing):
1. commit("<type(scope): subject, >=20 chars>")
   — makes the git commit, auto-prefixes task ID, records progress.
   Repeat per meaningful chunk.
2. note(scope='decision'|'learning'|'reflect', task_id="...", text=...)
   as you make trade-offs.

When acceptance criteria are met, call
open_pr(task_id="...") to push your branch and open the PR,
then i_am_done(task_id="...", notes="<self-verification summary>")
to submit for QA review.

If you hit something you can't unblock yourself:
i_am_blocked(task_id="...",
    reason="<blocked_external|low_context|...>").
""",
            "REVISION_REQUIRED": f"""## REVISION REQUESTED

QA or PM requested changes:
1. evidence(task_id="{task_id}") — read qa_notes / pm_notes / inline diff
2. i_will_work_on(task_id="{task_id}",
   plan="<revised plan addressing each issue>")
3. commit() the fixes, then
   i_am_done(task_id="{task_id}", notes="<what was fixed>")
""",
            "VERIFYING": f"""## SELF-VERIFICATION

Run the project's quality checks against acceptance criteria:
1. Run tests, lint, type checks in your workspace.
2. evidence(task_id="{task_id}") — sanity-check inline diff + commits.
3. If everything passes:
   i_am_done(task_id="{task_id}", notes="<verification summary>")
   — chains submit_verification + push + create_pr + submit_qa.
4. If issues found: commit() the fixes and retry.
""",
        }
        return instructions.get(
            state, f'Call evidence(task_id="{task_id}") to check status.'
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

Start by calling evidence(task_id="{task_id}") for full details and acceptance criteria.

When out of work: i_am_idle().
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

== QA WORKFLOW ==

1. claim_review(task_id="{task_id}")
   — assigns the QA seat; returns inline diff + PR + commits as evidence.
   The PR is already open (dev opened it before submitting QA);
   review on GitHub if you need more context.
2. Review the implementation against EVERY acceptance criterion.
   Run/read tests; sanity-check the diff for regressions, security,
   and scope creep.
3. Decide:
   - PASS: pass(task_id="{task_id}",
            notes="<>=80 chars: what you verified, which AC, evidence>")
     — transitions awaiting_qa → awaiting_documentation.
   - FAIL: fail(task_id="{task_id}",
            issues=["concrete issue 1", "concrete issue 2", ...])
     — transitions to needs_revision; each issue must be specific and
     actionable.
4. note(scope='reflect'|'learning', task_id="{task_id}", text=...)
   for anything worth flagging.
5. give_me_work() to pick up the next QA item,
   or i_am_idle() if the queue is empty.
"""

    def _build_doc_prompt(self, task: dict[str, Any]) -> str:
        """Build initial prompt for a documenter."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        return f"""A task is ready for documentation. The dev's PR is already open
— you're documenting alongside the QA-passed branch.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}

== DOC WORKFLOW ==

1. claim_doc_task(task_id="{task_id}")
   — assigns the doc seat and opens your workspace on the task's branch.
2. evidence(task_id="{task_id}") — read dev handoff notes, qa_notes,
   and the inline diff so the docs reflect what actually shipped.
3. Write/update docs in your workspace: README sections, API references,
   code comments, migration notes, or new docs files as the change requires.
4. commit("docs(scope): <subject, >=20 chars>") per logical doc chunk
   — auto-prefixes the task ID and stages tracked changes.
5. i_documented(task_id="{task_id}",
   notes="<>=20 chars: what you documented and where>",
   files=["docs/foo.md", "README.md", ...])
   — transitions awaiting_documentation → awaiting_pm_review.
6. give_me_work() for the next doc item,
   or i_am_idle() if the queue is empty.
"""

    def _build_pm_review_prompt(self, task: dict[str, Any]) -> str:
        """Prompt for PM reviewing a SUBTASK in awaiting_pm_review."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        team = task.get("team", "unknown")

        return f"""A SUBTASK in your cell is awaiting your PM review.
It has passed QA and documentation; the leaf PR is open and ready to merge.

TASK ID: {task_id}
TITLE: {title}
TEAM: {team}

== PM REVIEW WORKFLOW (leaf subtask) ==

1. evidence(task_id="{task_id}")
   — review PR, commits, inline diff, dev_notes, qa_notes, doc files.
2. Spot-check that:
   - every acceptance criterion is satisfied,
   - QA's pass notes line up with the actual diff,
   - docs reflect what shipped.
3. note(scope='decision', task_id="{task_id}",
        text="<approve rationale or rejection reason>")
   — REQUIRED before complete().
4. Decide:
   - APPROVE: complete(task_id="{task_id}", notes="<merge rationale>")
     — auto-merges the leaf PR and finalizes the subtask.
   - NEEDS REWORK: leave a clear note(scope='decision', text="...") and
     rely on the dispatcher to respawn the dev for revision.
     Use escalate_up only if the issue is truly outside your cell.
5. give_me_work() / triage() for the next item, or i_am_idle().

Never `commit`, never write code, never run `git`. PMs coordinate.
"""

    def _build_board_prompt(self, task: dict[str, Any]) -> str:
        """Prompt for a board agent (Product Owner / Head of Marketing) to
        review and SHAPE a strategic task. Board roles advise — they do not
        build, code, or delegate."""
        task_id = task.get("id", "unknown")
        title = task.get("title", "Untitled")
        description = task.get("description", "No description")

        return f"""\
You are on the Board. This strategic task is under board review.

TASK: {task_id}
TITLE: {title}
DESCRIPTION: {description}

THE BOARD REVIEWS AS A PAIR: the Product Owner AND the Head of Marketing both
review every board task before it reaches the CEO. The Product Owner owns
product requirements + acceptance scope; the Head of Marketing owns the UX /
user-facing / positioning dimension. The CEO only gets the handoff after BOTH
of you have recorded a review.

YOUR ROLE: review and shape this work. You do NOT build, code, claim, or
delegate — those verbs are not yours. Your deliverable is a recorded review.

== WHAT TO DO ==

1. triage()
     — see your board-level work and context.
2. note(text="<the product requirements and acceptance criteria you expect, the
        scope, the must-haves, and what 'done' looks like — Head of Marketing:
        the UX, user-facing impact, and how the feature is positioned>",
        scope='decision', task_id="{task_id}")
     — this recorded review is how the CEO and Main PM act on your input.
3. say(...) in your board channel to flag UX, positioning, or risk concerns and
     to coordinate with your fellow board reviewer.
4. i_am_idle()
     — when your review is recorded. Once both board reviewers are done, the
       CEO is notified the task is ready for Approve & Start, then routes it to
       Main PM for delegation to the cells; you do NOT hand it off yourself.

Do NOT attempt to claim, plan, complete, or delegate — the gateway will reject
those, and a substantive recorded note IS your job here.
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

1. Review the task details above (full acceptance criteria arrive in your
   briefing / the give_me_work response)
2. Execute the marketing task (content, campaigns, research, etc.)
3. Coordinate with Product Owner or Main PM if needed
4. Call i_am_done() when done
5. Call give_me_work() to check for more marketing work
6. If no more work, call i_am_idle() to shutdown gracefully
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
4. Once resolved, call unblock("{task_id}") to release the task back to the developer
5. Call triage() to check for other blocked tasks in your cell
6. If no more blockers, call i_am_idle() to shutdown gracefully
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

1. Acknowledge the notification with notify_ack("{notif_id}")
2. Assess the escalation and determine action needed
3. Communicate decisions via appropriate channels
4. If this requires further escalation, use escalate_up()
5. When resolved, call triage() for other work
6. If no more work, call i_am_idle() to shutdown gracefully
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
2. If related to a task, use the task context provided in your briefing
3. Make your decision and communicate it
4. Acknowledge with notify_ack("{notif_id}")
5. Call triage() for other work
6. If no more work, call i_am_idle() to shutdown gracefully
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
5. Call i_am_idle() when complete
"""

        return """Periodic AUDIT requested.

Your job:

1. Review recent activity across all cells
2. Check quality metrics (QA pass/fail rates, blocker frequency, etc.)
3. Identify any concerns or patterns
4. Compile audit report for CEO
5. Call i_am_idle() when complete
"""

    def _build_a2a_prompt(self, notification: dict[str, Any]) -> str:
        """Build initial prompt for handling an A2A (Agent-to-Agent) request.

        Reads `priority` directly off the notification row (set by
        NotificationService.send_a2a_notification). Pre-Phase-3 this
        consumed a non-existent `metadata.urgent` and always rendered
        urgency_note=False; the column-level priority is now the source
        of truth.
        """
        notif_id = notification.get("id", "unknown")
        from_agent = notification.get("from_agent", "unknown")
        body = notification.get("body", "No message provided")
        related_task_id = notification.get("related_task_id")
        metadata = notification.get("metadata", {})
        skill = metadata.get("skill", "general")
        priority_raw = notification.get("priority", "normal")

        # URGENT gets the bold attention-grabber; HIGH gets a quieter
        # "higher priority" hint; NORMAL gets no prefix.
        if priority_raw == "urgent":
            urgency_note = "**URGENT** - This request has priority.\n\n"
        elif priority_raw == "high":
            urgency_note = "**HIGH PRIORITY** - Please handle promptly.\n\n"
        else:
            urgency_note = ""
        task_note = f"RELATED TASK: {related_task_id}\n" if related_task_id else ""

        return f"""You have received an A2A (Agent-to-Agent) REQUEST.

{urgency_note}FROM: {from_agent}
SKILL: {skill}
{task_note}
REQUEST:
{body}

Your job:

1. Acknowledge the notification with notify_ack("{notif_id}")
2. Process the request using your {skill} capabilities
3. Respond to {from_agent} using dm("{from_agent}", ...)
4. If you need task context, it is provided in your briefing for the related task
5. When done, call give_me_work() for other work
6. If no more work, call i_am_idle() to shutdown gracefully
"""

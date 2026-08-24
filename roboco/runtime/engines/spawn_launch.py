"""Auto-extracted engine mixin -- see decomp/extract.py. Method bodies below are
moved verbatim from AgentOrchestrator (family: spawn_launch)."""

from __future__ import annotations

import asyncio
import contextlib
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx
from fastapi import status as http_status

from roboco.agents_config import (
    ALL_DOCS,
    get_agent_role,
    get_agent_team,
)
from roboco.config import settings
from roboco.foundation.identity import (
    is_human_only_role,
    is_worktree_author_role,
    role_for_slug_or_none,
)
from roboco.models.base import ModelProvider
from roboco.models.runtime import (
    ROLE_EFFORT_MAP,
    ROLE_MODEL_MAP,
    AgentInstance,
    OrchestratorAgentConfig,
    SpawnGitContext,
)
from roboco.runtime.compose_labels import compose_label_args
from roboco.runtime.orchestrator import (
    _AUTO_BLOCK_SKIP_STATUSES,
    AGENT_BASE_IMAGE,
    AGENT_IMAGES,
    AGENT_NETWORK,
    CLAUDE_AUTH_HOST_PATH,
    DATA_HOST_PATH,
    GATEWAY_ENABLED_ROLES,
    PROJECT_HOST_PATH,
    AgentConfig,
    AgentReadinessError,
    AgentState,
    _agent_cwd_path,
    _agent_workspace_path,
    _agent_worktree_path,
    _branch_is_expected,
    _build_manifest_for_agent,
    _cell_workspace_path,
    _is_coordination_task,
    _qualify_agent_image,
    _read_project_slug,
    _resolve_agent_cli_model,
    _resolve_project_slug_from_git_context,
    _system_api_headers,
    get_agent_image,
    is_unattributed_delivery_spawn,
    logger,
)
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.llm.providers import AgentProvider, ProviderRegistry

    # Also bound into this module's real (non-TYPE_CHECKING) globals at
    # runtime by orchestrator.py, right after the class is defined -- see
    # its bottom-of-file comment.
    from roboco.runtime.orchestrator import (
        AgentOrchestrator,
    )
    from roboco.services.llm import AgentRoute


if TYPE_CHECKING:
    from roboco.runtime.engines._types import AgentOrchestratorSelf as _Base
else:
    _Base = object


class SpawnLaunchEngine(_Base):
    """Mixin holding the "spawn_launch" methods moved out of AgentOrchestrator."""

    # Re-declared (not just inherited from _Base above): mypy's Protocol
    # attribute inference cannot determine the type of an inherited member
    # that a method both reads AND assigns within the same method body
    # (read-then-write in one scope; see _ensure_provider_registry below)
    # without a bare re-declaration directly on the concrete class.
    if TYPE_CHECKING:
        _provider_registry: ProviderRegistry | None

    async def _ensure_agent_image(self, agent_id: str | None = None) -> None:
        """Ensure the agent Docker images are present.

        Local mode (no ``agent_image_registry``) builds the base image first,
        then the role-specialized image, from ``docker/agent-*.Dockerfile``.
        Registry mode pulls the pre-built images instead. Idempotent — skips
        anything already present locally.
        """
        # Determine build context
        if PROJECT_HOST_PATH:
            build_context = PROJECT_HOST_PATH
            docker_dir = f"{PROJECT_HOST_PATH}/docker"
        else:
            build_context = str(self.project_root)
            docker_dir = str(self.project_root / "docker")

        # Always ensure base image exists
        await self._ensure_image_present(
            AGENT_BASE_IMAGE,
            f"{docker_dir}/agent-base.Dockerfile",
            build_context,
        )

        # Ensure the role-specialized image if this agent uses one
        if agent_id:
            bare = AGENT_IMAGES.get(agent_id, AGENT_BASE_IMAGE)
            if bare != AGENT_BASE_IMAGE:
                # Map the bare image name to its dockerfile
                dockerfile_map = {
                    "roboco-agent-pm": "agent-pm.Dockerfile",
                    "roboco-agent-dev-be": "agent-dev-be.Dockerfile",
                    "roboco-agent-dev-fe": "agent-dev-fe.Dockerfile",
                    "roboco-agent-qa-be": "agent-qa-be.Dockerfile",
                    "roboco-agent-qa-fe": "agent-qa-fe.Dockerfile",
                    "roboco-agent-doc": "agent-doc.Dockerfile",
                    "roboco-agent-ux": "agent-ux.Dockerfile",
                    "roboco-agent-prompter": "agent-prompter.Dockerfile",
                    "roboco-agent-secretary": "agent-secretary.Dockerfile",
                    "roboco-agent-pr-reviewer": "agent-pr-reviewer.Dockerfile",
                }
                dockerfile = dockerfile_map.get(bare)
                if dockerfile:
                    await self._ensure_image_present(
                        bare,
                        f"{docker_dir}/{dockerfile}",
                        build_context,
                    )

    @staticmethod
    def _safe_agent_path_segment(agent_id: str) -> str:
        """Return ``agent_id`` if it is safe as a single path segment, else raise.

        ``agent_id`` reaches the grok usage dir from request-facing call sites, so
        it must not be able to traverse the path. Reject every traversal vector —
        empty, ``.`` / ``..``, a ``/`` or ``\\`` separator, or an embedded NUL —
        rather than stripping it; the orchestrator only ever assigns plain
        slug / uuid ids, none of which contain these.
        """
        if (
            not agent_id
            or agent_id in {".", ".."}
            or "/" in agent_id
            or "\\" in agent_id
            or "\x00" in agent_id
        ):
            raise ValueError(f"unsafe agent id for a filesystem path: {agent_id!r}")
        return agent_id

    async def _ensure_image_present(
        self, bare_image: str, dockerfile_path: str, build_context: str
    ) -> None:
        """Ensure one agent image is present locally.

        Pulls it (registry mode) or builds it from its Dockerfile (local mode)
        when missing; no-op if already present.
        """
        image = _qualify_agent_image(bare_image)
        # Check if image exists
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "image",
            "inspect",
            image,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode == 0:
            return

        if settings.agent_image_registry:
            # Registry mode: pull the pre-built image; never build from source
            # (a deployment running pre-built images has no build context).
            logger.info("Pulling agent image...", image=image)
            proc = await asyncio.create_subprocess_exec(
                "docker",
                "pull",
                image,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError(f"Failed to pull image {image}: {stderr.decode()}")
            logger.info("Agent image pulled", image=image)
            return

        logger.info("Building Docker image...", image=image)
        proc = await asyncio.create_subprocess_exec(
            "docker",
            "build",
            "-t",
            image,
            "-f",
            dockerfile_path,
            build_context,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to build image {image}: {stderr.decode()}")
        logger.info("Docker image built successfully", image=image)

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
        from roboco.models.env_branches import head_branch

        try:
            async with get_db_context() as db:
                result = await db.execute(
                    select(ProjectTable)
                    .where(ProjectTable.is_active.is_(True))
                    .order_by(ProjectTable.created_at.asc())
                    .limit(1)
                )
                project = result.scalar_one_or_none()
                if project is None or not project.slug:
                    return None
                return SpawnGitContext(
                    project_slug=project.slug,
                    branch_name=head_branch(project),
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
                ctx = SpawnGitContext(
                    project_slug=project_slug, branch_name=branch_name
                )
                if branch_name and task_id:
                    ctx.task_short_id = str(task_id)[:8]
                return ctx
        except Exception as e:
            logger.warning(
                "Could not derive git context from task_id",
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
        # agent_id lands in per-agent settings/prompt/briefing filenames below;
        # re-assert single-segment safety at the proximate write site.
        self._safe_agent_path_segment(agent_id)
        project_slug = self._resolve_project_slug(git_context, agent_id, task_id)
        ambient = await self._resolve_conventions_ambient(project_slug, task_id)
        blueprint_path = self._generate_composed_prompt(agent_id, ambient=ambient)
        canonical_role = get_agent_role(agent_id)
        team = get_agent_team(agent_id) or "backend"

        # Resolve the provider route for this agent. Caller-supplied `model`
        # wins (dispatcher overrides, tests). Otherwise the routing service
        # resolves (agent_slug | role+complexity | role | global) assignments,
        # falling back internally to `ROLE_MODEL_MAP` when no rows exist — so
        # a fresh deployment with an empty `model_assignments` table behaves
        # exactly as before.
        route = await self._resolve_agent_route(agent_id, task_id)
        if not model:
            model = route.model_name

        cell_workspace_path = _cell_workspace_path(project_slug, team)
        # The agent's edit scope + container cwd: the per-task worktree when
        # the task carries a branch (F123), else the clone root. Routed through
        # _agent_cwd_path so the Edit/Write allowlist (_generate_agent_settings
        # -> _get_role_permissions) and the docker -w (_append_workspace_cwd)
        # resolve the SAME path.
        cwd_path = _agent_cwd_path(project_slug, team, agent_id, git_context)

        # Re-attach the task's worktree before the container launches with -w
        # pointing at it (F123). A pruned/evicted worktree would start the
        # agent in a missing dir; idempotent re-add, no-op for branchless spawns.
        await self._ensure_worktree_before_spawn(
            git_context, project_slug, team, agent_id, task_id
        )

        # Availability probe only (flag + per-project opt-in) — sandboxes are
        # provisioned on demand via the request_sandbox do-verb
        # (ensure_sandbox), never here, so a spawn never fails on sandbox
        # infrastructure.
        sandbox_services = await self._sandbox_available_services(project_slug)

        agent_settings_path = self._generate_agent_settings(
            agent_id, canonical_role, cwd_path, cell_workspace_path
        )

        briefing_path = await self._write_agent_briefing(
            agent_id, task_id, cwd_path, sandbox_services
        )

        await self._ensure_agent_image(agent_id)
        mcp_config_path = await self._generate_mcp_config(
            agent_id, git_context, task_id=task_id
        )

        from uuid import uuid4

        config = AgentConfig(
            agent_id=agent_id,
            blueprint_path=blueprint_path,
            model=model,
            mcp_config_path=mcp_config_path,
            claude_session_id=str(uuid4()),
            git_context=git_context,
            briefing_path=briefing_path,
            provider_type=route.provider_type.value,
            provider_base_url=route.base_url,
            provider_auth_token=route.auth_token,
            sandbox_available_services=sandbox_services,
        )
        # Crash-retry budget is per task, not per agent: a fresh AgentInstance
        # defaults error_count to 0, which let every respawn reset the cap
        # and made _crash_retry_or_escalate's threshold unreachable. Carry
        # the count over only when this spawn resumes the SAME task the
        # prior instance was on; a new task starts fresh, otherwise a count
        # stranded on a different task could land on 4 after one crash here
        # (matching neither the respawn nor the escalate branch, so the
        # agent dies silently forever). A graceful exit and a provider park
        # already reset it to 0 explicitly, so inheriting here is safe.
        prior = self._instances.get(agent_id)
        carried_error_count = (
            prior.error_count
            if prior is not None and prior.current_task_id == task_id
            else 0
        )
        instance = AgentInstance(
            agent_id=agent_id,
            state=AgentState.STARTING,
            config=config,
            current_task_id=task_id,
            error_count=carried_error_count,
        )
        self._instances[agent_id] = instance
        return config, instance, agent_settings_path

    async def _ensure_worktree_before_spawn(
        self,
        git_context: SpawnGitContext | None,
        project_slug: str,
        team: str,
        agent_id: str,
        task_id: str | None,
    ) -> None:
        """Re-attach the task's per-task worktree before the container starts.

        The container launches with ``-w`` at the worktree; a pruned/evicted
        worktree (reaper, disk pressure, manual cleanup while the agent was
        down) — or a vanished clone root (disk loss, a redeploy that wiped
        ``/data/workspaces``) — would start the agent in a missing directory.
        Idempotent: a pruned worktree is re-added from the surviving branch
        ref; a missing clone is re-cloned and the branch ref recovered from
        origin (``create_branch`` pushes at claim time) so the pushed work
        survives. A PRESENT worktree is refreshed against origin, role-aware
        (see ``ensure_worktree_self_heal``) rather than left frozen at
        whatever commit it was created on — the role comes from
        ``get_agent_role``, the same lookup ``_prepare_agent_spawn`` already
        makes for this agent. No-op for branchless / no-task spawns.

        The reaper-style claim release preserves ownership + ``branch_name``, so
        a re-dispatch is a RESUME, not a fresh claim — ``create_branch`` never
        re-runs to re-clone. Without the clone self-heal a vanished clone_root
        fatal-looped every tick (``git -C <missing>`` -> release -> re-dispatch
        into the same missing clone). A fatal git-state failure
        (``WorkspaceError`` — the clone won't re-clone, the token is missing,
        or the branch ref is unrecoverable) releases the claim and aborts so
        the next dispatch retries the rebuild, never launching the container at
        a missing ``-w``. A transient failure (DB/other) aborts without
        releasing — the next tick retries the same claim.
        """
        if not (git_context and git_context.task_short_id and git_context.branch_name):
            return
        clone_root = Path(_agent_workspace_path(project_slug, team, agent_id))
        worktree = Path(
            _agent_worktree_path(
                project_slug, team, agent_id, git_context.task_short_id
            )
        )
        can_author = is_worktree_author_role(get_agent_role(agent_id))
        from roboco.db.base import get_db_context
        from roboco.services.workspace import WorkspaceError, WorkspaceService

        try:
            async with get_db_context() as db:
                ws = WorkspaceService(db)
                # Heal a vanished/unhealthy clone first. The reaper-style claim
                # release preserves ownership + branch_name, so a re-dispatch is
                # a RESUME, not a fresh claim — create_branch never re-runs to
                # re-clone, and ensure_worktree_for_resume would ``git -C`` a
                # missing directory and fatal-loop every tick. Skipped on a
                # healthy clone (no new fetch overhead on the common resume).
                if not WorkspaceService._is_workspace_healthy(clone_root):
                    await ws.ensure_workspace(project_slug, agent_id)
                await ws.ensure_worktree_self_heal(
                    clone_root,
                    worktree,
                    git_context.branch_name,
                    project_slug,
                    can_author=can_author,
                )
        except WorkspaceError as e:
            # Fatal git state (clone won't re-clone, token missing, branch ref
            # unrecoverable): release the claim so the next dispatch can retry
            # the rebuild, and abort before docker run -w lands on a missing
            # path. The release is best-effort (suppressed) so a release
            # failure never masks the fatal error.
            logger.error(
                "worktree ensure failed (fatal); releasing claim for rebuild",
                agent_id=agent_id,
                task_short_id=git_context.task_short_id,
                error=str(e),
            )
            if task_id:
                with contextlib.suppress(Exception):
                    await self._release_claim_to_pending(task_id)
            raise AgentReadinessError(
                f"worktree ensure failed for {agent_id}"
                f" (task={task_id}, branch={git_context.branch_name}): {e};"
                f" claim released for rebuild"
            ) from e
        except Exception as e:
            # Transient (DB hiccup, etc.): abort so we don't launch at a
            # possibly-missing path, but do NOT release — a fresh claim would
            # not help and re-cloning is destructive. Next tick retries.
            logger.warning(
                "worktree ensure failed (transient); aborting spawn",
                agent_id=agent_id,
                task_short_id=git_context.task_short_id,
                error=str(e),
            )
            raise AgentReadinessError(
                f"worktree ensure failed (transient) for {agent_id}"
                f" (task={task_id}): {e}; will retry next tick"
            ) from e

    async def _launch_spawn(
        self,
        task_id: str | None,
        config: AgentConfig,
        instance: AgentInstance,
        initial_prompt: str | None,
        agent_settings_path: Path | None,
        *,
        spawned_by: str | None = None,
    ) -> AgentInstance:
        """Launch the container and emit spawn audit events.

        `agent_id` was dropped as a redundant parameter — `config.agent_id`
        is the same value and was always the caller's source.

        ``spawned_by`` names the dispatch loop that requested the spawn; it is
        stamped into the spawned/spawn_failed audit details so a rogue spawner
        is identifiable from the audit log alone.
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
                    "spawned_by": spawned_by or "unspecified",
                },
            )

            # Record a token-usage session row in the DB and bind its UUID to
            # the instance so _finalize_spawn_session can look it up directly.
            usage_session_id = await self._record_spawn_session(config, task_id)
            if usage_session_id is not None:
                instance.usage_session_id = usage_session_id

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
                details={
                    "error": str(e),
                    "spawned_by": spawned_by or "unspecified",
                },
                severity="error",
            )
            raise

    @staticmethod
    def _spawn_preflight_reason(agent_id: str) -> str | None:
        """Refusal reason if this spawn is deterministically futile, else None.

        Flag-gated (``spawn_preflight_enabled``, default off). A non-human
        delivery role absent from ``GATEWAY_ENABLED_ROLES`` gets no manifest and
        ``ROBOCO_GATEWAY_ENABLED=false``, so it can never claim its work and the
        dispatcher would respawn it on the same task forever. Fail fast instead of
        burning the full system prompt on each futile retry.
        """
        if not settings.spawn_preflight_enabled:
            return None
        role = get_agent_role(agent_id)
        if role is not None and role not in GATEWAY_ENABLED_ROLES:
            return (
                f"role {role!r} ({agent_id}) is not gateway-enabled — it could "
                f"never claim its work and would respawn forever"
            )
        return None

    async def _refuse_unspawnable(self, agent_id: str, task_id: str | None) -> None:
        """Chokepoint guards for ``spawn_agent``; raise ``AgentReadinessError``.

        Three refusals, in order:
        1. A traversal-shaped ``agent_id`` (it flows into log dirs, settings and
           container names) — rejected before any filesystem op.
        2. Human-only roles (ceo / prompter / secretary) are NEVER spawned by a
           dispatcher. The CEO is the human operator; intake and secretary are
           human-driven chats launched through their own guarded paths
           (_spawn_intake_container / _spawn_secretary_container), not this
           method. Without this, a dispatcher that spawns "any A2A/notification
           target" could resolve a CEO-addressed notification to slug "ceo" and
           launch a CEO container — the system acting as the human CEO.
        3. Spawn preflight (flag-gated): a non-gateway delivery role could never
           claim its work and would respawn forever — refuse + alert once.
        """
        AgentOrchestrator._safe_agent_path_segment(agent_id)
        task_id_str = str(task_id) if task_id else None
        _role = role_for_slug_or_none(agent_id)
        if is_human_only_role(_role):
            logger.error(
                "spawn_agent refused for human-only role — dispatchers must never"
                " spawn the CEO / prompter / secretary; these are human-driven",
                agent_id=agent_id,
                role=str(_role),
                task_id=task_id_str,
            )
            raise AgentReadinessError(
                f"refused to spawn human-only role {_role!r} ({agent_id}) — the"
                f" CEO is the human operator, not a container; intake and secretary"
                f" launch through their dedicated paths, not spawn_agent"
            )
        preflight_reason = AgentOrchestrator._spawn_preflight_reason(agent_id)
        if preflight_reason:
            logger.error(
                "spawn_agent refused (spawn preflight): role not gateway-enabled",
                agent_id=agent_id,
                task_id=task_id_str,
            )
            if task_id_str:
                await self._notify_stuck_agent(agent_id, task_id_str, None)
            raise AgentReadinessError(preflight_reason)

    async def spawn_agent(
        self,
        agent_id: str,
        initial_prompt: str | None = None,
        task_id: str | None = None,
        model: str | None = None,
        git_context: SpawnGitContext | None = None,
        *,
        spawned_by: str | None = None,
    ) -> AgentInstance:
        """
        Spawn a Claude Code container for an agent.

        Args:
            agent_id: Agent identifier (e.g., "be-dev-1")
            initial_prompt: Optional initial prompt
            task_id: Optional task ID being worked on
            model: Override model selection
            git_context: Optional git context (project_slug, branch_name)
            spawned_by: Name of the dispatch loop / entry point requesting
                the spawn — stamped into the agent.spawned audit details

        Returns:
            AgentInstance handle

        Raises:
            AgentReadinessError: task is not spawn-ready (missing criteria,
                missing git token, no branch plan, role mismatch). The task
                is auto-blocked before we raise so the dispatcher doesn't
                keep retrying.
        """
        # Chokepoint guards: path-safe id, never-a-human-role, spawn preflight.
        await self._refuse_unspawnable(agent_id, task_id)
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

        # Provider-parking loop-breaker (cheap pre-check): while this agent's
        # provider is parked (rate-limited or overloaded), do NOT run the full
        # ``_prepare_agent_spawn`` — which writes the blueprint / settings /
        # briefing / MCP-config files, ensures the agent image, and registers a
        # STARTING instance — only to bail. The dispatcher re-ticks a parked
        # agent every cycle, so running the full prepare each tick wasted all
        # that file I/O and left a STARTING instance registered then downgraded
        # to OFFLINE. The parked check only needs ``provider_type``, cheaply
        # resolvable via ``_resolve_agent_route``. Bailing here returns a
        # minimal UNREGISTERED OFFLINE instance (no stale ``_instances`` entry),
        # so the next tick re-checks cheaply until the provider recovers. The
        # existing-running check above stays first, so a live agent is never
        # replaced by this bail. Fail-open: a tracker read error never blocks.
        route = await self._resolve_agent_route(agent_id, task_id)
        skip_reason = await self._spawn_gate_skip_reason(
            route.provider_type.value, exclude_agent_id=agent_id
        )
        if skip_reason:
            self._mark_task_handled(task_id)
            logger.info(
                skip_reason,
                agent_id=agent_id,
                task_id=task_id,
                provider=route.provider_type.value,
            )
            return self._offline_route_bail(agent_id, task_id, route, git_context)

        async with self._lock:
            # TOCTOU re-check: another tick may have started this agent during
            # the unlocked route resolve + parked check above. Re-check before
            # the expensive prepare so two concurrent ticks don't double-spawn.
            existing = self._existing_running_instance(agent_id)
            if existing is not None:
                return existing
            config, instance, agent_settings_path = await self._prepare_agent_spawn(
                agent_id, task_id, model, git_context
            )
        # Rare-race defense: a park could land during prepare. The every-tick
        # parked case is already handled above; this guards the window between
        # the pre-check and the launch. Fail-open: a tracker read error never
        # blocks spawning.
        skip_reason = await self._spawn_gate_skip_reason(
            config.provider_type, exclude_agent_id=agent_id
        )
        if skip_reason:
            return self._bail_prepared_instance(
                instance, agent_id, task_id, config.provider_type, skip_reason
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
            spawned_by=spawned_by,
        )

    def _resolve_host_paths(
        self, config: AgentConfig, agent_settings_path: Path | None
    ) -> dict[str, str | None]:
        """Compute host mount paths for both containerized and host runtime."""
        mcp_name = config.mcp_config_path.name if config.mcp_config_path else ""
        if PROJECT_HOST_PATH:
            return {
                "docs": f"{PROJECT_HOST_PATH}/docs",
                "workspaces": f"{DATA_HOST_PATH}/workspaces",
                "claude": CLAUDE_AUTH_HOST_PATH,
                "mcp_config": f"{DATA_HOST_PATH}/mcp-configs/{mcp_name}",
                # Per-agent grok usage dir (GROK only); the orchestrator reads the
                # captured tokens back at finalize via the shared data volume
                # (see GROK_USAGE_DATA_DIR).
                "grok_usage": f"{DATA_HOST_PATH}/grok-usage/{config.agent_id}",
                # Per-agent codex usage dir (OPENAI only); same shape.
                "codex_usage": f"{DATA_HOST_PATH}/codex-usage/{config.agent_id}",
                # Per-agent gemini usage dir (GEMINI only); same shape as
                # grok_usage above (see GEMINI_USAGE_DATA_DIR).
                "gemini_usage": f"{DATA_HOST_PATH}/gemini-usage/{config.agent_id}",
                # Per-agent kimi usage dir (KIMI only); same shape.
                "kimi_usage": f"{DATA_HOST_PATH}/kimi-usage/{config.agent_id}",
                # Per-agent openrouter usage dir (OPENROUTER only); same shape.
                "openrouter_usage": (
                    f"{DATA_HOST_PATH}/openrouter-usage/{config.agent_id}"
                ),
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
            "docs": str((self.project_root / "docs").absolute()),
            "workspaces": str(Path(settings.workspaces_root)),
            "claude": CLAUDE_AUTH_HOST_PATH,
            "mcp_config": str(config.mcp_config_path),
            "grok_usage": str(
                Path(tempfile.gettempdir()) / "roboco-grok-usage" / config.agent_id
            ),
            "codex_usage": str(
                Path(tempfile.gettempdir()) / "roboco-codex-usage" / config.agent_id
            ),
            "gemini_usage": str(
                Path(tempfile.gettempdir()) / "roboco-gemini-usage" / config.agent_id
            ),
            "kimi_usage": str(
                Path(tempfile.gettempdir()) / "roboco-kimi-usage" / config.agent_id
            ),
            "openrouter_usage": str(
                Path(tempfile.gettempdir())
                / "roboco-openrouter-usage"
                / config.agent_id
            ),
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
            # Let spawned containers resolve host.docker.internal so the eval
            # harness's disposable orchestrator (bound 0.0.0.0 on the host) is
            # reachable via host.docker.internal:<port>. Inert in production
            # where MCP servers use http://roboco-orchestrator:8000. Docker
            # 20.10+ (May 2021) supports host-gateway on Linux.
            "--add-host",
            "host.docker.internal:host-gateway",
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
        """Mount host's ~/.claude.json sibling FILE if present."""
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
        """The always-on -v/-e block (prompt, docs, workspaces, env)."""
        docs_ro = "" if config.agent_id in ALL_DOCS else ":ro"
        env = [
            "-v",
            f"{hosts['prompt']}:/app/system-prompt.md:ro",
            "-v",
            f"{hosts['docs']}:/app/docs{docs_ro}",
            "-v",
            f"{hosts['workspaces']}:/data/workspaces",
            "-v",
            f"{hosts['mcp_config']}:/app/mcp-config.json:ro",
            "-e",
            # Auth identity is the agent's UUID, not its slug: the MCP servers
            # forward X-Agent-ID as the UUID (gateway v1 endpoints parse it as
            # Annotated[UUID]) and the HMAC token is signed over the same value,
            # so the container env the SDK server inherits must match or its
            # direct API calls 401 with "signature mismatch".
            f"ROBOCO_AGENT_ID={AGENT_UUIDS.get(config.agent_id, config.agent_id)}",
            "-e",
            f"ROBOCO_AGENT_ROLE={role}",
            "-e",
            "ROBOCO_API_URL=http://roboco-orchestrator:8000",
            "-e",
            "ROBOCO_SDK_PORT=9000",
            "-e",
            "ROBOCO_SDK_URL=http://localhost:9000",
            "-e",
            # Claude Code's own MCP client connect-timeout (ms); its 30s
            # default is too tight once a fleet-wide spawn burst slows every
            # stdio MCP server's startup past it. No-op for the grok/gemini/
            # codex/kimi CLIs, which don't read this var.
            f"MCP_TIMEOUT={settings.agent_mcp_startup_timeout_ms}",
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
        return env

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
        # workspace_path mirrors the container -w (same resolver) so the
        # manifest never claims a different directory than the shell.
        manifest_host_path = _build_manifest_for_agent(
            config.agent_id,
            subagent_model,
            workspace_path=AgentOrchestrator._resolve_workspace_cwd(config),
        )
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

    @staticmethod
    def _resolve_workspace_cwd(config: AgentConfig) -> str | None:
        """The task-resolved workspace path for this spawn, or None.

        Single source of truth consumed by BOTH the container ``-w`` and the
        spawn manifest's ``workspace_path`` — they must agree, or the agent's
        prompt claims one directory while its shell sits in another (live
        2026-07-02: manifest said the roboco workspace for a guard-core task).
        """
        role = get_agent_role(config.agent_id) or "developer"
        team = get_agent_team(config.agent_id) or ""
        project = _resolve_project_slug_from_git_context(config.git_context)
        if role in AgentOrchestrator._ROLES_WITH_AGENT_WORKSPACE:
            # Per-task worktree when the task has a branch (F123), else the
            # clone root. _agent_cwd_path is the SAME formula the Edit/Write
            # allowlist is built from, so -w and the allowlist match exactly.
            return _agent_cwd_path(project, team, config.agent_id, config.git_context)
        if role in AgentOrchestrator._ROLES_WITH_CELL_WORKSPACE:
            return _cell_workspace_path(project, team)
        return None

    @staticmethod
    def _append_workspace_cwd(cmd: list[str], config: AgentConfig) -> None:
        """Set the container -w to the agent or cell workspace by role."""
        # Pre-gateway parity: set the container's cwd
        # to the agent's task workspace so Edit/Write resolve to paths that
        # match _get_role_permissions allowlist, and `git add` operates inside
        # the workspace clone. Without this, container WORKDIR (/app from the
        # Dockerfile) shadows the workspace and every file op fails.
        #
        # Workspace selection lives in _resolve_workspace_cwd:
        # - developer / product_owner / head_marketing: per-agent workspace
        # - documenter: cell workspace
        # - qa / cell_pm / main_pm / auditor: no write workspace → omit -w
        workspace = AgentOrchestrator._resolve_workspace_cwd(config)
        if workspace is not None:
            cmd.extend(["-w", workspace])

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
        # Sign over the UUID, not the slug: every in-container caller (MCP
        # servers via the manifest env, SDK server via this container env)
        # sends X-Agent-ID as the UUID, so the HMAC payload must be the UUID
        # or the middleware rejects with "signature mismatch". AGENT_UUIDS
        # maps slug→UUID; fall back to the slug for custom agents not seeded.
        _agent_uuid = AGENT_UUIDS.get(config.agent_id, config.agent_id)
        _token = issue_agent_token(
            _agent_uuid,
            _role,
            _team,
            ttl_seconds=settings.agent_token_ttl_seconds,
        )
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
    def _append_gate_env(cmd: list[str]) -> None:
        """Inject the test-DB env so an agent's gate runs the real, DB-backed
        suite instead of a hollow unit-only subset.

        Without a reachable Postgres the conftest skips every integration test,
        so coverage collapses far below the gate threshold and a role 'gates'
        against a partial run (the failure that made a PM read 71% on a suite
        that is ~96% with a DB). The values come from the orchestrator's own DB
        settings; agents share the Docker network, so the host resolves. The app
        runtime reads ROBOCO_DATABASE_*, never ROBOCO_TEST_DB_*, so this only
        feeds the test harness and never changes live behaviour. Gated on the
        same faithful-gate flag as interpreter matching — both exist to make an
        agent's self-gate trustworthy.

        Under DB network isolation (postgres/redis on the data-only compose
        network) agents cannot reach these hosts at all, so the injection is
        suppressed entirely: creds that dead-end in a connect timeout are
        worse than none (the conftest reachability check skips cleanly on a
        fast refusal). DB-needing projects opt into `sandbox_services` instead.
        """
        if settings.db_network_isolated:
            return
        if not settings.toolchain_match_enabled:
            return
        cmd.extend(
            [
                "-e",
                f"ROBOCO_TEST_DB_HOST={settings.database_host}",
                "-e",
                f"ROBOCO_TEST_DB_PORT={settings.database_port}",
                "-e",
                f"ROBOCO_TEST_DB_USER={settings.database_user}",
                "-e",
                f"ROBOCO_TEST_DB_PASSWORD={settings.database_password}",
                "-e",
                "ROBOCO_TEST_DB_ADMIN_DB=postgres",
            ]
        )

    @staticmethod
    def _append_sandbox_marker_env(cmd: list[str], services: list[str]) -> None:
        """Cheap availability probe: names the request_sandbox-eligible services.

        Replaces eager sandbox env injection now that provisioning is
        on-demand (the `request_sandbox` do-verb) — never prod creds, purely
        informational so the agent knows the verb will succeed. Called
        INSTEAD OF `_append_gate_env` for an opted-in project (never both).
        """
        cmd.extend(["-e", f"ROBOCO_SANDBOX_SERVICES_AVAILABLE={','.join(services)}"])

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
          - TodoWrite        : per-session planning
        Permissions still gate *which* paths Edit/Write can touch (see
        `_get_role_permissions`), so this is purely about loading vs
        denying.

        `--disable-slash-commands` closes a separate capability channel
        `--tools` doesn't reach: skills/slash-commands resolve independently
        of the built-in tool allowlist (Anthropic's own `--bare` flag docs
        call this out — skills still resolve via `/skill-name` even with
        everything else disabled). The agent's `~/.claude` is the host's
        shared Claude Code auth dir, bind-mounted into every container
        (`_build_mount_args`); if it ever carries personal
        skills/plugins/marketplace installs, this stops them from silently
        becoming callable inside the agent's session. No RoboCo role's
        workflow uses a Claude Code skill (their surface is the MCP gateway
        + the `--tools` set above), so this has no legitimate flow to break.
        """
        claude_args = [
            get_agent_image(config.agent_id),
            "--model",
            cls._resolve_cli_model(config),
            "--system-prompt-file",
            "/app/system-prompt.md",
            "--mcp-config",
            "/app/mcp-config.json",
            "--strict-mcp-config",
            "--tools",
            "Read,Write,Edit,Bash,Grep,Glob,TodoWrite",
            "--disable-slash-commands",
            "--output-format",
            "stream-json",
            "--verbose",
        ]
        # Per-role reasoning-effort override via Claude Code's `--effort` flag.
        # Only set for roles in ROLE_EFFORT_MAP; models without effort support
        # ignore it, so passing it for a mapped role is always safe.
        _effort_role = get_agent_role(config.agent_id)
        _effort = ROLE_EFFORT_MAP.get(_effort_role) if _effort_role else None
        if _effort:
            claude_args += ["--effort", _effort]
        # Pin the Claude session id so the agent's transcript is locatable by id
        # at finalize, regardless of which project/cwd dir Claude Code writes it
        # to (review/coordinate roles run at /app, not a per-agent workspace).
        if config.claude_session_id:
            claude_args += ["--session-id", config.claude_session_id]
        claude_args += ["-p", initial_prompt or cls._default_spawn_prompt()]
        cmd.extend(claude_args)

    def _ensure_provider_registry(self) -> "ProviderRegistry":
        """Build (once) the registry of dedicated provider backends.

        Only providers that need a runtime other than the built-in Claude Code
        container are registered. Today that is GROK (xAI, OpenAI protocol),
        OPENAI (Codex CLI, subscription-shaped like GROK), GEMINI (Google,
        official CLI, one-shot delivery roles only — see
        roboco.llm.providers.gemini for the V1 scope), and KIMI (Moonshot,
        official CLI, one-shot delivery roles only — see
        roboco.llm.providers.kimi for the V1 scope), and OPENROUTER (any
        OpenRouter model via the opencode CLI, the Ollama shape — static key
        via env, no auth mount; one-shot delivery roles only — see
        roboco.llm.providers.openrouter for the V1 scope).
        """
        if self._provider_registry is None:
            from roboco.llm.providers import (
                CodexCliProvider,
                GeminiCliProvider,
                GrokCliProvider,
                KimiCliProvider,
                OpenRouterProvider,
                ProviderRegistry,
            )
            from roboco.models.base import ModelProvider

            registry = ProviderRegistry()
            # Qualify each image with the registry namespace + tag so it
            # resolves in both local-build and registry deploys (parity with
            # get_agent_image for the Claude path).
            registry.register(
                ModelProvider.GROK,
                GrokCliProvider(self, image=_qualify_agent_image("roboco-agent-grok")),
            )
            registry.register(
                ModelProvider.OPENAI,
                CodexCliProvider(
                    self, image=_qualify_agent_image("roboco-agent-codex")
                ),
            )
            registry.register(
                ModelProvider.GEMINI,
                GeminiCliProvider(
                    self, image=_qualify_agent_image("roboco-agent-gemini")
                ),
            )
            registry.register(
                ModelProvider.KIMI,
                KimiCliProvider(self, image=_qualify_agent_image("roboco-agent-kimi")),
            )
            registry.register(
                ModelProvider.OPENROUTER,
                OpenRouterProvider(
                    self, image=_qualify_agent_image("roboco-agent-openrouter")
                ),
            )
            self._provider_registry = registry
        return self._provider_registry

    def _provider_for(self, provider_type: str) -> "AgentProvider | None":
        """Resolve a dedicated provider for a route's ``provider_type`` string.

        Returns ``None`` for providers that use the built-in Claude Code spawn
        (Anthropic / Ollama Cloud / self-hosted) or any unrecognised value — the
        caller then runs the existing container path unchanged.
        """
        from roboco.models.base import ModelProvider

        try:
            model_provider = ModelProvider(provider_type)
        except ValueError:
            return None
        return self._ensure_provider_registry().get_or_none(model_provider)

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
        # Every spawn gets a non-empty user prompt. A prompt-less spawn (e.g. the
        # crash auto-restart, which passes no initial_prompt) must still direct the
        # agent to scan for work. The Claude body re-applies the same default; doing
        # it here single-sources it so dedicated providers (GROK) get it too —
        # otherwise grok would launch with an empty `grok -p ""`.
        if not initial_prompt:
            initial_prompt = self._default_spawn_prompt()
        # A dedicated provider backend (e.g. GROK / OpenAI protocol) handles its
        # own spawn. Anthropic / Ollama Cloud / self-hosted have no dedicated
        # provider registered and fall through to the Claude Code body below,
        # byte-for-byte unchanged.
        provider = self._provider_for(config.provider_type)
        if provider is not None:
            result = await provider.spawn(config, initial_prompt, agent_settings_path)
            return result.instance_id

        container_name = f"roboco-agent-{config.agent_id}"
        # teardown_sandbox=False: nothing is provisioned before spawn anymore
        # (sandboxes are on-demand via request_sandbox/ensure_sandbox), so this
        # is now vestigial for THIS spawn — but it still protects a respawn
        # racing a sandbox the agent just requested moments ago via the verb.
        await self._remove_container(
            container_name, teardown_sandbox=False, stop_reason="pre_spawn_stale_clear"
        )

        if not config.mcp_config_path:
            raise RuntimeError("MCP config path not set")

        hosts = self._resolve_host_paths(config, agent_settings_path)
        cmd = self._build_mount_args(container_name, config, hosts)
        self._append_agent_auth_env(cmd, config)
        self._append_git_context_env(cmd, config)
        if config.sandbox_available_services:
            self._append_sandbox_marker_env(cmd, config.sandbox_available_services)
        else:
            self._append_gate_env(cmd)
        cmd.extend(await compose_label_args(config.agent_id))
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

    async def _is_video_authoring_spawn(
        self, agent_id: str, agent_role: str, task_id: str | None
    ) -> bool:
        """A ux_ui developer spawned onto a source=video task — the one
        non-QA case that gets the playwright MCP, so the author can open the
        composition HTML in a real browser while iterating instead of
        shipping the first render blind. Gating-only: the agent-ux image
        already bakes the browser + wrapper entrypoint. Fail-closed — any
        lookup error means no browser, never a broken spawn."""
        if agent_role != "developer" or not task_id:
            return False
        if get_agent_team(agent_id) != "ux_ui":
            return False
        try:
            from uuid import UUID

            from roboco.db.base import get_session_factory
            from roboco.services.task import VIDEO_SOURCE, get_task_service

            factory = get_session_factory()
            async with factory() as db:
                t = await get_task_service(db).get(UUID(task_id))
            return t is not None and t.source == VIDEO_SOURCE
        except Exception as exc:
            logger.warning(
                "video-authoring spawn probe failed; playwright not registered",
                agent_id=agent_id,
                task_id=task_id,
                error=str(exc),
            )
            return False

    async def _is_dogfood_spawn(
        self, agent_id: str, agent_role: str, task_id: str | None
    ) -> bool:
        """A Product Owner spawned onto a source=board_dogfood task — the
        ONLY board-role case that gets the playwright MCP, so the PO can walk
        the product as a user (spec §4). Task-scoped, not role-blanket: a PO
        spawned for roadmap/pest_control/scales/any other cycle must NOT get
        browser tools. Mirrors ``_is_video_authoring_spawn``'s shape exactly
        — fail-closed: any lookup error means no browser, never a broken
        spawn."""
        if agent_role != "product_owner" or not task_id:
            return False
        try:
            from uuid import UUID

            from roboco.db.base import get_session_factory
            from roboco.services.task import DOGFOOD_SOURCE, get_task_service

            factory = get_session_factory()
            async with factory() as db:
                t = await get_task_service(db).get(UUID(task_id))
            return t is not None and t.source == DOGFOOD_SOURCE
        except Exception as exc:
            logger.warning(
                "dogfood spawn probe failed; playwright not registered",
                agent_id=agent_id,
                task_id=task_id,
                error=str(exc),
            )
            return False

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
            async with httpx.AsyncClient(
                timeout=5.0, headers=_system_api_headers()
            ) as client:
                task_or_reason = await self._readiness_fetch_task(client, task_id)
                if isinstance(task_or_reason, str):
                    return task_or_reason
                task = task_or_reason

                # Universal dependency gate: refuse to spawn an agent of ANY role
                # onto a task whose cross-task dependencies are not yet terminal.
                # This check previously lived only on the dev dispatch path, so
                # cell-PM, Main-PM and board agents were spawned onto
                # dependency-blocked tasks and flailed unblock / escalate / notify
                # against an unfinished upstream. Auto-block so the task leaves the
                # pending pool (no per-tick spawn-refusal that would starve
                # siblings); `_unblock_dependents` revives it the moment the
                # upstream reaches a terminal state.
                if dep_reason := await self._check_dependencies_terminal(client, task):
                    return await self._readiness_block(client, task_id, dep_reason)

                persistent = self._readiness_check_task(agent_id, task)
                # Skip the git-token gate for coordination tasks — they have no
                # project of their own, so there's no token to require.
                if persistent is None and not _is_coordination_task(task):
                    project_slug = _read_project_slug(task)
                    persistent = await self._readiness_check_git_token(project_slug)
                if persistent is not None:
                    return await self._readiness_block(client, task_id, persistent)
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
        agent_id: str,
        role: str,
        status: str,
        *,
        is_coordination: bool = False,
        owner_is_pm: bool = False,
    ) -> str | None:
        """Verify agent role matches the role expected for the task status.

        Handoff states are role-specific. Dev-owned states (in_progress,
        verifying, needs_revision, paused, blocked) are restricted to
        developer/documenter to defang the bug where QA got
        respawned on a `needs_revision` task via the crash-restart path
        and immediately hit ``role 'qa' may not claim from status
        'needs_revision'`` at the gateway. The exception is a PM-OWNED revision:
        a coordination root (no code; product fan-out owned by a PM, a CEO-reject
        returning to its PM) AND a gate-failed assembled PR (the PR-review gate's
        ``pr_fail`` sends a cell->root / root->master PR back to needs_revision,
        still owned by the cell/main PM). In both the owner is a PM, so the
        dev-owned states also accept the PM roles when ``owner_is_pm`` — matching
        ``_dispatch_revision_coordination_roots``, which re-spawns exactly those.
        A pure widening; nothing currently allowed is blocked, and QA stays out.
        """
        role_mismatch: dict[str, str | set[str]] = {
            "awaiting_qa": "qa",
            "awaiting_documentation": "documenter",
            "awaiting_pr_review": "pr_reviewer",
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
        if status in ("needs_revision", "verifying") and (
            is_coordination or owner_is_pm
        ):
            required = set(required) | {"cell_pm", "main_pm"}
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
            # the readiness and stuck-detection paths agree.
            if _branch_is_expected(task) and not task.get("branch_name"):
                return f"state={status} but branch_name is unset"
        owner = task.get("assigned_to") or task.get("claimed_by")
        owner_role = get_agent_role(self._resolve_agent_slug(owner)) if owner else None
        return self._readiness_check_role_for_status(
            agent_id,
            role,
            status,
            is_coordination=_is_coordination_task(task),
            owner_is_pm=owner_role in ("cell_pm", "main_pm"),
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

    async def _resolve_agent_route(
        self, agent_id: str, task_id: str | None = None
    ) -> "AgentRoute":
        """Resolve (provider, model) for `agent_id` via `ModelRoutingService`.

        When `task_id` is given, its `estimated_complexity` (LOW/MEDIUM/HIGH)
        threads into the resolver as a lowercase string so a cost-tiered
        `ROLE(":"complexity)` override (see `ModelRoutingService`) can apply.
        The task lookup is isolated in its own try/except: a missing task or
        lookup failure degrades to the plain-role path silently (debug log —
        this is the common case for non-task spawns like idle PM bootstrap),
        never escalating to the full legacy-Anthropic downgrade below.

        Errors are contained: any DB/session failure degrades to a legacy
        Anthropic-default AgentRoute so spawn never stalls on routing.
        """
        from sqlalchemy import select

        from roboco.db.base import get_session_factory
        from roboco.db.tables import TaskTable
        from roboco.models.base import ModelProvider
        from roboco.models.runtime import MODEL_MAP
        from roboco.services.llm import (
            AgentRoute,
            get_model_routing_service,
        )

        try:
            factory = get_session_factory()
            async with factory() as db:
                complexity: str | None = None
                if task_id:
                    try:
                        result = await db.execute(
                            select(TaskTable.estimated_complexity).where(
                                TaskTable.id == task_id
                            )
                        )
                        row = result.scalar_one_or_none()
                        if row is not None:
                            complexity = row.value.lower()
                    except Exception as e:
                        logger.debug(
                            "Task complexity lookup failed; using plain-role routing",
                            agent_id=agent_id,
                            task_id=task_id,
                            error=str(e),
                        )
                router = get_model_routing_service(db)
                return await router.resolve_for_agent(agent_id, complexity=complexity)
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

    async def _record_spawn_session(
        self,
        config: "OrchestratorAgentConfig",
        task_id: str | None,
    ) -> "UUID | None":
        """Insert a row into agent_spawn_sessions after a successful spawn.

        Returns the UUID of the created row so the caller can store it on
        the AgentInstance for later direct-by-id lookup in
        _finalize_spawn_session.  Returns None when the insert fails; a
        missing session row must never block the spawn path.
        """
        try:
            from uuid import uuid4 as _uuid4

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentSpawnSessionTable

            agent_slug = config.agent_id
            team = get_agent_team(agent_slug) or "backend"
            role = get_agent_role(agent_slug) or "developer"

            # A delivery-role spawn with no task_id is unattributed usage (#11) —
            # the rollup can't tie the spend to a task. Intake/secretary/PM spawns
            # legitimately carry no task and are not flagged.
            if is_unattributed_delivery_spawn(role, task_id):
                logger.warning(
                    "Spawn session has no task_id for a delivery role — "
                    "unattributed usage",
                    agent_slug=agent_slug,
                    role=role,
                )

            session_id = _uuid4()
            session_factory = get_session_factory()
            async with session_factory() as db:
                row = AgentSpawnSessionTable(
                    id=session_id,
                    agent_slug=agent_slug,
                    team=team,
                    role=role,
                    model=config.model or "unknown",
                    task_id=task_id,
                    started_at=datetime.now(UTC),
                )
                db.add(row)
                await db.commit()
                logger.debug(
                    "Spawn session recorded",
                    agent_slug=agent_slug,
                    session_id=str(session_id),
                    task_id=task_id,
                )
            return session_id
        except Exception as exc:
            logger.warning(
                "Failed to record spawn session",
                agent_slug=config.agent_id,
                error=str(exc),
            )
            return None

    def _make_tracker(self, provider: str) -> Any:
        """Return a RateLimitStateTracker for *provider*.

        Extracted as its own method so unit tests can monkeypatch it to
        return an async mock without needing to intercept lazy imports.
        """
        from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

        return RateLimitStateTracker(provider)

    async def _provider_spawn_parked(self, provider_type: str | None) -> bool:
        """True when *provider_type*'s provider is parked (rate-limited/overloaded).

        The spawn loop-breaker consults this before launching any container.
        Fail-open: any error reading the tracker returns False so a Redis hiccup
        can never block spawning.
        """
        if provider_type is None:
            return False
        try:
            tracker = self._make_tracker(provider_type)
            return bool(await tracker.is_rate_limited())
        except Exception as exc:
            logger.warning(
                "provider rate-limit check failed; allowing spawn",
                provider=provider_type,
                error=str(exc),
            )
            return False

    @staticmethod
    def _provider_concurrency_cap(provider_type: str | None) -> int | None:
        """Max concurrent live containers for *provider_type*, or None if uncapped.

        Only KIMI is capped: every Kimi container shares one OAuth
        refresh-token chain (see ``settings.kimi_max_concurrent``) — a second
        concurrent container risks forking it and revoking fleet-wide Kimi
        auth. No other provider has this constraint.
        """
        if provider_type == ModelProvider.KIMI.value:
            return settings.kimi_max_concurrent
        return None

    def _live_provider_instance_count(
        self, provider_type: str, exclude_agent_id: str | None = None
    ) -> int:
        """Count ``_instances`` entries for *provider_type* still holding a slot.

        Mirrors ``_existing_running_instance``'s liveness definition: any
        state other than OFFLINE/WAITING_LONG occupies (or is about to
        occupy) a container. *exclude_agent_id* drops the agent being
        spawned from the count — ``_prepare_agent_spawn`` registers its
        STARTING instance before the post-prepare gate re-check, and with
        cap=1 a self-counting check cancels every spawn forever.
        """
        return sum(
            1
            for agent_id, inst in self._instances.items()
            if agent_id != exclude_agent_id
            and inst.config is not None
            and inst.config.provider_type == provider_type
            and inst.state not in (AgentState.OFFLINE, AgentState.WAITING_LONG)
        )

    def _provider_spawn_at_capacity(
        self, provider_type: str | None, exclude_agent_id: str | None = None
    ) -> bool:
        """True when *provider_type* is at its concurrency cap, if any."""
        cap = self._provider_concurrency_cap(provider_type)
        if cap is None or provider_type is None:
            return False
        live = self._live_provider_instance_count(provider_type, exclude_agent_id)
        return live >= cap

    async def _spawn_gate_skip_reason(
        self, provider_type: str | None, exclude_agent_id: str | None = None
    ) -> str | None:
        """Why ``spawn_agent`` should bail before launch, or None to proceed.

        Checked in order: provider-parked (rate-limited/overloaded), then the
        provider's concurrency cap (currently kimi-only, see
        ``_provider_concurrency_cap``). The returned string is both the skip
        reason and the log event name — same shape both callers already used.
        *exclude_agent_id* is the agent this spawn is FOR: its own registered
        instance must never count against its own capacity check.
        """
        if await self._provider_spawn_parked(provider_type):
            return "Spawn skipped: provider rate-limited (parked)"
        if self._provider_spawn_at_capacity(provider_type, exclude_agent_id):
            return "Spawn skipped: kimi concurrency cap reached"
        return None

    def _bail_prepared_instance(
        self,
        instance: AgentInstance,
        agent_id: str,
        task_id: str | None,
        provider_type: str | None,
        reason: str,
    ) -> AgentInstance:
        """OFFLINE a just-``_prepare_agent_spawn``'d instance and log why the
        launch was skipped (used by both the parked and concurrency-cap
        rare-race checks after prepare)."""
        self._mark_task_handled(task_id)
        instance.state = AgentState.OFFLINE
        logger.info(reason, agent_id=agent_id, task_id=task_id, provider=provider_type)
        return instance

    def _offline_route_bail(
        self,
        agent_id: str,
        task_id: str | None,
        route: Any,
        git_context: SpawnGitContext | None,
    ) -> AgentInstance:
        """Build the unregistered OFFLINE instance returned by a pre-prepare
        spawn bail (parked or at-capacity) — no blueprint/settings/image work
        has run, so nothing needs undoing."""
        return AgentInstance(
            agent_id=agent_id,
            state=AgentState.OFFLINE,
            config=AgentConfig(
                agent_id=agent_id,
                blueprint_path=Path(),  # not launching — no blueprint written
                model=route.model_name,
                provider_type=route.provider_type.value,
                provider_base_url=route.base_url,
                provider_auth_token=route.auth_token,
                git_context=git_context,
            ),
            current_task_id=task_id,
        )

    @property
    def _api_url(self) -> str:
        """Get the internal API URL for task/notification queries."""
        return settings.internal_api_url

    async def _check_dependencies_terminal(
        self, client: httpx.AsyncClient, task: dict[str, Any]
    ) -> str | None:
        """Hold a pre-assigned task whose dependencies are not yet terminal.

        A dev subtask is always pre-assigned, so it never passes through the
        unassigned claim pool's dependency filter. Without this gate the
        dispatcher would spawn the dev container while a cross-cell dependency
        (e.g. the UX/UI design the frontend dev waits on) is still open. Return
        a skip reason while ANY dependency is non-terminal; allow the spawn
        once every dependency reaches completed/cancelled.
        """
        dependency_ids = task.get("dependency_ids") or []
        if not dependency_ids:
            return None
        terminal = ("completed", "cancelled")
        for dep_id in dependency_ids:
            dep_resp = await client.get(f"{self._api_url}/tasks/{dep_id}")
            # A dependency we cannot read is treated as unmet — fail closed
            # rather than spawn ahead of work whose state is unknown.
            if not dep_resp.is_success or dep_resp.json().get("status") not in terminal:
                return (
                    f"Task {task.get('id')} waiting on non-terminal dependency {dep_id}"
                )
        return None

    async def _auto_block_task(
        self, client: httpx.AsyncClient, task_id: str, reason: str
    ) -> None:
        """Auto-block a task that cannot proceed due to missing prerequisites.

        Re-checks live status first: every caller's view of the task can be
        stale by the time this runs (a spawn-readiness check that raced a
        reassignment, a dead container whose task was already picked up and
        submitted for QA). Blocking is meaningless once the task moved past
        the caller's control — it skips with an info log instead of yanking
        a task out from under whoever now owns it.
        """
        try:
            resp = await client.get(f"{self._api_url}/tasks/{task_id}")
            if (
                resp.is_success
                and resp.json().get("status") in _AUTO_BLOCK_SKIP_STATUSES
            ):
                logger.info(
                    "Skipped auto-block: task already past dev control",
                    task_id=task_id,
                    status=resp.json().get("status"),
                    reason=reason,
                )
                return
        except Exception as e:
            # A pre-check failure must not swallow the block attempt itself —
            # fall through and try the PATCH as before.
            logger.debug(
                "Auto-block status pre-check failed, proceeding anyway",
                task_id=task_id,
                error=str(e) or repr(e),
            )
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
            # str(e) is empty for some exception types (e.g. a bare
            # asyncio.TimeoutError) — repr always names the class, so the
            # fallback guarantees the log line is never blank.
            logger.error(
                "Failed to auto-block task",
                task_id=task_id,
                error=str(e) or repr(e),
            )

    async def _notify_stuck_agent(
        self, agent_slug: str, task_id: str, task_status: str | None
    ) -> None:
        """One-shot alert to the CEO that an agent is wedged in a respawn loop.

        Best-effort: a notification failure must not wedge dispatch, so any
        error is logged and swallowed.
        """
        from uuid import UUID

        from roboco.db.base import get_db_context
        from roboco.services.notification import NotificationService
        from roboco.services.task import TaskService

        task_title: str | None = None
        try:
            async with get_db_context() as db:
                task = await TaskService(db).get(UUID(task_id))
                task_title = task.title if task else None
        except Exception:
            task_title = None

        try:
            await NotificationService().send_stuck_agent_notification(
                task_id=task_id,
                agent_slug=agent_slug,
                task_status=task_status or "unknown",
                to_agent="ceo",
                task_title=task_title,
            )
        except Exception as exc:
            logger.warning(
                "Failed to send stuck-agent notification",
                agent_id=agent_slug,
                task_id=task_id,
                error=str(exc),
            )

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
